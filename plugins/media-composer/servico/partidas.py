from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import plataforma

PASTA = "partidas"
INDICE = "partidas.json"
RE_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_VERSAO = re.compile(r"[\s._-]*(v|ver|versao|versão)?[\s._-]*\d+\s*$", re.IGNORECASE)


def _pasta() -> Path:
    p = plataforma.pasta_dados() / PASTA
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ler() -> list[dict]:
    try:
        d = json.loads((_pasta() / INDICE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [x for x in d if isinstance(x, dict) and RE_ID.match(str(x.get("id") or ""))] \
        if isinstance(d, list) else []


def _gravar(lista: list[dict]) -> None:
    alvo = _pasta() / INDICE
    tmp = alvo.with_name(f"{INDICE}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(lista, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, alvo)


def base_do_nome(nome: str) -> str:
    return _VERSAO.sub("", (nome or "").strip()).strip().lower()


def guardar(id_envio: str, carta_arquivo: str, resumo: dict, timeline: dict | None,
            familia: str = "avid") -> dict:
    if not RE_ID.match(id_envio or ""):
        raise ValueError("envio inválido")
    destino = _pasta() / f"{id_envio}.carta"
    shutil.copyfile(carta_arquivo, destino)
    return _anotar(id_envio, resumo, timeline, familia)


def guardar_doc(doc: dict, resumo: dict, timeline: dict | None, familia: str = "avid") -> dict:
    import uuid

    id_novo = str(uuid.uuid4())
    alvo = _pasta() / f"{id_novo}.carta"
    tmp = alvo.with_name(f"{id_novo}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, alvo)
    return _anotar(id_novo, resumo, timeline, familia)


def _anotar(id_envio: str, resumo: dict, timeline: dict | None, familia: str) -> dict:
    t = timeline if isinstance(timeline, dict) else {}
    item = {"id": id_envio, "timeline_uid": str(t.get("uid") or "")[:80],
            "timeline_nome": str(t.get("nome") or resumo.get("nome") or "")[:200],
            "sequencia": str(resumo.get("sequencia") or "")[:200],
            "mob_id": str(resumo.get("mob_id") or "")[:200],
            "canal": str(resumo.get("canal") or "")[:40],
            "enviado_em": str(resumo.get("enviado_em") or "")[:40],
            "familia": str(familia or "avid")[:20],
            "trazido_em": datetime.now().astimezone().isoformat(timespec="microseconds")}
    lista = [x for x in _ler() if x["id"] != id_envio] + [item]
    _gravar(lista)
    return item


def aposentar(id_envio: str) -> None:
    if RE_ID.match(id_envio or ""):
        _gravar([x for x in _ler() if x["id"] != id_envio])


def listar() -> list[dict]:
    pasta = _pasta()
    return sorted((x for x in _ler() if (pasta / f"{x['id']}.carta").is_file()),
                  key=lambda x: (x.get("trazido_em") or "", x.get("enviado_em") or ""), reverse=True)


def familia(id_envio: str) -> str:
    return next((str(x.get("familia") or "avid") for x in _ler() if x["id"] == id_envio), "avid")


def da_timeline(uid: str, nome: str) -> list[dict]:
    todas = listar()
    mesmo_nome = lambda x: bool(nome) and (x.get("timeline_nome") or "").casefold() == nome.casefold()
    par = [x for x in todas if uid and x.get("timeline_uid") == uid and mesmo_nome(x)]
    if par:
        return [{**x, "motivo": "desta_timeline"} for x in par]
    pelo_nome = [x for x in todas if mesmo_nome(x)]
    if pelo_nome:
        return [{**x, "motivo": "mesmo_nome_de_timeline"} for x in pelo_nome]
    por_uid = [x for x in todas if uid and x.get("timeline_uid") == uid]
    if len({(x.get("timeline_nome") or "").casefold() for x in por_uid}) == 1:
        return [{**x, "motivo": "desta_timeline"} for x in por_uid]
    return []


def carta(id_envio: str) -> Path:
    if not RE_ID.match(id_envio or ""):
        raise ValueError("partida inválida")
    p = _pasta() / f"{id_envio}.carta"
    if not p.is_file():
        raise LookupError("a Carta de partida não está mais nesta máquina — traga como nova")
    return p


def sugerir(resumo_nova: dict) -> list[dict]:
    mob = str(resumo_nova.get("mob_id") or "")
    base = base_do_nome(resumo_nova.get("sequencia") or resumo_nova.get("nome") or "")
    fora = []
    for x in listar():
        if x["id"] == resumo_nova.get("id"):
            continue
        if mob and x.get("mob_id") == mob:
            motivo, peso = "mesma_sequencia", 0
        elif base and base in (base_do_nome(x.get("sequencia") or ""), base_do_nome(x.get("timeline_nome") or "")):
            motivo, peso = "mesmo_nome", 1
        else:
            motivo, peso = "recente", 2
        fora.append((peso, {**x, "motivo": motivo}))
    return [x for _p, x in sorted(fora, key=lambda t: t[0])]


AJUSTES = "atualizar.json"
FAIXAS_DE_FABRICA = (25.0, 60.0)


def faixas() -> tuple[float, float]:
    try:
        d = json.loads((plataforma.pasta_dados() / AJUSTES).read_text(encoding="utf-8"))
        a, b = float(d["faixas"][0]), float(d["faixas"][1])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return FAIXAS_DE_FABRICA
    return (a, b) if 0 < a < b <= 100 else FAIXAS_DE_FABRICA


def gravar_faixas(a, b) -> tuple[float, float]:
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        a, b = FAIXAS_DE_FABRICA
    if not 0 < a < b <= 100:
        a, b = FAIXAS_DE_FABRICA
    pasta = plataforma.pasta_dados()
    pasta.mkdir(parents=True, exist_ok=True)
    alvo = pasta / AJUSTES
    tmp = alvo.with_name(f"{AJUSTES}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"faixas": [a, b]}), encoding="utf-8")
    os.replace(tmp, alvo)
    return a, b
