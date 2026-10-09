"""Maya collectors: hierarchy, meshes, materials, dependencies and skinning.

Geometry and DAG traversal use the Python API 2.0 (maya.api.OpenMaya),
which is much faster than cmds for per-shape work. cmds is used where it is
the documented way to get an answer, such as reference queries and
polyInfo topology checks.

The Maya imports are guarded so the classes still register outside Maya.
That lets the planner compute the collector signature for Maya scenes from
plain Python; the collectors only run inside mayapy.

"""

import collections

from scene_harvest import constants
from scene_harvest import paths
from scene_harvest import registry
from scene_harvest import schema
from scene_harvest import topology

try:
    from maya import cmds
    from maya.api import OpenMaya as om
except ImportError:
    cmds = None
    om = None

WEIGHT_TOLERANCE = 1e-6
DEFAULT_TOPOLOGY_FACE_LIMIT = 500000
REFERENCE_NODES_TO_SKIP = ("sharedReferenceNode", "_UNKNOWN_REF_NODE_")

# (node type, attribute, dependency kind) for nodes that point at one file.
FILE_ATTRIBUTES = (
    ("file", "fileTextureName", "texture"),
    ("aiImage", "filename", "texture"),
    ("AlembicNode", "abc_File", "cache"),
    ("gpuCache", "cacheFileName", "cache"),
    ("aiStandIn", "dso", "cache"),
    ("aiVolume", "filename", "cache"),
    ("imagePlane", "imageName", "image"),
    ("audio", "filename", "audio"),
)


@registry.register
class MayaHierarchyCollector(registry.Collector):
    """One NodeRecord per DAG path, instances included."""

    name = "hierarchy"
    host = constants.HOST_MAYA
    version = 1

    def collect(self, context):
        for dag_path in _iter_dag():
            full_path = dag_path.fullPathName()
            node = om.MFnDagNode(dag_path)
            yield schema.NodeRecord(
                scene_id=context.scene_id,
                path=full_path,
                name=node.name(),
                node_type=node.typeName,
                parent_path=full_path.rsplit("|", 1)[0],
                depth=full_path.count("|"),
                child_count=dag_path.childCount(),
                visible=dag_path.isVisible(),
            )


@registry.register
class MayaMeshCollector(registry.Collector):
    """Topology, UV sets, world bounds and world matrix of every mesh shape.

    Options:
        topology_checks (bool): Run polyInfo for non-manifold edges and
            lamina faces. On by default.
        topology_face_limit (int): Skip the checks above this face count.

    """

    name = "meshes"
    host = constants.HOST_MAYA
    version = 1

    def collect(self, context):
        check_topology = context.options.get("topology_checks", True)
        face_limit = context.options.get("topology_face_limit", DEFAULT_TOPOLOGY_FACE_LIMIT)
        for dag_path in _iter_meshes():
            mesh = om.MFnMesh(dag_path)
            triangle_counts, _ = mesh.getTriangles()
            matrix = dag_path.inclusiveMatrix()
            bbox = om.MFnDagNode(dag_path).boundingBox
            bbox.transformUsing(matrix)
            shape_path = dag_path.fullPathName()
            non_manifold = lamina = schema.NOT_COMPUTED
            if check_topology and mesh.numPolygons <= face_limit:
                non_manifold = _poly_info_count(shape_path, "nonManifoldEdges")
                lamina = _poly_info_count(shape_path, "laminaFaces")
            yield schema.MeshRecord(
                scene_id=context.scene_id,
                shape_path=shape_path,
                vertex_count=mesh.numVertices,
                face_count=mesh.numPolygons,
                edge_count=mesh.numEdges,
                triangle_count=sum(triangle_counts),
                uv_sets=list(mesh.getUVSetNames()),
                bbox_min=[bbox.min.x, bbox.min.y, bbox.min.z],
                bbox_max=[bbox.max.x, bbox.max.y, bbox.max.z],
                world_matrix=[matrix.getElement(row, column) for row in range(4) for column in range(4)],
                non_manifold_edges=non_manifold,
                lamina_faces=lamina,
            )


@registry.register
class MayaMaterialCollector(registry.Collector):
    """Shading group and surface shader per mesh instance, with face counts."""

    name = "materials"
    host = constants.HOST_MAYA
    version = 1

    def collect(self, context):
        for dag_path in _iter_meshes():
            mesh = om.MFnMesh(dag_path)
            shading_engines, face_indices = mesh.getConnectedShaders(dag_path.instanceNumber())
            faces_per_engine = collections.Counter(index for index in face_indices if index >= 0)
            for engine_index, face_count in sorted(faces_per_engine.items()):
                engine = om.MFnDependencyNode(shading_engines[engine_index])
                yield schema.MaterialRecord(
                    scene_id=context.scene_id,
                    shape_path=dag_path.fullPathName(),
                    material=_surface_shader(engine),
                    shading_group=engine.name(),
                    face_count=face_count,
                )


@registry.register
class MayaDependencyCollector(registry.Collector):
    """References, file textures, caches and other file-backed nodes."""

    name = "dependencies"
    host = constants.HOST_MAYA
    version = 1

    def collect(self, context):
        workspace = cmds.workspace(query=True, rootDirectory=True) or ""
        for record in _reference_dependencies(context.scene_id):
            yield record
        known_types = set(cmds.allNodeTypes())
        for node_type, attribute, kind in FILE_ATTRIBUTES:
            if node_type not in known_types:
                continue
            for node in cmds.ls(type=node_type, long=True) or []:
                raw = cmds.getAttr("{0}.{1}".format(node, attribute)) or ""
                if not raw:
                    continue
                checked = _file_node_pattern(node, raw) if node_type == "file" else raw
                yield _dependency(context.scene_id, kind, node, attribute, raw, paths.resolve(checked, workspace))
        for record in _cache_file_dependencies(context.scene_id, workspace):
            yield record


@registry.register
class MayaSkinningCollector(registry.Collector):
    """Joint hierarchies and per-skinCluster influence statistics."""

    name = "skinning"
    host = constants.HOST_MAYA
    version = 1

    def collect(self, context):
        for root in _joint_roots():
            descendants = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
            root_depth = root.count("|")
            yield schema.SkeletonRecord(
                scene_id=context.scene_id,
                root=root,
                joint_count=len(descendants) + 1,
                max_depth=max([joint.count("|") - root_depth for joint in descendants] or [0]) + 1,
            )
        for skin in cmds.ls(type="skinCluster") or []:
            influences = cmds.skinCluster(skin, query=True, influence=True) or []
            geometry = cmds.skinCluster(skin, query=True, geometry=True) or [""]
            per_vertex = _influences_per_vertex(skin)
            max_influences, mean_influences = topology.influence_stats(per_vertex)
            yield schema.SkinRecord(
                scene_id=context.scene_id,
                skin=skin,
                geometry=geometry[0],
                influence_count=len(influences),
                vertex_count=len(per_vertex),
                max_influences_per_vertex=max_influences,
                mean_influences_per_vertex=mean_influences,
            )


def _iter_dag(filter_type=None):
    filter_type = om.MFn.kInvalid if filter_type is None else filter_type
    iterator = om.MItDag(om.MItDag.kDepthFirst, filter_type)
    while not iterator.isDone():
        dag_path = iterator.getPath()
        if dag_path.fullPathName():
            yield dag_path
        iterator.next()


def _iter_meshes():
    for dag_path in _iter_dag(om.MFn.kMesh):
        if not om.MFnDagNode(dag_path).isIntermediateObject:
            yield dag_path


def _poly_info_count(shape_path, flag):
    components = cmds.polyInfo(shape_path, **{flag: True}) or []
    try:
        # Expand ranges such as "e[3:7]" into single components.
        return len(cmds.ls(components, flatten=True) or [])
    except (RuntimeError, ValueError):
        return len(components)


def _surface_shader(engine):
    plug = engine.findPlug("surfaceShader", False)
    source = plug.source()
    if source.isNull:
        return ""
    return om.MFnDependencyNode(source.node()).name()


def _dependency(scene_id, kind, node, attribute, raw_path, resolved):
    return schema.DependencyRecord(
        scene_id=scene_id,
        kind=kind,
        node=node,
        attribute=attribute,
        path=raw_path,
        resolved_path=resolved,
        exists=paths.exists(resolved),
    )


def _reference_dependencies(scene_id):
    for reference_node in cmds.ls(type="reference") or []:
        if reference_node in REFERENCE_NODES_TO_SKIP:
            continue
        try:
            resolved = cmds.referenceQuery(reference_node, filename=True, withoutCopyNumber=True)
            raw = cmds.referenceQuery(reference_node, filename=True, unresolvedName=True, withoutCopyNumber=True)
        except RuntimeError:
            # Reference nodes that are not associated with a file.
            continue
        yield _dependency(scene_id, "reference", reference_node, "fileName", raw, paths.resolve(resolved))


def _file_node_pattern(node, raw_path):
    # Tiled and sequence textures store one concrete file in fileTextureName;
    # the pattern Maya resolves at render time is the one worth checking.
    tiled = cmds.getAttr(node + ".uvTilingMode") != 0
    sequence = cmds.getAttr(node + ".useFrameExtension")
    if tiled or sequence:
        return cmds.getAttr(node + ".computedFileTextureNamePattern") or raw_path
    return raw_path


def _cache_file_dependencies(scene_id, workspace):
    for node in cmds.ls(type="cacheFile", long=True) or []:
        folder = cmds.getAttr(node + ".cachePath") or ""
        name = cmds.getAttr(node + ".cacheName") or ""
        if not name:
            continue
        raw = "{0}/{1}.xml".format(folder.rstrip("/\\"), name) if folder else name + ".xml"
        yield _dependency(scene_id, "cache", node, "cachePath", raw, paths.resolve(raw, workspace))


def _joint_roots():
    roots = []
    for joint in cmds.ls(type="joint", long=True) or []:
        if not cmds.listRelatives(joint, parent=True, type="joint", fullPath=True):
            roots.append(joint)
    return roots


def _influences_per_vertex(skin):
    selection = om.MSelectionList()
    selection.add(skin)
    node = om.MFnDependencyNode(selection.getDependNode(0))
    weight_list = node.findPlug("weightList", False)
    counts = []
    for vertex in range(weight_list.numElements()):
        weights = weight_list.elementByPhysicalIndex(vertex).child(0)
        counts.append(
            sum(
                1 for slot in range(weights.numElements())
                if weights.elementByPhysicalIndex(slot).asDouble() > WEIGHT_TOLERANCE
            )
        )
    return counts
