"""USD collectors: hierarchy, meshes, materials, dependencies and skinning.

The USD counterparts of the Maya collectors. They read a Usd.Stage handed
over by the USD host adapter and produce the same record types, so USD and
Maya scenes land in the same tables. Because pxr runs without a DCC, these
collectors let the whole pipeline run end to end in CI.

Column mapping where USD and Maya differ: `node_type` is the prim type
name, and `shading_group` holds the GeomSubset path for per-face bindings
(empty for a whole-prim binding).

"""

from pxr import Sdf
from pxr import Usd
from pxr import UsdGeom
from pxr import UsdShade
from pxr import UsdSkel

from scene_harvest import constants
from scene_harvest import paths
from scene_harvest import registry
from scene_harvest import schema
from scene_harvest import topology

UV_PRIMVAR_NAMES = ("st", "uv")
WEIGHT_TOLERANCE = 1e-6
ASSET_TYPES = (Sdf.ValueTypeNames.Asset, Sdf.ValueTypeNames.AssetArray)


@registry.register
class UsdHierarchyCollector(registry.Collector):
    """One NodeRecord per prim in the composed stage."""

    name = "hierarchy"
    host = constants.HOST_USD
    version = 1

    def collect(self, context):
        stage = context.handle
        for prim in stage.Traverse():
            path = prim.GetPath()
            parent = path.GetParentPath()
            yield schema.NodeRecord(
                scene_id=context.scene_id,
                path=str(path),
                name=prim.GetName(),
                node_type=str(prim.GetTypeName()),
                parent_path="" if parent == Sdf.Path.absoluteRootPath else str(parent),
                depth=path.pathElementCount,
                child_count=len(prim.GetChildren()),
                visible=_is_visible(prim),
            )


@registry.register
class UsdMeshCollector(registry.Collector):
    """Topology, UV sets, world bounds and transform of every UsdGeom.Mesh.

    Options:
        topology_checks (bool): Count non-manifold edges and lamina faces.
            On by default; turn off for very dense datasets.

    """

    name = "meshes"
    host = constants.HOST_USD
    version = 1

    def collect(self, context):
        stage = context.handle
        time = Usd.TimeCode.Default()
        check_topology = context.options.get("topology_checks", True)
        bbox_cache = UsdGeom.BBoxCache(time, [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh):
                continue
            mesh = UsdGeom.Mesh(prim)
            counts = list(mesh.GetFaceVertexCountsAttr().Get(time) or [])
            indices = list(mesh.GetFaceVertexIndicesAttr().Get(time) or [])
            points = mesh.GetPointsAttr().Get(time) or []
            stats = topology.mesh_stats(counts, indices, len(points))
            if not check_topology:
                stats["non_manifold_edges"] = schema.NOT_COMPUTED
                stats["lamina_faces"] = schema.NOT_COMPUTED
            bbox_min, bbox_max = _world_bounds(bbox_cache, prim)
            yield schema.MeshRecord(
                scene_id=context.scene_id,
                shape_path=str(prim.GetPath()),
                uv_sets=_uv_sets(prim),
                bbox_min=bbox_min,
                bbox_max=bbox_max,
                world_matrix=_flatten(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(time)),
                **stats
            )


@registry.register
class UsdMaterialCollector(registry.Collector):
    """Resolved material bindings of every gprim, including GeomSubsets."""

    name = "materials"
    host = constants.HOST_USD
    version = 1

    def collect(self, context):
        for prim in context.handle.Traverse():
            if not prim.IsA(UsdGeom.Gprim):
                continue
            binding_api = UsdShade.MaterialBindingAPI(prim)
            material, _ = binding_api.ComputeBoundMaterial()
            if material:
                yield schema.MaterialRecord(
                    scene_id=context.scene_id,
                    shape_path=str(prim.GetPath()),
                    material=str(material.GetPath()),
                    shading_group="",
                    face_count=_face_count(prim),
                )
            for subset in binding_api.GetMaterialBindSubsets():
                subset_material, _ = UsdShade.MaterialBindingAPI(subset.GetPrim()).ComputeBoundMaterial()
                if not subset_material:
                    continue
                yield schema.MaterialRecord(
                    scene_id=context.scene_id,
                    shape_path=str(prim.GetPath()),
                    material=str(subset_material.GetPath()),
                    shading_group=str(subset.GetPath()),
                    face_count=len(subset.GetIndicesAttr().Get() or []),
                )


@registry.register
class UsdDependencyCollector(registry.Collector):
    """Sublayers, references, payloads and asset-valued attributes."""

    name = "dependencies"
    host = constants.HOST_USD
    version = 1

    def collect(self, context):
        stage = context.handle
        seen = set()
        for layer in stage.GetUsedLayers():
            for record in _layer_dependencies(context.scene_id, layer):
                key = (record.kind, record.node, record.attribute, record.path)
                if key not in seen:
                    seen.add(key)
                    yield record
        for record in _attribute_dependencies(context.scene_id, stage):
            key = (record.kind, record.node, record.attribute, record.path)
            if key not in seen:
                seen.add(key)
                yield record


@registry.register
class UsdSkinningCollector(registry.Collector):
    """UsdSkel skeletons and the influence statistics of skinned prims."""

    name = "skinning"
    host = constants.HOST_USD
    version = 1

    def collect(self, context):
        for prim in context.handle.Traverse():
            if prim.IsA(UsdSkel.Skeleton):
                yield _skeleton_record(context.scene_id, prim)
            elif prim.IsA(UsdGeom.Imageable):
                record = _skin_record(context.scene_id, prim)
                if record is not None:
                    yield record


def _is_visible(prim):
    if not prim.IsA(UsdGeom.Imageable):
        return True
    return UsdGeom.Imageable(prim).ComputeVisibility() != UsdGeom.Tokens.invisible


def _uv_sets(prim):
    names = []
    for primvar in UsdGeom.PrimvarsAPI(prim).GetPrimvars():
        type_name = primvar.GetTypeName()
        name = str(primvar.GetPrimvarName())
        if type_name.role == Sdf.ValueRoleNames.TextureCoordinate or name in UV_PRIMVAR_NAMES:
            names.append(name)
    return sorted(names)


def _world_bounds(bbox_cache, prim):
    bounds = bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
    if bounds.IsEmpty():
        return [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    return [float(value) for value in bounds.GetMin()], [float(value) for value in bounds.GetMax()]


def _flatten(matrix):
    return [float(matrix[row][column]) for row in range(4) for column in range(4)]


def _face_count(prim):
    if not prim.IsA(UsdGeom.Mesh):
        return 0
    return len(UsdGeom.Mesh(prim).GetFaceVertexCountsAttr().Get() or [])


def _dependency(scene_id, kind, node, attribute, path, resolved):
    return schema.DependencyRecord(
        scene_id=scene_id,
        kind=kind,
        node=node,
        attribute=attribute,
        path=path,
        resolved_path=resolved,
        exists=paths.exists(resolved),
    )


def _layer_dependencies(scene_id, layer):
    if layer.anonymous:
        return []
    records = []
    for sublayer in layer.subLayerPaths:
        resolved = Sdf.ComputeAssetPathRelativeToLayer(layer, sublayer)
        records.append(_dependency(scene_id, "sublayer", layer.identifier, "subLayers", sublayer, resolved))

    def visit(spec_path):
        if not spec_path.IsPrimPath():
            return
        spec = layer.GetPrimAtPath(spec_path)
        for attribute, items in (("references", spec.referenceList), ("payload", spec.payloadList)):
            for item in _list_items(items):
                if not item.assetPath:
                    continue
                resolved = Sdf.ComputeAssetPathRelativeToLayer(layer, item.assetPath)
                kind = "reference" if attribute == "references" else "payload"
                records.append(
                    _dependency(scene_id, kind, str(spec_path), attribute, item.assetPath, resolved)
                )

    layer.Traverse(Sdf.Path.absoluteRootPath, visit)
    return records


def _list_items(list_editor):
    return list(list_editor.explicitItems) + list(list_editor.prependedItems) + list(list_editor.appendedItems)


def _attribute_dependencies(scene_id, stage):
    for prim in stage.Traverse():
        for attribute in prim.GetAttributes():
            if attribute.GetTypeName() not in ASSET_TYPES:
                continue
            value = attribute.Get()
            if value is None:
                continue
            asset_paths = value if attribute.GetTypeName() == Sdf.ValueTypeNames.AssetArray else [value]
            layer = _authoring_layer(attribute)
            for asset_path in asset_paths:
                if not asset_path.path:
                    continue
                resolved = asset_path.resolvedPath
                if not resolved and layer is not None:
                    resolved = Sdf.ComputeAssetPathRelativeToLayer(layer, asset_path.path)
                yield _dependency(
                    scene_id,
                    paths.classify(asset_path.path, default="asset"),
                    str(prim.GetPath()),
                    attribute.GetName(),
                    asset_path.path,
                    resolved,
                )


def _authoring_layer(attribute):
    stack = attribute.GetPropertyStack(Usd.TimeCode.Default())
    return stack[0].layer if stack else None


def _skeleton_record(scene_id, prim):
    joints = [str(joint) for joint in (UsdSkel.Skeleton(prim).GetJointsAttr().Get() or [])]
    return schema.SkeletonRecord(
        scene_id=scene_id,
        root=str(prim.GetPath()),
        joint_count=len(joints),
        max_depth=max([joint.count("/") + 1 for joint in joints] or [0]),
    )


def _skin_record(scene_id, prim):
    binding = UsdSkel.BindingAPI(prim)
    indices_primvar = binding.GetJointIndicesPrimvar()
    weights_primvar = binding.GetJointWeightsPrimvar()
    if not (indices_primvar and indices_primvar.IsDefined() and weights_primvar.IsDefined()):
        return None
    element_size = max(weights_primvar.GetElementSize(), 1)
    weights = list(weights_primvar.Get() or [])
    indices = list(indices_primvar.Get() or [])
    if not weights or len(weights) != len(indices):
        return None
    per_vertex = topology.influences_per_vertex(weights, element_size, WEIGHT_TOLERANCE)
    max_influences, mean_influences = topology.influence_stats(per_vertex)
    used_joints = {index for index, weight in zip(indices, weights) if weight > WEIGHT_TOLERANCE}
    skeleton_targets = binding.GetSkeletonRel().GetTargets() if binding.GetSkeletonRel() else []
    return schema.SkinRecord(
        scene_id=scene_id,
        skin=str(skeleton_targets[0]) if skeleton_targets else "",
        geometry=str(prim.GetPath()),
        influence_count=len(used_joints),
        vertex_count=len(per_vertex),
        max_influences_per_vertex=max_influences,
        mean_influences_per_vertex=mean_influences,
    )
