import logging
import os
import yaml
import copy
import time
import argparse
import bddl
import pkgutil
import omnigibson as og
from omnigibson.macros import gm, macros
import json
import csv
import traceback
from omnigibson.objects import DatasetObject
from omnigibson.object_states import Contains
from omnigibson.tasks import BehaviorTask
from omnigibson.systems import remove_callback_on_system_init, remove_callback_on_system_clear, get_system, MicroPhysicalParticleSystem
from omnigibson.systems.system_base import clear_all_systems, PhysicalParticleSystem
from omnigibson.utils.python_utils import clear as clear_pu
from omnigibson.utils.python_utils import create_object_from_init_info
from omnigibson.utils.bddl_utils import OBJECT_TAXONOMY
from omnigibson.utils.constants import PrimType
from bddl.activity import Conditions, evaluate_state
from utils import *
import numpy as np
import random


# TODO:
# 1. Set boundingCube approximation earlier (maybe right after importing the scene objects). Otherwise after loading the robot, we will elapse one physics step
# 2. Enable transition rule and refresh all rules before online validation

parser = argparse.ArgumentParser()
parser.add_argument("--scene_model", type=str, default=None,
                    help="Scene model to sample tasks in")
parser.add_argument("--activities", type=str, default=None,
                    help="Activity/ie(s) to be sampled, if specified. This should be a comma-delimited list of desired activities. Otherwise, will try to sample all tasks in this scene")
parser.add_argument("--start_at", type=str, default=None,
                    help="If specified, activity to start at, ignoring all previous")
parser.add_argument("--thread_id", type=str, default=None,
                    help="If specified, ID to assign to the thread when tracking in_progress")
parser.add_argument("--randomize", action="store_true",
                    help="If set, will randomize order of activities.")
parser.add_argument("--overwrite_existing", action="store_true",
                    help="If set, will overwrite any existing tasks that are found. Otherwise, will skip.")

gm.HEADLESS = True
gm.USE_GPU_DYNAMICS = True
gm.ENABLE_FLATCACHE = False
gm.ENABLE_OBJECT_STATES = True
gm.ENABLE_TRANSITION_RULES = False

# macros.prims.entity_prim.DEFAULT_SLEEP_THRESHOLD = 0.0

def main(random_selection=False, headless=False, short_exec=False):
    args = parser.parse_args()

    # Parse arguments based on whether values are specified in os.environ
    # Priority is:
    # 1. command-line args
    # 2. environment level variables
    if args.scene_model is None:
        # This MUST be specified
        assert os.environ.get("SAMPLING_SCENE_MODEL"), "scene model MUST be specified, either as a command-line arg or as an environment variable!"
        args.scene_model = os.environ["SAMPLING_SCENE_MODEL"]
    if args.activities is None and os.environ.get("SAMPLING_ACTIVITIES"):
        args.activities = os.environ["SAMPLING_ACTIVITIES"]
    if args.start_at is None and os.environ.get("SAMPLING_START_AT"):
        args.start_at = os.environ["SAMPLING_START_AT"]
    if args.thread_id is None:
        # This checks for both "" and non-existent key
        args.thread_id = os.environ["SAMPLING_THREAD_ID"] if os.environ.get("SAMPLING_THREAD_ID") else "1"
    if not args.randomize:
        args.randomize = os.environ.get("SAMPLING_RANDOMIZE") in {"1", "true", "True"}
    if not args.overwrite_existing:
        args.overwrite_existing = os.environ.get("SAMPLING_OVERWRITE_EXISTING") in {"1", "true", "True"}

    # Make sure scene can be sampled by current user
    scene_row = validate_scene_can_be_sampled(scene=args.scene_model)

    # Set the thread id for the given scene
    worksheet.update_acell(f"X{scene_row}", args.thread_id)

    # If we want to create a stable scene config, do that now
    default_scene_fpath = f"{gm.DATASET_PATH}/scenes/{args.scene_model}/json/{args.scene_model}_stable.json"
    if not os.path.exists(default_scene_fpath):
        create_stable_scene_json(args=args)

    # Get the default scene instance
    assert os.path.exists(default_scene_fpath), "Did not find default stable scene json!"
    with open(default_scene_fpath, "r") as f:
        default_scene_dict = json.load(f)

    # Define the configuration to load -- we'll use a Fetch
    cfg = {
        # Use default frequency
        "env": {
            "action_frequency": 30,
            "physics_frequency": 120,
        },
        "scene": {
            "type": "InteractiveTraversableScene",
            "scene_file": default_scene_fpath,
            "scene_model": args.scene_model,
        },
        "robots": [
            {
                "type": "Fetch",
                "obs_modalities": ["rgb"],
                "grasping_mode": "physical",
                "default_arm_pose": "diagonal30",
                "default_reset_mode": "tuck",
            },
        ],
    }

    valid_tasks = get_valid_tasks()
    mapping = parse_task_mapping(fpath=TASK_INFO_FPATH)
    activities = get_scene_compatible_activities(scene_model=args.scene_model, mapping=mapping) \
        if args.activities is None else args.activities.split(",")

    # Create the environment
    # Attempt to sample the activity
    # env = create_env_with_stable_objects(cfg)
    env = og.Environment(configs=copy.deepcopy(cfg))

    # After we load the robot, we do self.scene.reset() (one physics step) and then self.scene.update_initial_state().
    # We need to set all velocities to zero after this. Otherwise, the visual only objects will drift.
    for obj in og.sim.scene.objects:
        obj.keep_still()
    og.sim.scene.update_initial_state()

    # Store the initial state -- this is the safeguard to reset to!
    scene_initial_state = copy.deepcopy(env.scene._initial_state)
    og.sim.stop()

    n_scene_objects = len(env.scene.objects)

    # Set environment configuration after environment is loaded, because we will load the task
    env.task_config["type"] = "BehaviorTask"
    env.task_config["online_object_sampling"] = True

    should_start = args.start_at is None
    if args.randomize:
        random.shuffle(activities)
    else:
        activities = sorted(activities)
    for activity in activities:
        print(f"Checking activity: {activity}...")
        if not should_start:
            if args.start_at == activity:
                should_start = True
            else:
                continue

        # sleep to avoid gspread query limits
        time.sleep(np.random.uniform(1.0, 3.0))

        # Don't sample any invalid activities
        if activity not in valid_tasks:
            continue

        if activity not in ACTIVITY_TO_ROW:
            continue

        # Get info from spreadsheet
        row = ACTIVITY_TO_ROW[activity]
        in_progress, success, validated, scene_id, user, reason, exception, misc = worksheet.get(f"B{row}:I{row}")[0]

        # If we manually do not want to sample the task (DO NOT SAMPLE == "DNS", skip)
        if success.lower() == "dns":
            continue

        # Only sample stuff which is fixed
        # if "fixed" not in misc.lower():
        #     continue

        # If we've already sampled successfully (success is populated with a 1) and we don't want to overwrite the
        # existing sampling result, skip
        if success != "" and int(success) == 1 and not args.overwrite_existing:
            continue

        # If another thread is already in the process of sampling, skip
        if in_progress not in {None, ""}:
            continue

        # Reserve this task by marking in_progress = 1
        worksheet.update_acell(f"B{row}", args.thread_id)

        should_sample, success, reason = True, False, ""

        # Skip any with unsupported predicates, but still record the reason why we can't sample
        conditions = Conditions(activity, 0, simulator_name="omnigibson")
        init_predicates = set(get_predicates(conditions.parsed_initial_conditions))
        unsupported_predicates = set.intersection(init_predicates, UNSUPPORTED_PREDICATES)
        if len(unsupported_predicates) > 0:
            should_sample = False
            reason = f"Unsupported predicate(s): {unsupported_predicates}"

        env.task_config["activity_name"] = activity
        scene_instance = BehaviorTask.get_cached_activity_scene_filename(
            scene_model=args.scene_model,
            activity_name=activity,
            activity_definition_id=0,
            activity_instance_id=0,
        )

        # Make sure sim is stopped
        assert og.sim.is_stopped()

        # Attempt to sample
        try:
            if should_sample:
                relevant_rooms = set(get_rooms(conditions.parsed_initial_conditions))
                print(f"relevant rooms: {relevant_rooms}")
                for obj in og.sim.scene.objects:
                    if isinstance(obj, DatasetObject):
                        obj_rooms = {"_".join(room.split("_")[:-1]) for room in obj.in_rooms}
                        active = len(relevant_rooms.intersection(obj_rooms)) > 0 or obj.category in {"floors", "walls"}
                        obj.visual_only = not active
                        obj.visible = active

                og.log.info(f"Sampling task: {activity}")
                env._load_task()
                assert og.sim.is_stopped()

                success, feedback = env.task.feedback is None, env.task.feedback

                if success:
                    # Set masses of all task-relevant objects to be very high
                    # This is to avoid particles from causing instabilities
                    # Don't use this on cloth since these may be unstable at high masses
                    for obj in env.scene.objects[n_scene_objects:]:
                        if obj.prim_type != PrimType.CLOTH and Contains in obj.states and any(obj.states[Contains].get_value(system) for system in PhysicalParticleSystem.get_active_systems().values()):
                            obj.root_link.mass = max(1.0, obj.root_link.mass)

                    # Sampling success
                    og.sim.play()
                    # This will actually reset the objects to their sample poses
                    env.task.reset(env)

                    for i in range(300):
                        og.sim.step(render=not gm.HEADLESS)

                    # from IPython import embed; embed()

                    task_final_state = og.sim.dump_state()
                    task_scene_dict = {"state": task_final_state}
                    # from IPython import embed; print("validate_task"); embed()
                    validated, error_msg = validate_task(env.task, task_scene_dict, default_scene_dict)
                    if not validated:
                        success = False
                        feedback = error_msg

                if success:
                    og.sim.load_state(task_final_state)
                    og.sim.scene.update_initial_state(task_final_state)
                    env.task.save_task(override=True)
                    og.log.info(f"\n\nSampling success: {activity}\n\n")
                    reason = ""
                else:
                    reason = feedback
                    og.log.error(f"\n\nSampling failed: {activity}.\n\nFeedback: {reason}\n\n")
                og.sim.stop()
            else:
                og.log.error(f"\n\nSampling failed: {activity}.\n\nFeedback: {reason}\n\n")

            assert og.sim.is_stopped()

            # Write to google sheets
            cell_list = worksheet.range(f"B{row}:H{row}")
            for cell, val in zip(cell_list,
                                 ("", int(success), "", args.scene_model, USER, reason, "")):
                cell.value = val
            worksheet.update_cells(cell_list)

            # Clear task callbacks if sampled
            if should_sample:
                callback_name = f"{activity}_refresh"
                og.sim.remove_callback_on_import_obj(name=callback_name)
                og.sim.remove_callback_on_remove_obj(name=callback_name)
                remove_callback_on_system_init(name=callback_name)
                remove_callback_on_system_clear(name=callback_name)

                # Remove all the additionally added objects
                for obj in env.scene.objects[n_scene_objects:]:
                    og.sim.remove_object(obj)

                # Clear all systems
                clear_all_systems()
                clear_pu()
                og.sim.step(not gm.HEADLESS)

                # Update the scene initial state to the original state
                og.sim.scene.update_initial_state(scene_initial_state)

        except Exception as e:
            traceback_str = f"{traceback.format_exc()}"
            og.log.error(traceback_str)
            og.log.error(f"\n\nCaught exception sampling activity {activity} in scene {args.scene_model}:\n\n{e}\n\n")

            # Clear the in_progress reservation and note the exception
            cell_list = worksheet.range(f"B{row}:H{row}")
            for cell, val in zip(cell_list,
                                 ("", 0, "", args.scene_model, USER, reason, traceback_str)):
                cell.value = val
            worksheet.update_cells(cell_list)

            try:
                # Stop sim, clear simulator, and re-create environment
                og.sim.stop()
                og.sim.clear()
            except AttributeError as e:
                # This is the "GetPath" error that happens sporatically. It's benign, so we ignore it
                pass

            # env = create_env_with_stable_objects(cfg)
            env = og.Environment(configs=copy.deepcopy(cfg))

            # After we load the robot, we do self.scene.reset() (one physics step) and then self.scene.update_initial_state().
            # We need to set all velocities to zero after this. Otherwise, the visual only objects will drift.
            for obj in og.sim.scene.objects:
                obj.keep_still()
            og.sim.scene.update_initial_state()

            # Store the initial state -- this is the safeguard to reset to!
            scene_initial_state = copy.deepcopy(env.scene._initial_state)
            og.sim.stop()

            n_scene_objects = len(env.scene.objects)

            # Set environment configuration after environment is loaded, because we will load the task
            env.task_config["type"] = "BehaviorTask"
            env.task_config["online_object_sampling"] = True

    print("Successful shutdown!")

    # Record when we successfully complete all the activities
    worksheet.update_acell(f"W{scene_row}", 1)

    # Shutdown at the end
    og.shutdown()


if __name__ == "__main__":
    main()
