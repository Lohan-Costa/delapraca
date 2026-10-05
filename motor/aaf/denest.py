from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .percurso import componentes

log = logging.getLogger("delapraca.aaf.denest")

_APPCODE_GRUPO = (4, 5)


def _primeiro_source_clip(seg):
    k = type(seg).__name__
    if k == "SourceClip":
        return seg
    if k == "Selector":
        try:
            sel = seg["Selected"].value
            if sel is not None:
                r = _primeiro_source_clip(sel)
                if r is not None:
                    return r
        except Exception:
            pass
        try:
            for alt in list(seg.get("Alternates") or []):
                r = _primeiro_source_clip(alt)
                if r is not None:
                    return r
        except Exception:
            pass
    if k == "OperationGroup":
        try:
            for ip in list(seg.get("InputSegments") or []):
                r = _primeiro_source_clip(ip)
                if r is not None:
                    return r
        except Exception:
            pass
    if hasattr(seg, "components"):
        for c in seg.components:
            r = _primeiro_source_clip(c)
            if r is not None:
                return r
    return None


def _e_subclipe(mob) -> bool:
    return (mob is not None
            and type(mob).__name__ == "CompositionMob"
            and getattr(mob, "usage", None) == "Usage_SubClip")


def _e_group_clip(selector) -> bool:
    try:
        selecionado = selector["Selected"].value
        if selecionado is None:
            return False
        sc = _primeiro_source_clip(selecionado)
        mob = getattr(sc, "mob", None) if sc is not None else None
        if mob is None or type(mob).__name__ != "CompositionMob":
            return False
        try:
            codigo = mob["AppCode"].value
        except Exception:
            codigo = getattr(mob, "application_code", None)
        return codigo in _APPCODE_GRUPO
    except Exception:
        return False


def _repontar_subclipe(sc) -> bool:
    mob = getattr(sc, "mob", None)
    if not _e_subclipe(mob):
        return False

    slot_id = getattr(sc, "slot_id", None)
    alvo = next((s for s in mob.slots if getattr(s, "slot_id", None) == slot_id), None)
    if alvo is None:
        alvo = next((s for s in mob.slots if s.media_kind == sc.media_kind), None)
    if alvo is None:
        return False

    interno = _primeiro_source_clip(alvo.segment)
    mob_interno = getattr(interno, "mob", None) if interno is not None else None
    if mob_interno is None:
        return False

    try:
        sc.mob_id = mob_interno.mob_id
        slot_interno = getattr(interno, "slot_id", None)
        if slot_interno is not None:
            sc["SourceMobSlotID"].value = slot_interno
        atual = sc["StartTime"].value if "StartTime" in sc else 0
        sc["StartTime"].value = (atual or 0) + (getattr(interno, "start", 0) or 0)
        return True
    except Exception as e:
        log.debug("falha ao achatar subclipe: %s", e)
        return False


def achatar_arquivo(entrada: str, saida: str, *, subclipes: bool = True,
                    group_clips: bool = True) -> dict:
    import aaf2

    orig, dest = Path(entrada), Path(saida)
    if orig.resolve() == dest.resolve():
        raise ValueError("entrada e saída devem ser arquivos diferentes")
    if not (subclipes or group_clips):
        raise ValueError("nada a achatar: as duas opções vieram desligadas")

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(orig, dest)
    with aaf2.open(str(dest), "rw") as f:
        contagem = achatar(f, subclipes=subclipes, group_clips=group_clips)
    contagem["saida"] = str(dest)
    return contagem


def _mobs_referenciados(f) -> set[str]:
    usados: set[str] = set()
    for mob in list(f.content.mobs):
        for slot in mob.slots:
            for comp in componentes(slot.segment):
                mid = getattr(comp, "mob_id", None)
                if mid is not None:
                    usados.add(str(mid))
    return usados

def remover_subclipes_orfaos(f) -> int:
    conjunto = f.content["Mobs"]
    removidos = 0

    for _ in range(10):
        usados = _mobs_referenciados(f)
        orfaos = [m for m in list(f.content.mobs)
                  if _e_subclipe(m) and str(m.mob_id) not in usados]
        if not orfaos:
            break
        antes = removidos
        for mob in orfaos:
            try:
                conjunto.pop(mob.mob_id)
                removidos += 1
            except Exception as e:
                log.warning("não consegui remover o subclipe %r: %s", mob.name, e)
        if removidos == antes:
            break

    log.info("removidos %d subclipe(s) órfão(s) do arquivo", removidos)
    return removidos


def achatar(f, *, subclipes: bool = True, group_clips: bool = True) -> dict:
    contagem = {"subclipes": 0, "group_clips": 0, "orfaos_removidos": 0}

    def percorrer(seg):
        k = type(seg).__name__

        if k == "SourceClip":
            if subclipes and _repontar_subclipe(seg):
                contagem["subclipes"] += 1
            return

        if k == "Sequence":
            componentes = list(seg.components)
            novos = []
            mudou = False
            for c in componentes:
                if group_clips and type(c).__name__ == "Selector" and _e_group_clip(c):
                    sc = _primeiro_source_clip(c["Selected"].value)
                    if sc is not None:
                        novo = f.create.SourceClip(media_kind=sc.media_kind)
                        novo.mob_id = sc.mob_id
                        novo["SourceMobSlotID"].value = getattr(sc, "slot_id", 1)
                        novo["StartTime"].value = getattr(sc, "start", 0) or 0
                        novo.length = c.length
                        if subclipes:
                            _repontar_subclipe(novo)
                        novos.append(novo)
                        contagem["group_clips"] += 1
                        mudou = True
                        continue
                percorrer(c)
                novos.append(c)
            if mudou:
                seg["Components"].value = novos
            return

        if k == "NestedScope":
            for s in seg.slots:
                percorrer(s.segment if hasattr(s, "segment") else s)
            return

        if k == "OperationGroup":
            try:
                for ip in list(seg.get("InputSegments") or []):
                    percorrer(ip)
            except Exception:
                pass
            return

        if k == "Selector":
            try:
                sel = seg["Selected"].value
                if sel is not None:
                    percorrer(sel)
            except Exception:
                pass
            try:
                for alt in list(seg.get("Alternates") or []):
                    percorrer(alt)
            except Exception:
                pass
            return

        if k == "Transition":
            try:
                for ip in list(seg.get("InputSegments") or []):
                    percorrer(ip)
            except Exception:
                pass
            try:
                op = seg["OperationGroup"].value
                if op is not None:
                    percorrer(op)
            except Exception:
                pass
            return

    try:
        for topo in list(f.content.toplevel()):
            for slot in topo.slots:
                percorrer(slot.segment)
    except Exception as e:
        log.warning("erro ao achatar a timeline: %s", e)

    if subclipes:
        contagem["orfaos_removidos"] = remover_subclipes_orfaos(f)

    log.info("achatado: %d subclipe(s), %d group clip(s), %d órfão(s) removido(s)",
             contagem["subclipes"], contagem["group_clips"],
             contagem["orfaos_removidos"])
    return contagem
