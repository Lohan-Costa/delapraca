from __future__ import annotations

import glob
import os
import re
import struct
import sys

_EXTENSOES = (".ttf", ".otf", ".ttc", ".otc", ".dfont")


def pasta_adobe_fonts() -> str:
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA") or "", "Adobe", "CoreSync", "plugins", "livetype", "r")
    return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Adobe", "CoreSync",
                        "plugins", "livetype", ".r")


def pastas_de_fontes() -> list[str]:
    if sys.platform == "win32":
        win = os.environ.get("WINDIR") or r"C:\Windows"
        local = os.environ.get("LOCALAPPDATA") or ""
        return [os.path.join(win, "Fonts")] + ([os.path.join(local, "Microsoft", "Windows", "Fonts")]
                                                if local else []) + [pasta_adobe_fonts()]
    casa = os.path.expanduser("~")
    pastas = ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
              os.path.join(casa, "Library", "Fonts")]
    pastas += glob.glob("/System/Library/AssetsV2/com_apple_MobileAsset_Font*/*/AssetData")
    return pastas + [pasta_adobe_fonts()]


def _faces_do_arquivo(caminho: str):
    with open(caminho, "rb") as fh:
        def ler(pos: int, n: int) -> bytes:
            fh.seek(pos)
            return fh.read(n)
        cab = ler(0, 12)
        if len(cab) < 12:
            return
        inicios = [0]
        if cab[:4] == b"ttcf":
            n = struct.unpack(">I", cab[8:12])[0]
            if n > 256:
                return
            inicios = list(struct.unpack(">%dI" % n, ler(12, 4 * n)))
        for ini in inicios:
            nt = struct.unpack(">H", ler(ini + 4, 2))[0]
            dire = ler(ini + 12, 16 * nt)
            tabs = {}
            for i in range(nt):
                tag, _soma, pos, tam = struct.unpack(">4sIII", dire[16 * i:16 * i + 16])
                tabs[tag] = (pos, tam)
            if b"name" not in tabs or b"head" not in tabs or b"hhea" not in tabs:
                continue
            pos, tam = tabs[b"name"]
            nome = ler(pos, tam)
            cnt, area = struct.unpack(">HH", nome[2:6])
            nomes: dict[int, str] = {}
            for i in range(cnt):
                pid, _eid, lid, nid, n, off = struct.unpack(">6H", nome[6 + 12 * i:18 + 12 * i])
                bruto = nome[area + off:area + off + n]
                if pid == 3 and lid == 0x409:
                    nomes[nid] = bruto.decode("utf-16-be", "replace")
                elif pid == 1 and lid == 0 and nid not in nomes:
                    nomes[nid] = bruto.decode("mac_roman", "replace")
            upm = struct.unpack(">H", ler(tabs[b"head"][0] + 18, 2))[0] or 1000
            asc, desc, gap = struct.unpack(">hhh", ler(tabs[b"hhea"][0] + 4, 6))
            os2 = ler(tabs[b"OS/2"][0], 78) if b"OS/2" in tabs else b""
            if sys.platform == "win32" and len(os2) >= 78:
                yield nomes, *_metricas_directwrite(os2, asc, desc, gap, upm)
                continue
            yield nomes, asc / upm, (asc - desc + max(0, gap)) / upm


def _metricas_directwrite(os2: bytes, asc: int, desc: int, gap: int, upm: int) -> tuple[float, float]:
    fs = struct.unpack(">H", os2[62:64])[0]
    t_asc, t_desc, t_gap = struct.unpack(">hhh", os2[68:74])
    w_asc, w_desc = struct.unpack(">HH", os2[74:78])
    if fs & 0x80:
        return t_asc / upm, (t_asc - t_desc + max(0, t_gap)) / upm
    sobra = max(0, gap - ((w_asc + w_desc) - (asc - desc)))
    return w_asc / upm, (w_asc + w_desc + sobra) / upm


_INDICE: dict[str, dict] | None = None


def indice() -> dict[str, dict]:
    global _INDICE
    if _INDICE is not None:
        return _INDICE
    idx: dict[str, dict] = {}
    adobe = os.path.normcase(pasta_adobe_fonts())
    for pasta in pastas_de_fontes():
        da_adobe = os.path.normcase(pasta) == adobe
        for raiz, _dirs, arquivos in os.walk(pasta):
            for a in arquivos:
                if not da_adobe and not a.lower().endswith(_EXTENSOES):
                    continue
                caminho = os.path.join(raiz, a)
                try:
                    for nomes, asc, linha in _faces_do_arquivo(caminho):
                        ps = nomes.get(6)
                        if not ps:
                            continue
                        idx.setdefault(ps.lower(), {
                            "familia": nomes.get(16) or nomes.get(1) or ps,
                            "face": nomes.get(17) or nomes.get(2) or "Regular",
                            "ascendente": asc, "linha": linha, "adobe": da_adobe,
                            "arquivo": caminho})
                except (OSError, struct.error, ValueError):
                    continue
    _INDICE = idx
    return idx


_SUFIXOS_PS = re.compile(r"(MT|PS|Std|Pro)$")


def traduzir(postscript: str) -> dict:
    if not postscript:
        return {"familia": "Open Sans", "face": "", "ascendente": 1.069, "linha": 1.362,
                "adobe": False, "instalada": False}
    achada = indice().get(postscript.lower())
    if achada:
        return {**achada, "instalada": True}
    familia, _, face = postscript.partition("-")
    familia = _SUFIXOS_PS.sub("", familia) or familia
    familia = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", familia)
    face = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", _SUFIXOS_PS.sub("", face)) or "Regular"
    return {"familia": familia, "face": face, "ascendente": 0.9, "linha": 1.2, "adobe": False,
            "instalada": False}
