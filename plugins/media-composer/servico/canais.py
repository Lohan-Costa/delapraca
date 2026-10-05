from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import plataforma

log = logging.getLogger("delapraca.canais")

EDICAO = "edicao"

SETORES: dict[str, dict] = {
    "cor": {"rotulo": "Cor", "de": "da Cor", "para": "para a Cor", "insert": "insert_cor"},
    "vfx": {"rotulo": "VFX", "de": "do VFX", "para": "para o VFX", "insert": "insert_vfx"},
}
ROTULO_LADO = {EDICAO: "Edição", **{k: v["rotulo"] for k, v in SETORES.items()}}

IMPORTADO = "importado"
SUBPASTA = {"timeline": "timelines", "insert": "inserts",
            **{v["insert"]: "inserts" for v in SETORES.values()}}
CONFIG = "magic_link.json"


def caixa_nome(lado: str) -> str:
    return f"para-{lado}"


def e_setor(setor) -> bool:
    return isinstance(setor, str) and setor in SETORES


def _config_maquina() -> dict:
    try:
        d = json.loads((plataforma.pasta_dados() / CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _gravar_maquina(d: dict) -> None:
    pasta = plataforma.pasta_dados()
    pasta.mkdir(parents=True, exist_ok=True)
    alvo = pasta / CONFIG
    tmp = alvo.with_name(f"{CONFIG}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, alvo)


def canais_da_maquina() -> dict[str, str]:
    c = _config_maquina().get("canais")
    return {k: v for k, v in c.items() if e_setor(k) and isinstance(v, str)} if isinstance(c, dict) else {}


def guardar_canal_na_maquina(setor: str, pasta: str) -> None:
    d = _config_maquina()
    d.pop("papel", None)
    d["canais"] = {**canais_da_maquina(), setor: pasta}
    _gravar_maquina(d)


def pasta_do_canal(canais: dict, setor: str) -> str | None:
    p = canais.get(setor) if e_setor(setor) else None
    return p if isinstance(p, str) and p and Path(p).is_dir() else None


def validar_pasta(pasta: str) -> str:
    p = Path(pasta or "")
    if not p.is_absolute():
        raise ValueError("escolha uma pasta (caminho completo)")
    if not p.is_dir():
        raise ValueError("essa pasta não existe ou não está acessível agora")
    return str(p)


def lados(setor: str) -> tuple[str, str]:
    return EDICAO, setor


def garantir_pastas(pasta_canal: str, setor: str) -> None:
    for lado in lados(setor):
        for sub in set(SUBPASTA.values()):
            (Path(pasta_canal) / caixa_nome(lado) / IMPORTADO / sub).mkdir(parents=True, exist_ok=True)


def caixa_de_saida(canais: dict, setor: str) -> str:
    if not e_setor(setor):
        raise ValueError(f"canal desconhecido: {setor!r}")
    pasta = pasta_do_canal(canais, setor)
    if not pasta:
        raise ValueError(f"conecte o canal {SETORES[setor]['rotulo']} em Ajustes antes de enviar")
    garantir_pastas(pasta, setor)
    return str(Path(pasta) / caixa_nome(setor))


def tipo_do_insert(setor: str) -> str:
    return SETORES[setor]["insert"]


def arquivar(carta: str, tipo: str) -> str:
    p = Path(carta)
    destino = p.parent / IMPORTADO / SUBPASTA.get(tipo, "timelines")
    destino.mkdir(parents=True, exist_ok=True)
    base = p.name[:-len(".carta.json")] if p.name.endswith(".carta.json") else p.stem
    alvo = destino / p.name
    n = 2
    while alvo.exists():
        alvo = destino / f"{base}_{n}.carta.json"
        n += 1
    os.replace(p, alvo)
    return str(alvo)


def e_pendente(carta: str) -> bool:
    return Path(carta).parent.name.startswith("para-")
