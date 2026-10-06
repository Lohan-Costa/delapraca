#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
VERSION = RAIZ / "VERSION"
BUILD = RAIZ / "build.avpi"

sys.path.insert(0, str(RAIZ / "plugins" / "media-composer" / "servico"))
import plataforma




def proxima_versao(atual: str) -> str:
    partes = atual.strip().split(".")
    partes[-1] = str(int(partes[-1]) + 1)
    return ".".join(partes)


def empacotar(versao: str) -> Path:
    import integracoes

    BUILD.write_bytes(integracoes.avpi(versao))
    return BUILD


def instalar_resolve() -> str:
    import integracoes

    try:
        return f"painel do Resolve: {integracoes.instalar_resolve(forcar=True)['destino']}"
    except integracoes.Impedido as e:
        return f"painel do Resolve não instalado: {e}"
    except Exception as e:
        return f"AVISO: não consegui instalar o painel do Resolve ({e})"


def main(argv=None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="Empacota e instala o painel .avpi")
    ap.add_argument("versao", nargs="?", help="versão exata (senão, auto-incrementa)")
    ap.add_argument("--so-empacota", action="store_true",
                    help="gera o build.avpi e não instala")
    args = ap.parse_args(argv)

    atual = VERSION.read_text(encoding="utf-8").strip() if VERSION.is_file() else "0.1.0"
    versao = args.versao or proxima_versao(atual)
    VERSION.write_text(versao + "\n", encoding="utf-8")
    print(f"versão: {versao}")

    caminho = empacotar(versao)
    with zipfile.ZipFile(caminho) as z:
        for nome in z.namelist():
            print(f"  {nome}")

    if args.so_empacota:
        print(f"empacotado: {caminho}  (não instalado)")
        return 0

    import integracoes

    try:
        destino = integracoes.instalar_mc(forcar=True)["destino"]
    except integracoes.Impedido as e:
        raise SystemExit(f"ERRO: {e}") from None
    print(f"instalado: {destino}")
    print(instalar_resolve())
    print()
    print(">>> painel já aberto? botão direito na área vazia → Reload")
    print(">>> painel novo/atualizado? feche o MC, rode 'python tools/reload.py', reabra")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
