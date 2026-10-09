"""Local parallel runner: one worker subprocess per batch.

Each batch of the current plan is run as
`<interpreter> -m scene_harvest.worker <batch.json>`. Maya batches use
mayapy, USD batches the current Python. Worker output goes to a log file per
batch.

The heavy lifting happens in the worker subprocesses, so the pool that
drives them is a `multiprocessing.pool.ThreadPool`: each thread only waits on
its subprocess. That avoids re-importing `__main__` in spawned children,
which breaks when the runner is called from a notebook or a script.

When a batch finishes, the runner folds its result markers into the
manifest and saves it, so progress survives a crash of the runner itself.
Scenes a crashed worker never reached are marked failed and get scheduled
again on the next plan.

"""

import glob
import os
import re
import subprocess
import sys
from multiprocessing import pool as mp_pool

from scene_harvest import constants
from scene_harvest import manifest as manifest_mod
from scene_harvest import planner
from scene_harvest import writers

WORKER_MODULE = "scene_harvest.worker"
PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def mayapy_patterns():
    """Return glob patterns where Maya installs usually put mayapy.

    Returns:
        list: Patterns for the current OS.

    """
    if sys.platform == "win32":
        root = os.environ.get("ProgramFiles", r"C:\Program Files")
        return [os.path.join(root, "Autodesk", "Maya*", "bin", "mayapy.exe")]
    if sys.platform == "darwin":
        return ["/Applications/Autodesk/maya*/Maya.app/Contents/bin/mayapy"]
    return ["/usr/autodesk/maya*/bin/mayapy"]


def find_mayapy(patterns=None):
    """Find installed mayapy executables, newest Maya first.

    Args:
        patterns (list, optional): Glob patterns, ``mayapy_patterns()`` by
            default.

    Returns:
        list: Paths of existing mayapy executables.

    """
    found = set()
    for pattern in patterns or mayapy_patterns():
        found.update(path for path in glob.glob(pattern) if os.path.isfile(path))

    def _version(path):
        match = re.search(r"maya(\d{4})", path, re.IGNORECASE)
        return int(match.group(1)) if match else 0

    return sorted(found, key=lambda path: (_version(path), path), reverse=True)


def interpreter_for(host, mayapy=None, python=None):
    """Pick the interpreter that runs batches of a host.

    Args:
        host (str): Host name.
        mayapy (str, optional): mayapy executable. Falls back to the
            SCENE_HARVEST_MAYAPY environment variable, then "mayapy".
        python (str, optional): Interpreter for non-Maya hosts, the current
            one by default.

    Returns:
        str: Executable path or name.

    """
    if host == constants.HOST_MAYA:
        return mayapy or os.environ.get(constants.MAYAPY_ENV_VAR) or "mayapy"
    return python or sys.executable


def build_command(batch_path, host, mayapy=None, python=None, worker_module=WORKER_MODULE):
    """Build the command line of one worker process.

    Args:
        batch_path (str): Batch JSON file.
        host (str): Host of the batch.
        mayapy (str, optional): mayapy executable.
        python (str, optional): Interpreter for non-Maya hosts.
        worker_module (str): Module run with `-m`.

    Returns:
        list: Command arguments.

    """
    return [interpreter_for(host, mayapy, python), "-m", worker_module, batch_path]


def worker_env():
    """Return the environment for worker processes.

    The package root is put first on PYTHONPATH so mayapy imports the same
    scene_harvest as the runner.

    Returns:
        dict: Environment variables.

    """
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(part for part in (PACKAGE_ROOT, existing) if part)
    return env


def run_batch_process(job):
    """Run one worker subprocess. Executed by a pool thread.

    Args:
        job (dict): batch_path, command, log_path and timeout.

    Returns:
        dict: The job plus returncode.

    """
    os.makedirs(os.path.dirname(job["log_path"]), exist_ok=True)
    with open(job["log_path"], "w", encoding="utf-8") as log:
        try:
            completed = subprocess.run(
                job["command"],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=worker_env(),
                timeout=job.get("timeout"),
                check=False,
            )
            returncode = completed.returncode
        except subprocess.TimeoutExpired:
            log.write("\nscene_harvest: worker timed out after {0}s\n".format(job["timeout"]))
            returncode = -1
        except OSError as error:
            log.write("\nscene_harvest: could not start worker: {0}\n".format(error))
            returncode = -2
    result = dict(job)
    result["returncode"] = returncode
    return result


def collect_batch(manifest, batch, returncode, log_path):
    """Fold the result markers of a finished batch into the manifest.

    Args:
        manifest (Manifest): Manifest to update.
        batch (dict): Batch description.
        returncode (int): Exit code of the worker.
        log_path (str): Worker log, quoted in error messages.

    Returns:
        dict: {"done": n, "failed": n}.

    """
    counts = {constants.STATUS_DONE: 0, constants.STATUS_FAILED: 0}
    for scene in batch["scenes"]:
        result = writers.read_json(manifest_mod.result_path(batch["output_dir"], scene["scene_id"]))
        if result is None:
            manifest.mark_failed(
                scene["scene_id"],
                "Worker exited with code {0} before finishing, see {1}".format(returncode, log_path),
            )
            counts[constants.STATUS_FAILED] += 1
            continue
        manifest.apply_result(result)
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return counts


def run(output_dir, workers=constants.DEFAULT_WORKERS, mayapy=None, python=None,
        worker_module=WORKER_MODULE, timeout=None, echo=print):
    """Execute every batch of the current plan.

    Args:
        output_dir (str): Harvest output folder with a plan.
        workers (int): Number of worker processes in parallel.
        mayapy (str, optional): mayapy executable.
        python (str, optional): Interpreter for non-Maya hosts.
        worker_module (str): Module run with `-m`, swapped for a fake in tests.
        timeout (float, optional): Seconds before a batch is killed.
        echo (callable): Progress output.

    Returns:
        dict: {"done": n, "failed": n, "batches": n}.

    Raises:
        ValueError: If workers is smaller than 1.

    """
    if workers < 1:
        raise ValueError("workers must be at least 1, got {0}".format(workers))
    output_dir = os.path.abspath(output_dir)
    plan = planner.load_plan(output_dir)
    manifest = manifest_mod.Manifest.load(output_dir)
    batches = {}
    jobs = []
    for batch_path in plan["batches"]:
        batch = writers.read_json(batch_path)
        if batch is None:
            continue
        for scene in batch["scenes"]:
            stale_marker = manifest_mod.result_path(output_dir, scene["scene_id"])
            if os.path.isfile(stale_marker):
                os.remove(stale_marker)
        batches[batch_path] = batch
        jobs.append(
            {
                "batch_path": batch_path,
                "command": build_command(batch_path, batch["host"], mayapy, python, worker_module),
                "log_path": os.path.join(
                    output_dir, constants.JOBS_DIR, constants.LOGS_DIR, batch["batch_id"] + ".log"
                ),
                "timeout": timeout,
            }
        )

    totals = {constants.STATUS_DONE: 0, constants.STATUS_FAILED: 0, "batches": len(jobs)}
    if jobs:
        with mp_pool.ThreadPool(processes=min(workers, len(jobs))) as pool:
            for finished in pool.imap_unordered(run_batch_process, jobs):
                batch = batches[finished["batch_path"]]
                counts = collect_batch(manifest, batch, finished["returncode"], finished["log_path"])
                manifest.save()
                for status, count in counts.items():
                    totals[status] = totals.get(status, 0) + count
                echo(
                    f"  {batch['batch_id']}: {counts[constants.STATUS_DONE]} done, "
                    f"{counts[constants.STATUS_FAILED]} failed (exit {finished['returncode']})"
                )

    writers.consolidate(
        output_dir,
        manifest.ids_with_status(constants.STATUS_DONE),
        plan.get("format", constants.FORMAT_PARQUET),
    )
    return totals
