from __future__ import annotations

import re
from pathlib import Path

_SUFIXO_AVID = re.compile(r"\.(new|sync|cds|grp|sub|copy|exported|cpy)\.\d{1,4}$", re.I)


def nome_base(nome: str) -> str:
    anterior = None
    while nome != anterior:
        anterior = nome
        nome = _SUFIXO_AVID.sub("", nome)
    base = nome.lower()
    for ext in (".mov", ".mxf", ".mp4", ".wav", ".aif", ".aiff", ".mp3", ".m4a"):
        if base.endswith(ext):
            return base[: -len(ext)]
    return base


def arquivos_por_umid(raizes: list[str], on_progress=None) -> dict[str, list[str]]:
    from media import opatom

    from .mcapi import mob_id_para_pyavb

    res = opatom.scan(raizes, on_progress=on_progress)
    fora: dict[str, list[str]] = {}
    for c in res.clips:
        if not c.umid:
            continue
        try:
            fora[mob_id_para_pyavb(c.umid)] = c.paths
        except Exception:
            continue
    return fora


def pastas_de_midia_do_avid(colunas_por_master: list[dict]) -> list[str]:
    import plataforma

    fora: list[str] = []
    for col in colunas_por_master:
        raiz = plataforma.raiz_do_volume((col.get("Drive") or "").strip())
        if raiz is None:
            continue
        alvo = raiz / "Avid MediaFiles"
        if alvo.is_dir() and str(alvo) not in fora:
            fora.append(str(alvo))
    return fora


def resolver(paths: list[str], mc=None,
             mob_ids: dict[str, str] | None = None,
             mob_lengths: dict[str, int] | None = None
             ) -> tuple[dict[str, str], dict[str, int], list[str]]:
    from media import opatom

    from .mcapi import mob_id_para_pyavb

    ids: dict[str, str] = {}
    duracoes: dict[str, int] = {}
    faltam: list[str] = []

    dados = mob_ids or {}
    tamanhos = mob_lengths or {}
    for p in paths:
        cru = dados.get(p) or dados.get(Path(p).name)
        if cru:
            try:
                h = mob_id_para_pyavb(cru)
            except Exception:
                continue
            ids[Path(p).name] = h
            L = tamanhos.get(p) or tamanhos.get(Path(p).name)
            if L:
                duracoes[h] = int(L)

    restantes: list[str] = []
    for p in paths:
        nome = Path(p).name
        if nome in ids:
            continue
        if Path(p).suffix.lower() == ".mxf":
            doc = opatom.probe_file(p)
            lido = opatom.parse_probe(doc, p) if doc is not None else "sem probe"
            if not isinstance(lido, str) and lido.clip_umid:
                h = mob_id_para_pyavb(lido.clip_umid)
                ids[nome] = h
                if lido.duration_frames:
                    duracoes[h] = lido.duration_frames
                continue
        restantes.append(p)

    if not restantes:
        return ids, duracoes, faltam

    if mc is None:
        return ids, duracoes, [Path(p).name for p in restantes]

    def procurar(caminhos, alvos):
        for caminho in caminhos:
            if not alvos:
                return []
            por_base: dict[str, object] = {}
            for m in mc.masters([caminho]):
                por_base.setdefault(nome_base(m.name), m)
            sobraram = []
            for q in alvos:
                m = por_base.get(nome_base(Path(q).name))
                if m is None:
                    sobraram.append(q)
                    continue
                ids[Path(q).name] = m.mob_id
                if m.length_frames:
                    duracoes[m.mob_id] = m.length_frames
            alvos = sobraram
        return alvos

    todas = mc.bins()
    abertas = set(mc.bins_abertas() or ())
    prioritarias = [b for b in todas if Path(b).stem in abertas]
    resto = [b for b in todas if b not in prioritarias]

    sobraram = procurar(prioritarias, restantes)
    sobraram = procurar(resto, sobraram)
    faltam.extend(Path(q).name for q in sobraram)
    _completar_duracoes(ids, duracoes, mc)
    return ids, duracoes, faltam


def _completar_duracoes(ids: dict[str, str], duracoes: dict[str, int], mc) -> None:
    if mc is None:
        return
    faltando = {h for h in ids.values() if h not in duracoes}
    if not faltando:
        return
    try:
        abertas = set(mc.bins_abertas() or ())
    except Exception:
        abertas = set()
    todas = mc.bins()
    ordem = ([b for b in todas if Path(b).stem in abertas]
             + [b for b in todas if Path(b).stem not in abertas])
    for caminho in ordem:
        if not faltando:
            return
        try:
            for m in mc.masters([caminho]):
                if m.mob_id in faltando and m.length_frames:
                    duracoes[m.mob_id] = m.length_frames
                    faltando.discard(m.mob_id)
        except Exception:
            continue
