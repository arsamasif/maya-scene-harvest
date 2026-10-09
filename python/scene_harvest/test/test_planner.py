"""Tests for scene discovery, fingerprinting and batch planning.

"""

import os

import pytest

from scene_harvest import constants
from scene_harvest import paths
from scene_harvest import planner
from scene_harvest import writers
from scene_harvest.test import fake_worker


def _fingerprint(name, size):
    return {"scene_id": name, "path": "/scenes/" + name, "size": size}


def test_discover_finds_scene_extensions_only(fake_scenes, output_dir):
    fake_worker.write_scene(os.path.join(fake_scenes, "notes.txt"))
    fake_worker.write_scene(os.path.join(fake_scenes, ".backup", "old.ma"))
    fake_worker.write_scene(os.path.join(fake_scenes, "layout.usda"))
    found = [os.path.basename(path) for path in planner.discover(fake_scenes)]
    assert found == ["a_hero.ma", "b_villain.mb", "layout.usda", "c_crate.ma", "d_barrel.ma"]


def test_discover_filters_hosts_and_excludes(fake_scenes):
    fake_worker.write_scene(os.path.join(fake_scenes, "layout.usda"))
    usd_only = planner.discover(fake_scenes, hosts=[constants.HOST_USD])
    assert [os.path.basename(path) for path in usd_only] == ["layout.usda"]
    without_props = planner.discover(fake_scenes, exclude=[os.path.join(fake_scenes, "props")])
    assert len(without_props) == 3


def test_discover_missing_root(tmp_path):
    with pytest.raises(FileNotFoundError):
        planner.discover(str(tmp_path / "nope"))


def test_fingerprint_hashes_content(fake_scenes):
    path = os.path.join(fake_scenes, "chars", "a_hero.ma")
    result = planner.fingerprint(path)
    assert result["scene_id"] == paths.scene_id(path)
    assert result["host"] == constants.HOST_MAYA
    assert result["content_hash"] == paths.content_hash(path)
    assert result["size"] == os.path.getsize(path)


def test_fingerprint_reuses_hash_when_stat_matches(fake_scenes, monkeypatch):
    path = os.path.join(fake_scenes, "chars", "a_hero.ma")
    previous = planner.fingerprint(path)
    previous["content_hash"] = "cached"

    calls = []
    monkeypatch.setattr(paths, "content_hash", lambda value: calls.append(value) or "fresh")
    assert planner.fingerprint(path, previous)["content_hash"] == "cached"
    assert planner.fingerprint(path, previous, rehash=True)["content_hash"] == "fresh"
    previous["size"] += 1
    assert planner.fingerprint(path, previous)["content_hash"] == "fresh"
    assert len(calls) == 2


def test_split_batches_respects_size_limit():
    scenes = [_fingerprint("s{0}".format(index), 10) for index in range(7)]
    batches = planner.split_batches(scenes, 3)
    assert [len(batch) for batch in batches] == [3, 2, 2]
    assert sorted(scene["scene_id"] for batch in batches for scene in batch) == sorted(
        scene["scene_id"] for scene in scenes
    )


def test_split_batches_balances_bytes():
    sizes = [100, 90, 10, 10, 5, 5]
    batches = planner.split_batches([_fingerprint("s{0}".format(i), s) for i, s in enumerate(sizes)], 3)
    totals = sorted(sum(scene["size"] for scene in batch) for batch in batches)
    assert totals == [105, 115]


def test_split_batches_edge_cases():
    assert planner.split_batches([], 4) == []
    with pytest.raises(ValueError):
        planner.split_batches([_fingerprint("a", 1)], 0)


def test_plan_writes_batches_and_marks_pending(fake_scenes, output_dir):
    result = planner.plan(fake_scenes, output_dir, batch_size=3)
    assert result["scheduled"] == 4
    assert result["skipped"] == 0
    assert len(result["batches"]) == 2
    assert constants.HOST_MAYA in result["signatures"]

    batch = writers.read_json(result["batches"][0])
    assert batch["host"] == constants.HOST_MAYA
    assert batch["output_dir"] == os.path.abspath(output_dir)
    assert batch["options"] == {}

    manifest = writers.read_json(os.path.join(output_dir, constants.MANIFEST_FILE))
    assert {entry["status"] for entry in manifest["scenes"].values()} == {constants.STATUS_PENDING}
    assert planner.load_plan(output_dir)["scheduled"] == 4


def test_plan_replaces_old_batch_files(fake_scenes, output_dir):
    planner.plan(fake_scenes, output_dir, batch_size=1)
    result = planner.plan(fake_scenes, output_dir, batch_size=4)
    batch_dir = os.path.dirname(result["batches"][0])
    assert len(os.listdir(batch_dir)) == 1


def test_plan_rejects_unknown_format(fake_scenes, output_dir):
    with pytest.raises(ValueError):
        planner.plan(fake_scenes, output_dir, fmt="csv")


def test_load_plan_without_plan(output_dir):
    with pytest.raises(FileNotFoundError):
        planner.load_plan(output_dir)
