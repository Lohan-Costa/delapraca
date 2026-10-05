#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import io
import stat
import sys
import urllib.request
import zipfile
from pathlib import Path

VENDOR = "https://github.com/Lohan-Costa/orbita/releases/download/vendor-ffmpeg-8.1.2"
WIN_ZIP = f"{VENDOR}/ffmpeg-win64-gpl.zip"
MAC_FFMPEG_ZIP = f"{VENDOR}/ffmpeg-macos-arm64.zip"
MAC_FFPROBE_ZIP = f"{VENDOR}/ffprobe-macos-arm64.zip"

SHA256 = {
    "ffmpeg-win64-gpl.zip": "0d5dd2a2c87c57c8578593aaedcaaea8397989e2cb1606b843c5c988a290a651",
    "ffmpeg-macos-arm64.zip": "67e5309940f435289e9fc597c097cfa09249813fe07461445305aaff2f4bd2b6",
    "ffprobe-macos-arm64.zip": "43a8d0f48b6ab30cc3e0edf5e7b4c537c605017f33338077f0e49547e513fd55",
}


CACHE = Path(__file__).resolve().parent.parent / "build" / "vendor"


def _baixar(url: str) -> bytes:
    nome = url.rsplit("/", 1)[-1]
    esperado = SHA256[nome]
    guardado = CACHE / nome
    if guardado.is_file() and hashlib.sha256(guardado.read_bytes()).hexdigest() == esperado:
        print(f"  {nome}: do cache, sha256 confere")
        return guardado.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "delapraca-baixar-ffmpeg"})
    with urllib.request.urlopen(req, timeout=300) as r:
        dado = r.read()
    obtido = hashlib.sha256(dado).hexdigest()
    if obtido != esperado:
        raise SystemExit(f"O ARQUIVO BAIXADO NÃO É O ESPERADO — nada foi instalado.\n  url: {url}\n"
                         f"  esperado: {esperado}\n  recebido: {obtido}\n"
                         "  Ou o release de vendor mudou sem este script, ou alguém trocou o arquivo.")
    print(f"  {nome}: baixado, sha256 confere")
    CACHE.mkdir(parents=True, exist_ok=True)
    guardado.write_bytes(dado)
    return dado


def _do_zip(dado: bytes, ferramenta: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(dado)) as z:
        for n in z.namelist():
            if Path(n).name in (ferramenta, ferramenta + ".exe"):
                return z.read(n)
        arquivos = [n for n in z.namelist() if not n.endswith("/")]
        if len(arquivos) == 1:
            return z.read(arquivos[0])
    raise SystemExit(f"não achei {ferramenta} no zip")


def baixar(alvo: str, destino: Path) -> None:
    destino.mkdir(parents=True, exist_ok=True)
    if "windows" in alvo:
        z = _baixar(WIN_ZIP)
        partes = {"ffmpeg.exe": _do_zip(z, "ffmpeg"), "ffprobe.exe": _do_zip(z, "ffprobe")}
    elif alvo == "aarch64-apple-darwin":
        partes = {"ffmpeg": _do_zip(_baixar(MAC_FFMPEG_ZIP), "ffmpeg"),
                  "ffprobe": _do_zip(_baixar(MAC_FFPROBE_ZIP), "ffprobe")}
    else:
        raise SystemExit(f"alvo sem ffmpeg mapeado: {alvo}")
    for nome, dado in partes.items():
        p = destino / nome
        p.write_bytes(dado)
        if not nome.endswith(".exe"):
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("uso: python tools/baixar_ffmpeg.py <alvo do Rust> <pasta de destino>")
    baixar(sys.argv[1], Path(sys.argv[2]))
