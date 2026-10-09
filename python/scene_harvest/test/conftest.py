"""Shared fixtures: fake Maya scene folders and the generated USD library.

"""

import os

import pytest

from scene_harvest.test import fake_worker


@pytest.fixture
def fake_scenes(tmp_path):
    """Four fake Maya scenes in two sub folders.

    Returns:
        str: Root folder of the scenes.

    """
    root = str(tmp_path / "scenes")
    fake_worker.write_scene(os.path.join(root, "chars", "a_hero.ma"), ["a", "b", "c"])
    fake_worker.write_scene(os.path.join(root, "chars", "b_villain.mb"), ["a"])
    fake_worker.write_scene(os.path.join(root, "props", "c_crate.ma"), ["a", "b"])
    fake_worker.write_scene(os.path.join(root, "props", "d_barrel.ma"), ["a", "b", "c", "d"])
    return root


@pytest.fixture
def output_dir(tmp_path):
    """Empty harvest output folder.

    Returns:
        str: Folder path.

    """
    return str(tmp_path / "harvest")


@pytest.fixture(scope="session")
def usd_library(tmp_path_factory):
    """The sample USD library from usd_samples, built once per session.

    Returns:
        dict: Paths from usd_samples.build plus "root".

    """
    pytest.importorskip("pxr")
    from scene_harvest.test import usd_samples

    root = str(tmp_path_factory.mktemp("usd_library"))
    library = usd_samples.build(root)
    library["root"] = root
    return library
