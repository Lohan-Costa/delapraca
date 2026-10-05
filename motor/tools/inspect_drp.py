#!/usr/bin/env python3
from __future__ import annotations

import binascii
import io
import struct
import sys
import zipfile
from typing import Any


def _read_varint(b: bytes, o: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while True:
        byte = b[o]
        o += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, o
        shift += 7


def _write_varint(v: int) -> bytes:
    out = bytearray()
    while True:
        byte = v & 0x7F
        v >>= 7
        if v:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def pb_decode(b: bytes, o: int = 0, end: int | None = None) -> list[dict]:
    if end is None:
        end = len(b)
    fields: list[dict] = []
    while o < end:
        key, o = _read_varint(b, o)
        field = key >> 3
        wire = key & 0x7
        if wire == 0:
            val, o = _read_varint(b, o)
            fields.append({"field": field, "wire": 0, "value": val})
        elif wire == 1:
            val = b[o:o + 8]
            o += 8
            fields.append({"field": field, "wire": 1, "value": val})
        elif wire == 2:
            ln, o = _read_varint(b, o)
            data = b[o:o + ln]
            o += ln
            sub = None
            try:
                sub = pb_decode(data)
                if pb_encode(sub) != data:
                    sub = None
            except Exception:
                sub = None
            fields.append({"field": field, "wire": 2,
                           "value": sub if sub is not None else data})
        elif wire == 5:
            val = b[o:o + 4]
            o += 4
            fields.append({"field": field, "wire": 5, "value": val})
        else:
            raise ValueError(f"wire type {wire} não suportado @ {o}")
    return fields


def pb_encode(fields: list[dict]) -> bytes:
    out = bytearray()
    for f in fields:
        out += _write_varint((f["field"] << 3) | f["wire"])
        if f["wire"] == 0:
            out += _write_varint(f["value"])
        elif f["wire"] in (1, 5):
            out += f["value"]
        elif f["wire"] == 2:
            v = f["value"]
            data = pb_encode(v) if isinstance(v, list) else v
            out += _write_varint(len(data))
            out += data
        else:
            raise ValueError(f"wire {f['wire']}")
    return out


def fieldsblob_decode(hexstr: str) -> dict:
    b = binascii.unhexlify(hexstr.strip())
    ver = struct.unpack(">I", b[0:4])[0]
    blen = struct.unpack(">I", b[4:8])[0]
    body = b[8:8 + blen]
    flag = body[0]
    if flag == 0x81 and body[1:5].hex() == "28b52ffd":
        import zstandard
        pb = pb_decode(zstandard.ZstdDecompressor().decompress(body[1:]))
    else:
        pb = pb_decode(body, 1)
    return {"version": ver, "flag": flag, "fields": pb}


def fieldsblob_encode(dec: dict) -> str:
    dados = pb_encode(dec["fields"])
    if dec["flag"] == 0x81:
        import zstandard
        dados = zstandard.ZstdCompressor().compress(bytes(dados))
    body = bytes([dec["flag"]]) + dados
    out = struct.pack(">I", dec["version"]) + struct.pack(">I", len(body)) + body
    return out.hex()


def ba_tokens(hexstr: str) -> list[str]:
    import re
    raw = binascii.unhexlify(hexstr.strip())
    s = raw.decode("utf-16-be", errors="replace")
    return re.findall(r"[ -~]{2,}", s)


_BA_INT, _BA_DOUBLE, _BA_STRING, _BA_OBJ = 2, 6, 10, 12
_U = lambda b, o: struct.unpack(">I", b[o:o + 4])[0]


def _ba_decode_entries(b: bytes, o: int, count: int):
    items = []
    for _ in range(count):
        kl = _U(b, o); o += 4
        key = b[o:o + kl].decode("utf-16-be"); o += kl
        t = _U(b, o); o += 4
        if t == _BA_INT:
            v = {"flag": b[o], "int": struct.unpack(">i", b[o + 1:o + 5])[0]}; o += 5
        elif t == _BA_DOUBLE:
            v = {"tag": b[o], "dbl": struct.unpack(">d", b[o + 1:o + 9])[0]}; o += 9
        elif t == _BA_STRING:
            fl = b[o]; sl = _U(b, o + 1)
            v = {"flag": fl, "str": b[o + 5:o + 5 + sl].decode("utf-16-be")}; o += 5 + sl
        elif t == _BA_OBJ:
            a = _U(b, o); tag = b[o + 4]; bb = _U(b, o + 5); cnt = _U(b, o + 9)
            sub, o = _ba_decode_entries(b, o + 13, cnt)
            v = {"a": a, "tag": tag, "b": bb, "entries": sub}
        else:
            raise ValueError(f"tipo *BA desconhecido {t} @ {o}")
        items.append({"key": key, "type": t, "value": v})
    return items, o


def _ba_encode_entries(items: list) -> bytes:
    out = bytearray()
    for it in items:
        kb = it["key"].encode("utf-16-be")
        out += struct.pack(">I", len(kb)) + kb + struct.pack(">I", it["type"])
        t, v = it["type"], it["value"]
        if t == _BA_INT:
            out += bytes([v["flag"]]) + struct.pack(">i", v["int"])
        elif t == _BA_DOUBLE:
            out += bytes([v["tag"]]) + struct.pack(">d", v["dbl"])
        elif t == _BA_STRING:
            sb = v["str"].encode("utf-16-be")
            out += bytes([v["flag"]]) + struct.pack(">I", len(sb)) + sb
        elif t == _BA_OBJ:
            sub = _ba_encode_entries(v["entries"])
            out += (struct.pack(">I", v["a"]) + bytes([v["tag"]])
                    + struct.pack(">I", v["b"]) + struct.pack(">I", len(v["entries"])) + sub)
        else:
            raise ValueError(f"tipo {t}")
    return bytes(out)


def timemap_decode(hexstr: str):
    b = binascii.unhexlify(hexstr.strip())
    if len(b) <= 12:
        return {"kind": "const", "tag": b[0], "value": struct.unpack(">d", b[1:9])[0]}
    ver = _U(b, 0)
    items, _ = _ba_decode_entries(b, 8, _U(b, 4))
    return {"kind": "map", "version": ver, "items": items}


def timemap_encode(dec) -> str:
    if dec["kind"] == "const":
        return (bytes([dec["tag"]]) + struct.pack(">d", dec["value"])).hex()
    body = (struct.pack(">I", dec["version"]) + struct.pack(">I", len(dec["items"]))
            + _ba_encode_entries(dec["items"]))
    return body.hex()


def _dbl(v):  return {"tag": 0, "dbl": float(v)}
def _int(v):  return {"flag": 0, "int": int(v)}
def _str(v):  return {"flag": 0, "str": v}


def _keyframe(x: float, y: float, interp: int = 0) -> dict:
    return {"a": 0, "tag": 0xA7, "b": 1, "entries": [
        {"key": "interp", "type": _BA_INT,    "value": _int(interp)},
        {"key": "YOut",   "type": _BA_DOUBLE, "value": _dbl(0.0)},
        {"key": "YIn",    "type": _BA_DOUBLE, "value": _dbl(0.0)},
        {"key": "Y",      "type": _BA_DOUBLE, "value": _dbl(y)},
        {"key": "XOut",   "type": _BA_DOUBLE, "value": _dbl(0.0)},
        {"key": "XIn",    "type": _BA_DOUBLE, "value": _dbl(0.0)},
        {"key": "X",      "type": _BA_DOUBLE, "value": _dbl(x)},
    ]}


def _timemap_dois_kf(ymax: float, xmax: float, kf1: tuple, kf0: tuple,
                     unique_id: str | None = None) -> str:
    import uuid as _uuid
    gid = unique_id or str(_uuid.uuid4())
    items = [
        {"key": "YMax", "type": _BA_DOUBLE, "value": _dbl(ymax)},
        {"key": "XMax", "type": _BA_DOUBLE, "value": _dbl(xmax)},
        {"key": "UniqueId", "type": _BA_STRING, "value": _str(gid)},
        {"key": "LastValidYOffset", "type": _BA_DOUBLE, "value": _dbl(ymax)},
        {"key": "KeyframesBA", "type": _BA_OBJ, "value": {"a": 1, "tag": 0x74, "b": 1,
            "entries": [
                {"key": "1", "type": _BA_OBJ, "value": _keyframe(*kf1)},
                {"key": "0", "type": _BA_OBJ, "value": _keyframe(*kf0)},
            ]}},
        {"key": "DbType", "type": _BA_STRING, "value": _str("Sm2TimeMap")},
    ]
    return timemap_encode({"kind": "map", "version": 1, "items": items})


def build_speed_timemap(nb_frames: int, fps: float, speed_ratio: float,
                        unique_id: str | None = None) -> str:
    ymax = (nb_frames - 1) / fps
    xmax = ymax * speed_ratio
    return _timemap_dois_kf(ymax, xmax, (xmax, ymax), (0.0, 0.0), unique_id)


_KF_HEADER_BY_N = {
    2: (1, 0x74), 4: (2, 0xE0), 6: (4, 0x4C), 13: (9, 0x4C), 14: (10, 0x04),
    22: (15, 0xC4), 30: (21, 0x84), 37: (26, 0x8C), 159: (114, 0xB2),
}
_RAMP_N = 159


def _keyframes_container(pts: list, n: int) -> dict:
    a, tag = _KF_HEADER_BY_N[n]
    by_key = {str(i): _keyframe(round(x, 6), round(y, 6), interp=0)
              for i, (x, y) in enumerate(pts)}
    order = sorted(by_key.keys(), reverse=True)
    entries = [{"key": k, "type": _BA_OBJ, "value": by_key[k]} for k in order]
    return {"a": a, "tag": tag, "b": 1, "entries": entries}


def _douglas_peucker(pts: list, tol: float) -> list:
    n = len(pts)
    if n <= 2:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        x0, y0 = pts[a]; x1, y1 = pts[b]
        dx = x1 - x0
        dmax = -1.0; idx = -1
        for i in range(a + 1, b):
            x, y = pts[i]
            yl = y0 + (y1 - y0) * (x - x0) / dx if dx > 1e-12 else y0
            d = abs(y - yl)
            if d > dmax:
                dmax = d; idx = i
        if dmax > tol and idx > a:
            keep[idx] = True
            stack.append((a, idx)); stack.append((idx, b))
    return [i for i in range(n) if keep[i]]


def _pad_to_header_n(pts: list) -> list:
    avail = sorted(_KF_HEADER_BY_N)
    need = len(pts)
    target = next((n for n in avail if n >= need), None)
    if target is None:
        raise ValueError(f"{need} keyframes excede o maior header tabelado ({avail[-1]}); "
                         f"colher headers maiores no Resolve")
    pts = list(pts)
    while len(pts) < target:
        gi = max(range(len(pts) - 1), key=lambda i: pts[i + 1][0] - pts[i][0])
        (x0, y0), (x1, y1) = pts[gi], pts[gi + 1]
        pts.insert(gi + 1, ((x0 + x1) / 2.0, (y0 + y1) / 2.0))
    return pts


def reverso_y0(nb_frames: int, media_fps: float, velocidade: float) -> float:
    return (nb_frames - velocidade) / media_fps


def build_reverse_timemap(nb_frames: int, media_fps: float, velocidade: float,
                          unique_id: str | None = None) -> str:
    ymax = (nb_frames - 1) / media_fps
    y0 = reverso_y0(nb_frames, media_fps, velocidade)
    x1 = y0 * (1.0 / velocidade)
    return _timemap_dois_kf(ymax, x1, (x1, 0.0), (0.0, y0), unique_id)


def build_audio_speed_timemap(dur_s: float, tl_fps: float, speed_ratio: float,
                              unique_id: str | None = None,
                              media_fps: float = 24000 / 1001) -> str:
    ymax = dur_s - 1 / media_fps
    x1 = dur_s * speed_ratio - 1 / tl_fps
    y1 = x1 / speed_ratio
    return _timemap_dois_kf(ymax, x1, (x1, y1), (0.0, 0.0), unique_id)


def build_adaptive_ramp_timemap(nb_frames: int, media_fps: float, offset_map: list,
                                source_start_frames: int, tl_fps: float,
                                tol_frames: float = 0.5,
                                unique_id: str | None = None) -> str:
    import uuid as _uuid
    if not offset_map:
        return build_speed_timemap(nb_frames, media_fps, 1.0, unique_id)

    ymax_full = (nb_frames - 1) / media_fps
    om = sorted(((float(t), float(v)) for t, v in offset_map), key=lambda p: p[0])
    raw = [(t / tl_fps, (source_start_frames + v) / media_fps) for t, v in om]
    dedup = [raw[0]]
    for x, y in raw[1:]:
        if x > dedup[-1][0] + 1e-9:
            dedup.append((x, y))
    raw = dedup
    if raw[-1][1] < ymax_full - 1e-6 and len(raw) >= 2:
        (x0, y0), (x1, y1) = raw[-2], raw[-1]
        slope = (y1 - y0) / (x1 - x0) if x1 > x0 else 1.0
        if slope > 1e-6:
            raw.append((x1 + (ymax_full - y1) / slope, ymax_full))

    sel_idx = _douglas_peucker(raw, tol_frames / media_fps)
    pts = [raw[i] for i in sel_idx]
    try:
        pts = _pad_to_header_n(pts)
    except ValueError:
        return build_ramp_timemap(nb_frames, media_fps, offset_map,
                                  source_start_frames, tl_fps, unique_id, _RAMP_N)
    pts[0] = (0.0, 0.0)
    n = len(pts)

    gid = unique_id or str(_uuid.uuid4())
    items = [
        {"key": "YMax", "type": _BA_DOUBLE, "value": _dbl(ymax_full)},
        {"key": "XMax", "type": _BA_DOUBLE, "value": _dbl(pts[-1][0])},
        {"key": "UniqueId", "type": _BA_STRING, "value": _str(gid)},
        {"key": "LastValidYOffset", "type": _BA_DOUBLE, "value": _dbl(ymax_full)},
        {"key": "KeyframesBA", "type": _BA_OBJ, "value": _keyframes_container(pts, n)},
        {"key": "DbType", "type": _BA_STRING, "value": _str("Sm2TimeMap")},
    ]
    return timemap_encode({"kind": "map", "version": 1, "items": items})


def build_ramp_timemap(nb_frames: int, media_fps: float, offset_map: list,
                       source_start_frames: int, tl_fps: float,
                       unique_id: str | None = None, n_keyframes: int = _RAMP_N) -> str:
    import uuid as _uuid
    if not offset_map or n_keyframes not in _KF_HEADER_BY_N:
        return build_speed_timemap(nb_frames, media_fps, 1.0, unique_id)

    ymax_full = (nb_frames - 1) / media_fps
    om = sorted(((float(t), float(v)) for t, v in offset_map), key=lambda p: p[0])
    raw = [(t / tl_fps, (source_start_frames + v) / media_fps) for t, v in om]
    dedup = [raw[0]]
    for x, y in raw[1:]:
        if x > dedup[-1][0] + 1e-9:
            dedup.append((x, y))
    raw = dedup
    if raw[-1][1] < ymax_full - 1e-6 and len(raw) >= 2:
        (x0, y0), (x1, y1) = raw[-2], raw[-1]
        slope = (y1 - y0) / (x1 - x0) if x1 > x0 else 1.0
        if slope > 1e-6:
            raw.append((x1 + (ymax_full - y1) / slope, ymax_full))

    xmax = raw[-1][0]
    xs = [p[0] for p in raw]
    def _interp_y(xq):
        import bisect
        i = max(1, min(bisect.bisect_left(xs, xq), len(raw) - 1))
        (a0, b0), (a1, b1) = raw[i - 1], raw[i]
        return b0 + (b1 - b0) * (xq - a0) / (a1 - a0) if a1 > a0 else b0
    pts = [(i * xmax / (n_keyframes - 1), _interp_y(i * xmax / (n_keyframes - 1)))
           for i in range(n_keyframes)]
    pts[0] = (0.0, 0.0)

    gid = unique_id or str(_uuid.uuid4())
    items = [
        {"key": "YMax", "type": _BA_DOUBLE, "value": _dbl(ymax_full)},
        {"key": "XMax", "type": _BA_DOUBLE, "value": _dbl(xmax)},
        {"key": "UniqueId", "type": _BA_STRING, "value": _str(gid)},
        {"key": "LastValidYOffset", "type": _BA_DOUBLE, "value": _dbl(ymax_full)},
        {"key": "KeyframesBA", "type": _BA_OBJ, "value": _keyframes_container(pts, n_keyframes)},
        {"key": "DbType", "type": _BA_STRING, "value": _str("Sm2TimeMap")},
    ]
    return timemap_encode({"kind": "map", "version": 1, "items": items})


_EFB_FILTERS = {
    "transform": (0x04, 0x00, 12, {
        "zoom_x": (0, 0x2A), "zoom_y": (1, 0x2B),
        "pos_x": (3, 0x28), "pos_y": (4, 0x29), "rotation": (5, 0x2F),
        "pitch": (8, 0x2D), "yaw": (9, 0x2E),
        "flip_x": (10, 0x32), "flip_y": (11, 0x33)}),
    "crop": (0x06, 0x02, 6, {
        "crop_l": (0, 0x36), "crop_r": (1, 0x37),
        "crop_t": (2, 0x38), "crop_b": (3, 0x39),
        "crop_suave": (4, 0x3A)}),
    "composite": (0x02, 0x06, 2, {"modo": (0, 0x00), "opacity": (1, 0x01)}),
}
_EFB_PARAM_FILTER = {p: f for f, (_, _, _, sm) in _EFB_FILTERS.items() for p in sm}

ESCALA_DO_CLIPE = {"crop": 1, "fit": 2, "fill": 3, "stretch": 4}


RETIME_DO_CLIPE = {"nearest": 1, "frame_blend": 2, "optical_flow": 3}
COMPOSITE_DO_CLIPE = {"normal": 0, "add": 1, "screen": 5, "lighten": 10, "multiply": 4}


def _efb_slot_inteiro(param_id: int, n: int) -> bytes:
    slot = bytes([0x08, param_id, 0x1A, 0x04, 0x0A, 0x02, 0x20, int(n)])
    return bytes([0x4A, len(slot)]) + slot


def _efb_filtro_escala(n: int | None, retime: int | None = None) -> bytes:
    vazio = bytes([0x4A, 0x00])
    slots = [vazio] * 5
    if retime is not None:
        slots[0] = _efb_slot_inteiro(0x5B, retime)
    if n is not None:
        slots[2] = _efb_slot_inteiro(0x5C, n)
    fc = bytes([0x08, 0x2C]) + b"".join(slots)
    return bytes([0x0A]) + _write_varint(len(fc)) + fc


def _efb_slot_double(param_id: int, value: float) -> bytes:
    return (bytes([0x4A, 0x0F, 0x08, param_id, 0x1A, 0x0B, 0x0A, 0x09, 0x11])
            + struct.pack("<d", float(value)))


_KF_FORMA = {0x2A: "zoom", 0x2B: "zoom"}


def _efb_slot_animado(param_id: int, pontos: list) -> bytes:
    import struct as _st
    zoom = _KF_FORMA.get(param_id) == "zoom"
    corpo = _st.pack(">i", -(64 if zoom else 16) * len(pontos))
    for q, v in pontos:
        corpo += _st.pack("<IHH", max(0, int(q)), 0, 0x0D if zoom else 0x0C) + _st.pack("<d", float(v))
        if zoom:
            corpo += bytes(48)
    fc = bytes([0x08, param_id])
    if zoom:
        fc += bytes([0x1A, 0x0B, 0x0A, 0x09, 0x11]) + _st.pack("<d", 0.0)
    fc += bytes([0x52]) + _write_varint(len(corpo)) + corpo
    return bytes([0x4A]) + _write_varint(len(fc)) + fc


def _efb_filter(name: str, vals: dict, animado: dict | None = None) -> bytes:
    ftype, f7, nslots, slotmap = _EFB_FILTERS[name]
    slots = [bytes([0x4A, 0x00])] * nslots
    for p, v in vals.items():
        if v is None or p not in slotmap:
            continue
        idx, pid = slotmap[p]
        if isinstance(v, bool):
            slots[idx] = bytes([0x4A, 0x08, 0x08, pid, 0x1A, 0x04, 0x0A, 0x02, 0x28, int(v)])
        elif p == "modo":
            slots[idx] = _efb_slot_inteiro(pid, int(v))
        else:
            slots[idx] = _efb_slot_double(pid, v)
    for p, pontos in (animado or {}).items():
        if p in slotmap and pontos and len(pontos) > 1:
            idx, pid = slotmap[p]
            slots[idx] = _efb_slot_animado(pid, pontos)
    fc = bytes([0x08, ftype, 0x38, f7]) + b"".join(slots)
    return bytes([0x0A]) + _write_varint(len(fc)) + fc


def build_effect_filters_ba(*, zoom_x=None, zoom_y=None, pos_x=None, pos_y=None,
                            rotation=None, pitch=None, yaw=None, crop_l=None, crop_r=None, crop_t=None,
                            crop_b=None, opacity=None, escala: str | None = None,
                            retime: str | None = None, animado: dict | None = None,
                            flip_x: bool = False, flip_y: bool = False,
                            composite: str | None = None, crop_suave=None) -> str:
    allv = dict(zoom_x=zoom_x, zoom_y=zoom_y, pos_x=pos_x, pos_y=pos_y, rotation=rotation,
                pitch=pitch, yaw=yaw,
                crop_l=crop_l, crop_r=crop_r, crop_t=crop_t, crop_b=crop_b, opacity=opacity,
                flip_x=True if flip_x else None, flip_y=True if flip_y else None,
                crop_suave=crop_suave,
                modo=COMPOSITE_DO_CLIPE.get(composite) if composite else None)
    payload = bytes([0x80])
    for fname in ("transform", "crop", "composite"):
        sm = _EFB_FILTERS[fname][3]
        fvals = {p: allv[p] for p in sm if allv.get(p) is not None}
        if fname == "transform" or fvals:
            payload += _efb_filter(fname, fvals, animado)
    if escala in ESCALA_DO_CLIPE or retime in RETIME_DO_CLIPE:
        payload += _efb_filtro_escala(ESCALA_DO_CLIPE.get(escala), RETIME_DO_CLIPE.get(retime))
    return (struct.pack(">I", 2) + struct.pack(">I", len(payload)) + payload).hex()


def framerate_decode(hexstr: str) -> float:
    b = binascii.unhexlify(hexstr.strip())
    return struct.unpack("<d", b[0:8])[0]


def framerate_encode(fps: float, total_bytes: int = 16) -> str:
    b = struct.pack("<d", fps)
    b = b + b"\x00" * (total_bytes - len(b))
    return b.hex()


def read_container(path: str) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def _xml_clips(xml_bytes: bytes, tag: str = "Sm2TiVideoClip"):
    import re
    txt = xml_bytes.decode("utf-8", errors="replace")
    for m in re.finditer(rf"<{tag}\b.*?</{tag}>", txt, re.DOTALL):
        block = m.group(0)
        def field(name):
            mm = re.search(rf"<{name}>(.*?)</{name}>", block, re.DOTALL)
            return mm.group(1).strip() if mm else None
        yield block, field


def selftest(path: str) -> int:
    files = read_container(path)
    seq = next((v for k, v in files.items() if "SeqContainer" in k), None)
    if seq is None:
        print("sem SeqContainer no arquivo"); return 1

    ok = fail = 0

    def check(label, original_hex, roundtrip_hex):
        nonlocal ok, fail
        a = original_hex.strip().lower()
        b = roundtrip_hex.strip().lower()
        if a == b:
            ok += 1
        else:
            fail += 1
            print(f"  ✗ {label}\n    orig={a[:80]}\n    rt  ={b[:80]}")

    for block, field in _xml_clips(seq, "Sm2TiVideoClip"):
        nm = field("Name") or "?"
        fb = field("FieldsBlob")
        if fb:
            try:
                check(f"FieldsBlob {nm}", fb, fieldsblob_encode(fieldsblob_decode(fb)))
            except Exception as e:
                print(f"  ~ FieldsBlob {nm}: pulado ({e})")
        fr = field("MediaFrameRate")
        if fr and fr not in ("", "0"):
            nbytes = len(binascii.unhexlify(fr))
            check(f"MediaFrameRate {nm}", fr,
                  framerate_encode(framerate_decode(fr), nbytes))
        tm = field("MediaTimemapBA")
        if tm:
            try:
                check(f"MediaTimemapBA {nm}", tm, timemap_encode(timemap_decode(tm)))
            except Exception as e:
                print(f"  ~ MediaTimemapBA {nm}: pulado ({e})")

    print(f"\nround-trip: {ok} OK, {fail} FALHA(S)")
    return 0 if fail == 0 else 2


def dump(path: str) -> None:
    files = read_container(path)
    print("=== arquivos no container ===")
    for n, data in files.items():
        print(f"  {n}  ({len(data)} bytes)")
    seq = next((v for k, v in files.items() if "SeqContainer" in k), None)
    if seq is None:
        return
    print("\n=== clipes de vídeo (campos XML + blobs decodificados) ===")
    for block, field in _xml_clips(seq, "Sm2TiVideoClip"):
        nm = field("Name")
        print(f"\n[{nm}]")
        for k in ("Start", "Duration", "In", "MediaFilePath", "MediaReelNumber",
                  "MediaStartTime"):
            v = field(k)
            if v:
                print(f"  {k} = {v}")
        fr = field("MediaFrameRate")
        if fr:
            try: print(f"  MediaFrameRate = {framerate_decode(fr):.6f}  (hex {fr})")
            except Exception: print(f"  MediaFrameRate = <{fr}>")
        fb = field("FieldsBlob")
        if fb:
            try:
                d = fieldsblob_decode(fb)
                rt = "✓" if fieldsblob_encode(d) == fb.strip().lower() else "✗"
                print(f"  FieldsBlob[{rt}] = v{d['version']} flag={d['flag']:#x} fields={d['fields']}")
            except Exception as e:
                print(f"  FieldsBlob = <não-protobuf simples: {e}>")
        tm = field("MediaTimemapBA")
        if tm:
            toks = ba_tokens(tm)
            print(f"  MediaTimemapBA = {len(binascii.unhexlify(tm))}B keys={toks}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("uso: python tools/inspect_drp.py <arquivo.drt|.drp> [--selftest]")
        return 1
    path = argv[1]
    if "--selftest" in argv:
        return selftest(path)
    dump(path)
    print("\n--- round-trip ---")
    return selftest(path)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
