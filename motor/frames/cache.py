import hashlib
import json
import os
from pathlib import Path
from typing import Optional, Tuple

APP_NAME = os.environ.get("RELINKER_APP_NAME", "Relinker")

_INDEX_NAME = "_index.json"

_MANIFESTS: dict = {}


def file_signature(path: str) -> str:
    try:
        st = os.stat(path)
        return f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        return "0:0"


def _key_hash(parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _sanitize(label: str) -> str:
    bad = set('/\\:*?"<>|\n\r\t')
    s = "".join("_" if c in bad else c for c in label).strip()
    return s[:120] or "frame"


def _index_path(cache_dir: str) -> Path:
    return Path(cache_dir) / _INDEX_NAME


def _manifest(cache_dir: str) -> dict:
    m = _MANIFESTS.get(cache_dir)
    if m is None:
        m = _load_manifest(cache_dir)
        _MANIFESTS[cache_dir] = m
    return m


def _load_manifest(cache_dir: str) -> dict:
    mapping = {}
    ip = _index_path(cache_dir)
    if ip.is_file():
        try:
            data = json.loads(ip.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                mapping = {str(k): str(v) for k, v in data.items()}
        except (OSError, ValueError):
            mapping = {}
    used = set(mapping.values())
    for f in _cache_images(cache_dir):
        used.add(f.name)
    return {"map": mapping, "used": used}


def _save_manifest(cache_dir: str, m: dict) -> None:
    try:
        _index_path(cache_dir).write_text(
            json.dumps(m["map"], ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def resolve_cache_file(cache_dir: str, key_parts, label: Optional[str] = None,
                       ext: str = "png") -> Path:
    d = Path(cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    kh = _key_hash(key_parts)
    m = _manifest(cache_dir)
    fname = m["map"].get(kh)
    if fname:
        return d / fname
    base = _sanitize(label) if label else kh[:12]
    fname = f"{base}.{ext}"
    if fname in m["used"]:
        i = 1
        while f"{base}_{i}.{ext}" in m["used"]:
            i += 1
        fname = f"{base}_{i}.{ext}"
    m["map"][kh] = fname
    m["used"].add(fname)
    _save_manifest(cache_dir, m)
    return d / fname


def cached_file(cache_dir: str, key_parts, ext: str = "png") -> Optional[Path]:
    if not cache_dir:
        return None
    fname = _manifest(cache_dir)["map"].get(_key_hash(key_parts))
    if fname:
        p = Path(cache_dir) / fname
        if p.exists():
            return p
    return None


_SNAP_NAME = "_snap.json"
_SNAPS: dict = {}


def _snap_path(cache_dir: str) -> Path:
    return Path(cache_dir) / _SNAP_NAME


def _snaps(cache_dir: str) -> dict:
    m = _SNAPS.get(cache_dir)
    if m is None:
        m = {}
        sp = _snap_path(cache_dir)
        if sp.is_file():
            try:
                d = json.loads(sp.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    m = {str(k): str(v) for k, v in d.items()}
            except (OSError, ValueError):
                m = {}
        _SNAPS[cache_dir] = m
    return m


def snap_get(cache_dir: Optional[str], snap_key) -> Optional[str]:
    if not cache_dir:
        return None
    return _snaps(cache_dir).get(_key_hash(snap_key))


def snap_put(cache_dir: Optional[str], snap_key, value: str) -> None:
    if not cache_dir:
        return
    m = _snaps(cache_dir)
    m[_key_hash(snap_key)] = str(value)
    try:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        _snap_path(cache_dir).write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def image_dimensions(path: str) -> Tuple[Optional[int], Optional[int]]:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None, None
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:2] == b"\xff\xd8":
        i, n = 2, len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            mk = data[i + 1]
            if 0xC0 <= mk <= 0xCF and mk not in (0xC4, 0xC8, 0xCC):
                h = int.from_bytes(data[i + 5:i + 7], "big")
                w = int.from_bytes(data[i + 7:i + 9], "big")
                return w, h
            if mk in (0xD8, 0xD9) or 0xD0 <= mk <= 0xD7:
                i += 2
                continue
            i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None, None


def _cache_images(cache_dir: str):
    d = Path(cache_dir)
    if d.is_dir():
        for f in d.iterdir():
            if f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                yield f


def cache_stats(cache_dir: Optional[str]) -> dict:
    total = 0
    count = 0
    if cache_dir:
        for f in _cache_images(cache_dir):
            try:
                total += f.stat().st_size
                count += 1
            except OSError:
                pass
    return {"bytes": total, "count": count, "cache_dir": cache_dir}


def clear_cache(cache_dir: Optional[str]) -> dict:
    removed = 0
    if cache_dir:
        for f in _cache_images(cache_dir):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
        for extra in (_index_path(cache_dir), _snap_path(cache_dir)):
            try:
                extra.unlink(missing_ok=True)
            except OSError:
                pass
        _MANIFESTS.pop(cache_dir, None)
        _SNAPS.pop(cache_dir, None)
    return {"removed": removed, "cache_dir": cache_dir}
