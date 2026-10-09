"""Worker entry point: harvest every scene of one batch file.

Runs inside the DCC interpreter, e.g.
`mayapy -m scene_harvest.worker _jobs/batches/maya_0001.json`, or plain
Python for USD batches. Each scene is opened, every collector of the host
runs on it, and the records are written to the scene's shard folder,
followed by a `_result.json` marker. The marker is written last, so a
scene only counts as done once all of its tables are on disk.

A failing scene is recorded as failed and the worker moves on. Only a hard
crash of the interpreter stops the batch, and the runner handles that.

"""

import argparse
import os
import sys
import time
import traceback

from scene_harvest import constants
from scene_harvest import hosts
from scene_harvest import manifest
from scene_harvest import registry
from scene_harvest import schema
from scene_harvest import writers


def load_batch(path):
    """Read and validate a batch file.

    Args:
        path (str): Batch JSON written by the planner.

    Returns:
        dict: Batch description.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If required keys are missing.

    """
    batch = writers.read_json(path)
    if batch is None:
        raise FileNotFoundError("Missing batch file: {0}".format(path))
    missing = [key for key in ("batch_id", "host", "output_dir", "scenes") if key not in batch]
    if missing:
        raise ValueError("Batch {0} is missing keys: {1}".format(path, ", ".join(missing)))
    return batch


def harvest_scene(scene, batch, adapter, collectors, signature):
    """Harvest one scene and write its shard and result marker.

    Args:
        scene (dict): Scene fingerprint from the batch.
        batch (dict): The batch the scene belongs to.
        adapter (module): Host adapter.
        collectors (list): Collector instances to run.
        signature (str): Collector signature recorded in the marker.

    Returns:
        dict: The result marker that was written.

    """
    output_dir = batch["output_dir"]
    marker_path = manifest.result_path(output_dir, scene["scene_id"])
    if os.path.isfile(marker_path):
        os.remove(marker_path)

    started = time.time()
    result = dict(scene)
    result.update({"signature": signature, "batch": batch["batch_id"], "error": "", "rows": {}})
    handle = None
    try:
        handle = adapter.open_scene(scene["path"])
        context = registry.SceneContext(
            scene["scene_id"], scene["path"], batch["host"], handle, batch.get("options")
        )
        records = []
        for collector in collectors:
            records.extend(collector.collect(context))
        records.append(
            schema.SceneRecord(
                scene_id=scene["scene_id"],
                scene_path=scene["path"],
                host=batch["host"],
                content_hash=scene["content_hash"],
                file_size=scene["size"],
                duration_s=round(time.time() - started, 3),
                collected_at=manifest.now(),
                collectors=[collector.name for collector in collectors],
            )
        )
        fmt = batch.get("format", constants.FORMAT_PARQUET)
        result["rows"] = writers.write_shard(output_dir, scene["scene_id"], records, fmt)
        result["status"] = constants.STATUS_DONE
    except Exception as error:  # noqa: BLE001 - one bad scene must not stop the batch
        result["status"] = constants.STATUS_FAILED
        result["error"] = "{0}: {1}".format(type(error).__name__, error)
        traceback.print_exc()
    finally:
        if handle is not None:
            adapter.close_scene(handle)

    result["duration_s"] = round(time.time() - started, 3)
    result["finished_at"] = manifest.now()
    writers.write_json(marker_path, result)
    return result


def run_batch(batch, adapter=None, collectors=None, signature=None):
    """Harvest all scenes of a batch.

    Args:
        batch (dict): Batch description from `load_batch`.
        adapter (module, optional): Host adapter, looked up from the batch
            host by default. Tests pass a fake one.
        collectors (list, optional): Collector instances, all registered
            collectors of the host by default.
        signature (str, optional): Collector signature, computed from the
            registry by default.

    Returns:
        list: One result marker per scene.

    """
    host = batch["host"]
    if adapter is None:
        adapter = hosts.load_adapter(host)
    if collectors is None:
        collectors = registry.load_builtin(host).for_host(host)
    if signature is None:
        signature = registry.REGISTRY.signature(host)
    if batch.get("signature") and batch["signature"] != signature:
        print(
            "Warning: collectors changed since planning ({0} -> {1})".format(batch["signature"], signature),
            file=sys.stderr,
        )

    adapter.initialize()
    results = []
    for index, scene in enumerate(batch["scenes"], start=1):
        print(f"[{batch['batch_id']}] {index}/{len(batch['scenes'])} {scene['path']}", flush=True)
        result = harvest_scene(scene, batch, adapter, collectors, signature)
        print(f"[{batch['batch_id']}]   -> {result['status']} in {result['duration_s']}s", flush=True)
        results.append(result)
    return results


def main(argv=None):
    """Command line entry point of the worker.

    Args:
        argv (list, optional): Arguments, sys.argv[1:] by default.

    Returns:
        int: 0 if every scene succeeded, 1 if any failed.

    """
    parser = argparse.ArgumentParser(prog="scene_harvest.worker", description=__doc__.splitlines()[0])
    parser.add_argument("batch", help="Batch JSON file written by the planner.")
    args = parser.parse_args(argv)
    results = run_batch(load_batch(args.batch))
    failed = [result for result in results if result["status"] != constants.STATUS_DONE]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
