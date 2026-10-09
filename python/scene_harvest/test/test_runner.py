"""Tests for worker command building and runner edge cases.

"""

import os
import sys

import pytest

from scene_harvest import constants
from scene_harvest import runner
from scene_harvest import worker


def test_interpreter_for_maya_prefers_argument_then_env(monkeypatch):
    monkeypatch.delenv(constants.MAYAPY_ENV_VAR, raising=False)
    assert runner.interpreter_for(constants.HOST_MAYA) == "mayapy"
    monkeypatch.setenv(constants.MAYAPY_ENV_VAR, "/opt/maya2025/bin/mayapy")
    assert runner.interpreter_for(constants.HOST_MAYA) == "/opt/maya2025/bin/mayapy"
    assert runner.interpreter_for(constants.HOST_MAYA, mayapy="/custom/mayapy") == "/custom/mayapy"


def test_interpreter_for_usd_is_current_python():
    assert runner.interpreter_for(constants.HOST_USD) == sys.executable


def test_build_command():
    command = runner.build_command("/jobs/maya_0001.json", constants.HOST_MAYA, mayapy="mayapy")
    assert command == ["mayapy", "-m", "scene_harvest.worker", "/jobs/maya_0001.json"]


def test_worker_env_puts_package_first(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/elsewhere")
    parts = runner.worker_env()["PYTHONPATH"].split(os.pathsep)
    assert parts == [runner.PACKAGE_ROOT, "/elsewhere"]
    assert os.path.isfile(os.path.join(parts[0], "scene_harvest", "worker.py"))


def test_run_rejects_zero_workers(output_dir):
    with pytest.raises(ValueError):
        runner.run(output_dir, workers=0)


def test_run_without_plan(output_dir):
    with pytest.raises(FileNotFoundError):
        runner.run(output_dir)


def test_run_batch_process_reports_missing_interpreter(tmp_path):
    job = {
        "batch_path": "x.json",
        "command": [str(tmp_path / "no-such-mayapy"), "-m", "scene_harvest.worker", "x.json"],
        "log_path": str(tmp_path / "logs" / "x.log"),
        "timeout": None,
    }
    assert runner.run_batch_process(job)["returncode"] == -2
    with open(job["log_path"], encoding="utf-8") as handle:
        assert "could not start worker" in handle.read()


def test_load_batch_validates(tmp_path):
    with pytest.raises(FileNotFoundError):
        worker.load_batch(str(tmp_path / "missing.json"))
    path = tmp_path / "bad.json"
    path.write_text('{"batch_id": "b"}')
    with pytest.raises(ValueError):
        worker.load_batch(str(path))


def test_find_mayapy_lists_newest_maya_first(tmp_path):
    for version in ("Maya2025", "Maya2027", "Maya2026"):
        folder = tmp_path / version / "bin"
        folder.mkdir(parents=True)
        (folder / "mayapy.exe").write_text("")
    (tmp_path / "Maya2024" / "bin").mkdir(parents=True)
    found = runner.find_mayapy([str(tmp_path / "Maya*" / "bin" / "mayapy.exe")])
    assert [os.path.basename(os.path.dirname(os.path.dirname(path))) for path in found] == [
        "Maya2027", "Maya2026", "Maya2025",
    ]
