"""
Enterprise Build Pipeline — Complex 11-Task Simulation Suite
=============================================================

Validates the Universal Build Tool against a realistic multi-stage enterprise
CI/CD pipeline with 11 discrete tasks arranged in a diamond-shaped DAG that
exercises parallelism sizing, serial chaining, incremental caching, cycle
defense, fingerprint integrity, and scheduler adaptability simultaneously.

Run from repo root:
    python -m tests.test_enterprise_pipeline
"""

import os
import sys
import time
import json
import tempfile
import shutil

# Ensure the repo root is importable regardless of invocation method.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from universal_build_tool import engine, dag_resolver, ai_scheduler, fingerprint

# ── Globals ──────────────────────────────────────────────────────────────────

_PASS = 0
_FAIL = 0
_TMPDIR = None
_SRC_DIR = None


def _setup():
    """Create a disposable workspace with mock source files for fingerprinting."""
    global _TMPDIR, _SRC_DIR
    _TMPDIR = tempfile.mkdtemp(prefix="ubt_enterprise_test_")
    _SRC_DIR = os.path.join(_TMPDIR, "src")
    os.makedirs(_SRC_DIR, exist_ok=True)
    for name in ("main.py", "utils.py", "config.yaml"):
        with open(os.path.join(_SRC_DIR, name), "w") as f:
            f.write(f"# mock source: {name}\n")
    return _TMPDIR


def _teardown():
    if _TMPDIR and os.path.isdir(_TMPDIR):
        shutil.rmtree(_TMPDIR, ignore_errors=True)


def _ok(label):
    global _PASS
    _PASS += 1
    print(f"  ✔ {label}")


def _fail(label, detail=""):
    global _FAIL
    _FAIL += 1
    msg = f"  ✘ {label}"
    if detail:
        msg += f"  ({detail})"
    print(msg)


def _check(condition, label, detail=""):
    if condition:
        _ok(label)
    else:
        _fail(label, detail)


# ── Enterprise Pipeline Manifest (11 tasks) ─────────────────────────────────

ENTERPRISE_PIPELINE = {
    "tasks": [
        "checkout",
        "install_deps",
        "lint",
        "type_check",
        "unit_tests",
        "integration_tests",
        "security_scan",
        "build_frontend",
        "build_backend",
        "docker_package",
        "deploy_staging",
    ],
    "graph": {
        "checkout":          [],
        "install_deps":      ["checkout"],
        "lint":              ["install_deps"],
        "type_check":        ["install_deps"],
        "unit_tests":        ["lint", "type_check"],
        "integration_tests": ["unit_tests"],
        "security_scan":     ["install_deps"],
        "build_frontend":    ["lint"],
        "build_backend":     ["lint"],
        "docker_package":    ["build_frontend", "build_backend",
                              "integration_tests", "security_scan"],
        "deploy_staging":    ["docker_package"],
    },
}


# ── Test 1: Full Pipeline Execution ─────────────────────────────────────────

def test_01_full_pipeline_execution():
    """All 11 tasks complete without failure on first run."""
    print("\n▶ TEST 1: Full 11-Task Enterprise Pipeline Execution")
    result = engine.execute_build_manifest(
        ENTERPRISE_PIPELINE, v=1.2, reward=2.0, lr=0.1,
        cache_path=os.path.join(_TMPDIR, ".cache_t1.json"),
        base_dir=_TMPDIR,
    )
    _check(result is True, "Pipeline returned success")


# ── Test 2: DAG Structural Analysis ─────────────────────────────────────────

def test_02_dag_structural_analysis():
    """Verify node count, edge count, critical-path length, and parallelism."""
    print("\n▶ TEST 2: DAG Structural Analysis (11 nodes)")
    tasks = ENTERPRISE_PIPELINE["tasks"]
    graph = ENTERPRISE_PIPELINE["graph"]
    n, edges, crit, para = dag_resolver.analyze_graph(tasks, graph)

    _check(n == 11, f"Node count == 11 (got {n})")
    _check(edges == 14, f"Edge count == 14 (got {edges})")
    _check(crit == 7, f"Critical path == 7 (got {crit})")
    _check(para > 1.0, f"Parallelism > 1.0 (got {para:.2f})")


# ── Test 3: AI Scheduler Thread Allocation ───────────────────────────────────

def test_03_scheduler_thread_allocation():
    """Scheduler yields sensible worker counts for different reward signals."""
    print("\n▶ TEST 3: AI Scheduler Dynamic Thread Allocation")
    tasks = ENTERPRISE_PIPELINE["tasks"]
    graph = ENTERPRISE_PIPELINE["graph"]
    n, edges, crit, para = dag_resolver.analyze_graph(tasks, graph)

    w_high = ai_scheduler.calculate_execution_bounds(n, edges, crit, para, 1.2, 5.0, 0.1)
    w_low  = ai_scheduler.calculate_execution_bounds(n, edges, crit, para, 1.0, -5.0, 0.1)
    _check(1 <= w_high <= 32, f"High-reward workers in [1,32] (got {w_high})")
    _check(1 <= w_low  <= 32, f"Low-reward workers in [1,32] (got {w_low})")
    _check(w_high >= w_low, f"High-reward >= low-reward ({w_high} >= {w_low})")


# ── Test 4: Incremental Cache — Second Run Skips All Tasks ──────────────────

def test_04_incremental_cache_rebuild():
    """After an identical second run, every task should be up-to-date."""
    print("\n▶ TEST 4: Incremental Cache — Full Rebuild Skip")
    cache_file = os.path.join(_TMPDIR, ".cache_t4.json")
    engine.execute_build_manifest(
        ENTERPRISE_PIPELINE, v=1.0, reward=1.0, lr=0.1,
        cache_path=cache_file, base_dir=_TMPDIR,
    )
    plan = engine.plan_build(
        ENTERPRISE_PIPELINE, v=1.0, reward=1.0, lr=0.1,
        cache_path=cache_file, base_dir=_TMPDIR,
    )
    _check(len(plan["dirty"]) == 0,
           f"Second run: 0 dirty tasks (got {len(plan['dirty'])})")
    _check(len(plan["up_to_date"]) == 11,
           f"Second run: 11 up-to-date (got {len(plan['up_to_date'])})")


# ── Test 5: Cycle Defense — Injected Circular Dependency ────────────────────

def test_05_cycle_defense():
    """A pathological cycle must not crash the engine or hang indefinitely."""
    print("\n▶ TEST 5: Cycle Defense — Pathological Circular Dependency")
    cyclic = {
        "tasks": ["alpha", "bravo", "charlie"],
        "graph": {
            "bravo":   ["alpha"],
            "charlie": ["bravo"],
            "alpha":   ["charlie"],
        },
    }
    try:
        result = engine.execute_build_manifest(
            cyclic, v=1.0, reward=-5.0, lr=0.1,
            cache_path=os.path.join(_TMPDIR, ".cache_t5.json"),
            base_dir=_TMPDIR,
        )
        _ok("Cyclic graph handled without crash")
    except Exception as exc:
        _fail("Cyclic graph caused exception", str(exc))


# ── Test 6: SHA-256 Fingerprint Integrity ────────────────────────────────────

def test_06_fingerprint_integrity():
    """Workspace fingerprints are non-empty and deterministic."""
    print("\n▶ TEST 6: SHA-256 Fingerprint Integrity")
    sigs = fingerprint.scan_project_signatures(_SRC_DIR)
    _check(len(sigs) == 3, f"3 source files fingerprinted (got {len(sigs)})")
    sigs2 = fingerprint.scan_project_signatures(_SRC_DIR)
    _check(sigs == sigs2, "Fingerprints are deterministic across scans")
    for path, digest in sigs.items():
        _check(len(digest) == 64, f"SHA-256 length for {os.path.basename(path)}")


# ── Test 7: Fingerprint Invalidation After Mutation ──────────────────────────

def test_07_fingerprint_invalidation():
    """Changing a source file produces a different signature."""
    print("\n▶ TEST 7: Fingerprint Invalidation After File Mutation")
    target = os.path.join(_SRC_DIR, "utils.py")
    before = fingerprint.calculate_file_hash(target)
    with open(target, "a") as f:
        f.write("# mutation\n")
    after = fingerprint.calculate_file_hash(target)
    _check(before != after, "Hash changed after file mutation")


# ── Test 8: Plan-Build Metrics Consistency ───────────────────────────────────

def test_08_plan_build_metrics():
    """plan_build returns consistent structural and cache metrics."""
    print("\n▶ TEST 8: Plan-Build Metrics Consistency")
    plan = engine.plan_build(
        ENTERPRISE_PIPELINE, v=1.0, reward=1.0, lr=0.1,
        cache_path=os.path.join(_TMPDIR, ".cache_t8.json"),
        base_dir=_TMPDIR,
    )
    _check(plan["n"] == 11, f"plan.n == 11 (got {plan['n']})")
    _check(plan["workers"] >= 1, f"plan.workers >= 1 (got {plan['workers']})")
    _check(set(plan["dirty"] + plan["up_to_date"]) == set(ENTERPRISE_PIPELINE["tasks"]),
           "dirty ∪ up_to_date == all tasks")


# ── Test 9: Empty Manifest Graceful Handling ─────────────────────────────────

def test_09_empty_manifest():
    """An empty manifest must not crash; the engine returns success vacuously."""
    print("\n▶ TEST 9: Empty Manifest Graceful Handling")
    empty = {"tasks": [], "graph": {}}
    result = engine.execute_build_manifest(
        empty, v=1.0, reward=1.0, lr=0.1,
        cache_path=os.path.join(_TMPDIR, ".cache_t9.json"),
        base_dir=_TMPDIR,
    )
    _check(result is True, "Empty manifest returns success")


# ── Test 10: High-Velocity Scheduler Stress ──────────────────────────────────

def test_10_high_velocity_stress():
    """Extreme v / reward values must stay within safe worker bounds [1, 32]."""
    print("\n▶ TEST 10: High-Velocity Scheduler Stress Test")
    n, edges, crit, para = dag_resolver.analyze_graph(
        ENTERPRISE_PIPELINE["tasks"], ENTERPRISE_PIPELINE["graph"],
    )
    for v, reward, lr in [(100.0, 100.0, 10.0), (0.001, -100.0, 0.001), (50.0, 0.0, 5.0)]:
        w = ai_scheduler.calculate_execution_bounds(n, edges, crit, para, v, reward, lr)
        _check(1 <= w <= 32, f"v={v} reward={reward} lr={lr} → workers={w} in [1,32]")


# ── Test 11: End-to-End Pipeline Timing Sanity ──────────────────────────────

def test_11_end_to_end_timing():
    """Full pipeline completes within a generous wall-clock bound (< 10 s)."""
    print("\n▶ TEST 11: End-to-End Pipeline Timing Sanity")
    t0 = time.perf_counter()
    engine.execute_build_manifest(
        ENTERPRISE_PIPELINE, v=1.5, reward=3.0, lr=0.15,
        cache_path=os.path.join(_TMPDIR, ".cache_t11.json"),
        base_dir=_TMPDIR,
    )
    elapsed = time.perf_counter() - t0
    _check(elapsed < 10.0, f"Pipeline completed in {elapsed:.2f}s (limit 10s)")


# ── Runner ───────────────────────────────────────────────────────────────────

def main():
    global _PASS, _FAIL
    print("=" * 72)
    print("  UNIVERSAL BUILD TOOL — ENTERPRISE PIPELINE VALIDATION SUITE")
    print("  11-Task Complex Multi-Stage Simulation")
    print("=" * 72)

    _setup()
    try:
        test_01_full_pipeline_execution()
        test_02_dag_structural_analysis()
        test_03_scheduler_thread_allocation()
        test_04_incremental_cache_rebuild()
        test_05_cycle_defense()
        test_06_fingerprint_integrity()
        test_07_fingerprint_invalidation()
        test_08_plan_build_metrics()
        test_09_empty_manifest()
        test_10_high_velocity_stress()
        test_11_end_to_end_timing()
    finally:
        _teardown()

    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    if _FAIL == 0:
        print(f"  🎉 ALL {total} ASSERTIONS PASSED — ENTERPRISE PIPELINE VALIDATED")
    else:
        print(f"  ❌ {_FAIL}/{total} ASSERTION(S) FAILED")
    print("=" * 72)
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
