from __future__ import annotations

import json
import os
import re
from pathlib import Path

import plataforma

ARQUIVO = "configuracoes.json"

TETO_PADRAO_GB = 20
TETO_MINIMO_GB = 1
TETO_MAXIMO_GB = 2000

_PADROES: dict = {
    "cache_dir": None,
    "cache_max_gb": TETO_PADRAO_GB,
    "cache_antigo_dispensado": False,
    "avisar_versao_nova": True,
}


def _arquivo() -> Path:
    return plataforma.pasta_dados() / ARQUIVO


def pasta_aceitavel(caminho) -> str | None:
    if not isinstance(caminho, str):
        return None
    c = caminho.strip()
    if not c or "\x00" in c or c.startswith(("\\\\", "//")):
        return None
    if plataforma.E_WINDOWS:
        if not re.match(r"^[A-Za-z]:[\\/]", c):
            return None
    elif not c.startswith("/"):
        return None
    return os.path.normpath(c)


def _sanear(dado: dict) -> dict:
    fora = dict(dado)
    fora["cache_dir"] = pasta_aceitavel(fora.get("cache_dir"))
    try:
        gb = int(fora.get("cache_max_gb"))
    except (TypeError, ValueError):
        gb = TETO_PADRAO_GB
    fora["cache_max_gb"] = gb if TETO_MINIMO_GB <= gb <= TETO_MAXIMO_GB else TETO_PADRAO_GB
    fora["cache_antigo_dispensado"] = fora.get("cache_antigo_dispensado") is True
    fora["avisar_versao_nova"] = fora.get("avisar_versao_nova") is not False
    return fora


def ler() -> dict:
    dado = dict(_PADROES)
    try:
        bruto = json.loads(_arquivo().read_text(encoding="utf-8"))
        if isinstance(bruto, dict):
            dado.update({k: bruto[k] for k in _PADROES if k in bruto})
    except (OSError, ValueError):
        pass
    return _sanear(dado)


def gravar(mudancas: dict) -> dict:
    dado = ler()
    dado.update({k: mudancas[k] for k in _PADROES if k in mudancas})
    dado = _sanear(dado)
    alvo = _arquivo()
    alvo.parent.mkdir(parents=True, exist_ok=True)
    tmp = alvo.with_name(f"{ARQUIVO}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(dado, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, alvo)
    return dado
