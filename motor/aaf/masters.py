from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .percurso import source_clips as _source_clips

log = logging.getLogger("delapraca.aaf.masters")

_HEX = set("0123456789abcdefABCDEF")

_PICTURE = ("picture",)
_SOUND = ("sound",)


def chave_mob(mob_id) -> str:
    s = str(mob_id).strip()
    baixo = s.lower()
    if baixo.startswith("urn:smpte:umid:"):
        s = s[len("urn:smpte:umid:"):]
    elif baixo.startswith("0x"):
        s = s[2:]
    return "".join(c for c in s if c in _HEX).lower()


@dataclass
class Master:

    nome: str
    mob_id: str
    mob_id_urn: str
    e_audio: bool = False
    segmentos: int = 0
    duracao_frames: int | None = None
    edit_rate: float | None = None
    via_subclipe: bool = False
    slots: list[int] = field(default_factory=list)
    e_ama: bool | None = None
    esta_offline: bool | None = None

    def to_dict(self) -> dict:
        return {
            "nome": self.nome, "mob_id": self.mob_id, "e_audio": self.e_audio,
            "segmentos": self.segmentos, "duracao_frames": self.duracao_frames,
            "edit_rate": self.edit_rate, "via_subclipe": self.via_subclipe,
            "e_ama": self.e_ama, "esta_offline": self.esta_offline,
        }


def _e_subclipe(mob) -> bool:
    return type(mob).__name__ == "CompositionMob" and \
        "subclip" in str(getattr(mob, "usage", "") or "").lower()


def _tipo_de_midia(slot) -> str:
    return str(getattr(slot, "media_kind", "") or "").strip().lower()


def _segundos_do_slot(slot) -> float | None:
    segmento = getattr(slot, "segment", None)
    length = getattr(segmento, "length", None)
    taxa = getattr(slot, "edit_rate", None)
    if not length or not taxa:
        return None
    try:
        return float(length) / float(taxa)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _duracao_em_frames(master_mob, quero: str, edit_rate) -> int | None:
    slots = list(getattr(master_mob, "slots", []) or [])
    preferidos = [s for s in slots if _tipo_de_midia(s) == (quero or "").lower()]
    for slot in preferidos + slots:
        segundos = _segundos_do_slot(slot)
        if segundos is None:
            continue
        taxa = edit_rate or getattr(slot, "edit_rate", None)
        try:
            return int(round(segundos * float(taxa))) if taxa else None
        except (TypeError, ValueError):
            return None
    return None


def _resolver(mob, slot_id, por_id, quero: str, profundidade=0):
    if mob is None or profundidade > 4:
        return []

    if type(mob).__name__ == "MasterMob":
        return [(mob, profundidade > 0)]

    if not _e_subclipe(mob):
        return []

    def descer(slots):
        achados = []
        for slot in slots:
            for sc in _source_clips(slot.segment):
                destino = por_id.get(chave_mob(getattr(sc, "mob_id", "")))
                achados.extend(_resolver(destino, getattr(sc, "slot_id", None),
                                         por_id, quero, profundidade + 1))
        return achados

    slots = list(mob.slots)
    if slot_id is not None:
        exatos = [s for s in slots if getattr(s, "slot_id", None) == slot_id]
        if exatos:
            return descer(exatos)
    do_tipo = [s for s in slots if _tipo_de_midia(s).startswith(quero)]
    if do_tipo:
        return descer(do_tipo)
    return descer(slots)


def masters_em_uso(caminho_aaf: str, segmentos=None) -> list[Master]:
    import aaf2

    if segmentos is None:
        from .parser import parse_aaf
        segmentos = parse_aaf(caminho_aaf)["segments"]

    achados: dict[str, Master] = {}

    with aaf2.open(caminho_aaf, "r") as f:
        por_id = {chave_mob(m.mob_id): m for m in f.content.mobs}

        for seg in segmentos:
            if seg.get("is_gap") or seg.get("is_transition"):
                continue
            mob_id = seg.get("mob_id")
            if not mob_id:
                continue
            referenciado = por_id.get(chave_mob(mob_id))
            if referenciado is None:
                continue

            quero = "sound" if seg.get("is_audio") else "picture"
            for master, via_sub in _resolver(
                    referenciado, seg.get("source_slot_id"), por_id, quero):
                k = chave_mob(master.mob_id)
                m = achados.get(k)
                if m is None:
                    taxa = seg.get("source_fps")
                    m = Master(
                        nome=master.name or "",
                        mob_id=k,
                        mob_id_urn=str(master.mob_id),
                        e_audio=bool(seg.get("is_audio")),
                        edit_rate=taxa,
                        duracao_frames=_duracao_em_frames(master, quero, taxa),
                        via_subclipe=via_sub,
                    )
                    achados[k] = m
                m.segmentos += 1
                trilha = seg.get("track")
                if trilha is not None and trilha not in m.slots:
                    m.slots.append(trilha)

    return sorted(achados.values(), key=lambda m: (-m.segmentos, m.nome))
