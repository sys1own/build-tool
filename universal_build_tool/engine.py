import os
import time
import concurrent.futures
from . import dag_resolver, ai_scheduler, fingerprint

# Repo root: .../<root>/universal_build_tool/engine.py -> <root>
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CACHE_VERSION = 1


def _resolve_artifacts(graph_dict, base_dir):
    """Return ``{task: [abs_path, ...]}`` from the manifest's optional
    ``artifacts`` map. Relative paths are anchored at ``base_dir`` (the project
    root), so the tool works on any project, not just this repo."""
    raw = graph_dict.get("artifacts", {}) if isinstance(graph_dict, dict) else {}
    resolved = {}
    if isinstance(raw, dict):
        for task, paths in raw.items():
            abs_paths = []
            for p in fingerprint.normalize_paths(paths):
                abs_paths.append(p if os.path.isabs(p) else os.path.join(base_dir, p))
            resolved[task] = abs_paths
    return resolved


def _effective_signature(task, graph, artifacts, memo, visiting):
    """Composite signature of a task: its own file fingerprints folded together
    with the *effective* signatures of its parent dependencies.

    Because a parent's signature is itself composite, any change to a file
    anywhere up the dependency chain propagates down and invalidates this task.
    Memoized for a single-pass traversal; cycles are broken with a stable
    marker rather than recursing forever.
    """
    if task in memo:
        return memo[task]
    if task in visiting:
        return "cycle:" + str(task)
    visiting.add(task)

    parts = ["name:" + str(task)]
    own = fingerprint.signature_for_paths(artifacts.get(task))
    if own is not None:
        parts.append("own:" + own)
    for dep in (graph.get(task, []) or []):
        if dep != task:
            parts.append("dep:" + _effective_signature(dep, graph, artifacts, memo, visiting))

    visiting.discard(task)
    sig = fingerprint.combine_signatures(parts)
    memo[task] = sig
    return sig


def plan_build(graph_dict, v=1.0, reward=1.0, lr=0.1,
               cache_path=None, max_workers=None, base_dir=None):
    """Compute a build plan WITHOUT executing or mutating the cache.

    Returns a dict describing structural metrics, the resolved worker count
    (manual override or ai_scheduler), and the incremental diff (which tasks are
    dirty vs up-to-date). Used by ``status``/``build``/``graph`` alike.
    """
    base_dir = base_dir or REPO_ROOT
    tasks = graph_dict.get("tasks", []) or []
    graph = graph_dict.get("graph", {}) or {}
    artifacts = _resolve_artifacts(graph_dict, base_dir)

    if cache_path is None:
        cache_path = os.path.join(base_dir, fingerprint.DEFAULT_CACHE_FILENAME)

    n, edges, crit, parallelism = dag_resolver.analyze_graph(tasks, graph)
    auto_workers = ai_scheduler.calculate_execution_bounds(
        n, edges, crit, parallelism, v, reward, lr
    )

    if max_workers and max_workers > 0:
        workers = max(1, min(1024, int(max_workers)))
        workers_source = "manual"
    else:
        workers = auto_workers
        workers_source = "ai_scheduler"

    cache = fingerprint.load_cache(cache_path)
    cached_sigs = cache.get("signatures", {})
    if not isinstance(cached_sigs, dict):
        cached_sigs = {}

    memo, visiting = {}, set()
    current_sigs = {t: _effective_signature(t, graph, artifacts, memo, visiting) for t in tasks}

    up_to_date, dirty = [], []
    for task in tasks:
        if cached_sigs.get(task) == current_sigs[task]:
            up_to_date.append(task)
        else:
            dirty.append(task)

    return {
        "tasks": tasks, "graph": graph, "artifacts": artifacts,
        "cache_path": cache_path, "base_dir": base_dir,
        "n": n, "edges": edges, "critical_path": crit, "parallelism": parallelism,
        "auto_workers": auto_workers, "workers": workers, "workers_source": workers_source,
        "current_sigs": current_sigs, "cached_sigs": cached_sigs,
        "dirty": dirty, "up_to_date": up_to_date,
    }


class _DefaultReporter:
    """Reproduces the engine's historical console output verbatim, so existing
    callers (the system runner, the harness) are unchanged."""

    def on_plan(self, plan):
        pass

    def on_skip(self, task):
        print(f"  ↳ UP-TO-DATE (skipped): '{task}'")

    def on_all_cached(self, plan):
        print(f"[UBT Engine] All {len(plan['tasks'])} task(s) up-to-date; nothing to rebuild. ✅")

    def on_launch(self, plan):
        print(f"[UBT Engine] {len(plan['dirty'])} dirty / {len(plan['up_to_date'])} cached. "
              f"Launching parallel pipeline utilizing {plan['workers']} AI-scheduled worker thread(s)...")

    def on_complete(self, task, elapsed, index, total):
        print(f"  ↳ Task Complete: '{task}'")

    def on_fail(self, task, err):
        print(f"  ↳ Task FAILED: '{task}' ({err})")

    def on_finish(self, plan, results):
        pass


def execute_build_manifest(graph_dict, v=1.0, reward=1.0, lr=0.1,
                           cache_path=None, max_workers=None, base_dir=None,
                           reporter=None):
    """Concurrently execute the task pipeline with dynamic thread scheduling and
    high-speed incremental compilation.

    Tasks whose composite file signature (own artifacts + parent dependency
    paths) matches the persisted ``.ubt_cache.json`` matrix are flagged
    UP-TO-DATE and skipped entirely. Only dirty tasks are dispatched to the
    worker pool (size from ``max_workers`` if given, else ``ai_scheduler``);
    their signatures are written back on success.
    """
    if reporter is None:
        reporter = _DefaultReporter()

    plan = plan_build(graph_dict, v, reward, lr,
                      cache_path=cache_path, max_workers=max_workers, base_dir=base_dir)
    tasks, dirty, up_to_date = plan["tasks"], plan["dirty"], plan["up_to_date"]
    current_sigs = plan["current_sigs"]

    reporter.on_plan(plan)

    # Carry forward every known signature; refresh dirty ones only on success.
    new_sigs = dict(plan["cached_sigs"])
    for task in up_to_date:
        new_sigs[task] = current_sigs[task]
        reporter.on_skip(task)

    if not dirty:
        reporter.on_all_cached(plan)
        _persist_cache(plan["cache_path"], new_sigs)
        reporter.on_finish(plan, {"built": [], "failed": [], "skipped": up_to_date, "elapsed": 0.0})
        return True

    reporter.on_launch(plan)

    built, failed = [], []
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=plan["workers"]) as executor:
        future_map = {executor.submit(_compile_task, task): task for task in dirty}
        done = 0
        for future in concurrent.futures.as_completed(future_map):
            task = future_map[future]
            done += 1
            try:
                task_elapsed = future.result()
                new_sigs[task] = current_sigs[task]  # mark clean only on success
                built.append(task)
                reporter.on_complete(task, task_elapsed, done, len(dirty))
            except Exception as exc:
                failed.append(task)
                reporter.on_fail(task, exc)
    total_elapsed = time.perf_counter() - started

    _persist_cache(plan["cache_path"], new_sigs)
    reporter.on_finish(plan, {"built": built, "failed": failed,
                              "skipped": up_to_date, "elapsed": total_elapsed})
    return not failed


def _compile_task(task):
    """Unit of build work for a single task. Placeholder for real compilation;
    kept as the prior lightweight mock so timing/scheduling behavior is stable.
    Returns the task's wall-clock duration for telemetry."""
    start = time.perf_counter()
    time.sleep(0.01)
    return time.perf_counter() - start


def _persist_cache(cache_path, signatures):
    fingerprint.save_cache(cache_path, {
        "version": CACHE_VERSION,
        "updated": time.time(),
        "signatures": signatures,
    })
