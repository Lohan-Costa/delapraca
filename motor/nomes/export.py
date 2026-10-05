from __future__ import annotations

import re
from datetime import datetime

from . import template

CAMPOS = (
    ("sequencia", "nome da sequência"),
    ("data", "data AAMMDD"),
    ("hora", "hora HHMM"),
    ("projeto", "nome do projeto"),
    ("tipo", "COR ou VFX"),
)

SO_INSERT = frozenset({"tipo"})


def valores(sequencia: str, projeto: str = "", agora: datetime | None = None,
            tipo: str = "") -> dict:
    t = agora or datetime.now()
    return {
        "sequencia": sequencia or "",
        "data": t.strftime("%y%m%d"),
        "hora": t.strftime("%H%M"),
        "projeto": projeto or "",
        "tipo": (tipo or "").upper(),
    }


def literal(texto: str) -> str:
    return (texto or "").replace("%", "%%")


_PROIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVADOS = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
               *(f"lpt{i}" for i in range(1, 10))}


def nome_de_arquivo(texto: str) -> str:
    limpo = _PROIBIDOS.sub("_", texto or "").strip().rstrip(". ")
    if limpo.split(".")[0].lower() in _RESERVADOS:
        limpo = "_" + limpo
    return limpo[:150]


def analisar(padrao: str, campos: dict) -> dict:
    ruins = template.validar(padrao, campos)
    if ruins:
        return {"nome": "", "erro": "campo que não existe: " + ", ".join(t.bruto for t in ruins),
                "ruins": [{"inicio": t.inicio, "fim": t.fim, "bruto": t.bruto} for t in ruins]}
    nome = nome_de_arquivo(template.render(padrao, campos))
    if not nome:
        return {"nome": "", "erro": "o nome ficou vazio", "ruins": []}
    return {"nome": nome, "erro": "", "ruins": []}
