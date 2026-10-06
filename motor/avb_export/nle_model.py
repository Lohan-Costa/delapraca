#!/usr/bin/env python3
import os, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANCO = os.path.normpath(os.path.join(ROOT, "..", "bancodedadosnles"))

def _num(v):
    try: return int(v)
    except Exception:
        try: return float(v)
        except Exception: return v

def _mobid(o):
    try: return o.bytes_le.hex()
    except Exception: return str(o)

def fp_component(c):
    cn = type(c).__name__
    pd = getattr(c, "property_data", {})
    d = {"_": cn, "len": _num(getattr(c, "length", pd.get("length", None)))}
    if cn == "SourceClip":
        d["mob"] = _mobid(c.mob_id)
        d["track_id"] = _num(getattr(c, "track_id", None))
        d["start"] = _num(getattr(c, "start_time", None))
    elif cn == "Filler":
        pass
    elif cn == "Timecode":
        d["fps"] = _num(getattr(c, "fps", None)); d["start"] = _num(getattr(c, "start", None))
    elif cn == "TransitionEffect":
        d["eid"] = getattr(c, "effect_id", None); d["cut"] = _num(getattr(c, "cutpoint", None))
    elif cn == "TrackEffect":
        d["eid"] = getattr(c, "effect_id", None)
        d["tracks"] = [fp_component(t.component) for t in getattr(c, "tracks", [])]
        d["params"] = fp_paramlist(pd.get("param_list"))
    elif cn == "MotionEffect":
        d["eid"] = getattr(c, "effect_id", None)
        d["speed_ratio"] = list(getattr(c, "speed_ratio", []) or [])
        d["tracks"] = [fp_component(t.component) for t in getattr(c, "tracks", [])]
        d["cps"] = fp_speed_cps(pd.get("param_list"))
    elif cn == "PanVolumeEffect":
        d["level"] = _num(pd.get("level")); d["pan"] = _num(pd.get("pan"))
        d["level_set"] = bool(pd.get("level_set")); d["pan_set"] = bool(pd.get("pan_set"))
        d["tracks"] = [fp_component(t.component) for t in getattr(c, "tracks", [])]
    elif cn == "Selector":
        d["selected"] = _num(pd.get("selected")); d["ganged"] = bool(pd.get("is_ganged"))
        d["mk"] = _num(pd.get("media_kind_id"))
        d["tracks"] = [fp_component(t.component) for t in getattr(c, "tracks", [])]
    elif cn == "Sequence":
        d["comps"] = [fp_component(x) for x in c.components]
    else:
        eid = getattr(c, "effect_id", None)
        if eid is not None:
            d["eid"] = eid
            d["tracks"] = [fp_component(t.component) for t in getattr(c, "tracks", [])]
        else:
            d["_unknown"] = True
    return d

import re as _re
def _canon_val(v):
    if isinstance(v, (int, float, str, bool)) or v is None:
        return _num(v)
    return _re.sub(r" at 0x[0-9a-fA-F]+", "", repr(v))

def fp_paramlist(pl):
    if not pl: return []
    out = []
    for it in pl:
        p = it.property_data
        out.append([str(p.get("uuid")), _num(p.get("value_type")), _canon_val(p.get("value"))])
    return out

def fp_speed_cps(pl):
    if not pl or len(pl) < 1: return []
    ct = pl[0].property_data.get("control_track")
    if ct is None: return []
    cps = ct.property_data.get("control_points") or []
    out = []
    for cp in cps:
        p = cp.property_data
        off = p.get("offset"); off = list(off) if isinstance(off, (list, tuple)) else off
        out.append([off, round(float(p.get("value", 0)), 4)])
    return out

META_MK = ("timecode", "edgecode", "DescriptiveMetadata")

def fp_mob(m, media_only=False):
    if media_only:
        tracks = {}
        for t in m.tracks:
            mk = getattr(t, "media_kind", None)
            if mk in META_MK:
                continue
            key = "%s#%s" % (mk, _num(getattr(t, "index", None)))
            tracks[key] = fp_component(deref(t.component))
        return {"name": m.name, "usage_code": _num(getattr(m, "usage_code", None)),
                "len": _num(getattr(m, "length", None)), "tracks": tracks}
    tracks = []
    for t in m.tracks:
        mk = getattr(t, "media_kind", None)
        if mk in META_MK:
            comp = t.component
            tracks.append({"mk": mk, "meta": type(comp).__name__, "len": _num(getattr(comp, "length", None))})
            continue
        tracks.append({"mk": mk, "idx": _num(getattr(t, "index", None)), "comp": fp_component(t.component)})
    return {"name": m.name, "usage_code": _num(getattr(m, "usage_code", None)),
            "len": _num(getattr(m, "length", None)), "tracks": tracks}

def fingerprint(path, media_only=False):
    import avb
    with avb.open(path) as f:
        comps = [m for m in f.content.mobs if getattr(m, "mob_type", None) == "CompositionMob"]
        top = [m for m in comps if _num(getattr(m, "usage_code", -1)) == 0]
        refs = [m for m in comps if _num(getattr(m, "usage_code", -1)) in (2, 4, 5)]
        fp = {"top": [fp_mob(m, media_only) for m in top],
              "refs": sorted([fp_mob(m, media_only) for m in refs],
                             key=lambda d: (d["usage_code"], d["name"] or "", d["len"] or 0))}
    return fp

def diff_fp(a, b, path=""):
    diffs = []
    if type(a) != type(b):
        return ["%s: tipo %s != %s" % (path, type(a).__name__, type(b).__name__)]
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a: diffs.append("%s.%s: ausente no A" % (path, k))
            elif k not in b: diffs.append("%s.%s: ausente no B" % (path, k))
            else: diffs += diff_fp(a[k], b[k], "%s.%s" % (path, k))
    elif isinstance(a, list):
        if len(a) != len(b):
            diffs.append("%s: len %d (A) != %d (B)" % (path, len(a), len(b)))
        for i in range(min(len(a), len(b))):
            diffs += diff_fp(a[i], b[i], "%s[%d]" % (path, i))
    else:
        if a != b:
            diffs.append("%s: %r (A) != %r (B)" % (path, a, b))
    return diffs

EFFECT_NAMES = {
    "EFF2_BLEND_DISSOLVE": "dissolve", "EFF2_BLEND_DIP": "dip-to-color",
    "BLEND_AUDIO_DISSOLVE": "audio-dissolve",
    "EFF2_BLEND_RESIZE": "resize", "EFF2_BLEND_PIP": "pip", "EFF2_SBLEND": "3dwarp",
    "EFF_ADV_MOTION_CTL": "speed", "EFF_TIMEWARP.AUDIO_TIME_WARP": "audio-warp",
    "EFF2_AUDIO_CHANNEL_COMBINER": "channel-combiner",
    "EFF_AUDIO_AS_PLUG_IN": "audio-plugin(rendered)",
    "EFF2_LUTSFX": "color-lut", "INT_EFF_SPATIAL_ADAPTER": "frameflex",
    "Audio Pan Volume": "gain/pan",
    "EFF2_RGB_COLOR_CORRECTION": "color-correction",
    "E4FE520A-B815-447F-A969-2D5E1C5F6C14": "subcap(subtitle)",
}

FORMAT_TYPES = {
    7: "HD 1080p/24", 9: "HD 1080p/23.976", 13: "HD 1080p/25", 22: "HD 1080p/29.97",
    26: "2160p/23.976",
}

USAGE_ROLES = {0: "TOP", 2: "subclip", 4: "group", 5: "container"}

def _load_banco_json(rel):
    p = os.path.join(BANCO, rel)
    try:
        with open(p, encoding="utf-8") as fh: return json.load(fh)
    except Exception:
        return None

def load_handlers():
    out = {}
    d = _load_banco_json("protocolos/avb/dados/ama-online.json")
    if d:
        for k, v in (d.get("container_handler_guids") or {}).items():
            g = (v.get("guid") or "").lower()
            if g:
                exts = ",".join(v.get("ext", []))
                out[g] = "%s [%s]" % (v.get("plugin", k), exts) if exts else v.get("plugin", k)
    out.setdefault("73dc063c-7602-4c4c-ba3f-3f47120cd1e9", "QuickTime/UME [.mov,.mp4]")
    out.setdefault("3711d3cc-62d0-49d7-b0ae-c118101d1a16", "WaveAiff [.wav,.aif]")
    out.setdefault("6229779f-002a-684d-be63-f081ab16643b", "mp3")
    out.setdefault("d945322d-f018-874b-8f3d-c5d8938fa2a5", "image [.png]")
    out.setdefault("bd449ccc-ff06-4f38-a2de-011870df8480", "Blackmagic RAW [.braw]")
    return out

def load_format_types():
    out = dict(FORMAT_TYPES)
    d = _load_banco_json("protocolos/avb/dados/codec-templates.json")
    if d:
        for k, v in (d.get("formatos_de_sequencia") or {}).items():
            if isinstance(v, dict) and "format_type" in v:
                out[int(v["format_type"])] = v.get("format_string", str(k))
    return out

def effect_label(effect_id):
    if effect_id is None: return None
    return EFFECT_NAMES.get(effect_id, effect_id)

AAF_EFFECT = {
    "VideoDissolve_2": "dissolve", "Dip_2": "dip-to-color", "Audio Dissolve": "audio-dissolve",
    "PaintResize_v2": "resize", "Picture-In-Picture_v2": "pip", "SBlend_v2": "3dwarp",
    "Motion Control": "speed", "Audio Warp": "audio-warp",
    "Audio Channel Combiner": "channel-combiner", "Audio Gain": "gain",
    "Audio Suite Plugin": "audio-plugin(rendered)",
    "ColorAdapter": "color-lut", "SpatialAdapter": "frameflex",
    "RGBColorCorrect_2": "color-correction", "SubCap": "subcap(subtitle)",
}
AAF_META_MK = ("timecode", "edgecode", "pulldown", "soundmastertrack", "descriptivemetadata")

def aaf_mk(x):
    return str(x).lower() if x is not None else None

def aaf_effect(op_name):
    return AAF_EFFECT.get(op_name, op_name)

def _aaf_seg_fp(seg):
    cn = type(seg).__name__
    d = {"_": _aaf_cls(cn), "len": _num(getattr(seg, "length", None))}
    if cn == "SourceClip":
        d["mob"] = _mobid(seg.mob_id)
        d["track_id"] = _num(getattr(seg, "slot_id", None))
        try: d["start"] = _num(seg["StartTime"].value)
        except Exception: d["start"] = None
    elif cn == "Filler":
        pass
    elif cn == "Transition":
        try: op = seg["OperationGroup"].value.operation.name
        except Exception: op = None
        d["_"] = "TransitionEffect"; d["eid"] = aaf_effect(op)
        d["cut"] = _num(seg["CutPoint"].value) if "CutPoint" in seg else None
    elif cn == "OperationGroup":
        d["_"] = "OperationGroup"; d["eid"] = aaf_effect(getattr(seg.operation, "name", None))
        d["tracks"] = [_aaf_seg_fp(s) for s in getattr(seg, "segments", [])]
    elif cn == "Sequence":
        d["comps"] = [_aaf_seg_fp(c) for c in seg.components]
    elif cn == "Selector":
        d["_"] = "Selector"
        alts = []
        try: alts = [_aaf_seg_fp(seg["Selected"].value)] if "Selected" in seg else []
        except Exception: pass
        try: alts += [_aaf_seg_fp(a) for a in (list(seg["Alternates"].value) if "Alternates" in seg else [])]
        except Exception: pass
        d["tracks"] = alts
    elif cn == "NestedScope":
        d["_"] = "NestedScope"; d["layers"] = [_aaf_seg_fp(s.segment if hasattr(s, "segment") else s)
                                               for s in getattr(seg, "slots", [])]
    elif cn == "ScopeReference":
        d["_"] = "Filler"
    else:
        d["_unknown"] = cn
    return d

def _aaf_cls(cn):
    return {"SourceClip": "SourceClip", "Filler": "Filler", "Sequence": "Sequence"}.get(cn, cn)

def aaf_layers(seg):
    if type(seg).__name__ == "NestedScope":
        return [getattr(sl, "segment", sl) for sl in getattr(seg, "slots", [])]
    return [seg]

def fp_mob_aaf(m, media_only=False):
    rank = {}
    tracks_list = []; tracks_dict = {}
    for s in m.slots:
        mk = aaf_mk(getattr(s, "media_kind", None))
        if mk in AAF_META_MK:
            if not media_only:
                tracks_list.append({"mk": mk, "meta": type(s.segment).__name__,
                                    "len": _num(getattr(s.segment, "length", None))})
            continue
        for layer in aaf_layers(s.segment):
            rank[mk] = rank.get(mk, 0) + 1
            comp = _aaf_seg_fp(layer)
            if media_only:
                tracks_dict["%s#%s" % (mk, rank[mk])] = comp
            else:
                tracks_list.append({"mk": mk, "idx": rank[mk], "comp": comp})
    return {"name": m.name, "usage_code": _aaf_usage_code(m),
            "len": _num(_aaf_toplen(m)), "tracks": tracks_dict if media_only else tracks_list}

def _aaf_usage_code(m):
    u = str(getattr(m, "usage", None))
    return {"Usage_TopLevel": 0, "Usage_SubClip": 2, "Usage_LowerLevel": 4, "None": 5}.get(u, 5)

def _aaf_toplen(m):
    for s in m.slots:
        if aaf_mk(getattr(s, "media_kind", None)) in ("picture", "sound"):
            return getattr(s.segment, "length", None)
    return None

def fingerprint_aaf(path, media_only=False):
    import aaf2
    with aaf2.open(path, "r") as f:
        mobs = list(f.content.mobs)
        top = [m for m in mobs if str(getattr(m, "usage", None)) == "Usage_TopLevel"]
        refs = [m for m in mobs if type(m).__name__ == "CompositionMob"
                and str(getattr(m, "usage", None)) in ("Usage_SubClip", "Usage_LowerLevel", "None")]
        fp = {"top": [fp_mob_aaf(m, media_only) for m in top],
              "refs": sorted([fp_mob_aaf(m, media_only) for m in refs],
                             key=lambda d: (d["usage_code"], d["name"] or "", d["len"] or 0))}
    return fp

def deref(x):
    return getattr(x, "value", x) if type(x).__name__ == "AVBObjectRef" else x

def safe_descriptor(m):
    try:
        return getattr(m, "descriptor", None)
    except Exception:
        return None

def mob_role(m):
    mt = getattr(m, "mob_type", None)
    if mt == "MasterMob": return "MASTER"
    if mt == "SourceMob":
        d = safe_descriptor(m)
        if d is None: return "SOURCE"
        try:
            return "SOURCE-file" if getattr(d, "mob_kind", None) == 5 else "SOURCE"
        except Exception:
            return "SOURCE(undecoded)"
    if mt == "CompositionMob":
        return USAGE_ROLES.get(_num(getattr(m, "usage_code", -1)), "COMP")
    return mt or "?"

def _attr(m, key):
    a = getattr(m, "attributes", None)
    if a is None: return None
    try: return a.get(key)
    except Exception:
        try: return a[key]
        except Exception: return None

def _guid_str(v):
    if v is None: return None
    if isinstance(v, (bytes, bytearray)) and len(v) == 16:
        h = bytes(v).hex()
        return "%s-%s-%s-%s-%s" % (h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])
    return str(v).lower()

def descriptor_handler(m, handlers):
    d = safe_descriptor(m)
    if d is None: return (None, None)
    guid = None
    try:
        da = getattr(d, "attributes", None)
        if da is not None:
            guid = da.get("_CONTAINER_HANDLER_GUID")
        if not guid:
            u = getattr(d, "uuid", None)
            if u is not None: guid = u
    except Exception:
        return (None, None)
    if not guid: return (None, None)
    g = _guid_str(guid)
    return (g, handlers.get(g))

def locator_path(m):
    d = safe_descriptor(m)
    if d is None: return None
    for cand in ([d] + list(getattr(d, "descriptors", []) or [])):
        L = getattr(cand, "locator", None)
        for loc in ([L] if L is not None else []) + list(getattr(cand, "locators", []) or []):
            pp = None
            try: pp = loc.property_data.get("path_posix")
            except Exception: pp = getattr(loc, "path_posix", None)
            if pp: return pp
    return None

def mob_index(mobs):
    idx = {}
    for m in mobs:
        try: idx[_mobid(m.mob_id)] = m
        except Exception: pass
    return idx

def _iter_source_clips(comp, depth=0):
    if depth > 8: return
    comp = deref(comp)
    cn = type(comp).__name__
    try:
        if cn == "SourceClip":
            yield comp
        elif cn == "Sequence":
            for c in comp.components:
                yield from _iter_source_clips(c, depth + 1)
        else:
            for t in getattr(comp, "tracks", []) or []:
                yield from _iter_source_clips(t.component, depth + 1)
    except Exception:
        return

def reachable_sources(master, index, _seen=None, depth=0):
    if _seen is None: _seen = set()
    out = []
    if depth > 6: return out
    try:
        for t in master.tracks:
            for sc in _iter_source_clips(t.component):
                hid = _mobid(sc.mob_id)
                if hid in _seen: continue
                _seen.add(hid)
                sm = index.get(hid)
                if sm is None: continue
                if getattr(sm, "mob_type", None) == "SourceMob":
                    out.append(sm)
                out += reachable_sources(sm, index, _seen, depth + 1)
    except Exception:
        pass
    return out

def online_verdict(source_mobs, master=None):
    path = None; date_bin = None
    for sm in source_mobs:
        path = path or locator_path(sm)
        date_bin = date_bin if date_bin is not None else _attr(sm, "_AMA_FILE_DATE_TIME")
    is_ama = _attr(master, "_IMPORTED_AMA") is not None if master is not None else (path is not None)
    res = {"path": path, "exists": None, "mtime_ok": None, "date_bin": date_bin,
           "date_file": None, "is_ama": bool(is_ama), "managed": (path is None and not is_ama)}
    if path and os.path.exists(path):
        res["exists"] = True
        try:
            mt = int(os.path.getmtime(path)); res["date_file"] = mt
            if date_bin is not None: res["mtime_ok"] = (int(date_bin) == mt)
        except Exception: pass
    elif path:
        res["exists"] = False
    return res
