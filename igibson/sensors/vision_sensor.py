from collections import OrderedDict
import numpy as np
import time
import gym

from igibson import app
from igibson.sensors.sensor_base import BaseSensor
from igibson.utils.constants import MAX_CLASS_COUNT, MAX_INSTANCE_COUNT, MAX_VIEWER_SIZE, VALID_OMNI_CHARS
from igibson.utils.python_utils import assert_valid_key, classproperty
from igibson.utils.usd_utils import get_camera_params
from igibson.utils.transform_utils import euler2quat, quat2euler

import carb
from omni.isaac.core.utils.stage import get_current_stage
from pxr import Gf, UsdGeom

# Make sure synthetic data extension is enabled
ext_manager = app.app.get_extension_manager()
ext_manager.set_extension_enabled("omni.syntheticdata", True)

# Continue with omni synethic data imports afterwards
from omni.syntheticdata import sensors as sensors_util
import omni.syntheticdata._syntheticdata as sd
sensor_types = sd.SensorType
from omni.kit.viewport.window import get_viewport_window_instances


class VisionSensor(BaseSensor):
    """
    Vision sensor that handles a variety of modalities, including:

        - RGB (normal)
        - Depth (normal, linear)
        - Normals
        - Segmentation (semantic, instance)
        - Optical flow
        - 2D Bounding boxes (tight, loose)
        - 3D Bounding boxes
        - Camera state

    Args:
        prim_path (str): prim path of the Prim to encapsulate or create.
        name (str): Name for the object. Names need to be unique per scene.
        modalities (str or list of str): Modality(s) supported by this sensor. Default is "all", which corresponds
            to all modalities being used. Otherwise, valid options should be part of cls.all_modalities.
            For this vision sensor, this includes any of:
                {rgb, depth, depth_linear, normal, seg_semantic, seg_instance, flow, bbox_2d_tight,
                bbox_2d_loose, bbox_3d, camera}
        enabled (bool): Whether this sensor should be enabled by default
        noise (None or BaseSensorNoise): If specified, sensor noise model to apply to this sensor.
        load_config (None or dict): If specified, should contain keyword-mapped values that are relevant for
            loading this sensor's prim at runtime.
        image_height (int): Height of generated images, in pixels
        image_width (int): Width of generated images, in pixels
        viewport_name (None or str): If specified, will link this camera to the specified viewport, overriding its
            current camera. Otherwise, creates a new viewport
    """
    _SENSOR_HELPERS = OrderedDict(
        rgb=sensors_util.get_rgb,
        depth=sensors_util.get_depth,
        depth_linear=sensors_util.get_depth_linear,
        normal=sensors_util.get_normals,
        seg_semantic=sensors_util.get_semantic_segmentation,
        seg_instance=sensors_util.get_instance_segmentation,
        flow=sensors_util.get_motion_vector,
        bbox_2d_tight=sensors_util.get_bounding_box_2d_tight,
        bbox_2d_loose=sensors_util.get_bounding_box_2d_loose,
        bbox_3d=sensors_util.get_bounding_box_3d,
        camera=get_camera_params,
    )

    # Define raw sensor types
    _RAW_SENSOR_TYPES = OrderedDict(
        rgb=sensor_types.Rgb,
        depth=sensor_types.Depth,
        depth_linear=sensor_types.DepthLinear,
        normal=sensor_types.Normal,
        seg_semantic=sensor_types.SemanticSegmentation,
        seg_instance=sensor_types.InstanceSegmentation,
        flow=sensor_types.MotionVector,
        bbox_2d_tight=sensor_types.BoundingBox2DTight,
        bbox_2d_loose=sensor_types.BoundingBox2DLoose,
        bbox_3d=sensor_types.BoundingBox3D,
    )

    def __init__(
        self,
        prim_path,
        name,
        modalities="all",
        enabled=True,
        noise=None,
        load_config=None,
        image_height=128,
        image_width=128,
        viewport_name=None,
    ):
        # Create load config from inputs
        load_config = dict() if load_config is None else load_config
        load_config["image_height"] = image_height
        load_config["image_width"] = image_width
        load_config["viewport_name"] = viewport_name

        # Create variables that will be filled in later at runtime
        self._sd = None             # synthetic data interface
        self._viewport = None       # Viewport from which to grab data

        # Run super method
        super().__init__(
            prim_path=prim_path,
            name=name,
            modalities=modalities,
            enabled=enabled,
            noise=noise,
            load_config=load_config,
        )

    def _load(self, simulator=None):
        # Define a new camera prim at the current stage
        stage = get_current_stage()
        prim = UsdGeom.Camera.Define(stage, self._prim_path).GetPrim()
        return prim

    def _post_load(self):
        # run super first
        super()._post_load()

        # Get synthetic data interface
        self._sd = sd.acquire_syntheticdata_interface()

        # Create a new viewport to link to this camera or link to a pre-existing one
        viewport_name = self._load_config["viewport_name"]
        vp_names_to_handles = {window_instance.name : window_instance for window_instance in get_viewport_window_instances()}
        # if viewport_name is not None:
        # else:
        #     raise Exception("Requires a viewport in scene")
            # viewport_handle = vp.create_instance()
        self._viewport = vp_names_to_handles[viewport_name].viewport_api
        self._window = vp_names_to_handles[viewport_name]

        # Link the camera and viewport together
        self._viewport.set_active_camera(self._prim_path)

        # Set the viewer size
        self._viewport.set_texture_resolution((self._load_config["image_width"], self._load_config["image_height"]))
        # self._viewport.set_window_size(self._load_config["image_height"], self._load_config["image_width"])
        self._window.width = self._load_config["image_width"]
        self._window.height = self._load_config["image_height"]
        # Requires 3 updates to propagate changes
        for i in range(3):
            app.update()

    def _initialize(self):
        # Run super first
        super()._initialize()

        # Initialize sensors
        self._initialize_sensors(names=self._modalities)

    def _initialize_sensors(self, names, timeout=10.0):
        """Initializes a raw sensor in the simulation.

        Args:
            names (str or list of str): Name of the raw sensor(s) to initialize.
                If they are not part of self._RAW_SENSOR_TYPES' keys, we will simply pass over them
            timeout (int): Maximum time in seconds to attempt to initialize sensors.
        """
        # Standardize the input and grab the intersection with all possible raw sensors
        names = set([names]) if isinstance(names, str) else set(names)
        names = names.intersection(set(self._RAW_SENSOR_TYPES.keys()))

        # Record the start time so we know how long this takes
        start = time.time()
        is_initialized = False
        sensors = []

        # Initialize sensors
        for name in names:
            sensors.append(sensors_util.create_or_retrieve_sensor(self._viewport, self._RAW_SENSOR_TYPES[name]))
        app.update()
        app.update()  # Extra frame required to prevent access violation error

    def _get_obs(self):
        # Make sure we're initialized
        assert self.initialized, "Cannot grab vision observations without first initializing this VisionSensor!"

        # Run super first to grab any upstream obs
        obs = super()._get_obs()

        # Process each sensor modality individually
        for modality in self.modalities:
            mod_kwargs = dict()
            mod_kwargs["viewport"] = self._viewport
            if modality == "seg_instance":
                mod_kwargs.update({"parsed": True, "return_mapping": True})
            elif modality == "bbox_3d":
                mod_kwargs.update({"parsed": True, "return_corners": True})
            obs[modality] = self._SENSOR_HELPERS[modality](**mod_kwargs)

        return obs

    def add_modality(self, modality):
        # Check if we already have this modality (if so, no need to initialize it explicitly)
        should_initialize = modality not in self._modalities

        # Run super
        super().add_modality(modality=modality)

        # We also need to initialize this new modality
        if should_initialize:
            self._initialize_sensors(names=modality)

    def get_local_pose(self):
        # We have to overwrite this because camera prims can't set their quat for some reason ):
        xform_translate_op = self.get_attribute("xformOp:translate")
        xform_orient_op = self.get_attribute("xformOp:rotateXYZ")
        return np.array(xform_translate_op), euler2quat(np.array(xform_orient_op))

    def set_window_position(self, x, y):
        """Set the position of the viewport window.

        :param x: x position of the viewport window
        :param y: y position of the viewport window
        """
        self._viewport.set_window_pos(x ,y)

    def set_window_size(self, width, height):
        """Set the size of the viewport window.

        :param width: width of the viewport window
        :param height: height of the viewport window
        """
        self._viewport.set_window_size(width, height)

    def set_camera_position(self, x, y, z, rotate=True):
        """Set the position of the active camera.

        :param x: x coordinate of the camera
        :param y: y coordinate of the camera
        :param z: z coordinate of the camera
        :param rotate: set rotate=True to move the camera, but rotate to keep its focus;
            set rotate=False to move the camera and look at a new point
        """
        self._viewport.set_camera_position(self._prim_path, x, y, z, rotate)

    def set_camera_target(self, x, y, z, rotate=True):
        """Set the target of the active camera.

        :param x: x coordinate of the camera
        :param y: y coordinate of the camera
        :param z: z coordinate of the camera
        :param rotate: rotate=True to rotate the camera to look at the target;
            set rotate=False to move the camera to look at the target
        """
        self._viewport.set_camera_target(self._prim_path, x, y, z, rotate)

    @property
    def viewer_visibility(self):
        """
        Returns:
            bool: Whether the viewer is visible or not
        """
        return self._viewport.is_visible()

    @viewer_visibility.setter
    def viewer_visibility(self, visible):
        """
        Sets whether the viewer should be visible or not in the Omni UI

        Args:
            visible (bool): Whether the viewer should be visible or not
        """
        self._viewport.set_visible(visible)
        # Requires 1 update to propagate changes
        app.update()

    @property
    def image_height(self):
        """
        Returns:
            int: Image height of this sensor, in pixels
        """
        return self._viewport.get_texture_resolution()[1]

    @image_height.setter
    def image_height(self, height):
        """
        Sets the image height @height for this sensor

        Args:
            height (int): Image height of this sensor, in pixels
        """
        width, _ = self._viewport.get_texture_resolution()
        self._viewport.set_texture_resolution((width, height))
        # Requires 3 updates to propagate changes
        for i in range(3):
            app.update()

    @property
    def image_width(self):
        """
        Returns:
            int: Image width of this sensor, in pixels
        """
        return self._viewport.get_texture_resolution()[0]

    @image_width.setter
    def image_width(self, width):
        """
        Sets the image width @width for this sensor

        Args:
            width (int): Image width of this sensor, in pixels
        """
        _, height = self._viewport.get_texture_resolution()
        self._viewport.set_texture_resolution((width, height))
        # Requires 3 updates to propagate changes
        for i in range(3):
            app.update()

    @property
    def clipping_range(self):
        """
        Returns:
            2-tuple: [min, max] value of the sensor's clipping range, in meters
        """
        return np.array(self.get_attribute("clippingRange"))

    @clipping_range.setter
    def clipping_range(self, limits):
        """
        Sets the clipping range @limits for this sensor

        Args:
            limits (2-tuple): [min, max] value of the sensor's clipping range, in meters
        """
        self.set_attribute(attr="clippingRange", val=Gf.Vec2f(*limits))
        # In order for sensor changes to propagate, we must toggle its visibility
        self.visible = False
        # A single update step has to happen here before we toggle visibility for changes to propagate
        app.update()
        self.visible = True

    @property
    def focal_length(self):
        """
        Returns:
            float: focal length of this sensor, in meters
        """
        return self.get_attribute("focalLength")

    @focal_length.setter
    def focal_length(self, length):
        """
        Sets the focal length @length for this sensor

        Args:
            length (float): focal length of this sensor, in meters
        """
        self.set_attribute("focalLength", length)

    @property
    def _obs_space_mapping(self):
        # Generate the complex space types for special modalities:
        # {"bbox_2d_tight", "bbox_2d_loose", "bbox_3d", "camera"}
        bbox_3d_space = gym.spaces.Sequence(space=gym.spaces.Tuple((
            gym.spaces.Box(low=-np.inf, high=np.inf, shape=(), dtype=int),  # uniqueId
            gym.spaces.Text(min_length=1, max_length=50, charset=VALID_OMNI_CHARS),  # name
            gym.spaces.Text(min_length=1, max_length=50, charset=VALID_OMNI_CHARS),  # semanticLabel
            gym.spaces.Text(min_length=0, max_length=50, charset=VALID_OMNI_CHARS),  # metadata
            gym.spaces.Sequence(space=gym.spaces.Box(low=0, high=MAX_INSTANCE_COUNT, shape=(), dtype=np.uint)),   # instanceIds
            gym.spaces.Box(low=0, high=MAX_CLASS_COUNT, shape=(), dtype=np.uint),  # semanticId
            gym.spaces.Box(low=-np.inf, high=np.inf, shape=(), dtype=float), # x_min
            gym.spaces.Box(low=-np.inf, high=np.inf, shape=(), dtype=float), # y_min
            gym.spaces.Box(low=-np.inf, high=np.inf, shape=(), dtype=float), # z_min
            gym.spaces.Box(low=-np.inf, high=np.inf, shape=(), dtype=float), # x_max
            gym.spaces.Box(low=-np.inf, high=np.inf, shape=(), dtype=float), # y_max
            gym.spaces.Box(low=-np.inf, high=np.inf, shape=(), dtype=float), # z_max
            gym.spaces.Box(low=-np.inf, high=np.inf, shape=(4, 4), dtype=float), # transform
            gym.spaces.Box(low=-np.inf, high=np.inf, shape=(8, 3), dtype=float), # corners
        )))

        bbox_2d_space = gym.spaces.Sequence(space=gym.spaces.Tuple((
            gym.spaces.Box(low=-np.inf, high=np.inf, shape=(), dtype=int),  # uniqueId
            gym.spaces.Text(min_length=1, max_length=50, charset=VALID_OMNI_CHARS),  # name
            gym.spaces.Text(min_length=1, max_length=50, charset=VALID_OMNI_CHARS),  # semanticLabel
            gym.spaces.Text(min_length=0, max_length=50, charset=VALID_OMNI_CHARS),  # metadata
            gym.spaces.Sequence(space=gym.spaces.Box(low=0, high=MAX_INSTANCE_COUNT, shape=(), dtype=np.uint)), # instanceIds
            gym.spaces.Box(low=0, high=MAX_CLASS_COUNT, shape=(), dtype=np.uint),  # semanticId
            gym.spaces.Box(low=0, high=MAX_VIEWER_SIZE, shape=(), dtype=int),  # x_min
            gym.spaces.Box(low=0, high=MAX_VIEWER_SIZE, shape=(), dtype=int),  # y_min
            gym.spaces.Box(low=0, high=MAX_VIEWER_SIZE, shape=(), dtype=int),  # x_max
            gym.spaces.Box(low=0, high=MAX_VIEWER_SIZE, shape=(), dtype=int),  # y_max
        )))

        camera_space = gym.spaces.Dict(OrderedDict(
            pose=gym.spaces.Box(low=-np.inf, high=np.inf, shape=(4, 4), dtype=float),
            fov=gym.spaces.Box(low=0, high=np.inf, shape=(), dtype=float),
            focal_length=gym.spaces.Box(low=0, high=np.inf, shape=(), dtype=float),
            horizontal_aperature=gym.spaces.Box(low=0, high=np.inf, shape=(), dtype=float),
            view_projection_matrix=gym.spaces.Box(low=-np.inf, high=np.inf, shape=(4, 4), dtype=float),
            resolution=gym.spaces.Dict(OrderedDict(
                width=gym.spaces.Box(low=1, high=MAX_VIEWER_SIZE, shape=(), dtype=np.uint),
                height=gym.spaces.Box(low=1, high=MAX_VIEWER_SIZE, shape=(), dtype=np.uint),
            )),
            clipping_range=gym.spaces.Box(low=0, high=np.inf, shape=(2,), dtype=float),
        ))

        obs_space_mapping = OrderedDict(
            rgb=((self.image_height, self.image_width, 4), 0, 255, np.uint8),
            depth=((self.image_height, self.image_width), 0.0, 1.0, np.float32),
            depth_linear=((self.image_height, self.image_width), 0.0, np.inf, np.float32),
            normal=((self.image_height, self.image_width, 3), -1.0, 1.0, np.float32),
            seg_semantic=((self.image_height, self.image_width), 0.0, np.inf, np.float32),
            seg_instance=((self.image_height, self.image_width), 0.0, np.inf, np.float32),
            flow=((self.image_height, self.image_width, 3), -np.inf, np.inf, np.float32),
            bbox_2d_tight=bbox_2d_space,
            bbox_2d_loose=bbox_2d_space,
            bbox_3d=bbox_3d_space,
            camera=camera_space,
        )

        return obs_space_mapping

    @classproperty
    def all_modalities(cls):
        return {k for k in cls._SENSOR_HELPERS.keys()}

    @classproperty
    def no_noise_modalities(cls):
        # bounding boxes and camera state should not have noise
        return {"bbox_2d_tight", "bbox_2d_loose", "bbox_3d", "camera"}
