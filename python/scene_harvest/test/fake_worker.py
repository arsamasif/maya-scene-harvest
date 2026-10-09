"""Stand-in for `mayapy -m scene_harvest.worker` used by the runner tests.

Runs the real worker loop with a fake host adapter and one fake collector,
so tests exercise batching, markers, the manifest and crash handling
without Maya. The scene files are plain text:

- a line `CRASH` kills the interpreter mid-batch, like a mayapy segfault;
- a line `BROKEN` makes the scene fail with an exception;
- anything else is harvested, with one face per line of text.

"""

import os
import sys

from scene_harvest import registry
from scene_harvest import schema
from scene_harvest import worker

CRASH_EXIT_CODE = 3


class FakeAdapter(object):
    """Host adapter that reads scenes as text files."""

    @staticmethod
    def initialize():
        """Nothing to start."""

    @staticmethod
    def open_scene(path):
        """Read the scene text, crashing or failing on request.

        Args:
            path (str): Scene file.

        Returns:
            list: Lines of the file.

        Raises:
            RuntimeError: If the file contains a BROKEN line.

        """
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        if "CRASH" in lines:
            sys.stdout.flush()
            os._exit(CRASH_EXIT_CODE)
        if "BROKEN" in lines:
            raise RuntimeError("Scene is broken: {0}".format(path))
        return lines

    @staticmethod
    def close_scene(handle):
        """Nothing to release."""
        del handle


class FakeMeshCollector(registry.Collector):
    """Yields one mesh whose face count is the number of text lines."""

    name = "fake_meshes"
    host = "maya"

    def collect(self, context):
        yield schema.MeshRecord(
            scene_id=context.scene_id,
            shape_path="|fake|fakeShape",
            vertex_count=len(context.handle) * 4,
            face_count=len(context.handle),
            edge_count=len(context.handle) * 4,
            triangle_count=len(context.handle) * 2,
            uv_sets=["map1"],
            bbox_min=[0.0, 0.0, 0.0],
            bbox_max=[1.0, 1.0, 1.0],
            world_matrix=[1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0],
        )


def write_scene(path, lines=("polyCube",)):
    """Write a fake text scene understood by the fake worker.

    Args:
        path (str): Scene file to write.
        lines (iterable): Text lines; see fake_worker for special lines.

    Returns:
        str: The path.

    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


def main(argv=None):
    """Harvest a batch with the fake adapter.

    Args:
        argv (list, optional): [batch path].

    Returns:
        int: 0 if every scene succeeded, 1 otherwise.

    """
    argv = sys.argv[1:] if argv is None else argv
    batch = worker.load_batch(argv[0])
    results = worker.run_batch(batch, FakeAdapter, [FakeMeshCollector()], batch.get("signature", ""))
    return 0 if all(result["status"] == "done" for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
