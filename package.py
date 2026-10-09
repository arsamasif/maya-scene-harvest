# -*- coding: utf-8 -*-
"""Rez package definition for scene_harvest.

"""

name = "scene_harvest"

version = "0.1.0"

authors = ["Arsam Ali"]

description = "Batch extraction of Maya and USD scene data into Parquet datasets for analysis and ML."

requires = [
    "python-3.9+",
    "pyarrow",
    "pandas",
]

variants = []

tests = {
    "unit": {
        "command": "python -m pytest {root}/python/scene_harvest/test",
        "requires": ["pytest", "usd_core"],
    },
    "maya": {
        "command": "mayapy -m pytest {root}/python/scene_harvest/test/test_maya_collectors.py",
        "requires": ["pytest", "maya-2024+"],
    },
}


def commands():
    env.PYTHONPATH.append("{root}/python")
    env.PATH.append("{root}/bin")
