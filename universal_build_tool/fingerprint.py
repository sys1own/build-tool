import os
import json
import hashlib

# Default on-disk cache for incremental builds. Stores the per-task composite
# signatures from the previous build round so unchanged work can be skipped.
DEFAULT_CACHE_FILENAME = ".ubt_cache.json"

# Stable placeholder digest used when a referenced file is missing/unreadable,
# so a task's signature stays deterministic instead of silently vanishing.
_MISSING_DIGEST = "0" * 64


def calculate_file_hash(file_path):
    """Generates high-speed SHA-256 fingerprint for cache change verification."""
    hasher = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return None


def scan_project_signatures(src_directory):
    """Maps cryptographic hashes across all workspace assets."""
    signatures = {}
    if not os.path.exists(src_directory):
        return signatures
    for root, _, files in os.walk(src_directory):
        for fname in sorted(files):
            full_path = os.path.join(root, fname)
            f_hash = calculate_file_hash(full_path)
            if f_hash:
                signatures[full_path] = f_hash
    return signatures


def normalize_paths(paths):
    """Coerce a path / iterable-of-paths into a clean list of str paths."""
    if paths is None:
        return []
    if isinstance(paths, (str, bytes, os.PathLike)):
        paths = [paths]
    out = []
    for p in paths:
        if p:
            out.append(os.fspath(p))
    return out


def combine_signatures(parts):
    """Deterministically fold an iterable of strings into one SHA-256 digest.

    Order-independent (parts are sorted) so the composite is stable regardless
    of dependency-declaration order.
    """
    hasher = hashlib.sha256()
    for part in sorted(str(p) for p in parts):
        hasher.update(part.encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def signature_for_paths(paths):
    """Composite cryptographic signature over one or more files.

    Missing/unreadable files contribute a stable placeholder so the result is
    deterministic. Returns ``None`` when no paths are supplied (the caller then
    knows the task has no file-based inputs of its own).
    """
    norm = normalize_paths(paths)
    if not norm:
        return None
    parts = []
    for p in norm:
        digest = calculate_file_hash(p)
        parts.append(f"{p}:{digest or _MISSING_DIGEST}")
    return combine_signatures(parts)


def load_cache(cache_path):
    """Load the persisted signature cache. Robust to a missing/corrupt file."""
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_cache(cache_path, cache):
    """Persist the signature cache as JSON (best-effort, write-then-rename)."""
    try:
        directory = os.path.dirname(os.path.abspath(cache_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp_path = cache_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
        os.replace(tmp_path, cache_path)
        return True
    except Exception:
        return False
