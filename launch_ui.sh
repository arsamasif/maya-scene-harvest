#!/bin/sh
# Opens the Scene Harvest app on Linux or macOS. The first run creates a
# .venv next to this file and installs the app into it; set PYTHON to pick
# the interpreter (64-bit Python 3.10-3.13).
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo "First run: setting up a Python environment in .venv..."
    "${PYTHON:-python3}" -m venv .venv
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -e ".[ui]"
fi
exec .venv/bin/python -m scene_harvest.ui
