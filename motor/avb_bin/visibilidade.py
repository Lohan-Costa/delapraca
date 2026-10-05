from __future__ import annotations

import logging

from .masters import classificar
from .percurso import e_composicao, e_master

log = logging.getLogger("delapraca.avb_bin.visibilidade")


def ajustar(f, timeline_mob=None, online: set[str] | None = None) -> dict:
    online = online or set()
    itens = f.content.property_data.get("items") or []
    id_timeline = str(getattr(timeline_mob, "mob_id", "")) if timeline_mob else ""
    visiveis = ocultos = 0

    for item in itens:
        mob = getattr(item, "mob", None)
        if mob is None:
            continue
        mob_id = str(getattr(mob, "mob_id", ""))

        if id_timeline and mob_id == id_timeline:
            mostrar = True
        elif not e_master(mob):
            mostrar = False
        elif mob_id in online:
            mostrar = False
        else:
            _ama, offline = classificar(mob)
            mostrar = offline is not False

        item.property_data["user_placed"] = mostrar
        visiveis += mostrar
        ocultos += not mostrar

    resultado = {"visiveis": visiveis, "ocultos": ocultos}
    log.info("visibilidade da bin: %s", resultado)
    return resultado
