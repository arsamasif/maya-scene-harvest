"""Tests for the collector base class and registry.

"""

import sys

import pytest

from scene_harvest import constants
from scene_harvest import registry
from scene_harvest import schema


def _collector(name, host="maya", version=1):
    return type(
        "Test{0}".format(name.title()),
        (registry.Collector,),
        {"name": name, "host": host, "version": version, "collect": lambda self, context: []},
    )


def test_register_and_get():
    reg = registry.Registry()
    collector_class = reg.register(_collector("meshes"))
    assert reg.get("maya", "meshes") is collector_class
    assert reg.names("maya") == ["meshes"]
    assert reg.names("usd") == []


def test_register_is_idempotent_for_same_class():
    reg = registry.Registry()
    collector_class = _collector("meshes")
    reg.register(collector_class)
    reg.register(collector_class)
    assert reg.names("maya") == ["meshes"]


def test_register_rejects_duplicates_and_bad_classes():
    reg = registry.Registry()
    reg.register(_collector("meshes"))
    with pytest.raises(ValueError):
        reg.register(_collector("meshes"))
    with pytest.raises(ValueError):
        reg.register(_collector(""))
    with pytest.raises(TypeError):
        reg.register(object)


def test_get_unknown_raises_key_error():
    with pytest.raises(KeyError):
        registry.Registry().get("maya", "nope")


def test_for_host_instantiates_sorted_and_filters():
    reg = registry.Registry()
    for name in ("skinning", "hierarchy", "meshes"):
        reg.register(_collector(name))
    reg.register(_collector("meshes", host="usd"))
    assert [c.name for c in reg.for_host("maya")] == ["hierarchy", "meshes", "skinning"]
    assert [c.name for c in reg.for_host("maya", names=["meshes"])] == ["meshes"]
    assert all(isinstance(c, registry.Collector) for c in reg.for_host("usd"))


def test_signature_tracks_collector_set_and_versions():
    reg = registry.Registry()
    reg.register(_collector("meshes"))
    first = reg.signature("maya")
    assert reg.signature("maya") == first

    reg.register(_collector("skinning"))
    second = reg.signature("maya")
    assert second != first

    reg.unregister("maya", "skinning")
    reg.unregister("maya", "meshes")
    reg.register(_collector("meshes", version=2))
    assert reg.signature("maya") not in (first, second)


def test_base_collect_is_abstract():
    with pytest.raises(NotImplementedError):
        registry.Collector().collect(None)


def test_builtin_collectors_register_without_maya():
    reg = registry.load_builtin(constants.HOST_MAYA)
    assert reg.names(constants.HOST_MAYA) == ["dependencies", "hierarchy", "materials", "meshes", "skinning"]


def test_builtin_usd_collectors_match_maya_names():
    pytest.importorskip("pxr")
    reg = registry.load_builtin(constants.HOST_USD)
    assert reg.names(constants.HOST_USD) == reg.names(constants.HOST_MAYA)


def test_unknown_host_raises():
    with pytest.raises(ValueError):
        registry.load_builtin("houdini")


def test_plugins_from_environment(tmp_path, monkeypatch):
    plugin = tmp_path / "studio_collectors.py"
    plugin.write_text(
        "from scene_harvest import registry\n"
        "\n"
        "@registry.register\n"
        "class LightCollector(registry.Collector):\n"
        "    name = 'lights_plugin_test'\n"
        "    host = 'maya'\n"
        "\n"
        "    def collect(self, context):\n"
        "        return []\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(constants.PLUGINS_ENV_VAR, "studio_collectors")
    try:
        reg = registry.load_builtin(constants.HOST_MAYA)
        assert "lights_plugin_test" in reg.names(constants.HOST_MAYA)
    finally:
        registry.REGISTRY.unregister(constants.HOST_MAYA, "lights_plugin_test")
        sys.modules.pop("studio_collectors", None)


def test_scene_context_copies_options():
    options = {"topology_checks": False}
    context = registry.SceneContext("id", "/a.ma", "maya", options=options)
    context.options["topology_checks"] = True
    assert options["topology_checks"] is False
    assert schema.NOT_COMPUTED == -1
