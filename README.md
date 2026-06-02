# Universal Build Tool (UBT)

An ultra-lean, **dependency-free**, high-performance concurrent build orchestration engine written in pure Python.

UBT requires **zero third-party packages** — every module is built exclusively on the Python standard library (`hashlib`, `concurrent.futures`, `math`, `json`, `os`, `argparse`). The underlying scheduling mathematics, topological weighting functions, and parallelism boundary calculations utilize a deterministic, fitness-optimized core that maps any directed acyclic graph (DAG) to an optimal thread pool allocation in constant time.

---

## Why UBT?

Many modern build pipelines require heavy external runtimes or multi-megabyte tools just to orchestrate simple execution graphs. UBT provides an ultra-lightweight, native Python alternative for projects that need fast, concurrent, and incremental build pipelines without bloating your project dependencies. It is ideally suited for monorepos, localized task automation, and resource-constrained CI/CD pipelines.

---

## How It Works (Under the Hood)

UBT is composed of four tightly coupled modules, each responsible for one stage of the build pipeline:

### `dag_resolver.py` — Structural Dependency Analysis

Performs a **single-pass structural analysis** of the task graph, computing four key metrics in one traversal:

* **Node count** (`n`) — total tasks in the manifest.
* **Edge count** — validated dependency links (self-loops and references to tasks outside the manifest are discarded).
* **Critical-path depth** — the longest dependency chain, computed by a **recursively memoized depth explorer** (`calculate_depth`) decorated with `@lru_cache(maxsize=None)`. A `visiting` set acts as a **loop-visiting cycle protection key**: if a node is encountered while already on the current recursion stack, the cycle is broken by returning depth `0` instead of recursing forever.
* **Parallelism ratio** — `n / critical_path`, quantifying how much of the graph can execute concurrently.

### `ai_scheduler.py` — Dynamic Thread Pool Allocation

`calculate_execution_bounds()` evaluates the graph's geometric profile to dynamically provision a hardware thread worker pool. The mathematical model operates on the following structural signals:

| Signal | Formula | Role |
| --- | --- | --- |
| **Parallelism factor** | $\frac{\text{parallelism}}{\text{parallelism} + 2.0}$ | Saturating measure of structural width |
| **Depth penalty** | $\frac{1.0}{1.0 + \log_{1p}(\text{critical path} - 1)}$ | Inverse-serial drag ratio dampening deep chains |
| **Edge density** | $\frac{\text{edges}}{\max(n, 1)}$ | Graph connectivity weight |
| **Health metric** | $\sqrt{ | \text{para factor} \times (1 - \frac{1}{1 + \log_{1p}(\text{critical path})}) |
| **Structural score** | $0.7 \times \text{health} + 0.3 \times \text{para factor}$ | Blended geometry signal |
| **Reward nudge** | $\tanh(\text{reward})$ | Bounded external feedback signal |
| **Gain** | $1.0 + \text{lr} \times (0.5 \times (\text{structural} - 0.4) + 0.4 \times \text{reward nudge})$ | Learning-rate-scaled adjustment |

The final worker count is clamped to `[1, 32]` via `max(1, min(32, ceil(parallelism * new_v)))`, where `new_v` is the velocity parameter bounded by safe operational limits. On any arithmetic exception, the scheduler falls back to `max(1, ceil(parallelism))`.

### `fingerprint.py` — SHA-256 Workspace Fingerprinting

Generates **high-speed cryptographic SHA-256 file state signatures** for incremental change detection:

* `calculate_file_hash()` streams files in **8 KiB chunks** through `hashlib.sha256`, producing a 64-character hex digest.
* `scan_project_signatures()` recursively walks a directory tree via `os.walk`, mapping every file to its SHA-256 digest.
* `combine_signatures()` deterministically folds multiple digest strings into a single composite hash. Parts are **sorted before hashing**, making the result order-independent and stable regardless of dependency-declaration order.
* `signature_for_paths()` computes a composite signature over one or more file paths. Missing or unreadable files contribute a **stable placeholder digest** (`"0" * 64`) so results remain deterministic rather than silently vanishing.
* Cache persistence uses a **write-then-rename** strategy (`save_cache` writes to `.tmp`, then `os.replace`) to avoid partial-write corruption. The default on-disk cache file is `.ubt_cache.json`.

### `engine.py` — Concurrent Build Orchestration

The central orchestrator that ties DAG resolution, scheduling, fingerprinting, and execution together:

1. **`plan_build()`** computes a build plan without executing anything — it resolves artifacts, runs `dag_resolver.analyze_graph` and `ai_scheduler.calculate_execution_bounds`, loads the persisted signature cache, computes current composite signatures via `_effective_signature()`, and partitions tasks into `dirty` (signature mismatch) and `up_to_date` (cache hit) sets.
2. **`_effective_signature()`** builds a **composite signature** for each task by folding its own file fingerprints together with the effective signatures of all its parent dependencies. This means a file change anywhere up the dependency chain propagates down and invalidates all downstream tasks. The traversal is memoized (`memo` dict) and cycle-safe (`visiting` set with a `"cycle:"` marker).
3. **`execute_build_manifest()`** dispatches all dirty tasks to a `concurrent.futures.ThreadPoolExecutor` whose pool size is governed precisely by the scheduler's calculated boundaries (or a manual `max_workers` override clamped to `[1, 1024]`). Up-to-date tasks are skipped entirely. On completion, the updated signature matrix is persisted back to the cache file.

---

## Installation & Setup

```bash
# Clone the repository
git clone https://github.com/sys1own/build-tool.git
cd build-tool

# Install in editable development mode (no external dependencies required)
pip install -e .

```

After installation, the `ubt` command is available system-wide.

---

## Programmatic Usage

Import the engine and pass an arbitrary build manifest — a dictionary with a `tasks` list and a `graph` mapping each task to its parent dependencies:

```python
from universal_build_tool import engine

manifest = {
    "tasks": ["fetch", "lint", "compile", "test", "package"],
    "graph": {
        "fetch":   [],
        "lint":    ["fetch"],
        "compile": ["lint"],
        "test":    ["compile"],
        "package": ["compile"],
    },
}

# Execute the pipeline with optimized thread allocation
success = engine.execute_build_manifest(manifest, v=1.0, reward=1.0, lr=0.1)

```

For **incremental builds** with file-level change tracking, add an `artifacts` map that binds tasks to their source file paths:

```python
manifest = {
    "tasks": ["lint", "compile", "test"],
    "graph": {
        "lint": [], "compile": ["lint"], "test": ["compile"],
    },
    "artifacts": {
        "lint":    ["src/main.py", "src/utils.py"],
        "compile": ["src/main.py"],
    },
}

# Only tasks whose file signatures have changed since the last run are rebuilt
engine.execute_build_manifest(manifest, cache_path=".ubt_cache.json")

```

You can also inspect the build plan without executing:

```python
plan = engine.plan_build(manifest)
print(f"Dirty: {plan['dirty']}, Cached: {plan['up_to_date']}, Workers: {plan['workers']}")

```

---

## CLI Usage

UBT provides a full command-line interface with four subcommands:

```bash
# Run an incremental, dynamically-scheduled build (auto-detects ./ubt.json or uses built-in demo)
python -m universal_build_tool build

# Check what's stale vs up-to-date without building
python -m universal_build_tool status

# Visualize the dependency graph structure
python -m universal_build_tool graph

# Delete the incremental build cache
python -m universal_build_tool clean

```

### Options

| Flag | Description |
| --- | --- |
| `-f, --manifest PATH` | Path to a JSON build manifest. Defaults to `./ubt.json`, then a built-in demo pipeline. |
| `--threads N` | Force the worker pool size (bypasses the dynamic scheduler). |
| `--learning-rate FLOAT` | Scheduler learning rate (default: `0.1`). |
| `--cache-file PATH` | Override the cache file location (default: `<project>/.ubt_cache.json`). |
| `--plain` | Disable live TTY progress bars; emit plain log lines. |
| `--version` | Print `UBT 2.0` and exit. |

### Build Manifest Format

A manifest is a JSON file containing:

```json
{
    "tasks": ["fetch", "lint", "compile", "test", "package"],
    "graph": {
        "fetch": [],
        "lint": ["fetch"],
        "compile": ["lint"],
        "test": ["compile"],
        "package": ["compile"]
    },
    "artifacts": {
        "compile": ["src/main.py", "src/utils.py"]
    }
}

```

* **`tasks`** — ordered list of task name strings.
* **`graph`** — maps each task to a list of its parent dependency task names.
* **`artifacts`** *(optional)* — maps tasks to file paths whose SHA-256 signatures drive incremental rebuilds. Relative paths are anchored at the manifest's directory.

---

## Testing

The repository includes a comprehensive enterprise pipeline validation suite:

```bash
# Run the full 11-task simulation (25 assertions across 11 tests)
python -m tests.test_enterprise_pipeline

```

---

## Architecture

```
universal_build_tool/
├── __init__.py          # Package marker
├── __main__.py          # python -m universal_build_tool entry point
├── main.py              # CLI argument parsing, subcommands, rich reporter
├── engine.py            # Build orchestration, ThreadPoolExecutor dispatch
├── dag_resolver.py      # Single-pass graph analysis, memoized critical path
├── ai_scheduler.py      # Dynamic, geometry-aware thread pool calculator
└── fingerprint.py       # SHA-256 file hashing, composite signatures, cache I/O

```

---

## License

See [LICENSE](https://www.google.com/search?q=LICENSE) for details.
