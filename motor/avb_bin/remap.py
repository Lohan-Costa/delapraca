from __future__ import annotations

import logging
from collections import Counter

from aaf.masters import chave_mob
from .percurso import e_master, por_mob_id, source_clips, timeline, track_de

log = logging.getLogger("delapraca.avb_bin.remap")


def repontar(f, mapa: dict[str, object], alvo=None) -> dict:
    alvo = alvo if alvo is not None else timeline(f)
    if alvo is None:
        return {"repontados": 0, "sem_destino": 0, "masters_trocados": 0}

    por_id = por_mob_id(f)
    cont: Counter[str] = Counter()
    trocados: set[str] = set()

    for sc in source_clips(alvo):
        referenciado = por_id.get(str(sc.mob_id))
        if referenciado is None or not e_master(referenciado):
            continue
        chave = chave_mob(referenciado.mob_id)
        novo = mapa.get(chave)
        if novo is None:
            cont["sem_destino"] += 1
            continue
        sc.mob_id = novo
        trocados.add(chave)
        cont["repontados"] += 1

    resultado = {"repontados": cont["repontados"],
                 "sem_destino": cont["sem_destino"],
                 "masters_trocados": len(trocados)}
    log.info("repontado: %s", resultado)
    return resultado


def clampar(f, alvo=None) -> dict:
    alvo = alvo if alvo is not None else timeline(f)
    if alvo is None:
        return {"ajustados": 0, "irreparaveis": 0, "maior_excedente": 0}

    por_id = por_mob_id(f)
    cont: Counter[str] = Counter()
    maior = 0

    for sc in source_clips(alvo):
        mob = por_id.get(str(sc.mob_id))
        if mob is None or not e_master(mob):
            continue
        tr = track_de(mob, getattr(sc, "track_id", None), getattr(sc, "media_kind", None))
        comp = getattr(tr, "component", None) if tr is not None else None
        limite = getattr(comp, "length", None)
        if not limite:
            continue

        inicio = getattr(sc, "start_time", 0) or 0
        duracao = getattr(sc, "length", 0) or 0
        excedente = (inicio + duracao) - int(limite)
        if excedente <= 0:
            continue
        maior = max(maior, excedente)

        novo_inicio = inicio - excedente
        if novo_inicio >= 0:
            sc.start_time = novo_inicio
            cont["ajustados"] += 1
        else:
            cont["irreparaveis"] += 1
            log.warning("clipe de %d frames não cabe no master %r (%s frames)",
                        duracao, getattr(mob, "name", ""), limite)

    resultado = {"ajustados": cont["ajustados"],
                 "irreparaveis": cont["irreparaveis"],
                 "maior_excedente": maior}
    log.info("clamp: %s", resultado)
    return resultado
