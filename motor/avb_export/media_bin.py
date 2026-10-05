#!/usr/bin/env python3
import os, sys, json, random, tempfile, subprocess

from . import reader as avb_read
from . import clone_generic as CG
from .merge import serialize, materialize
from media import fftools

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
MANIFEST = os.path.join(TEMPLATE_DIR, "manifest.json")


def load_bundled_templates(template_dir=None):
    tdir = template_dir or TEMPLATE_DIR
    man = json.load(open(os.path.join(tdir, "manifest.json"), encoding="utf-8"))["templates"]
    out = {}
    for k, v in man.items():
        parts = k.split("|")
        key = tuple([parts[0]] + [int(p) for p in parts[1:]]) if parts[0] == "audio" else tuple(parts)
        out[key] = (os.path.join(tdir, v["file"]), v["clip"], v["kind"])
    return out


def _probe_key(path):
    o = json.loads(subprocess.check_output([fftools.ffprobe_exe(), "-v", "error", "-show_entries",
        "format=format_name:stream=codec_type,codec_name,channels,bits_per_sample,sample_fmt",
        "-of", "json", path]).decode())
    streams = o.get("streams", [])
    fmt_name = (o.get("format", {}) or {}).get("format_name", "") or ""
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if v is not None:
        return ("video", v.get("codec_name") or "h264")
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if a is not None:
        codec = a.get("codec_name", ""); fmt = a.get("sample_fmt", "")
        is_aiff = "aiff" in fmt_name.lower() or path.lower().endswith((".aif", ".aiff"))
        if is_aiff:
            coding = 7
        elif codec.startswith("pcm_f") or fmt.startswith(("flt", "dbl")):
            coding = 10
        else:
            coding = 1
        bits = int(a.get("bits_per_sample") or 0) or {"s16": 16, "s32": 32, "flt": 32}.get(fmt.rstrip("p"), 16)
        return ("audio", coding, bits, int(a.get("channels") or 1))
    return ("video", "h264")


def extract_template_bin(src_bin, clip_name, out_bin):
    def pd(o): return getattr(o, "property_data", {}) or {}
    def iter_c(c, d=0):
        if c is None or d > 6: return
        yield c
        for x in pd(c).get("components", []): yield from iter_c(x, d + 1)
        for t in pd(c).get("tracks", []): yield from iter_c(pd(t).get("component"), d + 1)
    with avb_read.topen(src_bin) as f:
        by_id = {m.mob_id: m for m in f.content.mobs}
        start = next((m for m in f.content.mobs if (pd(m).get("name") or "") == clip_name
                      and pd(m).get("mob_type_id") == 2), None)
        if start is None:
            raise SystemExit("master %r nao encontrado" % clip_name)
        seen = set(); stack = [start.mob_id]
        while stack:
            mid = stack.pop()
            if mid in seen or mid not in by_id: continue
            seen.add(mid)
            for t in pd(by_id[mid]).get("tracks", []):
                for sub in iter_c(pd(t).get("component")):
                    r = getattr(sub, "mob_id", None)
                    if r is not None and r != mid: stack.append(r)
        items = f.content.property_data["items"]
        f.content.property_data["items"] = [bi for bi in items if bi.mob.mob_id in seen]
        f.content.uid = random.getrandbits(63)
        f.write(out_bin)
    return out_bin


def _master_of(bin_path):
    with avb_read.topen(bin_path) as f:
        for m in f.content.mobs:
            if m.property_data.get("mob_type_id") == 2:
                return m.property_data.get("name"), m.mob_id.bytes_le.hex()
    return None, None


def build_media_bin(files, out_bin, templates=None, media_map_out=None, extra_clones=None):
    if templates is None:
        templates = load_bundled_templates()
    tmpdir = tempfile.mkdtemp(prefix="mediabin_")
    clones = []
    media_map = {}
    skipped = []
    for i, path in enumerate(files):
        key = _probe_key(path)
        tpl = templates.get(key) or (templates.get(("video", "h264")) if key[0] == "video" else None)
        if tpl is None:
            skipped.append((os.path.basename(path), key)); continue
        tbin, tclip, kind = tpl
        cbin = os.path.join(tmpdir, "clone_%d.avb" % i)
        try:
            r = CG.clone_generic(tbin, tclip, path, cbin, kind=kind)
        except Exception as e:
            skipped.append((os.path.basename(path), str(e)[:60])); continue
        name, mid = _master_of(cbin)
        media_map[path] = {"name": name, "mob_id": mid}
        clones.append(cbin)
        print("  clonado %-30s -> mob %s… (%d geo)" % ((name or "")[:30], (mid or "")[:16], r["geometry_patches"]))
    if skipped:
        print("  SKIP (%d):" % len(skipped), skipped[:8])
    if not clones:
        raise RuntimeError("nenhum arquivo clonado (sem template compatível?) — %d skipped" % len(skipped))
    added = 0
    with avb_read.topen(clones[0]) as fa:
        have = {m.mob_id for m in fa.content.mobs}
        for cb in clones[1:] + list(extra_clones or []):
            with avb_read.topen(cb) as fb:
                for m in fb.content.mobs:
                    if m.mob_id in have:
                        continue
                    nm = materialize(fa, serialize(m))
                    fa.content.add_mob(nm); have.add(m.mob_id); added += 1
        for bi in fa.content.property_data["items"]:
            bi.property_data["user_placed"] = (getattr(bi.mob, "mob_type_id", None) == 2)
        fa.content.uid = random.getrandbits(63)
        fa.write(out_bin)
    info = {"output": out_bin, "clips": len(clones), "merged": added, "skipped": skipped}
    if media_map_out:
        json.dump(media_map, open(media_map_out, "w", encoding="utf-8"), indent=1)
    print("MEDIA BIN: %d clipes -> %s (%d merged)" % (len(clones), out_bin, added))
    return media_map, info


def _matched_paths(match_results):
    seen, out = set(), []
    for r in match_results or []:
        if not r:
            continue
        for p in [r.get("matched_path")] + [a.get("matched_path") for a in r.get("alternate_matches", [])]:
            if p and p not in seen:
                seen.add(p); out.append(p)
    return out


def build_remap(segments, match_results, media_map, aaf_path):
    from .translate import _urn2hex_from_aaf
    m2h = _urn2hex_from_aaf(aaf_path) if aaf_path else {}
    path2hex = {}
    for p, v in (media_map or {}).items():
        if v.get("mob_id"):
            path2hex[p] = v["mob_id"]
            path2hex[os.path.abspath(p)] = v["mob_id"]
    remap = {}
    for r in match_results or []:
        if not r or r.get("matched_path") is None:
            continue
        media_hex = path2hex.get(r["matched_path"]) or path2hex.get(os.path.abspath(r["matched_path"]))
        if not media_hex:
            continue
        idx = r.get("segment_index")
        seg = segments[idx] if (idx is not None and 0 <= idx < len(segments)) else None
        aaf_hex = m2h.get(seg.get("mob_id")) if seg else None
        if aaf_hex:
            remap[aaf_hex] = media_hex
    return remap


def _offline_specs(segments, match_results, handle=0):
    matched = {r.get("segment_index") for r in (match_results or []) if r and r.get("matched_path")}
    specs = {}
    for i, s in enumerate(segments):
        if i in matched:
            continue
        if s.get("is_gap") or s.get("is_transition") or s.get("is_effect") or s.get("disabled"):
            continue
        mob = s.get("mob_id")
        if not mob:
            continue
        end = int(s.get("source_start_frames", 0) or 0) + int(s.get("duration_frames", 0) or 0)
        d = specs.setdefault(mob, {"name": s.get("clip_name") or "OFFLINE",
                                   "master_len": 0, "kind": "audio" if s.get("is_audio") else "video"})
        d["master_len"] = max(d["master_len"], end)
    for d in specs.values():
        d["master_len"] += 2 * int(handle or 0)
    return specs


def build_offline_masters(specs, out_dir, templates=None, fps=None):
    if templates is None:
        templates = load_bundled_templates()
    vid = templates.get(("video", "h264"))
    aud = templates.get(("audio", 1, 24, 2)) or next((v for k, v in templates.items() if k[0] == "audio"), None)
    out = {}
    for i, (mob, d) in enumerate(specs.items()):
        tpl = vid if d["kind"] == "video" else aud
        if tpl is None:
            continue
        cbin = os.path.join(out_dir, "offline_%d.avb" % i)
        try:
            CG.clone_offline(tpl[0], tpl[1], cbin, d["name"], d["master_len"], kind=d["kind"], fps=fps)
        except Exception as e:
            print("  offline SKIP %r: %s" % (str(d["name"])[:30], str(e)[:50])); continue
        name, mid = _master_of(cbin)
        out[mob] = (cbin, name, mid)
    return out


def build_media_bin_and_remap(segments, match_results, out_bin, aaf_path,
                              templates=None, media_map_out=None, offline=True, timeline_fps=None):
    paths = _matched_paths(match_results)
    if not paths:
        raise RuntimeError("nenhum original casado (match_results sem matched_path)")
    from .translate import offline_handle_frames
    handle = offline_handle_frames(segments)
    om = {}
    if offline:
        specs = _offline_specs(segments, match_results, handle=handle)
        if specs:
            odir = tempfile.mkdtemp(prefix="offline_")
            om = build_offline_masters(specs, odir, templates=templates, fps=timeline_fps)
    extra = [v[0] for v in om.values()]
    media_map, info = build_media_bin(paths, out_bin, templates=templates,
                                      media_map_out=media_map_out, extra_clones=extra)
    remap = build_remap(segments, match_results, media_map, aaf_path)
    if om:
        from .translate import _urn2hex_from_aaf
        m2h = _urn2hex_from_aaf(aaf_path) if aaf_path else {}
        for mob, (_cb, name, hexid) in om.items():
            if not hexid:
                continue
            aaf_hex = m2h.get(mob)
            if aaf_hex:
                remap[aaf_hex] = hexid
            media_map["offline:" + str(mob)] = {"name": name, "mob_id": hexid}
        print("OFFLINE: %d masters (não-resolvidos preservados na bin)" % len(om))
    info["offline_masters"] = len(om)
    info["remap_entries"] = len(remap)
    return out_bin, media_map, remap, info


if __name__ == "__main__":
    out = sys.argv[1]
    mm, info = build_media_bin(sys.argv[2:], out)
    print(json.dumps({"media_map_n": len(mm), **info}, indent=1, default=str))
