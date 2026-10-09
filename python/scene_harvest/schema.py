"""Record types that make up the harvested dataset.

Every collector yields instances of the dataclasses below. Each record class
names the table it belongs to, and the Arrow schema for that table is derived
from the dataclass annotations, so adding a column is a one-line change that
the Parquet and JSON writers pick up automatically.

pyarrow is only imported when a schema is built. Workers inside Maya often
have no pyarrow and only need the dataclasses.

"""

import dataclasses
import typing

NOT_COMPUTED = -1


@dataclasses.dataclass
class SceneRecord:
    """One row per successfully harvested scene."""

    TABLE: typing.ClassVar[str] = "scenes"

    scene_id: str
    scene_path: str
    host: str
    content_hash: str
    file_size: int
    duration_s: float
    collected_at: str
    collectors: typing.List[str]


@dataclasses.dataclass
class NodeRecord:
    """A DAG node (Maya) or prim (USD) and its place in the hierarchy."""

    TABLE: typing.ClassVar[str] = "nodes"

    scene_id: str
    path: str
    name: str
    node_type: str
    parent_path: str
    depth: int
    child_count: int
    visible: bool


@dataclasses.dataclass
class MeshRecord:
    """Geometry statistics for one mesh shape."""

    TABLE: typing.ClassVar[str] = "meshes"

    scene_id: str
    shape_path: str
    vertex_count: int
    face_count: int
    edge_count: int
    triangle_count: int
    uv_sets: typing.List[str]
    bbox_min: typing.List[float]
    bbox_max: typing.List[float]
    world_matrix: typing.List[float]
    non_manifold_edges: int = NOT_COMPUTED
    lamina_faces: int = NOT_COMPUTED


@dataclasses.dataclass
class MaterialRecord:
    """A material assignment on a shape, whole or per face set."""

    TABLE: typing.ClassVar[str] = "materials"

    scene_id: str
    shape_path: str
    material: str
    shading_group: str
    face_count: int


@dataclasses.dataclass
class DependencyRecord:
    """An external file the scene points at, and whether it exists on disk."""

    TABLE: typing.ClassVar[str] = "dependencies"

    scene_id: str
    kind: str
    node: str
    attribute: str
    path: str
    resolved_path: str
    exists: bool


@dataclasses.dataclass
class SkeletonRecord:
    """A joint hierarchy, keyed by its root."""

    TABLE: typing.ClassVar[str] = "skeletons"

    scene_id: str
    root: str
    joint_count: int
    max_depth: int


@dataclasses.dataclass
class SkinRecord:
    """Influence statistics for one skin binding."""

    TABLE: typing.ClassVar[str] = "skins"

    scene_id: str
    skin: str
    geometry: str
    influence_count: int
    vertex_count: int
    max_influences_per_vertex: int
    mean_influences_per_vertex: float


RECORD_TYPES = (
    SceneRecord,
    NodeRecord,
    MeshRecord,
    MaterialRecord,
    DependencyRecord,
    SkeletonRecord,
    SkinRecord,
)
RECORD_TYPE_BY_TABLE = {record_type.TABLE: record_type for record_type in RECORD_TYPES}
TABLES = tuple(RECORD_TYPE_BY_TABLE)


def arrow_schema(record_type):
    """Build the Arrow schema for a record dataclass.

    Args:
        record_type (type): One of the classes in RECORD_TYPES.

    Returns:
        pyarrow.Schema: Field per dataclass field, in declaration order.

    Raises:
        TypeError: If a field uses a type with no Arrow mapping.

    """
    import pyarrow as pa

    arrow_types = {
        str: pa.string(),
        int: pa.int64(),
        float: pa.float64(),
        bool: pa.bool_(),
        typing.List[str]: pa.list_(pa.string()),
        typing.List[float]: pa.list_(pa.float64()),
    }
    hints = typing.get_type_hints(record_type)
    fields = []
    for field in dataclasses.fields(record_type):
        arrow_type = arrow_types.get(hints[field.name])
        if arrow_type is None:
            raise TypeError(
                "No Arrow type for {0}.{1}: {2}".format(
                    record_type.__name__, field.name, hints[field.name]
                )
            )
        fields.append(pa.field(field.name, arrow_type))
    return pa.schema(fields)


def table_schema(table):
    """Return the Arrow schema of a table by name.

    Args:
        table (str): Table name, e.g. "meshes".

    Returns:
        pyarrow.Schema: The table's schema.

    Raises:
        KeyError: If the table is unknown.

    """
    if table not in RECORD_TYPE_BY_TABLE:
        raise KeyError("Unknown table: {0}".format(table))
    return arrow_schema(RECORD_TYPE_BY_TABLE[table])


def group_by_table(records):
    """Sort records into lists keyed by their table name.

    Args:
        records (iterable): Record dataclass instances.

    Returns:
        dict: {table name: [row dict, ...]} with every known table present.

    Raises:
        TypeError: If an object is not a known record type.

    """
    grouped = {table: [] for table in TABLES}
    for record in records:
        table = getattr(type(record), "TABLE", None)
        if table not in grouped:
            raise TypeError("Not a scene_harvest record: {0!r}".format(record))
        grouped[table].append(dataclasses.asdict(record))
    return grouped
