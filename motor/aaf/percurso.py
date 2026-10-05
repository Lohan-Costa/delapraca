from __future__ import annotations

_COLECOES = ("components", "segments", "slots", "tracks")
_PROPRIEDADES = ("InputSegments", "Alternates", "Choices")
_UNICOS = ("segment", "selected")


def componentes(raiz):
    pilha = [raiz]
    visto: set[int] = set()
    while pilha:
        atual = pilha.pop()
        if atual is None or id(atual) in visto:
            continue
        visto.add(id(atual))
        yield atual

        for nome in _COLECOES:
            filhos = getattr(atual, nome, None)
            if filhos is None:
                continue
            try:
                pilha.extend(list(filhos))
            except TypeError:
                pass

        for nome in _PROPRIEDADES:
            try:
                valor = atual.get(nome)
            except Exception:
                continue
            if valor is None:
                continue
            try:
                pilha.extend(list(valor))
            except TypeError:
                pilha.append(valor)

        for nome in _UNICOS:
            filho = getattr(atual, nome, None)
            if filho is not None:
                pilha.append(filho)

        for nome in ("Selected", "OperationGroup"):
            try:
                valor = atual[nome].value
            except Exception:
                continue
            if valor is not None:
                pilha.append(valor)


def source_clips(raiz):
    for c in componentes(raiz):
        if type(c).__name__ == "SourceClip":
            yield c


def todos_os_source_clips(f):
    for mob in list(f.content.mobs):
        for slot in mob.slots:
            yield from source_clips(slot.segment)
