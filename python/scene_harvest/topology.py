"""Pure-Python mesh topology statistics.

Works on the face-vertex representation shared by USD and most interchange
formats: a list of vertex counts per face and a flat list of vertex indices.
Used by the USD collector, and handy for testing the numbers the Maya
collector gets from the API.

"""

import collections


def faces(face_vertex_counts, face_vertex_indices):
    """Split the flat index list into one tuple per face.

    Args:
        face_vertex_counts (list): Vertex count of each face.
        face_vertex_indices (list): Concatenated vertex indices.

    Returns:
        list: Tuples of vertex indices, one per face.

    Raises:
        ValueError: If the counts do not add up to the number of indices.

    """
    counts = list(face_vertex_counts)
    indices = list(face_vertex_indices)
    if sum(counts) != len(indices):
        raise ValueError(
            "Face counts sum to {0} but there are {1} indices".format(sum(counts), len(indices))
        )
    result = []
    offset = 0
    for count in counts:
        result.append(tuple(indices[offset:offset + count]))
        offset += count
    return result


def triangle_count(face_vertex_counts):
    """Count triangles after fan triangulation.

    Args:
        face_vertex_counts (list): Vertex count of each face.

    Returns:
        int: Number of triangles.

    """
    return sum(max(count - 2, 0) for count in face_vertex_counts)


def edge_usage(face_list):
    """Count how many faces use each undirected edge.

    Args:
        face_list (list): Face vertex tuples from `faces`.

    Returns:
        collections.Counter: {(low, high): face count}.

    """
    usage = collections.Counter()
    for face in face_list:
        for position, vertex in enumerate(face):
            following = face[(position + 1) % len(face)]
            usage[(min(vertex, following), max(vertex, following))] += 1
    return usage


def non_manifold_edge_count(face_list, usage=None):
    """Count edges shared by more than two faces.

    Args:
        face_list (list): Face vertex tuples from `faces`.
        usage (Counter, optional): Precomputed result of `edge_usage`.

    Returns:
        int: Number of non-manifold edges.

    """
    usage = usage if usage is not None else edge_usage(face_list)
    return sum(1 for count in usage.values() if count > 2)


def lamina_face_count(face_list):
    """Count faces that share all their vertices with another face.

    Args:
        face_list (list): Face vertex tuples from `faces`.

    Returns:
        int: Number of faces that belong to a lamina pair or group.

    """
    groups = collections.Counter(frozenset(face) for face in face_list)
    return sum(count for count in groups.values() if count > 1)


def mesh_stats(face_vertex_counts, face_vertex_indices, point_count):
    """Compute the topology columns of a MeshRecord.

    Args:
        face_vertex_counts (list): Vertex count of each face.
        face_vertex_indices (list): Concatenated vertex indices.
        point_count (int): Number of points of the mesh.

    Returns:
        dict: vertex_count, face_count, edge_count, triangle_count,
            non_manifold_edges and lamina_faces.

    """
    face_list = faces(face_vertex_counts, face_vertex_indices)
    usage = edge_usage(face_list)
    return {
        "vertex_count": int(point_count),
        "face_count": len(face_list),
        "edge_count": len(usage),
        "triangle_count": triangle_count(face_vertex_counts),
        "non_manifold_edges": non_manifold_edge_count(face_list, usage),
        "lamina_faces": lamina_face_count(face_list),
    }


def influences_per_vertex(weights, element_size, tolerance=1e-6):
    """Count non-zero weights per vertex from a flat, fixed-stride array.

    This is the layout of UsdSkel joint weights and of most skin exports:
    `element_size` weights per vertex, unused slots padded with zero.

    Args:
        weights (list): Flat weight values.
        element_size (int): Weights stored per vertex.
        tolerance (float): Weights at or below this count as zero.

    Returns:
        list: Influence count per vertex.

    Raises:
        ValueError: If element_size is not positive or does not divide the
            weight count.

    """
    values = list(weights)
    if element_size < 1 or len(values) % element_size:
        raise ValueError(
            "{0} weights cannot be split into groups of {1}".format(len(values), element_size)
        )
    return [
        sum(1 for weight in values[start:start + element_size] if weight > tolerance)
        for start in range(0, len(values), element_size)
    ]


def influence_stats(counts_per_vertex):
    """Summarize how many joints drive each vertex.

    Args:
        counts_per_vertex (list): Non-zero influence count per vertex.

    Returns:
        tuple: (max influences, mean influences). Zeros for an empty list.

    """
    counts = list(counts_per_vertex)
    if not counts:
        return 0, 0.0
    return max(counts), round(sum(counts) / float(len(counts)), 4)
