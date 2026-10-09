"""Collector base class and the plugin registry.

A collector is a small class that reads one aspect of an open scene and
yields records. Collectors register themselves with the module level
registry through the `register` decorator, scoped to the host they run in.
The worker asks the registry for every collector of its host, so adding a
new one means writing the class and importing its module, nothing else.

Third party collector modules can be listed in the SCENE_HARVEST_PLUGINS
environment variable (comma separated module names) and are imported when
the built-in collectors load.

"""

import hashlib
import importlib
import os

from scene_harvest import constants

BUILTIN_MODULES = {
    constants.HOST_MAYA: "scene_harvest.maya_adapter.collectors",
    constants.HOST_USD: "scene_harvest.usd_adapter.collectors",
}


class Collector(object):
    """Base class for all collectors.

    Subclasses set `name`, `host` and `version`, and implement `collect`.
    Bump `version` whenever the output of a collector changes, so scenes
    harvested with the old logic get scheduled again.

    """

    name = ""
    host = ""
    version = 1
    description = ""

    def collect(self, context):
        """Yield records for the open scene.

        Args:
            context (SceneContext): The scene being harvested.

        Returns:
            iterable: Record dataclass instances from scene_harvest.schema.

        """
        raise NotImplementedError("{0} must implement collect()".format(type(self).__name__))

    def __repr__(self):
        return "<{0} {1}@{2} v{3}>".format(type(self).__name__, self.name, self.host, self.version)


class SceneContext(object):
    """What a collector needs to know about the scene it is reading.

    Args:
        scene_id (str): Stable id of the scene inside the dataset.
        scene_path (str): Absolute path of the scene file.
        host (str): Host name the scene was opened in.
        handle (object): Host specific handle, e.g. a Usd.Stage. None in Maya,
            where the open scene is global.
        options (dict): Free-form collector options from the batch file.

    """

    def __init__(self, scene_id, scene_path, host, handle=None, options=None):
        self.scene_id = scene_id
        self.scene_path = scene_path
        self.host = host
        self.handle = handle
        self.options = dict(options or {})


class Registry(object):
    """Holds collector classes keyed by host and name."""

    def __init__(self):
        self._collectors = {}

    def register(self, collector_class):
        """Register a collector class. Usable as a class decorator.

        Args:
            collector_class (type): Subclass of Collector.

        Returns:
            type: The same class, so the decorator is transparent.

        Raises:
            TypeError: If the class is not a Collector subclass.
            ValueError: If name or host are missing, or the key is taken.

        """
        if not (isinstance(collector_class, type) and issubclass(collector_class, Collector)):
            raise TypeError("Not a Collector subclass: {0!r}".format(collector_class))
        if not collector_class.name or not collector_class.host:
            raise ValueError("Collector {0} needs a name and a host".format(collector_class.__name__))
        key = (collector_class.host, collector_class.name)
        existing = self._collectors.get(key)
        if existing is not None and existing is not collector_class:
            raise ValueError("Collector already registered: {0}/{1}".format(*key))
        self._collectors[key] = collector_class
        return collector_class

    def unregister(self, host, name):
        """Remove a collector. Missing keys are ignored.

        Args:
            host (str): Host name.
            name (str): Collector name.

        """
        self._collectors.pop((host, name), None)

    def get(self, host, name):
        """Return one collector class.

        Args:
            host (str): Host name.
            name (str): Collector name.

        Returns:
            type: The registered class.

        Raises:
            KeyError: If nothing is registered under that key.

        """
        try:
            return self._collectors[(host, name)]
        except KeyError:
            raise KeyError("No collector {0!r} for host {1!r}".format(name, host)) from None

    def for_host(self, host, names=None):
        """Instantiate the collectors of a host, sorted by name.

        Args:
            host (str): Host name.
            names (list, optional): Restrict to these collector names.

        Returns:
            list: Collector instances.

        """
        keys = sorted(key for key in self._collectors if key[0] == host)
        if names is not None:
            wanted = set(names)
            keys = [key for key in keys if key[1] in wanted]
        return [self._collectors[key]() for key in keys]

    def names(self, host):
        """Return the sorted collector names of a host.

        Args:
            host (str): Host name.

        Returns:
            list: Collector names.

        """
        return sorted(name for key_host, name in self._collectors if key_host == host)

    def signature(self, host, names=None):
        """Fingerprint the collector set of a host.

        The manifest stores this next to each scene. When a collector is
        added, removed or bumped in version, the signature changes and every
        scene of that host is harvested again.

        Args:
            host (str): Host name.
            names (list, optional): Restrict to these collector names.

        Returns:
            str: Short hex digest.

        """
        parts = ["schema={0}".format(constants.SCHEMA_VERSION)]
        for collector in self.for_host(host, names):
            parts.append("{0}:{1}".format(collector.name, collector.version))
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


REGISTRY = Registry()
register = REGISTRY.register


def load_builtin(host):
    """Import the built-in collector module of a host plus any plugins.

    Args:
        host (str): Host name.

    Returns:
        Registry: The module level registry, now populated.

    Raises:
        ValueError: If the host is unknown.

    """
    if host not in BUILTIN_MODULES:
        raise ValueError("Unknown host: {0}".format(host))
    importlib.import_module(BUILTIN_MODULES[host])
    for module_name in _plugin_modules():
        importlib.import_module(module_name)
    return REGISTRY


def _plugin_modules():
    raw = os.environ.get(constants.PLUGINS_ENV_VAR, "")
    return [name.strip() for name in raw.split(",") if name.strip()]
