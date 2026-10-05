from __future__ import annotations

_RAMOS = ("components", "tracks", "input_segments", "alternates")

PROFUNDIDADE_MAX = 12


def tipo(o) -> str:
    return type(o).__name__


def e_master(mob) -> bool:
    return str(getattr(mob, "mob_type", "")) == "MasterMob"


def e_composicao(mob) -> bool:
    return str(getattr(mob, "mob_type", "")) == "CompositionMob"


def e_subclipe(mob) -> bool:
    return (mob is not None and e_composicao(mob)
            and "subclip" in str(getattr(mob, "usage", "") or "").lower())


def filhos(componente) -> list:
    fora = []
    for atributo in _RAMOS:
        valor = getattr(componente, atributo, None)
        if valor is None or callable(valor):
            continue
        try:
            itens = list(valor)
        except TypeError:
            continue
        for item in itens:
            fora.append(getattr(item, "component", item))
    return fora


def source_clips(componente, _profundidade: int = 0, _fora: list | None = None) -> list:
    if _fora is None:
        _fora = []
    if componente is None or _profundidade > PROFUNDIDADE_MAX:
        return _fora
    if tipo(componente) == "SourceClip":
        _fora.append(componente)
        return _fora
    for filho in filhos(componente):
        source_clips(filho, _profundidade + 1, _fora)
    return _fora


def timeline(f):
    composicoes = [m for m in f.content.mobs if e_composicao(m)]
    if not composicoes:
        return None
    return max(composicoes, key=lambda c: len(list(getattr(c, "tracks", []) or [])))


def por_mob_id(f) -> dict[str, object]:
    return {str(m.mob_id): m for m in f.content.mobs}


def track_de(mob, track_id, media_kind):
    alvo = str(media_kind or "").lower()
    tracks = list(getattr(mob, "tracks", []) or [])
    for tr in tracks:
        if (getattr(tr, "index", None) == track_id
                and str(getattr(tr, "media_kind", "")).lower() == alvo):
            return tr
    for tr in tracks:
        if str(getattr(tr, "media_kind", "")).lower() == alvo:
            return tr
    return None
