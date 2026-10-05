from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .matcher import (
    _duration_ok,
    _has_significant_token_overlap,
    _normalize_name,
    _token_sort_ratio,
)

log = logging.getLogger("delapraca.media.matcher_masters")

OPCOES_PADRAO = {
    "score_minimo": 0.70,
    "score_confiavel": 0.90,
    "duration_tol_secs": 0.3,
    "duration_tol_rel": 0.01,
    "max_candidatos": 5,
}

FORCA = {"manual": 6, "caminho": 5, "umid": 4, "nome_exato": 3, "nome_frouxo": 2}


@dataclass
class Casamento:

    master: object
    arquivo: str | None = None
    sugestao: str | None = None
    metodo: str = "nao_resolvido"
    confianca: float = 0.0
    ambiguo: bool = False
    candidatos: list[dict] = field(default_factory=list)
    motivo: str = ""

    @property
    def resolvido(self) -> bool:
        return self.arquivo is not None

    @property
    def precisa_de_revisao(self) -> bool:
        return self.ambiguo or not self.resolvido or self.confianca < 0.9

    def to_dict(self) -> dict:
        return {
            "nome": self.master.nome,
            "mob_id": self.master.mob_id,
            "e_audio": self.master.e_audio,
            "segmentos": self.master.segmentos,
            "arquivo": self.arquivo,
            "sugestao": self.sugestao,
            "metodo": self.metodo,
            "confianca": round(self.confianca, 3),
            "ambiguo": self.ambiguo,
            "precisa_de_revisao": self.precisa_de_revisao,
            "motivo": self.motivo,
            "candidatos": self.candidatos,
        }


def _chave_uid(valor) -> str:
    if not valor:
        return ""
    s = str(valor).strip()
    baixo = s.lower()
    if baixo.startswith("urn:smpte:umid:"):
        s = s[len("urn:smpte:umid:"):]
    elif baixo.startswith("0x"):
        s = s[2:]
    hexa = "".join(c for c in s if c in "0123456789abcdefABCDEF").lower()
    return hexa if len(hexa) in (32, 64) else ""


def _duracao_do_master(master) -> float | None:
    if master.duracao_frames and master.edit_rate:
        return master.duracao_frames / master.edit_rate
    return None


def _passa_na_duracao(master, arquivo: dict, opcoes: dict):
    return _duration_ok({"media_ref": {"source_duration_secs": _duracao_do_master(master)}},
                        arquivo, opcoes)


def casar_masters(masters, indice: dict, opcoes: dict | None = None,
                  progresso=None) -> list[Casamento]:
    opcoes = {**OPCOES_PADRAO, **(opcoes or {})}
    arquivos = indice.get("files") or []

    por_caminho = {a["path"]: a for a in arquivos}
    por_uid: dict[str, list[dict]] = {}
    por_nome: dict[str, list[dict]] = {}
    normalizados: list[tuple[str, dict]] = []
    for a in arquivos:
        uid = _chave_uid(a.get("uid"))
        if uid:
            por_uid.setdefault(uid, []).append(a)
        nome = _normalize_name(a.get("filename") or "")
        if nome:
            por_nome.setdefault(nome, []).append(a)
            normalizados.append((nome, a))

    resultados: list[Casamento] = []
    total = len(masters)

    for i, master in enumerate(masters):
        if progresso:
            progresso(i, total, master.nome)
        resultados.append(_casar_um(master, opcoes, por_caminho, por_uid,
                                    por_nome, normalizados))

    if progresso:
        progresso(total, total, "")
    _suprimir_duplicatas(resultados)
    return resultados


def _casar_um(master, opcoes, por_caminho, por_uid, por_nome, normalizados) -> Casamento:
    r = Casamento(master=master)

    caminho = getattr(master, "caminho_conhecido", None)
    if caminho and caminho in por_caminho:
        r.arquivo, r.metodo, r.confianca = caminho, "caminho", 1.0
        return r

    uid = _chave_uid(master.mob_id)
    if uid:
        candidatos = por_uid.get(uid) or []
        if len(candidatos) == 1:
            r.arquivo, r.metodo, r.confianca = candidatos[0]["path"], "umid", 0.95
            return r
        if len(candidatos) > 1:
            r.ambiguo = True
            r.candidatos = [_cand(a, 0.95, "umid") for a in candidatos[:opcoes["max_candidatos"]]]
            r.motivo = f"{len(candidatos)} arquivos com o mesmo UMID"
            return r

    alvo = _normalize_name(master.nome)
    if not alvo:
        r.motivo = "o master não tem nome utilizável"
        return r

    exatos = por_nome.get(alvo) or []
    if exatos:
        return _decidir(r, [(a, 1.0) for a in exatos], "nome_exato", opcoes)

    pontuados: list[tuple[dict, float]] = []
    for nome, arquivo in normalizados:
        score = _token_sort_ratio(alvo, nome)
        if score < opcoes["score_minimo"]:
            continue
        if not _has_significant_token_overlap(alvo, nome):
            continue
        pontuados.append((arquivo, score))

    if not pontuados:
        r.motivo = "nenhum arquivo com nome parecido"
        return r

    melhor = max(s for _a, s in pontuados)
    topo = [(a, s) for a, s in pontuados if s >= melhor - 1e-9]
    return _decidir(r, topo, "nome_frouxo", opcoes, todos=pontuados)


def _decidir(r: Casamento, topo, metodo, opcoes, todos=None) -> Casamento:
    viaveis, reprovados = [], 0
    for arquivo, score in topo:
        portao = _passa_na_duracao(r.master, arquivo, opcoes)
        if portao is False:
            reprovados += 1
            continue
        viaveis.append((arquivo, score, portao))

    if len(viaveis) == 1:
        arquivo, score, portao = viaveis[0]
        r.metodo = metodo
        r.confianca = 1.0 if (metodo == "nome_exato" and portao is True) else score
        r.candidatos = [_cand(arquivo, score, metodo)]

        if metodo == "nome_frouxo":
            r.sugestao = arquivo["path"]
            r.motivo = f"sugestão por semelhança de nome ({score:.0%}) — confirme"
            return r

        r.arquivo = arquivo["path"]
        if portao is None:
            r.motivo = "sem duração para conferir"
        return r

    if len(viaveis) > 1:
        r.ambiguo = True
        r.metodo = metodo
        r.candidatos = [_cand(a, s, metodo)
                        for a, s, _p in viaveis[:opcoes["max_candidatos"]]]
        r.motivo = f"{len(viaveis)} arquivos igualmente plausíveis"
        return r

    fonte = todos or topo
    r.candidatos = [_cand(a, s, metodo) for a, s in list(fonte)[:opcoes["max_candidatos"]]]
    if reprovados:
        dur = _duracao_do_master(r.master)
        quanto = f", o master tem {dur:.1f}s" if dur else ""
        r.motivo = (f"{reprovados} arquivo(s) com o nome certo, mas duração "
                    f"incompatível{quanto} — confirme se for o certo")
    else:
        r.motivo = "nenhum candidato passou"
    return r


def _cand(arquivo: dict, score: float, metodo: str) -> dict:
    return {
        "path": arquivo["path"],
        "filename": arquivo.get("filename"),
        "score": round(float(score), 3),
        "metodo": metodo,
        "duration_ms": arquivo.get("duration_ms"),
        "frame_rate": arquivo.get("frame_rate"),
    }


def _suprimir_duplicatas(resultados: list[Casamento]) -> None:
    dono: dict[str, Casamento] = {}
    for r in resultados:
        if not r.resolvido:
            continue
        atual = dono.get(r.arquivo)
        if atual is None:
            dono[r.arquivo] = r
            continue
        if _normalize_name(r.master.nome) == _normalize_name(atual.master.nome):
            continue
        if FORCA.get(r.metodo, 0) > FORCA.get(atual.metodo, 0):
            perdedor, dono[r.arquivo] = atual, r
        else:
            perdedor = r
        vencedor = dono[r.arquivo]
        perdedor.candidatos = perdedor.candidatos or [
            _cand({"path": perdedor.arquivo, "filename": None}, perdedor.confianca,
                  perdedor.metodo)]
        perdedor.arquivo = None
        perdedor.metodo = "nao_resolvido"
        perdedor.confianca = 0.0
        perdedor.motivo = (f"esse arquivo já é de {vencedor.master.nome!r} "
                           f"(por {vencedor.metodo})")


def resumo(resultados: list[Casamento]) -> dict:
    por_metodo: dict[str, int] = {}
    for r in resultados:
        por_metodo[r.metodo] = por_metodo.get(r.metodo, 0) + 1
    return {
        "total": len(resultados),
        "resolvidos": sum(1 for r in resultados if r.resolvido),
        "sugeridos": sum(1 for r in resultados if r.sugestao and not r.resolvido),
        "ambiguos": sum(1 for r in resultados if r.ambiguo),
        "nao_resolvidos": sum(1 for r in resultados if not r.resolvido),
        "precisam_de_revisao": sum(1 for r in resultados if r.precisa_de_revisao),
        "segmentos_resolvidos": sum(r.master.segmentos for r in resultados if r.resolvido),
        "segmentos_total": sum(r.master.segmentos for r in resultados),
        "por_metodo": por_metodo,
    }
