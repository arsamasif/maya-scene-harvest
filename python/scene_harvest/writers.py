"""Parquet and JSON-lines writers for harvested records.

Each scene is written to its own shard folder, one file per table, so
workers never contend for the same file and a crash can only lose the scene
in flight. `consolidate` merges the shards of finished scenes into one file
per table for analysis, and `read_table` loads a table into pandas.

All writes go through a temporary file and `os.replace`, so a reader never
sees a half-written file.

pyarrow is imported only where Parquet is read or written. Stock `mayapy`
has no pyarrow, so `write_shard` falls back to JSON lines there, and the
orchestrator, which does have pyarrow, turns the shards into Parquet in
`consolidate`.

"""

import json
import os

from scene_harvest import constants
from scene_harvest import schema


def write_json(path, data):
    """Atomically write a JSON document.

    Args:
        path (str): Destination file.
        data (object): JSON serializable data.

    """
    _ensure_parent(path)
    temp_path = "{0}.tmp{1}".format(path, os.getpid())
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
    os.replace(temp_path, path)


def read_json(path, default=None):
    """Read a JSON document, returning a default if it does not exist.

    Args:
        path (str): File to read.
        default (object): Returned when the file is missing.

    Returns:
        object: Parsed data or `default`.

    """
    if not os.path.isfile(path):
        return default
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def to_arrow(table, rows):
    """Convert row dicts to an Arrow table with the table's schema.

    Args:
        table (str): Table name.
        rows (list): Row dicts, e.g. from schema.group_by_table.

    Returns:
        pyarrow.Table: Typed table, possibly empty.

    """
    import pyarrow as pa

    return pa.Table.from_pylist(rows, schema=schema.table_schema(table))


def parquet_available():
    """Tell whether pyarrow can be imported in this interpreter.

    Returns:
        bool: True when Parquet can be written.

    """
    try:
        import pyarrow.parquet  # noqa: F401
    except ImportError:
        return False
    return True


def write_table(path, table, rows, fmt=constants.FORMAT_PARQUET):
    """Write the rows of one table to a single file.

    Args:
        path (str): Destination file.
        table (str): Table name, used for the schema.
        rows (list): Row dicts.
        fmt (str): "parquet" or "json".

    Raises:
        ValueError: If the format is unknown.

    """
    if fmt not in constants.FORMATS:
        raise ValueError("Unknown format {0!r}, expected one of {1}".format(fmt, constants.FORMATS))
    _ensure_parent(path)
    temp_path = "{0}.tmp{1}".format(path, os.getpid())
    if fmt == constants.FORMAT_PARQUET:
        import pyarrow.parquet as pq

        pq.write_table(to_arrow(table, rows), temp_path)
    else:
        with open(temp_path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True))
                handle.write("\n")
    os.replace(temp_path, path)


def read_rows(path):
    """Read a Parquet or JSON-lines table file back into row dicts.

    Args:
        path (str): File written by `write_table`.

    Returns:
        list: Row dicts.

    Raises:
        FileNotFoundError: If the file does not exist.

    """
    if not os.path.isfile(path):
        raise FileNotFoundError("Missing table file: {0}".format(path))
    if path.endswith(constants.EXTENSION_BY_FORMAT[constants.FORMAT_PARQUET]):
        import pyarrow.parquet as pq

        return pq.read_table(path).to_pylist()
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def shard_dir(output_dir, scene_id):
    """Return the shard folder of a scene.

    Args:
        output_dir (str): Harvest output folder.
        scene_id (str): Scene id.

    Returns:
        str: Folder path.

    """
    return os.path.join(output_dir, constants.SHARDS_DIR, scene_id)


def write_shard(output_dir, scene_id, records, fmt=constants.FORMAT_PARQUET):
    """Write all records of one scene, one file per table.

    Parquet shards fall back to JSON lines when pyarrow is missing, which is
    the normal case inside Maya. `consolidate` reads both.

    Args:
        output_dir (str): Harvest output folder.
        scene_id (str): Scene id.
        records (iterable): Record dataclass instances.
        fmt (str): "parquet" or "json".

    Returns:
        dict: {table name: row count}.

    """
    if fmt == constants.FORMAT_PARQUET and not parquet_available():
        fmt = constants.FORMAT_JSON
    folder = shard_dir(output_dir, scene_id)
    extension = constants.EXTENSION_BY_FORMAT[fmt]
    _remove_other_formats(folder, extension)
    counts = {}
    for table, rows in schema.group_by_table(records).items():
        write_table(os.path.join(folder, table + extension), table, rows, fmt)
        counts[table] = len(rows)
    return counts


def shard_files(output_dir, table, scene_ids=None):
    """List the shard files of a table.

    Args:
        output_dir (str): Harvest output folder.
        table (str): Table name.
        scene_ids (iterable, optional): Only these scenes.

    Returns:
        list: Sorted file paths that exist.

    """
    root = os.path.join(output_dir, constants.SHARDS_DIR)
    if not os.path.isdir(root):
        return []
    ids = sorted(scene_ids) if scene_ids is not None else sorted(os.listdir(root))
    found = []
    for current_id in ids:
        for extension in constants.EXTENSION_BY_FORMAT.values():
            path = os.path.join(root, current_id, table + extension)
            if os.path.isfile(path):
                found.append(path)
                break
    return found


def collect_rows(output_dir, table, scene_ids=None):
    """Gather the rows of a table across shards.

    Args:
        output_dir (str): Harvest output folder.
        table (str): Table name.
        scene_ids (iterable, optional): Only these scenes.

    Returns:
        list: Row dicts.

    """
    rows = []
    for path in shard_files(output_dir, table, scene_ids):
        rows.extend(read_rows(path))
    return rows


def consolidate(output_dir, scene_ids=None, fmt=constants.FORMAT_PARQUET):
    """Merge shards into one file per table under `dataset/`.

    Args:
        output_dir (str): Harvest output folder.
        scene_ids (iterable, optional): Only these scenes, usually the ones
            the manifest marks done.
        fmt (str): "parquet" or "json".

    Returns:
        dict: {table name: written file path}.

    """
    scene_ids = list(scene_ids) if scene_ids is not None else None
    written = {}
    for table in schema.TABLES:
        rows = collect_rows(output_dir, table, scene_ids)
        path = dataset_path(output_dir, table, fmt)
        write_table(path, table, rows, fmt)
        written[table] = path
    return written


def dataset_path(output_dir, table, fmt=constants.FORMAT_PARQUET):
    """Return where `consolidate` writes a table.

    Args:
        output_dir (str): Harvest output folder.
        table (str): Table name.
        fmt (str): "parquet" or "json".

    Returns:
        str: File path.

    """
    return os.path.join(output_dir, constants.DATASET_DIR, table + constants.EXTENSION_BY_FORMAT[fmt])


def read_table(output_dir, table):
    """Load a consolidated table into a pandas DataFrame.

    Prefers Parquet, falls back to JSON lines. A missing table comes back as
    an empty frame with the right columns, so analysis code needs no special
    cases.

    Args:
        output_dir (str): Harvest output folder.
        table (str): Table name.

    Returns:
        pandas.DataFrame: The table.

    """
    for fmt in constants.FORMATS:
        path = dataset_path(output_dir, table, fmt)
        if os.path.isfile(path):
            return to_arrow(table, read_rows(path)).to_pandas()
    return to_arrow(table, []).to_pandas()


def _remove_other_formats(folder, extension):
    # A rerun may write a different format than before (e.g. once with and
    # once without pyarrow); stale files of the other format would shadow it.
    if not os.path.isdir(folder):
        return
    others = [ext for ext in constants.EXTENSION_BY_FORMAT.values() if ext != extension]
    for name in os.listdir(folder):
        if os.path.splitext(name)[1] in others:
            os.remove(os.path.join(folder, name))


def _ensure_parent(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
