"""Job planner: find scenes, compare them with the manifest, cut batches.

Planning is cheap and safe to repeat. It fingerprints every scene under a
root folder, asks the manifest which ones need work, and writes one batch
JSON file per worker invocation. Scenes are spread over batches by file
size so no single worker is stuck with all the heavy files.

Hashing large scenes is the slow part, so a scene whose size and mtime match
the manifest reuses the stored hash unless `rehash` is set.

"""

import math
import os
import shutil

from scene_harvest import constants
from scene_harvest import manifest as manifest_mod
from scene_harvest import paths
from scene_harvest import registry
from scene_harvest import writers

BATCHES_DIR = "batches"


def discover(root, hosts=None, exclude=()):
    """Find scene files under a folder.

    Args:
        root (str): Folder to walk.
        hosts (iterable, optional): Only keep scenes of these hosts.
        exclude (iterable): Folders to skip, e.g. the output folder.

    Returns:
        list: Sorted absolute scene paths.

    Raises:
        FileNotFoundError: If the root folder does not exist.

    """
    if not os.path.isdir(root):
        raise FileNotFoundError("Scene folder not found: {0}".format(root))
    wanted_hosts = set(hosts) if hosts else set(constants.EXTENSIONS_BY_HOST)
    skipped = {os.path.normcase(os.path.abspath(folder)) for folder in exclude}
    found = []
    for folder, sub_folders, file_names in os.walk(os.path.abspath(root)):
        sub_folders[:] = [
            name for name in sorted(sub_folders)
            if not name.startswith(".")
            and os.path.normcase(os.path.join(folder, name)) not in skipped
        ]
        for file_name in file_names:
            if paths.host_for_path(file_name) in wanted_hosts:
                found.append(os.path.join(folder, file_name))
    return sorted(found)


def fingerprint(path, previous=None, rehash=False):
    """Describe the current state of a scene file.

    Args:
        path (str): Scene path.
        previous (dict, optional): Manifest entry from the last run.
        rehash (bool): Always hash, even if size and mtime are unchanged.

    Returns:
        dict: scene_id, path, host, size, mtime_ns and content_hash.

    """
    stat = os.stat(path)
    content_hash = ""
    if previous and not rehash:
        if previous.get("size") == stat.st_size and previous.get("mtime_ns") == stat.st_mtime_ns:
            content_hash = previous.get("content_hash", "")
    return {
        "scene_id": paths.scene_id(path),
        "path": os.path.abspath(path),
        "host": paths.host_for_path(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "content_hash": content_hash or paths.content_hash(path),
    }


def split_batches(fingerprints, batch_size):
    """Spread scenes over batches, balancing total bytes.

    Uses the classic longest-processing-time heuristic: biggest scenes
    first, each into the lightest batch that still has room.

    Args:
        fingerprints (list): Scene fingerprints of a single host.
        batch_size (int): Maximum scenes per batch.

    Returns:
        list: Lists of fingerprints, none of them empty.

    Raises:
        ValueError: If batch_size is smaller than 1.

    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1, got {0}".format(batch_size))
    if not fingerprints:
        return []
    bins = [[] for _ in range(int(math.ceil(len(fingerprints) / float(batch_size))))]
    weights = [0] * len(bins)
    for item in sorted(fingerprints, key=lambda value: (-value["size"], value["path"])):
        open_bins = [index for index, current in enumerate(bins) if len(current) < batch_size]
        target = min(open_bins, key=lambda index: (weights[index], index))
        bins[target].append(item)
        weights[target] += item["size"]
    return [sorted(current, key=lambda value: value["path"]) for current in bins]


def plan(
    root,
    output_dir,
    batch_size=constants.DEFAULT_BATCH_SIZE,
    fmt=constants.FORMAT_PARQUET,
    force=False,
    rehash=False,
    retry_failed=True,
    hosts=None,
    options=None,
):
    """Plan a harvest and write its batch files.

    Args:
        root (str): Folder of scenes.
        output_dir (str): Harvest output folder.
        batch_size (int): Maximum scenes per batch.
        fmt (str): Output format for the workers, "parquet" or "json".
        force (bool): Schedule every scene, ignoring the manifest.
        rehash (bool): Hash every scene even if size and mtime match.
        retry_failed (bool): Schedule scenes whose last attempt failed.
        hosts (iterable, optional): Only plan scenes of these hosts.
        options (dict, optional): Collector options passed to workers.

    Returns:
        dict: The plan, also written to _jobs/plan.json.

    Raises:
        ValueError: If the format is unknown.

    """
    if fmt not in constants.FORMATS:
        raise ValueError("Unknown format {0!r}".format(fmt))
    output_dir = os.path.abspath(output_dir)
    manifest = manifest_mod.Manifest.load(output_dir)
    manifest.reconcile(output_dir)

    scenes_by_host = {}
    skipped = 0
    signatures = {}
    for path in discover(root, hosts=hosts, exclude=[output_dir]):
        scene = fingerprint(path, manifest.get(paths.scene_id(path)), rehash=rehash)
        host = scene["host"]
        if host not in signatures:
            signatures[host] = registry.load_builtin(host).signature(host)
        if force or manifest.needs_work(scene, signatures[host], retry_failed=retry_failed):
            scenes_by_host.setdefault(host, []).append(scene)
        else:
            skipped += 1

    batch_dir = os.path.join(output_dir, constants.JOBS_DIR, BATCHES_DIR)
    shutil.rmtree(batch_dir, ignore_errors=True)
    batch_files = []
    for host in sorted(scenes_by_host):
        for index, scenes in enumerate(split_batches(scenes_by_host[host], batch_size), start=1):
            batch_id = "{0}_{1:04d}".format(host, index)
            batch_path = os.path.join(batch_dir, batch_id + ".json")
            writers.write_json(
                batch_path,
                {
                    "batch_id": batch_id,
                    "host": host,
                    "output_dir": output_dir,
                    "format": fmt,
                    "signature": signatures[host],
                    "options": dict(options or {}),
                    "scenes": scenes,
                },
            )
            for scene in scenes:
                manifest.mark_pending(scene, signatures[host], batch_id)
            batch_files.append(batch_path)
    manifest.save()

    result = {
        "created_at": manifest_mod.now(),
        "root": os.path.abspath(root),
        "output_dir": output_dir,
        "format": fmt,
        "scheduled": sum(len(scenes) for scenes in scenes_by_host.values()),
        "skipped": skipped,
        "signatures": signatures,
        "batches": batch_files,
    }
    writers.write_json(plan_path(output_dir), result)
    return result


def plan_path(output_dir):
    """Return where the last plan of an output folder is stored.

    Args:
        output_dir (str): Harvest output folder.

    Returns:
        str: Path of plan.json.

    """
    return os.path.join(output_dir, constants.JOBS_DIR, constants.PLAN_FILE)


def load_plan(output_dir):
    """Load the last plan written to an output folder.

    Args:
        output_dir (str): Harvest output folder.

    Returns:
        dict: The plan.

    Raises:
        FileNotFoundError: If no plan was written yet.

    """
    path = plan_path(output_dir)
    data = writers.read_json(path)
    if data is None:
        raise FileNotFoundError("No plan found, run `scene-harvest plan` first: {0}".format(path))
    return data
