#!/usr/bin/env python3
import os, sys, struct, subprocess, argparse
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from media import fftools


def region(ml):
    return bytes([0x48]) + ml[0:4] + bytes([0x46]) + ml[4:6] + bytes([0x46]) + ml[6:8] \
         + bytes([0x41, 8, 0, 0, 0]) + ml[8:16]


def frame_count(path):
    r = subprocess.run([fftools.ffprobe_exe(), "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=nb_frames", "-of", "default=nw=1:nk=1", path],
                       capture_output=True, text=True)
    out = r.stdout.strip()
    if out.isdigit() and int(out) > 0:
        return int(out)
    r = subprocess.run([fftools.ffprobe_exe(), "-v", "error", "-select_streams", "v:0", "-count_frames",
                        "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1", path],
                       capture_output=True, text=True)
    out = r.stdout.strip()
    if not out.isdigit():
        raise SystemExit("ffprobe nao retornou nb_frames p/ %s (%r)" % (path, out))
    return int(out)


def collect_indices(mob):
    s = set()
    if getattr(mob, "instance_id", None) is not None:
        s.add(mob.instance_id)
    a = getattr(mob, "attributes", None)
    if getattr(a, "instance_id", None) is not None:
        s.add(a.instance_id)
    d = getattr(mob, "descriptor", None)
    if d is not None:
        if getattr(d, "instance_id", None) is not None:
            s.add(d.instance_id)
        L = getattr(d, "locator", None)
        if getattr(L, "instance_id", None) is not None:
            s.add(L.instance_id)
    for t in mob.tracks:
        c = getattr(t, "component", None)
        if c is None:
            continue
        if getattr(c, "instance_id", None) is not None:
            s.add(c.instance_id)
        for sc in getattr(c, "components", []) or []:
            if getattr(sc, "instance_id", None) is not None:
                s.add(sc.instance_id)
    return s


def clip_info(f, clipname):
    mobs = [m for m in f.content.mobs if getattr(m, "name", None) == clipname]
    if not mobs:
        raise SystemExit("clipe-base %r nao esta no template" % clipname)
    info = {"mobids": [], "indices": set(), "length": None, "date": None, "name": clipname}
    for m in mobs:
        info["mobids"].append(m.mob_id.bytes_le)
        info["indices"] |= collect_indices(m)
        if getattr(m, "descriptor", None) is None:
            for t in m.tracks:
                c = getattr(t, "component", None)
                if c is not None and getattr(c, "length", None):
                    info["length"] = c.length
        else:
            a = getattr(m, "attributes", {}) or {}
            if "_AMA_FILE_DATE_TIME" in a:
                info["date"] = a["_AMA_FILE_DATE_TIME"]
    if info["length"] is None or info["date"] is None:
        raise SystemExit("clipe %r: nao achei duracao/data (template vídeo-puro?)" % clipname)
    return info


def ranges_for(pos, raw, indices):
    out = []
    for iid in indices:
        hdr = pos[iid]
        _, size = struct.unpack(b"<4sI", raw[hdr:hdr + 8])
        out.append((hdr + 8, hdr + 8 + size))
    return out


def scoped_replace_u32(data, ranges, old, new):
    ob, nb = struct.pack("<I", old), struct.pack("<I", new)
    n = 0
    for s, e in ranges:
        seg = bytes(data[s:e])
        c = seg.count(ob)
        if c:
            data[s:e] = seg.replace(ob, nb)
            n += c
    return n


def clone_one(data, pos, raw, info, target, do_name=True):
    if not os.path.exists(target):
        print("AVISO: arquivo-alvo nao existe:", target)
    F = frame_count(target)
    mt = int(os.stat(target).st_mtime)
    oldname = info["name"]
    rngs = ranges_for(pos, raw, info["indices"])
    for blle in info["mobids"]:
        r_old, r_new = region(blle[16:32]), region(os.urandom(16))
        if data.count(r_old) == 0:
            raise SystemExit("region do MobID nao encontrada")
        data[:] = data.replace(r_old, r_new)
    nL = scoped_replace_u32(data, rngs, info["length"], F)
    nD = scoped_replace_u32(data, rngs, info["date"], mt)
    nN = 0
    if do_name:
        newname = os.path.splitext(os.path.basename(target))[0]
        if len(newname) != len(oldname):
            raise SystemExit("nome novo %r tem tamanho != template %r; use do_name=False + "
                             "replace_field (ver clone_generic)" % (newname, oldname))
        ob, nb = oldname.encode("latin-1"), newname.encode("latin-1")
        nN = data.count(ob)
        data[:] = data.replace(ob, nb)
    print("  %s -> %s F=%d (len %d->%d x%d, date->%d x%d, name x%d)"
          % (oldname, "(nome adiado)" if not do_name else os.path.splitext(os.path.basename(target))[0],
             F, info["length"], F, nL, mt, nD, nN))
    return F


def main():
    ap = argparse.ArgumentParser(description="Clona clipes AMA de um template p/ arquivos novos (byte-surgery).")
    ap.add_argument("template")
    ap.add_argument("output")
    ap.add_argument("--job", nargs=2, action="append", metavar=("CLIPE_BASE", "ARQUIVO"),
                    required=True, help="clipe-base no template -> arquivo-alvo (repetível)")
    args = ap.parse_args()
    import avb
    raw = open(args.template, "rb").read()
    data = bytearray(raw)
    with avb.open(args.template) as f:
        pos = list(f.object_positions)
        infos = [(clip_info(f, base), tgt) for base, tgt in args.job]
    for info, tgt in infos:
        clone_one(data, pos, raw, info, tgt)
    if len(data) != len(raw):
        raise SystemExit("ERRO: tamanho mudou (%d->%d) — patch nao size-neutral" % (len(raw), len(data)))
    open(args.output, "wb").write(data)
    print("OK size=%d ->" % len(data), args.output)


if __name__ == "__main__":
    main()
