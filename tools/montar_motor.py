#!/usr/bin/env python3

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SERVICO = RAIZ / "plugins" / "media-composer" / "servico"
MOTOR = RAIZ / "motor"
DESTINO = RAIZ / "app" / "src-tauri" / "motor"
NOME = "delapraca-servico"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baixar_ffmpeg

DADOS = [
    (SERVICO / "ui", "ui"),
    (SERVICO / "recursos", "recursos"),
    (MOTOR / "drp" / "formas_r19.xml", "drp"),
    (MOTOR / "drp" / "formas_r21_arriraw.xml", "drp"),
    (MOTOR / "drp" / "formas_r21_still.xml", "drp"),
    (MOTOR / "drp" / "skeleton_r19.drt", "drp"),
    (MOTOR / "drp" / "fairlight_r19_mesa99.bin", "drp"),
    (MOTOR / "titulos" / "texto_r19.xml", "titulos"),
    (MOTOR / "avb_export" / "templates", "avb_export/templates"),
    (MOTOR / "avb_export" / "templates.json", "avb_export"),
    (RAIZ / "VERSION", "."),
    (RAIZ / "plugins" / "media-composer" / "painel", "integracoes/media-composer/painel"),
    (RAIZ / "plugins" / "resolve" / "casca", "integracoes/resolve/casca"),
]


def executavel(pasta: Path, alvo: str) -> Path:
    return pasta / (NOME + (".exe" if "windows" in alvo else ""))


def montar(alvo: str) -> Path:
    sep = ";" if "windows" in alvo else ":"
    faltam = [str(o) for o, _ in DADOS if not o.exists()]
    if faltam:
        raise SystemExit("faltam arquivos de dados: " + ", ".join(faltam))
    trabalho = RAIZ / "build" / "motor"
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--name", NOME, "--onedir", "--clean", "--noconfirm",
         "--distpath", str(trabalho / "dist"), "--workpath", str(trabalho / "work"),
         "--specpath", str(trabalho), "--paths", str(MOTOR), "--paths", str(SERVICO),
         "--hidden-import", "dialogo_win",
         *[f"--add-data={o}{sep}{d}" for o, d in DADOS],
         str(SERVICO / "main.py")],
        cwd=RAIZ, check=True)
    if DESTINO.exists():
        shutil.rmtree(DESTINO)
    shutil.copytree(trabalho / "dist" / NOME, DESTINO)
    baixar_ffmpeg.baixar(alvo, DESTINO)
    return executavel(DESTINO, alvo)


def conferir(exe: Path) -> dict:
    import json

    r = subprocess.run([str(exe), "--conferir-pacote"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    print(r.stdout.strip())
    if r.returncode:
        raise SystemExit(f"o motor montado NÃO passou na conferência: {r.stderr.strip()[-2000:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def tamanho(pasta: Path) -> float:
    return sum(p.stat().st_size for p in pasta.rglob("*") if p.is_file()) / 1e6


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("uso: python tools/montar_motor.py <alvo do Rust>")
    exe = montar(sys.argv[1])
    print(f"motor montado em {DESTINO} ({tamanho(DESTINO):.0f} MB)")
    conferir(exe)
    print("conferido.")
