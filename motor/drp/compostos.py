from __future__ import annotations


def _achar(timeline, inicio: int, c: dict):
    for it in timeline.GetItemListInTrack("video", int(c["trilha"])) or []:
        if it.GetStart() - inicio == int(c["quadro"]) and it.GetName() == c["arquivo"]:
            return it
    return None


def aplicar_compostos(timeline, relatorio: dict) -> list[dict]:
    grupos = relatorio.get("compostos") or []
    if grupos and not callable(getattr(timeline, "CreateCompoundClip", None)):
        return [{"nome": g["nome"], "ok": False,
                 "erro": "este Resolve não cria Compound Clip pela API — o nest ficou em trilhas"}
                for g in grupos]
    inicio = timeline.GetStartFrame()
    out = []
    for g in grupos:
        base = {"nome": g["nome"], "clipes": len(g["clipes"])}
        itens = [_achar(timeline, inicio, c) for c in g["clipes"]]
        faltam = sum(1 for x in itens if x is None)
        if faltam or not itens:
            out.append({**base, "ok": False,
                        "erro": f"{faltam} de {len(itens)} clipes não encontrados — o nest ficou em trilhas"})
            continue
        try:
            cc = timeline.CreateCompoundClip(itens, {"name": g["nome"]})
        except Exception as e:
            cc, erro = None, f"{type(e).__name__}: {e}"
        else:
            erro = "o Resolve não criou o Compound Clip — o nest ficou em trilhas"
        out.append({**base, "ok": True} if cc else {**base, "ok": False, "erro": erro})
    return out
