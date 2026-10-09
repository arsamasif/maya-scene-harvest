"""Command line interface: `scene-harvest plan|run|status|report|charts`.

plan    fingerprint scenes and write batch files, nothing is executed
run     plan, then execute the batches in parallel worker processes
status  show what the manifest knows about each scene
report  print summary statistics of the harvested dataset
charts  save matplotlib charts of the dataset as PNG files

"""

import argparse
import os
import sys

from scene_harvest import constants
from scene_harvest import manifest as manifest_mod
from scene_harvest import planner
from scene_harvest import report
from scene_harvest import runner


def build_parser():
    """Build the argument parser.

    Returns:
        argparse.ArgumentParser: Parser with one sub-command per action.

    """
    parser = argparse.ArgumentParser(
        prog="scene-harvest",
        description="Extract datasets from folders of Maya and USD scenes.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    plan_parser = commands.add_parser("plan", help="Plan batches without running them.")
    _add_plan_arguments(plan_parser)

    run_parser = commands.add_parser("run", help="Plan and run all batches.")
    _add_plan_arguments(run_parser)
    run_parser.add_argument("-w", "--workers", type=int, default=constants.DEFAULT_WORKERS,
                            help="Worker processes in parallel.")
    run_parser.add_argument("--mayapy", help="mayapy executable (default: $SCENE_HARVEST_MAYAPY or mayapy).")
    run_parser.add_argument("--timeout", type=float, help="Seconds before a batch is killed.")
    run_parser.add_argument("--worker-module", default=runner.WORKER_MODULE, help=argparse.SUPPRESS)

    for name, help_text in (("status", "Show manifest status."), ("report", "Print dataset statistics.")):
        sub_parser = commands.add_parser(name, help=help_text)
        sub_parser.add_argument("-o", "--output", required=True, help="Harvest output folder.")

    charts_parser = commands.add_parser("charts", help="Save charts of the dataset as PNG files.")
    charts_parser.add_argument("-o", "--output", required=True, help="Harvest output folder.")
    charts_parser.add_argument("--folder", help="Where to save the PNGs (default: <output>/charts).")
    return parser


def _add_plan_arguments(parser):
    parser.add_argument("root", help="Folder of scenes to harvest.")
    parser.add_argument("-o", "--output", required=True, help="Harvest output folder.")
    parser.add_argument("-b", "--batch-size", type=int, default=constants.DEFAULT_BATCH_SIZE,
                        help="Maximum scenes per worker process.")
    parser.add_argument("-f", "--format", choices=constants.FORMATS, default=constants.FORMAT_PARQUET)
    parser.add_argument("--host", action="append", choices=sorted(constants.EXTENSIONS_BY_HOST),
                        help="Only harvest scenes of this host. Repeatable.")
    parser.add_argument("--force", action="store_true", help="Harvest every scene again.")
    parser.add_argument("--rehash", action="store_true", help="Hash scenes even if size and mtime match.")
    parser.add_argument("--skip-failed", action="store_true", help="Do not retry scenes that failed before.")
    parser.add_argument("--no-topology", action="store_true",
                        help="Skip non-manifold and lamina checks on meshes.")


def _plan(args):
    result = planner.plan(
        args.root,
        args.output,
        batch_size=args.batch_size,
        fmt=args.format,
        force=args.force,
        rehash=args.rehash,
        retry_failed=not args.skip_failed,
        hosts=args.host,
        options={"topology_checks": not args.no_topology},
    )
    print(f"Planned {result['scheduled']} scene(s) in {len(result['batches'])} batch(es), "
          f"{result['skipped']} unchanged scene(s) skipped.")
    return result


def cmd_plan(args):
    """Run the plan sub-command.

    Args:
        args (argparse.Namespace): Parsed arguments.

    Returns:
        int: Exit code.

    """
    _plan(args)
    return 0


def cmd_run(args):
    """Run the run sub-command: plan, then execute every batch.

    Args:
        args (argparse.Namespace): Parsed arguments.

    Returns:
        int: 0 if every scheduled scene succeeded, 1 otherwise.

    """
    _plan(args)
    totals = runner.run(
        args.output,
        workers=args.workers,
        mayapy=args.mayapy,
        worker_module=args.worker_module,
        timeout=args.timeout,
    )
    print(f"Finished: {totals[constants.STATUS_DONE]} done, {totals[constants.STATUS_FAILED]} failed.")
    print(f"Dataset:  {os.path.join(os.path.abspath(args.output), constants.DATASET_DIR)}")
    return 1 if totals[constants.STATUS_FAILED] else 0


def cmd_status(args):
    """Run the status sub-command.

    Args:
        args (argparse.Namespace): Parsed arguments.

    Returns:
        int: Exit code.

    """
    manifest = manifest_mod.Manifest.load(args.output)
    if not manifest.entries:
        print("No scenes in the manifest yet.")
        return 0
    for status, count in sorted(manifest.counts().items()):
        print(f"{status:>8}: {count}")
    failed = manifest.ids_with_status(constants.STATUS_FAILED)
    if failed:
        print("")
        print("Failed scenes:")
        for scene_id in failed:
            entry = manifest.get(scene_id)
            print(f"  {entry.get('path', scene_id)}")
            print(f"    {entry.get('error', '')}")
    return 0


def cmd_report(args):
    """Run the report sub-command.

    Args:
        args (argparse.Namespace): Parsed arguments.

    Returns:
        int: Exit code.

    """
    print(report.render(report.summarize(args.output)))
    return 0


def cmd_charts(args):
    """Run the charts sub-command.

    Args:
        args (argparse.Namespace): Parsed arguments.

    Returns:
        int: Exit code, 1 when there was nothing to chart.

    """
    from scene_harvest import charts

    paths = charts.save(args.output, args.folder)
    if not paths:
        print("Nothing to chart yet; run a harvest first.")
        return 1
    for path in paths:
        print(path)
    return 0


COMMANDS = {
    "plan": cmd_plan,
    "run": cmd_run,
    "status": cmd_status,
    "report": cmd_report,
    "charts": cmd_charts,
}


def main(argv=None):
    """Entry point of the scene-harvest command.

    Args:
        argv (list, optional): Arguments, sys.argv[1:] by default.

    Returns:
        int: Exit code.

    """
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (FileNotFoundError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
