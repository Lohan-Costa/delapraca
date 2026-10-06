#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
APP = RAIZ / "app" / "src-tauri"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import montar_motor

E_MAC = sys.platform == "darwin"
TARGET_MAC = Path.home() / "Library" / "Caches" / "DeLaPraCa-build" / "target"


def configuracao(versao: str) -> Path:
    cfg = json.loads((APP / "tauri.release.conf.json").read_text(encoding="utf-8"))
    cfg["version"] = versao
    if E_MAC:
        import unicodedata

        produto = json.loads((APP / "tauri.conf.json").read_text(encoding="utf-8"))["productName"]
        cfg["productName"] = unicodedata.normalize("NFD", produto)
        cfg["bundle"].setdefault("macOS", {})["signingIdentity"] = "-"
    destino = RAIZ / "build" / "tauri.release.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino


def montar(alvo: str, reusar_motor: bool = False) -> list[Path]:
    versao = (RAIZ / "VERSION").read_text(encoding="utf-8").strip()
    exe_motor = montar_motor.executavel(montar_motor.DESTINO, alvo)
    if not (reusar_motor and exe_motor.is_file() and montar_motor.conferir(exe_motor)["versao"] == versao):
        exe_motor = montar_motor.montar(alvo)
    if montar_motor.conferir(exe_motor)["versao"] != versao:
        raise SystemExit(f"o motor montado não é da versão {versao}")
    pacotes = ["nsis"] if "windows" in alvo else ["dmg"]
    env = dict(os.environ, CARGO_TARGET_DIR=str(TARGET_MAC)) if E_MAC else None
    subprocess.run(["cargo", "tauri", "build", "--target", alvo, "--bundles", *pacotes,
                    "--config", str(configuracao(versao))], cwd=APP, check=True, env=env)
    saida = APP / "target" / alvo / "release" / "bundle"
    if E_MAC:
        dmgs = sorted((TARGET_MAC / alvo / "release" / "bundle" / "dmg").glob(f"*{versao}*.dmg"))
        (saida / "dmg").mkdir(parents=True, exist_ok=True)
        for d in dmgs:
            shutil.copyfile(d, saida / "dmg" / d.name)
    return sorted(p for p in saida.rglob("*") if p.suffix in (".exe", ".dmg") and versao in p.name
                  and not p.name.startswith("._"))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("uso: python tools/montar_instalador.py <alvo do Rust> [--reusar-motor]")
    feitos = montar(sys.argv[1], "--reusar-motor" in sys.argv)
    if not feitos:
        raise SystemExit("o build terminou, mas não achei o instalador")
    for p in feitos:
        print(f"instalador: {p} ({p.stat().st_size / 1e6:.0f} MB)")
