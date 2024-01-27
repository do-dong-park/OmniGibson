from omnigibson.utils.sim_utils import meets_minimum_isaac_version
import omnigibson as og
import omnigibson.lazy as lazy
from omnigibson.objects.stateful_object import StatefulObject
from omnigibson.prims.xform_prim import XFormPrim
from omnigibson.utils.python_utils import assert_valid_key
from omnigibson.utils.constants import PrimType
from omnigibson.utils.ui_utils import create_module_logger
import numpy as np

# Create module logger
log = create_module_logger(module_name=__name__)


class LightObject(StatefulObject):
    """
    LightObjects are objects that generate light in the simulation
    """
    LIGHT_TYPES = {
        "Cylinder",
        "Disk",
        "Distant",
        "Dome",
        "Geometry",
        "Rect",
        "Sphere",
    }

    def __init__(
        self,
        name,
        light_type,
        prim_path=None,
        category="light",
        class_id=None,
        uuid=None,
        scale=None,
        fixed_base=False,
        load_config=None,
        abilities=None,
        include_default_states=True,
        radius=1.0,
        intensity=50000.0,
        **kwargs,
    ):

        """
        Args:
            name (str): Name for the object. Names need to be unique per scene
            light_type (str): Type of light to create. Valid options are LIGHT_TYPES
            prim_path (None or str): global path in the stage to this object. If not specified, will automatically be
                created at /World/<name>
            category (str): Category for the object. Defaults to "object".
            class_id (None or int): What class ID the object should be assigned in semantic segmentation rendering mode.
                If None, the ID will be inferred from this object's category.
            uuid (None or int): Unique unsigned-integer identifier to assign to this object (max 8-numbers).
                If None is specified, then it will be auto-generated
            scale (None or float or 3-array): if specified, sets either the uniform (float) or x,y,z (3-array) scale
                for this object. A single number corresponds to uniform scaling along the x,y,z axes, whereas a
                3-array specifies per-axis scaling.
            fixed_base (bool): whether to fix the base of this object or not
            load_config (None or dict): If specified, should contain keyword-mapped values that are relevant for
                loading this prim at runtime.
            abilities (None or dict): If specified, manually adds specific object states to this object. It should be
                a dict in the form of {ability: {param: value}} containing object abilities and parameters to pass to
                the object state instance constructor.
            include_default_states (bool): whether to include the default object states from @get_default_states
            radius (float): Radius for this light.
            intensity (float): Intensity for this light.
            kwargs (dict): Additional keyword arguments that are used for other super() calls from subclasses, allowing
                for flexible compositions of various object subclasses (e.g.: Robot is USDObject + ControllableObject).
        """
        # Compose load config and add rgba values
        load_config = dict() if load_config is None else load_config
        load_config["scale"] = scale
        load_config["intensity"] = intensity
        load_config["radius"] = radius if light_type in {"Cylinder", "Disk", "Sphere"} else None

        # Make sure primitive type is valid
        assert_valid_key(key=light_type, valid_keys=self.LIGHT_TYPES, name="light_type")
        self.light_type = light_type

        # Other attributes to be filled in at runtime
        self._light_link = None

        # Run super method
        super().__init__(
            prim_path=prim_path,
            name=name,
            category=category,
            class_id=class_id,
            uuid=uuid,
            scale=scale,
            visible=True,
            fixed_base=fixed_base,
            visual_only=True,
            self_collisions=False,
            prim_type=PrimType.RIGID,
            include_default_states=include_default_states,
            load_config=load_config,
            abilities=abilities,
            **kwargs,
        )

    def _load(self):
        # Define XForm and base link for this light
        prim = og.sim.stage.DefinePrim(self._prim_path, "Xform")
        base_link = og.sim.stage.DefinePrim(f"{self._prim_path}/base_link", "Xform")

        # Define the actual light link
        light_prim = getattr(lazy.pxr.UsdLux, f"{self.light_type}Light").Define(og.sim.stage, f"{self._prim_path}/base_link/light").GetPrim()

        return prim

    def _post_load(self):
        # run super first
        super()._post_load()

        # Grab reference to light link
        self._light_link = XFormPrim(prim_path=f"{self._prim_path}/base_link/light", name=f"{self.name}:light_link")

        # Apply Shaping API and set default cone angle attribute
        shaping_api = lazy.pxr.UsdLux.ShapingAPI.Apply(self._light_link.prim).GetShapingConeAngleAttr().Set(180.0)

        # Optionally set the intensity
        if self._load_config.get("intensity", None) is not None:
            self.intensity = self._load_config["intensity"]

        # Optionally set the radius
        if self._load_config.get("radius", None) is not None:
            self.radius = self._load_config["radius"]

    def _initialize(self):
        # Run super
        super()._initialize()

        # Initialize light link
        self._light_link.initialize()

    @property
    def aabb(self):
        # This is a virtual object (with no associated visual mesh), so omni returns an invalid AABB.
        # Therefore we instead return a hardcoded small value
        return np.ones(3) * -0.001, np.ones(3) * 0.001


    @property
    def light_link(self):
        """
        Returns:
            XFormPrim: Link corresponding to the light prim itself
        """
        return self._light_link

    @property
    def radius(self):
        """
        Gets this light's radius

        Returns:
            float: radius for this light
        """
        return self._light_link.get_attribute("inputs:radius" if meets_minimum_isaac_version("2023.0.0") else "radius")

    @radius.setter
    def radius(self, radius):
        """
        Sets this light's radius

        Args:
            radius (float): radius to set
        """
        self._light_link.set_attribute("inputs:radius" if meets_minimum_isaac_version("2023.0.0") else "radius", radius)

    @property
    def intensity(self):
        """
        Gets this light's intensity

        Returns:
            float: intensity for this light
        """
        return self._light_link.get_attribute(
            "inputs:intensity" if meets_minimum_isaac_version("2023.0.0") else "intensity")

    @intensity.setter
    def intensity(self, intensity):
        """
        Sets this light's intensity

        Args:
            intensity (float): intensity to set
        """
        self._light_link.set_attribute(
            "inputs:intensity" if meets_minimum_isaac_version("2023.0.0") else "intensity",
            intensity)
        
    @property
    def color(self):
        """
        Gets this light's color

        Returns:
            float: color for this light
        """
        return tuple(float(x) for x in self._light_link.get_attribute(
            "inputs:color" if meets_minimum_isaac_version("2023.0.0") else "color"))

    @color.setter
    def color(self, color):
        """
        Sets this light's color

        Args:
            color ([float, float, float]): color to set, each value in range [0, 1]
        """
        self._light_link.set_attribute(
            "inputs:color" if meets_minimum_isaac_version("2023.0.0") else "color",
            lazy.pxr.Gf.Vec3f(color))

    @property
    def texture_file_path(self):
        """
        Gets this light's texture file path. Only valid for dome lights.

        Returns:
            str: texture file path for this light
        """
        return str(self._light_link.get_attribute(
            "inputs:texture:file" if meets_minimum_isaac_version("2023.0.0") else "texture:file"))

    @texture_file_path.setter
    def texture_file_path(self, texture_file_path):
        """
        Sets this light's texture file path. Only valid for dome lights.

        Args:
            texture_file_path (str): path of texture file that should be used for this light
        """
        self._light_link.set_attribute(
            "inputs:texture:file" if meets_minimum_isaac_version("2023.0.0") else "texture:file",
            lazy.pxr.Sdf.AssetPath(texture_file_path))


    def _create_prim_with_same_kwargs(self, prim_path, name, load_config):
        # Add additional kwargs (bounding_box is already captured in load_config)
        return self.__class__(
            prim_path=prim_path,
            light_type=self.light_type,
            name=name,
            intensity=self.intensity,
            load_config=load_config,
        )
