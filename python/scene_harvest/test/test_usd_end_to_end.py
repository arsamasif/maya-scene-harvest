"""End to end: USD library -> CLI run -> Parquet dataset -> report.

Uses the real worker in a subprocess (plain Python with pxr), so this is
the same path a Maya harvest takes, minus mayapy.

"""

import os

import pytest

from scene_harvest import cli
from scene_harvest import constants
from scene_harvest import registry
from scene_harvest import report
from scene_harvest import writers

pytest.importorskip("pxr")

from scene_harvest.usd_adapter import host as usd_host  # noqa: E402


@pytest.fixture(scope="module")
def harvested(usd_library, tmp_path_factory):
    output_dir = str(tmp_path_factory.mktemp("usd_harvest"))
    exit_code = cli.main(["run", usd_library["root"], "-o", output_dir, "-b", "1", "-w", "2"])
    return {"output_dir": output_dir, "exit_code": exit_code, "library": usd_library}


def _by_scene(frame, scenes, name):
    scene_id = scenes.loc[scenes["scene_path"].str.endswith(name), "scene_id"].iloc[0]
    return frame[frame["scene_id"] == scene_id]


def test_run_succeeds(harvested):
    assert harvested["exit_code"] == 0
    scenes = writers.read_table(harvested["output_dir"], "scenes")
    assert sorted(os.path.basename(path) for path in scenes["scene_path"]) == [
        "crate.usda", "hero.usda", "sh010.usda",
    ]
    assert set(scenes["host"]) == {constants.HOST_USD}


def test_mesh_stats(harvested):
    output_dir = harvested["output_dir"]
    scenes = writers.read_table(output_dir, "scenes")
    meshes = writers.read_table(output_dir, "meshes")
    crate = _by_scene(meshes, scenes, "crate.usda").iloc[0]
    assert (crate["vertex_count"], crate["face_count"], crate["edge_count"], crate["triangle_count"]) == (8, 6, 12, 12)
    assert list(crate["uv_sets"]) == ["st"]
    assert list(crate["bbox_min"]) == [-1.0, 0.0, -1.0]
    assert list(crate["bbox_max"]) == [1.0, 2.0, 1.0]
    assert list(crate["world_matrix"])[12:15] == [0.0, 1.0, 0.0]
    assert len(_by_scene(meshes, scenes, "sh010.usda")) == 4


def test_hierarchy(harvested):
    output_dir = harvested["output_dir"]
    scenes = writers.read_table(output_dir, "scenes")
    nodes = _by_scene(writers.read_table(output_dir, "nodes"), scenes, "crate.usda").set_index("path")
    assert nodes.loc["/crate", "parent_path"] == ""
    assert nodes.loc["/crate/geo/body", "node_type"] == "Mesh"
    assert nodes.loc["/crate/geo/body", "depth"] == 3


def test_materials(harvested):
    output_dir = harvested["output_dir"]
    scenes = writers.read_table(output_dir, "scenes")
    materials = _by_scene(writers.read_table(output_dir, "materials"), scenes, "crate.usda")
    assert sorted(zip(materials["material"], materials["face_count"])) == [
        ("/crate/mtl/metal", 1), ("/crate/mtl/wood", 6),
    ]


def test_dependencies_flag_missing_files(harvested):
    output_dir = harvested["output_dir"]
    scenes = writers.read_table(output_dir, "scenes")
    dependencies = writers.read_table(output_dir, "dependencies")
    hero = _by_scene(dependencies, scenes, "hero.usda").set_index("path")
    assert bool(hero.loc["./crate.usda", "exists"])
    assert hero.loc["./hero_hair_groom.usda", "kind"] == "payload"
    assert not bool(hero.loc["./hero_hair_groom.usda", "exists"])

    shot = _by_scene(dependencies, scenes, "sh010.usda")
    missing = set(shot.loc[~shot["exists"], "kind"])
    assert missing == {"sublayer", "payload", "texture"}
    textures = shot[shot["kind"] == "texture"]
    assert textures["exists"].sum() == 3


def test_skinning(harvested):
    output_dir = harvested["output_dir"]
    scenes = writers.read_table(output_dir, "scenes")
    skins = _by_scene(writers.read_table(output_dir, "skins"), scenes, "hero.usda").iloc[0]
    assert skins["skin"] == "/hero/skel"
    assert skins["influence_count"] == 3
    assert skins["vertex_count"] == 8
    assert skins["max_influences_per_vertex"] == 2
    assert skins["mean_influences_per_vertex"] == 1.5
    skeleton = _by_scene(writers.read_table(output_dir, "skeletons"), scenes, "hero.usda").iloc[0]
    assert (skeleton["joint_count"], skeleton["max_depth"]) == (3, 3)


def test_second_run_is_a_no_op(harvested, capsys):
    exit_code = cli.main(["run", harvested["library"]["root"], "-o", harvested["output_dir"]])
    assert exit_code == 0
    assert "Planned 0 scene(s)" in capsys.readouterr().out


def test_report_and_status(harvested, capsys):
    summary = report.summarize(harvested["output_dir"])
    assert summary["scene_count"] == 3
    assert summary["status"] == {constants.STATUS_DONE: 3}
    assert summary["missing_by_kind"]["texture"] == 5
    assert summary["over_budget"] == 0

    assert cli.main(["report", "-o", harvested["output_dir"]]) == 0
    assert cli.main(["status", "-o", harvested["output_dir"]]) == 0
    out = capsys.readouterr().out
    assert "Triangles per scene" in out
    assert "Most referenced missing files" in out
    assert "done: 3" in out


def test_json_output(usd_library, tmp_path):
    output_dir = str(tmp_path / "json_harvest")
    assert cli.main(["run", usd_library["root"], "-o", output_dir, "-f", "json", "-w", "1"]) == 0
    assert os.path.isfile(writers.dataset_path(output_dir, "meshes", constants.FORMAT_JSON))
    assert len(writers.read_table(output_dir, "meshes")) == 7


def test_topology_checks_can_be_disabled(usd_library):
    stage = usd_host.open_scene(usd_library["prop"])
    collector = registry.load_builtin(constants.HOST_USD).get(constants.HOST_USD, "meshes")()
    context = registry.SceneContext("id", usd_library["prop"], constants.HOST_USD, stage, {"topology_checks": False})
    record = next(iter(collector.collect(context)))
    assert record.non_manifold_edges == record.lamina_faces == -1


def test_usd_host_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        usd_host.open_scene(str(tmp_path / "missing.usda"))


def test_cli_errors(tmp_path, capsys):
    assert cli.main(["plan", str(tmp_path / "nope"), "-o", str(tmp_path / "out")]) == 2
    assert "Scene folder not found" in capsys.readouterr().err
    assert cli.main(["status", "-o", str(tmp_path / "empty")]) == 0
