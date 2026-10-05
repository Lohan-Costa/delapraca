from __future__ import annotations

import os
import tempfile

SOFTEDGE_POR_PX = 1.0
BLUR_POR_PX = 0.2


def _f(x: float) -> str:
    t = f"{float(x):.6f}".rstrip("0").rstrip(".")
    return "0" if t in ("-0", "") else t


def cantos_basic3d(swivel: float, tilt: float, fonte_px: list) -> dict:
    import math
    s, t = math.radians(swivel), math.radians(tilt)
    w, h = float(fonte_px[0]) or 1920.0, float(fonte_px[1]) or 1080.0
    out = {}
    for nome, (x, y) in (("TopLeft", (-w / 2, -h / 2)), ("TopRight", (w / 2, -h / 2)),
                         ("BottomLeft", (-w / 2, h / 2)), ("BottomRight", (w / 2, h / 2))):
        qx = math.cos(s) * x + math.sin(s) * math.sin(t) * y
        qy = math.cos(t) * y
        qz = -math.sin(s) * x + math.cos(s) * math.sin(t) * y
        k = w / (w + qz)
        out[nome] = (qx * k / w + 0.5, 0.5 - qy * k / h)
    return out


def _pontos_polygon(vertices: list) -> list[dict]:
    out = []
    for suave, (x, y), (ix, iy), (ox, oy) in vertices:
        if suave:
            out.append({"linear": False, "x": x - 0.5, "y": 0.5 - y, "lx": ix - x, "ly": y - iy,
                        "rx": ox - x, "ry": y - oy})
        else:
            out.append({"linear": True, "x": x - 0.5, "y": 0.5 - y})
    return out


def _no_polygon(receita: dict, inverter_se_normal: bool, n: int) -> dict | None:
    m = (receita.get("mascaras") or [None])[0]
    if m is None:
        return None
    w, h = (int(receita["fonte_px"][0]) or 1920, int(receita["fonte_px"][1]) or 1080)
    return {"id": f"Polygon{n}", "tipo": "PolylineMask", "largura": w, "altura": h,
            "suave": m.get("suave_px", 0.0) * SOFTEDGE_POR_PX / w,
            "inverter": (not m["invertida"]) if inverter_se_normal else m["invertida"],
            "pontos": _pontos_polygon(m["vertices"])}


def plano(receitas: list[dict]) -> dict:
    nos, ligacoes = [], []
    atual, n_poly, n_blur, n_cp = "MediaIn1", 0, 0, 0
    for r in receitas:
        efeito = r["efeito"]
        if efeito == "recorte":
            if atual != "MediaIn1":
                raise ValueError("Crop com máscara depois de outro efeito")
            n_poly += 1
            poly = _no_polygon(r, True, n_poly)
            if poly is None:
                raise ValueError("Crop com máscara sem contorno")
            nos.append(poly)
            ligacoes.append(["MediaIn1", "EffectMask", poly["id"]])
        elif efeito == "desfoque":
            n_blur += 1
            tam = r.get("px", 0.0) * BLUR_POR_PX
            entradas = {"XBlurSize": tam}
            if r.get("direcao") in (1, 2):
                entradas = {"LockXY": 0, "XBlurSize": tam if r["direcao"] == 1 else 0.0,
                            "YBlurSize": tam if r["direcao"] == 2 else 0.0}
            blur = {"id": f"Blur{n_blur}", "tipo": "Blur", "entradas": entradas}
            nos.append(blur)
            ligacoes.append([blur["id"], "Input", atual])
            n_poly += 1
            poly = _no_polygon(r, False, n_poly)
            if poly is not None:
                nos.append(poly)
                ligacoes.append([blur["id"], "EffectMask", poly["id"]])
            atual = blur["id"]
        elif efeito == "basic3d":
            n_cp += 1
            cp = {"id": f"CornerPositioner{n_cp}", "tipo": "CornerPositioner",
                  "cantos": {k: list(v) for k, v in
                             cantos_basic3d(r["swivel"], r["tilt"], r["fonte_px"]).items()}}
            nos.append(cp)
            ligacoes.append([cp["id"], "Input", atual])
            atual = cp["id"]
        else:
            raise ValueError(f"efeito desconhecido: {efeito}")
    if atual != "MediaIn1":
        ligacoes.append(["MediaOut1", "Input", atual])
    return {"nos": nos, "ligacoes": ligacoes}


import math
import re

_TIPOS = {"PolylineMask", "Blur", "CornerPositioner"}
_ENTRADAS_BLUR = {"XBlurSize", "YBlurSize", "LockXY"}
_LIGACOES = {"Input", "EffectMask"}
_IDS = re.compile(r"^(MediaIn1|MediaOut1|(Polygon|Blur|CornerPositioner)\d{1,3})$")
_CANTOS = ("TopLeft", "TopRight", "BottomLeft", "BottomRight")


def _numero(v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError("número inválido no plano do Fusion")
    return float(v)


def validar(pl: dict) -> dict:
    nos = (pl or {}).get("nos") or []
    ids = {"MediaIn1", "MediaOut1"}
    for no in nos:
        if not isinstance(no, dict) or no.get("tipo") not in _TIPOS or not _IDS.match(str(no.get("id") or "")):
            raise ValueError("nó inválido no plano")
        ids.add(no["id"])
        if no["tipo"] == "PolylineMask":
            for k in ("largura", "altura", "suave"):
                _numero(no.get(k))
            if not no.get("pontos"):
                raise ValueError("Polygon sem pontos")
            for p_ in no["pontos"]:
                for k in (("x", "y") if p_.get("linear") else ("x", "y", "lx", "ly", "rx", "ry")):
                    _numero(p_.get(k))
        elif no["tipo"] == "CornerPositioner":
            for k in _CANTOS:
                c = (no.get("cantos") or {}).get(k)
                if not isinstance(c, (list, tuple)) or len(c) != 2:
                    raise ValueError(f"CornerPositioner sem {k}")
                _numero(c[0]), _numero(c[1])
        else:
            for k, v in (no.get("entradas") or {}).items():
                if k not in _ENTRADAS_BLUR:
                    raise ValueError(f"entrada {k} recusada")
                _numero(v)
    for l_ in (pl or {}).get("ligacoes") or []:
        if (not isinstance(l_, (list, tuple)) or len(l_) != 3 or l_[1] not in _LIGACOES
                or l_[0] not in ids or l_[2] not in ids or l_[2] == "MediaOut1"):
            raise ValueError("ligação inválida no plano")
    return pl


_SAIDA = {"PolylineMask": "Mask", "Blur": "Output", "CornerPositioner": "Output", "Loader": "Output"}


def _entradas(no: dict) -> list[str]:
    if no["tipo"] == "PolylineMask":
        pts = []
        for p in no["pontos"]:
            if p["linear"]:
                pts.append(f"{{ Linear = true, X = {_f(p['x'])}, Y = {_f(p['y'])} }}")
            else:
                pts.append(f"{{ X = {_f(p['x'])}, Y = {_f(p['y'])}, LX = {_f(p['lx'])}, "
                           f"LY = {_f(p['ly'])}, RX = {_f(p['rx'])}, RY = {_f(p['ry'])} }}")
        return (["Invert = Input { Value = 1, }"] if no["inverter"] else []) + [
            f"MaskWidth = Input {{ Value = {int(no['largura'])}, }}",
            f"MaskHeight = Input {{ Value = {int(no['altura'])}, }}",
            "UseFrameFormatSettings = Input { Value = 0, }",
            f"SoftEdge = Input {{ Value = {_f(no['suave'])}, }}",
            "Polyline = Input { Value = Polyline { Closed = true, Points = { "
            + ", ".join(pts) + " } }, }"]
    if no["tipo"] == "CornerPositioner":
        return [f"{k} = Input {{ Value = {{ {_f(no['cantos'][k][0])}, {_f(no['cantos'][k][1])} }}, }}"
                for k in ("TopLeft", "TopRight", "BottomLeft", "BottomRight")]
    if no["tipo"] == "Blur":
        return [f"{k} = Input {{ Value = {_f(v)}, }}" for k, v in (no.get("entradas") or {}).items()]
    raise ValueError(f"tipo de nó desconhecido: {no['tipo']}")


def compor(texto: str, pl: dict) -> str:
    validar(pl)
    tipos = {"MediaIn1": "Loader"} | {n["id"]: n["tipo"] for n in pl["nos"]}
    ligar: dict[str, list[str]] = {}
    for destino, entrada, origem in pl["ligacoes"]:
        ligar.setdefault(destino, []).append(
            f'{entrada} = Input {{ SourceOp = "{origem}", Source = "{_SAIDA[tipos[origem]]}", }}')
    defs = []
    for k, no in enumerate(pl["nos"]):
        corpo = ", ".join(_entradas(no) + ligar.get(no["id"], []))
        defs.append(f"{no['id']} = {no['tipo']} {{ Inputs = {{ {corpo}, }}, "
                    f"ViewInfo = OperatorInfo {{ Pos = {{ {110 * (k + 1)}, 115 }} }}, }},")
    i_out = texto.find("MediaOut1 = Saver {")
    i_in = texto.find("MediaIn1 = Loader {")
    if i_out < 0 or i_in < 0:
        raise ValueError("composição exportada sem MediaIn1/MediaOut1")
    fim_out = texto.find("ViewInfo", i_out)
    trecho = texto[i_out:fim_out]
    for linha in ligar.get("MediaOut1", []):
        origem = linha.split('"')[1]
        novo = trecho.replace('SourceOp = "MediaIn1"', f'SourceOp = "{origem}"', 1)
        if novo == trecho:
            raise ValueError("MediaOut1 sem a entrada do MediaIn1")
        trecho = novo
    texto = texto[:i_out] + trecho + texto[fim_out:]
    if ligar.get("MediaIn1"):
        j = texto.find("Inputs = {", i_in)
        if j < 0 or j > texto.find("MediaOut1 = Saver {"):
            raise ValueError("MediaIn1 sem Inputs")
        j += len("Inputs = {")
        texto = texto[:j] + "".join(f"\n\t\t\t\t{x}," for x in ligar["MediaIn1"]) + texto[j:]
    i_out = texto.find("MediaOut1 = Saver {")
    return texto[:i_out] + "\n\t\t".join(defs) + "\n\t\t" + texto[i_out:]


def setting(no: dict) -> str:
    validar({"nos": [no], "ligacoes": []})
    return ("{ Tools = ordered() { " + no["id"] + " = " + no["tipo"] + " { Inputs = { "
            + ", ".join(_entradas(no)) + ", }, }, }, }\n")


MODELO = ('Composition { Tools = { MediaIn1 = Loader { Inputs = { }, ViewInfo = OperatorInfo { }, }, '
          'MediaOut1 = Saver { Inputs = { Input = Input { SourceOp = "MediaIn1", Source = "Output", }, }, '
          'ViewInfo = OperatorInfo { }, }, }, }')


def _arquivo_comp() -> str:
    fd, arq = tempfile.mkstemp(suffix=".comp", prefix="dlpc_")
    os.close(fd)
    return arq


def _montada(item, indice: int, pl: dict) -> bool:
    arq = _arquivo_comp()
    try:
        if not item.ExportFusionComp(arq, indice):
            return False
        with open(arq, encoding="utf-8", errors="replace") as f:
            t = f.read()
        return "MEDIA_PATH" in t and all(f"{no['id']} = {no['tipo']} {{" in t for no in pl.get("nos") or [])
    except (OSError, ValueError):
        return False
    finally:
        try:
            os.remove(arq)
        except OSError:
            pass


def executar(item, pl: dict) -> dict:
    try:
        direto = compor(MODELO, pl)
    except ValueError as e:
        return {"ok": False, "erro": str(e)}
    if not (item.GetFusionCompNameList() or []):
        arq = _arquivo_comp()
        try:
            with open(arq, "w", encoding="utf-8") as f:
                f.write(direto)
            item.ImportFusionComp(arq)
        finally:
            try:
                os.remove(arq)
            except OSError:
                pass
        nomes = item.GetFusionCompNameList() or []
        if nomes and _montada(item, len(nomes), pl):
            return {"ok": True, "comp": nomes[-1]}
        if nomes and not item.DeleteFusionCompByName(nomes[-1]):
            return {"ok": False, "comp": nomes[-1],
                    "erro": "o Resolve não montou a composição — refazer à mão no Fusion"}
    item.AddFusionComp()
    nomes = item.GetFusionCompNameList() or []
    if not nomes:
        return {"ok": False, "erro": "AddFusionComp falhou"}
    nome = nomes[-1]
    item.LoadFusionCompByName(nome)
    arq = _arquivo_comp()
    try:
        if not item.ExportFusionComp(arq, len(nomes)):
            return {"ok": False, "comp": nome, "erro": "ExportFusionComp falhou"}
        with open(arq, encoding="utf-8") as f:
            texto = f.read()
        try:
            texto = compor(texto, pl)
        except ValueError as e:
            return {"ok": False, "comp": nome, "erro": str(e)}
        with open(arq, "w", encoding="utf-8") as f:
            f.write(texto)
        item.ImportFusionComp(arq)
    finally:
        try:
            os.remove(arq)
        except OSError:
            pass
    if not _montada(item, len(nomes), pl):
        try:
            item.DeleteFusionCompByName(nome)
        except Exception:
            pass
        return {"ok": False, "comp": nome, "erro": "o Resolve não montou a composição — refazer à mão no Fusion"}
    return {"ok": True, "comp": nome}


def aplicar(item, receitas) -> dict:
    if isinstance(receitas, dict):
        receitas = [receitas]
    try:
        pl = plano(receitas)
    except ValueError as e:
        return {"ok": False, "erro": str(e)}
    return executar(item, pl)


def agrupar(entradas: list[dict]) -> list[dict]:
    por: dict[tuple, dict] = {}
    for e in entradas:
        k = (e["trilha"], e["quadro"], e["arquivo"])
        g = por.setdefault(k, {"trilha": e["trilha"], "quadro": e["quadro"], "arquivo": e["arquivo"],
                               "duracao": e.get("duracao"), "segmento": e.get("segmento"),
                               "nomes": [], "receitas": []})
        g["nomes"].append(e["nome"])
        g["receitas"].append(e["receita"])
    out = []
    for g in por.values():
        try:
            g["plano"] = plano(g["receitas"])
        except ValueError as e:
            g["plano"], g["erro"] = None, str(e)
        out.append(g)
    return out


def aplicar_relatorio(timeline, relatorio: dict) -> list[dict]:
    inicio = timeline.GetStartFrame()
    out = []
    for g in relatorio.get("fusion") or []:
        base = {k: g[k] for k in ("trilha", "quadro", "arquivo")} | {"nome": " + ".join(g["nomes"])}
        if not g.get("plano"):
            out.append({**base, "ok": False, "erro": g.get("erro") or "sem plano"})
            continue
        alvo = None
        for it in timeline.GetItemListInTrack("video", g["trilha"]) or []:
            if it.GetStart() - inicio == g["quadro"] and it.GetName() == g["arquivo"]:
                alvo = it
                break
        if alvo is None:
            out.append({**base, "ok": False, "erro": "item não encontrado na timeline"})
            continue
        try:
            res = executar(alvo, g["plano"])
        except Exception as e:
            res = {"ok": False, "erro": f"{type(e).__name__}: {e}"}
        out.append({**base, **res})
    return out
