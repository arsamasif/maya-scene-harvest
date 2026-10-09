"""Maya collector tests. Run with `mayapy -m pytest`; skipped without Maya.

Builds a small scene from scratch (a cube with two shading groups, a
two-joint skin and a file texture pointing at a missing image), saves it,
and harvests it through the real worker code path.

"""

import os

import pytest

pytest.importorskip("maya.standalone")

from scene_harvest import constants  # noqa: E402
from scene_harvest import registry  # noqa: E402
from scene_harvest import schema  # noqa: E402
from scene_harvest import worker  # noqa: E402
from scene_harvest import writers  # noqa: E402
from scene_harvest.maya_adapter import host as maya_host  # noqa: E402


@pytest.fixture(scope="module")
def maya_scene(tmp_path_factory):
    maya_host.initialize()
    from maya import cmds

    cmds.file(new=True, force=True)
    cube = cmds.polyCube(name="crate", width=2, height=2, depth=2)[0]
    cmds.move(0, 1, 0, cube)

    shader = cmds.shadingNode("lambert", asShader=True, name="wood_mtl")
    engine = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="wood_SG")
    cmds.connectAttr(shader + ".outColor", engine + ".surfaceShader")
    cmds.sets(cube, edit=True, forceElement=engine)
    texture = cmds.shadingNode("file", asTexture=True, name="wood_tex")
    cmds.setAttr(texture + ".fileTextureName", "/does/not/exist/wood.png", type="string")
    cmds.connectAttr(texture + ".outColor", shader + ".color")

    lid_shader = cmds.shadingNode("lambert", asShader=True, name="metal_mtl")
    lid_engine = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="metal_SG")
    cmds.connectAttr(lid_shader + ".outColor", lid_engine + ".surfaceShader")
    cmds.sets(cube + ".f[1]", edit=True, forceElement=lid_engine)

    cmds.select(clear=True)
    root = cmds.joint(name="root", position=(0, 0, 0))
    cmds.joint(name="tip", position=(0, 2, 0))
    cmds.skinCluster(root, cube, maximumInfluences=2, name="crate_skin")

    path = str(tmp_path_factory.mktemp("maya") / "crate.ma")
    cmds.file(rename=path)
    cmds.file(save=True, type="mayaAscii")
    return path


def _harvest(path, tmp_path):
    batch = {
        "batch_id": "maya_test",
        "host": constants.HOST_MAYA,
        "output_dir": str(tmp_path),
        "format": constants.FORMAT_PARQUET,
        "scenes": [{
            "scene_id": "crate", "path": path, "host": constants.HOST_MAYA,
            "size": os.path.getsize(path), "mtime_ns": 0, "content_hash": "test",
        }],
    }
    result = worker.run_batch(batch)[0]
    assert result["status"] == constants.STATUS_DONE, result["error"]
    return str(tmp_path)


def test_mesh_record(maya_scene, tmp_path):
    output_dir = _harvest(maya_scene, tmp_path)
    meshes = writers.collect_rows(output_dir, "meshes")
    assert len(meshes) == 1
    mesh = meshes[0]
    assert (mesh["vertex_count"], mesh["face_count"], mesh["edge_count"], mesh["triangle_count"]) == (8, 6, 12, 12)
    assert mesh["uv_sets"] == ["map1"]
    assert mesh["bbox_min"] == pytest.approx([-1.0, 0.0, -1.0])
    assert mesh["bbox_max"] == pytest.approx([1.0, 2.0, 1.0])
    assert mesh["non_manifold_edges"] == 0
    assert mesh["lamina_faces"] == 0


def test_materials_and_dependencies(maya_scene, tmp_path):
    output_dir = _harvest(maya_scene, tmp_path)
    materials = {row["shading_group"]: row for row in writers.collect_rows(output_dir, "materials")}
    assert materials["wood_SG"]["material"] == "wood_mtl"
    assert materials["wood_SG"]["face_count"] == 5
    assert materials["metal_SG"]["face_count"] == 1

    dependencies = writers.collect_rows(output_dir, "dependencies")
    texture = [row for row in dependencies if row["node"].endswith("wood_tex")][0]
    assert texture["kind"] == "texture"
    assert texture["exists"] is False


def test_skinning(maya_scene, tmp_path):
    output_dir = _harvest(maya_scene, tmp_path)
    skins = writers.collect_rows(output_dir, "skins")
    assert len(skins) == 1
    assert skins[0]["influence_count"] == 2
    assert skins[0]["vertex_count"] == 8
    assert 1 <= skins[0]["max_influences_per_vertex"] <= 2
    skeletons = writers.collect_rows(output_dir, "skeletons")
    assert [(row["joint_count"], row["max_depth"]) for row in skeletons] == [(2, 2)]


def test_hierarchy_contains_transform_and_shape(maya_scene, tmp_path):
    output_dir = _harvest(maya_scene, tmp_path)
    nodes = {row["path"]: row for row in writers.collect_rows(output_dir, "nodes")}
    assert nodes["|crate"]["node_type"] == "transform"
    assert nodes["|crate|crateShape"]["parent_path"] == "|crate"
    assert nodes["|crate|crateShape"]["depth"] == 2


def test_registry_collectors_run_in_maya():
    reg = registry.load_builtin(constants.HOST_MAYA)
    assert all(isinstance(c, registry.Collector) for c in reg.for_host(constants.HOST_MAYA))
    assert schema.TABLES
