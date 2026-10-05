from __future__ import annotations

import logging
from collections import Counter

from .percurso import e_subclipe, por_mob_id, source_clips, timeline, track_de

log = logging.getLogger("delapraca.avb_bin.denest")


def achatar(f, alvo=None) -> dict:
    alvo = alvo if alvo is not None else timeline(f)
    if alvo is None:
        return {"achatados": 0, "sem_track": 0, "sem_source_clip": 0,
                "subclipes_restantes": 0}

    por_id = por_mob_id(f)
    cont: Counter[str] = Counter()

    for sc in source_clips(alvo):
        sub = por_id.get(str(sc.mob_id))
        if not e_subclipe(sub):
            continue

        tr = track_de(sub, getattr(sc, "track_id", None), getattr(sc, "media_kind", None))
        if tr is None:
            cont["sem_track"] += 1
            continue
        internos = source_clips(getattr(tr, "component", None))
        if not internos:
            cont["sem_source_clip"] += 1
            continue

        interno = internos[0]
        sc.mob_id = interno.mob_id
        track_interna = getattr(interno, "track_id", None)
        if track_interna is not None:
            sc.track_id = track_interna
        sc.start_time = ((getattr(sc, "start_time", 0) or 0)
                         + (getattr(interno, "start_time", 0) or 0))
        cont["achatados"] += 1

    restantes = sum(1 for s in source_clips(alvo)
                    if e_subclipe(por_id.get(str(s.mob_id))))
    resultado = {
        "achatados": cont["achatados"],
        "sem_track": cont["sem_track"],
        "sem_source_clip": cont["sem_source_clip"],
        "subclipes_restantes": restantes,
    }
    log.info("achatado: %s", resultado)
    return resultado
