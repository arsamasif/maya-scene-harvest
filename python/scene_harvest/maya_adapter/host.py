"""Maya host adapter: boots maya.standalone and opens scenes.

Scenes are opened with every reference loaded, without UI prompts and
ignoring version mismatches, which is what an unattended batch needs. A
scene with broken references makes `cmds.file` raise even though the
scene did open; that case is accepted so the dependency collector can
report the missing files instead of losing the whole scene.

"""

import os
import sys

OPTIONAL_PLUGINS = ("AbcImport", "gpuCache", "mtoa")

_STATE = {"initialized": False}


def initialize():
    """Start Maya in this interpreter once and load the optional plugins."""
    if _STATE["initialized"]:
        return
    import maya.standalone

    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        # Already running inside an initialized Maya session.
        pass
    from maya import cmds

    for plugin in OPTIONAL_PLUGINS:
        try:
            cmds.loadPlugin(plugin, quiet=True)
        except RuntimeError:
            print("scene_harvest: plugin {0} not available".format(plugin), file=sys.stderr)
    _STATE["initialized"] = True


def open_scene(path):
    """Open a Maya scene for harvesting.

    Args:
        path (str): .ma or .mb file.

    Returns:
        str: The path of the opened scene, used as the handle.

    Raises:
        FileNotFoundError: If the file does not exist.
        RuntimeError: If Maya could not open the scene at all.

    """
    from maya import cmds

    if not os.path.isfile(path):
        raise FileNotFoundError("Missing Maya scene: {0}".format(path))
    cmds.file(new=True, force=True)
    try:
        cmds.file(
            path,
            open=True,
            force=True,
            prompt=False,
            ignoreVersion=True,
            loadReferenceDepth="all",
        )
    except RuntimeError as error:
        opened = cmds.file(query=True, sceneName=True) or ""
        if os.path.normcase(os.path.abspath(opened)) != os.path.normcase(os.path.abspath(path)):
            raise
        print("scene_harvest: opened with errors: {0}".format(error), file=sys.stderr)
    return path


def close_scene(handle):
    """Discard the open scene.

    Args:
        handle (str): Value returned by open_scene.

    """
    from maya import cmds

    del handle
    cmds.file(new=True, force=True)
