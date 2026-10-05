from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from aaf.parser import frames_to_tc

VERSAO_DO_FORMATO = "1.2"

CAMPOS_DO_MASTER = ("nome", "caminho_na_bin", "descritor", "fps", "duracao_frames",
                    "identidade", "essencias", "colunas_mc")

TIPOS_DE_ENVIO = ("timeline", "insert_cor", "insert_vfx", "insert")


def montar(resultado: dict, match_results: list, nome: str, comentario: str | None = None,
           aplicativo: str = "Avid Media Composer", gerado_por: str = "De Lá Pra Cá", *,
           arquivos: dict[str, str] | None = None, gerenciados: dict[str, str] | None = None,
           envio: dict | None = None, selecao: list[dict] | None = None) -> dict:
    if envio is not None and envio.get("tipo") not in TIPOS_DE_ENVIO:
        raise ValueError(f"tipo de envio desconhecido: {envio.get('tipo')!r}")
    arquivos = arquivos or {}
    gerenciados = gerenciados or {}
    validos = [m for m in (match_results or []) if m.get("matched_path") and not m.get("rejected")]
    por_segmento = {m["segment_index"]: m["matched_path"] for m in validos}
    offline = {m["segment_index"] for m in validos if m.get("offline")}
    segmentos = []
    for i, s in enumerate(resultado.get("segments") or []):
        item = dict(s)
        item["arquivo"] = por_segmento.get(i)
        item["offline"] = i in offline
        segmentos.append(item)

    por_mob: dict[str, dict] = {}
    for s in segmentos:
        if s.get("mob_id") and s["arquivo"] and s["mob_id"] not in por_mob:
            por_mob[s["mob_id"]] = {"arquivo": s["arquivo"], "offline": s["offline"]}
    masters = []
    for chave, m in (resultado.get("masters") or {}).items():
        ident = m.get("identidade") or {}
        inicio, tc_fps = ident.get("tc_inicio"), ident.get("tc_fps")
        essencias = m.get("essencias") or {}
        masters.append({
            "mob_id": chave,
            "nome": m.get("nome"),
            "reel": ident.get("reel") or None,
            "tc_origem": frames_to_tc(inicio, tc_fps) if inicio is not None and tc_fps else None,
            "duracao_frames": m.get("duracao_frames"),
            "fps": ident.get("fps") or m.get("fps"),
            "resolucao": ([ident["largura"], ident["altura"]]
                          if ident.get("largura") and ident.get("altura") else None),
            "arquivo_original": ((m.get("colunas_mc") or {}).get("Source File") or None),
            **por_mob.get(chave, {"arquivo": None, "offline": None}),
            "avid": {k: m[k] for k in CAMPOS_DO_MASTER if k in m},
            "arquivo_ama": arquivos.get(chave),
            "gerenciadas": {trilha: gerenciados[urn] for trilha, urn in essencias.items()
                            if urn in gerenciados},
        })

    texto = (comentario or "").strip()
    doc = {
        "carta_aberta": VERSAO_DO_FORMATO,
        "gerado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
        "gerado_por": gerado_por,
        "nome": nome,
        "origem": {
            "aplicativo": aplicativo,
            "sequencia": resultado.get("composition_name") or "",
            "mob_id": resultado.get("sequence_mob_id"),
            "leitura": resultado.get("fonte") or "aaf",
        },
        "tempo": {
            "fps": resultado.get("timeline_fps"),
            "drop_frame": bool(resultado.get("timeline_drop_frame")),
            "tc_inicial": resultado.get("timeline_start_tc"),
            "tc_inicial_frames": resultado.get("timeline_start_frames"),
            "base_dos_segmentos": "frames a partir do início da timeline (00:00:00:00), "
                                  "sem o TC inicial somado",
        },
        "comentario": texto or None,
        "marcadores": list(resultado.get("markers") or []),
        "masters": masters,
        "segmentos": segmentos,
        "resumo": {
            "segmentos": len(segmentos),
            "com_arquivo": sum(1 for s in segmentos if s["arquivo"] and not s["offline"]),
            "offline": sum(1 for s in segmentos if s["offline"]),
            "sem_arquivo": sum(1 for s in segmentos
                               if not s["arquivo"] and s.get("mob_id")
                               and not s.get("is_gap") and not s.get("is_transition")),
        },
        "avisos": list(resultado.get("avisos") or []),
        "conferencia": list(resultado.get("conferencia") or []),
    }
    if envio is not None:
        doc["envio"] = dict(envio)
    if selecao is not None:
        doc["selecao"] = [dict(s) for s in selecao]
    return doc


def build_carta_aberta(resultado: dict, match_results: list, saida: str, nome: str,
                       comentario: str | None = None, **extras) -> dict:
    import tempfile

    doc = montar(resultado, match_results, nome, comentario, **extras)
    alvo = Path(saida)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    fd, temporario = tempfile.mkstemp(dir=alvo.parent, prefix=f".{alvo.name}.", suffix=".parcial")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(temporario, alvo)
    except BaseException:
        Path(temporario).unlink(missing_ok=True)
        raise
    return {"output": str(alvo), **doc["resumo"]}
