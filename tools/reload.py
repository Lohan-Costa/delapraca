#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "plugins" / "media-composer" / "servico"))
import plataforma


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Limpa o gateway do Panel SDK")
    ap.add_argument("--todos", action="store_true",
                    help="mata qualquer gateway, não só o órfão")
    args = ap.parse_args(argv)

    if plataforma.media_composer_aberto():
        print("AVISO: o Media Composer parece ABERTO.")
        print("       Para recarregar de forma confiável, feche-o ANTES.")
        print("       (com ele aberto, o MC sobe outro gateway na hora e NÃO re-registra)")
        print()

    rotulo = "gateway(s)" if args.todos else "gateway(s) ÓRFÃO(S)"
    r = plataforma.liberar_gateway(todos=args.todos)

    if not r["alvos"]:
        print(f"nenhum {rotulo} rodando — nada a matar.")
        if not args.todos:
            print("(há um gateway vivo com pai vivo? use --todos para matá-lo mesmo assim)")
        return 0

    print(f"matando {rotulo}: {', '.join(map(str, r['alvos']))}")
    if r["restantes"]:
        print(f"ATENÇÃO: ainda há gateway rodando (pid {r['restantes']}).")
        return 1
    print("OK: portas do Panel SDK livres. Pode abrir o Media Composer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
