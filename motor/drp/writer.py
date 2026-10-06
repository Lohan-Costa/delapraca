from __future__ import annotations

import ntpath
import os
import re
import subprocess as _sp
import sys
import time
import uuid
import zipfile
from pathlib import Path

from tools.inspect_drp import (fieldsblob_decode, fieldsblob_encode, timemap_encode,
                               build_speed_timemap, build_ramp_timemap,
                               build_adaptive_ramp_timemap, build_effect_filters_ba,
                               framerate_encode, build_reverse_timemap, reverso_y0,
                               build_audio_speed_timemap)
from media.fftools import SEM_JANELA

_RAMP_MODE = "adaptive"

_TIMELINE_OFFSET_FRAMES = 86400
_NOMINAL_FPS = 24

_TL_FORMATS = {
    (24, True,  False): ("872211b5dcf937400000000000000000",
        "800a2c10011a2208800f10b8081d0000803f20800f28b808350000803f38800f40b80848ffffffff0f60f6bde8bf4b100120f6bde8bf4b"),
    (24, False, False): ("00000000000038400000000000000000",
        "800a2c10011a2208800f10b8081d0000803f20800f28b808350000803f38800f40b80848ffffffff0f60b08889ca4b100120b08889ca4b"),
    (30, True,  False): ("286b55e253f83d400000000000000000",
        "800a2c10011a2208800f10b8081d0000803f20800f28b808350000803f38800f40b80848ffffffff0f60c4ecaba214100120c4ecaba214"),
    (30, True,  True):  ("286b55e253f84d400000000000000000",
        "800a2c10011a2208800f10b8081d0000803f20800f28b808350000803f38800f40b80848ffffffff0f60d8b3fe9914100120d8b3fe9914"),
}


from drp import sinais as _sn
from media import fftools
from media import sonda as _sonda

def _tl_format(fps: float, interlaced: bool = False):
    nominal = max(1, round(float(fps or 24.0)))
    ntsc = abs(float(fps or 0) - nominal) > 1e-3
    return _TL_FORMATS.get((nominal, ntsc, bool(interlaced)))


def _tc_to_frames(tc: str, nominal: int | None = None) -> int:
    n = int(nominal) if nominal else _NOMINAL_FPS
    h, m, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
    return ((h * 60 + m) * 60 + s) * n + f


from media.safepath import forma_windows as _forma_windows


def _abs(caminho: str) -> str:
    if _forma_windows(caminho):
        return ntpath.normpath(caminho)
    return os.path.abspath(caminho)


def _nome(caminho: str) -> str:
    if _forma_windows(caminho):
        return ntpath.basename(caminho)
    return os.path.basename(caminho)


def _pasta(caminho: str) -> str:
    if _forma_windows(caminho):
        return ntpath.dirname(ntpath.normpath(caminho))
    return os.path.dirname(os.path.abspath(caminho))


def _reel(name: str) -> str:
    return Path(_nome(name)).stem if name else name


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _xml_field(block: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.DOTALL)
    return m.group(1) if m else None


def _set_field(block: str, tag: str, value: str) -> str:
    novo = f"<{tag}>{value}</{tag}>"
    if re.search(rf"<{tag}>.*?</{tag}>", block, re.DOTALL):
        return re.sub(rf"<{tag}>.*?</{tag}>", lambda _m: novo, block,
                      count=1, flags=re.DOTALL)
    if re.search(rf"<{tag}/>", block):
        return re.sub(rf"<{tag}/>", lambda _m: novo, block, count=1)
    return block


_META_CACHE: dict[str, tuple] = {}
_SONDA_LENTA: set[str] = set()
_TL_FPS: float = 24.0
_META_CACHE_OFFLINE: set[str] = set()


def _media_meta(media_path: str) -> tuple:
    if media_path in _META_CACHE:
        return _META_CACHE[media_path]
    nb = fps = tc = None
    try:
        j = _sonda.ler(media_path)
        st = _sonda.primeiro(j, "video")
        rfr = st.get("r_frame_rate")
        if rfr and "/" in rfr:
            num, den = (int(x) for x in rfr.split("/"))
            fps = num / den if den else None
        if st.get("nb_frames") and str(st["nb_frames"]).isdigit():
            nb = int(st["nb_frames"])
        elif fps and st.get("duration"):
            try:
                nb = int(round(float(st["duration"]) * fps))
            except Exception:
                pass
        tc = (st.get("tags", {}) or {}).get("timecode") \
            or ((j.get("format", {}).get("tags", {}) or {}).get("timecode"))
        if not tc and not st:
            tc = (_sonda.primeiro(j, "audio").get("tags", {}) or {}).get("timecode")
        if not tc and st:
            tc = "00:00:00:00"
    except _sp.TimeoutExpired:
        _SONDA_LENTA.add(media_path)
        return (None, None, None)
    except Exception:
        pass
    _META_CACHE[media_path] = (nb, fps, tc)
    return _META_CACHE[media_path]


def _media_timemap(media_path: str, speed_ratio: float, seg: dict | None = None,
                   tl_fps: float | None = None) -> str | None:
    nb, fps, _ = _media_meta(media_path)
    if nb is None or not fps:
        return None
    if seg and seg.get("motion_effect_type") == "ramp" and seg.get("source_offset_map"):
        off = seg["source_offset_map"]
        ss = int(seg.get("source_start_frames") or 0)
        f = float(tl_fps or fps)
        if _RAMP_MODE == "dense":
            return build_ramp_timemap(nb, fps, off, ss, f)
        return build_adaptive_ramp_timemap(nb, fps, off, ss, f)
    if abs(speed_ratio - 1.0) < 1e-3:
        return timemap_encode({"kind": "const", "tag": 0x02, "value": (nb - 1) / fps})
    return build_speed_timemap(nb, fps, speed_ratio)


def _regen_time_blob(hexstr: str, tc_str: str | None, nb_frames: int | None) -> str:
    raw = bytearray.fromhex(hexstr)

    def _vpos(key: str):
        kb = key.encode("utf-16-be")
        i = raw.find(kb)
        return (i + len(kb)) if i >= 0 else None

    if tc_str:
        p = _vpos("Timecode")
        if p is not None and int.from_bytes(raw[p:p + 4], "big") == 10:
            ln = int.from_bytes(raw[p + 5:p + 9], "big")
            new = tc_str.encode("utf-16-be")
            if len(new) == ln:
                raw[p + 9:p + 9 + ln] = new
    if nb_frames:
        p = _vpos("NumFrames")
        if p is not None and int.from_bytes(raw[p:p + 4], "big") == 2:
            raw[p + 5:p + 9] = int(nb_frames).to_bytes(4, "big")
    return bytes(raw).hex()


def _patch_fieldsblob_start(fb_hex: str, start: int) -> str:
    dec = fieldsblob_decode(fb_hex)
    for f1 in dec["fields"]:
        if f1["field"] == 1 and f1["wire"] == 2 and isinstance(f1["value"], list):
            for f1b in f1["value"]:
                if f1b["field"] == 1 and isinstance(f1b["value"], list):
                    for f in f1b["value"]:
                        if f["field"] == 21 and f["wire"] == 0:
                            f["value"] = start
    return fieldsblob_encode(dec)


_TRANSITION_TEMPLATE = (
    '<Sm2TiTransition DbId="{dbid}">'
    '<FieldsBlob>{fieldsblob}</FieldsBlob>'
    '<PrettyType>{prettytype}</PrettyType>'
    '<Name>Transition</Name>'
    '<Start>{start}</Start>'
    '<Duration>{duration}</Duration>'
    '<LinkedItemSync/>'
    '<WasDisbanded>false</WasDisbanded>'
    '<MarkersBA/>'
    '<UiMemento>0</UiMemento>'
    '<Flags>0</Flags>'
    '<PriorityIndex>0</PriorityIndex>'
    '{effectfilters}'
    '<ImportExportMetadataBA/>'
    '<RenderTextEnabled>true</RenderTextEnabled>'
    '<RenderTextGanged>true</RenderTextGanged>'
    '<RenderTextPrefixed>true</RenderTextPrefixed>'
    '<AlignmentType>{align}</AlignmentType>'
    '<Position>{pos}</Position>'
    '</Sm2TiTransition>'
)
_TRANSITION_FB_BASE = ("0000000200000024800a0d1a07080410a689c857a801932c12120000002c"
                       "789c636660642016000000da0005")


def _patch_transition_start(fb_hex: str, start: int) -> str:
    dec = fieldsblob_decode(fb_hex)
    for f1 in dec["fields"]:
        if f1["field"] == 1 and f1["wire"] == 2 and isinstance(f1["value"], list):
            for f in f1["value"]:
                if f["field"] == 21 and f["wire"] == 0:
                    f["value"] = start
    return fieldsblob_encode(dec)


_DISSOLVE_EFFECT_FILTERS = (
    "<EffectFiltersBA>000000020000003a800a37081238024a004a004a004a004a29088b015224ffffffe0"
    "0000000000000c000000000000000000d800000000000c00000000000000f03f</EffectFiltersBA>")


def _make_transition(start: int, duration: int, align: int = 2, kind: str = "video") -> str:
    audio = kind == "audio"
    return _TRANSITION_TEMPLATE.format(
        dbid=_new_id(),
        fieldsblob=_patch_transition_start(_TRANSITION_FB_BASE, int(start)),
        prettytype="Cross Fade 0DB" if audio else "Cross Dissolve",
        effectfilters="<EffectFiltersBA/>" if audio else _DISSOLVE_EFFECT_FILTERS,
        start=int(start), duration=int(duration), align=int(align), pos=int(align),
    )


ESQUELETO_PADRAO = "skeleton_r19.drt"


def _skeleton_path() -> str:
    here = Path(__file__).resolve().parent / ESQUELETO_PADRAO
    if here.exists():
        return str(here)
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cand = Path(base) / "drp" / ESQUELETO_PADRAO
        if cand.exists():
            return str(cand)
    return str(here)


def _new_id() -> str:
    return str(uuid.uuid4())


_UUID_RE = re.compile(r'DbId="([0-9a-fA-F-]{36})"')


def _regen_zstd_blob(hexstr: str, remap: dict[str, str]) -> str:
    try:
        import zstandard
    except Exception:
        return hexstr
    raw = bytes.fromhex(hexstr)
    if len(raw) < 13 or raw[9:13].hex() != "28b52ffd":
        return hexstr
    ver, flag, z = raw[0:4], raw[8:9], raw[9:]
    try:
        dec = zstandard.ZstdDecompressor().decompress(z)
    except Exception:
        return hexstr
    for old, new in remap.items():
        dec = dec.replace(old.encode("utf-16-be"), new.encode("utf-16-be"))
    comp = zstandard.ZstdCompressor().compress(dec)
    payload = flag + comp
    return (ver + len(payload).to_bytes(4, "big") + payload).hex()


def _regen_clip_blob(hexstr: str, abspath: str) -> str:
    try:
        import zstandard
        from tools.inspect_drp import pb_decode, pb_encode
    except Exception:
        return hexstr
    raw = bytes.fromhex(hexstr)
    if len(raw) < 13 or raw[9:13].hex() != "28b52ffd":
        return hexstr
    ver, flag = raw[0:4], raw[8:9]
    try:
        d = zstandard.ZstdDecompressor().decompress(raw[9:])
        fields = pb_decode(d)
    except Exception:
        return hexstr
    dirp = _pasta(abspath).encode("utf-8")
    name = _nome(abspath).encode("utf-8")
    mstr = mus = None
    try:
        if _abs(abspath) in _META_CACHE_OFFLINE:
            raise OSError("offline")
        mt = os.path.getmtime(abspath)
        mstr = time.ctime(mt).encode("utf-8")
        mus = int(mt) * 1_000_000
    except Exception:
        pass
    for f in fields:
        if f["wire"] == 2 and f["field"] == 1:
            f["value"] = dirp
        elif f["wire"] == 2 and f["field"] in (2, 6):
            f["value"] = name
        elif f["wire"] == 2 and f["field"] == 7:
            f["value"] = _new_id().encode("utf-8")
        elif f["wire"] == 2 and f["field"] == 3 and mstr:
            f["value"] = mstr
        elif f["wire"] == 0 and f["field"] == 13 and mus:
            f["value"] = mus
    try:
        comp = zstandard.ZstdCompressor().compress(pb_encode(fields))
    except Exception:
        return hexstr
    payload = flag + comp
    return (ver + len(payload).to_bytes(4, "big") + payload).hex()


def _uniquify(block: str, top_db_id: str | None = None) -> str:
    ids = []
    for m in _UUID_RE.finditer(block):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    if not ids:
        return block
    remap = {old: _new_id() for old in ids}
    if top_db_id is not None:
        remap[ids[0]] = top_db_id
    block = _UUID_RE.sub(lambda m: f'DbId="{remap.get(m.group(1), m.group(1))}"', block)
    block = re.sub(r"<FieldsBlob>([0-9a-fA-F]+)</FieldsBlob>",
                   lambda m: "<FieldsBlob>" + _regen_zstd_blob(m.group(1), remap) + "</FieldsBlob>",
                   block)
    return block


def _make_pool_clip(shape: str, name: str, db_id: str, uniq_id: str, matched_path: str) -> str:
    b = _uniquify(shape, top_db_id=db_id)
    b = re.sub(r"<Clip>([0-9a-fA-F]{40,})</Clip>",
               lambda m: "<Clip>" + _regen_clip_blob(m.group(1), matched_path) + "</Clip>", b)
    nb, _fps, tc = _media_meta(matched_path)
    b = re.sub(r"<Time>([0-9a-fA-F]{20,})</Time>",
               lambda m: "<Time>" + _regen_time_blob(m.group(1), tc, nb) + "</Time>", b)
    b = _set_field(b, "Name", _xml_escape(name))
    b = _set_field(b, "UniqueMediaPoolItemId", uniq_id)
    if _e_still(matched_path):
        meta = _formato_meta(matched_path)
        if meta.get("w"):
            b = re.sub(r"<Geometry>([0-9a-fA-F]+)</Geometry>",
                       lambda g: "<Geometry>" + _patch_geometry(g.group(1), meta["w"], meta["h"])
                       + "</Geometry>", b)
        for tag in ("Time", "Geometry", "Proxy", "VideoMetadata"):
            b = re.sub(rf"<{tag}>([0-9a-fA-F]+)</{tag}>",
                       lambda m, t=tag: f"<{t}>" + _novos_unique_ids(m.group(1)) + f"</{t}>", b)
    elif not _e_mxf(matched_path) or _e_arriraw(matched_path):
        b = _ajustar_formato_nativo(b, matched_path)
    return b


FORMATO_MXF, FORMATO_OUTRO = 4, 2
FLAG_VIDEO, FLAG_AUDIO = 16384, 32768
FORMAS_NATIVAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "formas_r19.xml")
FORMA_ARRIRAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "formas_r21_arriraw.xml")
FORMA_STILL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "formas_r21_still.xml")
_EXT_STILL = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".psd", ".tga"}
_FORMAS_NATIVAS: dict[str, str] = {}
_FORMATO_CACHE: dict[str, dict] = {}
_CODEC_AUDIO = {"aac": b"AAC"}


def _arquivo_de_video(caminho: str) -> bool:
    if os.path.splitext(caminho or "")[1].lower() not in (".mp4", ".mov", ".m4v", ".mxf"):
        return False
    return bool(_formato_meta(caminho).get("w")) or _e_arriraw(caminho)


def _e_mxf(caminho: str) -> bool:
    return os.path.splitext(caminho or "")[1].lower() == ".mxf"


def _formas_nativas() -> dict[str, str]:
    if not _FORMAS_NATIVAS:
        with open(FORMAS_NATIVAS, encoding="utf-8") as f:
            x = f.read()
        for b in re.findall(r"<Sm2MpVideoClip\b.*?</Sm2MpVideoClip>", x, re.DOTALL):
            nome = re.search(r"<Name>([^<]*)</Name>", b).group(1)
            _FORMAS_NATIVAS["braw" if nome.lower().endswith(".braw") else "video"] = b
        _FORMAS_NATIVAS["audio"] = re.search(r"<Sm2MpAudioClip\b.*?</Sm2MpAudioClip>",
                                             x, re.DOTALL).group(0)
        with open(FORMA_ARRIRAW, encoding="utf-8") as f:
            _FORMAS_NATIVAS["arriraw"] = re.search(r"<Sm2MpVideoClip\b.*?</Sm2MpVideoClip>",
                                                   f.read(), re.DOTALL).group(0)
        with open(FORMA_STILL, encoding="utf-8") as f:
            x = f.read()
        _FORMAS_NATIVAS["still"] = re.search(r"<Sm2MpVideoClip\b.*?</Sm2MpVideoClip>",
                                             x, re.DOTALL).group(0)
        _FORMAS_NATIVAS["timemap_still"] = re.search(r"<TimemapStill>([0-9a-f]+)<", x).group(1)
    return _FORMAS_NATIVAS


def _familia(caminho: str) -> str:
    if _e_still(caminho):
        return "still"
    if _e_arriraw(caminho):
        return "arriraw"
    return "braw" if os.path.splitext(caminho or "")[1].lower() == ".braw" else "video"


_ARRIRAW_CACHE: dict[str, tuple[int, int] | None] = {}
_UL_OP1A = re.compile(rb"\x06\x0e\x2b\x34\x04\x01\x01[\x01-\x0f]\x0d\x01\x02\x01\x01\x01[\x00-\xff]\x00")


def _raster_arriraw(caminho: str) -> tuple[int, int] | None:
    if caminho in _ARRIRAW_CACHE:
        return _ARRIRAW_CACHE[caminho]
    r = None
    if _e_mxf(caminho) and _abs(caminho) not in _META_CACHE_OFFLINE:
        try:
            import struct
            with open(caminho, "rb") as f:
                cab = f.read(200_000)
            if _UL_OP1A.search(cab) and b"ARRI" in cab and not _formato_meta(caminho).get("w"):
                w = cab.find(bytes.fromhex("32030004"))
                h = cab.find(bytes.fromhex("32020004"))
                if w >= 0 and h >= 0:
                    r = (struct.unpack(">I", cab[w + 4:w + 8])[0],
                         struct.unpack(">I", cab[h + 4:h + 8])[0])
            if caminho in _SONDA_LENTA:
                return None
        except OSError:
            return None
    _ARRIRAW_CACHE[caminho] = r
    return r


def _e_still(caminho: str) -> bool:
    return os.path.splitext(caminho or "")[1].lower() in _EXT_STILL


def _e_arriraw(caminho: str) -> bool:
    return _raster_arriraw(caminho) is not None


def _braw_crop(caminho: str) -> tuple[int, int] | None:
    import struct
    try:
        with open(caminho, "rb") as f:
            tam = os.fstat(f.fileno()).st_size

            def filhos(ini, fim):
                while ini < fim - 8:
                    f.seek(ini)
                    sz, tipo = struct.unpack(">I4s", f.read(8))
                    cab = 8
                    if sz == 1:
                        sz, cab = struct.unpack(">Q", f.read(8))[0], 16
                    if sz < cab:
                        return
                    yield tipo, ini + cab, ini + sz
                    ini += sz

            moov = next((a, b) for t, a, b in filhos(0, tam) if t == b"moov")
            meta = next((a, b) for t, a, b in filhos(*moov) if t == b"meta")
            chaves: list[str] = []
            for t, a, b in filhos(meta[0], meta[1]):
                if t == b"keys":
                    f.seek(a + 4)
                    n = struct.unpack(">I", f.read(4))[0]
                    o = a + 8
                    for _ in range(min(n, 500)):
                        f.seek(o)
                        ks = struct.unpack(">I", f.read(4))[0]
                        f.read(4)
                        chaves.append(f.read(max(0, ks - 8)).decode("utf-8", "replace"))
                        o += ks
                elif t == b"ilst":
                    for it, ia, ib in filhos(a, b):
                        idx = struct.unpack(">I", it)[0]
                        if not 0 < idx <= len(chaves) or chaves[idx - 1] != "crop_size":
                            continue
                        for dt, da, _db in filhos(ia, ib):
                            if dt == b"data":
                                f.seek(da + 8)
                                w, h = struct.unpack(">ff", f.read(8))
                                return (int(w), int(h)) if w > 0 and h > 0 else None
    except Exception:
        return None
    return None


def _formato_meta(caminho: str) -> dict:
    if caminho in _FORMATO_CACHE:
        return _FORMATO_CACHE[caminho]
    meta: dict = {}
    if _abs(caminho) not in _META_CACHE_OFFLINE:
        try:
            for st in _sonda.ler(caminho).get("streams") or []:
                if st.get("codec_type") == "video" and "w" not in meta:
                    tag = str(st.get("codec_tag_string") or "")
                    if re.fullmatch(r"[A-Za-z0-9]{4}", tag):
                        meta["vtag"] = tag.encode("ascii")
                    if st.get("width") and st.get("height"):
                        meta["w"], meta["h"] = int(st["width"]), int(st["height"])
                elif st.get("codec_type") == "audio" and "acodec" not in meta:
                    nome = str(st.get("codec_name") or "")
                    meta["acodec"] = (b"Linear PCM" if nome.startswith("pcm_")
                                      else _CODEC_AUDIO.get(nome))
                    bits = {"16": 1, "24": 2, "32": 3}.get(re.sub(r"\D", "", nome[5:8]))
                    if bits:
                        meta["abits"] = bits
        except _sp.TimeoutExpired:
            _SONDA_LENTA.add(caminho)
            return {}
        except Exception:
            meta = {}
    _FORMATO_CACHE[caminho] = meta
    return meta


def _patch_formato_clip(hexstr: str, formato: int | None, flag: int | None,
                        codec: bytes | None) -> str:
    try:
        import zstandard
        from tools.inspect_drp import pb_decode, pb_encode
    except Exception:
        return hexstr
    raw = bytes.fromhex(hexstr)
    if len(raw) < 13 or raw[9:13].hex() != "28b52ffd":
        return hexstr
    ver, fl = raw[0:4], raw[8:9]
    try:
        campos = pb_decode(zstandard.ZstdDecompressor().decompress(raw[9:]))
    except Exception:
        return hexstr
    for f in campos:
        if f["field"] == 15 and f["wire"] == 0 and formato is not None:
            f["value"] = formato
        elif f["field"] == 18 and f["wire"] == 0 and flag is not None:
            f["value"] = flag
        elif f["field"] == 5 and f["wire"] == 2 and codec:
            f["value"] = codec
    comp = zstandard.ZstdCompressor().compress(pb_encode(campos))
    payload = fl + comp
    return (ver + len(payload).to_bytes(4, "big") + payload).hex()


_UUID16 = re.compile(rb"(?:\x00[0-9a-f]){8}\x00-(?:(?:\x00[0-9a-f]){4}\x00-){3}(?:\x00[0-9a-f]){12}")


def _novos_unique_ids(hexstr: str) -> str:
    raw = bytes.fromhex(hexstr)
    novo = _UUID16.sub(lambda _m: _new_id().encode("utf-16-be"), raw)
    return novo.hex() if len(novo) == len(raw) else hexstr


def _patch_geometry(hexstr: str, w: int, h: int) -> str:
    raw = bytearray.fromhex(hexstr)
    k = "Resolution".encode("utf-16-be")
    i = raw.find(k)
    if i < 0:
        return hexstr
    i += len(k)
    if raw[i:i + 4] != b"\x00\x00\x00\x0c" or raw[i + 5:i + 9] != b"\x00\x00\x00\x10":
        return hexstr
    w0 = int.from_bytes(raw[i + 9:i + 17], "big")
    h0 = int.from_bytes(raw[i + 17:i + 25], "big")
    raw[i + 9:i + 17] = int(w).to_bytes(8, "big")
    raw[i + 17:i + 25] = int(h).to_bytes(8, "big")
    k = "FrameSize".encode("utf-16-be")
    j = raw.find(k)
    if j >= 0 and w0 and h0:
        j += len(k)
        if raw[j:j + 4] == b"\x00\x00\x00\x02":
            fs0 = int.from_bytes(raw[j + 5:j + 9], "big")
            raw[j + 5:j + 9] = round(fs0 * w * h / (w0 * h0)).to_bytes(4, "big")
    return bytes(raw).hex()


def _patch_time_fps(hexstr: str, fps: float) -> str:
    import struct
    raw = bytearray.fromhex(hexstr)
    k = "FrameRate".encode("utf-16-be")
    i = raw.find(k)
    if i < 0 or not fps:
        return hexstr
    i += len(k)
    if raw[i:i + 4] != b"\x00\x00\x00\x0c" or raw[i + 5:i + 9] != b"\x00\x00\x00\x10":
        return hexstr
    raw[i + 9:i + 17] = struct.pack("<d", float(fps))
    return bytes(raw).hex()


RADIOMETRIA_BRAW = "forma"


def _ajustar_formato_nativo(b: str, caminho: str) -> str:
    meta = _formato_meta(caminho)
    _nb, fps, tc = _media_meta(caminho)
    familia = _familia(caminho)
    braw = familia == "braw"
    tamanho = (_braw_crop(caminho) if braw and _abs(caminho) not in _META_CACHE_OFFLINE
               else None) or (_raster_arriraw(caminho) if familia == "arriraw" else None) \
        or ((meta["w"], meta["h"]) if meta.get("w") else None)
    codec_video = None if familia in ("braw", "arriraw") else meta.get("vtag")

    def _video(m):
        blk = m.group(0)
        blk = re.sub(r"<Clip>([0-9a-fA-F]{40,})</Clip>",
                     lambda c: "<Clip>" + _patch_formato_clip(c.group(1), None, None, codec_video)
                     + "</Clip>", blk)
        if tamanho:
            blk = re.sub(r"<Geometry>([0-9a-fA-F]+)</Geometry>",
                         lambda g: "<Geometry>" + _patch_geometry(g.group(1), *tamanho)
                         + "</Geometry>", blk)
        if braw and RADIOMETRIA_BRAW == "generica":
            generica = re.search(r"<Radiometry>([0-9a-fA-F]*)</Radiometry>",
                                 _formas_nativas()["video"]).group(1)
            blk = re.sub(r"<Radiometry>[0-9a-fA-F]*</Radiometry>",
                         lambda _r: f"<Radiometry>{generica}</Radiometry>", blk)
        if fps:
            blk = re.sub(r"<Time>([0-9a-fA-F]+)</Time>",
                         lambda t: "<Time>" + _patch_time_fps(t.group(1), fps) + "</Time>", blk)
        return blk

    b = re.sub(r"<BtVideoInfo\b.*?</BtVideoInfo>", _video, b, count=1, flags=re.DOTALL)
    _mst, dur_sec, sr, ch, dts = _audio_meta(caminho)
    inicio = _tc_to_frames(tc, round(fps)) / fps if tc and fps else 0.0

    def _audio(m):
        blk = m.group(0)
        blk = re.sub(r"<Clip>([0-9a-fA-F]{40,})</Clip>",
                     lambda c: "<Clip>" + _patch_formato_clip(c.group(1), None, None,
                                                              meta.get("acodec")) + "</Clip>", blk)
        return re.sub(r"<TracksBA>([0-9a-fA-F]+)</TracksBA>",
                      lambda t: "<TracksBA>" + _regen_audio_tracks_ba(t.group(1), sr, ch, dts, inicio)
                      + "</TracksBA>", blk)

    b = re.sub(r"<BtAudioInfo\b.*?</BtAudioInfo>", _audio, b, flags=re.DOTALL)
    if dur_sec:
        b = re.sub(r"<FieldsBlob>([0-9a-fA-F]{40,})</FieldsBlob>",
                   lambda f: "<FieldsBlob>" + _patch_media_extents(f.group(1), inicio, dur_sec)
                   + "</FieldsBlob>", b, count=1)
    for tag in ("Time", "Geometry", "Proxy", "VideoMetadata", "TracksBA"):
        b = re.sub(rf"<{tag}>([0-9a-fA-F]+)</{tag}>",
                   lambda m, t=tag: f"<{t}>" + _novos_unique_ids(m.group(1)) + f"</{t}>", b)
    return b


def _effect_filters_ba_for(seg: dict) -> str | None:
    escala = _ESCALA_DO_REFORMAT.get(seg.get("reframe_mode") or "")
    retime = seg.get("retime")
    for e in (seg.get("effects") or []):
        if e.get("category") != "geometric" or not e.get("reproducible"):
            continue
        p = e.get("params") or {}
        kw = {"escala": escala, "retime": retime}
        if "pos_x" in p: kw["pos_x"] = p["pos_x"] / 1000.0
        if "pos_y" in p: kw["pos_y"] = p["pos_y"] / 1000.0
        if "scale_x" in p: kw["zoom_x"] = p["scale_x"]
        if "scale_y" in p: kw["zoom_y"] = p["scale_y"]
        if "rot_z" in p: kw["rotation"] = p["rot_z"]
        if p.get("pitch"): kw["pitch"] = p["pitch"]
        if p.get("yaw"): kw["yaw"] = p["yaw"]
        if "opacity" in p: kw["opacity"] = p["opacity"]
        if p.get("flip_x"): kw["flip_x"] = True
        if p.get("flip_y"): kw["flip_y"] = True
        if p.get("composite"): kw["composite"] = p["composite"]
        if p.get("crop_frac"):
            c = p["crop_frac"]
            kw.update(crop_l=c.get("l"), crop_r=c.get("r"), crop_t=c.get("t"), crop_b=c.get("b"))
            if p.get("crop_suave"):
                kw["crop_suave"] = p["crop_suave"]
        if p.get("kf"):
            k = p["kf"]
            kw["animado"] = {"zoom_x": k.get("zoom"), "zoom_y": k.get("zoom_y") or k.get("zoom"),
                             "pos_x": k.get("pos_x"), "pos_y": k.get("pos_y"),
                             "rotation": k.get("rot")}
        if "crop_l" in p: kw["crop_l"] = (p["crop_l"] + 999.0) / 1998.0
        if "crop_b" in p: kw["crop_b"] = (p["crop_b"] + 999.0) / 1998.0
        if "crop_r" in p: kw["crop_r"] = (999.0 - p["crop_r"]) / 1998.0
        if "crop_t" in p: kw["crop_t"] = (999.0 - p["crop_t"]) / 1998.0
        if len(kw) > 2 or escala or retime:
            return build_effect_filters_ba(**kw)
    return build_effect_filters_ba(escala=escala, retime=retime) if (escala or retime) else None


_ESCALA_DO_REFORMAT = {"pillarbox": "fit", "center_crop": "fill", "keep_size": "crop",
                       "stretch": "stretch"}


FLAG_DESLIGADO = "2"


def _flags_de(block: str, seg: dict) -> str:
    return _set_field(block, "Flags", FLAG_DESLIGADO) if seg.get("disabled") else block


def _make_timeline_clip(shape: str, seg: dict, db_id: str, matched_path: str,
                        offset_corr: int = 0, start_override: int | None = None,
                        dur_override: int | None = None, in_extra: int = 0) -> str:
    base_start = start_override if start_override is not None else _tc_to_frames(seg["timeline_tc_in"])
    start = base_start + _TIMELINE_OFFSET_FRAMES
    dur = int(dur_override) if dur_override is not None else int(seg["duration_frames"])
    sr = float(seg.get("speed_ratio") or 1.0) or 1.0
    rel = float(seg.get("relative_speed") or sr) or sr
    is_ramp = seg.get("motion_effect_type") == "ramp" and seg.get("source_offset_map")
    reverso = seg.get("motion_effect_type") == "reverse"
    if (seg.get("media_ref") or {}).get("descriptor_type") == "prproj" and not is_ramp and not reverso:
        _nb_a, fps_arq, _tc_a = _media_meta(matched_path)
        fps_premiere = float(seg.get("source_fps") or 0)
        if fps_arq and fps_premiere and abs(fps_premiere / fps_arq - 1) > 1e-3:
            sr *= fps_premiere / fps_arq
    in_pt = 0 if is_ramp else round((int(seg.get("source_start_frames") or 0)
                                     + int(offset_corr or 0)) * sr) + int(in_extra or 0)
    if reverso:
        _nb, _fps_m, _ = _media_meta(matched_path)
        v = abs(float(seg.get("velocidade_fonte") or 1.0)) or 1.0
        if _nb and _fps_m:
            y_ini = float(seg.get("source_start_exato") or seg.get("source_start_frames") or 0) / _fps_m
            in_pt = round((reverso_y0(_nb, _fps_m, v) - y_ini) / v * _TL_FPS) + int(in_extra or 0)
    name = _nome(matched_path)
    reel = _reel(_nome(matched_path))

    block = _uniquify(shape)
    block = _set_field(block, "Name", _xml_escape(name))
    block = _set_field(block, "Start", str(start))
    block = _set_field(block, "Duration", str(dur))
    if in_pt == 0:
        block = re.sub(r"<In>.*?</In>", "<In/>", block, count=1, flags=re.DOTALL)
    else:
        block = _set_field(block, "In", str(in_pt))
    block = _set_field(block, "MediaRef", db_id)
    block = _set_field(block, "MediaFilePath", _xml_escape(_abs(matched_path)))
    block = _set_field(block, "MediaReelNumber", _xml_escape(reel))
    block = _flags_de(block, seg)

    _nb, fps, tc = _media_meta(matched_path)
    if tc and fps:
        block = _set_field(block, "MediaStartTime", repr(_tc_to_frames(tc, round(fps)) / fps))
    if fps:
        block = _set_field(block, "MediaFrameRate", framerate_encode(fps))
    if reverso:
        _nb, _fps_m, _ = _media_meta(matched_path)
        tm = build_reverse_timemap(_nb, _fps_m, abs(float(seg.get("velocidade_fonte") or 1.0))) \
            if (_nb and _fps_m) else None
    else:
        tm = _media_timemap(matched_path, rel, seg=seg,
                            tl_fps=float(seg.get("source_fps") or 0) or None)
    if tm:
        block = _set_field(block, "MediaTimemapBA", tm)

    if _e_still(matched_path):
        block = _set_field(block, "MediaFrameRate", framerate_encode(_TL_FPS))
        block = _set_field(block, "MediaTimemapBA",
                           _novos_unique_ids(_formas_nativas()["timemap_still"]))
        block = _set_field(block, "MediaStartTime", "0")
        block = re.sub(r"<In>.*?</In>", "<In/>", block, count=1, flags=re.DOTALL)

    efb = _effect_filters_ba_for(seg)
    if efb:
        if "<EffectFiltersBA>" in block:
            block = _set_field(block, "EffectFiltersBA", efb)
        else:
            block = block.replace(
                "<UseOppositeSrcForLeftEye>",
                f"<EffectFiltersBA>{efb}</EffectFiltersBA><UseOppositeSrcForLeftEye>", 1)

    fb = _xml_field(block, "FieldsBlob")
    if fb:
        block = _set_field(block, "FieldsBlob", _patch_fieldsblob_start(fb, start))
    return block


def _make_video_track(track_shape: str, clips_xml: list[str]) -> str:
    tr = re.sub(r'(<Sm2TiTrack\s+DbId=")[^"]+(")',
                lambda m: m.group(1) + _new_id() + m.group(2), track_shape, count=1)
    items = "<Items>" + "".join(f"<Element>{c}</Element>" for c in clips_xml) + "</Items>"
    return re.sub(r"<Items>.*?</Items>", lambda _m: items, tr, count=1, flags=re.DOTALL)


def _empty_all_items(vec_xml: str) -> str:
    return re.sub(r"<Items>.*?</Items>", "<Items></Items>", vec_xml, flags=re.DOTALL)


_META_CACHE_A: dict[str, tuple] = {}


def _audio_meta(path: str) -> tuple:
    if path in _META_CACHE_A:
        return _META_CACHE_A[path]
    sr = dur = ch = dts = None
    mst = 0.0
    try:
        j = _sonda.ler(path)
        st = _sonda.primeiro(j, "audio")
        if st.get("sample_rate") and str(st["sample_rate"]).isdigit():
            sr = int(st["sample_rate"])
        if st.get("channels"):
            ch = int(st["channels"])
        if st.get("duration_ts") and str(st["duration_ts"]).isdigit():
            dts = int(st["duration_ts"])
        if sr and dts:
            dur = dts / sr
        elif j.get("format", {}).get("duration"):
            dur = float(j["format"]["duration"])
            if sr and dur:
                dts = int(round(dur * sr))
        tr = (j.get("format", {}).get("tags", {}) or {}).get("time_reference")
        if tr and str(tr).isdigit() and sr:
            mst = int(tr) / sr
    except _sp.TimeoutExpired:
        _SONDA_LENTA.add(path)
        return (mst, dur, sr, ch, dts)
    except Exception:
        pass
    _META_CACHE_A[path] = (mst, dur, sr, ch, dts)
    return _META_CACHE_A[path]


def _regen_audio_tracks_ba(hexstr: str, sr: int | None, ch: int | None,
                           dts: int | None, start_sec: float) -> str:
    import struct
    raw = bytearray.fromhex(hexstr)

    def voff(key: str):
        i = raw.find(key.encode("utf-16-be"))
        return (i + len(key.encode("utf-16-be")) + 5) if i >= 0 else None

    if sr:
        p = voff("SampleRate")
        if p is not None:
            raw[p:p + 4] = int(sr).to_bytes(4, "big")
    if ch:
        p = voff("NumChannels")
        if p is not None:
            raw[p:p + 4] = int(ch).to_bytes(4, "big")
    if dts:
        p = voff("Duration")
        if p is not None:
            raw[p:p + 8] = int(dts).to_bytes(8, "big")
    if start_sec:
        p = voff("StartTime")
        if p is not None:
            raw[p:p + 8] = struct.pack(">d", float(start_sec))
    return bytes(raw).hex()


def _replace_in_zstd(hexstr: str, old: bytes, new: bytes) -> str:
    if len(old) != len(new):
        return hexstr
    try:
        import zstandard
    except Exception:
        return hexstr
    raw = bytes.fromhex(hexstr)
    if len(raw) < 13 or raw[9:13].hex() != "28b52ffd":
        return hexstr
    ver, flag = raw[0:4], raw[8:9]
    try:
        dec = zstandard.ZstdDecompressor().decompress(raw[9:])
    except Exception:
        return hexstr
    if old not in dec:
        return hexstr
    comp = zstandard.ZstdCompressor().compress(dec.replace(old, new))
    payload = flag + comp
    return (ver + len(payload).to_bytes(4, "big") + payload).hex()


def _patch_media_extents(hexstr: str, start_sec: float, dur_sec: float) -> str:
    import struct
    try:
        import zstandard
    except Exception:
        return hexstr
    raw = bytes.fromhex(hexstr)
    if len(raw) < 13 or raw[9:13].hex() != "28b52ffd":
        return hexstr
    ver, flag = raw[0:4], raw[8:9]
    try:
        dec = bytearray(zstandard.ZstdDecompressor().decompress(raw[9:]))
    except Exception:
        return hexstr
    key = "MediaExtents".encode("utf-16-be")
    trocou = False
    k = dec.find(key)
    while k >= 0:
        p = k + len(key)
        if dec[p:p + 4] == (12).to_bytes(4, "big"):
            if start_sec is not None:
                dec[p + 9:p + 17] = struct.pack("<d", float(start_sec))
            if dur_sec:
                dec[p + 17:p + 25] = struct.pack("<d", float(dur_sec))
            trocou = True
        k = dec.find(key, p)
    if not trocou:
        return hexstr
    comp = zstandard.ZstdCompressor().compress(bytes(dec))
    payload = flag + comp
    return (ver + len(payload).to_bytes(4, "big") + payload).hex()


def _patch_channel_vec(hexstr: str, num_channels: int) -> str:
    import struct
    if num_channels <= 1:
        return hexstr
    try:
        import zstandard
    except Exception:
        return hexstr
    raw = bytes.fromhex(hexstr)
    if len(raw) < 13 or raw[9:13].hex() != "28b52ffd":
        return hexstr
    ver, flag = raw[0:4], raw[8:9]
    try:
        dec = bytearray(zstandard.ZstdDecompressor().decompress(raw[9:]))
    except Exception:
        return hexstr
    KEY_CI = "ChannelIdx".encode("utf-16-be")
    KEY_BD = "BitDepth".encode("utf-16-be")
    ci = bytes(dec).find(KEY_CI)
    if ci < 0:
        return hexstr
    bd = bytes(dec).find(KEY_BD, ci)
    if bd < 0:
        return hexstr
    entry_start = ci - 4
    entry_end = (bd - 4) + 4 + len(KEY_BD) + 4 + 1 + 4
    if entry_start < 0 or entry_end > len(dec):
        return hexstr
    entry_template = bytes(dec[entry_start:entry_end])
    ci_val_off = 4 + len(KEY_CI) + 4 + 1
    new_entries = bytearray()
    for ch in range(num_channels - 1, 0, -1):
        entry = bytearray(entry_template)
        struct.pack_into(">i", entry, ci_val_off, ch)
        new_entries.extend(entry)
    new_dec = bytearray(dec[:entry_start]) + new_entries + bytearray(dec[entry_end:])
    comp = zstandard.ZstdCompressor().compress(bytes(new_dec))
    payload = flag + comp
    return (ver + len(payload).to_bytes(4, "big") + payload).hex()


def _mapa_de_canais(hexstr: str, n: int, inicio: float | None, dur: float | None,
                    trilha: str, sr: int | None, bits: int | None) -> str:
    import copy
    import struct

    import zstandard
    from drp.ba_mapa import QBYTEARRAY, escrever_mapa, ler_mapa, valor
    from tools.inspect_drp import pb_decode, pb_encode

    raw = bytes.fromhex(hexstr)
    if n < 1 or len(raw) < 13 or raw[9:13].hex() != "28b52ffd":
        return hexstr
    campos = pb_decode(zstandard.ZstdDecompressor().decompress(raw[9:]))
    ba = campos[0]["value"][0]["value"]
    ver, itens, resto = ler_mapa(bytes(ba))
    _mv, modelo, mresto = ler_mapa(itens[0][1][2])
    _cv, cv_modelo, cv_resto = ler_mapa(valor(modelo, "ChannelVecBA")[2])
    c_ver, c_modelo, c_resto = ler_mapa(cv_modelo[0][1][2])

    def canal(k: int) -> list:
        c = [(key, copy.deepcopy(x)) for key, x in c_modelo if key != "ChannelIdx"]
        valor(c, "MediaRef")[2] = trilha
        if k:
            c.append(("ChannelIdx", [2, 0, k]))
        return [QBYTEARRAY, 0, escrever_mapa(c_ver, c, c_resto)]

    def registro(canais: list[tuple[str, int]], cabeca: list) -> list:
        reg = []
        for key, x in modelo:
            x = copy.deepcopy(x)
            if key == "MediaExtents" and inicio is not None and dur:
                x[2] = struct.pack("<dd", float(inicio), float(dur))
            elif key == "ChannelVecBA":
                x[2] = escrever_mapa(_cv, [(chave, canal(k)) for chave, k in canais], cv_resto)
            elif key == "BitDepth":
                if bits == 3:
                    continue
                if bits:
                    x[2] = bits
            reg.append((key, x))
        return [QBYTEARRAY, 0, escrever_mapa(_mv, cabeca + reg, mresto)]

    taxa = [("SampleRate", [3, 0, int(sr)])] if sr and int(sr) != 48000 else []
    if n == 1:
        novos = [("0", registro([("0", 0)], taxa))]
    elif n == 2:
        novos = [("0", registro([("1", 1), ("0", 0)], [("Type", [3, 0, 0])] + taxa))]
    else:
        novos = [(str(k), registro([("0", k)], [])) for k in range(n - 1, -1, -1)]
    campos[0]["value"][0]["value"] = escrever_mapa(ver, novos, resto)
    comp = zstandard.ZstdCompressor().compress(pb_encode(campos))
    payload = raw[8:9] + comp
    return (raw[0:4] + len(payload).to_bytes(4, "big") + payload).hex()


def _trilha_nativa(hexstr: str, sr, ch, dts, inicio, bits, layout) -> str:
    from drp.ba_mapa import QBYTEARRAY, escrever_mapa, ler_mapa, valor

    ver, itens, resto = ler_mapa(bytes.fromhex(hexstr))
    t_ver, trk, t_resto = ler_mapa(itens[0][1][2])
    for key, novo in (("StartTime", float(inicio or 0.0)), ("SampleRate", sr),
                      ("NumChannels", ch), ("Duration", dts), ("BitDepth", bits)):
        x = valor(trk, key)
        if x is not None and novo is not None:
            x[2] = novo
    trk = [(k, x) for k, x in trk if k != "ChannelLayout"]
    if layout:
        i = next((j for j, (k, _x) in enumerate(trk) if k == "BitDepth"), len(trk))
        trk.insert(i, ("ChannelLayout", [2, 0, int(layout)]))
    itens[0] = (itens[0][0], [QBYTEARRAY, 0, escrever_mapa(t_ver, trk, t_resto)])
    return _novos_unique_ids(escrever_mapa(ver, itens, resto).hex())


def _make_audio_pool_clip_nativo(name: str, db_id: str, uniq_id: str, matched_path: str) -> str:
    meta = _formato_meta(matched_path)
    aiff = os.path.splitext(matched_path)[1].lower() in (".aif", ".aiff")
    mst, dur_sec, sr, ch, dts = _audio_meta(matched_path)
    b = _uniquify(_formas_nativas()["audio"], top_db_id=db_id)
    b = re.sub(r"<Clip>([0-9a-fA-F]{40,})</Clip>",
               lambda m: "<Clip>" + _patch_formato_clip(
                   _regen_clip_blob(m.group(1), matched_path), None,
                   FLAG_AUDIO + 2 if aiff else None, meta.get("acodec")) + "</Clip>", b)
    layout = ch if ch and (ch == 1 or aiff) else None
    b = re.sub(r"<TracksBA>([0-9a-fA-F]+)</TracksBA>",
               lambda m: "<TracksBA>" + _trilha_nativa(m.group(1), sr, ch, dts, mst,
                                                       meta.get("abits"), layout) + "</TracksBA>", b)
    trilha = re.search(r'<BtAudioInfo DbId="([^"]+)"', b).group(1)
    if ch:
        b = re.sub(r"<FieldsBlob>([0-9a-fA-F]{40,})</FieldsBlob>",
                   lambda m: "<FieldsBlob>" + _mapa_de_canais(m.group(1), ch, mst, dur_sec, trilha,
                                                              sr, meta.get("abits"))
                   + "</FieldsBlob>", b, count=1)
    if ch == 2:
        b = _set_field(b, "VirtualAudioTracksBA", VIRTUAL_DUAL_MONO)
    b = _set_field(b, "Name", _xml_escape(name))
    b = _set_field(b, "UniqueMediaPoolItemId", uniq_id)
    return b


VIRTUAL_DUAL_MONO = (
    "00000001000000020000000200310000000c0000000054000000010000000200000014004300680061006e006e"
    "0065006c0073004200410000000c000000000c000000020000000100004002000000120041007500640069006f"
    "00540079007000650000000200000000010000000200300000000c000000005400000001000000020000001400"
    "4300680061006e006e0065006c0073004200410000000c000000000c0000000200000001000040010000001200"
    "41007500640069006f0054007900700065000000020000000001")


def _make_audio_pool_clip(shape: str, name: str, db_id: str, uniq_id: str, matched_path: str) -> str:
    if not _e_mxf(matched_path):
        return _make_audio_pool_clip_nativo(name, db_id, uniq_id, matched_path)
    b = _uniquify(shape, top_db_id=db_id)
    b = re.sub(r"<Clip>([0-9a-fA-F]{40,})</Clip>",
               lambda m: "<Clip>" + _regen_clip_blob(m.group(1), matched_path) + "</Clip>", b)
    mst, dur_sec, sr, ch, dts = _audio_meta(matched_path)
    old_sr = None
    m = re.search(r"<TracksBA>([0-9a-fA-F]+)</TracksBA>", b)
    if m:
        rb = bytes.fromhex(m.group(1))
        i = rb.find("SampleRate".encode("utf-16-be"))
        if i >= 0:
            p = i + len("SampleRate".encode("utf-16-be")) + 5
            old_sr = int.from_bytes(rb[p:p + 4], "big")
    b = re.sub(r"<TracksBA>([0-9a-fA-F]+)</TracksBA>",
               lambda m: "<TracksBA>" + _regen_audio_tracks_ba(m.group(1), sr, ch, dts, mst) + "</TracksBA>", b)
    if sr and old_sr and old_sr != sr:
        b = re.sub(r"<FieldsBlob>([0-9a-fA-F]+)</FieldsBlob>",
                   lambda m: "<FieldsBlob>" + _replace_in_zstd(
                       m.group(1), int(old_sr).to_bytes(4, "big"), int(sr).to_bytes(4, "big")) + "</FieldsBlob>", b)
    if dur_sec or ch:
        def _patch_fb(hexstr):
            h = hexstr
            if dur_sec:
                h = _patch_media_extents(h, mst, dur_sec)
            if ch and ch > 1:
                h = _patch_channel_vec(h, ch)
            return h
        b = re.sub(r"<FieldsBlob>([0-9a-fA-F]+)</FieldsBlob>",
                   lambda m: "<FieldsBlob>" + _patch_fb(m.group(1)) + "</FieldsBlob>", b)
    b = _set_field(b, "Name", _xml_escape(name))
    b = _set_field(b, "UniqueMediaPoolItemId", uniq_id)
    return b


def _volume_ba(db: float) -> str:
    import struct as _st
    fc = bytes([0x08, 0x7C, 0x4A, 0x0F, 0x08, 0x5F, 0x1A, 0x0B, 0x0A, 0x09, 0x11]) \
        + _st.pack("<d", db) + bytes([0x4A, 0x00] * 4)
    payload = bytes([0x80, 0x0A, len(fc)]) + fc
    return (_st.pack(">I", 2) + _st.pack(">I", len(payload)) + payload).hex()


def _volume_ba_kf(pontos: list) -> str:
    import struct as _st

    from tools.inspect_drp import _efb_slot_animado, _write_varint
    fc = bytes([0x08, 0x7C]) + _efb_slot_animado(0x5F, pontos) + bytes([0x4A, 0x00] * 4)
    payload = bytes([0x80, 0x0A]) + _write_varint(len(fc)) + fc
    return (_st.pack(">I", 2) + _st.pack(">I", len(payload)) + payload).hex()


def _make_audio_timeline_clip(shape: str, seg: dict, db_id: str, matched_path: str,
                              channel: int = 1, start_override: int | None = None,
                              dur_override: int | None = None, in_extra: int = 0) -> str:
    base_start = start_override if start_override is not None else _tc_to_frames(seg["timeline_tc_in"])
    start = base_start + _TIMELINE_OFFSET_FRAMES
    dur = int(dur_override) if dur_override is not None else int(seg["duration_frames"])
    sr = float(seg.get("speed_ratio") or 1.0) or 1.0
    in_pt = round(int(seg.get("source_start_frames") or 0) * sr) + int(in_extra or 0)
    reel = _reel(_nome(matched_path))
    mst, mdur, _sr, _ch, _dts = _audio_meta(matched_path)
    _nbV, _fpsV, _tcV = _media_meta(matched_path)
    if _tcV and _fpsV:
        mst = _tc_to_frames(_tcV, round(_fpsV)) / _fpsV
    elif _tcV:
        taxa = _taxa_exata(_TL_FPS)
        mst = _tc_to_frames(_tcV, round(taxa)) / taxa

    block = _uniquify(shape)
    block = _set_field(block, "Name", _xml_escape(_nome(matched_path)))
    block = _set_field(block, "Start", str(start))
    block = _set_field(block, "Duration", str(dur))
    if in_pt == 0:
        block = re.sub(r"<In>.*?</In>", "<In/>", block, count=1, flags=re.DOTALL)
    else:
        block = _set_field(block, "In", str(in_pt))
    block = _set_field(block, "MediaRef", db_id)
    block = _set_field(block, "MediaFilePath", _xml_escape(_abs(matched_path)))
    block = _set_field(block, "MediaReelNumber", _xml_escape(reel))
    block = _set_field(block, "MediaStartTime", repr(mst))
    block = _flags_de(block, seg)
    ch_idx = max(0, int(channel) - 1)
    block = _set_field(block, "MediaTrackIdx", str(ch_idx))
    if seg.get("volume_kf"):
        block = _set_field(block, "EffectFiltersBA", _volume_ba_kf(seg["volume_kf"]))
    elif seg.get("volume_db"):
        block = _set_field(block, "EffectFiltersBA", _volume_ba(float(seg["volume_db"])))
    ch_1based = channel
    ch_word = (0x4000 | ch_1based) if (_ch == 2) else (ch_1based * 0x4000 | 0x01)
    vatba = re.search(r"<VirtualAudioTrackBA>([0-9a-fA-F]+)</VirtualAudioTrackBA>", block)
    if vatba:
        old_ba = vatba.group(1)
        old_bytes = bytes.fromhex(old_ba)
        KEY_CHAN = "ChannelsBA".encode("utf-16-be")
        ci = old_bytes.find(KEY_CHAN)
        if ci >= 0:
            import struct as _s
            entry_off = ci + len(KEY_CHAN) + 17
            if entry_off + 4 <= len(old_bytes):
                new_bytes = bytearray(old_bytes)
                _s.pack_into(">I", new_bytes, entry_off, ch_word)
                block = block.replace(
                    f"<VirtualAudioTrackBA>{old_ba}</VirtualAudioTrackBA>",
                    f"<VirtualAudioTrackBA>{bytes(new_bytes).hex()}</VirtualAudioTrackBA>",
                    1,
                )
    if mdur and abs(sr - 1.0) > 1e-6 and (seg.get("prproj") or {}).get("audio"):
        block = _set_field(block, "MediaTimemapBA",
                           build_audio_speed_timemap(mdur, _taxa_exata(_TL_FPS), sr))
    elif mdur:
        block = _set_field(block, "MediaTimemapBA",
                           timemap_encode({"kind": "const", "tag": 0x02, "value": mdur}))
    fb = _xml_field(block, "FieldsBlob")
    if fb:
        block = _set_field(block, "FieldsBlob", _patch_fieldsblob_start(fb, start))
    return block


def _transition_overrides(segments: list, matched: dict):
    by_tr: dict[int, list] = {}
    for i, s in enumerate(segments or []):
        if not s:
            continue
        by_tr.setdefault(int(s.get("track") or 1), []).append((i, s))
    def _tf(s):
        return _tc_to_frames(s.get("timeline_tc_in") or "00:00:00:00")
    seg_by_idx = {i: s for i, s in enumerate(segments or [])}
    left_cut: dict[int, int] = {}
    right_cut: dict[int, int] = {}
    trans_after: dict[int, tuple] = {}
    fade_before: dict[int, tuple] = {}
    fade_after: dict[int, tuple] = {}
    for lst in by_tr.values():
        lst = sorted(lst, key=lambda x: _tf(x[1]))
        for pos, (i, s) in enumerate(lst):
            if not s.get("is_transition"):
                continue
            L = int(s.get("duration_frames") or 0)
            if L <= 0:
                continue
            C = int(s.get("cutpoint") if s.get("cutpoint") is not None else (L // 2))
            T_in = _tf(s)
            cut = T_in + C
            start_abs = T_in + _TIMELINE_OFFSET_FRAMES
            def _adj(seq):
                for j, sj in seq:
                    if sj.get("is_transition"):
                        continue
                    if sj.get("is_gap"):
                        return ("empty", None)
                    if j not in matched:
                        return ("unmatched", None)
                    return ("clip", j)
                return ("empty", None)
            ak, aj = _adj(list(reversed(lst[:pos])))
            bk, bj = _adj(lst[pos + 1:])
            if ak == "clip" and bk == "clip":
                right_cut[aj] = cut
                left_cut[bj] = cut
                align = 1 if C <= 0 else (3 if C >= L else 2)
                trans_after[aj] = (start_abs, L, align)
            elif ak == "clip" and bk == "empty":
                fade_after[aj] = (start_abs, L)
            elif ak == "empty" and bk == "clip":
                fade_before[bj] = (start_abs, L)

    dur_ovr, start_ovr, in_ovr = {}, {}, {}
    for i in set(left_cut) | set(right_cut):
        s = seg_by_idx.get(i) or {}
        orig_start = _tf(s)
        orig_end = orig_start + int(s.get("duration_frames") or 0)
        new_start = left_cut.get(i, orig_start)
        new_end = right_cut.get(i, orig_end)
        if i in left_cut:
            start_ovr[i] = new_start
            in_ovr[i] = new_start - orig_start
        dur_ovr[i] = max(1, new_end - new_start)
    return dur_ovr, start_ovr, in_ovr, trans_after, fade_before, fade_after


def build_drt(segments: list, match_results: list, output_drt: str,
              skeleton_path: str | None = None, timeline_name: str | None = None,
              ramp_mode: str = "adaptive", timeline_fps: float = 24.0,
              interlaced: bool = False, offline: dict | None = None,
              notas: list | None = None, sinais: list | None = None,
              marcar: bool = False, colorir: bool = False,
              codigo_de_cores: dict | None = None,
              timeline_size: tuple[int, int] | None = None,
              timeline_start_frames: int | None = None, andamento=None) -> dict:
    _semear_offline(offline)
    try:
        return _build_drt(segments, match_results, output_drt, skeleton_path, timeline_name,
                          ramp_mode, timeline_fps, interlaced, notas, sinais, marcar, colorir,
                          codigo_de_cores, timeline_size, timeline_start_frames, andamento)
    finally:
        for p in (offline or {}):
            _META_CACHE.pop(p, None)
            _META_CACHE_A.pop(p, None)
            _META_CACHE_OFFLINE.discard(_abs(p))


TRILHAS_NA_MESA_DO_ESQUELETO = 16
MESA_99 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fairlight_r19_mesa99.bin")
_CHAVE_MESA = "FLStudioModelBA".encode("utf-16-be")


def _mesa_de_99(blob: bytes) -> bytes | None:
    import struct as _st
    j = blob.find(_CHAVE_MESA)
    if j < 0:
        return None
    j += len(_CHAVE_MESA)
    if _st.unpack(">I", blob[j:j + 4])[0] != 12:
        return None
    n = _st.unpack(">I", blob[j + 5:j + 9])[0]
    with open(MESA_99, "rb") as f:
        mesa = f.read()
    return blob[:j + 5] + _st.pack(">I", len(mesa)) + mesa + blob[j + 9 + n:]


_CODIGO_TAXA_SETUP = {(24, True): 23, (24, False): 24, (25, False): 25, (30, True): 29,
                      (30, False): 30, (48, True): 47, (48, False): 48, (50, False): 50,
                      (60, True): 59, (60, False): 60}


def _patch_sequence_setup(raw: bytes, w: int | None, h: int | None,
                          fps: float | None) -> tuple[bytes, bool]:
    import struct
    k = "SequenceSetup".encode("utf-16-be")
    i = raw.find(k)
    if i < 0 or not (w and h):
        return raw, False
    i += len(k)
    if raw[i:i + 4] != b"\x00\x00\x00\x0c":
        return raw, False
    n = struct.unpack(">I", raw[i + 5:i + 9])[0]
    ini = i + 9
    ss = bytearray(raw[ini:ini + n])
    alvo = struct.pack(">QQ", 1920, 1080)
    primeiro, j = None, 0
    while (j := ss.find(alvo, j)) >= 0:
        primeiro = j if primeiro is None else primeiro
        ss[j:j + 16] = struct.pack(">QQ", int(w), int(h))
        j += 16
    conhecida = False
    if primeiro is not None and fps:
        cod = _CODIGO_TAXA_SETUP.get((max(1, round(fps)), abs(fps - round(fps)) > 1e-3))
        if cod is not None:
            p = primeiro + 16 + 20
            ss[p:p + 8] = struct.pack(">Q", cod)
            conhecida = True
    return raw[:ini] + bytes(ss) + raw[ini + n:], conhecida


def _taxa_exata(fps: float) -> float:
    f = float(fps or 0)
    nominal = round(f)
    if nominal and abs(f - nominal) > 1e-3 and abs(f - nominal * 1000 / 1001) < 5e-3:
        return nominal * 1000 / 1001
    return f


def _semear_offline(offline: dict | None) -> None:
    for p, m in (offline or {}).items():
        _META_CACHE_OFFLINE.add(_abs(p))
        fps = _taxa_exata(m.get("fps") or 0) or None
        nb = int(m.get("nb") or 0) or None
        _META_CACHE[p] = (nb, fps, m.get("tc"))
        sr = int(m.get("audio_sr") or 48000)
        dur = (nb / fps) if (nb and fps) else None
        inicio = (_tc_to_frames(m["tc"], round(fps)) / fps) if (m.get("tc") and fps) else 0.0
        _META_CACHE_A[p] = (inicio, dur, sr, int(m.get("audio_canais") or 1) or 1,
                            int(round(dur * sr)) if dur else None)


_MARCADORES_DA_TIMELINE = re.compile(
    r"\s*<Element>\s*<Sm2SequenceLockableBlob\b.*?</Sm2SequenceLockableBlob>\s*</Element>", re.DOTALL)


def sem_marcadores_da_timeline(project_xml: str) -> str:
    return _MARCADORES_DA_TIMELINE.sub("", project_xml)


SONDAGENS_SIMULTANEAS = 4


def _sondar_em_paralelo(caminhos: list[str], aviso=None) -> list[str]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def sondar(p: str) -> str:
        _media_meta(p)
        _formato_meta(p)
        _raster_arriraw(p)
        _audio_meta(p)
        return p

    if not caminhos:
        return []
    _SONDA_LENTA.difference_update(caminhos)
    ex = ThreadPoolExecutor(max_workers=min(SONDAGENS_SIMULTANEAS, len(caminhos)))
    lentos: list[str] = []
    try:
        for fut in as_completed([ex.submit(sondar, p) for p in caminhos]):
            p = fut.result()
            if p in _SONDA_LENTA:
                lentos.append(p)
                continue
            if aviso is not None:
                aviso(p)
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
    for p in lentos:
        _SONDA_LENTA.discard(p)
        sondar(p)
        if aviso is not None:
            aviso(p)
    return [p for p in lentos if p in _SONDA_LENTA]


def _build_drt(segments: list, match_results: list, output_drt: str,
               skeleton_path: str | None, timeline_name: str | None,
               ramp_mode: str, timeline_fps: float, interlaced: bool,
               notas: list | None = None, sinais: list | None = None,
               marcar: bool = False, colorir: bool = False,
               codigo_de_cores: dict | None = None,
               timeline_size: tuple[int, int] | None = None,
               timeline_start_frames: int | None = None, andamento=None) -> dict:
    global _RAMP_MODE, _NOMINAL_FPS, _TIMELINE_OFFSET_FRAMES, _TL_FPS
    _RAMP_MODE = ramp_mode if ramp_mode in ("adaptive", "dense") else "adaptive"
    _TL_FPS = float(timeline_fps or 24.0)
    _NOMINAL_FPS = max(1, round(float(timeline_fps or 24.0)))
    _TIMELINE_OFFSET_FRAMES = 3600 * _NOMINAL_FPS
    if timeline_start_frames is not None and timeline_size:
        _TIMELINE_OFFSET_FRAMES = max(0, int(timeline_start_frames))
    skeleton_path = skeleton_path or _skeleton_path()
    with zipfile.ZipFile(skeleton_path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    seq_name = next(n for n in files if "SeqContainer" in n)
    pool_name = next((n for n in files
                      if "MpFolder.xml" in n and b"Sm2MpVideoClip" in files[n]),
                     next(n for n in files if "MpFolder.xml" in n))
    seq = files[seq_name].decode("utf-8", "replace")
    pool = files[pool_name].decode("utf-8", "replace")

    tl_v_shape = re.search(r"<Sm2TiVideoClip\b.*?</Sm2TiVideoClip>", seq, re.DOTALL).group(0)
    vtv = re.search(r"<VideoTrackVec>.*?</VideoTrackVec>", seq, re.DOTALL).group(0)
    track_shape = re.search(r"<Sm2TiTrack\b.*?</Sm2TiTrack>", vtv, re.DOTALL).group(0)
    track_shape = re.sub(r"<Items>.*?</Items>", "<Items></Items>", track_shape,
                         count=1, flags=re.DOTALL)
    pool_v_shape = re.search(r"<Sm2MpVideoClip\b.*?</Sm2MpVideoClip>", pool, re.DOTALL).group(0)
    pool_a_m = re.search(r"<Sm2MpAudioClip\b.*?</Sm2MpAudioClip>", pool, re.DOTALL)
    tl_a_m = re.search(r"<Sm2TiAudioClip\b.*?</Sm2TiAudioClip>", seq, re.DOTALL)
    atv = re.search(r"<AudioTrackVec>.*?</AudioTrackVec>", seq, re.DOTALL).group(0)
    a_track_shape = re.search(r"<Sm2TiTrack\b.*?</Sm2TiTrack>", atv, re.DOTALL).group(0)
    a_track_shape = re.sub(r"<Items>.*?</Items>", "<Items></Items>", a_track_shape,
                           count=1, flags=re.DOTALL)

    matched: dict[int, str] = {}
    offc_by_index: dict[int, int] = {}
    for r in (match_results or []):
        if r and r.get("matched_path") and not r.get("rejected"):
            matched[r.get("segment_index")] = r["matched_path"]
            offc_by_index[r.get("segment_index")] = int(r.get("source_offset_correction") or 0)

    vsegs = []
    for i, s in enumerate(segments or []):
        if not s or s.get("is_audio") or s.get("is_gap") or s.get("is_transition"):
            continue
        nm = (s.get("clip_name") or "").strip()
        if not nm or nm.startswith("("):
            continue
        if s.get("disabled"):
            continue
        if matched.get(i):
            vsegs.append((i, s))

    asegs = []
    for i, s in enumerate(segments or []):
        if not s or not s.get("is_audio") or s.get("is_gap") or s.get("is_transition"):
            continue
        nm = (s.get("clip_name") or "").strip()
        if not nm or nm.startswith("("):
            continue
        if matched.get(i):
            asegs.append((i, s))

    n_pool = len({matched[i] for i, _s in vsegs} | {matched[i] for i, _s in asegs})
    passos = {"feitos": 0, "total": n_pool + len(vsegs) + len(asegs)}

    def passo(atual: str = "", detalhe: str = "") -> None:
        if andamento is not None:
            andamento(passos["feitos"], passos["total"], atual, detalhe)
        passos["feitos"] += 1

    sem_resposta = _sondar_em_paralelo(list(dict.fromkeys([matched[i] for i, _s in vsegs] + [matched[i] for i, _s in asegs])),
                        lambda p: passo(p, "media pool: o formato, o timecode, a duração e o som do arquivo"))

    path_to_db: dict[str, str] = {}
    pool_elems = []
    for i, _s in vsegs:
        p = matched[i]
        if p in path_to_db:
            continue
        db = _new_id()
        path_to_db[p] = db
        forma = pool_v_shape if (_e_mxf(p) and not _e_arriraw(p)) \
            else _formas_nativas()[_familia(p)]
        pool_elems.append("<Element>" + _make_pool_clip(
            forma, _nome(p), db, _new_id(), p) + "</Element>")

    apath_to_db: dict[str, str] = {}
    apool_elems = []
    if pool_a_m:
        for i, _s in asegs:
            p = matched[i]
            if p in path_to_db or p in apath_to_db:
                continue
            db = _new_id()
            if _arquivo_de_video(p):
                path_to_db[p] = db
                forma = pool_v_shape if (_e_mxf(p) and not _e_arriraw(p)) \
                    else _formas_nativas()[_familia(p)]
                pool_elems.append("<Element>" + _make_pool_clip(
                    forma, _nome(p), db, _new_id(), p) + "</Element>")
                continue
            apath_to_db[p] = db
            apool_elems.append("<Element>" + _make_audio_pool_clip(
                pool_a_m.group(0), _nome(p), db, _new_id(), p) + "</Element>")

    antigo = re.search(r"<MediaVec>.*?</MediaVec>", pool, re.DOTALL)
    timelines = re.findall(r"<Element>\s*<Sm2MpTimelineClip\b.*?</Sm2MpTimelineClip>\s*</Element>",
                           antigo.group(0) if antigo else "", re.DOTALL)
    new_vec = ("<MediaVec>" + "".join(pool_elems) + "".join(apool_elems) + "".join(timelines)
               + "</MediaVec>")
    pool_new = re.sub(r"<MediaVec>.*?</MediaVec>", lambda _m: new_vec, pool, count=1,
                      flags=re.DOTALL)

    com_titulo = dict(matched)
    com_titulo.update({i: "" for i, s in enumerate(segments or []) if _titulo_do_segmento(s)})
    dur_ovr, start_ovr, in_ovr, trans_after, fade_before, fade_after = \
        _transition_overrides(segments, com_titulo)

    by_track: dict[int, list[str]] = {}
    generated = []
    clipe_do_segmento: dict[int, str] = {}
    for i, s in vsegs:
        p = matched[i]
        passo(p, f"timeline: o plano na V{int(s.get('track') or 1)}, em {s.get('timeline_tc_in') or ''}"
                 " (corte, velocidade, efeitos)")
        clip = _make_timeline_clip(tl_v_shape, s, path_to_db[p], p,
                                   offset_corr=offc_by_index.get(i, 0),
                                   start_override=start_ovr.get(i),
                                   dur_override=dur_ovr.get(i), in_extra=in_ovr.get(i, 0))
        clipe_do_segmento[i] = re.search(r'DbId="([^"]+)"', clip).group(1)
        t = int(s.get("track") or 1)
        lane = by_track.setdefault(t, [])
        if i in fade_before:
            st, L = fade_before[i]
            lane.append(_make_transition(st, L, align=1, kind="video"))
        lane.append(clip)
        if i in fade_after:
            st, L = fade_after[i]
            lane.append(_make_transition(st, L, align=3, kind="video"))
        if i in trans_after:
            st, L, al = trans_after[i]
            lane.append(_make_transition(st, L, al, kind="video"))
        generated.append({
            "name": s["clip_name"],
            "track": int(s.get("track") or 1),
            "start": _tc_to_frames(s["timeline_tc_in"]) + _TIMELINE_OFFSET_FRAMES,
            "duration": int(s["duration_frames"]),
            "path": p,
        })

    titulos, avisos_titulos = _titulos_na_timeline(
        segments, by_track, (dur_ovr, start_ovr, trans_after, fade_before, fade_after))
    trilhas_de_notas = _notas_na_timeline(notas or [], by_track)
    n_notas = len(notas or [])
    trilhas_desligadas = {int(s.get("track") or 1) for s in segments or []
                          if s and s.get("trilha_desligada") and not s.get("is_audio")}

    for t in range(1, max((k for k in by_track if k >= 1), default=0)):
        by_track.setdefault(t, [])

    track_elems = []
    for t in sorted(by_track):
        trilha = _make_video_track(track_shape, by_track[t])
        if t in trilhas_de_notas or t in trilhas_desligadas:
            trilha = re.sub(r"<Flags>\d+</Flags>", "<Flags>2</Flags>", trilha, count=1)
        track_elems.append("<Element>" + trilha + "</Element>")
    new_vtv = "<VideoTrackVec>" + "".join(track_elems) + "</VideoTrackVec>"
    seq_new = seq.replace(vtv, new_vtv, 1)

    apath_tracks: dict[str, list[int]] = {}
    for i, s in asegs:
        p = matched[i]
        tr = int(s.get("track") or 1000)
        apath_tracks.setdefault(p, [])
        if tr not in apath_tracks[p]:
            apath_tracks[p].append(tr)
    for p in apath_tracks:
        apath_tracks[p].sort()
    apath_nch: dict[str, int] = {p: (_audio_meta(p)[3] or 1) for p in apath_tracks}

    def _embedded_channel(path: str, track: int) -> int:
        tracks = apath_tracks.get(path) or [track]
        nch = apath_nch.get(path) or 1
        rank = tracks.index(track) if track in tracks else 0
        return (rank % nch) + 1

    a_by_track: dict[int, list[str]] = {}
    if asegs and tl_a_m:
        for i, s in asegs:
            p = matched[i]
            passo(p, f"timeline: o som em {s.get('timeline_tc_in') or ''} (canal, volume, fades)")
            tr = int(s.get("track") or 1000)
            a_db = path_to_db.get(p) or apath_to_db.get(p)
            canal = int(s.get("canal_arquivo") or 0) or _embedded_channel(p, tr)
            clip = _make_audio_timeline_clip(tl_a_m.group(0), s, a_db, p,
                                             channel=canal,
                                             start_override=start_ovr.get(i),
                                             dur_override=dur_ovr.get(i), in_extra=in_ovr.get(i, 0))
            lane = a_by_track.setdefault(tr, [])
            if i in fade_before:
                st, L = fade_before[i]
                lane.append(_make_transition(st, L, align=1, kind="audio"))
            lane.append(clip)
            if i in fade_after:
                st, L = fade_after[i]
                lane.append(_make_transition(st, L, align=3, kind="audio"))
            if i in trans_after:
                st, L, al = trans_after[i]
                lane.append(_make_transition(st, L, al, kind="audio"))
        solo = {int(s.get("track") or 1000) for _i, s in asegs if s.get("solo")}

        def _trilha_a(t: int) -> str:
            tr = _make_video_track(a_track_shape, a_by_track[t])
            if t in solo:
                tr = re.sub(r"<Flags>\d+</Flags>", "<Flags>32</Flags>", tr, count=1)
            return tr
        a_track_elems = ["<Element>" + _trilha_a(t) + "</Element>" for t in sorted(a_by_track)]
        new_atv = "<AudioTrackVec>" + "".join(a_track_elems) + "</AudioTrackVec>"
        seq_new = seq_new.replace(atv, new_atv, 1)
    else:
        seq_new = seq_new.replace(atv, _empty_all_items(atv), 1)

    out_files = dict(files)
    blocos, n_marcadores, n_cores = _sinais(sinais or [], clipe_do_segmento, pool_new, marcar, colorir,
                                              codigo_de_cores)
    for n in out_files:
        if n.endswith("project.xml"):
            px = sem_marcadores_da_timeline(out_files[n].decode("utf-8"))
            out_files[n] = _sn.no_project_xml(px, blocos).encode("utf-8")
    out_files[seq_name] = seq_new.encode("utf-8")
    out_files[pool_name] = pool_new.encode("utf-8")

    fmt = _tl_format(timeline_fps, interlaced)
    tl_rate_blob = fmt[0] if fmt else framerate_encode(_taxa_exata(timeline_fps or 24.0))
    for n in list(out_files):
        if not n.endswith("Master/MpFolder.xml"):
            continue
        txt = out_files[n].decode("utf-8", "replace")

        def _ajustar(m) -> str:
            bloco = m.group(0)
            if timeline_name:
                bloco = re.sub(r"<Name>.*?</Name>",
                               lambda _n: "<Name>" + _xml_escape(timeline_name) + "</Name>",
                               bloco, count=1, flags=re.DOTALL)
            if _xml_field(bloco, "FrameRate") != tl_rate_blob:
                bloco = _set_field(bloco, "FrameRate", tl_rate_blob)
                if fmt:
                    bloco = _set_field(bloco, "Body", fmt[1])
            if timeline_size:
                import struct
                taxa = _taxa_exata(timeline_fps or 24.0)
                fim = max((g["start"] + g["duration"] for g in generated),
                          default=_TIMELINE_OFFSET_FRAMES) - _TIMELINE_OFFSET_FRAMES
                bloco = _set_field(bloco, "MediaExtents", struct.pack(
                    "<dd", _TIMELINE_OFFSET_FRAMES / taxa, max(fim, 0) / taxa).hex())
            fbs_ = re.findall(r"<FieldsBlob>([0-9a-fA-F]+)</FieldsBlob>", bloco)
            if len(fbs_) > 1 and len(a_by_track) > TRILHAS_NA_MESA_DO_ESQUELETO:
                novo = _mesa_de_99(bytes.fromhex(fbs_[1]))
                if novo is not None:
                    bloco = bloco.replace(fbs_[1], novo.hex(), 1)
                elif len(a_by_track) > 16:
                    avisos_titulos.append("mais de 16 trilhas de áudio: confira o Bus Assign no Fairlight")
            if len(a_by_track) > 99:
                avisos_titulos.append(f"{len(a_by_track)} trilhas de áudio: a partir da 100ª, sem "
                                      "saída no Bus 1 (Bus Assign › Assign All)")
            if timeline_size:
                fbs = re.findall(r"<FieldsBlob>([0-9a-fA-F]+)</FieldsBlob>", bloco)
                if len(fbs) > 1:
                    novo, ok = _patch_sequence_setup(bytes.fromhex(fbs[1]), *timeline_size,
                                                     timeline_fps)
                    bloco = bloco.replace(fbs[1], novo.hex(), 1)
                    if not ok:
                        avisos_titulos.append(
                            f"a taxa da timeline ({timeline_fps:g} fps) não é das medidas: o Resolve "
                            "pode abrir a timeline a 23,976 — confira em Timeline Settings")
            bloco = re.sub(r"\s*<IsCustom>.*?</IsCustom>", "", bloco, flags=re.DOTALL)
            bloco = re.sub(r"\s*<ImportExportMetadataBA>.*?</ImportExportMetadataBA>", "",
                           bloco, flags=re.DOTALL)
            return bloco

        txt = re.sub(r"<Sm2MpTimelineClip\b.*?</Sm2MpTimelineClip>", _ajustar, txt, count=1,
                     flags=re.DOTALL)
        out_files[n] = txt.encode("utf-8")

    if andamento is not None:
        andamento(passos["total"], passos["total"], output_drt, "gravando o arquivo da timeline (DRT)")
    Path(output_drt).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_drt, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in out_files.items():
            z.writestr(n, data)

    return {
        "output": output_drt,
        "clips_generated": len(generated),
        "pool_clips": len(path_to_db) + len(apath_to_db),
        "video_tracks": len(by_track),
        "audio_clips": sum(len(v) for v in a_by_track.values()),
        "audio_tracks": len(a_by_track),
        "timeline_name": timeline_name,
        "clips": generated,
        "titulos": titulos,
        "notas": n_notas,
        "marcadores": n_marcadores,
        "segmentos_coloridos": n_cores,
        "avisos": [f"o disco não respondeu a tempo ao ler {os.path.basename(p)}: confira o clipe "
                   "(timecode e duração podem ter saído errados)" for p in sem_resposta] + avisos_titulos,
    }


_NOTA_SEP, _NOTA_MAX = "\n", 1000


def _sinais(sinais: list, clipe_do_segmento: dict, pool_xml: str, marcar: bool,
            colorir: bool, codigo: dict | None = None) -> tuple[list[str], int, int]:
    codigo = _sn.validar_codigo(codigo)
    cores = codigo["cores"]
    vistos: dict[int, dict] = {}
    for n in sinais:
        seg = n.get("segmento")
        if n.get("cor") in cores and seg is not None and seg not in vistos:
            vistos[seg] = n
    blocos: list[str] = []
    n_cores = 0
    if colorir:
        for seg, n in vistos.items():
            if seg in clipe_do_segmento:
                blocos.append(_sn.cor_local(clipe_do_segmento[seg], cores[n["cor"]]["clipe"]))
                n_cores += 1
    lista = []
    if marcar and vistos:
        ordem = list(cores)
        por_quadro: dict[int, list] = {}
        for n in vistos.values():
            por_quadro.setdefault(int(n["inicio"]), []).append(n)
        for quadro in sorted(por_quadro):
            grupo = sorted(por_quadro[quadro], key=lambda x: ordem.index(x["cor"]))
            linhas = [x for n in grupo for x in ("".join(t for t, _d in linha) for linha in n["linhas"])]
            desc = ["".join(t for t, _d in linha) for linha in grupo[0]["linhas"][int(grupo[0].get("cabeca") or 0):]]
            tipo = grupo[0].get("tipo") or (desc[0] if desc else linhas[0] if linhas else _sn.CATEGORIAS.get(
                grupo[0]["cor"], {}).get("rotulo", ""))
            if len(grupo) > 1:
                tipo += f" (+{len(grupo) - 1})"
            lista.append({"quadro": quadro, "cor": cores[grupo[0]["cor"]]["marcador"],
                          "nome": tipo[:120], "nota": _NOTA_SEP.join(linhas)[:_NOTA_MAX],
                          "duracao": 1, "palavras": [codigo["palavra"]]})
        dono = re.search(r'<Sm2Sequence DbId="([^"]+)"', pool_xml)
        if dono:
            blocos.append(_sn.marcadores(dono.group(1), lista))
    return blocos, len(lista), n_cores


_NOTA_BRANCO, _NOTA_VERMELHO = (1.0, 1.0, 1.0), (1.0, 0.33, 0.33)
_NOTA_X, _NOTA_TOPO = 80 / 1920, 0.967
_NOTA_LINHA = 0.05
_ORDEM_DOS_GRUPOS = ("efeito", "rampa")


def _notas_na_timeline(notas: list, by_track: dict) -> set:
    if not notas:
        return set()
    from titulos import resolve as texto

    base = max(by_track or {0: None}) + 1
    faixas: list[list] = []
    ordem = {g: i for i, g in enumerate(_ORDEM_DOS_GRUPOS)}
    for n in sorted(notas, key=lambda x: (ordem.get(x.get("grupo"), 9), x["inicio"])):
        grupo = n.get("grupo") or "efeito"
        k = next((i for i, f in enumerate(faixas) if f[0] == grupo and f[1] <= n["inicio"]), None)
        if k is None:
            faixas.append([grupo, 0, 0, []])
            k = len(faixas) - 1
        faixas[k][1] = int(n["inicio"]) + max(1, int(n["dur"]))
        faixas[k][2] = max(faixas[k][2], len(n["linhas"]))
        faixas[k][3].append(n)
    forma = texto.forma()
    topo = _NOTA_TOPO
    for k, (_g, _fim, linhas, lista) in enumerate(faixas):
        lane = by_track.setdefault(base + k, [])
        for n in lista:
            trechos = [[(t, _NOTA_VERMELHO if destaque else _NOTA_BRANCO) for t, destaque in linha]
                       for linha in n["linhas"]]
            titulo = {"estilo_lido": True, "fundo": None, "caixas": [{
                "texto": n["nome"], "trechos": trechos, "fonte": {"familia": "Open Sans", "estilo": ""},
                "corpo": 0.035, "cor": [1, 1, 1], "alinhamento": "esquerda",
                "ancora": "topo_esquerda", "posicao_resolve": (_NOTA_X, topo)}]}
            efb, _avisos = texto.efeitos_do_texto(titulo)
            bloco = _uniquify(forma)
            bloco = _set_field(bloco, "Name", _xml_escape(n["nome"][:250]))
            bloco = _set_field(bloco, "Start", str(int(n["inicio"]) + _TIMELINE_OFFSET_FRAMES))
            bloco = _set_field(bloco, "Duration", str(max(1, int(n["dur"]))))
            bloco = _set_field(bloco, "EffectFiltersBA", efb.hex())
            lane.append(bloco)
        topo -= linhas * _NOTA_LINHA
    return {base + k for k in range(len(faixas))}


def _titulo_do_segmento(s: dict | None) -> dict | None:
    if not s or s.get("is_audio") or s.get("disabled") or not s.get("is_effect"):
        return None
    titulo = next((e.get("params", {}).get("titulo") for e in (s.get("effects") or [])
                   if e.get("category") == "title" and (e.get("params") or {}).get("titulo")), None)
    if not titulo or not any((c.get("texto") or "").strip() for c in titulo.get("caixas") or []):
        return None
    return titulo


def _titulos_na_timeline(segments: list, by_track: dict, transicoes: tuple) -> tuple[int, list[str]]:
    from titulos import resolve as texto

    dur_ovr, start_ovr, trans_after, fade_before, fade_after = transicoes
    n, avisos = 0, []
    for i, s in enumerate(segments or []):
        titulo = _titulo_do_segmento(s)
        if not titulo:
            ef = next((e for e in (s or {}).get("effects") or [] if e.get("category") == "title"), None)
            if ef and not s.get("is_audio") and not s.get("disabled"):
                avisos.append(f"título do Avid ({ef.get('label') or ef.get('name') or 'Titler'}) em "
                              f"{s.get('timeline_tc_in')}: sem texto legível — não entrou; conferir")
            continue
        efb, avs = texto.efeitos_do_texto(titulo)
        start = (start_ovr.get(i) if i in start_ovr else _tc_to_frames(s["timeline_tc_in"])) \
            + _TIMELINE_OFFSET_FRAMES
        bloco = _uniquify(texto.forma())
        bloco = _set_field(bloco, "Start", str(start))
        bloco = _set_field(bloco, "Duration", str(int(dur_ovr.get(i, s["duration_frames"]))))
        bloco = _set_field(bloco, "EffectFiltersBA", efb.hex())
        grupo = []
        if i in fade_before:
            grupo.append(_make_transition(*fade_before[i], align=1, kind="video"))
        grupo.append(bloco)
        if i in fade_after:
            grupo.append(_make_transition(*fade_after[i], align=3, kind="video"))
        if i in trans_after:
            st, L, al = trans_after[i]
            grupo.append(_make_transition(st, L, al, kind="video"))
        lane = by_track.setdefault(int(s.get("track") or 1), [])
        depois = next((k for k, it in enumerate(lane)
                       if int(_xml_field(it, "Start") or 0) > start), len(lane))
        lane[depois:depois] = grupo
        rotulo = (titulo.get("caixas") or [{}])[0].get("texto", "").replace("\n", " ")[:40]
        avisos += [f"título “{rotulo}”: {a}" for a in avs]
        n += 1
    return n, avisos


if __name__ == "__main__":
    import json
    from aaf.parser import parse_aaf
    if len(sys.argv) < 3:
        print("uso: python -m drp.writer <aaf> <saida.drt> [timeline_name]\n"
              "(teste: casa cada segmento consigo mesmo via clip_name como matched_path)")
        raise SystemExit(1)
    parsed = parse_aaf(sys.argv[1])
    segs = parsed["segments"]
    mr = [{"segment_index": i, "matched_path": f"/tmp/{s.get('clip_name')}.mp4"}
          for i, s in enumerate(segs) if s.get("clip_name")]
    res = build_drt(segs, mr, sys.argv[2],
                    timeline_name=sys.argv[3] if len(sys.argv) > 3 else None)
    print(json.dumps({k: v for k, v in res.items() if k != "clips"}, indent=2))
