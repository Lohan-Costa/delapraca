#!/usr/bin/env python3
import sys, os, random, uuid, json
import avb
try:
    import aaf2
except Exception:
    aaf2 = None
from avb.core import AVBObject, AVBRefList
from avb.mobid import MobID as AVBMobID

_GOLD_DIR = os.environ.get("AVB_GOLD_DIR", "")
def _gold(*parts):
    return os.path.join(_GOLD_DIR, *parts)
_BUILD = "--build-templates" in sys.argv
GOLD = _gold("AMOSTRAS DE FASES", "FASE 1", "FASE 1 FADE.avb")
RATE = {
    (24000, 1001): (23.976, 24, 9,  'HD 1080p/23.976'),
    (30000, 1001): (29.97,  30, 22, 'HD 1080p/29.97'),
    (25, 1):       (25.0,   25, 13, 'HD 1080p/25'),
    (24, 1):       (24.0,   24, 7,  'HD 1080p/24'),
}
def resolve_rate(num, den):
    if (num, den) in RATE:
        return RATE[(num, den)]
    er = num / den
    fps = round(er)
    return (round(er, 3), fps, 9, 'HD 1080p/%g' % round(er, 3))

ER = 29.97
MAXLEN = 2147483647
COMP_PREFIX = bytes.fromhex("060a2b340101010501010f1013000000")

REMAP = {}
if not _BUILD and os.environ.get("AVB_REMAP"):
    REMAP = json.load(open(os.environ["AVB_REMAP"], encoding="utf-8"))
def set_remap(d):
    global REMAP
    REMAP = dict(d or {})
def _R(h):
    return REMAP.get(h, h)
_MASTER_LEN = {}
def set_master_len(d):
    global _MASTER_LEN
    _MASTER_LEN = dict(d or {})

_OFFLINE_HEXES = set()
_OFFLINE_H = 0
def offline_handle_frames(segments):
    mx = max((int(s.get("duration_frames") or 0) for s in segments if s.get("is_transition")), default=0)
    return (mx + 12) if mx else 0
def set_offline_handles(hexes, h):
    global _OFFLINE_HEXES, _OFFLINE_H
    _OFFLINE_HEXES = set(hexes or ())
    _OFFLINE_H = int(h or 0)
DROP_UNTEMPLATED = os.environ.get("AVB_DROP_FX") == "1"
PRIM = (int, float, str, bool, bytes, bytearray, type(None))
OP_MAP = {"VideoDissolve_2": "EFF2_BLEND_DISSOLVE", "Dip_2": "EFF2_BLEND_DIP",
          "Audio Dissolve": "BLEND_AUDIO_DISSOLVE"}
EFEITO_GOLD = _gold("AMOSTRAS DE FASES", "FASE 1", "FASE 1 EFEITO.avb")
VELOC_GOLD = _gold("AMOSTRAS DE FASES", "FASE 1", "FASE 1 VELOCIDADE.avb")
FX_MAP = {"PaintResize_v2": "EFF2_BLEND_RESIZE", "Picture-In-Picture_v2": "EFF2_BLEND_PIP", "SBlend_v2": "EFF2_SBLEND",
          "RGBColorCorrect_2": "EFF2_RGB_COLOR_CORRECTION",
          "SubCap": "E4FE520A-B815-447F-A969-2D5E1C5F6C14"}
MULTIEFEITO_GOLD = _gold("BIN", "MULTIEFEITO.avb")
MOTION_OP = "Motion Control"
SPEED_DEF = "72559a80-24d7-11d3-8a50-0050040ef7d2"
SPEED_MAP_DEF = "8d56827c-847e-11d5-935a-50f857c10000"
COMBINER_OP = "Audio Channel Combiner"
AWARP_OP = "Audio Warp"
ESTEREO_GOLD = _gold("AMOSTRAS DE FASES", "FASE 2 - AUDIO", "FASE 2 - AUDIO ESTEREO.avb")
GAIN_OP = "Audio Gain"
GANHO_GOLD = _gold("AMOSTRAS DE FASES", "FASE 2 - AUDIO", "FASE 2 - AUDIO GANHO.avb")
FX_GOLD = _gold("AMOSTRAS DE FASES", "FASE 2 - AUDIO", "FASE 2 - AUDIO FX.avb")
ASPLUGIN_OP = "Audio Suite Plugin"
ASP_NAME_DEF = "76401b7f"; ASP_CHUNK_DEF = "80bcd031"; ASP_PRESET_DEF = "e735afe6"
CHANMAP = {}
MCAM_CONTAINERS = set()
CONTAINER_SLOT_RANK = {}

def serialize(o):
    if isinstance(o, AVBObject):
        return {'__class__': type(o).__name__, '__props__': {k: serialize(v) for k, v in o.property_data.items()}}
    if isinstance(o, AVBMobID):
        return {'__mobid__': o.bytes_le.hex()}
    if isinstance(o, uuid.UUID):
        return {'__uuid__': o.bytes_le.hex()}
    if isinstance(o, AVBRefList):
        return {'__reflist__': type(o).__name__, '__items__': [serialize(x) for x in o]}
    if isinstance(o, (list, tuple)):
        return {'__list__': [serialize(x) for x in o]}
    if isinstance(o, bytearray):
        return {'__bytearray__': bytes(o).hex()}
    if isinstance(o, bytes):
        return {'__bytes__': o.hex()}
    if isinstance(o, PRIM):
        return o
    try:
        return {'__attrs__': {k: serialize(v) for k, v in dict(o).items()}}
    except Exception:
        return {'__skip__': type(o).__name__}

def materialize(f, spec):
    if isinstance(spec, PRIM):
        return spec
    if isinstance(spec, dict):
        if '__mobid__' in spec: return AVBMobID(bytes_le=bytes.fromhex(spec['__mobid__']))
        if '__uuid__' in spec: return uuid.UUID(bytes_le=bytes.fromhex(spec['__uuid__']))
        if '__bytes__' in spec: return bytes.fromhex(spec['__bytes__'])
        if '__bytearray__' in spec: return bytearray.fromhex(spec['__bytearray__'])
        if '__reflist__' in spec:
            rl = getattr(f.create, spec['__reflist__'])()
            for x in spec['__items__']: rl.append(materialize(f, x))
            return rl
        if '__list__' in spec: return [materialize(f, x) for x in spec['__list__']]
        if '__attrs__' in spec:
            a = f.create.Attributes()
            for k, v in spec['__attrs__'].items(): a[k] = materialize(f, v)
            return a
        if '__skip__' in spec: return None
        if '__class__' in spec:
            cls = getattr(f.create, spec['__class__'])
            try: o = cls()
            except TypeError:
                try: o = cls(25, None)
                except TypeError: o = cls(f)
            for k, v in spec['__props__'].items():
                o.property_data[k] = materialize(f, v)
            return o
    return spec

def _pic_comps(path, media_kind="picture"):
    specs, names, eids = [], [], []
    with avb.open(path) as f:
        comp = [m for m in f.content.mobs if getattr(m, "mob_type", None) == "CompositionMob" and getattr(m, "usage", None) is None][0]
        for t in comp.tracks:
            if getattr(t, "media_kind", None) != media_kind: continue
            if type(t.component).__name__ != "Sequence": continue
            for c in t.component.components:
                cn = type(c).__name__
                specs.append(serialize(c) if cn in ("TransitionEffect", "TrackEffect", "MotionEffect") else None)
                names.append(cn); eids.append(getattr(c, "effect_id", None))
    return specs, names, eids

TEMPLATES_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates.json")

def capture_templates():
    if os.path.exists(TEMPLATES_JSON):
        with open(TEMPLATES_JSON, encoding="utf-8") as fp:
            return json.load(fp)
    return _capture_from_gold()

def _capture_from_gold():
    tpl = {}
    for path, cls, mk in ((GOLD, "TransitionEffect", "picture"), (EFEITO_GOLD, "TrackEffect", "picture"),
                          (GOLD, "TransitionEffect", "sound"),
                          (ESTEREO_GOLD, "TrackEffect", "sound"),
                          (ESTEREO_GOLD, "MotionEffect", "sound")):
        specs, names, eids = _pic_comps(path, mk)
        for spec, nm, eid in zip(specs, names, eids):
            if nm == cls and spec is not None:
                tpl.setdefault(eid, spec)
    with avb.open(VELOC_GOLD) as f:
        comp = [m for m in f.content.mobs if getattr(m, "mob_type", None) == "CompositionMob" and getattr(m, "usage", None) is None][0]
        for c in next(t.component.components for t in comp.tracks if getattr(t, "media_kind", None) == "picture"):
            if type(c).__name__ != "MotionEffect": continue
            cps = c.property_data["param_list"][0].property_data["control_track"].property_data["control_points"]
            if len(cps) == 1 and cps[0].property_data.get("value", 0) > 1.0:
                tpl["EFF_ADV_MOTION_CTL"] = serialize(c); break
    def _find(node, want, hit):
        cn = type(node).__name__
        if cn == want and want not in hit: hit[want] = serialize(node)
        if cn == "Sequence":
            for c in node.components: _find(c, want, hit)
        for st in getattr(node, "tracks", None) or []:
            sub = getattr(st, "component", None)
            if sub is not None: _find(sub, want, hit)
    with avb.open(GANHO_GOLD) as f:
        comp = [m for m in f.content.mobs if getattr(m, "mob_type", None) == "CompositionMob" and getattr(m, "usage", None) is None][0]
        hit = {}
        for t in comp.tracks:
            if getattr(t, "media_kind", None) == "sound" and type(t.component).__name__ == "Sequence":
                _find(t.component, "PanVolumeEffect", hit); _find(t.component, "Selector", hit)
        if "PanVolumeEffect" in hit: tpl["__panvol__"] = hit["PanVolumeEffect"]
        if "Selector" in hit: tpl["__selector__"] = hit["Selector"]
    def _find_eid(node, wanted, hit):
        eid = getattr(node, "effect_id", None)
        if eid in wanted and eid not in hit: hit[eid] = serialize(node)
        if type(node).__name__ == "Sequence":
            for c in node.components: _find_eid(c, wanted, hit)
        for st in getattr(node, "tracks", None) or []:
            sub = getattr(st, "component", None)
            if sub is not None: _find_eid(sub, wanted, hit)
    def _find_fx(node, eid, acc):
        if getattr(node, "effect_id", None) == eid and node.property_data.get("param_list") is not None:
            acc.append(node)
        if type(node).__name__ == "Sequence":
            for c in node.components: _find_fx(c, eid, acc)
        for st in getattr(node, "tracks", None) or []:
            sub = getattr(st, "component", None)
            if sub is not None: _find_fx(sub, eid, acc)
    try:
        with avb.open(MULTIEFEITO_GOLD) as f:
            comp = [m for m in f.content.mobs if getattr(m, "mob_type", None) == "CompositionMob"
                    and getattr(m, "usage", None) is None][0]
            hit = {}; wanted = {"EFF2_RGB_COLOR_CORRECTION", "E4FE520A-B815-447F-A969-2D5E1C5F6C14"}
            for t in comp.tracks: _find_eid(t.component, wanted, hit)
            tpl.update(hit)
            for eid in ("EFF2_SBLEND", "EFF2_RGB_COLOR_CORRECTION",
                        "E4FE520A-B815-447F-A969-2D5E1C5F6C14"):
                insts = []
                for t in comp.tracks: _find_fx(t.component, eid, insts)
                if insts:
                    best = max(insts, key=lambda c: len(c.property_data.get("param_list") or []))
                    tpl[eid] = serialize(best)
    except Exception:
        pass
    def _find_asp(node, acc):
        if type(node).__name__ == "AudioSuitePluginEffect": acc.append(node)
        if type(node).__name__ == "Sequence":
            for c in node.components: _find_asp(c, acc)
        for st in getattr(node, "tracks", None) or []:
            sub = getattr(st, "component", None); sub = getattr(sub, "value", sub)
            if sub is not None: _find_asp(sub, acc)
    for gold in (FX_GOLD, MULTIEFEITO_GOLD):
        try:
            with avb.open(gold) as f:
                comp = [m for m in f.content.mobs if getattr(m, "mob_type", None) == "CompositionMob"
                        and getattr(m, "usage", None) is None][0]
                asps = []
                for t in comp.tracks:
                    cc = getattr(t.component, "value", t.component)
                    _find_asp(cc, asps)
                for c in asps:
                    nm = dict(c.property_data.get("attributes") or {}).get("_EFFECT_PLUGIN_NAME")
                    if nm: tpl.setdefault("__asp__" + str(nm), serialize(c))
        except Exception:
            pass
    return tpl

_TAN = {"PP_OUT_TANGENT_POS_U": "out_dt", "PP_OUT_TANGENT_VAL_U": "out_dv",
        "PP_IN_TANGENT_POS_U": "in_dt", "PP_IN_TANGENT_VAL_U": "in_dv"}

def extract_speed_kfs(param):
    kfs = []
    try: points = list(param["PointList"].value)
    except Exception: return kfs
    for cp in points:
        h = {"out_dt": 0.0, "out_dv": 0.0, "in_dt": 0.0, "in_dv": 0.0, "mode": 1}
        try:
            for _pid, prop in cp.property_entries.items():
                val = prop.value
                if isinstance(val, list) and val and hasattr(val[0], "name"):
                    for cv in val:
                        nm = getattr(cv, "name", "")
                        if nm in _TAN: h[_TAN[nm]] = float(cv.value_at(0))
                        elif nm == "PP_TANGENT_MODE_U": h["mode"] = int(cv.value_at(0))
        except Exception: pass
        try: kfs.append({"t": float(cp.time), "speed": float(cp.value), **h})
        except Exception: pass
    kfs.sort(key=lambda k: k["t"])
    return kfs

def _bez(p, u):
    mt = 1.0 - u
    return mt*mt*mt*p[0] + 3*mt*mt*u*p[1] + 3*mt*u*u*p[2] + u*u*u*p[3]

def _eval_speed(kfs, x):
    if not kfs: return 1.0
    if x <= kfs[0]["t"]: return kfs[0]["speed"]
    if x >= kfs[-1]["t"]: return kfs[-1]["speed"]
    for i in range(len(kfs)-1):
        k0, k1 = kfs[i], kfs[i+1]
        if k0["t"] <= x <= k1["t"]:
            xs = (k0["t"], k0["t"]+k0["out_dt"], k1["t"]+k1["in_dt"], k1["t"])
            ys = (k0["speed"], k0["speed"]+k0["out_dv"], k1["speed"]+k1["in_dv"], k1["speed"])
            lo, hi = 0.0, 1.0
            for _ in range(40):
                u = (lo+hi)*0.5
                if _bez(xs, u) < x: lo = u
                else: hi = u
            return _bez(ys, (lo+hi)*0.5)
    return kfs[-1]["speed"]

def _offset_map(kfs, length):
    out = [(0.0, 0.0)]; acc = 0.0; f = 0.0
    while f < length - 1e-9:
        a = _eval_speed(kfs, f); m = _eval_speed(kfs, f+0.5); b = _eval_speed(kfs, f+1.0)
        half1 = (a + 4*_eval_speed(kfs, f+0.25) + m)/6.0*0.5
        acc += half1; out.append((f+0.5, acc))
        half2 = (m + 4*_eval_speed(kfs, f+0.75) + b)/6.0*0.5
        acc += half2; out.append((f+1.0, acc)); f += 1.0
    return out

def _aaf_inner_clip(og):
    for seg in og.segments:
        comps = getattr(seg, "components", [seg])
        for cc in comps:
            if type(cc).__name__ == "SourceClip":
                st = cc['StartTime'].value if 'StartTime' in cc else 0
                return (cc.mob_id.bytes_le.hex(), st, cc.length)
    return None

def _aaf_effect_inner(og, mk=None):
    content = None
    for seg in og.segments:
        comps = list(getattr(seg, "components", [seg]))
        meaningful = [c for c in comps if type(c).__name__ != "ScopeReference"]
        if meaningful:
            content = (seg, meaningful)
    if content is None:
        return ("gap", None, 0, og.length, None)
    seg, meaningful = content
    if len(meaningful) == 1:
        return _seg_entry(meaningful[0], mk)
    non_fill = [c for c in meaningful if type(c).__name__ != "Filler"]
    if len(non_fill) >= 1:
        return _seg_entry(non_fill[0], mk)
    return ("gap", None, 0, og.length, None)

def _chan(mob_hex, slot_id):
    m = CHANMAP.get(mob_hex)
    if m and slot_id in m: return m[slot_id]
    return (slot_id - 1) if slot_id and slot_id > 1 else (slot_id or 1)

def _aaf_combiner_clips(og):
    clips = []
    for seg in og.segments:
        sub = getattr(seg, "components", [seg])
        for cc in sub:
            if type(cc).__name__ == "SourceClip":
                st = cc['StartTime'].value if 'StartTime' in cc else 0
                mh = cc.mob_id.bytes_le.hex()
                clips.append((mh, _chan(mh, cc.slot_id), st, cc.length))
    return clips

def _read_selector(sel):
    chans = []
    selected = sel['Selected'].value if 'Selected' in sel else None
    alts = list(sel['Alternates'].value) if 'Alternates' in sel else []
    order = ([selected] if selected is not None else []) + alts
    for sc in order:
        if type(sc).__name__ != "SourceClip": continue
        st = sc['StartTime'].value if 'StartTime' in sc else 0
        mh = sc.mob_id.bytes_le.hex()
        chans.append((mh, _chan(mh, sc.slot_id), st, sc.length))
    return ("selector", chans, 0, sel.length, None)

def _read_mcam_sel(sel, mk):
    sel_sc = sel['Selected'].value if 'Selected' in sel else None
    alts   = list(sel['Alternates'].value) if 'Alternates' in sel else []
    all_sc = ([sel_sc] if sel_sc is not None else []) + alts
    clips  = []
    for i, sc in enumerate(all_sc):
        if type(sc).__name__ != "SourceClip": continue
        cont_hex = sc.mob_id.bytes_le.hex()
        slot_id  = sc.slot_id
        track_id = CONTAINER_SLOT_RANK.get((cont_hex, mk, slot_id))
        if track_id is None: continue
        st = sc['StartTime'].value if 'StartTime' in sc else 0
        clips.append((cont_hex, track_id, st, sel.length, i == 0))
    if not clips: return _read_selector(sel)
    clips.sort(key=lambda x: x[1])
    sel_idx = next((i for i, c in enumerate(clips) if c[4]), 0)
    return ("mcam_sel", [(c[0], c[1], c[2], c[3]) for c in clips], sel_idx, sel.length, None)

def _read_gain(og, mk=None):
    level = None
    for p in og.parameters:
        try:
            v = p.value
            if type(v).__name__ == "AAFRational": level = int(v.numerator); break
        except Exception: pass
    inner = next((s for s in og.segments), None)
    return ("gain", level, _seg_entry(inner, mk) if inner is not None else None, og.length, None)

def _seg_entry(seg, mk=None):
    cn = type(seg).__name__
    if cn == "SourceClip":
        st = seg['StartTime'].value if 'StartTime' in seg else 0
        return ("clip", seg.mob_id.bytes_le.hex(), st, seg.length, seg.slot_id)
    if cn in ("Filler", "ScopeReference"):
        return ("gap", None, 0, seg.length, None)
    if cn == "Sequence":
        inner = next((c for c in seg.components if type(c).__name__ != "Filler"), None)
        return _seg_entry(inner, mk) if inner is not None else ("gap", None, 0, seg.length, None)
    if cn == "Selector":
        sel_sc = seg['Selected'].value if 'Selected' in seg else None
        if mk is not None and sel_sc is not None and type(sel_sc).__name__ == "SourceClip":
            if sel_sc.mob_id.bytes_le.hex() in MCAM_CONTAINERS:
                return _read_mcam_sel(seg, mk)
        return _read_selector(seg)
    if cn == "OperationGroup":
        op = seg.operation.name
        inner = _aaf_inner_clip(seg)
        if op == COMBINER_OP:
            return ("combiner", _aaf_combiner_clips(seg), None, seg.length, None)
        if op == GAIN_OP:
            return _read_gain(seg, mk)
        if op == AWARP_OP:
            sr = None
            for p in seg.parameters:
                try:
                    v = p.value
                    if type(v).__name__ == "AAFRational": sr = [int(v.numerator), int(v.denominator)]; break
                except Exception: pass
            comb = next((s for s in seg.segments if type(s).__name__ == "OperationGroup"
                         and s.operation.name == COMBINER_OP), None)
            clips = _aaf_combiner_clips(comb) if comb is not None else []
            comb_len = comb.length if comb is not None else seg.length
            inner_entry = None
            if comb is None:
                inner_seg = next((s for s in seg.segments), None)
                inner_entry = _seg_entry(inner_seg, mk) if inner_seg is not None else None
            return ("awarp", sr, (clips, comb_len, inner_entry), seg.length, None)
        if op == MOTION_OP:
            sr = None; kfs = []
            for p in seg.parameters:
                try:
                    d = str(p['Definition'].value); tn = type(p).__name__
                    if d == SPEED_DEF and tn == "ConstantValue":
                        v = p.value; sr = [int(v.numerator), int(v.denominator)]
                    elif d == SPEED_MAP_DEF and tn == "VaryingValue":
                        kfs = extract_speed_kfs(p)
                except Exception: pass
            if not kfs and sr and sr[0]:
                kfs = [{"t": 0.0, "speed": sr[1] / sr[0], "in_dt": 0.0, "in_dv": 0.0, "out_dt": 0.0, "out_dv": 0.0, "mode": 1}]
            return ("motion", (sr, kfs), inner, seg.length, None)
        if op == ASPLUGIN_OP:
            name = None; chunk = None; preset = None
            for p in seg.parameters:
                try: d = str(p['Definition'].value)
                except Exception: continue
                try: v = p.value
                except Exception: continue
                if d.startswith(ASP_NAME_DEF): name = v
                elif d.startswith(ASP_CHUNK_DEF) and isinstance(v, (list, bytes, bytearray)): chunk = bytes(v)
                elif d.startswith(ASP_PRESET_DEF) and isinstance(v, (list, bytes, bytearray)): preset = bytes(v)
            return ("asplugin", name, _aaf_effect_inner(seg, mk), seg.length, (chunk, preset))
        params = {}
        for p in seg.parameters:
            try:
                d = str(p['Definition'].value)
            except Exception:
                continue
            try: params[d] = p.value
            except Exception: params[d] = _VARYING
        return ("fx", FX_MAP.get(op, op), _aaf_effect_inner(seg, mk), seg.length, params)
    raise SystemExit("comp AAF não suportado: %s" % cn)

def _read_row(comps, mk=None):
    row = []
    for c in comps:
        if type(c).__name__ == "Transition":
            cp = c['CutPoint'].value if 'CutPoint' in c else 0
            op = c['OperationGroup'].value.operation.name
            row.append(("trans", OP_MAP.get(op, op), cp, c.length, None))
        else:
            row.append(_seg_entry(c, mk))
    return row

USAGE_UC = {"Usage_SubClip": 2, "Usage_LowerLevel": 4, "None": 5}

def _read_track_payload(seg, mk=None):
    if type(seg).__name__ == "Sequence":
        return ("seq", _read_row(seg.components, mk))
    return ("seg", _seg_entry(seg, mk))

def _read_comp_mobs(f):
    out = {}
    for m in f.content.mobs:
        if type(m).__name__ != "CompositionMob": continue
        u = str(getattr(m, "usage", None))
        if u == "Usage_TopLevel": continue
        uc = USAGE_UC.get(u)
        if uc is None: continue
        trks = []; length = 0
        for s in m.slots:
            mks = str(s.media_kind)
            mk = "picture" if mks == "Picture" else ("sound" if mks == "Sound" else None)
            if mk is None: continue
            trks.append((mk,) + _read_track_payload(s.segment, mk))
            length = max(length, getattr(s.segment, "length", 0) or 0)
        out[m.mob_id.bytes_le.hex()] = {"mob_id_hex": m.mob_id.bytes_le.hex(), "usage_code": uc,
                                        "name": m.name, "length": length, "tracks": trks}
    return out

def read_aaf(path):
    tracks = []; rate = None
    with aaf2.open(path, "r") as f:
        CHANMAP.clear()
        MCAM_CONTAINERS.clear(); CONTAINER_SLOT_RANK.clear()
        for m in f.content.mobs:
            cm = {}
            for kind in ("Picture", "Sound"):
                slots = sorted(s.slot_id for s in m.slots if str(s.media_kind) == kind)
                for i, sid in enumerate(slots): cm[sid] = i + 1
            if cm: CHANMAP[m.mob_id.bytes_le.hex()] = cm
            if getattr(m, "usage", "X") is None and getattr(m, "name", None) == "":
                mh = m.mob_id.bytes_le.hex()
                MCAM_CONTAINERS.add(mh)
                by_mk = {}
                for s in m.slots:
                    k = "picture" if str(s.media_kind) == "Picture" else "sound"
                    by_mk.setdefault(k, []).append(s.slot_id)
                for k, sids in by_mk.items():
                    for rank, sid in enumerate(sorted(sids)):
                        CONTAINER_SLOT_RANK[(mh, k, sid)] = rank + 1
        comp_mobs = _read_comp_mobs(f)
        comp = [m for m in f.content.mobs if getattr(m, "usage", None) == 'Usage_TopLevel'][0]
        name = comp.name
        for s in comp.slots:
            mk = str(s.media_kind)
            if mk in ("Picture", "picture"):
                kind = "picture"
            elif mk in ("Sound", "sound"):
                kind = "sound"
            else:
                continue
            er = s.edit_rate
            if rate is None: rate = (int(er.numerator), int(er.denominator))
            seg = s.segment
            if type(seg).__name__ == "NestedScope":
                for layer in seg.slots:
                    lseg = layer.segment if hasattr(layer, "segment") else layer
                    tracks.append((kind, _read_row(lseg.components, kind)))
            else:
                tracks.append((kind, _read_row(seg.components, kind)))
    return name, tracks, rate, comp_mobs

def _load_media_map():
    name2h = {}
    p = os.environ.get("AVB_MEDIA_MAP")
    if p and os.path.exists(p):
        try:
            for v in json.load(open(p, encoding="utf-8")).values():
                if isinstance(v, dict) and v.get("name"):
                    name2h[_norm_name(v["name"])] = v["mob_id"]
        except Exception:
            pass
    return name2h

def _urn2hex_from_aaf(path):
    m2h = {}
    with aaf2.open(path, "r") as f:
        for m in f.content.mobs:
            if type(m).__name__ == "MasterMob":
                m2h[str(m.mob_id)] = m.mob_id.bytes_le.hex()
    return m2h

def _norm_name(s):
    s = (s or "").lower().strip()
    for e in (".wav", ".aif", ".aiff", ".mp4", ".mov", ".mp3"):
        if s.endswith(e):
            return s[:-len(e)]
    return s

MOTION_OFF = os.environ.get("AVB_NO_MOTION") == "1"
TRANS_OFF = os.environ.get("AVB_NO_TRANS") == "1"

def _motion_entry_from_seg(s, h):
    from fractions import Fraction
    dur = s["duration_frames"]
    kfs = [dict(k) for k in (s.get("speed_keyframes") or [])]
    offmap = s.get("source_offset_map") or []
    if not kfs:
        if not (offmap and dur): return None
        sp = (offmap[-1][1] - offmap[0][1]) / dur
        kfs = [{"t": 0.0, "speed": sp, "in_dt": 0.0, "in_dv": 0.0, "out_dt": 0.0, "out_dv": 0.0, "mode": 1}]
    if s.get("motion_effect_type") == "reverse":
        for k in kfs: k["speed"] = -abs(k["speed"])
    src_len = int(round(abs(offmap[-1][1] - offmap[0][1]))) if offmap else int(round(abs(kfs[-1]["speed"]) * dur))
    inner = (h, s["source_start_frames"], max(1, src_len))
    avg = kfs[0]["speed"] if len(kfs) == 1 else ((offmap[-1][1] - offmap[0][1]) / dur if offmap and dur else 1.0)
    fr = Fraction(avg or 0).limit_denominator(1000)
    sr = [fr.numerator, fr.denominator] if fr.denominator else None
    return ("motion", (sr, kfs), inner, dur, None)

UNITY_GAIN = 1 << 29

def _gain_level(s):
    for e in (s.get("effects") or []):
        if e.get("name") == "Audio Gain":
            lv = (e.get("params") or {}).get("level_num")
            if lv is not None and lv != UNITY_GAIN:
                return lv
    return None

def _trim_tail(e, amount):
    if amount <= 0 or e[0] not in ("clip", "gain"):
        return e, 0
    if e[0] == "clip":
        k, h, ss, d, slot = e
        cut = min(d - 1, amount)
        return ((k, h, ss, d - cut, slot) if cut else e), cut
    k, lv, inner, d, slot = e
    if inner is None or inner[0] != "clip":
        return e, 0
    ik, ih, iss, idur, islot = inner
    cut = min(idur - 1, d - 1, amount)
    if not cut:
        return e, 0
    return (k, lv, (ik, ih, iss, idur - cut, islot), d - cut, slot), cut

def _trim_head(e, amount):
    if amount <= 0 or e[0] not in ("clip", "gain"):
        return e, 0
    if e[0] == "clip":
        k, h, ss, d, slot = e
        cut = min(d - 1, amount)
        return ((k, h, ss + cut, d - cut, slot) if cut else e), cut
    k, lv, inner, d, slot = e
    if inner is None or inner[0] != "clip":
        return e, 0
    ik, ih, iss, idur, islot = inner
    cut = min(idur - 1, d - 1, amount)
    if not cut:
        return e, 0
    return (k, lv, (ik, ih, iss + cut, idur - cut, islot), d - cut, slot), cut

def _seg_to_entry(s, m2h, name2h, kind):
    dur = s["duration_frames"]
    if s["is_transition"]:
        eid = "BLEND_AUDIO_DISSOLVE" if kind == "sound" else "EFF2_BLEND_DISSOLVE"
        return ("trans", eid, s["cutpoint"], dur, None)
    if kind == "picture" and not TITLE_OFF and s.get("is_effect"):
        t = _title_entry(s)
        if t is not None:
            return t
    if s["is_gap"] or s["disabled"] or s["is_effect"] or not s.get("mob_id"):
        return ("gap", None, 0, dur, None)
    h = m2h.get(s["mob_id"]) or name2h.get(_norm_name(s.get("clip_name")))
    if h is None or (_MASTER_LEN and _R(h) not in _MASTER_LEN):
        return ("gap", None, 0, dur, None)
    if kind == "picture" and s.get("has_motion_effect") and not MOTION_OFF:
        me = _motion_entry_from_seg(s, h)
        if me is not None: return me
    slot = s.get("source_slot_id") if kind == "sound" else 1
    src_start = s["source_start_frames"]
    if _OFFLINE_H and _R(h) in _OFFLINE_HEXES:
        src_start += _OFFLINE_H
    clip = ("clip", h, src_start, dur, slot)
    if kind == "sound":
        lv = _gain_level(s)
        if lv is not None:
            return ("gain", lv, clip, dur, None)
    return clip

def _tc_frames(tc, fps):
    p = (tc or "0:0:0:0").replace(";", ":").split(":")
    h, m, s, ff = (int(x) for x in (p + [0, 0, 0, 0])[:4])
    return (h * 3600 + m * 60 + s) * round(fps) + ff

def _position_row(segs, m2h, name2h, kind, fps):
    ents = [(_seg_to_entry(s, m2h, name2h, kind), s) for s in segs]
    _clip_like = ("clip", "motion", "gain", "title")
    pos = 0
    out = []
    pending_head_trim = 0
    for i, (e, s) in enumerate(ents):
        if e[0] == "trans":
            prev_clip = i > 0 and ents[i - 1][0][0] in _clip_like
            next_clip = i + 1 < len(ents) and ents[i + 1][0][0] in _clip_like
            prev_gap = i > 0 and ents[i - 1][0][0] == "gap"
            next_gap = i + 1 < len(ents) and ents[i + 1][0][0] == "gap"
            dur_t, cp = s["duration_frames"], s["cutpoint"]
            if not TRANS_OFF and prev_gap and next_clip and not prev_clip:
                clip_tc = _tc_frames(ents[i + 1][1]["timeline_tc_in"], fps)
                filler_end = clip_tc + dur_t
                if filler_end > pos:
                    out.append(("gap", None, 0, filler_end - pos, None)); pos = filler_end
                out.append(e); pos -= dur_t
                continue
            if not TRANS_OFF and prev_clip and next_gap and not next_clip:
                out.append(e); pos -= dur_t
                continue
            if prev_clip and next_clip:
                if TRANS_OFF and out and out[-1][0] in ("clip", "gain"):
                    trimmed, cut = _trim_tail(out[-1], max(0, dur_t - cp))
                    if cut:
                        out[-1] = trimmed
                        pos -= cut
                    pending_head_trim = cp
                else:
                    out.append(e); pos -= dur_t
            continue
        if e[0] not in _clip_like:
            continue
        seg_pos = _tc_frames(s["timeline_tc_in"], fps)
        if seg_pos > pos:
            out.append(("gap", None, 0, seg_pos - pos, None)); pos = seg_pos
        if pending_head_trim:
            e, _cut = _trim_head(e, pending_head_trim)
        pending_head_trim = 0
        out.append(e); pos += e[3]
    return out

def _clean_orphans(row):
    def is_clip(e): return e is not None and e[0] == "clip"
    out = []
    for i, e in enumerate(row):
        if e[0] == "trans":
            prev = out[-1] if out else None
            nxt = row[i + 1] if i + 1 < len(row) else None
            if not (is_clip(prev) and is_clip(nxt)):
                continue
        out.append(e)
    return out

def tracks_from_segments(segments, fps, m2h, name2h):
    from collections import defaultdict
    by_track = defaultdict(list)
    for s in segments:
        by_track[s["track"]].append(s)
    pic, snd = [], []
    for tnum in sorted(by_track):
        kind = "sound" if tnum >= 1000 else "picture"
        row = _position_row(by_track[tnum], m2h, name2h, kind, fps)
        (snd if kind == "sound" else pic).append((kind, row))
    return pic, snd

def tracks_from_relinker(path):
    _src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _src not in sys.path:
        sys.path.insert(0, _src)
    from aaf.parser import parse_aaf
    r = parse_aaf(path)
    return tracks_from_segments(r["segments"], r["timeline_fps"], _urn2hex_from_aaf(path), _load_media_map())

def _comp_mob_attrs(f, uc, L):
    if uc == 2:
        a = _attrs(f, _BOUNDS_REFORMATTING_OPTION=1, _START=0, _END=L, _COLOR_NONE=1, _USER_POS=L)
    elif uc == 4:
        a = _attrs(f, _EDIT_MODTIME=0, _IN=0, _OUT=L, _USER_POS=L)
    else:
        a = _attrs(f)
    a['_USER'] = f.create.Attributes()
    return a

def _author_comp_mob(f, d, tpl):
    import datetime
    L = d["length"]
    m = f.create.Composition(d["name"] or "", "CompositionMob"); m.name = d["name"]
    m.mob_id = AVBMobID(bytes_le=bytes.fromhex(d["mob_id_hex"]))
    m.edit_rate = ER; m.media_kind_id = 0; m.usage_code = d["usage_code"]; m.mob_type_id = 1
    m.length = L; m.mc_mode = 0; m.num_scalars = 0; m.param_list = None; m.descriptor = None
    m.attributes = _comp_mob_attrs(f, d["usage_code"], L)
    now = datetime.datetime.now()
    m.property_data["creation_time"] = now; m.property_data["last_modified"] = now
    m.tracks = []
    idx = {"picture": 0, "sound": 0}
    for (mk, tag, payload) in d["tracks"]:
        if tag == "seq":
            seq = f.create.Sequence(ER, mk); seq.components = [_build_one(f, e, mk, tpl) for e in payload]
            comp = seq
        else:
            comp = _build_one(f, payload, mk, tpl)
        idx[mk] += 1
        tk = f.create.Track(); tk.index = idx[mk]; tk.component = comp; tk.lock_number = 0
        m.tracks.append(tk)
    f.content.add_mob(m)
    return m

def _collect_clip_mobs(entry, acc):
    kind, a1, a2, length, slot_id = entry
    if kind == "clip": acc.add(a1)
    elif kind == "combiner":
        for (mh, *_r) in a1: acc.add(mh)
    elif kind == "awarp":
        for (mh, *_r) in a2[0]: acc.add(mh)
    elif kind == "selector":
        for (mh, *_r) in a1: acc.add(mh)
    elif kind == "mcam_sel":
        for (mh, *_r) in a1: acc.add(mh)
    elif kind == "gain":
        if a2 is not None: _collect_clip_mobs(a2, acc)
    elif kind == "motion" and a2:
        acc.add(a2[0])
    elif kind == "fx" and a2:
        _collect_clip_mobs(a2, acc)
    elif kind == "asplugin" and a2:
        _collect_clip_mobs(a2, acc)

def _collect_comp_mob_refs(d, acc):
    for (mk, tag, payload) in d["tracks"]:
        if tag == "seq":
            for e in payload: _collect_clip_mobs(e, acc)
        else:
            _collect_clip_mobs(payload, acc)

def _attrs(f, **kv):
    a = f.create.Attributes()
    for k, v in kv.items(): a[k] = v
    return a

def _filler(f, length, media="picture"):
    fl = f.create.Filler(ER, media); fl.length = length; return fl

def _trackref(f, media):
    tr = f.create.TrackRef(ER, media); tr.length = MAXLEN; tr.relative_scope = 0; tr.relative_track = -1; return tr

def _set_inner_clip(f, te, inner):
    if inner is None: return
    mob_hex, start, length = inner
    for tk in te.tracks:
        seq = getattr(tk, "component", None)
        if seq is None or type(seq).__name__ != "Sequence": continue
        sc = next((c for c in seq.components if type(c).__name__ == "SourceClip"), None)
        if sc is not None:
            sc.mob_id = AVBMobID(bytes_le=bytes.fromhex(_R(mob_hex)))
            sc.start_time = start; sc.length = length; sc.edit_rate = ER
        else:
            for c in seq.components:
                if type(c).__name__ in ("Filler", "ScopeReference") and getattr(c, "length", 0):
                    c.length = te.length

def _set_inner_component(f, comp_effect, inner):
    seq = comp_effect.tracks[0].component
    comps = list(seq.components)
    out = []; done = False
    for c in comps:
        if not done and type(c).__name__ != "Filler":
            out.append(inner); done = True
        else:
            out.append(c)
    if not done:
        out = [inner]
    seq.property_data["components"] = out

def _set_combiner_clips(f, te, clips, length):
    te.length = length; te.edit_rate = ER
    for i, st in enumerate(te.tracks):
        sc = st.component
        if type(sc).__name__ != "SourceClip" or i >= len(clips): continue
        mob_hex, chan, start, clen = clips[i]
        sc.mob_id = AVBMobID(bytes_le=bytes.fromhex(_R(mob_hex))); sc.track_id = chan
        sc.start_time = start; sc.length = clen; sc.edit_rate = ER

def _mk_pp(f, code, typ, value):
    pp = f.create.ParamControlPointProperty()
    pp.property_data["code"] = code; pp.property_data["type"] = typ; pp.property_data["value"] = value
    return pp

def _mk_speed_cp(f, kf, is_first):
    cp = f.create.ParamControlPoint()
    cp.property_data["offset"] = [int(round(kf["t"])), 1]
    cp.property_data["timescale"] = 1
    cp.property_data["value"] = float(kf["speed"])
    pp = []
    if is_first: pp.append(_mk_pp(f, 14, 2, 0.0))
    pp.append(_mk_pp(f, 5, 2, float(kf["in_dt"])))
    pp.append(_mk_pp(f, 6, 2, float(kf["in_dv"])))
    pp.append(_mk_pp(f, 7, 2, float(kf["out_dt"])))
    pp.append(_mk_pp(f, 8, 2, float(kf["out_dv"])))
    pp.append(_mk_pp(f, 9, 1, int(kf["mode"])))
    cp.property_data["pp"] = pp
    return cp

def _mk_offset_cp(f, t, val):
    cp = f.create.ParamControlPoint()
    num = int(round(t * 2))
    cp.property_data["offset"] = [num, 2] if num % 2 else [num // 2, 1]
    cp.property_data["timescale"] = 1
    cp.property_data["value"] = float(val)
    cp.property_data["pp"] = []
    return cp

def _set_dissolve_curve(te, length):
    for p in te.property_data.get("param_list") or []:
        ct = p.property_data.get("control_track")
        if ct is None:
            continue
        ct.property_data["length"] = length
        cps = ct.property_data.get("control_points") or []
        if len(cps) >= 2:
            off = cps[-1].property_data.get("offset")
            if isinstance(off, list) and off:
                cps[-1].property_data["offset"] = [length] + list(off[1:])

def _set_motion_speed(f, me, kfs, length):
    if not kfs: return
    pl = me.property_data.get("param_list")
    if not pl or len(pl) < 2: return
    sp_ct = pl[0].property_data.get("control_track")
    off_ct = pl[1].property_data.get("control_track")
    if len(kfs) == 1:
        speed = kfs[0]["speed"]
        if sp_ct is not None:
            sp_ct.property_data["length"] = length
            cps = sp_ct.property_data.get("control_points") or []
            if cps:
                cps[0].property_data["value"] = float(speed)
                for pp in cps[0].property_data.get("pp", []) or []:
                    if pp.property_data.get("code") == 5: pp.property_data["value"] = -length / 10.0
                    elif pp.property_data.get("code") == 7: pp.property_data["value"] = length / 10.0
        if off_ct is not None:
            off_ct.property_data["length"] = length
            ocps = off_ct.property_data.get("control_points") or []
            if len(ocps) >= 2:
                ocps[0].property_data["value"] = 0.0
                ocps[-1].property_data["value"] = float(round(abs(speed) * length))
                off = ocps[-1].property_data.get("offset")
                if isinstance(off, list) and off: ocps[-1].property_data["offset"] = [length] + list(off[1:])
    else:
        if sp_ct is not None:
            sp_ct.property_data["length"] = length
            sp_ct.property_data["control_points"] = [_mk_speed_cp(f, kf, i == 0) for i, kf in enumerate(kfs)]
        if off_ct is not None:
            off_ct.property_data["length"] = length
            off_ct.property_data["control_points"] = [_mk_offset_cp(f, t, v) for t, v in _offset_map(kfs, length)]
    if len(pl) > 2:
        ct2 = pl[2].property_data.get("control_track")
        if ct2 is not None: ct2.property_data["length"] = length

_VARYING = object()
GEOM_BLEND_FX = {"EFF2_SBLEND", "EFF2_BLEND_RESIZE", "EFF2_BLEND_PIP", "EFF2_RGB_COLOR_CORRECTION",
                 "E4FE520A-B815-447F-A969-2D5E1C5F6C14"}

def _parse_avup(blob):
    try: b = bytes(blob)
    except Exception: return None
    if len(b) < 40 or b[0:4] != b'PUVA': return None
    try:
        uid = uuid.UUID(bytes_le=b[16:32])
        size2 = int.from_bytes(b[36:40], "little")
        return uid, b[40:40 + size2]
    except Exception:
        return None

TITLE_EID = "E4EEBB07-AFAA-4122-BFA8-0A838BB40A8C"
TITLE_MQP_UUID = "67a614ac-29b6-4afe-b9cc-8107a5dd3468"
TITLE_OFF = os.environ.get("AVB_NO_TITLE") == "1"

def _avup_at_puva(blob):
    try: b = bytes(blob)
    except Exception: return None
    off = b.find(b'PUVA')
    if off < 0 or len(b) - off < 40: return None
    try:
        uid = uuid.UUID(bytes_le=b[off + 16:off + 32])
        size2 = int.from_bytes(b[off + 36:off + 40], "little")
        return uid, b[off + 40:off + 40 + size2]
    except Exception:
        return None

def _build_title(f, mqp_hex, text, length, tkind, tpl):
    if TITLE_EID not in tpl:
        return _filler(f, length, tkind)
    te = materialize(f, tpl[TITLE_EID]); te.length = length; te.edit_rate = ER
    for tk in te.tracks:
        seq = getattr(tk, "component", None)
        if seq is not None:
            seq.property_data["components"] = [_filler(f, length, tkind)]
    a = te.property_data.get("attributes")
    if a is not None and text is not None:
        try: a["_TTEXT"] = bytearray(b"\x00\x00T+: " + text.encode("utf-8") + b" \x00")
        except Exception: pass
    if mqp_hex:
        parsed = _avup_at_puva(bytes.fromhex(mqp_hex))
        if parsed is not None:
            for it in te.property_data.get("param_list") or []:
                if str(it.property_data.get("uuid")) == TITLE_MQP_UUID:
                    cf = it.property_data.get("value")
                    if type(cf).__name__ == "CFUserParam":
                        cf.property_data["data"] = bytearray(parsed[1])
    return te

def _title_entry(s):
    for e in (s.get("effects") or []):
        if e.get("category") == "title":
            p = e.get("params") or {}
            if p.get("mqp_hex"):
                return ("title", p["mqp_hex"], p.get("text"), s["duration_frames"], None)
    return None

def _override_params(te, aaf_params, effect_id=None):
    pl = te.property_data.get("param_list")
    if not pl or not aaf_params: return
    for it in pl:
        u = str(it.property_data.get("uuid"))
        if u not in aaf_params:
            continue
        val = aaf_params[u]
        if val is _VARYING:
            continue
        vt = it.property_data.get("value_type")
        try:
            if vt == 2:   it.property_data["value"] = float(val)
            elif vt == 1: it.property_data["value"] = int(bool(val)) if isinstance(val, bool) else int(val)
            elif vt == 4:
                cf = it.property_data.get("value")
                parsed = _parse_avup(val)
                if parsed is not None and type(cf).__name__ == "CFUserParam":
                    cf.property_data["uuid"] = parsed[0]
                    cf.property_data["data"] = bytearray(parsed[1])
        except (TypeError, ValueError):
            pass
    if effect_id in GEOM_BLEND_FX:
        for i in range(len(pl) - 1, -1, -1):
            if str(pl[i].property_data.get("uuid")) not in aaf_params:
                del pl[i]

def _build_selector(f, tpl, chans, selected, length, tkind):
    if not chans:
        return _filler(f, length, tkind)
    sel = materialize(f, tpl["__selector__"]); sel.length = length; sel.edit_rate = ER
    selected = max(0, min(selected or 0, len(chans) - 1))
    sel.property_data["selected"] = selected; sel.property_data["is_ganged"] = True
    tks = []
    for i, (mob_hex, chan, start, clen) in enumerate(chans):
        sc = f.create.SourceClip(ER, tkind)
        sc.mob_id = AVBMobID(bytes_le=bytes.fromhex(_R(mob_hex))); sc.track_id = chan
        sc.start_time = start; sc.length = length
        tk = f.create.Track(); tk.index = i + 1; tk.component = sc; tk.lock_number = 0
        tks.append(tk)
    sel.tracks = tks
    a = sel.property_data.get("attributes")
    if a is not None:
        try: a["_AAF_SELECTED"] = selected
        except Exception: pass
    return sel

def _build_one(f, entry, tkind, tpl):
    kind, a1, a2, length, slot_id = entry
    if kind == "clip":
        sc = f.create.SourceClip(ER, tkind)
        sc.mob_id = AVBMobID(bytes_le=bytes.fromhex(_R(a1)))
        sc.track_id = _chan(a1, slot_id) if tkind == "sound" else (slot_id or 1)
        sc.start_time = a2; sc.length = length
        return sc
    if kind == "gap":
        return _filler(f, length, tkind)
    if kind == "title":
        return _build_title(f, a1, a2, length, tkind, tpl)
    if kind == "asplugin":
        key = "__asp__" + str(a1)
        if key not in tpl:
            return _build_one(f, a2, tkind, tpl) if a2 is not None else _filler(f, length, tkind)
        te = materialize(f, tpl[key]); te.length = length; te.edit_rate = ER
        if a2 is not None:
            _set_inner_component(f, te, _build_one(f, a2, tkind, tpl))
        chunk, preset = slot_id if slot_id else (None, None)
        pls = te.property_data.get("plugins")
        if chunk is not None and pls:
            chs = pls[0].property_data.get("chunks")
            if chs: chs[0].property_data["data"] = bytearray(chunk)
            attrs = te.property_data.get("attributes")
            if attrs is not None:
                for k in list(attrs.keys()):
                    if "ALL_CHUNKS" in str(k):
                        old = bytes(attrs[k])
                        if len(old) >= len(chunk):
                            attrs[k] = bytearray(old[:len(old) - len(chunk)] + chunk)
        if preset is not None:
            te.property_data["preset_path"] = bytearray(preset)
        return te
    if kind == "combiner":
        te = materialize(f, tpl["EFF2_AUDIO_CHANNEL_COMBINER"]); _set_combiner_clips(f, te, a1, length); return te
    if kind == "awarp":
        me = materialize(f, tpl["EFF_TIMEWARP.AUDIO_TIME_WARP"]); me.length = length; me.edit_rate = ER
        if a1: me.speed_ratio = a1
        comb_clips, comb_len, inner_entry = a2
        inner = me.tracks[0].component
        if type(inner).__name__ == "TrackEffect" and comb_clips:
            _set_combiner_clips(f, inner, comb_clips, comb_len)
        elif inner_entry is not None:
            me.tracks[0].component = _build_one(f, inner_entry, tkind, tpl)
        return me
    if kind == "trans":
        te = materialize(f, tpl[a1]); te.length = length; te.cutpoint = a2; te.edit_rate = ER
        _set_dissolve_curve(te, length); return te
    if kind == "motion":
        sr, kfs = a1
        me = materialize(f, tpl["EFF_ADV_MOTION_CTL"]); me.length = length; me.edit_rate = ER
        if sr: me.speed_ratio = sr
        _set_motion_speed(f, me, kfs, length); _set_inner_clip(f, me, a2); return me
    if kind == "selector":
        return _build_selector(f, tpl, a1, a2, length, tkind)
    if kind == "mcam_sel":
        if not a1:
            return _filler(f, length, tkind)
        a2 = max(0, min(a2 or 0, len(a1) - 1))
        sel = materialize(f, tpl["__selector__"]); sel.length = length; sel.edit_rate = ER
        sel.property_data["selected"] = a2; sel.property_data["is_ganged"] = True
        sel.property_data["media_kind_id"] = 1 if tkind == "picture" else 2
        tks = []
        for i, (cont_hex, track_id, start, _cl) in enumerate(a1):
            sc = f.create.SourceClip(ER, tkind)
            sc.mob_id = AVBMobID(bytes_le=bytes.fromhex(cont_hex)); sc.track_id = track_id
            sc.start_time = start; sc.length = length
            tk = f.create.Track(); tk.index = i + 1; tk.component = sc; tk.lock_number = 0
            tks.append(tk)
        sel.tracks = tks
        a_attrs = sel.property_data.get("attributes")
        if a_attrs is not None:
            try: a_attrs["_AAF_SELECTED"] = a2
            except Exception: pass
        return sel
    if kind == "gain":
        pv = materialize(f, tpl["__panvol__"]); pv.length = length; pv.edit_rate = ER
        pd = pv.property_data
        if a1 is not None: pd["level"] = a1; pd["level_set"] = True
        pd["pan"] = pd.get("pan", 16384); pd["pan_set"] = False
        inner = _build_one(f, a2, tkind, tpl) if a2 is not None else None
        if inner is not None:
            tk = f.create.Track(); tk.index = 1; tk.component = inner; tk.lock_number = 0
            pv.tracks = [tk]
            if type(inner).__name__ == "Selector":
                ia = inner.property_data.get("attributes")
                if ia is not None and a1 is not None:
                    try: ia["_EFFECT_ANCILLARY_SIGNATURE"] = bytearray(str(a1).encode())
                    except Exception: pass
        return pv
    if a1 not in tpl:
        return _build_one(f, a2, tkind, tpl) if a2 is not None else _filler(f, length, tkind)
    te = materialize(f, tpl[a1]); te.length = length; te.edit_rate = ER
    if a2 is not None and a2[0] == "clip":
        _set_inner_clip(f, te, (a2[1], a2[2], a2[3]))
    elif a2 is not None:
        _set_inner_component(f, te, _build_one(f, a2, tkind, tpl))
    _override_params(te, slot_id, a1); return te

def build(f, name, tracks, tpl, tc_fps, fmt_type, fmt_str, comp_mobs=None):
    def tlen(tr): return sum(c[3] for c in tr if c[0] != "trans") - sum(c[3] for c in tr if c[0] == "trans")
    total = max((tlen(tr) for _k, tr in tracks), default=0)
    if comp_mobs:
        authored = {}
        for mh, d in comp_mobs.items():
            authored[mh] = _author_comp_mob(f, d, tpl)
        for h, d in comp_mobs.items():
            if d["usage_code"] != 4: continue
            refs = set(); _collect_comp_mob_refs(d, refs)
            for c in refs:
                if c in authored and comp_mobs.get(c, {}).get("usage_code") == 5:
                    mr = f.create.MobRef()
                    mr.property_data["position"] = 0
                    mr.property_data["mob_id"] = AVBMobID(bytes_le=bytes.fromhex(h))
                    authored[c].attributes["_MATCH"] = mr
    comp = f.create.Composition(name, "CompositionMob"); comp.name = name
    comp.mob_id = AVBMobID(bytes_le=bytes(bytearray(COMP_PREFIX) + os.urandom(16)))
    comp.edit_rate = ER; comp.media_kind_id = 0; comp.usage_code = 0
    comp.length = total; comp.mc_mode = 0; comp.num_scalars = 0; comp.param_list = None
    a = _attrs(f, _VERSION=2); a['SEQUERNCE_FORMAT_TYPE'] = fmt_type; a['SEQUERNCE_FORMAT_STRING'] = fmt_str
    a['_USER'] = f.create.Attributes(); a['_USER_POS'] = total
    comp.attributes = a
    comp.session_attrs = _attrs(f, _VIS_START=0, _VIS_END=2147483647)
    comp.tracks = []
    idx = {"picture": 0, "sound": 0}
    has_picture = any(k == "picture" for k, _ in tracks)
    for tkind, tr in tracks:
        seq = f.create.Sequence(ER, tkind); seq.components = []
        if not tr or tr[0][0] != "gap":
            seq.components.append(_filler(f, 0, tkind))
        for entry in tr:
            if entry[0] == "trans" and TRANS_OFF:
                continue
            pad = 0
            clampable = entry if entry[0] == "clip" else (
                entry[2] if entry[0] == "gain" and entry[2] and entry[2][0] == "clip" else None)
            if clampable is not None and _MASTER_LEN:
                ml = _MASTER_LEN.get(_R(clampable[1]))
                if ml is not None and clampable[2] + clampable[3] > ml:
                    pad = clampable[2] + clampable[3] - ml
                    new_clip = (clampable[0], clampable[1], clampable[2], max(1, clampable[3] - pad), clampable[4])
                    if entry[0] == "clip":
                        entry = new_clip
                    else:
                        entry = (entry[0], entry[1], new_clip, max(1, entry[3] - pad), entry[4])
            seq.components.append(_build_one(f, entry, tkind, tpl))
            if pad > 0:
                seq.components.append(_filler(f, pad, tkind))
        seq.components.append(_filler(f, max(0, total - tlen(tr)), tkind))
        idx[tkind] += 1
        vt = f.create.Track(); vt.index = idx[tkind]; vt.component = seq; vt.lock_number = 0
        vt.filler_proxy = _trackref(f, tkind)
        if tkind == "sound":
            vt.session_attr = _attrs(f, _MONSTATE=2, _ENBLSTATE=1)
            chans = 1; has_gain = False
            for kind, a1, a2, _l, _s in tr:
                if kind == "combiner": chans = max(chans, len(a1))
                elif kind == "awarp":  chans = max(chans, len(a2[0]))
                elif kind == "gain":   has_gain = True
            if has_gain:
                vt.session_attr["_TRK_CLIP_G"] = 1
            if chans > 1:
                vt.attributes = _attrs(f, _TRACK_FORMAT=chans)
            else:
                vt.attributes = _attrs(f, AudioMixerCompSolo=0, AudioMixerCompMute=0)
        else:
            vt.session_attr = _attrs(f, _ENBLSTATE=0, _MONSTATE=0)
        comp.tracks.append(vt)
    if not has_picture:
        pseq = f.create.Sequence(ER, "picture"); pseq.components = [_filler(f, total, "picture")]
        pt = f.create.Track(); pt.index = 1; pt.component = pseq; pt.lock_number = 0
        pt.filler_proxy = _trackref(f, "picture"); pt.session_attr = _attrs(f, _ENBLSTATE=1, _MONSTATE=1)
        comp.tracks.append(pt)
    tc = f.create.Timecode(ER, "timecode"); tc.length = total; tc.flags = 0; tc.fps = tc_fps; tc.start = 3600 * tc_fps
    tct = f.create.Track(); tct.index = 1; tct.component = tc; tct.lock_number = 0; tct.session_attr = _attrs(f, _ENBLSTATE=0)
    comp.tracks.append(tct)
    dseq = f.create.Sequence(ER, "DescriptiveMetadata"); dseq.components = [_filler(f, total, "DescriptiveMetadata")]
    dt = f.create.Track(); dt.index = 1; dt.component = dseq; dt.lock_number = 0; dt.filler_proxy = _trackref(f, "DescriptiveMetadata")
    comp.tracks.append(dt)
    f.content.add_mob(comp)
    return comp

def _fix_component(f, c, present, cnt):
    cn = type(c).__name__
    if cn == "SourceClip":
        h = c.mob_id.bytes_le.hex()
        if h not in present and "7f7f2a80" not in h:
            cnt[0] += 1
            mk = str(getattr(c, "media_kind", "sound")).lower()
            return _filler(f, getattr(c, "length", 0) or 0, "picture" if "pic" in mk else "sound")
        return c
    comps = getattr(c, "components", None)
    if isinstance(comps, list):
        for i, x in enumerate(comps):
            comps[i] = _fix_component(f, x, present, cnt)
    tks = getattr(c, "tracks", None)
    if isinstance(tks, list):
        for tk in tks:
            comp = getattr(tk, "component", None)
            if comp is not None:
                tk.component = _fix_component(f, comp, present, cnt)
    return c

def _fix_leaks(f):
    present = {m.mob_id.bytes_le.hex() for m in f.content.mobs}
    cnt = [0]
    for m in f.content.mobs:
        if getattr(m, "mob_type", None) != "CompositionMob":
            continue
        for tk in getattr(m, "tracks", []) or []:
            comp = getattr(tk, "component", None)
            if comp is not None:
                tk.component = _fix_component(f, comp, present, cnt)
    return cnt[0]

def build_templates_json(out_path=None):
    out_path = out_path or TEMPLATES_JSON
    tpl = _capture_from_gold()
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(tpl, fp)
    return tpl

def _rate_pair(rate, timeline_fps):
    if rate:
        return tuple(rate)
    from fractions import Fraction
    known = {23.976: (24000, 1001), 29.97: (30000, 1001), 59.94: (60000, 1001),
             24.0: (24, 1), 25.0: (25, 1), 30.0: (30, 1), 50.0: (50, 1), 60.0: (60, 1)}
    if timeline_fps is not None:
        r = known.get(round(float(timeline_fps), 3))
        if r:
            return r
        fr = Fraction(float(timeline_fps)).limit_denominator(1001)
        return (fr.numerator, fr.denominator)
    return (30000, 1001)

def _emit(f, tracks, rate, comp_mobs, seqname, fix_leaks):
    global ER
    ER, tc_fps, fmt_type, fmt_str = resolve_rate(*rate)
    tpl = capture_templates()
    c = build(f, seqname, tracks, tpl, tc_fps, fmt_type, fmt_str, comp_mobs)
    nleak = _fix_leaks(f) if fix_leaks else 0
    f.content.uid = random.getrandbits(63)
    return c, nleak

def build_avb(segments, timeline_fps, media_bin, out, seqname,
              aaf_path=None, remap=None, media_map=None, rate=None, fix_leaks=True):
    set_remap(remap)
    m2h = _urn2hex_from_aaf(aaf_path) if aaf_path else {}
    name2h = {}
    if media_map:
        for v in media_map.values():
            if isinstance(v, dict) and v.get("name"):
                name2h[_norm_name(v["name"])] = v["mob_id"]
    from .reader import topen as _topen
    ml = {}
    with _topen(media_bin) as rf:
        for m in rf.content.mobs:
            if m.property_data.get("mob_type_id") != 2:
                continue
            L = 0
            for t in m.property_data.get("tracks") or []:
                seq = t.property_data.get("component")
                if seq is not None:
                    L = max(L, sum((c.property_data.get("length", 0) or 0)
                                   for c in (seq.property_data.get("components") or [])))
            ml[m.mob_id.bytes_le.hex()] = L
    set_master_len(ml)
    offline_hexes = {v.get("mob_id") for k, v in (media_map or {}).items()
                     if isinstance(k, str) and k.startswith("offline:") and isinstance(v, dict) and v.get("mob_id")}
    set_offline_handles(offline_hexes, offline_handle_frames(segments) if offline_hexes else 0)
    pic, snd = tracks_from_segments(segments, timeline_fps, m2h, name2h)
    tracks = pic + snd
    with avb.open(media_bin) as f:
        c, nleak = _emit(f, tracks, _rate_pair(rate, timeline_fps), {}, seqname, fix_leaks)
        f.write(out)
    return {"name": c.name, "length": c.length, "tracks": len(c.tracks),
            "picture_tracks": len(pic), "sound_tracks": len(snd), "leaks_filled": nleak, "output": out}

def build_avb_from_aaf(aaf_in, media_bin, out, seqname, use_relinker=None, fix_leaks=None):
    if use_relinker is None:
        use_relinker = os.environ.get("AVB_RELINKER_AAF") == "1"
    if fix_leaks is None:
        fix_leaks = os.environ.get("AVB_FIX_LEAKS") == "1"
    name, tracks, rate, comp_mobs = read_aaf(aaf_in)
    if use_relinker:
        pic, snd = tracks_from_relinker(aaf_in)
        tracks = pic + snd
        comp_mobs = {}
        print("front-end RELINKER: %d trilhas de vídeo + %d de áudio (resolvidas aos originais)" % (len(pic), len(snd)))
    with avb.open(media_bin) as f:
        c, nleak = _emit(f, tracks, rate, comp_mobs, seqname, fix_leaks)
        f.write(out)
    print("comp: %s len=%s tracks=%d | leaks->filler=%d -> %s" % (c.name, c.length, len(c.tracks), nleak, out))
    return {"name": c.name, "length": c.length, "tracks": len(c.tracks), "leaks_filled": nleak, "output": out}

if __name__ == "__main__":
    if _BUILD:
        _t = build_templates_json()
        print("templates.json gravado: %d templates -> %s" % (len(_t), TEMPLATES_JSON))
        print("  chaves:", list(_t.keys()))
        sys.exit(0)
    build_avb_from_aaf(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
