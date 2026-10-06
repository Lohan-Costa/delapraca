from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from .masters import _e_subclipe, _tipo_de_midia, chave_mob
from .parser import parse_aaf, read_url_from_descriptor

_NOME_INTERNO = re.compile(r"^msmMMOB\.\d+$", re.I)
PROFUNDIDADE_MAX = 6


def _clips(seg) -> list:
    nome = type(seg).__name__
    if nome == "SourceClip":
        return [seg]
    if nome == "Sequence":
        return [c for c in seg.components if type(c).__name__ == "SourceClip"]
    return []


def _local(url: str) -> str:
    caminho = unquote(urlparse(url).path)
    if re.match(r"^/[A-Za-z]:[/\\]", caminho):
        caminho = caminho[1:]
    return caminho


def ate_o_master(mob, slot_id, por_id: dict, quero: str, prof: int = 0):
    if mob is None or prof > PROFUNDIDADE_MAX:
        return None, None
    if type(mob).__name__ == "MasterMob":
        return mob, slot_id
    if not _e_subclipe(mob):
        return None, None
    slots = list(mob.slots)
    alvo = [s for s in slots if slot_id is not None and s.slot_id == slot_id] \
        or [s for s in slots if _tipo_de_midia(s).startswith(quero)]
    for s in alvo:
        for sc in _clips(s.segment):
            destino = por_id.get(chave_mob(sc.mob_id))
            m, sl = ate_o_master(destino, sc.slot_id, por_id, quero, prof + 1)
            if m is not None:
                return m, sl
    return None, None


def _trilhas(master):
    fora, conta = [], {}
    for s in sorted(master.slots, key=lambda s: s.slot_id):
        k = _tipo_de_midia(s)
        if k not in ("picture", "sound"):
            continue
        conta[k] = conta.get(k, 0) + 1
        fora.append((k, conta[k], s))
    return fora


def _timecode(mob):
    for s in mob.slots:
        seg = s.segment
        comps = [seg] if type(seg).__name__ == "Timecode" else \
            list(getattr(seg, "components", []) or []) if type(seg).__name__ == "Sequence" else []
        for c in comps:
            if type(c).__name__ == "Timecode" and int(c.fps or 0) > 0:
                return int(c.start or 0), int(c.fps)
    return None


def info_do_master(master, por_id: dict) -> dict:
    trilhas = _trilhas(master)
    essencias: dict[str, str] = {}
    ident: dict = {"reel": "", "tc_inicio": None, "tc_fps": None, "largura": None, "altura": None,
                   "fps": None, "audio_sr": None,
                   "audio_canais": sum(1 for k, _i, _s in trilhas if k == "sound")}
    caminho, descritor = "", ""
    for k, i, s in sorted(trilhas, key=lambda t: 0 if t[0] == "picture" else 1):
        clips = _clips(s.segment)
        if not clips:
            continue
        arquivo = por_id.get(chave_mob(clips[0].mob_id))
        if arquivo is None:
            continue
        essencias[f"{k}:{i}"] = str(arquivo.mob_id)
        desc = getattr(arquivo, "descriptor", None)
        if k == "sound" and ident["audio_sr"] is None and desc is not None:
            try:
                sr = desc["SampleRate"].value
                ident["audio_sr"] = int(float(sr)) if sr else None
            except (KeyError, TypeError, ValueError):
                pass
        if ident["reel"] or ident["tc_inicio"] is not None:
            continue
        cadeia, desloc, atual, vistos = [(master, 0), (arquivo, 0)], 0, arquivo, set()
        while len(vistos) < PROFUNDIDADE_MAX:
            vistos.add(chave_mob(atual.mob_id))
            prox_clip = next((c for sl in atual.slots for c in _clips(sl.segment)), None)
            prox = por_id.get(chave_mob(prox_clip.mob_id)) if prox_clip is not None else None
            if prox is None or chave_mob(prox.mob_id) in vistos:
                break
            desloc += int(prox_clip.start or 0)
            cadeia.append((prox, desloc))
            atual = prox
        if k == "picture" and desc is not None and ident["largura"] is None:
            try:
                ident["largura"] = int(desc["StoredWidth"].value)
                altura = int(desc["StoredHeight"].value)
                layout = desc["FrameLayout"].value if "FrameLayout" in desc.keys() else None
                ident["altura"] = altura * 2 if layout not in (None, 0, "FullFrame") else altura
            except (KeyError, TypeError, ValueError):
                pass
            try:
                ident["fps"] = round(float(desc["SampleRate"].value), 3)
            except (KeyError, TypeError, ValueError):
                pass
        if not caminho and desc is not None:
            url = read_url_from_descriptor(desc)
            if url:
                caminho, descritor = _local(url), type(desc).__name__
        for mob, d in reversed(cadeia):
            tc = _timecode(mob)
            if tc is not None:
                ident["tc_inicio"], ident["tc_fps"] = tc[0] + d, tc[1]
                break
        for mob, _d in reversed(cadeia):
            nome = (mob.name or "").strip()
            if nome and not _NOME_INTERNO.match(nome):
                ident["reel"] = nome
                break
    s1 = trilhas[0][2] if trilhas else None
    return {"nome": master.name or "", "mob_id": str(master.mob_id), "caminho_na_bin": caminho,
            "descritor": descritor,
            "fps": round(float(s1.edit_rate), 3) if s1 is not None and s1.edit_rate else None,
            "duracao_frames": int(s1.segment.length) if s1 is not None and s1.segment.length else None,
            "identidade": ident, "essencias": essencias}


def ler_timeline_aaf(caminho: str) -> dict:
    import aaf2

    r = parse_aaf(caminho)
    masters: dict[str, dict] = {}
    sem_master = 0
    with aaf2.open(str(caminho), "r") as f:
        por_id = {chave_mob(m.mob_id): m for m in f.content.mobs}
        comp = next(iter(f.content.toplevel()))
        r["sequence_mob_id"] = str(comp.mob_id)
        for s in r["segments"]:
            if s.get("is_gap") or s.get("is_transition") or not s.get("mob_id"):
                continue
            quero = "sound" if s.get("is_audio") else "picture"
            master, slot = ate_o_master(por_id.get(chave_mob(s["mob_id"])), s.get("source_slot_id"),
                                        por_id, quero)
            if master is None:
                sem_master += 1
                continue
            chave = str(master.mob_id)
            if chave not in masters:
                masters[chave] = info_do_master(master, por_id)
            s["mob_id"] = chave
            if s.get("is_audio") and slot is not None:
                canal = next((i for k, i, sl in _trilhas(master) if k == "sound" and sl.slot_id == slot), None)
                if canal:
                    s["canal_arquivo"] = canal
    r["masters"] = masters
    r["fonte"] = "aaf"
    r["avisos"] = ([{"texto": "clipes sem master no AAF (sem mídia)", "vezes": sem_master}]
                   if sem_master else [])
    return r
