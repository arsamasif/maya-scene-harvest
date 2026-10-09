"""Batch extraction of Maya and USD scene data into analysis-ready datasets.

scene_harvest walks a folder of scenes, runs a set of registered collectors
on each one inside a DCC worker process, and writes the results as Parquet
or JSON tables. A content-hash manifest makes reruns incremental and lets an
interrupted run resume where it stopped.

"""

__version__ = "0.1.0"
