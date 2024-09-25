import os

import numpy as np

from omnigibson.macros import gm
from omnigibson.robots.manipulation_robot import GraspingPoint, ManipulationRobot


class A1(ManipulationRobot):
    """
    The A1 robot with inspire hand
    """

    def __init__(
        self,
        # Shared kwargs in hierarchy
        name,
        relative_prim_path=None,
        scale=None,
        visible=True,
        visual_only=False,
        self_collisions=True,
        load_config=None,
        fixed_base=True,
        # Unique to USDObject hierarchy
        abilities=None,
        # Unique to ControllableObject hierarchy
        control_freq=None,
        controller_config=None,
        action_type="continuous",
        action_normalize=True,
        reset_joint_pos=None,
        # Unique to BaseRobot
        obs_modalities=("rgb", "proprio"),
        proprio_obs="default",
        sensor_config=None,
        # Unique to ManipulationRobot
        grasping_mode="physical",
        **kwargs,
    ):
        """
        Args:
            name (str): Name for the object. Names need to be unique per scene
            prim_path (None or str): global path in the stage to this object. If not specified, will automatically be
                created at /World/<name>
            scale (None or float or 3-array): if specified, sets either the uniform (float) or x,y,z (3-array) scale
                for this object. A single number corresponds to uniform scaling along the x,y,z axes, whereas a
                3-array specifies per-axis scaling.
            visible (bool): whether to render this object or not in the stage
            visual_only (bool): Whether this object should be visual only (and not collide with any other objects)
            self_collisions (bool): Whether to enable self collisions for this object
            load_config (None or dict): If specified, should contain keyword-mapped values that are relevant for
                loading this prim at runtime.
            abilities (None or dict): If specified, manually adds specific object states to this object. It should be
                a dict in the form of {ability: {param: value}} containing object abilities and parameters to pass to
                the object state instance constructor.
            control_freq (float): control frequency (in Hz) at which to control the object. If set to be None,
                we will automatically set the control frequency to be at the render frequency by default.
            controller_config (None or dict): nested dictionary mapping controller name(s) to specific controller
                configurations for this object. This will override any default values specified by this class.
            action_type (str): one of {discrete, continuous} - what type of action space to use
            action_normalize (bool): whether to normalize inputted actions. This will override any default values
                specified by this class.
            reset_joint_pos (None or n-array): if specified, should be the joint positions that the object should
                be set to during a reset. If None (default), self._default_joint_pos will be used instead.
                Note that _default_joint_pos are hardcoded & precomputed, and thus should not be modified by the user.
                Set this value instead if you want to initialize the robot with a different rese joint position.
            obs_modalities (str or list of str): Observation modalities to use for this robot. Default is ["rgb", "proprio"].
                Valid options are "all", or a list containing any subset of omnigibson.sensors.ALL_SENSOR_MODALITIES.
                Note: If @sensor_config explicitly specifies `modalities` for a given sensor class, it will
                    override any values specified from @obs_modalities!
            proprio_obs (str or list of str): proprioception observation key(s) to use for generating proprioceptive
                observations. If str, should be exactly "default" -- this results in the default proprioception
                observations being used, as defined by self.default_proprio_obs. See self._get_proprioception_dict
                for valid key choices
            sensor_config (None or dict): nested dictionary mapping sensor class name(s) to specific sensor
                configurations for this object. This will override any default values specified by this class.
            grasping_mode (str): One of {"physical", "assisted", "sticky"}.
                If "physical", no assistive grasping will be applied (relies on contact friction + finger force).
                If "assisted", will magnetize any object touching and within the gripper's fingers.
                If "sticky", will magnetize any object touching the gripper's fingers.
            kwargs (dict): Additional keyword arguments that are used for other super() calls from subclasses, allowing
                for flexible compositions of various object subclasses (e.g.: Robot is USDObject + ControllableObject).
        """
        self._hand_part_names = [11, 12, 13, 14, 21, 22, 31, 32, 41, 42, 51, 52]
        # Run super init
        super().__init__(
            relative_prim_path=relative_prim_path,
            name=name,
            scale=scale,
            visible=visible,
            fixed_base=fixed_base,
            visual_only=visual_only,
            self_collisions=self_collisions,
            load_config=load_config,
            abilities=abilities,
            control_freq=control_freq,
            controller_config=controller_config,
            action_type=action_type,
            action_normalize=action_normalize,
            reset_joint_pos=reset_joint_pos,
            obs_modalities=obs_modalities,
            proprio_obs=proprio_obs,
            sensor_config=sensor_config,
            grasping_mode=grasping_mode,
            grasping_direction="upper",
            **kwargs,
        )

    @property
    def model_name(self):
        return "a1_inspire"

    @property
    def discrete_action_list(self):
        # Not supported for this robot
        raise NotImplementedError()

    def _create_discrete_action_space(self):
        # Fetch does not support discrete actions
        raise ValueError("Franka does not support discrete actions!")

    def update_controller_mode(self):
        super().update_controller_mode()
        # overwrite joint params (e.g. damping, stiffess, max_effort) here

    @property
    def controller_order(self):
        return ["arm_{}".format(self.default_arm), "gripper_{}".format(self.default_arm)]

    @property
    def _default_controllers(self):
        controllers = super()._default_controllers
        controllers["arm_{}".format(self.default_arm)] = "InverseKinematicsController"
        controllers["gripper_{}".format(self.default_arm)] = "MultiFingerGripperController"
        return controllers

    @property
    def _default_gripper_multi_finger_controller_configs(self):
        conf = super()._default_gripper_multi_finger_controller_configs
        # Since the end effector is not a gripper, set the mode to independent
        conf[self.default_arm]["mode"] = "independent"
        conf[self.default_arm]["command_input_limits"] = None
        return conf

    @property
    def _default_joint_pos(self):
        """
        This will setup the robot hand at [0.45, 0, 0.3] offset from base with palm open and facing downwards.
        """
        return np.concatenate([[0.0, 1.906, -0.991, 1.571, 0.915, -1.571], np.zeros(12)])

    @property
    def finger_lengths(self):
        return {self.default_arm: 0.1}

    @property
    def arm_link_names(self):
        return {self.default_arm: [f"arm_seg{i+1}" for i in range(5)]}

    @property
    def arm_joint_names(self):
        return {self.default_arm: [f"arm_joint{i+1}" for i in range(6)]}

    @property
    def eef_link_names(self):
        return {self.default_arm: "palm_lower"}

    @property
    def finger_link_names(self):
        return {self.default_arm: [f"link{i}" for i in self._hand_part_names]}

    @property
    def finger_joint_names(self):
        return {self.default_arm: [f"joint{i}" for i in self._hand_part_names]}

    @property
    def usd_path(self):
        return os.path.join(gm.ASSET_PATH, f"models/a1/a1_inspire.usd")

    @property
    def robot_arm_descriptor_yamls(self):
        return {self.default_arm: os.path.join(gm.ASSET_PATH, f"models/a1/a1_inspire_description.yaml")}

    @property
    def urdf_path(self):
        return os.path.join(gm.ASSET_PATH, f"models/a1/a1_inspire.urdf")

    @property
    def eef_usd_path(self):
        return {self.default_arm: os.path.join(gm.ASSET_PATH, f"models/franka/{self.model_name}_eef.usd")}

    @property
    def teleop_rotation_offset(self):
        return {self.default_arm: np.array([0, 0, 0.707, 0.707])}

    @property
    def assisted_grasp_start_points(self):
        return {
            self.default_arm: [
                GraspingPoint(link_name=f"base_link", position=[-0.025, -0.07, 0.012]),
                GraspingPoint(link_name=f"base_link", position=[-0.015, -0.11, 0.012]),
                GraspingPoint(link_name=f"link14", position=[-0.01, 0.015, 0.004]),
            ]
        }

    @property
    def assisted_grasp_end_points(self):
        return {
            self.default_arm: [
                GraspingPoint(link_name=f"link22", position=[0.006, 0.04, 0.003]),
                GraspingPoint(link_name=f"link32", position=[0.006, 0.045, 0.003]),
                GraspingPoint(link_name=f"link42", position=[0.006, 0.04, 0.003]),
                GraspingPoint(link_name=f"link52", position=[0.006, 0.04, 0.003]),
            ]
        }

    @property
    def disabled_collision_pairs(self):
        # some dexhand has self collisions that needs to be filtered out
        return [["base_link", "link12"]]
