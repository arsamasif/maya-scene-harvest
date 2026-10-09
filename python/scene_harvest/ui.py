"""Desktop window for running harvests and looking at the results.

The window builds the same ``scene-harvest run`` command a user would type
and runs it with ``QProcess`` (``python -u -m scene_harvest run ...``), so
the UI and the CLI always behave the same and a long run never blocks the
window. Output streams into the log tab. When a run ends, or when an
existing output folder is opened, the Status, Report and Charts tabs are
filled from the manifest and the dataset. Start it with ``scene-harvest-ui``.

"""

import os
import re
import sys

from PySide6 import QtCore, QtGui, QtWidgets

from scene_harvest import constants
from scene_harvest import manifest as manifest_mod
from scene_harvest import runner

WINDOW_TITLE = "Scene Harvest"
SETTINGS_ORG = "scene_harvest"
SETTINGS_APP = "ui"
MONOSPACE = "Consolas, Menlo, monospace"
ERROR_STYLE = "color: #d03b3b;"
OK_STYLE = "color: #0ca30c;"


class HarvestWindow(QtWidgets.QMainWindow):
    """Pick folders and options, run a harvest, browse what it found.

    Args:
        python (str, optional): Interpreter that runs ``scene_harvest``,
            the current one by default.
        mayapy_paths (list, optional): mayapy executables offered in the
            combo box; found with ``runner.find_mayapy`` by default.
        settings (QSettings, optional): Where the last folders are kept.
        parent (QWidget, optional): Parent widget.

    """

    def __init__(self, python=None, mayapy_paths=None, settings=None, parent=None):
        super().__init__(parent)
        self.python = python or sys.executable
        self.settings = settings or QtCore.QSettings(SETTINGS_ORG, SETTINGS_APP)
        self.process = QtCore.QProcess(self)
        self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._run_finished)
        self._planned = 0
        self._processed = 0
        self._canvases = []

        self.setWindowTitle(WINDOW_TITLE)
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.addWidget(self._options_box(mayapy_paths))
        layout.addLayout(self._buttons())
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.status_label = QtWidgets.QLabel()
        layout.addWidget(self.status_label)
        layout.addWidget(self._tabs(), 1)
        self.setCentralWidget(central)
        self.resize(980, 760)
        self._restore()

    def command_arguments(self):
        """Return the arguments of the run command for the current options.

        Returns:
            list: Arguments for ``python``, starting with ``-u -m``.

        """
        arguments = [
            "-u", "-m", "scene_harvest", "run", self.scenes_edit.text().strip(),
            "-o", self.output_edit.text().strip(),
            "-w", str(self.workers_spin.value()),
            "-f", self.format_combo.currentText(),
        ]
        mayapy = self.mayapy_combo.currentText().strip()
        if mayapy:
            arguments += ["--mayapy", mayapy]
        if self.force_check.isChecked():
            arguments.append("--force")
        if self.skip_failed_check.isChecked():
            arguments.append("--skip-failed")
        return arguments

    def run(self):
        """Start a harvest with the current options."""
        problem = self._check_options()
        if problem:
            self._report_status(problem, ok=False)
            return
        self._save()
        self.log.clear()
        self._planned = 0
        self._processed = 0
        self.progress.setRange(0, 0)
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONPATH", runner.worker_env()["PYTHONPATH"])
        self.process.setProcessEnvironment(environment)
        self.process.start(self.python, self.command_arguments())
        self._set_running(True)
        self._report_status("Running...")

    def stop(self):
        """Kill a running harvest. Finished scenes stay in the manifest."""
        if self.process.state() != QtCore.QProcess.NotRunning:
            self.process.kill()
            self._report_status("Stopped. Run again to pick up where it left off.", ok=False)

    def is_running(self):
        """bool: True while a harvest process is running."""
        return self.process.state() != QtCore.QProcess.NotRunning

    def refresh(self):
        """Reload the Status, Report and Charts tabs from the output folder."""
        output_dir = self.output_edit.text().strip()
        if not output_dir or not os.path.isdir(output_dir):
            self._report_status("Pick an output folder to see results.", ok=False)
            return
        self._fill_status(output_dir)
        self._fill_report(output_dir)
        self._fill_charts(output_dir)

    def closeEvent(self, event):
        """Kill a running harvest and remember the folders."""
        if self.is_running():
            self.process.kill()
            self.process.waitForFinished(3000)
        self._save()
        super().closeEvent(event)

    def _options_box(self, mayapy_paths):
        box = QtWidgets.QGroupBox("Harvest")
        form = QtWidgets.QFormLayout(box)
        self.scenes_edit = QtWidgets.QLineEdit()
        self.output_edit = QtWidgets.QLineEdit()
        self.output_edit.editingFinished.connect(self.refresh)
        form.addRow("Scenes folder", self._with_browse(self.scenes_edit, self._browse_folder))
        form.addRow("Output folder", self._with_browse(self.output_edit, self._browse_output))

        self.mayapy_combo = QtWidgets.QComboBox()
        self.mayapy_combo.setEditable(True)
        paths = runner.find_mayapy() if mayapy_paths is None else mayapy_paths
        self.mayapy_combo.addItems(paths)
        self.mayapy_combo.lineEdit().setPlaceholderText("path to mayapy (only needed for Maya scenes)")
        form.addRow("mayapy", self._with_browse(self.mayapy_combo, self._browse_mayapy))

        options = QtWidgets.QHBoxLayout()
        self.workers_spin = QtWidgets.QSpinBox()
        self.workers_spin.setRange(1, max(1, os.cpu_count() or 1))
        self.workers_spin.setValue(min(constants.DEFAULT_WORKERS, self.workers_spin.maximum()))
        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItems(constants.FORMATS)
        self.force_check = QtWidgets.QCheckBox("Force (harvest everything again)")
        self.skip_failed_check = QtWidgets.QCheckBox("Skip scenes that failed before")
        options.addWidget(QtWidgets.QLabel("Workers"))
        options.addWidget(self.workers_spin)
        options.addSpacing(12)
        options.addWidget(QtWidgets.QLabel("Format"))
        options.addWidget(self.format_combo)
        options.addSpacing(12)
        options.addWidget(self.force_check)
        options.addWidget(self.skip_failed_check)
        options.addStretch(1)
        form.addRow(options)
        return box

    def _buttons(self):
        row = QtWidgets.QHBoxLayout()
        self.run_button = QtWidgets.QPushButton("Run")
        self.run_button.clicked.connect(self.run)
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.setEnabled(False)
        refresh_button = QtWidgets.QPushButton("Refresh results")
        refresh_button.clicked.connect(self.refresh)
        for button in (self.run_button, self.stop_button, refresh_button):
            row.addWidget(button)
        row.addStretch(1)
        return row

    def _tabs(self):
        self.tabs = QtWidgets.QTabWidget()
        self.log = _text_view()
        self.status_tree = QtWidgets.QTreeWidget()
        self.status_tree.setHeaderLabels(["Scene", "Status", "Error"])
        self.status_tree.setRootIsDecorated(False)
        self.report_view = _text_view()
        self.charts_widget = QtWidgets.QWidget()
        self.charts_layout = QtWidgets.QVBoxLayout(self.charts_widget)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.charts_widget)
        self.tabs.addTab(self.log, "Log")
        self.tabs.addTab(self.status_tree, "Status")
        self.tabs.addTab(self.report_view, "Report")
        self.tabs.addTab(scroll, "Charts")
        return self.tabs

    def _with_browse(self, field, slot):
        widget = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        button = QtWidgets.QPushButton("Browse...")
        button.clicked.connect(slot)
        row.addWidget(field, 1)
        row.addWidget(button)
        return widget

    def _browse_folder(self):
        self._pick_folder(self.scenes_edit, "Folder of scenes")

    def _browse_output(self):
        if self._pick_folder(self.output_edit, "Output folder"):
            self.refresh()

    def _pick_folder(self, edit, caption):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, caption, edit.text())
        if folder:
            edit.setText(os.path.normpath(folder))
        return bool(folder)

    def _browse_mayapy(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "mayapy executable")
        if path:
            self.mayapy_combo.setEditText(os.path.normpath(path))

    def _check_options(self):
        scenes = self.scenes_edit.text().strip()
        if not scenes or not os.path.isdir(scenes):
            return "Pick a folder of scenes."
        if not self.output_edit.text().strip():
            return "Pick an output folder."
        mayapy = self.mayapy_combo.currentText().strip()
        if mayapy and not os.path.isfile(mayapy):
            return "mayapy not found: {0}".format(mayapy)
        return ""

    def _read_output(self):
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        self.log.moveCursor(QtGui.QTextCursor.End)
        self.log.insertPlainText(text)
        self.log.moveCursor(QtGui.QTextCursor.End)
        for line in text.splitlines():
            self._track_progress(line)

    def _track_progress(self, line):
        planned, processed = progress_from_line(line)
        if planned is not None:
            self._planned = planned
            self.progress.setRange(0, max(planned, 1))
            self.progress.setValue(0)
        if processed:
            self._processed += processed
            self.progress.setValue(self._processed)

    def _run_finished(self, exit_code, exit_status):
        self._set_running(False)
        self.progress.setRange(0, max(self._planned, 1))
        self.progress.setValue(self._planned if exit_code == 0 else self._processed)
        if exit_status == QtCore.QProcess.NormalExit and exit_code == 0:
            self._report_status("Finished: {0} scene(s) harvested.".format(self._processed))
        elif exit_status == QtCore.QProcess.NormalExit:
            self._report_status("Finished with failures; see the Status tab.", ok=False)
        self.refresh()

    def _set_running(self, running):
        self.run_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def _fill_status(self, output_dir):
        self.status_tree.clear()
        manifest = manifest_mod.Manifest.load(output_dir)
        for scene_id in sorted(manifest.entries, key=lambda key: manifest.get(key).get("path", key)):
            entry = manifest.get(scene_id)
            item = QtWidgets.QTreeWidgetItem(
                [entry.get("path", scene_id), entry.get("status", ""), entry.get("error", "")]
            )
            self.status_tree.addTopLevelItem(item)
        self.status_tree.resizeColumnToContents(0)
        counts = ", ".join("{0} {1}".format(count, status) for status, count in sorted(manifest.counts().items()))
        if counts:
            self._report_status("Manifest: " + counts)

    def _fill_report(self, output_dir):
        from scene_harvest import report

        try:
            self.report_view.setPlainText(report.render(report.summarize(output_dir)))
        except (OSError, ValueError, KeyError) as error:
            self.report_view.setPlainText("No report yet: {0}".format(error))

    def _fill_charts(self, output_dir):
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

        from scene_harvest import charts

        for canvas in self._canvases:
            canvas.setParent(None)
            canvas.deleteLater()
        self._canvases = []
        for _, figure in charts.figures(output_dir):
            canvas = FigureCanvasQTAgg(figure)
            canvas.setMinimumHeight(int(figure.get_figheight() * figure.get_dpi()))
            self.charts_layout.addWidget(canvas)
            self._canvases.append(canvas)

    def _report_status(self, text, ok=True):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(OK_STYLE if ok else ERROR_STYLE)

    def _restore(self):
        self.scenes_edit.setText(self.settings.value("scenes", "") or "")
        self.output_edit.setText(self.settings.value("output", "") or "")
        mayapy = self.settings.value("mayapy", "") or ""
        if mayapy:
            self.mayapy_combo.setEditText(mayapy)
        if self.output_edit.text() and os.path.isdir(self.output_edit.text()):
            self.refresh()

    def _save(self):
        self.settings.setValue("scenes", self.scenes_edit.text().strip())
        self.settings.setValue("output", self.output_edit.text().strip())
        self.settings.setValue("mayapy", self.mayapy_combo.currentText().strip())


def progress_from_line(line):
    """Read run progress from one line of ``scene-harvest run`` output.

    Args:
        line (str): Output line.

    Returns:
        tuple: (scenes planned or None, scenes processed in a finished batch).

    """
    planned = re.match(r"Planned (\d+) scene", line)
    if planned:
        return int(planned.group(1)), 0
    batch = re.match(r"\s+\S+: (\d+) done, (\d+) failed", line)
    if batch:
        return None, int(batch.group(1)) + int(batch.group(2))
    return None, 0


def _text_view():
    view = QtWidgets.QPlainTextEdit()
    view.setReadOnly(True)
    view.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
    view.setStyleSheet("font-family: {0}; font-size: 9pt;".format(MONOSPACE))
    return view


def main():
    """Entry point of ``scene-harvest-ui``.

    Returns:
        int: Qt exit code.

    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    window = HarvestWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
