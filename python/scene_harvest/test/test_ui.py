"""Offscreen tests for the desktop window; skipped without PySide6 or matplotlib.

The last test runs a real harvest of the USD sample library through the
window's QProcess, the same way a user clicking Run does.

"""

import os

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from scene_harvest import ui  # noqa: E402


@pytest.fixture
def window(tmp_path):
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    settings = QtCore.QSettings(str(tmp_path / "ui.ini"), QtCore.QSettings.IniFormat)
    window = ui.HarvestWindow(mayapy_paths=["C:/maya/bin/mayapy.exe"], settings=settings)
    yield window
    window.close()


@pytest.mark.parametrize("line, expected", [
    ("Planned 5 scene(s) in 2 batch(es), 0 unchanged scene(s) skipped.", (5, 0)),
    ("  maya_0001: 3 done, 1 failed (exit 1)", (None, 4)),
    ("Finished: 4 done, 1 failed.", (None, 0)),
])
def test_progress_from_line(line, expected):
    assert ui.progress_from_line(line) == expected


def test_command_follows_the_options(window):
    window.scenes_edit.setText("D:/library")
    window.output_edit.setText("D:/harvest")
    window.workers_spin.setValue(1)
    window.force_check.setChecked(True)
    arguments = window.command_arguments()
    assert arguments[:5] == ["-u", "-m", "scene_harvest", "run", "D:/library"]
    assert arguments[arguments.index("-o") + 1] == "D:/harvest"
    assert arguments[arguments.index("--mayapy") + 1] == "C:/maya/bin/mayapy.exe"
    assert "--force" in arguments and "--skip-failed" not in arguments


def test_run_checks_the_folders_first(window, tmp_path):
    window.scenes_edit.setText(str(tmp_path / "nope"))
    window.run()
    assert window.status_label.text() == "Pick a folder of scenes."
    assert not window.is_running()


def test_settings_are_remembered(tmp_path):
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = str(tmp_path / "ui.ini")
    first = ui.HarvestWindow(mayapy_paths=[], settings=QtCore.QSettings(path, QtCore.QSettings.IniFormat))
    first.scenes_edit.setText("D:/library")
    first.close()
    second = ui.HarvestWindow(mayapy_paths=[], settings=QtCore.QSettings(path, QtCore.QSettings.IniFormat))
    assert second.scenes_edit.text() == "D:/library"
    second.close()


def test_run_a_usd_harvest_and_show_results(window, usd_library, tmp_path):
    pytest.importorskip("pxr")
    window.mayapy_combo.setEditText("")
    window.scenes_edit.setText(usd_library["root"])
    window.output_edit.setText(str(tmp_path / "harvest"))
    window.run()
    assert window.is_running()
    assert window.process.waitForFinished(180000)
    QtWidgets.QApplication.processEvents()

    assert "Finished: 3 done, 0 failed." in window.log.toPlainText()
    assert window.status_label.text().startswith("Manifest: 3 done")
    assert window.status_tree.topLevelItemCount() == 3
    assert "Triangles per scene" in window.report_view.toPlainText()
    assert len(window._canvases) >= 3
    assert window.progress.value() == window.progress.maximum() == 3
