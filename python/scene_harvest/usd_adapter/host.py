"""USD host adapter: opens layers as stages with pxr.

Stages are opened with all payloads loaded so collectors see the full
scene, the same way a Maya scene is opened with all references loaded.

"""

import os

from pxr import Usd


def initialize():
    """Nothing to boot for USD; present for the adapter interface."""


def open_scene(path):
    """Open a USD file as a stage.

    Args:
        path (str): .usd, .usda or .usdc file.

    Returns:
        Usd.Stage: The opened stage.

    Raises:
        FileNotFoundError: If the file does not exist.
        RuntimeError: If USD cannot open it.

    """
    if not os.path.isfile(path):
        raise FileNotFoundError("Missing USD file: {0}".format(path))
    stage = Usd.Stage.Open(path, Usd.Stage.LoadAll)
    if stage is None:
        raise RuntimeError("USD could not open {0}".format(path))
    return stage


def close_scene(handle):
    """Release a stage. USD frees it once the last reference goes away.

    Args:
        handle (Usd.Stage): Stage from open_scene.

    """
    del handle
