from __future__ import annotations

import getpass
import logging
import os
import socket
import uuid
from datetime import datetime
from pathlib import Path

import banco
import canais

log = logging.getLogger("delapraca.magic_link")

SUFIXO = ".carta.json"
TIPOS_INSERT = {k: v["insert"] for k, v in canais.SETORES.items()}
INSERT_EXPORTADO = "insert"


def canais_do_projeto(pasta_projeto: str) -> dict[str, str]:
    c = ((banco.ler_config(pasta_projeto) or {}).get("magic_link") or {}).get("canais")
    if not isinstance(c, dict):
        return {}
    return {k: v for k, v in c.items() if canais.e_setor(k) and isinstance(v, str)}


def guardar_canal_no_projeto(pasta_projeto: str, setor: str, pasta: str) -> str:
    if not canais.e_setor(setor):
        raise ValueError(f"canal desconhecido: {setor!r}")
    pasta = canais.validar_pasta(pasta)
    config = banco.ler_config(pasta_projeto)
    ml = dict(config.get("magic_link") or {})
    ml["canais"] = {**canais_do_projeto(pasta_projeto), setor: pasta}
    config["magic_link"] = ml
    if not banco.gravar_config(pasta_projeto, config):
        raise RuntimeError("não consegui guardar o canal na configuração do projeto")
    return pasta


def novo_envio(tipo: str, canal: str | None = None, de: str = canais.EDICAO) -> dict:
    try:
        usuario = getpass.getuser()
    except Exception:
        usuario = ""
    envio = {"id": str(uuid.uuid4()), "tipo": tipo,
             "remetente": {"maquina": socket.gethostname(), "usuario": usuario},
             "enviado_em": datetime.now().astimezone().isoformat(timespec="seconds")}
    if canal:
        envio.update({"canal": canal, "de": de,
                      "para": canal if de == canais.EDICAO else canais.EDICAO})
    return envio


def caminho_do_envio(pasta: str, nome_sequencia: str, envio: dict) -> str:
    from nomes.export import nome_de_arquivo

    base = nome_de_arquivo(nome_sequencia or "") or "timeline"
    return str(Path(pasta) / f"{base}__{envio['id'][:8]}{SUFIXO}")


def conferir_itens(itens: list[dict], sequencia: dict) -> tuple[list[dict], list[dict]]:
    from carta_aberta.insert import _tc

    fps = float(sequencia.get("timeline_fps") or 0) or 25.0
    por = {}
    for s in sequencia.get("segments") or []:
        if s.get("mob_id") and not s.get("is_gap") and not s.get("is_transition"):
            por.setdefault((int(s["track"]), s["mob_id"]), []).append(
                (_tc(s["timeline_tc_in"], fps), _tc(s["timeline_tc_out"], fps)))
    valem, mudaram = [], []
    for it in itens:
        a, b = _tc(it["timeline_tc_in"], fps), _tc(it["timeline_tc_out"], fps)
        cobre = any(x <= a and b <= y for x, y in por.get((int(it["track"]), it["mob_id"]), []))
        (valem if cobre else mudaram).append(it)
    return valem, mudaram


def enviar_insert(mc, coleta, tipo: str, pasta: str, comentario: str = "",
                  so_video: bool = False, anotar=lambda *a, **k: None,
                  nome: str | None = None, canal: str | None = None) -> dict:
    import relink_avb
    from avb_bin.segmentos import ler_timeline_de_arquivo, montar_midia
    from carta_aberta.writer import build_carta_aberta
    from sessao_export import resolver_midia

    if tipo not in (*TIPOS_INSERT.values(), INSERT_EXPORTADO):
        raise ValueError(f"tipo de insert desconhecido: {tipo!r}")
    seq_mob = (coleta.sequencia or {}).get("mob_id")
    itens = [i for i in coleta.itens if not (so_video and int(i["track"]) >= 1000)]
    if not seq_mob or not itens:
        raise ValueError("a lista está vazia — colete clipes antes de enviar")

    anotar("salvando a bin da sequência")
    bin_seq = mc.bin_do_mob(seq_mob)
    if not bin_seq or not Path(bin_seq).is_file():
        raise LookupError("não achei a bin da sequência — ela está numa bin salva do projeto?")
    relink_avb.garantir_bin_no_disco(mc, bin_seq)
    anotar("lendo a timeline")
    leitura = ler_timeline_de_arquivo(bin_seq, mob_id=seq_mob)

    valem, mudaram = conferir_itens(itens, leitura)
    if not valem:
        raise ValueError("nenhum clipe da lista está mais na timeline como foi coletado — "
                         "colete de novo")
    midia = resolver_midia(mc, leitura, anotar, so_masters={i["mob_id"] for i in valem})

    envio = novo_envio(tipo, canal)
    nome = (nome or "").strip() or leitura.get("composition_name") or ""
    caminho = caminho_do_envio(pasta, nome, envio)
    selecao = [{k: i[k] for k in ("track", "timeline_tc_in", "timeline_tc_out", "mob_id",
                                  "clip_name")} for i in valem]
    anotar("escrevendo a carta na pasta combinada")
    _segs, casados, _metas = montar_midia(
        leitura, midia["arquivos"], pasta,
        {k: m.get("colunas_mc") or {} for k, m in leitura["masters"].items()},
        midia["gerenciados"])
    resumo = build_carta_aberta(leitura, casados, caminho, nome,
                                comentario, arquivos=midia["arquivos"],
                                gerenciados=midia["gerenciados"], envio=envio, selecao=selecao)
    log.info("insert %s enviado: %s (%d clipe(s), %d mudaram)", tipo, caminho, len(valem),
             len(mudaram))
    return {"arquivo": caminho, "tipo": tipo, "id": envio["id"], "clipes": len(valem),
            "mudaram": mudaram, "saida": {k: v for k, v in resumo.items() if k != "output"}}
