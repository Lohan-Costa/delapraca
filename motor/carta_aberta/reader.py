from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from carta_aberta.writer import CAMPOS_DO_MASTER, TIPOS_DE_ENVIO

LIMITE_BYTES = 64 * 1024 * 1024

LIMITE_SEGMENTOS = 100_000
LIMITE_MASTERS = 50_000

_DO_WRITER = ("arquivo", "offline")


class CartaInvalida(ValueError):
    pass


@dataclass
class Carta:

    resultado: dict
    arquivos: dict[str, str]
    gerenciados: dict[str, str]
    colunas_por_mob: dict[str, dict]
    nome: str = ""
    comentario: str | None = None
    envio: dict | None = None
    selecao: list[dict] | None = None
    versao: str = ""
    doc: dict = field(default_factory=dict, repr=False)

    @property
    def tipo(self) -> str:
        return (self.envio or {}).get("tipo") or "timeline"


def ler_carta(origem: str | Path | dict, limite_bytes: int = LIMITE_BYTES) -> Carta:
    if isinstance(origem, dict):
        doc = origem
    else:
        caminho = Path(origem)
        try:
            tamanho = caminho.stat().st_size
        except OSError as e:
            raise CartaInvalida(f"não consegui abrir a carta: {e.strerror or e}") from None
        if tamanho > limite_bytes:
            raise CartaInvalida(f"a carta tem {tamanho // (1024 * 1024)} MB — "
                                f"passa do limite de {limite_bytes // (1024 * 1024)} MB")
        try:
            doc = json.loads(caminho.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise CartaInvalida(f"a carta não é um JSON legível ({e.__class__.__name__})") from None
        except RecursionError:
            raise CartaInvalida("a carta tem aninhamento absurdo — recusada") from None
    return _interpretar(doc)


def _sem_caminhos(s: dict) -> dict:
    ref = s.get("media_ref")
    if isinstance(ref, dict):
        s["media_ref"] = {**ref, "local_path": "", "url": ""}
    return s


def _exigir(cond: bool, texto: str) -> None:
    if not cond:
        raise CartaInvalida(texto)


def _numero(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _interpretar(doc) -> Carta:
    _exigir(isinstance(doc, dict), "a carta não é um objeto JSON")
    versao = doc.get("carta_aberta")
    _exigir(isinstance(versao, str) and versao.split(".")[0] == "1",
            f"versão de carta desconhecida: {versao!r} (este De Lá Pra Cá lê 1.x)")
    _exigir(versao != "1.0", "carta 1.0: não traz a leitura completa dos masters — "
                             "gere de novo com este De Lá Pra Cá")

    tempo = doc.get("tempo")
    origem = doc.get("origem")
    segmentos = doc.get("segmentos")
    masters = doc.get("masters")
    _exigir(isinstance(tempo, dict) and _numero(tempo.get("fps")) and tempo["fps"] > 0,
            "a carta não diz a taxa da timeline")
    _exigir(isinstance(origem, dict), "a carta não diz de onde veio")
    _exigir(isinstance(segmentos, list) and all(isinstance(s, dict) for s in segmentos),
            "os segmentos da carta estão malformados")
    _exigir(isinstance(masters, list) and all(isinstance(m, dict) for m in masters),
            "os masters da carta estão malformados")
    _exigir(len(segmentos) <= LIMITE_SEGMENTOS and len(masters) <= LIMITE_MASTERS,
            f"a carta tem {len(segmentos)} segmentos e {len(masters)} masters — passa do limite "
            f"({LIMITE_SEGMENTOS} / {LIMITE_MASTERS})")

    masters_leitura: dict[str, dict] = {}
    arquivos: dict[str, str] = {}
    gerenciados: dict[str, str] = {}
    colunas: dict[str, dict] = {}
    for m in masters:
        chave = m.get("mob_id")
        avid = m.get("avid")
        _exigir(isinstance(chave, str) and chave, "um master da carta não tem mob_id")
        _exigir(isinstance(avid, dict), f"o master {chave} não traz o bloco 'avid'")
        info = {k: avid[k] for k in CAMPOS_DO_MASTER if k in avid}
        info["mob_id"] = chave
        masters_leitura[chave] = info
        if isinstance(avid.get("colunas_mc"), dict):
            colunas[chave] = dict(avid["colunas_mc"])
        if isinstance(m.get("arquivo_ama"), str) and m["arquivo_ama"]:
            arquivos[chave] = m["arquivo_ama"]
        essencias = info.get("essencias") or {}
        for trilha, mxf in (m.get("gerenciadas") or {}).items():
            urn = essencias.get(trilha) if isinstance(essencias, dict) else None
            if isinstance(urn, str) and isinstance(mxf, str) and mxf:
                gerenciados[urn] = mxf

    segs = [_sem_caminhos({k: v for k, v in s.items() if k not in _DO_WRITER}) for s in segmentos]
    resultado = {
        "segments": segs,
        "timeline_fps": tempo["fps"],
        "total_segments": len(segs),
        "has_nested_scope": False,
        "composition_name": origem.get("sequencia") or "",
        "sequence_mob_id": origem.get("mob_id"),
        "timeline_start_frames": tempo.get("tc_inicial_frames"),
        "timeline_drop_frame": bool(tempo.get("drop_frame")),
        "timeline_start_tc": tempo.get("tc_inicial"),
        "markers": list(doc.get("marcadores") or []),
        "masters": masters_leitura,
        "avisos": list(doc.get("avisos") or []),
        "conferencia": list(doc.get("conferencia") or []),
        "fonte": origem.get("leitura") or "avb",
    }

    envio = doc.get("envio")
    if envio is not None:
        _exigir(isinstance(envio, dict) and envio.get("tipo") in TIPOS_DE_ENVIO,
                f"tipo de envio desconhecido: {(envio or {}).get('tipo')!r}")
    selecao = doc.get("selecao")
    if selecao is not None:
        _exigir(isinstance(selecao, list) and all(isinstance(s, dict) for s in selecao),
                "a seleção do insert está malformada")

    comentario = doc.get("comentario")
    return Carta(resultado=resultado, arquivos=arquivos, gerenciados=gerenciados,
                 colunas_por_mob=colunas, nome=str(doc.get("nome") or ""),
                 comentario=comentario if isinstance(comentario, str) else None,
                 envio=envio, selecao=selecao, versao=versao, doc=doc)
