"""Shared constants for scene_harvest.

File layout inside an output folder, host names and the scene extensions
each host can open. Kept in one place so the planner, worker and runner
always agree on where things live.

"""

SCHEMA_VERSION = 1

HOST_MAYA = "maya"
HOST_USD = "usd"

EXTENSIONS_BY_HOST = {
    HOST_MAYA: (".ma", ".mb"),
    HOST_USD: (".usd", ".usda", ".usdc"),
}

MANIFEST_FILE = "manifest.json"
JOBS_DIR = "_jobs"
LOGS_DIR = "logs"
PLAN_FILE = "plan.json"
SHARDS_DIR = "shards"
DATASET_DIR = "dataset"
RESULT_FILE = "_result.json"

FORMAT_PARQUET = "parquet"
FORMAT_JSON = "json"
FORMATS = (FORMAT_PARQUET, FORMAT_JSON)
EXTENSION_BY_FORMAT = {FORMAT_PARQUET: ".parquet", FORMAT_JSON: ".jsonl"}

STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"

DEFAULT_BATCH_SIZE = 8
DEFAULT_WORKERS = 2
HASH_CHUNK_SIZE = 1024 * 1024

PLUGINS_ENV_VAR = "SCENE_HARVEST_PLUGINS"
MAYAPY_ENV_VAR = "SCENE_HARVEST_MAYAPY"
