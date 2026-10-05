from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

log = logging.getLogger("relinker.media.matcher")

DEFAULT_OPTIONS: dict = {
    "fuzzy_threshold": 0.70,
    "visual_skip_threshold": 0.90,
    "visual_hamming_threshold": 6,
    "visual_agree_dist": 8,
    "visual_min_agree": 0.75,
    "visual_samples": 4,
    "visual_frame_offset": 5,
    "max_candidates": 5,
    "visual_fallback": True,
    "ref_tc_start": "00:00:00:00",
    "duration_tol_secs": 0.3,
    "duration_tol_rel": 0.01,
    "visual_max_candidates": 8,
    "offset_search": True,
    "offset_search_max_secs": 120,
    "offset_search_probes": 60,
}


@dataclass
class MatchResult:
    segment_index: int
    segment: dict

    matched_path: Optional[str]
    match_method: str
    confidence: float

    candidates: list[dict]
    visual_score: Optional[float] = None

    ambiguous: bool = False
    verified: bool = False

    alternate_matches: list[dict] = field(default_factory=list)

    sequence: Optional[dict] = None

    source_offset_correction: int = 0

    def to_dict(self) -> dict:
        return {
            "segment_index": self.segment_index,
            "matched_path": self.matched_path,
            "match_method": self.match_method,
            "confidence": round(self.confidence, 4),
            "candidates": self.candidates,
            "visual_score": self.visual_score,
            "ambiguous": self.ambiguous,
            "verified": self.verified,
            "alternate_matches": self.alternate_matches,
            "sequence": self.sequence,
            "source_offset_correction": self.source_offset_correction,
        }


_AVID_NAME_SUFFIX_RE = re.compile(r"\.(sync|new|cds|copy|sub|grp|exported)$", re.IGNORECASE)
_MEDIA_EXT_RE = re.compile(
    r"\.(mxf|mov|mp4|m4v|wav|aif|aiff|flac|mts|mp3|mxf|r3d|braw)$", re.IGNORECASE
)
_TRAILING_COUNTER_RE = re.compile(r"\.\d{1,3}$")


def _strip_avid_suffixes(name: str) -> str:
    prev = None
    while name != prev:
        prev = name
        name = _TRAILING_COUNTER_RE.sub("", name)
        name = _AVID_NAME_SUFFIX_RE.sub("", name)
        name = _MEDIA_EXT_RE.sub("", name)
    return name


def _normalize_name(name: str) -> str:
    if not name:
        return ""
    name = _strip_avid_suffixes(name)
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    ascii_name = ascii_name.lower()
    ascii_name = re.sub(r"[\._\-/\\;:]+", " ", ascii_name)
    ascii_name = " ".join(ascii_name.split())
    return ascii_name


def _token_sort_ratio(a: str, b: str) -> float:
    a_sorted = " ".join(sorted(a.split()))
    b_sorted = " ".join(sorted(b.split()))
    return SequenceMatcher(None, a_sorted, b_sorted).ratio()


def _normalize_uid(uid: str) -> str:
    return re.sub(r"[\s\-{}]", "", uid).lower()


def _has_significant_token_overlap(a: str, b: str, min_len: int = 4) -> bool:
    if a == b:
        return True
    ta = {t for t in a.split() if len(t) >= min_len}
    tb = {t for t in b.split() if len(t) >= min_len}
    if ta or tb:
        return bool(ta & tb)
    return bool(set(a.split()) & set(b.split()))


def _candidate_dict(file: dict, score: float, method: str) -> dict:
    return {
        "path": file["path"],
        "filename": file["filename"],
        "score": round(score, 4),
        "method": method,
        "duration_ms": file.get("duration_ms"),
        "frame_rate": file.get("frame_rate"),
        "uid": file.get("uid"),
    }


def _frames_to_tc(frames: int, fps: float) -> str:
    fps_int = round(fps)
    ff = frames % fps_int
    ss = (frames // fps_int) % 60
    mm = (frames // fps_int // 60) % 60
    hh = frames // fps_int // 3600
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def _tc_to_seconds(tc: str, fps: float) -> float:
    parts = tc.replace(";", ":").split(":")
    if len(parts) != 4:
        return 0.0
    hh, mm, ss, ff = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    fps_int = round(fps) or 1
    total_frames = ((hh * 60 + mm) * 60 + ss) * fps_int + ff
    return total_frames / (fps if fps else fps_int)


def _candidate_secs(file: dict) -> Optional[float]:
    dur_ms = file.get("duration_ms")
    try:
        return float(dur_ms) / 1000.0 if dur_ms else None
    except (TypeError, ValueError):
        return None


def _seg_source_secs(seg: dict) -> Optional[float]:
    mr = seg.get("media_ref") or {}
    s = mr.get("source_duration_secs")
    try:
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


def _duration_ok(seg: dict, file: dict, opts: dict) -> Optional[bool]:
    seg_secs  = _seg_source_secs(seg)
    cand_secs = _candidate_secs(file)
    if not seg_secs or not cand_secs:
        return None
    tol = max(opts.get("duration_tol_secs", 0.3),
              opts.get("duration_tol_rel", 0.01) * seg_secs)
    return abs(seg_secs - cand_secs) <= tol


def _score_files_by_name(
    clip_name: str,
    mob_name: str,
    files: list[dict],
    max_candidates: int,
) -> list[tuple[float, dict]]:
    norm_clip = _normalize_name(clip_name)
    norm_mob  = _normalize_name(mob_name)

    clip_is_informative = len(norm_clip.split()) >= 2

    scored: list[tuple[float, dict]] = []

    for f in files:
        stem = Path(f["filename"]).stem
        norm_file = _normalize_name(stem)
        if not norm_file:
            continue

        score_clip = _token_sort_ratio(norm_clip, norm_file) if norm_clip else 0.0
        score_mob  = _token_sort_ratio(norm_mob,  norm_file) if norm_mob  else 0.0

        if clip_is_informative:
            score = max(score_clip, score_mob * 0.8)
        else:
            score = max(score_clip, score_mob)

        if score > 0.0:
            scored.append((score, f))

    scored.sort(key=lambda x: -x[0])
    return scored[:max_candidates]


def _cand_seek_frame(
    source_start_frames: int,
    timeline_off: int,
    duration_frames: int,
    speed_ratio: float,
    motion_effect_type: Optional[str],
    phase_offset: int,
    source_offset_map: Optional[list] = None,
) -> int:
    if source_offset_map:
        return source_start_frames + int(round(_interp_map(source_offset_map, timeline_off)))
    me = motion_effect_type or 'constant'
    if me == 'freeze':
        return source_start_frames + phase_offset
    if me == 'reverse':
        return source_start_frames + max(0, duration_frames - timeline_off)
    sr = speed_ratio if speed_ratio > 0 else 1.0
    return source_start_frames + int(round(timeline_off / sr))


def _interp_map(points: list, x: float) -> float:
    if not points:
        return x
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for i in range(1, len(points)):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        if x <= x1:
            if x1 == x0:
                return y0
            t = (x - x0) / (x1 - x0)
            return y0 + (y1 - y0) * t
    return points[-1][1]


_UNREPRODUCIBLE_IMAGE_CATEGORIES = {"color", "animate", "mask"}

_AVID_POS_UNIT = 1000.0


def _geometric_vf(effects: Optional[list]) -> Optional[str]:
    for e in (effects or []):
        if e.get("category") != "geometric" or not e.get("reproducible"):
            continue
        p = e.get("params") or {}
        sx = p.get("scale_x") or p.get("scale_y") or 1.0
        sy = p.get("scale_y") or sx
        px = float(p.get("pos_x", 0.0) or 0.0)
        py = float(p.get("pos_y", 0.0) or 0.0)
        if abs(sx - 1.0) < 1e-3 and abs(sy - 1.0) < 1e-3 and abs(px) < 1 and abs(py) < 1:
            return None
        if sx < 1.0 or sy < 1.0:
            return None
        pxf = px / _AVID_POS_UNIT
        pyf = py / _AVID_POS_UNIT
        cw = f"iw/{sx}"
        ch = f"ih/{sy}"
        x = f"(iw-{cw})/2-({pxf})*({cw})"
        y = f"(ih-{ch})/2+({pyf})*({ch})"
        return f"scale=iw*{sx}:ih*{sy},crop={cw}:{ch}:{x}:{y}"
    return None


def _try_visual_match(
    timeline_tc_in: str,
    source_start_frames: int,
    source_fps: float,
    candidate_path: str,
    ref_video: str,
    ref_tc_start: str,
    frame_offset: int,
    hamming_threshold: int,
    duration_frames: int = 0,
    n_samples: int = 4,
    agree_dist: int = 8,
    min_agree: float = 0.75,
    speed_ratio: float = 1.0,
    motion_effect_type: Optional[str] = None,
    phase_offset: int = 0,
    source_offset_map: Optional[list] = None,
    effects: Optional[list] = None,
    cand_fps: Optional[float] = None,
) -> Optional[float]:
    from frames.extractor import extract_frame
    from frames.comparator import compare_frames

    for e in (effects or []):
        if e.get("category") in _UNREPRODUCIBLE_IMAGE_CATEGORIES:
            log.debug("Visual pulado: efeito não-reproduzível '%s'", e.get("name"))
            return None

    cand_vf = _geometric_vf(effects)

    fps = source_fps
    cfps = cand_fps or fps
    ref_offset_secs = _tc_to_seconds(ref_tc_start, fps)
    base_secs = _tc_to_seconds(timeline_tc_in, fps) - ref_offset_secs

    dur = max(1, int(duration_frames or 0))
    if dur <= 2 or n_samples <= 1:
        offsets = [max(0, frame_offset)]
    else:
        fracs = [(i + 1) / (n_samples + 1) for i in range(n_samples)]
        offsets = sorted({max(1, min(dur - 1, int(round(dur * f)))) for f in fracs})

    distances: list[int] = []
    for off in offsets:
        ref_secs = base_secs + off / fps
        if ref_secs < 0:
            ref_secs = 0.0
        ref_tc = _frames_to_tc(round(ref_secs * fps), fps)
        cand_frames = _cand_seek_frame(
            source_start_frames, off, dur, speed_ratio, motion_effect_type, phase_offset,
            source_offset_map,
        )
        cand_tc = _frames_to_tc(max(0, cand_frames), cfps)

        ref_fp = cand_fp = None
        try:
            ref_fp = extract_frame(ref_video, ref_tc, fps, accurate=False)["frame_path"]
            cand_fp = extract_frame(candidate_path, cand_tc, cfps, accurate=False, vf=cand_vf)["frame_path"]
            cmp = compare_frames(ref_fp, cand_fp, hamming_threshold)
            if cmp.get("low_detail"):
                log.debug("Visual sample descartada (baixo detalhe) @ off=%d", off)
            else:
                distances.append(int(cmp["hamming_distance"]))
        except Exception as e:
            log.debug("Visual sample falhou (%s @ off=%d): %s", Path(candidate_path).name, off, e)
        finally:
            for p in (ref_fp, cand_fp):
                if p:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

    if not distances:
        return None

    mean_d = sum(distances) / len(distances)
    agree = sum(1 for d in distances if d <= agree_dist) / len(distances)
    score = 1.0 - mean_d / 64.0

    min_valid = 3 if n_samples >= 4 else 1
    if agree < min_agree or len(distances) < min_valid:
        score = min(score, 0.5)

    log.info(
        "Visual %s: dists=%s mean=%.1f agree=%.0f%% score=%.3f",
        Path(candidate_path).name, distances, mean_d, agree * 100, score,
    )
    return float(score)


def _visual_score_at(seg: dict, cand_file: dict, ref_video: str, opts: dict,
                     extra_off: int = 0) -> Optional[float]:
    return _try_visual_match(
        timeline_tc_in=seg.get("timeline_tc_in", "00:00:00:00"),
        source_start_frames=(seg.get("source_start_frames", 0) or 0) + extra_off,
        source_fps=seg.get("source_fps", 25.0),
        candidate_path=cand_file["path"],
        ref_video=ref_video,
        ref_tc_start=opts["ref_tc_start"],
        frame_offset=opts["visual_frame_offset"],
        hamming_threshold=opts["visual_hamming_threshold"],
        duration_frames=seg.get("duration_frames", 0) or 0,
        n_samples=opts["visual_samples"],
        agree_dist=opts["visual_agree_dist"],
        min_agree=opts["visual_min_agree"],
        speed_ratio=seg.get("speed_ratio", 1.0) or 1.0,
        motion_effect_type=seg.get("motion_effect_type"),
        phase_offset=seg.get("phase_offset", 0) or 0,
        source_offset_map=seg.get("source_offset_map") or [],
        effects=seg.get("effects") or [],
        cand_fps=(seg.get("media_ref") or {}).get("source_fps"),
    )


def _visual_score_for(seg: dict, cand_file: dict, ref_video: str, opts: dict) -> Optional[float]:
    return _visual_score_at(seg, cand_file, ref_video, opts, 0)


def _coarse_align_offset(seg: dict, cand_file: dict, ref_video: str, opts: dict) -> Optional[int]:
    from frames.extractor import extract_frame
    from frames.comparator import compare_frames
    fps = seg.get("source_fps") or 25.0
    dur = max(1, int(seg.get("duration_frames") or 0))
    src_start = int(seg.get("source_start_frames") or 0)
    cand_dur_f = int(round((_candidate_secs(cand_file) or 0) * fps))
    mid = dur // 2

    ref_offset_secs = _tc_to_seconds(opts["ref_tc_start"], fps)
    base_secs = _tc_to_seconds(seg.get("timeline_tc_in", "00:00:00:00"), fps) - ref_offset_secs
    ref_secs = max(0.0, base_secs + mid / fps)
    ref_tc = _frames_to_tc(round(ref_secs * fps), fps)
    cand_vf = _geometric_vf(seg.get("effects"))

    cap = int(opts.get("offset_search_max_secs", 120) * fps)
    window = min(cap, cand_dur_f) if cand_dur_f else cap
    n_probes = max(8, int(opts.get("offset_search_probes", 60)))
    step = max(1, window // n_probes)

    ref_fp = None
    best_off, best_d = None, 65
    try:
        ref_fp = extract_frame(ref_video, ref_tc, fps, accurate=False)["frame_path"]
        for off in range(-window, window + 1, step):
            cf = src_start + mid + off
            if cf < 0 or (cand_dur_f and cf >= cand_dur_f):
                continue
            cand_fp = None
            try:
                cand_fp = extract_frame(cand_file["path"], _frames_to_tc(cf, fps), fps,
                                        accurate=False, vf=cand_vf)["frame_path"]
                cmp = compare_frames(ref_fp, cand_fp, opts["visual_hamming_threshold"])
                d = 65 if cmp.get("low_detail") else int(cmp["hamming_distance"])
                if d < best_d:
                    best_d, best_off = d, off
            except Exception:
                pass
            finally:
                if cand_fp:
                    try: os.unlink(cand_fp)
                    except OSError: pass
    except Exception as e:
        log.debug("Busca grossa de offset falhou (%s): %s", Path(cand_file["path"]).name, e)
        return None
    finally:
        if ref_fp:
            try: os.unlink(ref_fp)
            except OSError: pass
    return best_off


def _search_visual_offset(seg: dict, cand_file: dict, ref_video: str,
                          opts: dict) -> tuple[Optional[float], int]:
    base = _visual_score_at(seg, cand_file, ref_video, opts, 0)
    good = 1.0 - opts["visual_hamming_threshold"] / 64.0
    if not opts.get("offset_search", True):
        return base, 0
    if base is not None and base >= good:
        return base, 0

    coarse = _coarse_align_offset(seg, cand_file, ref_video, opts)
    if not coarse:
        return base, 0

    fps = seg.get("source_fps") or 25.0
    fine = max(1, int(round(0.2 * fps)))
    best_sc = base if base is not None else -1.0
    best_off = 0
    for off in sorted({coarse + d * fine for d in (-2, -1, 0, 1, 2)}):
        sc = _visual_score_at(seg, cand_file, ref_video, opts, off)
        if sc is not None and sc > best_sc:
            best_sc, best_off = sc, off
    return best_sc, best_off


def compare_candidates(seg: dict, candidates: list[dict], ref_video: Optional[str],
                       options: Optional[dict] = None, align: bool = False) -> dict:
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    fps = seg.get("source_fps") or 25.0
    dur = max(1, int(seg.get("duration_frames") or 0))
    disp_off = 0
    ref_offset_secs = _tc_to_seconds(opts["ref_tc_start"], fps)
    base_secs = _tc_to_seconds(seg.get("timeline_tc_in", "00:00:00:00"), fps) - ref_offset_secs
    ref_tc = _frames_to_tc(max(0, round((base_secs + disp_off / fps) * fps)), fps)
    src_start = int(seg.get("source_start_frames") or 0)
    src_disp = _cand_seek_frame(
        src_start, disp_off, dur, seg.get("speed_ratio", 1.0) or 1.0,
        seg.get("motion_effect_type"), seg.get("phase_offset", 0) or 0,
        seg.get("source_offset_map") or [])

    src_fps = (seg.get("media_ref") or {}).get("source_fps") or fps

    out: list[dict] = []
    for f in candidates:
        if ref_video and align:
            score, off = _search_visual_offset(seg, f, ref_video, opts)
        else:
            score, off = None, 0
        cfps = src_fps
        cand_frame = max(0, src_disp + off)
        out.append({
            "path": f.get("path"),
            "filename": f.get("filename") or (f.get("path") or "").split("/")[-1],
            "offset": off,
            "offset_secs": round(off / fps, 3),
            "score": round(score, 4) if score is not None else None,
            "cand_tc": _frames_to_tc(cand_frame, cfps),
        })
    out.sort(key=lambda c: -(c["score"] if c["score"] is not None else -1.0))
    return {"ref_tc": ref_tc, "ref_fps": fps, "disp_offset": disp_off, "candidates": out}


def _visual_tiebreak(seg: dict, cands: list[dict], ref_video: str, opts: dict):
    import control
    scored: list[tuple[float, dict]] = []
    for f in cands:
        control.check()
        v = _visual_score_for(seg, f, ref_video, opts)
        if v is not None:
            scored.append((v, f))
    if not scored:
        return None, None
    scored.sort(key=lambda x: -x[0])
    best_v, best_f = scored[0]
    second = scored[1][0] if len(scored) > 1 else -1.0
    hamming_match_score = 1.0 - opts["visual_hamming_threshold"] / 64
    if best_v >= hamming_match_score and (best_v - second) >= 0.06:
        return best_f, best_v
    return None, None


def _match_alternate(
    alt: dict,
    files: list[dict],
    opts: dict,
    seg_idx: int,
) -> dict:
    mob_name     = alt.get("mob_name", "")
    local_path   = alt.get("local_path", "")
    original_url = alt.get("url", "")

    base = {"mob_name": mob_name, "original_url": original_url}

    if local_path:
        for f in files:
            if f["path"] == local_path:
                return {**base, "matched_path": f["path"], "method": "path", "confidence": 1.0}

    alt_seg = {"media_ref": {"source_duration_secs": alt.get("source_duration_secs")}}
    scored = _score_files_by_name(mob_name, mob_name, files, opts["max_candidates"])
    if scored:
        best_score, best_file = scored[0]
        dur_ok = _duration_ok(alt_seg, best_file, opts)
        if best_score == 1.0 and dur_ok is not False:
            return {**base, "matched_path": best_file["path"], "method": "exact_name", "confidence": 1.0}
        if best_score >= opts["fuzzy_threshold"] and dur_ok is not False:
            return {**base, "matched_path": best_file["path"], "method": "fuzzy_name", "confidence": round(best_score, 4)}

    return {**base, "matched_path": None, "method": "unresolved", "confidence": 0.0}


def _match_one(
    idx: int,
    seg: dict,
    files: list[dict],
    ref_video: Optional[str],
    opts: dict,
) -> MatchResult:
    media_ref = seg.get("media_ref", {}) or {}
    clip_name   = seg.get("clip_name", "")
    mob_id      = seg.get("mob_id", "")
    mob_name    = media_ref.get("mob_name", "")
    local_path  = media_ref.get("local_path", "")
    alts        = seg.get("selector_alternates", [])

    candidates: list[dict] = []

    if local_path:
        for f in files:
            if f["path"] == local_path:
                log.info("[%d] Path match: %s", idx, f["filename"])
                alt_matches = [_match_alternate(a, files, opts, idx) for a in alts]
                return MatchResult(
                    idx, seg,
                    matched_path=f["path"],
                    match_method="path",
                    confidence=1.0,
                    candidates=[_candidate_dict(f, 1.0, "path")],
                    alternate_matches=alt_matches,
                )

    if mob_id:
        norm_mob_id = _normalize_uid(mob_id)
        for f in files:
            file_uid = f.get("uid") or ""
            if file_uid and _normalize_uid(file_uid) == norm_mob_id:
                log.info("[%d] UID match: %s", idx, f["filename"])
                alt_matches = [_match_alternate(a, files, opts, idx) for a in alts]
                return MatchResult(
                    idx, seg,
                    matched_path=f["path"],
                    match_method="uid",
                    confidence=0.95,
                    candidates=[_candidate_dict(f, 0.95, "uid")],
                    alternate_matches=alt_matches,
                )

    log.debug("[%d] Nome matching — clip='%s' mob='%s'", idx, clip_name, mob_name)
    scored = _score_files_by_name(clip_name, mob_name, files, opts["max_candidates"])
    candidates = [_candidate_dict(f, s, "fuzzy_name") for s, f in scored]

    for s, f in scored[:3]:
        log.debug("[%d]   candidato: '%s' score=%.3f", idx, f["filename"], s)

    best_score = scored[0][0] if scored else 0.0
    best_file  = scored[0][1] if scored else None

    EPS = 1e-9
    top = [f for s, f in scored if s >= best_score - EPS] if scored else []

    name_strong = False
    method_name = "fuzzy_name"
    if best_score == 1.0:
        norm_clip_check = _normalize_name(clip_name)
        top = [f for f in top
               if _has_significant_token_overlap(
                   norm_clip_check, _normalize_name(Path(f["filename"]).stem))]
        if top:
            name_strong, method_name = True, "exact_name"
    elif best_score >= opts["visual_skip_threshold"]:
        name_strong, method_name = True, "fuzzy_name"

    if name_strong and top:
        conf = 1.0 if method_name == "exact_name" else best_score
        viable = [f for f in top if _duration_ok(seg, f, opts) is not False]

        if len(viable) == 1:
            chosen = viable[0]
            log.info("[%d] %s (duração ok): %s", idx, method_name, chosen["filename"])
            alt_matches = [_match_alternate(a, files, opts, idx) for a in alts]
            return MatchResult(idx, seg, matched_path=chosen["path"], match_method=method_name,
                               confidence=conf, candidates=candidates, alternate_matches=alt_matches)

        if len(viable) >= 2:
            if ref_video and not seg.get("is_audio"):
                chosen, vscore = _visual_tiebreak(seg, viable, ref_video, opts)
                if chosen is not None:
                    log.info("[%d] Desempate visual (%.3f): %s", idx, vscore, chosen["filename"])
                    alt_matches = [_match_alternate(a, files, opts, idx) for a in alts]
                    return MatchResult(idx, seg, matched_path=chosen["path"], match_method=method_name,
                                       confidence=conf, candidates=candidates,
                                       visual_score=round(vscore, 4), verified=True,
                                       alternate_matches=alt_matches)
            log.warning("[%d] Ambíguo: %d candidatos empatam por nome+duração — revisão.",
                        idx, len(viable))
            alt_matches = [_match_alternate(a, files, opts, idx) for a in alts]
            return MatchResult(idx, seg, matched_path=viable[0]["path"], match_method=method_name,
                               confidence=conf, candidates=candidates, ambiguous=True,
                               alternate_matches=alt_matches)

    if (ref_video and opts["visual_fallback"] and best_score < opts["fuzzy_threshold"]
            and not seg.get("is_audio")):
        name_score = {f["path"]: s for s, f in scored}
        eligible = [f for f in files if _duration_ok(seg, f, opts) is not False]
        eligible.sort(key=lambda f: -name_score.get(f["path"], 0.0))
        n_excluded = len(files) - len(eligible)
        if n_excluded:
            log.debug("[%d] Portão de duração excluiu %d candidato(s) do visual", idx, n_excluded)
        visual_top = [(name_score.get(f["path"], 0.0), f)
                      for f in eligible[:opts["visual_max_candidates"]]]

        import control
        best_visual_score = -1.0
        best_visual_file  = None
        best_visual_off   = 0

        for _, cand_file in visual_top:
            control.check()
            vscore, voff = _search_visual_offset(seg, cand_file, ref_video, opts)
            if vscore is not None and vscore > best_visual_score:
                best_visual_score = vscore
                best_visual_file  = cand_file
                best_visual_off   = voff

        hamming_match_score = 1.0 - opts["visual_hamming_threshold"] / 64
        if best_visual_file and best_visual_score >= hamming_match_score:
            log.info("[%d] Visual match (%.3f, off=%+df): %s",
                     idx, best_visual_score, best_visual_off, best_visual_file["filename"])
            alt_matches = [_match_alternate(a, files, opts, idx) for a in alts]
            return MatchResult(
                idx, seg,
                matched_path=best_visual_file["path"],
                match_method="visual",
                confidence=best_visual_score,
                candidates=candidates,
                visual_score=round(best_visual_score, 4),
                alternate_matches=alt_matches,
                source_offset_correction=best_visual_off,
            )

    if best_score >= opts["fuzzy_threshold"] and best_file and _duration_ok(seg, best_file, opts) is not False:
        log.info("[%d] Fuzzy name (baixa confiança %.2f): %s", idx, best_score, best_file["filename"])
        alt_matches = [_match_alternate(a, files, opts, idx) for a in alts]
        return MatchResult(
            idx, seg,
            matched_path=best_file["path"],
            match_method="fuzzy_name",
            confidence=best_score,
            candidates=candidates,
            alternate_matches=alt_matches,
        )

    log.warning("[%d] Não resolvido: clip='%s' mob='%s'", idx, clip_name, mob_name)
    return MatchResult(
        idx, seg,
        matched_path=None,
        match_method="unresolved",
        confidence=0.0,
        candidates=candidates,
        alternate_matches=[],
    )


def match_segments(
    segments: list[dict],
    index: dict,
    ref_video: Optional[str] = None,
    options: Optional[dict] = None,
    only_indices: Optional[list] = None,
) -> dict:
    _bench_t = time.perf_counter()
    opts  = {**DEFAULT_OPTIONS, **(options or {})}
    files = index.get("files", [])

    if not files:
        log.warning("Índice de arquivos vazio — matching não é possível.")

    if ref_video and not Path(ref_video).exists():
        log.warning("Vídeo de referência não encontrado: %s — visual match desativado.", ref_video)
        ref_video = None

    results: list[dict] = []
    skipped = 0
    target = set(only_indices) if only_indices is not None else None

    import control
    for i, seg in enumerate(segments):
        if target is not None and i not in target:
            continue
        control.check()
        if seg.get("is_gap") or seg.get("is_transition") or seg.get("is_effect"):
            skipped += 1
            continue

        if not seg.get("media_ref") and not (seg.get("clip_name") or "").strip():
            results.append(
                MatchResult(i, seg, None, "unresolved", 0.0, []).to_dict()
            )
            continue

        result = _match_one(i, seg, files, ref_video, opts)
        results.append(result.to_dict())

    _RANK = {"path": 5, "uid": 4, "exact_name": 3, "manual": 3, "fuzzy_name": 2, "visual": 1}
    seg_by_idx = {i: s for i, s in enumerate(segments)}

    def _url_of(res: dict) -> str:
        seg = seg_by_idx.get(res.get("segment_index"), {})
        return ((seg.get("media_ref") or {}).get("url")) or ""

    best_by_url: dict[str, dict] = {}
    for res in results:
        if not res.get("matched_path"):
            continue
        url = _url_of(res)
        if not url:
            continue
        rank = _RANK.get(res.get("match_method", ""), 0)
        cur = best_by_url.get(url)
        if cur is None or rank > _RANK.get(cur.get("match_method", ""), 0):
            best_by_url[url] = res

    for res in results:
        url = _url_of(res)
        donor = best_by_url.get(url)
        if not donor or donor is res:
            continue
        cur_rank = _RANK.get(res.get("match_method", ""), 0)
        if _RANK.get(donor.get("match_method", ""), 0) > cur_rank:
            res["matched_path"]  = donor["matched_path"]
            res["match_method"]  = donor["match_method"]
            res["confidence"]    = donor["confidence"]
            res["propagated_from_url"] = True

    SOLID_RANK = 3
    best_path: dict[str, tuple] = {}
    for res in results:
        p = res.get("matched_path")
        if not p:
            continue
        rank = _RANK.get(res.get("match_method", ""), 0)
        url  = _url_of(res)
        cur  = best_path.get(p)
        if cur is None or rank > cur[0]:
            best_path[p] = (rank, url)
    for res in results:
        p = res.get("matched_path")
        if not p:
            continue
        best_rank, best_url = best_path[p]
        my_rank = _RANK.get(res.get("match_method", ""), 0)
        if best_rank >= SOLID_RANK and _url_of(res) != best_url and my_rank < best_rank:
            res["matched_path"] = None
            res["match_method"] = "unresolved"
            res["confidence"]   = 0.0
            res["suppressed_dup"] = True
            log.info("[%s] Suprimido (arquivo já é match sólido de outra origem): %s",
                     res.get("segment_index"), Path(p).name)

    path_to_seq = {f["path"]: f["sequence"] for f in files if f.get("sequence")}
    for res in results:
        res["sequence"] = path_to_seq.get(res.get("matched_path"))

    stats: dict = {
        "total": len(results),
        "skipped": skipped,
        "matched_path": 0,
        "matched_uid": 0,
        "matched_exact_name": 0,
        "matched_fuzzy_name": 0,
        "matched_visual": 0,
        "unresolved": 0,
    }
    for res in results:
        method_key = f"matched_{res.get('match_method')}"
        if method_key in stats:
            stats[method_key] += 1
        else:
            stats["unresolved"] += 1

    log.info(
        "Matching concluído: total=%d | path=%d uid=%d exact=%d fuzzy=%d visual=%d unresolved=%d",
        stats["total"],
        stats["matched_path"],
        stats["matched_uid"],
        stats["matched_exact_name"],
        stats["matched_fuzzy_name"],
        stats["matched_visual"],
        stats["unresolved"],
    )
    import bench
    bench.record("match", (time.perf_counter() - _bench_t) * 1000.0,
                 total=stats["total"], visual=stats["matched_visual"],
                 unresolved=stats["unresolved"], files=len(files),
                 has_ref=bool(ref_video))
    return {"results": results, "stats": stats}
