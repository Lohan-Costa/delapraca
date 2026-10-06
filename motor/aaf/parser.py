from __future__ import annotations
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, unquote

import aaf2

from .markers import read_markers
from .speedcurve import extract_speed_keyframes

log = logging.getLogger("relinker.aaf.parser")


@dataclass
class MediaRef:
    mob_name: str
    url: str
    local_path: str
    descriptor_type: str
    source_duration_frames: Optional[int] = None
    source_duration_secs: Optional[float] = None
    source_fps: Optional[float] = None


@dataclass
class Segment:
    timeline_tc_in: str
    timeline_tc_out: str
    duration_frames: int

    source_start_frames: int
    source_fps: float

    clip_name: str
    track: int
    mob_id: Optional[str] = None
    source_slot_id: Optional[int] = None

    media_ref: Optional[MediaRef] = None

    is_gap: bool = False
    is_transition: bool = False
    is_effect: bool = False
    is_audio: bool = False
    cutpoint: int = 0
    has_motion_effect: bool = False
    motion_effect_type: Optional[str] = None
    speed_ratio: float = 1.0
    phase_offset: int = 0
    motion_class: str = "native"
    conform_ratio: float = 1.0
    relative_speed: float = 1.0
    source_offset_map: list = field(default_factory=list)
    speed_curve: list = field(default_factory=list)
    speed_keyframes: list = field(default_factory=list)
    effects: list = field(default_factory=list)
    has_reframe: bool = False
    reframe_mode: Optional[str] = None
    has_graphic_overlay: bool = False
    is_never_visible: bool = False
    disabled: bool = False

    visible_tc_in: Optional[str] = None
    visible_tc_out: Optional[str] = None

    is_group_clip: bool = False
    group_clip_name: Optional[str] = None

    selector_alternates: list[MediaRef] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timeline_tc_in": self.timeline_tc_in,
            "timeline_tc_out": self.timeline_tc_out,
            "duration_frames": self.duration_frames,
            "source_start_frames": self.source_start_frames,
            "source_fps": self.source_fps,
            "clip_name": self.clip_name,
            "track": self.track,
            "mob_id": self.mob_id,
            "source_slot_id": self.source_slot_id,
            "media_ref": {
                "mob_name": self.media_ref.mob_name,
                "url": self.media_ref.url,
                "local_path": self.media_ref.local_path,
                "descriptor_type": self.media_ref.descriptor_type,
                "source_duration_frames": self.media_ref.source_duration_frames,
                "source_duration_secs": self.media_ref.source_duration_secs,
                "source_fps": self.media_ref.source_fps,
            } if self.media_ref else None,
            "is_gap": self.is_gap,
            "is_transition": self.is_transition,
            "is_effect": self.is_effect,
            "is_audio": self.is_audio,
            "cutpoint": self.cutpoint,
            "has_motion_effect": self.has_motion_effect,
            "motion_effect_type": self.motion_effect_type,
            "speed_ratio": self.speed_ratio,
            "phase_offset": self.phase_offset,
            "motion_class": self.motion_class,
            "conform_ratio": self.conform_ratio,
            "relative_speed": self.relative_speed,
            "source_offset_map": self.source_offset_map,
            "speed_curve": self.speed_curve,
            "speed_keyframes": self.speed_keyframes,
            "effects": self.effects,
            "has_reframe": self.has_reframe,
            "reframe_mode": self.reframe_mode,
            "has_graphic_overlay": self.has_graphic_overlay,
            "is_never_visible": self.is_never_visible,
            "disabled": self.disabled,
            "visible_tc_in": self.visible_tc_in,
            "visible_tc_out": self.visible_tc_out,
            "is_group_clip": self.is_group_clip,
            "group_clip_name": self.group_clip_name,
            "selector_alternates": [
                {
                    "mob_name": a.mob_name,
                    "url": a.url,
                    "local_path": a.local_path,
                    "descriptor_type": a.descriptor_type,
                    "source_duration_frames": a.source_duration_frames,
                    "source_duration_secs": a.source_duration_secs,
                }
                for a in self.selector_alternates
            ],
        }


def _clip_disabled(comp) -> bool:
    try:
        attrs = comp['ComponentAttributeList'].value
    except Exception:
        return False
    for tv in (attrs or []):
        try:
            if getattr(tv, 'name', '') == '_DISABLE_CLIP_FLAG' and int(tv.value) != 0:
                return True
        except Exception:
            continue
    return False


def _param_def_name(param) -> str:
    pd = getattr(param, 'parameterdef', None)
    return (getattr(pd, 'name', '') if pd else '') or getattr(param, 'name', '') or ''


def _parse_avup_text(blob) -> Optional[str]:
    try:
        b = bytes(blob)
    except Exception:
        return None
    off = b.find(b'PUVA')
    if off < 0 or len(b) - off < 40:
        return None
    p = b[off:]
    try:
        size2 = int.from_bytes(p[36:40], "little")
        data = p[40:40 + size2]
        return data.split(b"\x00", 1)[0].decode("utf-8", "replace") or None
    except Exception:
        return None


def _avup_texto_inteiro(blob: bytes) -> Optional[str]:
    off = blob.find(b'PUVA')
    if off < 0 or len(blob) - off < 40:
        return None
    p = blob[off:]
    size2 = int.from_bytes(p[36:40], "little")
    return p[40:40 + size2].rstrip(b"\x00").decode("utf-8", "replace") or None


def _param_raw_bytes(param):
    try:
        d = param['Value'].data
        return bytes(d) if d is not None else None
    except Exception:
        return None


def _title_ttext(op_group) -> Optional[str]:
    try:
        for a in op_group['ComponentAttributeList'].value:
            if getattr(a, 'name', '') == '_TTEXT':
                s = bytes(a.value).decode('utf-8', 'replace').strip('\x00')
                if s.startswith('T+: '):
                    s = s[4:]
                return s.strip() or None
    except Exception:
        pass
    return None


def _extract_title_params(op_group) -> dict:
    out: dict = {}
    texts: list[str] = []
    mqp_hex = None
    try:
        params = list(op_group.get('Parameters') or [])
    except Exception:
        params = []
    for p in params:
        nm = _param_def_name(p)
        if nm == 'Text Box Text':
            t = _parse_avup_text(_param_raw_bytes(p))
            if t and t not in texts:
                texts.append(t)
        elif nm == 'MQP Blob':
            rb = _param_raw_bytes(p)
            if rb:
                mqp_hex = rb.hex()
    if not texts:
        tt = _title_ttext(op_group)
        if tt:
            texts.append(tt)
    if texts:
        out["text"] = "\n".join(texts)
        out["texts"] = texts
    if mqp_hex:
        out["mqp_hex"] = mqp_hex
    return out


def _read_pointlist(param) -> list:
    pts = []
    try:
        for cp in param['PointList'].value:
            try:
                pts.append((float(cp.time), float(cp.value)))
            except Exception:
                continue
    except Exception:
        pass
    return pts


def _normalize_offset_map(pts: list, op_length: int) -> list:
    if not pts:
        return []
    max_x = max(abs(t) for t, _ in pts)
    normalized = max_x <= 1.0 + 1e-6
    out = []
    for t, v in pts:
        tlf = t * op_length if normalized else t
        out.append((round(tlf, 3), round(v, 3)))
    out.sort(key=lambda p: p[0])
    return out


def _parse_motion_effect_params(op_group, op_length: int = 0) -> tuple:
    speed_ratio = 1.0
    is_reverse = False
    phase_offset = 0
    is_varying_speed = False
    offset_map: list = []
    speed_curve: list = []
    speed_keyframes: list = []

    try:
        params = list(op_group.get('Parameters') or [])
    except Exception:
        return (speed_ratio, is_reverse, phase_offset, 'constant',
                offset_map, speed_curve, speed_keyframes)

    for param in params:
        try:
            pname = _param_def_name(param)
            ptype = type(param).__name__

            if pname == 'SpeedRatio' or 'SpeedRatio' in pname:
                is_varying_speed = (ptype == 'VaryingValue')
                try:
                    raw = param.value_at(0)
                    speed_ratio = float(raw) if raw is not None else 1.0
                except Exception:
                    pass

            elif 'SPEED_OFFSET_MAP' in pname or pname == 'PARAM_OFFSET_MAP_U' or 'OFFSET_MAP' in pname:
                pts = _read_pointlist(param)
                if pts and (not offset_map or 'SPEED_OFFSET_MAP' in pname):
                    offset_map = _normalize_offset_map(pts, op_length)

            elif 'SPEED_MAP' in pname:
                speed_curve = _read_pointlist(param)
                try:
                    speed_keyframes = extract_speed_keyframes(param)
                except Exception as e:
                    log.debug("extract_speed_keyframes falhou: %s", e)

            elif 'SMPTEReverse' in pname:
                try:
                    is_reverse = bool(param.value_at(0))
                except Exception:
                    pass

            elif 'PhaseOffset' in pname or pname == 'AvidPhase':
                try:
                    phase_offset = int(param.value_at(0) or 0)
                except Exception:
                    pass

        except Exception as e:
            log.debug("MotionEffect param error: %s", e)

    curve_varies = len({round(v, 4) for _, v in speed_curve}) > 1 if speed_curve else False

    if is_reverse or speed_ratio < 0:
        me_type = 'reverse'
        speed_ratio = abs(speed_ratio)
    elif speed_ratio == 0.0:
        me_type = 'freeze'
    elif curve_varies or is_varying_speed:
        me_type = 'ramp'
    else:
        me_type = 'constant'

    return (speed_ratio, is_reverse, phase_offset, me_type,
            offset_map, speed_curve, speed_keyframes)


_RATE_TOL = 0.012


def _classify_motion(speed_ratio: float, motion_type: Optional[str],
                     native_fps: Optional[float],
                     timeline_fps: Optional[float]) -> tuple[str, float, float]:
    sr = float(speed_ratio) if speed_ratio else 1.0
    tl = float(timeline_fps) if timeline_fps else 0.0
    nat = float(native_fps) if native_fps else 0.0
    conform_ratio = (tl / nat) if (tl > 0 and nat > 0) else 1.0
    relative_speed = (sr / conform_ratio) if conform_ratio else sr

    if motion_type in ("ramp", "freeze", "reverse"):
        return motion_type, conform_ratio, relative_speed

    fps_differ = abs(conform_ratio - 1.0) > _RATE_TOL
    rel_is_one = abs(relative_speed - 1.0) <= _RATE_TOL

    if not fps_differ:
        return ("native" if rel_is_one else "creative"), conform_ratio, relative_speed
    return ("conform" if rel_is_one else "conform+creative"), conform_ratio, relative_speed


_EFFECT_CATEGORY_RULES = [
    (('Audio Gain',), 'gain', True),
    (('Titler', 'Title Tool', 'Avid Title'), 'title', True),
    (('Motion Control', 'Timewarp', 'Motion Effect', 'MotionEffect'), 'speed', False),
    (('SBlend', '3DWarp', '3D Warp', 'S3D', 'Resize', 'PaintResize', 'Reformat',
      'Picture in Picture', 'Picture-In-Picture', 'PIP', 'Pan', 'Scan', 'PanScan',
      'Spin', 'Squeeze', 'Xpress 3D', 'Region Stabilize', 'FrameFlex',
      'SpatialAdapter', 'Blanking'),
     'geometric', True),
    (('Wipe', 'Dissolve', 'Fade', 'Conceal', 'Push', 'Sawtooth', 'Matrix', 'Plasma',
      'Dip', 'Edge Wipe'), 'transition', False),
    (('Color', 'CC ', 'Color Correction', 'Curves', 'Levels', 'HSL', 'RGB',
      'Saturation', 'Safe Color'), 'color', False),
    (('Paint', 'Animatte', 'Anim', 'Intraframe', 'Scratch'), 'animate', False),
    (('Mask', 'Matte', 'SpectraMatte', 'Key', 'Luma', 'Chroma'), 'mask', False),
]


def _categorize_effect(op_name: str) -> tuple[str, bool]:
    for needles, category, reproducible in _EFFECT_CATEGORY_RULES:
        if any(n.lower() in op_name.lower() for n in needles):
            return category, reproducible
    return 'other', False


_FRIENDLY_BY_EFFECT_ID = {
    'EFF2_SBLEND': '3D Warp',
    'EFF2_BLEND_WIPE': 'SMPTE Wipe',
    'EFF2_BLEND_DISSOLVE': 'Dissolve',
    'EFF_BLEND_DISSOLVE': 'Dissolve',
}
_FRIENDLY_BY_OP = {
    'Motion Control': 'Timewarp',
    'Timewarp': 'Timewarp',
    'SBlend_v2': '3D Warp',
    'SMPTE Wipe': 'SMPTE Wipe',
    'SpatialAdapter': 'FrameFlex',
}

_INTERP_FRIENDLY = {
    'ConstantInterp': 'Shelf',
    'LinearInterp': 'Linear',
    'BezierInterpolator': 'Bezier',
    'BezierInterpolation': 'Bezier',
    'CubicInterpolator': 'Spline',
    'AvidCubicInterpolator': 'Spline',
}


def _decode_avid_effect_id(op_group) -> Optional[str]:
    try:
        for param in (op_group.get('Parameters') or []):
            if _param_def_name(param) == 'AvidEffectID':
                raw = param.value_at(0)
                if isinstance(raw, (list, tuple, bytes, bytearray)):
                    return bytes(b for b in raw if b).decode('ascii', 'ignore')
                if isinstance(raw, str):
                    return raw
    except Exception:
        pass
    return None


def _friendly_effect_label(op_group, op_name: str) -> str:
    eid = _decode_avid_effect_id(op_group)
    if eid:
        for prefix, label in _FRIENDLY_BY_EFFECT_ID.items():
            if eid.startswith(prefix):
                return label
    if op_name in _FRIENDLY_BY_OP:
        return _FRIENDLY_BY_OP[op_name]
    return op_name or '(efeito)'


def _interp_friendly(param) -> Optional[str]:
    try:
        name = getattr(getattr(param, 'interpolationdef', None), 'name', None)
        if name:
            return _INTERP_FRIENDLY.get(name, name)
    except Exception:
        pass
    return None


def _const_or_first(param):
    try:
        return float(param.value_at(0))
    except Exception:
        return None


_REFORMAT_MODES = {1: 'pillarbox', 2: 'center_crop', 3: 'keep_size', -1: 'stretch'}
_REFORMAT_NEEDS_REVIEW = {'center_crop', 'keep_size', 'stretch'}


def _detect_reframe_mode(comp) -> Optional[str]:
    seen: set = set()
    found: list = []

    def rec(o):
        if o is None or id(o) in seen:
            return
        seen.add(id(o))
        if type(o).__name__ == 'OperationGroup':
            op = getattr(o, 'operation', None)
            if getattr(op, 'name', '') == 'SpatialAdapter':
                try:
                    for p in (o.get('Parameters') or []):
                        if _param_def_name(p) == 'AFX_SPATIAL_REFORMAT':
                            v = _const_or_first(p)
                            if v is not None:
                                m = _REFORMAT_MODES.get(int(round(v)))
                                if m:
                                    found.append(m)
                except Exception:
                    pass
            try:
                for ip in list(o.get('InputSegments') or []):
                    rec(ip)
            except Exception:
                pass
        else:
            for attr in ('components', 'segments'):
                try:
                    for s in (getattr(o, attr, None) or []):
                        rec(s)
                except Exception:
                    pass
            try:
                inner = o.get('InputSegment')
                if inner is not None:
                    rec(inner.value if hasattr(inner, 'value') else inner)
            except Exception:
                pass

    rec(comp)
    if not found:
        return None
    for m in found:
        if m in _REFORMAT_NEEDS_REVIEW:
            return m
    return found[0]


def frameflex_neutro(size_x, size_y, pos_x, pos_y, rot_z) -> dict:
    out: dict = {}
    if size_y is None:
        size_y = size_x
    size_x = size_x or 100.0
    size_y = size_y or 100.0
    pos_x = pos_x or 0.0
    pos_y = pos_y or 0.0
    out['scale_x'] = 100.0 / size_x
    out['scale_y'] = 100.0 / size_y
    out['pos_x'] = -(pos_x / size_x) * 1000.0
    out['pos_y'] = -(pos_y / size_y) * 1000.0
    if rot_z:
        out['rot_z'] = rot_z
    return out


def _extract_frameflex_params(pnames: dict) -> dict:
    def val(name):
        p = pnames.get(name)
        return _const_or_first(p) if p is not None else None

    return frameflex_neutro(val('AFX_SPATIAL_FRAMING_WID'), val('AFX_SPATIAL_FRAMING_HEI'),
                            val('AFX_SPATIAL_FRAMING_POSX'), val('AFX_SPATIAL_FRAMING_POSY'),
                            val('DVE_ROT_Z_U'))


PARAMS_GEOMETRICOS = {
    'DVE_POS_X_U': 'pos_x', 'DVE_POS_Y_U': 'pos_y', 'DVE_POS_Z_U': 'pos_z',
    'DVE_SCALE_X_U': 'scale_x', 'DVE_SCALE_Y_U': 'scale_y',
    'DVE_ROT_X_U': 'rot_x', 'DVE_ROT_Y_U': 'rot_y', 'DVE_ROT_Z_U': 'rot_z',
    'DVE_CROP_LEFT_U': 'crop_l', 'DVE_CROP_RIGHT_U': 'crop_r',
    'DVE_CROP_TOP_U': 'crop_t', 'DVE_CROP_BOTTOM_U': 'crop_b',
    'DVE_AXIS_X_U': 'axis_x', 'DVE_AXIS_Y_U': 'axis_y',
    'DVE_FG_KEY_OPACITY_U': 'opacity',
    'AFX_POS_X_U': 'pos_x', 'AFX_POS_Y_U': 'pos_y',
    'AFX_SCALE_X_U': 'scale_x', 'AFX_SCALE_Y_U': 'scale_y',
}


def _extract_geometric_params(op_group) -> dict:
    out: dict = {}
    animated = False
    interp = None
    try:
        params = list(op_group.get('Parameters') or [])
    except Exception:
        return out

    pnames = {_param_def_name(p): p for p in params}
    if 'AFX_SPATIAL_FRAMING_WID' in pnames:
        return _extract_frameflex_params(pnames)
    wanted = PARAMS_GEOMETRICOS
    varying = {}
    times: set = set()
    for param in params:
        pname = _param_def_name(param)
        key = wanted.get(pname)
        if not key:
            continue
        val = _const_or_first(param)
        if val is None:
            continue
        out[key] = (val / 100.0) if key.startswith('scale') else val
        if type(param).__name__ == 'VaryingValue':
            animated = True
            varying[key] = param
            interp = interp or _interp_friendly(param)
            try:
                for cp in param['PointList'].value:
                    times.add(round(float(cp.time), 4))
            except Exception:
                pass
    if animated:
        out['animated'] = True
        if interp:
            out['interp'] = interp
        kfs = []
        for t in sorted(times):
            kf = {'t': t}
            for key in ('scale_x', 'scale_y', 'pos_x', 'pos_y'):
                p = varying.get(key)
                if p is not None:
                    try:
                        v = float(p.value_at(t))
                        kf[key] = (v / 100.0) if key.startswith('scale') else v
                        continue
                    except Exception:
                        pass
                if key in out:
                    kf[key] = out[key]
            kfs.append(kf)
        if kfs:
            out['keyframes'] = kfs
    return out


def _speed_interp(op_group) -> Optional[str]:
    try:
        for param in (op_group.get('Parameters') or []):
            if 'SPEED_MAP' in _param_def_name(param) and type(param).__name__ == 'VaryingValue':
                return _interp_friendly(param)
    except Exception:
        pass
    return None


def _classify_effect(op_group, op_name: str,
                     speed_ratio: float = 1.0, me_type: Optional[str] = None) -> dict:
    category, reproducible = _categorize_effect(op_name)
    eff = {
        "name": op_name or "(efeito)",
        "label": _friendly_effect_label(op_group, op_name),
        "category": category,
        "reproducible": reproducible,
        "params": {},
    }
    if category == 'geometric':
        eff["params"] = _extract_geometric_params(op_group)
        if 'panscan' in (op_name or '').lower().replace(' ', '').replace('&', ''):
            for k in ('pos_x', 'pos_y'):
                if k in eff["params"]:
                    eff["params"][k] = -eff["params"][k]
        if not eff["params"]:
            eff["reproducible"] = False
    elif category == 'speed':
        eff["params"] = {"speed_ratio": round(speed_ratio, 6), "type": me_type or 'constant'}
        interp = _speed_interp(op_group)
        if interp:
            eff["params"]["interp"] = interp
    elif category == 'title':
        eff["params"] = _extract_title_params(op_group)
        if not eff["params"].get("text"):
            eff["reproducible"] = False
        try:
            from titulos import avid as _tit
            mqp = eff["params"].get("mqp_hex")
            titulo = _tit.do_aaf(op_name, eff["params"].get("texts") or [],
                                 _avup_texto_inteiro(bytes.fromhex(mqp)) if mqp else None)
            if titulo:
                eff["params"]["titulo"] = titulo
        except Exception as e:
            log.debug("titulo do AAF: %s", e)
    elif category == 'gain':
        level_num = None
        for p in (op_group.get('Parameters') or []):
            try:
                v = p.value
                if type(v).__name__ == 'AAFRational':
                    level_num = int(v.numerator); break
            except Exception:
                pass
        if level_num is not None:
            eff["params"] = {"level_num": level_num}
        else:
            eff["reproducible"] = False
    return eff


def frames_to_tc(frames: int, fps: float) -> str:
    fps_int = round(fps)
    ff = frames % fps_int
    ss = (frames // fps_int) % 60
    mm = (frames // fps_int // 60) % 60
    hh = frames // fps_int // 3600
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def _find_timecode(seg):
    if seg is None:
        return None
    if type(seg).__name__ == 'Timecode':
        return seg
    comps = getattr(seg, 'components', None)
    if comps is not None:
        for c in comps:
            r = _find_timecode(c)
            if r is not None:
                return r
    inp = getattr(seg, 'input_segment', None)
    if inp is not None:
        return _find_timecode(inp)
    return None


def _frames_to_tc_df(frames: int, fps: float, drop: bool) -> str:
    nominal = round(fps) or 25
    if not drop or nominal not in (30, 60):
        return frames_to_tc(frames, fps)
    f = max(0, int(frames))
    dropf = 4 if nominal == 60 else 2
    frames_per_10min = nominal * 60 * 10
    frames_per_min   = nominal * 60 - dropf
    d = f // frames_per_10min
    m = f % frames_per_10min
    if m > dropf:
        f += dropf * 9 * d + dropf * ((m - dropf) // frames_per_min)
    else:
        f += dropf * 9 * d
    ff = f % nominal; ss = (f // nominal) % 60
    mm = (f // (nominal * 60)) % 60; hh = f // (nominal * 3600)
    return f"{hh:02d}:{mm:02d}:{ss:02d};{ff:02d}"


def _read_start_tc(comp, timeline_fps: float):
    nominal = round(timeline_fps) or 25
    fallback = None
    for slot in getattr(comp, 'slots', []):
        seg = getattr(slot, 'segment', None)
        if getattr(seg, 'media_kind', '') != 'Timecode':
            continue
        tc = _find_timecode(seg)
        if tc is None:
            continue
        try:
            tcfps = int(round(float(tc.fps)))
            start = int(tc.start)
            drop = bool(getattr(tc, 'drop', False))
        except Exception:
            continue
        if tcfps <= 0:
            continue
        if tcfps == nominal:
            return start, drop
        if fallback is None:
            h = start // (3600 * tcfps); m = (start // (60 * tcfps)) % 60
            sec = (start // tcfps) % 60; fr = start % tcfps
            fallback = (((h * 60 + m) * 60 + sec) * nominal + min(fr, nominal - 1), drop)
    return fallback or (0, False)


def url_to_local_path(url: str) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path)
    if path.startswith("/Macintosh HD/"):
        path = path[len("/Macintosh HD"):]
    return path


def read_url_from_descriptor(desc) -> Optional[str]:
    try:
        locs = list(getattr(desc, 'locator', []))
        for loc in locs:
            try:
                val = loc['URLString'].value
                if val and isinstance(val, str):
                    return val
            except Exception:
                pass
    except Exception:
        pass
    return None


def _source_mob_duration(src_mob) -> tuple[Optional[int], Optional[float]]:
    try:
        best_frames: Optional[int] = None
        best_secs: Optional[float] = None
        for slot in src_mob.slots:
            seg = getattr(slot, 'segment', None)
            length = getattr(seg, 'length', None)
            if not length:
                continue
            length = int(length)
            er = getattr(slot, 'edit_rate', None)
            er_f = None
            try:
                er_f = float(er) if er is not None else None
            except Exception:
                er_f = None
            if er_f is not None and er_f >= 1000:
                continue
            secs = (length / er_f) if er_f else None
            if best_frames is None or length > best_frames:
                best_frames = length
                best_secs = secs
        return best_frames, best_secs
    except Exception as e:
        log.debug("Falha ao ler duração do SourceMob '%s': %s",
                  getattr(src_mob, 'name', '?'), e)
        return None, None


def _source_mob_fps(src_mob) -> Optional[float]:
    try:
        best_len, best_fps = None, None
        for slot in src_mob.slots:
            seg = getattr(slot, 'segment', None)
            length = getattr(seg, 'length', None)
            er = getattr(slot, 'edit_rate', None)
            try:
                er_f = float(er) if er is not None else None
            except Exception:
                er_f = None
            if er_f is None or er_f >= 1000:
                continue
            length = int(length) if length else 0
            if best_len is None or length > best_len:
                best_len, best_fps = length, er_f
        return best_fps
    except Exception:
        return None


def resolve_master_mob(master_mob) -> Optional[MediaRef]:
    best: Optional[MediaRef] = None

    for mslot in master_mob.slots:
        seg = mslot.segment

        src_clip = None
        if type(seg).__name__ == 'SourceClip':
            src_clip = seg
        elif type(seg).__name__ == 'Sequence':
            for comp in seg.components:
                if type(comp).__name__ == 'SourceClip':
                    src_clip = comp
                    break

        if src_clip is None:
            continue

        try:
            src_mob = src_clip.mob
        except Exception:
            continue
        if type(src_mob).__name__ != 'SourceMob':
            continue

        desc = getattr(src_mob, 'descriptor', None)
        if not desc:
            continue

        desc_kind = type(desc).__name__

        dur_f, dur_s = _source_mob_duration(src_mob)
        src_fps = _source_mob_fps(src_mob)

        if desc_kind == 'ImportDescriptor':
            url = read_url_from_descriptor(desc)
            if url:
                return MediaRef(
                    mob_name=src_mob.name,
                    url=url,
                    local_path=url_to_local_path(url),
                    descriptor_type='ImportDescriptor',
                    source_duration_frames=dur_f,
                    source_duration_secs=dur_s,
                    source_fps=src_fps,
                )

        elif desc_kind == 'CDCIDescriptor':
            import_ref = _follow_proxy_to_original(src_mob)
            if import_ref and best is None:
                import_ref.source_duration_frames = dur_f
                import_ref.source_duration_secs = dur_s
                import_ref.source_fps = src_fps
                best = import_ref
            elif best is None:
                url = read_url_from_descriptor(desc)
                if url:
                    best = MediaRef(
                        mob_name=src_mob.name,
                        url=url,
                        local_path=url_to_local_path(url),
                        descriptor_type='CDCIDescriptor',
                        source_duration_frames=dur_f,
                        source_duration_secs=dur_s,
                        source_fps=src_fps,
                    )

    return best


def _follow_proxy_to_original(proxy_src_mob) -> Optional[MediaRef]:
    try:
        for slot in proxy_src_mob.slots:
            seg = slot.segment
            src_clip = None
            if type(seg).__name__ == 'SourceClip':
                src_clip = seg
            elif type(seg).__name__ == 'Sequence':
                for comp in seg.components:
                    if type(comp).__name__ == 'SourceClip':
                        src_clip = comp
                        break
            if src_clip is None:
                continue
            try:
                inner_mob = src_clip.mob
            except Exception:
                continue
            if type(inner_mob).__name__ != 'SourceMob':
                continue
            inner_desc = getattr(inner_mob, 'descriptor', None)
            if inner_desc and type(inner_desc).__name__ == 'ImportDescriptor':
                url = read_url_from_descriptor(inner_desc)
                if url:
                    return MediaRef(
                        mob_name=inner_mob.name,
                        url=url,
                        local_path=url_to_local_path(url),
                        descriptor_type='ImportDescriptor',
                    )
    except Exception as e:
        log.debug("Erro ao seguir proxy para original: %s", e)
    return None


def _get_app_code(mob) -> Optional[int]:
    try:
        prop = mob.get('AppCode')
        if prop is None:
            return None
        val = prop.value if hasattr(prop, 'value') else prop
        return int(val) if val is not None else None
    except Exception:
        return None


def _is_group_mob(comp_mob) -> tuple[bool, str]:
    app_code = _get_app_code(comp_mob)
    if app_code in (4, 5):
        return True, getattr(comp_mob, 'name', '') or ""

    try:
        picture_slots = sum(
            1 for slot in comp_mob.slots
            if getattr(slot.segment, 'media_kind', '') in ('picture', 'Picture')
        )
        if picture_slots > 1:
            return True, getattr(comp_mob, 'name', '') or ""
    except Exception:
        pass

    return False, ""


def _resolve_subclip(sub_mob, want_slot_id=None) -> tuple[Optional[MediaRef], str, int]:
    inner_off = 0
    name = getattr(sub_mob, 'name', '') or ""
    try:
        for slot in sub_mob.slots:
            if want_slot_id is not None:
                if getattr(slot, 'slot_id', None) != want_slot_id:
                    continue
            elif getattr(slot, 'media_kind', '') not in ('Picture', 'picture'):
                continue
            seg = slot.segment
            def _first_sc(x):
                k = type(x).__name__
                if k == 'SourceClip':
                    return x
                if k == 'OperationGroup':
                    for ip in (list(x.get('InputSegments') or [])):
                        r = _first_sc(ip)
                        if r:
                            return r
                    return None
                if k in ('Sequence', 'NestedScope'):
                    comps = list(x.segments) if hasattr(x, 'segments') else list(x.components)
                    for c in comps:
                        r = _first_sc(c)
                        if r:
                            return r
                return None
            inner_clip = _first_sc(seg)
            if inner_clip is None:
                continue
            inner_off = getattr(inner_clip, 'start', 0) or 0
            try:
                tgt = inner_clip.mob
            except Exception:
                return None, name, inner_off
            tgt_kind = type(tgt).__name__
            if tgt_kind == 'MasterMob':
                ref = resolve_master_mob(tgt)
                return ref, (getattr(tgt, 'name', '') or name), inner_off
            if tgt_kind == 'CompositionMob' and getattr(tgt, 'usage', None) == 'Usage_SubClip':
                ref, n2, off2 = _resolve_subclip(tgt, getattr(inner_clip, 'slot_id', None))
                return ref, (n2 or name), inner_off + off2
            return None, name, inner_off
    except Exception as e:
        log.debug("Erro ao resolver subclipe '%s': %s", name, e)
    return None, name, inner_off


def resolve_source_clip(sc) -> tuple[Optional[MediaRef], str, Optional[str], int]:
    try:
        mob = sc.mob
    except Exception:
        return None, "(gap)", None, 0

    mob_kind = type(mob).__name__
    mob_id = str(getattr(mob, 'mob_id', None) or "")
    mob_name = getattr(mob, 'name', '') or ""

    if mob_kind == 'MasterMob':
        ref = resolve_master_mob(mob)
        return ref, mob_name, mob_id, 0

    if mob_kind == 'CompositionMob':
        if getattr(mob, 'usage', None) == 'Usage_SubClip':
            ref, name, inner_off = _resolve_subclip(mob, getattr(sc, 'slot_id', None))
            return ref, (name or mob_name), mob_id, inner_off
        return None, mob_name, mob_id, 0

    return None, mob_name, mob_id, 0


def _resolve_alt_segment(s) -> Optional[tuple[Optional[MediaRef], str, Optional[str], int]]:
    sk = type(s).__name__
    base = getattr(s, 'start', 0) or 0

    def _ok(n: str) -> bool:
        return bool(n) and not n.startswith("(")

    if sk == 'SourceClip':
        r, n, mid, eoff = resolve_source_clip(s)
        if r is not None or _ok(n):
            return r, n, mid, base + eoff
        return None
    if sk == 'Selector':
        r, n, mid, _alts, _ig, _gn, ss = resolve_selector(s)
        if r is not None or _ok(n):
            return r, n, mid, ss
        return None
    if sk == 'Sequence':
        for c in s.components:
            got = _resolve_alt_segment(c)
            if got:
                return got
        return None
    if sk == 'OperationGroup':
        try:
            for ip in list(s.get('InputSegments') or []):
                got = _resolve_alt_segment(ip)
                if got:
                    return got
        except Exception:
            pass
        return None
    return None


def resolve_selector(
    sel,
) -> tuple[Optional[MediaRef], str, Optional[str], list[MediaRef], bool, str, int]:
    alternates: list[MediaRef] = []
    is_group = False
    group_name = ""

    try:
        selected_prop = sel.get('Selected')
        selected = selected_prop.value if hasattr(selected_prop, 'value') else selected_prop
    except Exception:
        return None, "(selector sem selected)", None, [], False, "", 0

    if type(selected).__name__ != 'SourceClip':
        try:
            for cand in [selected] + (list(sel.get('Alternates')) or []):
                got = _resolve_alt_segment(cand)
                if got:
                    r, n, mid, st = got
                    return r, n, mid, [], False, "", st
        except Exception as e:
            log.debug("Selector com Selected não-SourceClip; alternates falharam: %s", e)
        return None, "(selector: tipo inesperado)", None, [], False, "", 0

    try:
        mob = selected.mob
    except Exception:
        return None, "(gap)", None, [], False, "", 0

    mob_kind = type(mob).__name__
    mob_name = getattr(mob, 'name', '') or ""
    mob_id = str(getattr(mob, 'mob_id', None) or "")
    slot_id = getattr(selected, 'slot_id', None)
    selector_start = getattr(selected, 'start', 0) or 0

    if mob_kind == 'MasterMob':
        ref = resolve_master_mob(mob)
        return ref, mob_name, mob_id, [], False, "", selector_start

    source_start = selector_start
    if mob_kind == 'CompositionMob' and slot_id is not None:
        ref, clip_name, is_group, group_name, slot_start, slot_offset = _resolve_recon_slot(mob, slot_id)
        source_start = slot_start + max(0, selector_start - slot_offset)
    else:
        ref, clip_name, mob_id, _extra_off = resolve_source_clip(selected)
        source_start = selector_start + _extra_off

    try:
        alt_list = list(sel.get('Alternates'))
        for alt in alt_list:
            if type(alt).__name__ != 'SourceClip':
                continue
            try:
                alt_mob = alt.mob
            except Exception:
                continue
            alt_slot_id = getattr(alt, 'slot_id', None)
            if type(alt_mob).__name__ == 'CompositionMob' and alt_slot_id is not None:
                alt_ref, _, _, _, _, _ = _resolve_recon_slot(alt_mob, alt_slot_id)
            elif type(alt_mob).__name__ == 'MasterMob':
                alt_ref = resolve_master_mob(alt_mob)
            else:
                alt_ref = None
            if alt_ref:
                alternates.append(alt_ref)
    except Exception as e:
        log.debug("Erro ao resolver alternates do Selector: %s", e)

    return ref, clip_name or mob_name, mob_id, alternates, is_group, group_name, source_start


def _resolve_recon_slot(
    comp_mob, slot_id: int
) -> tuple[Optional[MediaRef], str, bool, str, int, int]:
    is_group, group_name = _is_group_mob(comp_mob)

    def _resolve_clip(clip) -> tuple[Optional[MediaRef], str, int]:
        c_kind = type(clip).__name__
        c_start = getattr(clip, 'start', 0) or 0
        if c_kind == 'SourceClip':
            try:
                inner_mob = clip.mob
            except Exception:
                return None, "", 0
            inner_kind = type(inner_mob).__name__
            if inner_kind == 'MasterMob':
                ref = resolve_master_mob(inner_mob)
                return ref, getattr(inner_mob, 'name', '') or "", c_start
            elif inner_kind == 'CompositionMob':
                inner_slot_id = getattr(clip, 'slot_id', None)
                if inner_slot_id is not None:
                    r, n, _, _, ss, _soff = _resolve_recon_slot(inner_mob, inner_slot_id)
                    if r:
                        return r, n, c_start + ss
        elif c_kind == 'Selector':
            try:
                selp = clip.get('Selected')
                sel = selp.value if hasattr(selp, 'value') else selp
            except Exception:
                return None, "", 0
            if type(sel).__name__ == 'SourceClip':
                return _resolve_clip(sel)
        return None, "", 0

    try:
        for slot in comp_mob.slots:
            if getattr(slot, 'slot_id', None) != slot_id:
                continue
            seg = slot.segment
            seg_kind = type(seg).__name__

            if seg_kind in ('SourceClip', 'Selector'):
                ref, name, start = _resolve_clip(seg)
                if ref:
                    return ref, name, is_group, group_name, start, 0
                continue

            if hasattr(seg, 'components'):
                pos = 0
                for c in seg.components:
                    c_kind = type(c).__name__
                    if c_kind in ('SourceClip', 'Selector'):
                        ref, name, start = _resolve_clip(c)
                        if ref:
                            return ref, name, is_group, group_name, start, pos
                        break
                    pos += getattr(c, 'length', 0) or 0
    except Exception as e:
        log.debug("Erro ao resolver slot %s de %s: %s", slot_id, comp_mob.name, e)
    return None, "", is_group, group_name, 0, 0


def parse_aaf(path: str) -> dict:
    aaf_path = Path(path)
    if not aaf_path.exists():
        raise FileNotFoundError(f"AAF não encontrado: {path}")

    log.info("Parseando AAF: %s", aaf_path.name)
    segments: list[Segment] = []

    with aaf2.open(str(aaf_path), "r") as f:
        compositions = list(f.content.toplevel())
        if not compositions:
            raise ValueError("AAF não contém nenhuma CompositionMob.")

        main_comp = compositions[0]
        comp_name = getattr(main_comp, 'name', '') or ""
        log.info("Composition: '%s'", comp_name)

        slots = list(main_comp.slots)

        video_slots = [
            s for s in slots
            if s.segment and getattr(s.segment, 'media_kind', '') in ('picture', 'Picture')
        ]
        log.info("Video slots encontrados: %d", len(video_slots))

        has_nested_scope = False
        timeline_fps = 25.0

        for track_idx, slot in enumerate(video_slots):
            edit_rate = getattr(slot, 'edit_rate', None)
            fps = float(edit_rate) if edit_rate else 25.0
            timeline_fps = fps
            track_num = track_idx + 1
            seg = slot.segment
            seg_kind = type(seg).__name__

            log.info("Processando Slot %s | fps=%.4f | tipo=%s",
                     getattr(slot, 'slot_id', '?'), fps, seg_kind)

            if seg_kind == 'NestedScope':
                has_nested_scope = True
                _parse_nested_scope(seg, fps, track_num, segments)

            elif seg_kind == 'Sequence':
                _parse_sequence(seg, fps, track_num, 0, segments)

            else:
                log.warning("Tipo de segmento inesperado no video slot: %s", seg_kind)

        sound_slots = [
            s for s in slots
            if s.segment and getattr(s.segment, 'media_kind', '') in ('sound', 'Sound')
        ]
        log.info("Sound slots encontrados: %d", len(sound_slots))
        audio_start = len(segments)
        for a_idx, slot in enumerate(sound_slots):
            edit_rate = getattr(slot, 'edit_rate', None)
            a_fps = float(edit_rate) if edit_rate else timeline_fps
            a_track = 1000 + a_idx
            seg = slot.segment
            seg_kind = type(seg).__name__
            try:
                if seg_kind == 'NestedScope':
                    _parse_nested_scope(seg, a_fps, a_track, segments)
                elif seg_kind == 'Sequence':
                    _parse_sequence(seg, a_fps, a_track, 0, segments)
            except Exception as e:
                log.warning("Falha ao parsear sound slot %s: %s",
                            getattr(slot, 'slot_id', '?'), e)
        for s in segments[audio_start:]:
            s.is_audio = True

        start_frames, drop_frame = _read_start_tc(main_comp, timeline_fps)

        markers = read_markers(main_comp, timeline_fps)
        for mk in markers:
            mk["tc"] = _frames_to_tc_df(start_frames + mk["position"],
                                        timeline_fps, drop_frame)

    log.info("Parsing concluído: %d segmento(s) extraído(s) · start_tc=%s · drop=%s",
             len(segments), _frames_to_tc_df(start_frames, timeline_fps, drop_frame), drop_frame)
    return {
        "segments": [s.to_dict() for s in segments],
        "timeline_fps": timeline_fps,
        "total_segments": len(segments),
        "has_nested_scope": has_nested_scope,
        "composition_name": comp_name,
        "timeline_start_frames": start_frames,
        "timeline_drop_frame": drop_frame,
        "timeline_start_tc": _frames_to_tc_df(start_frames, timeline_fps, drop_frame),
        "markers": markers,
    }


def _parse_nested_scope(nested, fps: float, track_num: int, out: list[Segment]) -> None:
    sequences = list(nested.slots)
    if not sequences:
        log.warning("NestedScope sem slots internos.")
        return

    v1_seq = sequences[0]
    _parse_sequence(v1_seq, fps, track_num, 0, out)

    for i in range(1, len(sequences)):
        sub_seq = sequences[i]
        sub_track = track_num + i
        log.info("NestedScope sub-seq %d → V%d", i, sub_track)
        _parse_sequence(sub_seq, fps, sub_track, 0, out)
        _annotate_graphic_overlays(sub_seq, fps, out)


def _parse_sequence(seq, fps: float, track_num: int,
                    timeline_offset: int, out: list[Segment]) -> None:
    tc_pos = timeline_offset

    components = seq.components if hasattr(seq, 'components') else [seq]
    for comp in components:
        kind = type(comp).__name__
        length = getattr(comp, 'length', 0) or 0

        if kind == 'Filler':
            seg = Segment(
                timeline_tc_in=frames_to_tc(tc_pos, fps),
                timeline_tc_out=frames_to_tc(tc_pos + length, fps),
                duration_frames=length,
                source_start_frames=0,
                source_fps=fps,
                clip_name="(gap)",
                track=track_num,
                is_gap=True,
            )
            out.append(seg)
            tc_pos += length

        elif kind == 'Transition':
            cutpoint = getattr(comp, 'cutpoint', 0) or 0
            t_in = max(0, tc_pos - length)
            seg = Segment(
                timeline_tc_in=frames_to_tc(t_in, fps),
                timeline_tc_out=frames_to_tc(tc_pos, fps),
                duration_frames=length,
                source_start_frames=0,
                source_fps=fps,
                clip_name="(transition)",
                track=track_num,
                is_transition=True,
                cutpoint=cutpoint,
            )
            out.append(seg)
            tc_pos -= length

        elif kind == 'SourceClip':
            ref, clip_name, mob_id, _extra_off = resolve_source_clip(comp)
            source_start = (getattr(comp, 'start', 0) or 0) + _extra_off
            seg = Segment(
                timeline_tc_in=frames_to_tc(tc_pos, fps),
                timeline_tc_out=frames_to_tc(tc_pos + length, fps),
                duration_frames=length,
                source_start_frames=source_start,
                source_fps=fps,
                clip_name=clip_name,
                track=track_num,
                mob_id=mob_id,
                media_ref=ref,
                source_slot_id=getattr(comp, 'slot_id', None),
                disabled=_clip_disabled(comp),
            )
            seg.motion_class, seg.conform_ratio, seg.relative_speed = _classify_motion(
                1.0, None, ref.source_fps if ref else None, fps)
            out.append(seg)
            tc_pos += length

        elif kind == 'Selector':
            ref, clip_name, mob_id, alts, is_grp, grp_name, source_start = resolve_selector(comp)
            seg = Segment(
                timeline_tc_in=frames_to_tc(tc_pos, fps),
                timeline_tc_out=frames_to_tc(tc_pos + length, fps),
                duration_frames=length,
                source_start_frames=source_start,
                source_fps=fps,
                clip_name=clip_name,
                track=track_num,
                mob_id=mob_id,
                media_ref=ref,
                source_slot_id=getattr(comp, 'slot_id', None),
                is_group_clip=is_grp,
                group_clip_name=grp_name if is_grp else None,
                selector_alternates=alts,
                disabled=_clip_disabled(comp),
            )
            seg.motion_class, seg.conform_ratio, seg.relative_speed = _classify_motion(
                1.0, None, ref.source_fps if ref else None, fps)
            out.append(seg)
            tc_pos += length

        elif kind == 'OperationGroup':
            op = getattr(comp, 'operation', None)
            op_name = getattr(op, 'name', '') if op else ''

            has_me = False
            me_type = None
            speed_r = 1.0
            phase_off = 0
            offset_map: list = []
            speed_curve: list = []
            speed_kfs: list = []
            if any(k in op_name for k in ('Motion', 'Speed', 'MotionEffect', 'Timewarp')):
                has_me = True
                speed_r, _is_rev, phase_off, me_type, offset_map, speed_curve, speed_kfs = \
                    _parse_motion_effect_params(comp, op_length=length)
                log.debug(
                    "MotionEffect: op='%s' tipo='%s' speed=%.4f phase=%d offmap=%d",
                    op_name, me_type, speed_r, phase_off, len(offset_map),
                )

            inner_ref = None
            inner_name = op_name or "(operation group)"
            inner_start = 0
            inner_mob_id = None
            inner_is_grp = False
            inner_grp_name = ""
            inner_alts: list[MediaRef] = []
            found_source = False

            def _is_real_name(n: str) -> bool:
                return bool(n) and not n.startswith("(") and n != op_name

            slot_dentro = [None]

            def _resolve_input(s):
                sk = type(s).__name__
                if sk == 'SourceClip':
                    slot_dentro[0] = getattr(s, 'slot_id', None)
                    r, n, mid, eoff = resolve_source_clip(s)
                    st = (getattr(s, 'start', 0) or 0) + eoff
                    if r is None and not _is_real_name(n):
                        try:
                            scm = s.mob
                            sid = getattr(s, 'slot_id', None)
                            if type(scm).__name__ == 'CompositionMob' and sid is not None:
                                r2, n2, ig2, gn2, ss2, soff2 = _resolve_recon_slot(scm, sid)
                                if r2 is not None or _is_real_name(n2):
                                    return (r2, n2 or n, mid,
                                            st + max(0, ss2 - soff2), [], ig2, gn2)
                        except Exception as _e:
                            log.debug("SourceClip→CompositionMob falhou: %s", _e)
                    if r is not None or _is_real_name(n):
                        return (r, n, mid, st, [], False, "")
                    return None
                if sk == 'Selector':
                    r, n, mid, alts, ig, gn, ss = resolve_selector(s)
                    if r is not None or _is_real_name(n):
                        return (r, n, mid, ss, alts, ig, gn)
                    return None
                if sk == 'Sequence':
                    for sub in s.components:
                        got = _resolve_input(sub)
                        if got:
                            return got
                    return None
                if sk == 'OperationGroup':
                    nonlocal has_me, me_type, speed_r, phase_off, offset_map, speed_curve, speed_kfs
                    inner_op = getattr(s, "operation", None)
                    inner_op_name = getattr(inner_op, "name", "") if inner_op else ""
                    if not has_me and any(k in inner_op_name
                                          for k in ('Motion', 'Speed', 'MotionEffect', 'Timewarp')):
                        try:
                            speed_r, _rev, phase_off, me_type, offset_map, speed_curve, speed_kfs = \
                                _parse_motion_effect_params(s, op_length=getattr(s, "length", 0) or 0)
                            has_me = True
                        except Exception as _e:
                            log.debug("Velocidade aninhada não lida: %s", _e)
                    try:
                        for ip in list(s.get('InputSegments')):
                            got = _resolve_input(ip)
                            if got:
                                return got
                    except Exception:
                        pass
                    return None
                return None

            try:
                for inp_seq in list(comp.get('InputSegments')):
                    got = _resolve_input(inp_seq)
                    if got:
                        (inner_ref, inner_name, inner_mob_id, inner_start,
                         inner_alts, inner_is_grp, inner_grp_name) = got
                        found_source = True
                        break
            except Exception as e:
                log.debug("Erro ao extrair inputs do OperationGroup '%s': %s", op_name, e)

            is_effect_only = not found_source

            effect_desc = _classify_effect(comp, op_name, speed_r, me_type)

            seg = Segment(
                timeline_tc_in=frames_to_tc(tc_pos, fps),
                timeline_tc_out=frames_to_tc(tc_pos + length, fps),
                duration_frames=length,
                source_start_frames=inner_start,
                source_fps=fps,
                clip_name=inner_name,
                track=track_num,
                mob_id=inner_mob_id,
                media_ref=inner_ref,
                is_effect=is_effect_only,
                has_motion_effect=has_me,
                motion_effect_type=me_type,
                speed_ratio=speed_r if has_me else 1.0,
                phase_offset=phase_off if has_me else 0,
                source_offset_map=offset_map if has_me else [],
                speed_curve=speed_curve if has_me else [],
                speed_keyframes=speed_kfs if has_me else [],
                effects=[effect_desc],
                has_reframe='Motion Control' in op_name,
                reframe_mode=_detect_reframe_mode(comp),
                source_slot_id=slot_dentro[0] if found_source else None,
                is_group_clip=inner_is_grp,
                group_clip_name=inner_grp_name if inner_is_grp else None,
                selector_alternates=inner_alts,
                disabled=_clip_disabled(comp),
            )
            seg.motion_class, seg.conform_ratio, seg.relative_speed = _classify_motion(
                speed_r if has_me else 1.0, me_type if has_me else None,
                inner_ref.source_fps if inner_ref else None, fps)
            out.append(seg)
            tc_pos += length

        elif kind == 'ScopeReference':
            tc_pos += length

        else:
            log.debug("Componente ignorado: %s (length=%d)", kind, length)
            tc_pos += length


def _annotate_graphic_overlays(v2_seq, fps: float, segments: list[Segment]) -> None:
    tc_pos = 0
    GRAPHIC_OPS = ('Avid Titler+', 'Avid Title Tool', 'Blend')

    components = v2_seq.components if hasattr(v2_seq, 'components') else [v2_seq]
    for comp in components:
        kind = type(comp).__name__
        length = getattr(comp, 'length', 0) or 0

        if kind == 'OperationGroup':
            op = getattr(comp, 'operation', None)
            op_name = getattr(op, 'name', '') if op else ''
            is_graphic = any(g in op_name for g in GRAPHIC_OPS)

            if is_graphic:
                for seg in segments:
                    if seg.is_gap or seg.is_transition:
                        continue
                    seg_in  = _tc_to_frames(seg.timeline_tc_in, fps)
                    seg_out = _tc_to_frames(seg.timeline_tc_out, fps)
                    if seg_in < tc_pos + length and seg_out > tc_pos:
                        seg.has_graphic_overlay = True
                        log.debug(
                            "Gráfico '%s' sobre segmento '%s' (%s→%s)",
                            op_name, seg.clip_name, seg.timeline_tc_in, seg.timeline_tc_out
                        )

        tc_pos += length


def _tc_to_frames(tc: str, fps: float) -> int:
    parts = tc.replace(';', ':').split(':')
    if len(parts) != 4:
        return 0
    hh, mm, ss, ff = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    return (hh * 3600 + mm * 60 + ss) * round(fps) + ff
