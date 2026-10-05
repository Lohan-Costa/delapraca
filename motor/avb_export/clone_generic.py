#!/usr/bin/env python3
import os, sys, copy, struct, subprocess

from . import reader as avb_read
from . import clone as AC
from . import relink as AR
from . import offsets as O
from . import descriptor_patch as DP
from . import video_descriptor as VD
from media import fftools


def _find_timecode(f, clip_name):
    def pd(o): return getattr(o, "property_data", {}) or {}
    def walk(comp, d=0):
        if comp is None or d > 6: return []
        r = [comp] if type(comp).__name__ == "Timecode" else []
        for c in pd(comp).get("components", []): r += walk(c, d + 1)
        for t in pd(comp).get("tracks", []): r += walk(pd(t).get("component"), d + 1)
        return r
    for m in f.content.mobs:
        if (pd(m).get("name") or "") != clip_name: continue
        for t in pd(m).get("tracks", []):
            for tc in walk(pd(t).get("component")):
                return tc.instance_id, pd(tc).get("start"), pd(tc).get("fps")
    return None, None, None


def _find_audio_start(f, clip_name):
    def pd(o): return getattr(o, "property_data", {}) or {}
    scs = []
    def walk(c, d=0):
        if c is None or d > 7: return
        if type(c).__name__ == "SourceClip" and pd(c).get("start_time"):
            scs.append(c)
        for x in pd(c).get("components", []): walk(x, d + 1)
        for t in pd(c).get("tracks", []): walk(pd(t).get("component"), d + 1)
    for m in f.content.mobs:
        if (pd(m).get("name") or "") != clip_name:
            continue
        for t in pd(m).get("tracks", []):
            walk(pd(t).get("component"))
    if not scs:
        return None, None, []
    st = pd(scs[0]).get("start_time")
    er = float(pd(scs[0]).get("edit_rate") or 24)
    iids = [c.instance_id for c in scs if pd(c).get("start_time") == st]
    return st, er, iids


def _own_track_sourceclips(mob):
    def pd(o): return getattr(o, "property_data", {}) or {}
    out = []
    for t in pd(mob).get("tracks", []):
        c = pd(t).get("component")
        for x in pd(c).get("components", []) or []:
            if type(x).__name__ == "SourceClip":
                out.append((x.instance_id, x.property_data.get("length"), float(x.property_data.get("edit_rate") or 0)))
    return out


def _probe_bwf_start(path, edit_rate):
    try:
        o = __import__("json").loads(subprocess.check_output([fftools.ffprobe_exe(), "-v", "error", "-select_streams",
             "a:0", "-show_entries", "format_tags=time_reference:stream=sample_rate", "-of", "json", path]).decode())
        tr = (o.get("format", {}).get("tags", {}) or {}).get("time_reference")
        sr = float(o["streams"][0]["sample_rate"])
        if tr and str(tr).isdigit():
            nominal = int(round(edit_rate))
            tc_frames = round(int(tr) / sr * nominal)
            factor = 1000.0 / 1001.0 if abs(edit_rate - nominal) > 0.005 else 1.0
            return int(round(tc_frames * factor))
    except Exception:
        pass
    return None


def _tc_to_frames(tcstr, fps):
    p = [int(x) for x in tcstr.replace(";", ":").split(":")]
    h, m, s, fr = (p + [0, 0, 0, 0])[:4]
    return (h * 3600 + m * 60 + s) * fps + fr


def _probe_start_tc(path, fps):
    try:
        o = subprocess.check_output([fftools.ffprobe_exe(), "-v", "error", "-show_entries",
             "format_tags=timecode:stream_tags=timecode", "-of", "default=nw=1:nk=1", path]).decode()
        tc = o.strip().split("\n")[0].strip()
        return _tc_to_frames(tc, fps) - 2 if tc else None
    except Exception:
        return None


def _target_geometry(tpl_pd, meta, ext):
    class Shim:
        def __init__(s, pd): s.property_data = pd
    sp = {k: ([list(p) if isinstance(p, list) else p for p in v] if isinstance(v, list) else v)
          for k, v in tpl_pd.items() if k not in ("attributes", "locator", "physical_media", "uuid")}
    sh = Shim(copy.deepcopy(sp))
    sh.property_data["attributes"] = {"_SD_AVC_SUB_DESCRIPTOR": {"ATN_AVCLevel": 40, "ATN_AVCProfile": 100}}
    VD.patch_cdci_geometry(sh, meta, ext=ext)
    return sh.property_data


def _audio_samples(path):
    o = subprocess.check_output([fftools.ffprobe_exe(), "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=duration_ts,nb_samples,duration,sample_rate", "-of", "json", path]).decode()
    s = __import__("json").loads(o)["streams"][0]
    sr = float(s["sample_rate"])
    for k in ("duration_ts", "nb_samples"):
        v = s.get(k)
        if v and str(v).isdigit() and int(v) > 0:
            return int(v), sr
    return int(round(float(s["duration"]) * sr)), sr


def _clone_audio_chain(raw, pos, info, target_file, tpl_desc_len, tpl_start=None, edit_rate=24.0,
                       template_bin=None, sclp_iids=None, tpl_sr=48000.0, master_len_min=None):
    import math
    samples, target_sr = _audio_samples(target_file)
    ratio = (info["length"] / tpl_desc_len) if tpl_desc_len else 0
    master_len = int(math.ceil(samples * ratio * (tpl_sr / target_sr))) if ratio else info["length"]
    if master_len_min and master_len_min > master_len:
        master_len = int(master_len_min)
    mt = int(os.stat(target_file).st_mtime)
    rngs = AC.ranges_for(pos, bytes(raw), info["indices"])
    seen = set()
    for blle in info["mobids"]:
        material = bytes(blle[16:32])
        if material in seen:
            continue
        seen.add(material)
        r_old, r_new = AC.region(material), AC.region(os.urandom(16))
        if raw.count(r_old):
            raw[:] = raw.replace(r_old, r_new)
    AC.scoped_replace_u32(raw, rngs, info["length"], master_len)
    AC.scoped_replace_u32(raw, rngs, tpl_desc_len, samples)
    AC.scoped_replace_u32(raw, rngs, info["date"], mt)
    new_start = None
    if os.environ.get("AVB_AUDIO_TC") == "1" and tpl_start is not None and template_bin and sclp_iids:
        new_start = _probe_bwf_start(target_file, edit_rate)
        if new_start is not None and new_start != tpl_start:
            for iid in sclp_iids:
                log = O.map_object(template_bin, iid)
                for off in (O.find_field(log, tpl_start, kind="read_s32le") or O.find_field(log, tpl_start)):
                    struct.pack_into("<i", raw, pos[iid] + 8 + off, new_start)
    print("  [audio] samples=%d sr=%.0f master_len=%d date=%d start=%s" % (samples, target_sr, master_len, mt, new_start))
    return samples, target_sr


def _patch_pcma_rate(raw, template_bin, iid, base, pcma_pd, target_sr):
    tpl_sr = int(round(float(pcma_pd.get("sample_rate") or 0)))
    if not tpl_sr or int(round(target_sr)) == tpl_sr:
        return 0
    log = O.map_object(template_bin, iid)
    n = 0
    for off in O.find_field(log, tpl_sr, kind="read_s32le"):
        struct.pack_into("<i", raw, base + off, int(round(target_sr))); n += 1
    tpl_avg = int(pcma_pd.get("average_bps") or 0)
    ba = int(pcma_pd.get("block_align") or 0)
    if tpl_avg and ba:
        for off in O.find_field(log, tpl_avg, kind="read_u32le"):
            struct.pack_into("<I", raw, base + off, ba * int(round(target_sr))); n += 1
    return n


def clone_generic(template_bin, clip_name, target_file, out_bin, kind="video", fps=None,
                  master_len_min=None):
    want_cls = "PCMADescriptor" if kind == "audio" else "CDCIDescriptor"
    with avb_read.topen(template_bin) as f:
        pos = list(f.object_positions)
        info = AC.clip_info(f, clip_name)
        cdci = old_path = pcma_len = None
        mpga = None
        for m in f.content.mobs:
            if (m.property_data.get("name") or "") != clip_name:
                continue
            d = m.property_data.get("descriptor")
            if d is not None and type(d).__name__ == want_cls:
                cdci = (d.instance_id, dict(d.property_data))
                pcma_len = d.property_data.get("length")
                phys = d.property_data.get("physical_media")
                if phys is not None:
                    loc = phys.property_data.get("locator")
                    old_path = loc.property_data.get("path_posix") if loc else None
            if kind != "audio" and d is not None and type(d).__name__ == "MPGADescriptor":
                mpga = (d.instance_id, dict(d.property_data), _own_track_sourceclips(m))
        tc_iid, tc_start, tc_fps = _find_timecode(f, clip_name)
        aud_start, aud_er, sclp_iids = _find_audio_start(f, clip_name) if kind == "audio" else (None, None, [])
    raw = bytearray(open(template_bin, "rb").read())
    npatch = 0
    if kind == "audio":
        tpl_sr = float(cdci[1].get("sample_rate") or 48000) if cdci else 48000.0
        _samples, target_sr = _clone_audio_chain(raw, pos, info, target_file, pcma_len, aud_start,
                           fps or aud_er or 24.0, template_bin=template_bin, sclp_iids=sclp_iids,
                           tpl_sr=tpl_sr, master_len_min=master_len_min)
        if cdci is not None:
            npatch = _patch_pcma_rate(raw, template_bin, cdci[0], pos[cdci[0]] + 8, cdci[1], target_sr)
    else:
        F = AC.clone_one(raw, pos, bytes(raw), info, target_file, do_name=False)
        if cdci is not None:
            meta = VD.probe_video(target_file)
            tgt = _target_geometry(cdci[1], meta, os.path.splitext(target_file)[1])
            log = O.map_object(template_bin, cdci[0])
            patches = DP.plan_video_geometry(log, cdci[1], tgt)
            DP.apply_patches(raw, pos[cdci[0]] + 8, patches)
            npatch = len(patches)
        if mpga is not None and F:
            desc_iid, desc_pd, own_clips = mpga
            for sc_iid, old_len, _er in own_clips:
                if old_len is None:
                    continue
                log = O.map_object(template_bin, sc_iid)
                offs = O.find_field(log, old_len)
                if offs:
                    struct.pack_into("<I", raw, pos[sc_iid] + 8 + offs[0], F)
            desc_len = desc_pd.get("length")
            sr = float(desc_pd.get("sample_rate") or 0)
            video_er = next((er for _i, _l, er in own_clips if er), None) or tc_fps or 23.976
            if desc_len and sr:
                import math
                new_desc_len = max(1, int(math.ceil(F / video_er * sr)))
                if new_desc_len != desc_len:
                    log = O.map_object(template_bin, desc_iid)
                    offs = O.find_field(log, desc_len)
                    if offs:
                        struct.pack_into("<I", raw, pos[desc_iid] + 8 + offs[0], new_desc_len)
    tc_patched = False
    if tc_iid is not None and tc_start is not None:
        new_start = _probe_start_tc(target_file, tc_fps or 24)
        if new_start is not None and new_start != tc_start:
            tclog = O.map_object(template_bin, tc_iid)
            offs = O.find_field(tclog, tc_start)
            if offs:
                struct.pack_into("<i", raw, pos[tc_iid] + 8 + offs[0], new_start)
                tc_patched = True
    changes = []
    base = os.path.basename(target_file)
    newname = base if kind == "audio" else os.path.splitext(base)[0]
    if newname != clip_name:
        out, _ = AR.replace_field(bytes(raw), clip_name, newname); raw = bytearray(out)
    if old_path:
        out, changes = AR.relink(bytes(raw), mapping={old_path: os.path.abspath(target_file)})
        raw = bytearray(out)
    open(out_bin, "wb").write(raw)
    return {"clip": clip_name, "target": os.path.basename(target_file),
            "geometry_patches": npatch, "tc_patched": tc_patched, "path_changes": len(changes), "out": out_bin}


def clone_offline(template_bin, clip_name, out_bin, new_name, master_len, kind="video", locator=None, fps=None):
    with avb_read.topen(template_bin) as f:
        pos = list(f.object_positions)
        info = AC.clip_info(f, clip_name)
        old_path = None
        pcma_len = pcma_sr = None
        for m in f.content.mobs:
            if (m.property_data.get("name") or "") != clip_name:
                continue
            d = m.property_data.get("descriptor")
            if d is not None:
                phys = d.property_data.get("physical_media")
                loc = (phys.property_data.get("locator") if phys else None) or d.property_data.get("locator")
                if loc:
                    old_path = loc.property_data.get("path_posix")
                if kind == "audio" and type(d).__name__ == "PCMADescriptor":
                    pcma_len = d.property_data.get("length")
                    pcma_sr = float(d.property_data.get("sample_rate") or 48000)
        _aud_start, aud_er, _sclp_iids = _find_audio_start(f, clip_name) if kind == "audio" else (None, None, [])
    raw = bytearray(open(template_bin, "rb").read())
    rngs = AC.ranges_for(pos, bytes(raw), info["indices"])
    seen = set()
    for blle in info["mobids"]:
        material = bytes(blle[16:32])
        if material in seen:
            continue
        seen.add(material)
        r_old, r_new = AC.region(material), AC.region(os.urandom(16))
        if raw.count(r_old):
            raw[:] = raw.replace(r_old, r_new)
    AC.scoped_replace_u32(raw, rngs, info["length"], max(1, int(master_len)))
    if pcma_len is not None:
        import math
        er = fps or aud_er or 24.0
        new_samples = max(1, int(math.ceil(master_len / er * pcma_sr)))
        AC.scoped_replace_u32(raw, rngs, pcma_len, new_samples)
    if new_name != clip_name:
        out, _ = AR.replace_field(bytes(raw), clip_name, new_name); raw = bytearray(out)
    ext = os.path.splitext(old_path or "")[1] or (".wav" if kind == "audio" else ".mp4")
    loc = locator or ("/AVB_OFFLINE/" + new_name + (ext if not new_name.lower().endswith(ext) else ""))
    if old_path:
        out, _ = AR.relink(bytes(raw), mapping={old_path: loc}); raw = bytearray(out)
    open(out_bin, "wb").write(raw)
    return out_bin
