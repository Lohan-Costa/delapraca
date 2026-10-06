#!/usr/bin/env python3
import sys, os, struct, json, argparse

MAGIC = b"Domain"
FILE_CLASS = b"FILE"


def _read_string_field(buf, off):
    n = struct.unpack_from("<H", buf, off)[0]
    return buf[off:off + 2 + n], buf[off + 2:off + 2 + n]


def parse_header_end(d):
    if d[0:2] != b"\x06\x00":
        raise ValueError("não é .avb little-endian (esperado 06 00)")
    o = 2
    if d[o:o + 6] != MAGIC:
        raise ValueError("magic 'Domain' ausente")
    o += 6
    o += 4
    n = struct.unpack_from("<H", d, o)[0]; o += 2 + n
    o += 1
    n = struct.unpack_from("<H", d, o)[0]; o += 2 + n
    num_objects = struct.unpack_from("<I", d, o)[0]; o += 4
    o += 4
    o += 4
    o += 4
    o += 4
    o += 4
    o += 4
    o += 32
    o += 16
    return o, num_objects


def walk_chunks(d):
    o, num = parse_header_end(d)
    for _ in range(num):
        pos = o
        class_id = bytes(d[o:o + 4])[::-1]
        size = struct.unpack_from("<I", d, o + 4)[0]
        yield pos, class_id, size
        o += 8 + size


def _locator_paths(chunk_data):
    res = {}
    p = chunk_data.find(b"\x01\x01\x4c")
    if p >= 0:
        _, payload = _read_string_field(chunk_data, p + 3)
        res["posix"] = (p + 3, payload.rstrip(b"\x00").decode("macroman"))
    p = chunk_data.find(b"\x01\x02\x4c")
    if p >= 0:
        _, payload = _read_string_field(chunk_data, p + 3)
        res["utf8"] = (p + 3, payload.strip(b"\x00").decode("utf-8"))
    return res


def _build_string_field(payload_bytes):
    return struct.pack("<H", len(payload_bytes)) + payload_bytes


def iter_file_paths(d):
    for pos, cid, size in walk_chunks(d):
        if cid != FILE_CLASS:
            continue
        data = bytes(d[pos + 8:pos + 8 + size])
        locs = _locator_paths(data)
        if "posix" in locs:
            yield pos, size, locs["posix"][1]


def _transform(path, replace, mapping):
    if mapping and path in mapping:
        return mapping[path]
    if replace:
        old, new = replace
        if path.startswith(old):
            return new + path[len(old):]
    return None


def relink(d, replace=None, mapping=None):
    d = bytearray(d)
    edits = []
    for pos, cid, size in walk_chunks(d):
        if cid != FILE_CLASS:
            continue
        data = bytes(d[pos + 8:pos + 8 + size])
        locs = _locator_paths(data)
        if "posix" not in locs:
            continue
        old_path = locs["posix"][1]
        new_path = _transform(old_path, replace, mapping)
        if not new_path or new_path == old_path:
            continue
        new_data = bytearray(data)
        items = []
        if "utf8" in locs:
            items.append(("utf8", locs["utf8"][0], new_path.encode("utf-8"), b"\x00\x00"))
        items.append(("posix", locs["posix"][0], new_path.encode("macroman"), b""))
        for _, off, payload, prefix in sorted(items, key=lambda x: -x[1]):
            field = _build_string_field(prefix + payload)
            old_len = 2 + struct.unpack_from("<H", new_data, off)[0]
            new_data[off:off + old_len] = field
        chunk = bytes(d[pos:pos + 4]) + struct.pack("<I", len(new_data)) + bytes(new_data)
        edits.append((pos, size, chunk, old_path, new_path))
    for pos, old_size, chunk, _, _ in sorted(edits, key=lambda x: -x[0]):
        d[pos:pos + 8 + old_size] = chunk
    return bytes(d), [(e[3], e[4]) for e in edits]


def replace_field(data, old, new):
    if isinstance(old, str): old = old.encode("latin-1")
    if isinstance(new, str): new = new.encode("latin-1")
    d = bytearray(data)
    needle = struct.pack("<H", len(old)) + old
    repl = struct.pack("<H", len(new)) + new
    edits = []
    n = 0
    for pos, cid, size in walk_chunks(d):
        cdata = bytes(d[pos + 8:pos + 8 + size])
        if needle not in cdata:
            continue
        ncd = cdata.replace(needle, repl)
        n += cdata.count(needle)
        chunk = bytes(d[pos:pos + 4]) + struct.pack("<I", len(ncd)) + ncd
        edits.append((pos, size, chunk))
    for pos, old_size, chunk in sorted(edits, key=lambda x: -x[0]):
        d[pos:pos + 8 + old_size] = chunk
    return bytes(d), n


def cmd_list(args):
    d = open(args.bin, "rb").read()
    rows = list(iter_file_paths(d))
    print("%d chunk(s) FILE com path_posix:\n" % len(rows))
    for _, _, path in rows:
        exists = os.path.exists(path)
        mark = "OK " if exists else "OFF"
        extra = ""
        if exists:
            extra = "  mtime=%d size=%d" % (int(os.stat(path).st_mtime), os.path.getsize(path))
        print("  [%s] %s%s" % (mark, path, extra))


def _sync_mtime(changes):
    done = skipped = 0
    print("\n--sync-mtime:")
    for old, new in changes:
        if old == new or not os.path.exists(new):
            print("   skip (novo ausente): %s" % new); skipped += 1; continue
        if not os.path.exists(old):
            print("   skip (antigo ausente; mv já preserva o mtime): %s" % old); skipped += 1; continue
        if os.path.samefile(old, new):
            print("   ok (mesmo arquivo/symlink): %s" % new); done += 1; continue
        st = os.stat(old)
        os.utime(new, ns=(st.st_atime_ns, st.st_mtime_ns))
        print("   set mtime %d -> %s" % (int(st.st_mtime), new)); done += 1
    print("   (%d ajustado(s), %d pulado(s))" % (done, skipped))


def cmd_relink(args):
    d = open(args.bin, "rb").read()
    mapping = None
    if args.map:
        mapping = json.load(open(args.map, encoding="utf-8"))
    replace = tuple(args.replace) if args.replace else None
    if not mapping and not replace:
        sys.exit("informe --replace OLD NEW ou --map mapa.json")
    out, changes = relink(d, replace=replace, mapping=mapping)
    if not changes:
        print("nenhum caminho casou; nada a fazer."); return
    open(args.out, "wb").write(out)
    print("%d caminho(s) relinkado(s) -> %s  (in=%d out=%d bytes)" %
          (len(changes), args.out, len(d), len(out)))
    for old, new in changes:
        warn = "" if os.path.exists(new) else "  [!! arquivo novo não existe]"
        print("   %s\n     -> %s%s" % (old, new, warn))
    if args.sync_mtime:
        _sync_mtime(changes)
    else:
        print("\nLembrete: para ONLINE, o mtime do arquivo deve casar com _AMA_FILE_DATE_TIME.")
        print("Mover (mv) preserva mtime; p/ cópia use --sync-mtime ou: touch -r <origem> <destino>.")


def main():
    ap = argparse.ArgumentParser(description="Relink de caminhos AMA em bins .avb (stdlib).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list", help="lista os caminhos de arquivo da bin")
    pl.add_argument("bin")
    pl.set_defaults(func=cmd_list)
    pr = sub.add_parser("relink", help="reescreve caminhos numa nova bin")
    pr.add_argument("bin"); pr.add_argument("out")
    pr.add_argument("--replace", nargs=2, metavar=("OLD", "NEW"),
                    help="substituição por prefixo de caminho")
    pr.add_argument("--map", metavar="JSON", help="JSON {caminho_antigo: caminho_novo}")
    pr.add_argument("--sync-mtime", action="store_true",
                    help="alinha o mtime do arquivo novo ao do antigo (quando o antigo existe)")
    pr.set_defaults(func=cmd_relink)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
