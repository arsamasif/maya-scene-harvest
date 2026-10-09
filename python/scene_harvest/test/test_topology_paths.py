"""Tests for the pure-Python topology and path helpers.

"""

import os

import pytest

from scene_harvest import constants
from scene_harvest import paths
from scene_harvest import topology

QUAD_CUBE_COUNTS = [4] * 6
QUAD_CUBE_INDICES = [0, 3, 2, 1, 4, 5, 6, 7, 0, 1, 5, 4, 1, 2, 6, 5, 2, 3, 7, 6, 3, 0, 4, 7]


def test_cube_stats():
    stats = topology.mesh_stats(QUAD_CUBE_COUNTS, QUAD_CUBE_INDICES, 8)
    assert stats == {
        "vertex_count": 8,
        "face_count": 6,
        "edge_count": 12,
        "triangle_count": 12,
        "non_manifold_edges": 0,
        "lamina_faces": 0,
    }


def test_non_manifold_fin():
    # Three triangles hinged on edge (0, 1).
    stats = topology.mesh_stats([3, 3, 3], [0, 1, 2, 1, 0, 3, 0, 1, 4], 5)
    assert stats["non_manifold_edges"] == 1


def test_lamina_faces():
    stats = topology.mesh_stats([4, 4], [0, 1, 2, 3, 3, 2, 1, 0], 4)
    assert stats["lamina_faces"] == 2


def test_faces_validates_counts():
    with pytest.raises(ValueError):
        topology.faces([3, 3], [0, 1, 2])


def test_triangle_count_ngons():
    assert topology.triangle_count([3, 4, 5, 2]) == 1 + 2 + 3 + 0


def test_influences_per_vertex():
    weights = [1.0, 0.0, 0.5, 0.5, 0.2, 0.0]
    assert topology.influences_per_vertex(weights, 2) == [1, 2, 1]
    with pytest.raises(ValueError):
        topology.influences_per_vertex(weights, 4)


def test_influence_stats():
    assert topology.influence_stats([1, 2, 4, 1]) == (4, 2.0)
    assert topology.influence_stats([]) == (0, 0.0)


def test_scene_id_is_stable_and_path_based(tmp_path):
    path = str(tmp_path / "a.ma")
    assert paths.scene_id(path) == paths.scene_id(os.path.join(str(tmp_path), ".", "a.ma"))
    assert paths.scene_id(path) != paths.scene_id(str(tmp_path / "b.ma"))
    assert len(paths.scene_id(path)) == 16


def test_content_hash(tmp_path):
    path = tmp_path / "a.ma"
    path.write_bytes(b"x" * 10)
    first = paths.content_hash(str(path), chunk_size=3)
    assert first == paths.content_hash(str(path))
    path.write_bytes(b"y" * 10)
    assert paths.content_hash(str(path)) != first
    with pytest.raises(FileNotFoundError):
        paths.content_hash(str(tmp_path / "missing.ma"))


@pytest.mark.parametrize("pattern", [
    "tex.<UDIM>.exr", "tex.<udim>.exr", "tex.####.exr", "tex.%04d.exr", "tex.<f>.exr", "tex.$F4.exr",
])
def test_token_paths_exist_when_one_file_matches(tmp_path, pattern):
    (tmp_path / "tex.1001.exr").write_bytes(b"")
    path = str(tmp_path / pattern)
    assert paths.has_tokens(path)
    assert paths.exists(path)
    assert not paths.exists(str(tmp_path / pattern.replace("tex", "other")))


def test_plain_paths(tmp_path):
    (tmp_path / "a[1].png").write_bytes(b"")
    assert not paths.has_tokens("/textures/wood.png")
    assert paths.exists(str(tmp_path / "a[1].png"))
    assert not paths.exists("")


def test_resolve(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSET_ROOT", str(tmp_path))
    assert paths.resolve("$ASSET_ROOT/tex/a.png") == os.path.normpath(str(tmp_path / "tex" / "a.png"))
    assert paths.resolve("sourceimages/a.png", "/proj") == os.path.normpath("/proj/sourceimages/a.png")
    assert paths.resolve("") == ""


def test_classify_and_host_for_path():
    assert paths.classify("a.EXR") == "texture"
    assert paths.classify("a.abc") == "cache"
    assert paths.classify("a.usda") == "scene"
    assert paths.classify("a.bin", default="asset") == "asset"
    assert paths.host_for_path("/x/shot.MB") == constants.HOST_MAYA
    assert paths.host_for_path("/x/shot.usdc") == constants.HOST_USD
    assert paths.host_for_path("/x/shot.hip") == ""
