"""Generate a small library of USD scenes for tests and the demo notebook.

Builds three scenes with known contents: a textured prop with a per-face
material subset, a skinned character that references the prop and points
at a missing payload, and a shot that sublayers a file that does not exist.
The numbers the tests assert on (8 points, 6 quads, 2 influences per vertex
and so on) all come from here.

"""

import os

from pxr import Gf
from pxr import Sdf
from pxr import Usd
from pxr import UsdGeom
from pxr import UsdShade
from pxr import UsdSkel
from pxr import Vt

CUBE_POINTS = [
    (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
    (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
]
CUBE_COUNTS = [4, 4, 4, 4, 4, 4]
CUBE_INDICES = [
    0, 3, 2, 1,
    4, 5, 6, 7,
    0, 1, 5, 4,
    1, 2, 6, 5,
    2, 3, 7, 6,
    3, 0, 4, 7,
]
JOINTS = ["root", "root/spine", "root/spine/head"]


def build(root):
    """Write the sample library under a folder.

    Args:
        root (str): Destination folder, created if needed.

    Returns:
        dict: {"prop": path, "character": path, "shot": path, "texture": path}.

    """
    texture = os.path.join(root, "textures", "crate_diffuse.png")
    os.makedirs(os.path.dirname(texture), exist_ok=True)
    with open(texture, "wb") as handle:
        handle.write(b"\x89PNG test texture")
    prop = _build_prop(os.path.join(root, "assets", "crate.usda"))
    character = _build_character(os.path.join(root, "assets", "hero.usda"))
    shot = _build_shot(os.path.join(root, "shots", "sh010.usda"))
    return {"prop": prop, "character": character, "shot": shot, "texture": texture}


def add_cube(stage, path, translate=(0, 0, 0)):
    """Define a cube mesh with UVs at a path.

    Args:
        stage (Usd.Stage): Stage to author into.
        path (str): Prim path of the mesh.
        translate (tuple): World offset of the mesh.

    Returns:
        UsdGeom.Mesh: The new mesh.

    """
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr([Gf.Vec3f(*point) for point in CUBE_POINTS])
    mesh.CreateFaceVertexCountsAttr(CUBE_COUNTS)
    mesh.CreateFaceVertexIndicesAttr(CUBE_INDICES)
    mesh.CreateExtentAttr([Gf.Vec3f(-1, -1, -1), Gf.Vec3f(1, 1, 1)])
    st = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
        "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying
    )
    st.Set([Gf.Vec2f(0, 0)] * len(CUBE_INDICES))
    if translate != (0, 0, 0):
        mesh.AddTranslateOp().Set(Gf.Vec3d(*translate))
    return mesh


def _material(stage, path, texture_path):
    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + "/diffuse")
    shader.CreateIdAttr("UsdUVTexture")
    shader.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(texture_path))
    surface = UsdShade.Shader.Define(stage, path + "/surface")
    surface.CreateIdAttr("UsdPreviewSurface")
    material.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), "surface")
    return material


def _build_prop(path):
    stage = Usd.Stage.CreateNew(path)
    root = UsdGeom.Xform.Define(stage, "/crate")
    stage.SetDefaultPrim(root.GetPrim())
    mesh = add_cube(stage, "/crate/geo/body", translate=(0, 1, 0))
    wood = _material(stage, "/crate/mtl/wood", "../textures/crate_diffuse.png")
    metal = _material(stage, "/crate/mtl/metal", "../textures/missing_metal.<UDIM>.exr")
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(wood)
    subset = UsdShade.MaterialBindingAPI(mesh).CreateMaterialBindSubset("lid", Vt.IntArray([1]))
    UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(metal)
    stage.GetRootLayer().Save()
    return path


def _build_character(path):
    stage = Usd.Stage.CreateNew(path)
    root = UsdSkel.Root.Define(stage, "/hero")
    stage.SetDefaultPrim(root.GetPrim())
    skeleton = UsdSkel.Skeleton.Define(stage, "/hero/skel")
    skeleton.CreateJointsAttr(JOINTS)
    identity = Gf.Matrix4d(1.0)
    skeleton.CreateBindTransformsAttr([identity] * len(JOINTS))
    skeleton.CreateRestTransformsAttr([identity] * len(JOINTS))

    body = add_cube(stage, "/hero/geo/body")
    binding = UsdSkel.BindingAPI.Apply(body.GetPrim())
    binding.CreateSkeletonRel().SetTargets([skeleton.GetPath()])
    # Two weight slots per vertex: the bottom four vertices are driven by one
    # joint only, the top four are blended between spine and head.
    indices = [0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 1, 2, 1, 2, 1, 2]
    weights = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.7, 0.3, 0.7, 0.3]
    binding.CreateJointIndicesPrimvar(False, 2).Set(indices)
    binding.CreateJointWeightsPrimvar(False, 2).Set(weights)

    prop = stage.DefinePrim("/hero/props/crate")
    prop.GetReferences().AddReference("./crate.usda")
    hair = stage.DefinePrim("/hero/hair")
    hair.GetPayloads().AddPayload("./hero_hair_groom.usda")
    stage.GetRootLayer().Save()
    return path


def _build_shot(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stage = Usd.Stage.CreateNew(path)
    stage.GetRootLayer().subLayerPaths.append("./sh010_lighting.usda")
    world = UsdGeom.Xform.Define(stage, "/world")
    stage.SetDefaultPrim(world.GetPrim())
    for index, offset in enumerate((-3, 3)):
        crate = stage.DefinePrim("/world/crate_{0}".format(index))
        crate.GetReferences().AddReference("../assets/crate.usda")
        UsdGeom.Xformable(crate).AddTranslateOp().Set(Gf.Vec3d(offset, 0, 0))
    hero = stage.DefinePrim("/world/hero")
    hero.GetReferences().AddReference("../assets/hero.usda")
    stage.GetRootLayer().Save()
    return path
