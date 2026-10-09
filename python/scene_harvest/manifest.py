"""Checkpoint manifest: which scene was harvested from which bytes.

The manifest is a JSON file in the output folder with one entry per scene:
its content hash, the collector signature it was harvested with, and the
outcome. The planner compares it with the scenes on disk to decide what to
run, so an unchanged scene is skipped and a changed one is redone.

Workers never touch the manifest. They drop a small result marker into each
scene's shard folder when the scene is finished, and the runner folds those
markers into the manifest. If the runner dies, the next run calls
`reconcile` and recovers every scene that completed before the crash.

"""

import datetime
import glob
import os

from scene_harvest import constants
from scene_harvest import writers

MANIFEST_VERSION = 1

# Fields copied from a result marker. Size and mtime travel with the hash so
# the planner's fast path never pairs a new mtime with an old hash.
RESULT_KEYS = (
    "path", "host", "size", "mtime_ns", "content_hash", "signature",
    "status", "error", "finished_at", "rows",
)


class Manifest(object):
    """In-memory view of the manifest file.

    Args:
        path (str): Location of manifest.json.
        entries (dict, optional): {scene_id: entry dict}.

    """

    def __init__(self, path, entries=None):
        self.path = path
        self.entries = dict(entries or {})

    @classmethod
    def load(cls, output_dir):
        """Load the manifest of an output folder, or start an empty one.

        Args:
            output_dir (str): Harvest output folder.

        Returns:
            Manifest: Loaded manifest.

        Raises:
            ValueError: If the file was written by an unknown manifest version.

        """
        path = os.path.join(output_dir, constants.MANIFEST_FILE)
        data = writers.read_json(path, default={"version": MANIFEST_VERSION, "scenes": {}})
        if data.get("version") != MANIFEST_VERSION:
            raise ValueError("Unsupported manifest version {0} in {1}".format(data.get("version"), path))
        return cls(path, data.get("scenes", {}))

    def save(self):
        """Atomically write the manifest to disk."""
        writers.write_json(self.path, {"version": MANIFEST_VERSION, "scenes": self.entries})

    def get(self, scene_id):
        """Return the entry of a scene, or None.

        Args:
            scene_id (str): Scene id.

        Returns:
            dict: Entry, or None if the scene was never seen.

        """
        return self.entries.get(scene_id)

    def needs_work(self, fingerprint, signature, retry_failed=True):
        """Decide whether a scene has to be (re)harvested.

        Args:
            fingerprint (dict): Current scene state from planner.fingerprint.
            signature (str): Current collector signature of the scene's host.
            retry_failed (bool): Schedule scenes whose last attempt failed.

        Returns:
            bool: True if the scene must run.

        """
        entry = self.get(fingerprint["scene_id"])
        if entry is None:
            return True
        if entry.get("content_hash") != fingerprint["content_hash"]:
            return True
        if entry.get("signature") != signature:
            return True
        if entry.get("status") == constants.STATUS_DONE:
            return False
        if entry.get("status") == constants.STATUS_FAILED:
            return retry_failed
        return True

    def mark_pending(self, fingerprint, signature, batch_id):
        """Record that a scene was scheduled into a batch.

        The previous outcome is kept in `last_status`, so `status` reports
        stay honest about scenes that never finished.

        Args:
            fingerprint (dict): Scene state from planner.fingerprint.
            signature (str): Collector signature the batch will use.
            batch_id (str): Batch the scene was put in.

        """
        entry = self.entries.setdefault(fingerprint["scene_id"], {})
        entry.update(fingerprint)
        entry.update(
            {
                "signature": signature,
                "status": constants.STATUS_PENDING,
                "batch": batch_id,
                "error": "",
            }
        )

    def apply_result(self, result):
        """Fold a worker result marker into the manifest.

        Args:
            result (dict): Content of a scene's _result.json.

        Returns:
            bool: True if the entry changed.

        """
        entry = self.entries.setdefault(result["scene_id"], {"scene_id": result["scene_id"]})
        if entry.get("finished_at") == result.get("finished_at") and entry.get("status") == result["status"]:
            return False
        for key in RESULT_KEYS:
            if key in result:
                entry[key] = result[key]
        entry["attempts"] = entry.get("attempts", 0) + 1
        return True

    def mark_failed(self, scene_id, error):
        """Mark a scheduled scene as failed without a worker marker.

        Used when a worker process dies before finishing a scene.

        Args:
            scene_id (str): Scene id.
            error (str): Why it failed.

        """
        entry = self.entries.setdefault(scene_id, {"scene_id": scene_id})
        entry["status"] = constants.STATUS_FAILED
        entry["error"] = error
        entry["finished_at"] = now()
        entry["attempts"] = entry.get("attempts", 0) + 1

    def reconcile(self, output_dir):
        """Pick up every result marker present in the shard folders.

        Args:
            output_dir (str): Harvest output folder.

        Returns:
            int: Number of entries that changed.

        """
        changed = 0
        pattern = os.path.join(output_dir, constants.SHARDS_DIR, "*", constants.RESULT_FILE)
        for marker_path in sorted(glob.glob(pattern)):
            result = writers.read_json(marker_path)
            if result and self.apply_result(result):
                changed += 1
        return changed

    def ids_with_status(self, status):
        """Return the scene ids whose entry has a given status.

        Args:
            status (str): "done", "failed" or "pending".

        Returns:
            list: Sorted scene ids.

        """
        return sorted(scene_id for scene_id, entry in self.entries.items() if entry.get("status") == status)

    def counts(self):
        """Count entries per status.

        Returns:
            dict: {status: count}.

        """
        totals = {}
        for entry in self.entries.values():
            status = entry.get("status", constants.STATUS_PENDING)
            totals[status] = totals.get(status, 0) + 1
        return totals


def result_path(output_dir, scene_id):
    """Return the result marker path of a scene.

    Args:
        output_dir (str): Harvest output folder.
        scene_id (str): Scene id.

    Returns:
        str: Path of _result.json.

    """
    return os.path.join(writers.shard_dir(output_dir, scene_id), constants.RESULT_FILE)


def now():
    """Return the current UTC time as an ISO 8601 string.

    Returns:
        str: Timestamp with microseconds.

    """
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
