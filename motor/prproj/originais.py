from __future__ import annotations

import os
import subprocess

from media import sonda

EXT_VIDEO = {".mxf", ".mov", ".mp4", ".r3d", ".braw", ".ari", ".arx", ".crm", ".mts", ".avi"}

_SONDA: dict[str, dict] = {}


_CHAVE_RIP = bytes.fromhex("060e2b34020501010d01020101110100")


def mxf_completo(caminho: str) -> bool:
    import struct
    try:
        with open(caminho, "rb") as f:
            f.seek(0, 2)
            tam = f.tell()
            if tam < 64:
                return False
            f.seek(tam - 4)
            n = struct.unpack(">I", f.read(4))[0]
            if not 20 <= n <= min(tam, 1 << 20):
                return False
            f.seek(tam - n)
            return f.read(16) == _CHAVE_RIP
    except OSError:
        return False


def sondar(caminho: str) -> dict:
    if caminho in _SONDA:
        return _SONDA[caminho]
    r = {"ok": False}
    try:
        if os.path.getsize(caminho) > 0:
            j = sonda.ler(caminho)
            st = sonda.primeiro(j, "video")
            fmt = j.get("format") or {}
            num, _, den = (st.get("r_frame_rate") or "0/1").partition("/")
            fps = int(num) / int(den or 1) if num.isdigit() and int(den or 1) else None
            dur = st.get("duration") or fmt.get("duration")
            r = {"ok": bool(fmt) and dur not in (None, "N/A"),
                 "w": st.get("width") or 0, "h": st.get("height") or 0,
                 "codec": st.get("codec_name"), "fps": fps,
                 "dur_s": float(dur) if dur not in (None, "N/A") else None,
                 "tc": (st.get("tags") or {}).get("timecode") or (fmt.get("tags") or {}).get("timecode")}
    except subprocess.TimeoutExpired:
        return {"ok": False, "lento": True}
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    _SONDA[caminho] = r
    return r


def indexar(raizes: list[str]) -> dict[str, list[str]]:
    idx: dict[str, list[str]] = {}
    for raiz in raizes:
        for pasta, subs, arqs in os.walk(raiz):
            subs[:] = [s for s in subs if not s.startswith(".")
                       and "Auto-Save" not in s and "Previews" not in s]
            for a in arqs:
                if a.startswith("._"):
                    continue
                base, ext = os.path.splitext(a)
                if ext.lower() in EXT_VIDEO:
                    idx.setdefault(base.lower(), []).append(os.path.join(pasta, a))
    return idx


def _impressao(caminho: str) -> tuple | None:
    import hashlib
    bloco = 1 << 20
    try:
        tam = os.path.getsize(caminho)
        h = hashlib.sha1()
        with open(caminho, "rb") as f:
            for pos in (0, tam // 2, max(0, tam - bloco)):
                f.seek(pos)
                h.update(f.read(bloco))
        return tam, h.hexdigest()
    except OSError:
        return None


def _tc_frames(tc: str | None, fps: float | None) -> int | None:
    if not tc or not fps:
        return None
    try:
        h, m, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
    except ValueError:
        return None
    n = round(fps)
    return ((h * 60 + m) * 60 + s) * n + f


EXT_PARADA = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".psd", ".exr", ".dpx", ".bmp", ".tga"}


def casar_um(proxy: str, indice: dict[str, list[str]]) -> dict:
    if os.path.splitext(proxy)[1].lower() in EXT_PARADA:
        existe = os.path.isfile(proxy)
        return {"proxy": proxy, "estado": "proprio" if existe else "sem_original",
                "original": proxy if existe else None, "candidatos": [], "copias": [],
                "recusados": [], "proxy_existe": existe}
    nome = os.path.splitext(os.path.basename(proxy))[0].lower()
    cands = [c for c in indice.get(nome, []) if os.path.normcase(c) != os.path.normcase(proxy)]
    px = sondar(proxy) if os.path.exists(proxy) else {"ok": False}
    lento = bool(px.get("lento"))
    bons, recusados = [], []
    for c in cands:
        s = sondar(c)
        if s.get("lento"):
            lento = True
            recusados.append((c, "o disco não respondeu a tempo (lento ou com defeito?) — confira"))
            continue
        if s.get("ok") and c.lower().endswith(".mxf") and not mxf_completo(c):
            recusados.append((c, "incompleto: o MXF não termina (arquivo truncado)"))
            continue
        if not s.get("ok"):
            recusados.append((c, "incompleto: vazio ou ilegível"))
            continue
        if px.get("ok"):
            if s.get("w") == px.get("w") and s.get("h") == px.get("h") \
                    and s.get("codec") == px.get("codec"):
                recusados.append((c, "cópia do proxy (mesmo raster e codec)"))
                continue
            fps = px.get("fps") or s.get("fps")
            if px.get("dur_s") and s.get("dur_s") and fps \
                    and abs(px["dur_s"] - s["dur_s"]) * fps > 1.01:
                recusados.append((c, f"duração diferente ({s['dur_s']:.2f}s × {px['dur_s']:.2f}s)"))
                continue
            a, b = _tc_frames(px.get("tc"), fps), _tc_frames(s.get("tc"), fps)
            if a and b and a != b:
                recusados.append((c, f"timecode diferente ({s.get('tc')} × {px.get('tc')})"))
                continue
        bons.append(c)
    copias = []
    if len(bons) > 1:
        imp = _impressao(bons[0])
        if imp is not None and all(_impressao(c) == imp for c in bons[1:]):
            copias, bons = bons[1:], bons[:1]
    if len(bons) == 1:
        estado = "original"
    elif len(bons) > 1:
        estado = "ambiguo"
    elif lento:
        estado = "lento"
    elif any("incompleto" in m for _c, m in recusados):
        estado = "incompleto"
    else:
        estado = "sem_original"
    return {"proxy": proxy, "estado": estado, "original": bons[0] if len(bons) == 1 else None,
            "candidatos": bons, "copias": copias, "recusados": recusados, "proxy_existe": bool(px.get("ok")),
            **({"lento": True} if lento else {})}


def casar(segmentos: list[dict], raizes: list[str], progresso=None) -> dict[str, dict]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    indice = indexar(raizes)
    proxies = sorted({s["media_ref"]["local_path"] for s in segmentos
                      if s.get("media_ref") and not s.get("is_audio")})
    feitos: dict[str, dict] = {}
    ex = ThreadPoolExecutor(max_workers=max(1, min(SONDAGENS_SIMULTANEAS, len(proxies))))
    try:
        futuros = {ex.submit(casar_um, p, indice): p for p in proxies}
        if progresso and proxies:
            progresso(0, len(proxies), proxies[0])
        for fut in as_completed(futuros):
            feitos[futuros[fut]] = fut.result()
            if progresso:
                progresso(len(feitos), len(proxies), futuros[fut])
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
    for p in [p for p in proxies if feitos[p].get("lento")]:
        feitos[p] = casar_um(p, indice)
    if progresso:
        progresso(len(proxies), len(proxies), "")
    return {p: feitos[p] for p in proxies}


SONDAGENS_SIMULTANEAS = 4
