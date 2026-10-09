"""Path helpers: scene ids, hashing and dependency resolution.

Dependency paths in scenes often carry frame or tile tokens (`<UDIM>`,
`####`, `%04d`, `<f>`) and environment variables. This module expands them
into glob patterns so "does this texture exist" has a real answer, and
classifies a path by extension into a dependency kind.

"""

import glob
import hashlib
import os
import re

from scene_harvest import constants

TOKEN_PATTERNS = (
    re.compile(r"<udim>", re.IGNORECASE),
    re.compile(r"<uvtile>", re.IGNORECASE),
    re.compile(r"<f\d*>", re.IGNORECASE),
    re.compile(r"\$F\d*"),
    re.compile(r"%0?\d*d"),
    re.compile(r"#+"),
    re.compile(r"<u>_<v>", re.IGNORECASE),
)

KIND_BY_EXTENSION = {
    "texture": (
        ".tx", ".tex", ".exr", ".tif", ".tiff", ".png", ".jpg", ".jpeg",
        ".hdr", ".tga", ".bmp", ".psd",
    ),
    "cache": (".abc", ".vdb", ".fur", ".xml", ".mcx", ".mcc", ".ass", ".bgeo", ".sc"),
    "scene": (".ma", ".mb", ".usd", ".usda", ".usdc", ".usdz", ".fbx", ".obj"),
    "audio": (".wav", ".aif", ".aiff", ".mp3"),
}


def scene_id(path):
    """Return a stable id for a scene path.

    Args:
        path (str): Scene file path.

    Returns:
        str: 16 hex characters derived from the normalized absolute path.

    """
    normalized = os.path.normcase(os.path.abspath(path)).replace("\\", "/")
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def content_hash(path, chunk_size=constants.HASH_CHUNK_SIZE):
    """Hash the bytes of a file.

    Args:
        path (str): File to hash.
        chunk_size (int): Read size in bytes.

    Returns:
        str: SHA-256 hex digest.

    Raises:
        FileNotFoundError: If the file does not exist.

    """
    if not os.path.isfile(path):
        raise FileNotFoundError("Cannot hash missing file: {0}".format(path))
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def has_tokens(path):
    """Tell whether a path contains frame or tile tokens.

    Args:
        path (str): File path.

    Returns:
        bool: True if any known token is present.

    """
    return any(pattern.search(path) for pattern in TOKEN_PATTERNS)


def to_glob(path):
    """Replace frame and tile tokens with glob wildcards.

    Args:
        path (str): File path, possibly with tokens.

    Returns:
        str: Glob pattern. Unchanged if no token is present.

    """
    pattern = glob.escape(path)
    for token in TOKEN_PATTERNS:
        pattern = token.sub("*", pattern)
    return pattern


def resolve(path, base_dir=""):
    """Expand variables and make a dependency path absolute.

    Args:
        path (str): Path as written in the scene.
        base_dir (str): Directory relative paths are resolved against.

    Returns:
        str: Normalized absolute path, or "" for an empty input.

    """
    if not path:
        return ""
    expanded = os.path.expanduser(os.path.expandvars(path))
    if not os.path.isabs(expanded) and base_dir:
        expanded = os.path.join(base_dir, expanded)
    return os.path.normpath(expanded)


def exists(path):
    """Check that a dependency exists, honouring sequence tokens.

    Args:
        path (str): Resolved path, possibly with tokens.

    Returns:
        bool: True if the file, or at least one frame or tile, exists.

    """
    if not path:
        return False
    if has_tokens(path):
        return bool(glob.glob(to_glob(path)))
    return os.path.exists(path)


def classify(path, default="other"):
    """Guess the dependency kind of a path from its extension.

    Args:
        path (str): File path.
        default (str): Kind returned for unknown extensions.

    Returns:
        str: One of "texture", "cache", "scene", "audio" or `default`.

    """
    extension = os.path.splitext(path)[1].lower()
    for kind, extensions in KIND_BY_EXTENSION.items():
        if extension in extensions:
            return kind
    return default


def host_for_path(path):
    """Return the host that opens a scene file, from its extension.

    Args:
        path (str): Scene path.

    Returns:
        str: Host name, or "" if no host handles the extension.

    """
    extension = os.path.splitext(path)[1].lower()
    for host, extensions in constants.EXTENSIONS_BY_HOST.items():
        if extension in extensions:
            return host
    return ""
