"""Checkpointing: skip unchanged scenes, redo changed ones, resume after crashes.

These tests run the real planner, runner and worker loop with the fake
worker from fake_worker.py standing in for mayapy.

"""

import os
import sys

from scene_harvest import constants
from scene_harvest import manifest as manifest_mod
from scene_harvest import planner
from scene_harvest import registry
from scene_harvest import runner
from scene_harvest import worker
from scene_harvest import writers
from scene_harvest.test import fake_worker

FAKE_WORKER = "scene_harvest.test.fake_worker"


def _harvest(root, output_dir, batch_size=2, workers=2, **plan_kwargs):
    result = planner.plan(root, output_dir, batch_size=batch_size, **plan_kwargs)
    totals = runner.run(
        output_dir, workers=workers, mayapy=sys.executable, worker_module=FAKE_WORKER, echo=lambda _: None
    )
    return result, totals


def _statuses(output_dir):
    manifest = manifest_mod.Manifest.load(output_dir)
    return {os.path.basename(entry["path"]): entry["status"] for entry in manifest.entries.values()}


def test_first_run_harvests_everything(fake_scenes, output_dir):
    result, totals = _harvest(fake_scenes, output_dir)
    assert result["scheduled"] == 4
    assert totals[constants.STATUS_DONE] == 4
    assert set(_statuses(output_dir).values()) == {constants.STATUS_DONE}

    meshes = writers.read_table(output_dir, "meshes")
    scenes = writers.read_table(output_dir, "scenes")
    assert sorted(meshes["face_count"]) == [1, 2, 3, 4]
    assert len(scenes) == 4
    assert os.path.isfile(os.path.join(output_dir, constants.JOBS_DIR, constants.LOGS_DIR, "maya_0001.log"))


def test_rerun_skips_unchanged_scenes(fake_scenes, output_dir):
    _harvest(fake_scenes, output_dir)
    result, totals = _harvest(fake_scenes, output_dir)
    assert result["scheduled"] == 0
    assert result["skipped"] == 4
    assert totals["batches"] == 0
    assert len(writers.read_table(output_dir, "meshes")) == 4


def test_changed_scene_is_redone(fake_scenes, output_dir):
    _harvest(fake_scenes, output_dir)
    changed = os.path.join(fake_scenes, "props", "c_crate.ma")
    fake_worker.write_scene(changed, ["a", "b", "c", "d", "e", "f", "g"])

    result, totals = _harvest(fake_scenes, output_dir)
    assert result["scheduled"] == 1
    assert totals[constants.STATUS_DONE] == 1
    meshes = writers.read_table(output_dir, "meshes")
    assert sorted(meshes["face_count"]) == [1, 3, 4, 7]


def test_touched_but_identical_scene_is_skipped(fake_scenes, output_dir):
    _harvest(fake_scenes, output_dir)
    path = os.path.join(fake_scenes, "chars", "a_hero.ma")
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10 ** 9))

    result, _ = _harvest(fake_scenes, output_dir)
    assert result["scheduled"] == 0


def test_collector_change_reschedules_host(fake_scenes, output_dir, monkeypatch):
    _harvest(fake_scenes, output_dir)
    monkeypatch.setattr(registry.Registry, "signature", lambda self, host, names=None: "new-collectors")
    result = planner.plan(fake_scenes, output_dir)
    assert result["scheduled"] == 4


def test_force_reschedules_everything(fake_scenes, output_dir):
    _harvest(fake_scenes, output_dir)
    result = planner.plan(fake_scenes, output_dir, force=True)
    assert result["scheduled"] == 4


def test_worker_crash_resumes_where_it_stopped(fake_scenes, output_dir):
    crashing = os.path.join(fake_scenes, "props", "c_crate.ma")
    fake_worker.write_scene(crashing, ["a", "CRASH"])

    # One batch so the crash cuts it in half: a, b done; c crashes; d never runs.
    _, totals = _harvest(fake_scenes, output_dir, batch_size=10, workers=1)
    assert totals[constants.STATUS_DONE] == 2
    assert totals[constants.STATUS_FAILED] == 2
    statuses = _statuses(output_dir)
    assert statuses["a_hero.ma"] == statuses["b_villain.mb"] == constants.STATUS_DONE
    assert statuses["c_crate.ma"] == statuses["d_barrel.ma"] == constants.STATUS_FAILED
    error = manifest_mod.Manifest.load(output_dir).get(planner.fingerprint(crashing)["scene_id"])["error"]
    assert "exited with code {0}".format(fake_worker.CRASH_EXIT_CODE) in error

    fake_worker.write_scene(crashing, ["a", "b"])
    result, totals = _harvest(fake_scenes, output_dir, batch_size=10, workers=1)
    assert result["scheduled"] == 2
    assert result["skipped"] == 2
    assert totals[constants.STATUS_DONE] == 2
    assert set(_statuses(output_dir).values()) == {constants.STATUS_DONE}
    assert len(writers.read_table(output_dir, "meshes")) == 4


def test_failed_scene_is_recorded_and_retried(fake_scenes, output_dir):
    broken = os.path.join(fake_scenes, "chars", "b_villain.mb")
    fake_worker.write_scene(broken, ["BROKEN"])
    _, totals = _harvest(fake_scenes, output_dir)
    assert totals[constants.STATUS_FAILED] == 1
    assert "Scene is broken" in manifest_mod.Manifest.load(output_dir).get(
        planner.fingerprint(broken)["scene_id"]
    )["error"]

    assert planner.plan(fake_scenes, output_dir, retry_failed=False)["scheduled"] == 0
    assert planner.plan(fake_scenes, output_dir)["scheduled"] == 1


def test_runner_crash_is_recovered_from_markers(fake_scenes, output_dir):
    """The worker finished but the runner died before saving the manifest."""
    result = planner.plan(fake_scenes, output_dir, batch_size=10)
    batch = worker.load_batch(result["batches"][0])
    worker.run_batch(batch, fake_worker.FakeAdapter, [fake_worker.FakeMeshCollector()], batch["signature"])
    assert set(_statuses(output_dir).values()) == {constants.STATUS_PENDING}

    replanned = planner.plan(fake_scenes, output_dir)
    assert replanned["scheduled"] == 0
    assert set(_statuses(output_dir).values()) == {constants.STATUS_DONE}


def test_stale_marker_does_not_hide_a_change(fake_scenes, output_dir):
    _harvest(fake_scenes, output_dir)
    changed = os.path.join(fake_scenes, "chars", "a_hero.ma")
    fake_worker.write_scene(changed, ["x"] * 9)
    planner.plan(fake_scenes, output_dir)
    # Runner never ran: replanning must still see the scene as changed.
    assert planner.plan(fake_scenes, output_dir)["scheduled"] == 1


def test_manifest_counts_and_reconcile_are_idempotent(fake_scenes, output_dir):
    _harvest(fake_scenes, output_dir)
    manifest = manifest_mod.Manifest.load(output_dir)
    assert manifest.counts() == {constants.STATUS_DONE: 4}
    assert manifest.reconcile(output_dir) == 0
