"""Tests for the record schema and the Parquet / JSON writers.

"""

import os

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scene_harvest import constants
from scene_harvest import schema
from scene_harvest import writers


def _mesh(scene_id, triangles=12):
    return schema.MeshRecord(
        scene_id=scene_id,
        shape_path="|cube|cubeShape",
        vertex_count=8,
        face_count=6,
        edge_count=12,
        triangle_count=triangles,
        uv_sets=["map1", "lightmap"],
        bbox_min=[-1.0, -1.0, -1.0],
        bbox_max=[1.0, 1.0, 1.0],
        world_matrix=[float(value) for value in range(16)],
    )


def _dependency(scene_id, exists):
    return schema.DependencyRecord(
        scene_id=scene_id, kind="texture", node="file1", attribute="fileTextureName",
        path="tex.png", resolved_path="/tex.png", exists=exists,
    )


def test_arrow_schema_types():
    mesh_schema = schema.table_schema("meshes")
    assert mesh_schema.field("vertex_count").type == pa.int64()
    assert mesh_schema.field("uv_sets").type == pa.list_(pa.string())
    assert mesh_schema.field("world_matrix").type == pa.list_(pa.float64())
    assert schema.table_schema("dependencies").field("exists").type == pa.bool_()


def test_every_table_has_a_schema():
    for table in schema.TABLES:
        assert "scene_id" in schema.table_schema(table).names


def test_unknown_table_raises():
    with pytest.raises(KeyError):
        schema.table_schema("lights")


def test_group_by_table_fills_every_table():
    grouped = schema.group_by_table([_mesh("a"), _mesh("b")])
    assert set(grouped) == set(schema.TABLES)
    assert len(grouped["meshes"]) == 2
    assert grouped["skins"] == []
    with pytest.raises(TypeError):
        schema.group_by_table([{"not": "a record"}])


@pytest.mark.parametrize("fmt", constants.FORMATS)
def test_table_round_trip(tmp_path, fmt):
    path = str(tmp_path / ("meshes" + constants.EXTENSION_BY_FORMAT[fmt]))
    rows = schema.group_by_table([_mesh("a")])["meshes"]
    writers.write_table(path, "meshes", rows, fmt)
    assert writers.read_rows(path) == rows
    assert not [name for name in os.listdir(str(tmp_path)) if ".tmp" in name]


def test_parquet_file_uses_schema(tmp_path):
    path = str(tmp_path / "meshes.parquet")
    writers.write_table(path, "meshes", [], constants.FORMAT_PARQUET)
    assert pq.read_schema(path).equals(schema.table_schema("meshes"))


def test_unknown_format_raises(tmp_path):
    with pytest.raises(ValueError):
        writers.write_table(str(tmp_path / "x.csv"), "meshes", [], "csv")


def test_read_rows_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        writers.read_rows(str(tmp_path / "missing.parquet"))


def test_write_shard_counts_rows(tmp_path):
    counts = writers.write_shard(str(tmp_path), "scene_a", [_mesh("scene_a"), _dependency("scene_a", False)])
    assert counts["meshes"] == 1
    assert counts["dependencies"] == 1
    assert counts["skins"] == 0
    assert os.path.isfile(os.path.join(writers.shard_dir(str(tmp_path), "scene_a"), "meshes.parquet"))


@pytest.mark.parametrize("fmt", constants.FORMATS)
def test_consolidate_merges_selected_scenes(tmp_path, fmt):
    output_dir = str(tmp_path)
    writers.write_shard(output_dir, "a", [_mesh("a", 10)], fmt)
    writers.write_shard(output_dir, "b", [_mesh("b", 20)], fmt)
    writers.write_shard(output_dir, "stale", [_mesh("stale", 99)], fmt)

    written = writers.consolidate(output_dir, scene_ids=["a", "b"], fmt=fmt)
    assert set(written) == set(schema.TABLES)

    meshes = writers.read_table(output_dir, "meshes")
    assert sorted(meshes["triangle_count"]) == [10, 20]
    assert list(meshes.iloc[0]["uv_sets"]) == ["map1", "lightmap"]


def test_read_table_without_data_returns_typed_empty_frame(tmp_path):
    frame = writers.read_table(str(tmp_path), "skins")
    assert frame.empty
    assert "max_influences_per_vertex" in frame.columns


def test_json_document_helpers(tmp_path):
    path = str(tmp_path / "nested" / "doc.json")
    assert writers.read_json(path, default={}) == {}
    writers.write_json(path, {"b": 1, "a": [1, 2]})
    assert writers.read_json(path) == {"a": [1, 2], "b": 1}


def test_shards_fall_back_to_json_without_pyarrow(tmp_path, monkeypatch):
    # Stock mayapy has no pyarrow; the worker must still write its shard,
    # and the orchestrator must still build Parquet from it.
    output = str(tmp_path)
    monkeypatch.setattr(writers, "parquet_available", lambda: False)
    writers.write_shard(output, "a", [_mesh("a"), _dependency("a", False)])
    folder = writers.shard_dir(output, "a")
    assert sorted(os.listdir(folder))[0].endswith(".jsonl")
    assert not [name for name in os.listdir(folder) if name.endswith(".parquet")]

    monkeypatch.undo()
    written = writers.consolidate(output, ["a"])
    assert written["meshes"].endswith(".parquet")
    meshes = pq.read_table(written["meshes"]).to_pylist()
    assert meshes[0]["uv_sets"] == ["map1", "lightmap"]
    assert pq.read_table(written["dependencies"]).to_pylist()[0]["exists"] is False


def test_rewriting_a_shard_in_another_format_removes_the_old_files(tmp_path, monkeypatch):
    output = str(tmp_path)
    writers.write_shard(output, "a", [_mesh("a")])
    monkeypatch.setattr(writers, "parquet_available", lambda: False)
    writers.write_shard(output, "a", [_mesh("a", triangles=99)])
    names = os.listdir(writers.shard_dir(output, "a"))
    assert not [name for name in names if name.endswith(".parquet")]
    assert writers.collect_rows(output, "meshes", ["a"])[0]["triangle_count"] == 99
