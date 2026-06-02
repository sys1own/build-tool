"""
Universal Build Tool (UBT) — command-line interface.

An intuitive, incremental, AI-scheduled build runner usable on any project.

    python -m universal_build_tool build      # incremental build
    python -m universal_build_tool status     # what's stale (no build)
    python -m universal_build_tool graph       # visualize the DAG
    python -m universal_build_tool clean        # drop the build cache

Supply a project manifest with ``-f/--manifest`` (JSON with ``tasks``/``graph``
and an optional ``artifacts`` map); without one, UBT auto-detects ``ubt.json``
in the working directory or falls back to a built-in demo pipeline.
"""

import argparse
import json
import os
import sys

try:  # normal: run as a package module (python -m universal_build_tool)
    from . import engine, dag_resolver, fingerprint
except ImportError:  # fallback: run as a plain script (python universal_build_tool/main.py)
    _HERE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(_HERE))
    from universal_build_tool import engine, dag_resolver, fingerprint


DEFAULT_BUILD_GRAPH = {
    "tasks": ["fetch", "lint", "compile", "test", "package"],
    "graph": {
        "fetch": [], "lint": ["fetch"], "compile": ["lint"],
        "test": ["compile"], "package": ["compile"],
    },
}

_FILL, _EMPTY = "█", "░"


# --------------------------------------------------------------------------- #
# Visualization helpers
# --------------------------------------------------------------------------- #
def _bar(fraction, width=20):
    fraction = 0.0 if fraction != fraction else max(0.0, min(1.0, fraction))
    filled = int(round(fraction * width))
    return _FILL * filled + _EMPTY * (width - filled)


def _fmt_ms(seconds):
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.2f}s"


def _compute_levels(tasks, graph):
    """Longest-path depth per task; same-level tasks are mutually parallel."""
    taskset = set(tasks)
    memo, visiting = {}, set()

    def level(t):
        if t in memo:
            return memo[t]
        if t in visiting:
            return 0
        visiting.add(t)
        deps = [d for d in (graph.get(t, []) or []) if d in taskset and d != t]
        val = 0 if not deps else 1 + max(level(d) for d in deps)
        visiting.discard(t)
        memo[t] = val
        return val

    return {t: level(t) for t in tasks}


def _direct_deps(task, graph, taskset):
    return [d for d in (graph.get(task, []) or []) if d in taskset and d != task]


# --------------------------------------------------------------------------- #
# Live reporter for `build`
# --------------------------------------------------------------------------- #
class RichReporter:
    """Renders live telemetry (cache-hit / thread bars, per-task timers) for a
    build. Falls back to plain line output when not attached to a TTY."""

    def __init__(self, plain=False, stream=None):
        self.stream = stream or sys.stdout
        self.plain = plain or not self.stream.isatty()

    def _w(self, line=""):
        print(line, file=self.stream)

    def on_plan(self, plan):
        n, d, u = plan["n"], len(plan["dirty"]), len(plan["up_to_date"])
        hit = (u / n) if n else 0.0
        denom = max(n, plan["workers"], 1)
        self._w()
        self._w(f"  Plan    {n} task(s)    {d} to build · {u} cached")
        self._w(f"  Cache   {_bar(hit)} {hit * 100:3.0f}%    {u}/{n} up-to-date")
        self._w(f"  Pool    {_bar(plan['workers'] / denom)} {plan['workers']} "
                f"worker(s)  [{plan['workers_source']}]")
        self._w()

    def on_skip(self, task):
        if self.plain:
            self._w(f"  · cached   {task}")

    def on_all_cached(self, plan):
        self._w("  ✨ Everything up-to-date — nothing to rebuild.")

    def on_launch(self, plan):
        self._w(f"  ⚙  Building {len(plan['dirty'])} task(s)…")

    def on_complete(self, task, elapsed, index, total):
        bar = _bar(index / total, 18)
        line = f"  [{bar}] {index}/{total}  {task}  {_fmt_ms(elapsed)}"
        if self.plain:
            self._w(line)
        else:
            self.stream.write("\r\x1b[K" + line)
            self.stream.flush()
            if index == total:
                self.stream.write("\n")
                self.stream.flush()

    def on_fail(self, task, err):
        if not self.plain:
            self.stream.write("\r\x1b[K")
        self._w(f"  ✗ FAILED  {task}: {err}")

    def on_finish(self, plan, results):
        built, failed = len(results["built"]), len(results["failed"])
        skipped, elapsed = len(results["skipped"]), results["elapsed"]
        badge = "❌ BUILD FAILED" if failed else "✅ BUILD OK"
        self._w()
        self._w(f"  {badge}    built {built} · cached {skipped} · failed {failed}"
                f"    {_fmt_ms(elapsed)}")


# --------------------------------------------------------------------------- #
# Manifest loading
# --------------------------------------------------------------------------- #
def _looks_like_graph(data):
    return (isinstance(data, dict)
            and isinstance(data.get("tasks"), list)
            and data["tasks"]
            and all(isinstance(t, str) for t in data["tasks"])
            and isinstance(data.get("graph"), dict))


def _coerce_graph(data):
    out = {"tasks": list(data["tasks"]), "graph": dict(data["graph"])}
    if isinstance(data.get("artifacts"), dict):
        out["artifacts"] = dict(data["artifacts"])
    return out


def _load_graph(manifest_path):
    """Return ``(graph_dict, base_dir, source_label)``."""
    if manifest_path:
        if not os.path.exists(manifest_path):
            raise SystemExit(f"ubt: manifest not found: {manifest_path}")
        base_dir = os.path.dirname(os.path.abspath(manifest_path)) or os.getcwd()
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            raise SystemExit(f"ubt: cannot parse manifest {manifest_path}: {exc}")
        if _looks_like_graph(data):
            return _coerce_graph(data), base_dir, manifest_path
        raise SystemExit(f"ubt: manifest {manifest_path} has no 'tasks'/'graph' build DAG.")

    auto = os.path.join(os.getcwd(), "ubt.json")
    if os.path.exists(auto):
        try:
            with open(auto, "r", encoding="utf-8") as f:
                data = json.load(f)
            if _looks_like_graph(data):
                return _coerce_graph(data), os.getcwd(), auto
        except Exception:
            pass
    return dict(DEFAULT_BUILD_GRAPH), os.getcwd(), "built-in demo pipeline"


def _cache_path_for(args, base_dir):
    return args.cache_file or os.path.join(base_dir, fingerprint.DEFAULT_CACHE_FILENAME)


# --------------------------------------------------------------------------- #
# Verbs
# --------------------------------------------------------------------------- #
def cmd_build(args):
    graph, base_dir, source = _load_graph(args.manifest)
    cache_path = _cache_path_for(args, base_dir)
    print("══════════════════════════════════════════════════════════════════")
    print("  UNIVERSAL BUILD TOOL — incremental, AI-scheduled build")
    print(f"  project: {base_dir}")
    print(f"  manifest: {source}")
    print("══════════════════════════════════════════════════════════════════")
    reporter = RichReporter(plain=args.plain)
    ok = engine.execute_build_manifest(
        graph, lr=args.learning_rate, cache_path=cache_path,
        max_workers=args.threads, base_dir=base_dir, reporter=reporter,
    )
    return 0 if ok else 1


def cmd_status(args):
    graph, base_dir, source = _load_graph(args.manifest)
    cache_path = _cache_path_for(args, base_dir)
    plan = engine.plan_build(graph, cache_path=cache_path, max_workers=args.threads, base_dir=base_dir)
    n, d, u = plan["n"], len(plan["dirty"]), len(plan["up_to_date"])
    hit = (u / n) if n else 0.0
    cache_exists = os.path.exists(cache_path)

    print(f"  UBT status · {base_dir}")
    print(f"  manifest: {source}")
    print(f"  cache:    {cache_path}" + ("" if cache_exists else "  (none yet)"))
    print()
    print(f"  Freshness  {_bar(hit)} {hit * 100:3.0f}%   {u}/{n} up-to-date")
    print()
    if plan["dirty"]:
        print(f"  Needs rebuild ({d}):")
        for t in plan["dirty"]:
            print(f"    ● {t}")
    if plan["up_to_date"]:
        print(f"  Up-to-date ({u}):")
        for t in plan["up_to_date"]:
            print(f"    ✓ {t}")
    if not plan["tasks"]:
        print("  (no tasks in manifest)")
    return 0


def cmd_graph(args):
    graph_dict, base_dir, source = _load_graph(args.manifest)
    tasks, graph = graph_dict["tasks"], graph_dict["graph"]
    taskset = set(tasks)
    n, edges, crit, parallelism = dag_resolver.analyze_graph(tasks, graph)
    levels = _compute_levels(tasks, graph)

    print(f"  Dependency graph · {source}")
    print(f"  {n} tasks · {edges} edges · critical path {crit} · parallelism {parallelism:.2f}")
    print()
    width = max((len(t) for t in tasks), default=4)
    for lvl in sorted(set(levels.values())):
        band = [t for t in tasks if levels[t] == lvl]
        for i, task in enumerate(band):
            deps = _direct_deps(task, graph, taskset)
            tag = f"L{lvl}" if i == 0 else "  "
            arrow = ("← " + ", ".join(deps)) if deps else "(root)"
            print(f"  {tag:<3} {task:<{width}}  {arrow}")
    print()
    pmax = max(parallelism, 1.0)
    print(f"  Parallelism  {_bar(min(parallelism / max(n, 1), 1.0))} {parallelism:.2f}×  "
          f"(wider = more tasks runnable at once)")
    return 0


def cmd_clean(args):
    _, base_dir, _ = _load_graph(args.manifest)
    cache_path = _cache_path_for(args, base_dir)
    removed = []
    for path in (cache_path, cache_path + ".tmp"):
        if os.path.exists(path):
            try:
                size = os.path.getsize(path)
                os.remove(path)
                removed.append((path, size))
            except OSError as exc:
                print(f"  ✗ could not remove {path}: {exc}")
    if removed:
        for path, size in removed:
            print(f"  🧹 removed {path} ({size} bytes)")
    else:
        print(f"  ✨ nothing to clean ({cache_path} does not exist)")
    return 0


# --------------------------------------------------------------------------- #
# Argument parsing / entry point
# --------------------------------------------------------------------------- #
def build_parser():
    parser = argparse.ArgumentParser(
        prog="ubt",
        description="Universal Build Tool — incremental, AI-scheduled builds.",
    )
    parser.add_argument("--version", action="version", version="UBT 2.0")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-f", "--manifest", metavar="PATH",
                        help="Build manifest JSON (tasks/graph/artifacts). "
                             "Defaults to ./ubt.json or a built-in demo pipeline.")
    common.add_argument("--cache-file", metavar="PATH",
                        help="Override the incremental cache path "
                             "(default: <project>/.ubt_cache.json).")
    common.add_argument("--plain", action="store_true",
                        help="Disable live progress animation (plain log output).")

    sub = parser.add_subparsers(dest="command")

    p_build = sub.add_parser("build", parents=[common], help="Run an incremental build.")
    p_build.add_argument("--threads", type=int, default=None, metavar="N",
                         help="Force worker pool size (default: ai_scheduler).")
    p_build.add_argument("--learning-rate", type=float, default=0.1,
                         help="Scheduler learning rate (advanced).")
    p_build.set_defaults(func=cmd_build)

    p_status = sub.add_parser("status", parents=[common],
                              help="Show up-to-date vs out-of-date tasks (no build).")
    p_status.add_argument("--threads", type=int, default=None, metavar="N",
                          help="Worker pool size to report (default: ai_scheduler).")
    p_status.set_defaults(func=cmd_status)

    p_graph = sub.add_parser("graph", parents=[common], help="Visualize the dependency graph.")
    p_graph.set_defaults(func=cmd_graph)

    p_clean = sub.add_parser("clean", parents=[common], help="Delete the incremental build cache.")
    p_clean.set_defaults(func=cmd_clean)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    if getattr(args, "threads", None) is not None and args.threads <= 0:
        parser.error("--threads must be a positive integer")

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
