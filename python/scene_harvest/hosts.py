"""Lookup of host adapters.

A host adapter is a module with three functions: `initialize()` to boot the
DCC once per process, `open_scene(path)` returning a handle the collectors
can use, and `close_scene(handle)`. The worker only talks to this
interface, which keeps Maya imports out of everything else.

"""

import importlib

from scene_harvest import constants

ADAPTER_MODULES = {
    constants.HOST_MAYA: "scene_harvest.maya_adapter.host",
    constants.HOST_USD: "scene_harvest.usd_adapter.host",
}


def load_adapter(host):
    """Import the adapter module of a host.

    Args:
        host (str): Host name.

    Returns:
        module: Adapter with initialize, open_scene and close_scene.

    Raises:
        ValueError: If the host is unknown.

    """
    if host not in ADAPTER_MODULES:
        raise ValueError("No adapter for host {0!r}".format(host))
    return importlib.import_module(ADAPTER_MODULES[host])
