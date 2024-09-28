"""
Helper script to download OmniGibson dataset and assets.
"""

import math
import pathlib
from typing import List, Literal, Optional, Union

import click
import trimesh

import omnigibson as og
from omnigibson.macros import gm
from omnigibson.utils.asset_conversion_utils import (
    generate_collision_meshes,
    generate_urdf_for_obj,
    import_obj_metadata,
    import_obj_urdf,
)


@click.command()
@click.argument("visual_mesh_path", type=click.Path(exists=True, dir_okay=False))
@click.argument("category", type=click.STRING)
@click.argument("model", type=click.STRING)
@click.option(
    "--collision-mesh-path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to the collision mesh file. If not provided, a collision mesh will be generated using the provided collision generation method.",
)
@click.option(
    "--collision-method",
    type=click.Choice(["coacd", "convex"]),
    default="coacd",
    help="Method to generate the collision mesh. 'coacd' generates a set of convex decompositions, while 'convex' generates a single convex hull.",
)
@click.option("--scale", type=float, default=1.0, help="Scale factor to apply to the mesh.")
@click.option("--up-axis", type=click.Choice(["z", "y"]), default="z", help="Up axis for the mesh.")
@click.option("--headless", is_flag=True, help="Run the script in headless mode.")
@click.option("--confirm-bbox", default=True, help="Whether to confirm the scale factor.")
def import_asset(
    visual_mesh_path: str,
    category: str,
    model: str,
    collision_mesh_path: Optional[Union[str, pathlib.Path, List[Union[str, pathlib.Path]]]],
    collision_method: Literal["coacd", "convex"],
    scale: float,
    up_axis: Literal["z", "y"],
    headless: bool,
    confirm_bbox: bool,
):
    assert len(model) == 6 and model.isalpha(), "Model name must be 6 characters long and contain only letters."

    # Load the mesh
    visual_mesh: trimesh.Trimesh = trimesh.load(visual_mesh_path, force="mesh", process=False)

    # Either load, or generate, the collision mesh
    if collision_mesh_path is not None:
        # If it's a single path, we import it and try to split
        if isinstance(collision_mesh_path, (str, pathlib.Path)):
            collision_meshes = trimesh.load(collision_mesh_path, force="mesh", process=False).split()
        else:
            # Otherwise, we assume it's a list of paths and we load each one
            collision_meshes = [
                trimesh.load(collision_file, force="mesh", process=False) for collision_file in collision_mesh_path
            ]
    else:
        if collision_method == "coacd":
            collision_meshes = generate_collision_meshes(visual_mesh)
        elif collision_method == "convex":
            collision_meshes = [visual_mesh.convex_hull]
        else:
            raise ValueError(f"Unsupported collision generation option: {collision_method}")

    # If the up axis is y, we need to rotate the meshes
    if up_axis == "y":
        rotation_matrix = trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0])
        visual_mesh.apply_transform(rotation_matrix)
        for collision_mesh in collision_meshes:
            collision_mesh.apply_transform(rotation_matrix)

    # If the scale is nonzero, we apply it to the meshes
    if scale != 1.0:
        scale_transform = trimesh.transformations.scale_matrix(scale)
        visual_mesh.apply_transform(scale_transform)
        for collision_mesh in collision_meshes:
            collision_mesh.apply_transform(scale_transform)

    # Check the bounding box size and complain if it's larger than 3 meters
    bbox_size = visual_mesh.bounding_box.extents
    click.echo(f"Visual mesh bounding box size: {bbox_size}")

    if confirm_bbox:
        if any(size > 3.0 for size in bbox_size):
            click.echo(
                f"Warning: The bounding box sounds a bit large. Are you sure you don't need to scale? "
                f"We just wanted to confirm this is intentional. You can skip this check by passing --no-confirm-bbox."
            )
            click.confirm("Do you want to continue?", abort=True)

    # Generate the URDF
    click.echo(f"Generating URDF for {category}/{model}...")
    og.launch()
    generate_urdf_for_obj(visual_mesh, collision_meshes, category, model)
    click.echo("URDF generation complete!")

    # Convert to USD
    click.echo("Converting to USD...")
    import_obj_urdf(
        obj_category=category,
        obj_model=model,
        dataset_root=gm.USER_ASSETS_PATH,
    )
    import_obj_metadata(
        obj_category=category,
        obj_model=model,
        dataset_root=gm.USER_ASSETS_PATH,
        import_render_channels=True,
    )
    click.echo("Conversion complete!")

    if not headless:
        click.echo("The asset has been successfully imported. You can view it and make changes and save if you'd like.")
        while True:
            og.sim.render()


if __name__ == "__main__":
    import_asset()
