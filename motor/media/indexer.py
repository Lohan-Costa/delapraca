import os
import logging
import unicodedata
from pathlib import Path

log = logging.getLogger("relinker.media.indexer")

VIDEO_EXTENSIONS = {
    ".mxf", ".mov", ".mp4", ".avi", ".mkv",
    ".mts", ".m2ts", ".r3d", ".braw", ".m4v", ".mpg", ".mpeg", ".m2v",
    ".crm", ".rmf", ".ari", ".arx",
    ".wav", ".wave", ".bwf", ".aif", ".aiff", ".aifc",
    ".mp3", ".m4a", ".aac", ".flac", ".ogg",
    ".dpx", ".exr", ".tif", ".tiff", ".jpg", ".jpeg",
    ".png", ".tga", ".bmp", ".gif", ".psd",
    ".jp2", ".j2k", ".hdr",
}


def index_folder(paths, progress=None, name_hints=None, exclude_paths=None) -> dict:
    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        return {"files": [], "total_files": 0}

    def _emit(payload):
        if progress is None:
            return
        try:
            progress(payload)
        except Exception:
            pass

    import bench
    import control
    import time as _time

    accept_name = _build_hint_filter(name_hints)
    exclude = set(exclude_paths or ())

    seen: set[str] = set()
    candidates: list[Path] = []
    scanned = 0
    _scan_t = _time.perf_counter()

    for raw in paths:
        p = Path(raw)
        if not p.exists():
            log.warning("Caminho não encontrado, ignorando: %s", raw)
            continue

        if p.is_dir():
            log.info("Indexando pasta: %s", p)
            for entry in _iter_entries(p):
                scanned += 1
                _collect(entry, seen, candidates, accept_name, exclude)
                if scanned % 200 == 0:
                    control.check()
                    _emit({"phase": "scan", "scanned": scanned,
                           "found": len(candidates), "current": entry.name})
        elif p.is_file():
            scanned += 1
            _collect(p, seen, candidates, accept_name, exclude)
        else:
            log.warning("Caminho não é pasta nem arquivo, ignorando: %s", raw)

    _emit({"phase": "scan", "scanned": scanned, "found": len(candidates), "current": ""})
    bench.record("index.scan", (_time.perf_counter() - _scan_t) * 1000.0,
                 scanned=scanned, media=len(candidates), roots=len(paths))

    groups, singles = _group_sequence_paths(candidates)
    work: list[tuple] = [(fp, None) for fp in singles] + [(g["rep_path"], g) for g in groups]
    total = len(work)
    files: list[dict] = []
    _meta_t = _time.perf_counter()
    for i, (fp, g) in enumerate(work, 1):
        control.check()
        _emit({"phase": "meta", "i": i, "total": total,
               "current": fp.name, "indexed": len(files)})
        info = _safe_meta(fp)
        if info is not None:
            if g is not None:
                info["sequence"] = g["sequence"]
            files.append(info)
    _emit({"phase": "meta", "i": total, "total": total, "current": "", "indexed": len(files)})
    bench.record("index.meta", (_time.perf_counter() - _meta_t) * 1000.0,
                 files=len(files), seqs=len(groups), total=total)

    log.info("Indexação concluída: %d entrada(s) (%d sequência(s)), %d varrida(s)",
             len(files), len(groups), scanned)
    return {"files": files, "total_files": len(files)}


def _strict_name_key(name: str) -> str:
    from media.matcher import _strip_avid_suffixes
    s = _strip_avid_suffixes(name or "")
    s = unicodedata.normalize("NFC", s)
    return s.strip().lower()


def _build_hint_filter(name_hints):
    if not name_hints:
        return None
    from media.matcher import _normalize_name

    estritas = {k for k in (_strict_name_key(h) for h in name_hints) if k}
    frouxas = {k for k in (_normalize_name(h) for h in name_hints) if k}
    if not (estritas or frouxas):
        return None

    def aceitar(stem: str) -> bool:
        return _strict_name_key(stem) in estritas or _normalize_name(stem) in frouxas

    return aceitar


def _iter_entries(root: Path):
    stack: list[str] = [str(root)]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError as e:
            log.warning("Sem acesso ao diretório %s: %s — ignorado", d, e)
            continue
        with it:
            while True:
                try:
                    entry = next(it)
                except StopIteration:
                    break
                except OSError as e:
                    log.warning("Erro lendo entrada em %s: %s — ignorado", d, e)
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                        continue
                except OSError:
                    continue
                yield entry


def _collect(entry, seen: set, out: list, accept_name=None, exclude=None) -> None:
    name = entry.name
    if name.startswith("._"):
        return
    raw_path = entry.path if hasattr(entry, "path") else str(entry)
    if exclude and raw_path in exclude:
        return
    stem, dotext = os.path.splitext(name)
    if dotext.lower() not in VIDEO_EXTENSIONS:
        return
    if accept_name is not None and not accept_name(stem):
        return
    fp = Path(entry.path) if hasattr(entry, "path") else entry
    try:
        a = str(fp.resolve())
    except OSError:
        a = str(fp)
    if a in seen:
        return
    seen.add(a)
    out.append(fp)


def _safe_meta(fp: Path):
    try:
        info = _extract_metadata(fp)
        log.info("Indexado: %s", fp.name)
        return info
    except Exception as e:
        log.warning("Falha ao extrair metadados de %s: %s — ignorado", fp.name, e)
        return None


_SEQUENCE_EXTS = {".exr", ".dpx", ".tif", ".tiff", ".jp2", ".j2k", ".hdr", ".ari", ".arx"}


def _group_sequence_paths(candidates: list):
    from frames.sequence import parse_sequence_name
    buckets: dict = {}
    singles: list = []
    for fp in candidates:
        ext = fp.suffix.lower()
        parsed = parse_sequence_name(fp.stem) if ext in _SEQUENCE_EXTS else None
        if not parsed:
            singles.append(fp)
            continue
        base, sep, num, pad = parsed
        buckets.setdefault((str(fp.parent), base, sep, ext, pad), []).append((int(num), fp))
    groups = []
    for (d, base, sep, ext, pad), members in buckets.items():
        members.sort(key=lambda x: x[0])
        nums = [n for n, _ in members]
        span = nums[-1] - nums[0] + 1
        if len(members) < 2 or len(members) < 0.5 * span:
            for _, fp in members:
                singles.append(fp)
            continue
        groups.append({
            "rep_path": members[0][1],
            "sequence": {"dir": d, "base": base, "sep": sep, "ext": ext, "pad": pad,
                         "first": nums[0], "last": nums[-1], "count": len(members)},
        })
    return groups, singles


_META_CACHE: dict[str, tuple] = {}


def _codec_label(vt) -> str | None:
    if vt is None:
        return None
    commercial = getattr(vt, "commercial_name", None) or getattr(vt, "format_commercial", None)
    if commercial:
        c = str(commercial).strip()
        if c.startswith("Apple "):
            c = c[len("Apple "):]
        if c.upper() == "XDCAM HD422":
            return "XDCAM 50"
        return c

    fmt  = getattr(vt, "format", None)
    prof = getattr(vt, "format_profile", None)
    if fmt:
        f = str(fmt).strip()
        fu = f.upper()
        if fu == "AVC":
            return "H.264"
        if fu == "HEVC":
            return "H.265"
        if "PRORES" in fu:
            return f"ProRes {prof}".strip() if prof else "ProRes"
        return f"{f} {prof}".strip() if prof else f
    return getattr(vt, "codec_id", None)


def _extract_metadata(file_path: Path) -> dict:
    from pymediainfo import MediaInfo

    abs_str = str(file_path)
    st = None
    try:
        st = file_path.stat()
        cached = _META_CACHE.get(abs_str)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            return dict(cached[2])
    except OSError:
        pass

    media_info = MediaInfo.parse(str(file_path))
    video_track = next(
        (t for t in media_info.tracks if t.track_type == "Video"),
        None
    )
    general_track = next(
        (t for t in media_info.tracks if t.track_type == "General"),
        None
    )

    stat = st or file_path.stat()

    info = {
        "path": str(file_path),
        "filename": file_path.name,
        "extension": file_path.suffix.lower(),
        "size_bytes": stat.st_size,
        "duration_ms": float(video_track.duration) if video_track and video_track.duration else None,
        "frame_rate": float(video_track.frame_rate) if video_track and video_track.frame_rate else None,
        "width": int(video_track.width) if video_track and video_track.width else None,
        "height": int(video_track.height) if video_track and video_track.height else None,
        "codec": video_track.codec_id if video_track else None,
        "codec_label": _codec_label(video_track),
        "uid": getattr(video_track, "unique_id", None),
        "tc_start": getattr(general_track, "time_code_of_first_frame", None),
        "camera_roll": getattr(general_track, "com_apple_quicktime_reelname", None),
        "created_at": getattr(general_track, "file_creation_date", None),
    }
    try:
        _META_CACHE[abs_str] = (stat.st_mtime, stat.st_size, dict(info))
    except Exception:
        pass
    return info
