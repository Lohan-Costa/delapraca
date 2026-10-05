from __future__ import annotations

import json
import os

import plataforma
from drp import sinais

ARQUIVO = "codigo_de_cores.json"


def ler() -> dict:
    try:
        d = json.loads((plataforma.pasta_dados() / ARQUIVO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = None
    return sinais.validar_codigo(d)


def gravar(d) -> dict:
    codigo = sinais.validar_codigo(d)
    pasta = plataforma.pasta_dados()
    pasta.mkdir(parents=True, exist_ok=True)
    alvo = pasta / ARQUIVO
    tmp = alvo.with_name(f"{ARQUIVO}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(codigo, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, alvo)
    return codigo


def estado(codigo: dict | None = None) -> dict:
    codigo = codigo or ler()
    return {"codigo": codigo, "fabrica": sinais.de_fabrica(),
            "categorias": sinais.CATEGORIAS,
            "tabelas": {"marcador": list(sinais.COR_DE_MARCADOR), "clipe": list(sinais.COR_DE_CLIPE)},
            "avisos": sinais.repeticoes(codigo)}
