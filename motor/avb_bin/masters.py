from __future__ import annotations

import logging

from aaf.masters import Master, chave_mob
from .percurso import (e_master, e_subclipe, por_mob_id, source_clips, timeline,
                       track_de)

log = logging.getLogger("delapraca.avb_bin.masters")


def _duracao(mob) -> int | None:
    for tr in list(getattr(mob, "tracks", []) or []):
        comp = getattr(tr, "component", None)
        n = getattr(comp, "length", None)
        if n:
            return int(n)
    return None


def _atributos(mob) -> dict:
    a = getattr(mob, "attributes", None)
    try:
        return dict(a) if a else {}
    except Exception:
        return {}


def classificar(mob) -> tuple[bool | None, bool | None]:
    import os

    from avb_export import nle_model as NM

    attrs = _atributos(mob)
    e_ama = "_IMPORTED_AMA" in attrs if attrs else None

    offline: bool | None = None
    if e_ama:
        try:
            veredito = NM.online_verdict(
                NM.reachable_sources(mob, {}), mob) or {}
            caminho = veredito.get("path")
            if caminho:
                existe = veredito.get("exists")
                if existe is None:
                    existe = os.path.exists(caminho)
                offline = (not existe) or (veredito.get("mtime_ok") is False)
        except Exception:
            offline = None
    return e_ama, offline


def _edit_rate(mob) -> float | None:
    for tr in list(getattr(mob, "tracks", []) or []):
        comp = getattr(tr, "component", None)
        er = getattr(comp, "edit_rate", None)
        if er:
            return float(er)
    return None


def masters_em_uso(f, alvo=None) -> list[Master]:
    alvo = alvo if alvo is not None else timeline(f)
    if alvo is None:
        return []

    por_id = por_mob_id(f)
    achados: dict[str, Master] = {}

    def registrar(mob, quero_audio: bool, trilha) -> None:
        chave = chave_mob(mob.mob_id)
        m = achados.get(chave)
        if m is None:
            e_ama, offline = classificar(mob)
            m = Master(
                nome=mob.name or "",
                mob_id=chave,
                mob_id_urn=str(mob.mob_id),
                e_audio=quero_audio,
                duracao_frames=_duracao(mob),
                edit_rate=_edit_rate(mob),
                e_ama=e_ama,
                esta_offline=offline,
            )
            achados[chave] = m
        m.segmentos += 1
        if trilha is not None and trilha not in m.slots:
            m.slots.append(trilha)

    for sc in source_clips(alvo):
        referenciado = por_id.get(str(sc.mob_id))
        if referenciado is None:
            continue
        kind = str(getattr(sc, "media_kind", "") or "").lower()
        e_audio = kind.startswith("sound")
        trilha = getattr(sc, "track_id", None)

        if e_master(referenciado):
            registrar(referenciado, e_audio, trilha)
            continue

        if not e_subclipe(referenciado):
            continue

        tr = track_de(referenciado, trilha, getattr(sc, "media_kind", None))
        internos = source_clips(getattr(tr, "component", None)) if tr is not None else []
        if not internos:
            internos = source_clips(referenciado)
        for interno in internos:
            destino = por_id.get(str(interno.mob_id))
            if destino is not None and e_master(destino):
                registrar(destino, e_audio, trilha)
                break

    log.info("%d masters em uso na timeline %r", len(achados), getattr(alvo, "name", ""))
    return sorted(achados.values(), key=lambda m: (-m.segmentos, m.nome))


def uso_maximo(f, alvo=None) -> dict[str, int]:
    alvo = alvo if alvo is not None else timeline(f)
    if alvo is None:
        return {}
    por_id = por_mob_id(f)
    fora: dict[str, int] = {}
    for sc in source_clips(alvo):
        mob = por_id.get(str(sc.mob_id))
        if mob is None or not e_master(mob):
            continue
        fim = (getattr(sc, "start_time", 0) or 0) + (getattr(sc, "length", 0) or 0)
        chave = chave_mob(mob.mob_id)
        if fim > fora.get(chave, 0):
            fora[chave] = int(fim)
    return fora


def masters_de_arquivo(caminho_avb: str, alvo=None) -> list[Master]:
    from avb_export.reader import topen

    with topen(caminho_avb) as f:
        return masters_em_uso(f, alvo)
