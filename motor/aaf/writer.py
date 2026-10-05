from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Optional

import aaf2
from media import fftools

log = logging.getLogger("relinker.aaf.writer")


_AVID_MOB_SUFFIX_RE = re.compile(
    r"\.(new|sync|cds|grp|sub|copy|exported|cpy)\.\d{1,4}$", re.IGNORECASE)


def _strip_avid_mob_suffix(name: str) -> str:
    if not name:
        return name
    out, prev = name, None
    while out != prev:
        prev = out
        out = _AVID_MOB_SUFFIX_RE.sub("", out)
    return out


def _clean_mob_names(f) -> int:
    n = 0
    for mob in f.content.mobs:
        if type(mob).__name__ != "MasterMob":
            continue
        old = getattr(mob, "name", "") or ""
        new = _strip_avid_mob_suffix(old)
        if new and new != old:
            try:
                mob.name = new
                n += 1
            except Exception as e:
                log.debug("Falha ao renomear mob '%s': %s", old, e)
    return n

_MXF_AMA_CONTAINER_GUID = "60eb8921-2a02-4406-891c-d9b6a6ae0645"

_AMA_CONTAINER_GUID = {
    "mxf": _MXF_AMA_CONTAINER_GUID,
    "quicktime": "87a0584d-cafa-41f5-9a68-f0cadefdbb71",
    "wav": "3711d3cc-62d0-49d7-b0ae-c118101d1a16",
}
_AMA_HANDLER_GUID = {
    "quicktime": "3c06dc73-0276-4c4c-ba3f-3f47120cd1e9",
}
_MXF_EXTS = {".mxf"}
_WAV_EXTS = {".wav", ".wave", ".bwf", ".aif", ".aiff", ".aifc"}
_QUICKTIME_EXTS = {".mov", ".mp4", ".m4v", ".mpg", ".mpe", ".mpeg",
                   ".3gp", ".3g2", ".qt", ".avi", ".m2ts", ".mts"}


def _ama_container_key(ext: str) -> Optional[str]:
    if ext in _MXF_EXTS:
        return "mxf"
    if ext in _QUICKTIME_EXTS:
        return "quicktime"
    if ext in _WAV_EXTS:
        return "wav"
    return None

_XDCAM_HD50_RESOLUTION_ID = 4076

_MPEG2_PATCHED = False


def _ffprobe_meta(path: str) -> dict:
    import subprocess
    import json
    from media import fftools
    out = subprocess.run(
        [fftools.ffprobe_exe(), "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True, timeout=60, **fftools.SEM_JANELA,
    )
    meta = json.loads(out.stdout) if out.stdout else {}
    if "format" not in meta:
        raise RuntimeError(f"ffprobe não retornou 'format' para {path}: {out.stderr[:200]}")
    meta["format"]["filename"] = path
    return meta


def _tc_string_to_frames(tc: str, fps_int: int, drop: bool) -> int:
    parts = re.split(r"[:;.]", tc.strip())
    if len(parts) != 4:
        raise ValueError(f"TC inválido: {tc!r}")
    h, m, s, ff = (int(x) for x in parts)
    total = ((h * 3600 + m * 60 + s) * fps_int) + ff
    if drop and fps_int in (30, 60):
        dropf = 4 if fps_int == 60 else 2
        total_min = h * 60 + m
        total -= dropf * (total_min - total_min // 10)
    return total


def _probe_online_tc(local_path: str):
    from fractions import Fraction
    try:
        meta = _ffprobe_meta(local_path)
    except Exception as e:
        log.debug("ffprobe TC falhou p/ %s: %s", local_path, e)
        return None
    streams = meta.get("streams", []) or []
    vid = next((s for s in streams if s.get("codec_type") == "video"), None)
    if vid is None:
        return None
    tc = ((meta.get("format", {}) or {}).get("tags", {}) or {}).get("timecode")
    if not tc:
        for s in streams:
            t = ((s.get("tags", {}) or {}).get("timecode"))
            if t:
                tc = t
                break
    if not tc:
        return None
    rfr = vid.get("r_frame_rate") or vid.get("avg_frame_rate") or ""
    try:
        num, den = rfr.split("/")
        er = Fraction(int(num), int(den))
        fps_real = float(er)
    except Exception:
        return None
    if fps_real <= 0:
        return None
    fps_int = round(fps_real)
    drop = (";" in tc) or ("." in tc)
    try:
        start = _tc_string_to_frames(tc, fps_int, drop)
    except Exception as e:
        log.debug("Conversão de TC '%s' falhou: %s", tc, e)
        return None
    nb = vid.get("nb_frames")
    try:
        nb = int(nb)
    except Exception:
        dur = float((meta.get("format", {}) or {}).get("duration") or 0)
        nb = round(dur * fps_real) if dur else 0
    return (start, fps_int, drop, er, nb, tc)


def _patch_pyaaf2_mpeg2() -> None:
    global _MPEG2_PATCHED
    if _MPEG2_PATCHED:
        return
    from aaf2 import mxf as aaf_mxf

    def _fixed_link(self):
        self.data["ResolutionID"] = _XDCAM_HD50_RESOLUTION_ID
        return super(aaf_mxf.MXFMPEG2VideoDescriptor, self).link()

    aaf_mxf.MXFMPEG2VideoDescriptor.link = _fixed_link
    _MPEG2_PATCHED = True


def _macos_boot_volume_name() -> str:
    import os
    try:
        for name in os.listdir("/Volumes"):
            if os.path.realpath(os.path.join("/Volumes", name)) == "/":
                return name
    except OSError:
        pass
    return "Macintosh HD"


def path_to_aaf_url(local_path: str) -> str:
    import sys
    import os
    import urllib.parse

    if sys.platform != "darwin":
        return Path(local_path).as_uri()

    abs_path = os.path.abspath(local_path)
    if abs_path.startswith("/Volumes/"):
        vol_rel = abs_path[len("/Volumes/"):]
    else:
        vol_rel = f"{_macos_boot_volume_name()}/{abs_path.lstrip('/')}"

    encoded = "/".join(urllib.parse.quote(seg) for seg in vol_rel.split("/"))
    return f"file:///{encoded}"


def _aaf_url_to_local(url: str) -> Optional[str]:
    import sys
    import os
    import urllib.parse

    if not url or not url.startswith("file://"):
        return None
    raw = urllib.parse.unquote(url[len("file://"):])
    if sys.platform != "darwin":
        return raw
    parts = raw.lstrip("/").split("/", 1)
    vol = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    try:
        if os.path.realpath(os.path.join("/Volumes", vol)) == "/":
            return "/" + rest
    except OSError:
        pass
    return "/Volumes/" + vol + (("/" + rest) if rest else "")


def _file_umid_mobid(local_path: str):
    import subprocess
    import re
    from aaf2.mobid import MobID

    if not local_path or not Path(local_path).exists():
        return None
    try:
        from media import fftools
        out = subprocess.run(
            [fftools.ffprobe_exe(), "-v", "error", "-show_entries", "format_tags:stream_tags", local_path],
            capture_output=True, text=True, timeout=30, **fftools.SEM_JANELA,
        ).stdout
    except Exception as e:
        log.debug("ffprobe falhou para UMID de '%s': %s", local_path, e)
        return None
    m = re.search(r"file_package_umid=0x([0-9A-Fa-f]{64})", out)
    if not m:
        return None
    h = m.group(1).lower()
    groups = [h[i:i + 8] for i in range(0, 64, 8)]
    try:
        return MobID("urn:smpte:umid:" + ".".join(groups))
    except Exception as e:
        log.debug("MobID inválido a partir do UMID '%s': %s", h, e)
        return None


def _material_num(mob_id) -> str:
    s = str(mob_id)
    parts = s.split(":")[-1].split(".")
    return ".".join(parts[4:8]) if len(parts) >= 8 else s


def _avid_chain_id(mob_id, matnum: str, inst_byte: int):
    from aaf2.mobid import MobID
    parts = str(mob_id).split(":")[-1].split(".")
    mat = (matnum or "").split(".")
    if len(parts) < 8 or len(mat) < 4:
        return mob_id
    g = parts[3]
    parts[3] = g[:2] + format(inst_byte & 0xFF, "02x") + g[4:]
    parts[4:8] = mat[:4]
    try:
        return MobID("urn:smpte:umid:" + ".".join(parts))
    except Exception as e:
        log.debug("MobID inválido (matnum=%s inst=%#x): %s", matnum, inst_byte, e)
        return mob_id


def _iter_sourceclips(mob):
    def walk(seg):
        k = type(seg).__name__
        if k == "SourceClip":
            yield seg
        elif hasattr(seg, "components"):
            for c in seg.components:
                yield from walk(c)
        elif k == "NestedScope":
            for s in getattr(seg, "slots", []):
                yield from walk(s)
        elif k == "OperationGroup":
            try:
                for inp in (seg.get("InputSegments") or []):
                    yield from walk(inp)
            except Exception:
                pass
        elif k == "Selector":
            try:
                sp = seg.get("Selected")
                sel = sp.value if hasattr(sp, "value") else sp
                if sel is not None:
                    yield from walk(sel)
            except Exception:
                pass
            try:
                for alt in (seg.get("Alternates") or []):
                    yield from walk(alt)
            except Exception:
                pass
    for slot in mob.slots:
        yield from walk(slot.segment)


def build_url_map(
    segments: list[dict],
    match_results: list[dict],
) -> dict[str, str]:
    url_map: dict[str, str] = {}
    url_method: dict[str, str] = {}
    _RANK = {"path": 5, "uid": 4, "exact_name": 3, "fuzzy_name": 2, "visual": 1, "manual": 9}

    seg_by_idx: dict[int, dict] = {i: s for i, s in enumerate(segments)}

    def _consider(url: str, path: str, method: str) -> None:
        if not url or not path:
            return
        cur = url_method.get(url)
        if cur is None or _RANK.get(method, 0) > _RANK.get(cur, 0):
            url_map[url] = path_to_aaf_url(path)
            url_method[url] = method
            log.debug("Mapeado (%s): %s → %s", method, url[-60:], path[-40:])

    for result in match_results:
        if not result:
            continue
        seg_idx      = result.get("segment_index")
        matched_path = result.get("matched_path")

        if matched_path is None:
            continue

        seg = seg_by_idx.get(seg_idx, {})
        method = result.get("match_method", "")

        media_ref = seg.get("media_ref") or {}
        _consider(media_ref.get("url", ""), matched_path, method)

        for alt_match in result.get("alternate_matches", []):
            _consider(alt_match.get("original_url", ""),
                      alt_match.get("matched_path"),
                      alt_match.get("method", ""))

    log.info("URL map construído: %d entrada(s)", len(url_map))
    return url_map


def _norm_name(name: str) -> str:
    if not name:
        return ""
    n = _strip_avid_mob_suffix(name)
    n = _MEDIA_EXT_RE.sub("", n) if "_MEDIA_EXT_RE" in globals() else re.sub(
        r"\.(mxf|mov|mp4|m4v|avi|mkv|wav|wave|bwf|aif|aiff|aifc|mp3|m4a|aac|flac|"
        r"mts|m2ts|r3d|braw|dpx|exr|tif|tiff|jpg|jpeg|png)$", "", n, flags=re.IGNORECASE)
    n = re.sub(r"[\._\-/\\]+", " ", n.lower())
    return " ".join(n.split())


def build_name_map(segments: list, match_results: list) -> dict:
    name_map: dict[str, str] = {}
    seg_by_idx = {i: s for i, s in enumerate(segments)}
    for result in match_results or []:
        if not result:
            continue
        path = result.get("matched_path")
        if not path:
            continue
        seg = seg_by_idx.get(result.get("segment_index"), {}) or {}
        key = _norm_name(seg.get("clip_name") or "")
        if key and key not in name_map:
            name_map[key] = path
    log.info("Name map (offline) construído: %d entrada(s)", len(name_map))
    return name_map


def _rename_offline_to_match(f, name_map: dict) -> int:
    import os as _os
    _AUDIO_EXTS = {".wav", ".wave", ".bwf", ".aif", ".aiff", ".aifc",
                   ".mp3", ".m4a", ".aac", ".flac"}
    renamed = 0
    for mob in list(f.content.mobs):
        old = getattr(mob, "name", "") or ""
        tgt = name_map.get(_norm_name(old))
        if not tgt:
            continue
        base = _os.path.basename(tgt)
        ext = _os.path.splitext(base)[1].lower()
        new = base if ext in _AUDIO_EXTS else _os.path.splitext(base)[0]
        if new and new != old:
            try:
                mob.name = new
                renamed += 1
            except Exception as e:
                log.debug("Falha ao renomear (nativo) '%s'→'%s': %s", old, new, e)
    log.info("Relink nativo: %d mob(s) renomeado(s) p/ casar a caixa do original", renamed)
    return renamed


def relink_aaf(
    source_aaf: str,
    output_aaf: str,
    url_map: dict[str, str],
    dry_run: bool = False,
    ama_autolink: bool = True,
    name_map: dict[str, str] | None = None,
    denest: bool = True,
) -> dict:
    src = Path(source_aaf)
    out = Path(output_aaf)

    if not src.exists():
        raise FileNotFoundError(f"AAF de origem não encontrado: {source_aaf}")
    if src.resolve() == out.resolve():
        raise ValueError(
            "source_aaf e output_aaf devem ser arquivos diferentes. "
            "O original nunca é modificado no lugar."
        )
    name_map = name_map or {}
    if not url_map and not name_map:
        raise ValueError("url_map e name_map vazios — nada para atualizar.")

    mobs_updated: list[dict] = []
    mobs_skipped: list[dict] = []
    error_count = 0
    align_targets: list = []
    ids_aligned: list[dict] = []
    relinked_leaves: dict[str, str] = {}
    ama_promoted: list[dict] = []
    tc_rebased: list[dict] = []
    names_cleaned = 0
    names_renamed = 0
    denest_result = {"subclips": 0, "groupclips": 0}

    def _process_mobs(f) -> None:
        nonlocal error_count
        for mob in f.content.mobs:
            if type(mob).__name__ != 'SourceMob':
                continue
            desc = getattr(mob, 'descriptor', None)
            if not desc or type(desc).__name__ != 'ImportDescriptor':
                continue

            mob_name = getattr(mob, 'name', '') or ''

            try:
                locators = list(getattr(desc, 'locator', []))
            except Exception as e:
                log.warning("Falha ao ler locators de '%s': %s", mob_name, e)
                error_count += 1
                continue

            for loc in locators:
                try:
                    current_url = loc['URLString'].value
                except Exception as e:
                    log.warning("Falha ao ler URLString de '%s': %s", mob_name, e)
                    error_count += 1
                    continue

                if current_url in url_map:
                    new_url = url_map[current_url]
                    if not dry_run:
                        loc['URLString'].value = new_url
                        log.info("✓ %s: ...%s → ...%s",
                                 mob_name, current_url[-50:], new_url[-50:])
                        align_targets.append((mob, _aaf_url_to_local(new_url)))
                        relinked_leaves[str(mob.mob_id)] = new_url
                    mobs_updated.append({
                        "mob_name": mob_name,
                        "old_url": current_url,
                        "new_url": new_url,
                    })
                else:
                    mobs_skipped.append({
                        "mob_name": mob_name,
                        "url": current_url,
                    })

    def _align_source_ids(f) -> None:
        remap: dict = {}
        for mob, local_path in align_targets:
            target = _file_umid_mobid(local_path) if local_path else None
            if target is None:
                continue
            if _material_num(mob.mob_id) == _material_num(target):
                continue
            old = mob.mob_id
            mob.mob_id = target
            remap[str(old)] = target
            ids_aligned.append({
                "mob_name": getattr(mob, "name", "") or "",
                "old_id": str(old),
                "new_id": str(target),
            })
            log.info("MobID alinhado ao UMID: %s  %s → %s",
                     getattr(mob, "name", ""), _material_num(old), _material_num(target))

        if not remap:
            return

        for m in f.content.mobs:
            for sc in _iter_sourceclips(m):
                try:
                    cur = sc.mob_id
                except Exception:
                    continue
                if cur is not None and str(cur) in remap:
                    sc.mob_id = remap[str(cur)]

    def _first_sourceclip(seg):
        k = type(seg).__name__
        if k == "SourceClip":
            return seg
        if k == "Selector":
            try:
                sel = seg["Selected"].value
                r = _first_sourceclip(sel) if sel is not None else None
                if r is not None:
                    return r
            except Exception:
                pass
            try:
                for alt in (list(seg.get("Alternates") or [])):
                    r = _first_sourceclip(alt)
                    if r is not None:
                        return r
            except Exception:
                pass
        if k == "OperationGroup":
            try:
                for ip in (list(seg.get("InputSegments") or [])):
                    r = _first_sourceclip(ip)
                    if r is not None:
                        return r
            except Exception:
                pass
        if hasattr(seg, "components"):
            for c in seg.components:
                r = _first_sourceclip(c)
                if r is not None:
                    return r
        return None

    def _add_imported_ama(f, master) -> bool:
        try:
            current = list(master["MobAttributeList"].value or [])
        except Exception:
            current = []
        for tv in current:
            if tv.name == "_IMPORTED_AMA":
                return False
        current.append(f.create.TaggedValue("_IMPORTED_AMA", 1))
        master["MobAttributeList"].value = current
        return True

    def _master_relink_local(f, master):
        for slot in master.slots:
            sc = _first_sourceclip(slot.segment)
            if sc is None:
                continue
            cur = next((m for m in f.content.mobs
                        if str(m.mob_id) == str(sc.mob_id)), None)
            seen = {str(master.mob_id)}
            depth = 0
            while cur is not None and depth < 6:
                cid = str(cur.mob_id)
                if cid in seen:
                    break
                seen.add(cid)
                if cid in relinked_leaves:
                    return _aaf_url_to_local(relinked_leaves[cid]), cid
                nxt = None
                for s in cur.slots:
                    r = _first_sourceclip(s.segment)
                    if r is not None and str(r.mob_id) != cid:
                        nxt = next((m for m in f.content.mobs
                                    if str(m.mob_id) == str(r.mob_id)), None)
                        if nxt is not None:
                            break
                cur = nxt
                depth += 1
        return None, None

    def _rebase_source_tc(f) -> None:
        import os as _os
        mobs = {str(m.mob_id): m for m in f.content.mobs}
        for master in list(f.content.mastermobs()):
            local, leaf_id = _master_relink_local(f, master)
            if not local or not _os.path.exists(local):
                continue
            if _os.path.splitext(local)[1].lower() in _WAV_EXTS:
                continue
            online = _probe_online_tc(local)
            if online is None:
                continue
            on_start, _on_fps_int, _on_drop, on_er, on_nb, on_tcstr = online
            on_fps = float(on_er) if on_er else float(_on_fps_int)
            if on_fps <= 0:
                continue
            online_s0 = on_start / on_fps
            online_s1 = (on_start + int(on_nb or 0)) / on_fps
            info = _source_tc_for_master(f, master)
            if info is None:
                continue
            cur_disp, fps_int, _drop, er, _length = info
            chain_fps = float(er) if er else float(fps_int or 0)
            if chain_fps <= 0:
                continue
            cur_s = cur_disp / chain_fps
            if (online_s0 - 0.5) <= cur_s <= (online_s1 + 0.5):
                continue
            target = round(online_s0 * chain_fps)
            delta = target - cur_disp
            if delta == 0:
                continue
            applied = 0
            proxy_ids = set()
            for slot in master.slots:
                sc = _first_sourceclip(slot.segment)
                if sc is not None:
                    proxy_ids.add(str(sc.mob_id))
            for pid in proxy_ids:
                proxy = mobs.get(pid)
                if proxy is None:
                    continue
                for s in proxy.slots:
                    psc = _first_sourceclip(s.segment)
                    if psc is None or "StartTime" not in psc:
                        continue
                    old = int(psc["StartTime"].value or 0)
                    new = max(0, old + delta)
                    if new != old:
                        psc["StartTime"].value = new
                        applied += 1
            if applied:
                log.info("TC de origem rebaseado ao online: %s  TC=%s (era frame %d @%gfps, "
                         "fora do alcance do arquivo) → frame %d",
                         getattr(master, "name", ""), on_tcstr, int(cur_disp), chain_fps,
                         int(target))
                tc_rebased.append({
                    "mob_name": getattr(master, "name", "") or "",
                    "online_tc": on_tcstr,
                    "online_start": int(target),
                    "offline_start": int(cur_disp),
                    "fps": int(round(chain_fps)),
                    "drop": bool(_drop),
                })

    def _denest_timeline(f) -> dict:
        counts = {"subclips": 0, "groupclips": 0}
        mobs = {str(m.mob_id): m for m in f.content.mobs}

        def _repoint_subclip(sc) -> bool:
            mob = getattr(sc, "mob", None)
            if (mob is None or type(mob).__name__ != "CompositionMob"
                    or getattr(mob, "usage", None) != "Usage_SubClip"):
                return False
            sid = getattr(sc, "slot_id", None)
            tslot = next((s for s in mob.slots if getattr(s, "slot_id", None) == sid), None)
            if tslot is None:
                tslot = next((s for s in mob.slots if s.media_kind == sc.media_kind), None)
            if tslot is None:
                return False
            inner = _first_sourceclip(tslot.segment)
            im = getattr(inner, "mob", None) if inner else None
            if im is None:
                return False
            try:
                sc.mob_id = im.mob_id
                islot = getattr(inner, "slot_id", None)
                if islot is not None:
                    sc["SourceMobSlotID"].value = islot
                cur = sc["StartTime"].value if "StartTime" in sc else 0
                sc["StartTime"].value = (cur or 0) + (getattr(inner, "start", 0) or 0)
                return True
            except Exception as e:
                log.debug("Falha ao de-nest subclip: %s", e)
                return False

        def _is_groupclip(sel):
            try:
                selm = sel["Selected"].value
                mob = getattr(_first_sourceclip(selm), "mob", None) if selm is not None else None
                if mob is None or type(mob).__name__ != "CompositionMob":
                    return False
                try:
                    ac = mob["AppCode"].value
                except Exception:
                    ac = getattr(mob, "application_code", None)
                return ac in (4, 5)
            except Exception:
                return False

        def walk(seg):
            k = type(seg).__name__
            if k == "SourceClip":
                if _repoint_subclip(seg):
                    counts["subclips"] += 1
                return
            if k == "Sequence":
                comps = list(seg.components)
                new = []
                changed = False
                for c in comps:
                    if type(c).__name__ == "Selector" and _is_groupclip(c):
                        sc = _first_sourceclip(c["Selected"].value)
                        if sc is not None:
                            rep = f.create.SourceClip(media_kind=sc.media_kind)
                            rep.mob_id = sc.mob_id
                            rep["SourceMobSlotID"].value = getattr(sc, "slot_id", 1)
                            rep["StartTime"].value = getattr(sc, "start", 0) or 0
                            rep.length = c.length
                            _repoint_subclip(rep)
                            new.append(rep)
                            counts["groupclips"] += 1
                            changed = True
                            continue
                    walk(c)
                    new.append(c)
                if changed:
                    seg["Components"].value = new
                return
            if k == "NestedScope":
                for s in seg.slots:
                    walk(s.segment if hasattr(s, "segment") else s)
                return
            if k == "OperationGroup":
                try:
                    for ip in (list(seg.get("InputSegments")) or []):
                        walk(ip)
                except Exception:
                    pass
                return
            if k == "Selector":
                try:
                    sel = seg["Selected"].value
                    if sel is not None:
                        walk(sel)
                except Exception:
                    pass
                return

        try:
            for top in list(f.content.toplevel()):
                for slot in top.slots:
                    walk(slot.segment)
        except Exception as e:
            log.warning("Erro no de-nest da timeline: %s", e)
        log.info("De-nest: %d subclipe(s) achatado(s), %d groupclip(s) committed",
                 counts["subclips"], counts["groupclips"])
        return counts

    def _ensure_aafklv_def(f) -> None:
        from aaf2.auid import AUID
        try:
            f.dictionary.lookup_containerdef("AAFKLV")
            return
        except Exception:
            pass
        klv_auid = AUID("4b464141-000d-4d4f-060e-2b34010101ff")
        existing = f.dictionary["ContainerDefinitions"].get(klv_auid, None)
        if existing is not None:
            existing.name = "ContainerDef_AAFKLV"
        else:
            d = f.create.ContainerDef(klv_auid, "ContainerDef_AAFKLV", "")
            f.dictionary["ContainerDefinitions"].append(d)

    def _build_pcm_descriptor(f, abspath, meta, old_desc):
        from aaf2.auid import AUID
        astream = next((s for s in (meta.get("streams") or [])
                        if s.get("codec_type") == "audio"), {})
        sr = int(float(astream.get("sample_rate") or 48000))
        bits = int(astream.get("bits_per_sample") or astream.get("bits_per_raw_sample") or 0) or 16
        ch = int(astream.get("channels") or 1)
        block = max(1, (bits + 7) // 8) * ch

        def _samples_from_meta():
            for key in ("duration_ts", "nb_samples"):
                v = astream.get(key)
                if v:
                    try: return int(v)
                    except Exception: pass
            dur = astream.get("duration") or (meta.get("format") or {}).get("duration")
            try: return int(round(float(dur) * sr)) if dur else None
            except Exception: return None

        pcm = f.create.PCMDescriptor()
        try: pcm["SampleRate"].value = old_desc["SampleRate"].value
        except Exception: pcm["SampleRate"].value = sr
        try:
            pcm["Length"].value = old_desc["Length"].value
        except Exception:
            n = _samples_from_meta()
            if n is not None:
                pcm["Length"].value = n
        pcm["AudioSamplingRate"].value = sr
        pcm["Channels"].value = ch
        pcm["QuantizationBits"].value = bits
        pcm["BlockAlign"].value = block
        pcm["AverageBPS"].value = block * sr
        try:
            _ensure_aafklv_def(f)
            pcm["ContainerFormat"].value = f.dictionary.lookup_containerdef("AAFKLV")
        except Exception as e:
            log.debug("ContainerFormat MXF no PCM falhou: %s", e)
        try: pcm["MediaContainerGUID"].value = AUID(_AMA_CONTAINER_GUID["wav"])
        except Exception: pass
        for prop, val in (("Locked", False), ("AudioRefLevel", -20),
                          ("SequenceOffset", 0), ("DialNorm", 27), ("DataOffset", 0)):
            try: pcm[prop].value = val
            except Exception: pass
        try: pcm["ElectroSpatial"].value = "ElectroSpatialFormulation_SingleChannelMode"
        except Exception: pass
        loc = f.create.NetworkLocator()
        loc["URLString"].value = path_to_aaf_url(abspath)
        pcm["Locator"].append(loc)
        return pcm

    def _build_wav_essence(f, leaf_mob, abspath, matnum, master):
        import os as _os
        snd = next((s for s in master.slots if s.media_kind == "Sound"), None)
        if snd is None:
            raise RuntimeError(f"master {master.name!r} sem sound slot")
        edit_rate = snd["EditRate"].value
        clip_len = int(snd.segment.length)
        leaf_snd = next((s.slot_id for s in leaf_mob.slots if s.media_kind == "Sound"), 2)

        meta = _ffprobe_meta(abspath)
        pcm = _build_pcm_descriptor(f, abspath, meta, None)

        pcm_mob = f.create.SourceMob()
        pcm_mob.name = _os.path.basename(abspath)
        pcm_mob.descriptor = pcm
        slot = pcm_mob.create_sound_slot(edit_rate)
        slot.slot_id = 2
        sc = leaf_mob.create_source_clip(leaf_snd, start=0, length=clip_len,
                                         media_kind="sound")
        slot.segment["Components"].append(sc)
        slot.segment.length = clip_len
        f.content.mobs.append(pcm_mob)
        pcm_mob.mob_id = _avid_chain_id(leaf_mob.mob_id, matnum, 0xb1)
        log.info("Essência PCM (edit-rate) criada p/ %s: len=%d @ %s",
                 pcm_mob.name, clip_len, edit_rate)
        return pcm_mob

    def _build_ama_source(f, local_path, umid_matnum=None):
        import os as _os
        from aaf2.auid import AUID
        import sys as _sys, io as _io

        abspath = _os.path.abspath(str(local_path))
        ext = _os.path.splitext(abspath)[1].lower()
        is_mxf = ext in _MXF_EXTS

        _patch_pyaaf2_mpeg2()
        if is_mxf:
            _ensure_aafklv_def(f)
            meta = {}
        else:
            meta = _ffprobe_meta(abspath)

        _saved = _sys.stdout
        try:
            _sys.stdout = _io.StringIO()
            result = f.content.create_ama_link(abspath, meta)
        finally:
            _sys.stdout = _saved

        new_master = next((m for m in result if type(m).__name__ == "MasterMob"), None)
        new_source = next((m for m in result if type(m).__name__ == "SourceMob"), None)
        if new_master is not None:
            try:
                f.content['Mobs'].pop(new_master.mob_id)
            except Exception as e:
                log.warning("Não removi o MasterMob extra do AMA-link: %s", e)
        if new_source is None:
            raise RuntimeError(f"create_ama_link não devolveu SourceMob para {abspath}")

        if not is_mxf and umid_matnum:
            gen_sources = [m for m in result if type(m).__name__ == "SourceMob"]
            remap = {}
            for sm in gen_sources:
                dn = type(getattr(sm, "descriptor", None)).__name__
                inst = 0xa1 if dn in ("ImportDescriptor", "TapeDescriptor") else 0xb1
                new_id = _avid_chain_id(sm.mob_id, umid_matnum, inst)
                if str(new_id) != str(sm.mob_id):
                    remap[str(sm.mob_id)] = new_id
                    sm.mob_id = new_id
            if remap:
                for sm in gen_sources:
                    for sc in _iter_sourceclips(sm):
                        if str(sc.mob_id) in remap:
                            sc.mob_id = remap[str(sc.mob_id)]
                log.info("Matnum AMA alinhado ao arquivo (%s): %s",
                         _os.path.basename(abspath), umid_matnum)

        desc = getattr(new_source, "descriptor", None)
        if desc is not None:
            ckey = _ama_container_key(ext)
            guid = _AMA_CONTAINER_GUID.get(ckey)
            if guid:
                try:
                    desc["MediaContainerGUID"].value = AUID(guid)
                except Exception:
                    pass
            hguid = _AMA_HANDLER_GUID.get(ckey)
            if hguid:
                try:
                    desc["ContainerHandlerGUID"].value = AUID(hguid)
                except Exception as e:
                    log.debug("Não setei ContainerHandlerGUID: %s", e)
            avid_url = path_to_aaf_url(abspath)
            try:
                for loc in (desc["Locator"].value or []):
                    loc["URLString"].value = avid_url
            except Exception as e:
                log.debug("Falha ao reescrever locator do AMA source: %s", e)

            if ckey == "quicktime":
                try:
                    subs = list(desc["FileDescriptors"].value or [])
                    vids = [s for s in subs if type(s).__name__ in ("CDCIDescriptor", "RGBADescriptor")]
                    if vids and len(vids) < len(subs):
                        desc["FileDescriptors"].value = vids
                    keep = [sl for sl in new_source.slots if sl.media_kind != "Sound"]
                    if len(keep) < len(list(new_source.slots)):
                        new_source["Slots"].value = keep
                except Exception as e:
                    log.debug("Falha ao tornar QuickTime só-vídeo: %s", e)

            elif ckey == "wav" and type(desc).__name__ == "WAVEDescriptor":
                try:
                    pcm = _build_pcm_descriptor(f, abspath, meta, desc)
                    new_source.descriptor = pcm
                except Exception as e:
                    log.warning("Falha ao converter WAVE→PCMDescriptor (%s): %s",
                                _os.path.basename(abspath), e)
        return new_source

    def _mob_timecode(mob):
        for s in mob.slots:
            stack = [s.segment]
            while stack:
                x = stack.pop()
                if type(x).__name__ == "Timecode":
                    er = None
                    try: er = s["EditRate"].value
                    except Exception: pass
                    return x, er
                stack.extend(getattr(x, "components", []) or [])
        return None, None

    def _source_tc_for_master(f, master):
        mobs = {str(m.mob_id): m for m in f.content.mobs}
        slots = sorted(master.slots, key=lambda s: 0 if s.media_kind == "Picture" else 1)
        for slot in slots:
            sc = _first_sourceclip(slot.segment)
            if sc is None:
                continue
            acc = int(sc["StartTime"].value or 0) if "StartTime" in sc else 0
            cur = mobs.get(str(sc.mob_id))
            seen = {str(master.mob_id)}
            depth = 0
            while cur is not None and depth < 8:
                cid = str(cur.mob_id)
                if cid in seen:
                    break
                seen.add(cid)
                tc, er = _mob_timecode(cur)
                if tc is not None:
                    return (int(tc["Start"].value or 0) + acc,
                            tc["FPS"].value, bool(tc["Drop"].value), er, tc.length)
                nxt = None
                for s in cur.slots:
                    r = _first_sourceclip(s.segment)
                    if r is not None and str(r.mob_id) != cid:
                        acc += int(r["StartTime"].value or 0) if "StartTime" in r else 0
                        nxt = mobs.get(str(r.mob_id))
                        if nxt is not None:
                            break
                cur = nxt
                depth += 1
        return None

    def _grafted_tape_mob(f, new_source):
        mobs = {str(m.mob_id): m for m in f.content.mobs}
        cur = new_source
        seen = set()
        depth = 0
        while cur is not None and depth < 6:
            cid = str(cur.mob_id)
            if cid in seen:
                break
            seen.add(cid)
            dn = type(getattr(cur, "descriptor", None)).__name__
            if dn in ("ImportDescriptor", "TapeDescriptor"):
                return cur
            nxt = None
            for s in cur.slots:
                r = _first_sourceclip(s.segment)
                if r is not None and str(r.mob_id) != cid:
                    nxt = mobs.get(str(r.mob_id))
                    if nxt is not None:
                        break
            cur = nxt
            depth += 1
        return None

    def _graft_ama_essence(f) -> None:
        import os as _os
        cache: dict = {}
        masters = [m for m in f.content.mastermobs()]
        for master in masters:
            local, leaf_id = _master_relink_local(f, master)
            if not local or not _os.path.exists(local):
                continue
            matnum = _material_num(leaf_id) if leaf_id else None
            ext = _os.path.splitext(local)[1].lower()
            is_wav = ext in _WAV_EXTS
            src_tc = None if is_wav else _source_tc_for_master(f, master)
            online_tc = None if is_wav else _probe_online_tc(local)
            try:
                if local not in cache:
                    if is_wav and leaf_id and matnum:
                        leaf_mob = next((m for m in f.content.mobs
                                         if str(m.mob_id) == leaf_id), None)
                        if leaf_mob is None:
                            raise RuntimeError("leaf do WAV não encontrado")
                        cache[local] = _build_wav_essence(f, leaf_mob, local, matnum, master)
                    else:
                        cache[local] = _build_ama_source(f, local, umid_matnum=matnum)
                new_source = cache[local]
            except Exception as e:
                log.warning("Falha ao gerar essência AMA p/ %s: %s",
                            getattr(master, "name", ""), e)
                continue
            pic_slots = [s.slot_id for s in new_source.slots if s.media_kind == "Picture"]
            snd_slots = [s.slot_id for s in new_source.slots if s.media_kind == "Sound"]
            snd_i = 0
            repointed = 0
            for slot in master.slots:
                sc = _first_sourceclip(slot.segment)
                if sc is None:
                    continue
                if slot.media_kind == "Picture" and pic_slots:
                    sc.mob_id = new_source.mob_id
                    sc["SourceMobSlotID"].value = pic_slots[0]
                    repointed += 1
                elif slot.media_kind == "Sound" and snd_slots:
                    sc.mob_id = new_source.mob_id
                    sc["SourceMobSlotID"].value = snd_slots[min(snd_i, len(snd_slots) - 1)]
                    snd_i += 1
                    repointed += 1
            if online_tc is not None or src_tc is not None:
                tape = _grafted_tape_mob(f, new_source)
                if tape is not None and _mob_timecode(tape)[0] is None:
                    off_start = src_tc[0] if src_tc is not None else None
                    on_start = online_tc[0] if online_tc is not None else None
                    on_fps = online_tc[1] if online_tc is not None else None
                    tol = int(on_fps or 30)
                    use_online = (online_tc is not None and
                                  (off_start is None or abs(int(off_start) - int(on_start)) > tol))
                    if use_online:
                        start, fps, drop, er, length, tc_str = online_tc
                        source = "online"
                    else:
                        start, fps, drop, er, length = src_tc
                        tc_str = None
                        source = "offline"
                    er = er or new_source.slots[0]["EditRate"].value
                    try:
                        tcslot = tape.create_timecode_slot(er, int(fps), bool(drop), length)
                        tcslot.segment["Start"].value = int(start)
                        log.info("TC de origem (%s) no tape: %s start=%d fps=%s%s",
                                 source, getattr(master, "name", ""), int(start), fps,
                                 f" tc={tc_str}" if tc_str else "")
                        if use_online and off_start is not None:
                            tc_rebased.append({
                                "mob_name": getattr(master, "name", "") or "",
                                "online_start": int(start),
                                "online_tc": tc_str,
                                "offline_start": int(off_start),
                                "fps": int(fps),
                                "drop": bool(drop),
                            })
                    except Exception as e:
                        log.warning("Falha ao reinserir TC no tape de %s: %s",
                                    getattr(master, "name", ""), e)
            _add_imported_ama(f, master)
            ama_promoted.append({
                "master": getattr(master, "name", "") or "",
                "source_desc": type(new_source.descriptor).__name__,
                "slots_repointed": repointed,
                "local": local,
            })
            log.info("Essência AMA enxertada: master=%s slots=%d arquivo=...%s",
                     getattr(master, "name", ""), repointed, local[-40:])

    if dry_run:
        log.info("[DRY RUN] Inspecionando AAF: %s", src.name)
        with aaf2.open(str(src), "r") as f:
            _process_mobs(f)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(str(src), str(out))
        except OSError as e:
            raise RuntimeError(
                f"Falha ao copiar AAF para '{out}': {e}. "
                "Verifique espaço em disco e permissões."
            ) from e
        log.info("AAF copiado: %s → %s", src.name, out.name)

        try:
            with aaf2.open(str(out), 'rw') as f:
                if denest:
                    denest_result = _denest_timeline(f)
                _process_mobs(f)
                if not dry_run:
                    _rebase_source_tc(f)
                if ama_autolink:
                    _graft_ama_essence(f)
                else:
                    _align_source_ids(f)
                if name_map:
                    names_renamed += _rename_offline_to_match(f, name_map)
                names_cleaned += _clean_mob_names(f)
        except Exception:
            try:
                out.unlink()
                log.warning("Rollback: '%s' removido após falha na escrita.", out.name)
            except OSError:
                pass
            raise

        log.info(
            "AAF relinkado: %d atualizado(s), %d sem mapeamento, %d erro(s), "
            "%d MobID alinhado(s), %d AMA promovido(s), %d nome(s) limpo(s)",
            len(mobs_updated), len(mobs_skipped), error_count,
            len(ids_aligned), len(ama_promoted), names_cleaned,
        )

    return {
        "output_path":   str(out),
        "updated_count": len(mobs_updated),
        "skipped_count": len(mobs_skipped),
        "error_count":   error_count,
        "mobs_updated":  mobs_updated,
        "mobs_skipped":  mobs_skipped,
        "ids_aligned":   ids_aligned,
        "ama_promoted":  ama_promoted,
        "tc_rebased":    tc_rebased,
        "names_cleaned": names_cleaned,
        "names_renamed": names_renamed,
        "denested_subclips":  denest_result.get("subclips", 0),
        "denested_groupclips": denest_result.get("groupclips", 0),
        "dry_run":       dry_run,
    }
