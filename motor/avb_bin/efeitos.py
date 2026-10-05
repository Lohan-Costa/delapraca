from __future__ import annotations

from avb.parameter_uuids import PARAMETER_UUIDS

from aaf.parser import PARAMS_GEOMETRICOS, _categorize_effect, frameflex_neutro

MODOS_REFORMAT = {1: "pillarbox", 2: "center_crop", 3: "keep_size", -1: "stretch"}


INTERPOLACAO = {2: "Shelf", 3: "Linear", 5: "Bezier", 6: "Spline"}


def _interpolacao(pd: dict) -> str:
    ct = pd.get("control_track")
    k = (getattr(ct, "property_data", {}) or {}).get("interp_kind")
    return INTERPOLACAO.get(k, "") if isinstance(k, int) else ""


def _pontos(pd: dict) -> list:
    ct = pd.get("control_track")
    fora = []
    for cp in (getattr(ct, "property_data", {}) or {}).get("control_points") or []:
        d = getattr(cp, "property_data", {}) or {}
        off = d.get("offset")
        try:
            quadro = off[0] / off[1] if isinstance(off, (list, tuple)) else float(off)
            fora.append((quadro, float(d.get("value"))))
        except (TypeError, ValueError, ZeroDivisionError, IndexError):
            continue
    return sorted(fora)


def parametros(te, interp: dict | None = None) -> dict:
    fora = {}
    for p in te.property_data.get("param_list") or []:
        pd = getattr(p, "property_data", {}) or {}
        nome = PARAMETER_UUIDS.get(str(pd.get("uuid")))
        if not nome:
            continue
        v = pd.get("value")
        pontos = _pontos(pd) if pd.get("control_track") else []
        if pontos:
            v = pontos[0][1]
            if interp is not None:
                interp[nome] = _interpolacao(pd)
        if isinstance(v, (int, float)):
            fora[nome] = (float(v), pontos)
    return fora


def e_frameflex(eid: str) -> bool:
    return "SPATIAL_ADAPTER" in (eid or "").upper()


def e_pan_scan(eid: str) -> bool:
    e = (eid or "").upper().replace("_", "").replace("&", "")
    return "PANSCAN" in e or "PANANDSCAN" in e


def enquadramento(te) -> tuple[dict | None, str | None, list[str]]:
    eid = str(getattr(te, "effect_id", "") or "")
    categoria, reproduzivel = _categorize_effect(eid)
    if e_frameflex(eid):
        categoria, reproduzivel = "geometric", True
    if categoria != "geometric":
        return None, None, []
    interps: dict = {}
    ps = parametros(te, interps)
    val = lambda n: ps[n][0] if n in ps else None
    animados = sorted(n for n, (_v, pts) in ps.items() if pts)
    avisos: list[str] = []
    modo = None

    if e_frameflex(eid):
        r = val("AFX_SPATIAL_REFORMAT")
        modo = MODOS_REFORMAT.get(int(round(r))) if r is not None else None
        if val("AFX_SPATIAL_FRAMING_WID") is None:
            return None, modo, []
        if any(n.startswith(("AFX_SPATIAL_FRAMING_", "DVE_ROT_Z")) for n in animados):
            guardado = {"name": eid, "category": "geometric", "reproducible": False,
                        "params": {"animated": True,
                                   "keyframes_avid": {n: ps[n][1] for n in animados},
                                   "interp_avid": interps}}
            return guardado, modo, ["FrameFlex animado: o enquadramento NÃO foi traduzido "
                                    "(a animação fica na Carta) — ajuste no Resolve"]
        wid, hei = val("AFX_SPATIAL_FRAMING_WID"), val("AFX_SPATIAL_FRAMING_HEI")
        px, py = val("AFX_SPATIAL_FRAMING_POSX"), val("AFX_SPATIAL_FRAMING_POSY")
        rot = val("DVE_ROT_Z_U")
        if (wid or 100) == 100 and (hei or wid or 100) == 100 and not px and not py and not rot:
            return None, modo, []
        params = frameflex_neutro(wid, hei, px, py, rot)
    else:
        params = {}
        for nome, chave in PARAMS_GEOMETRICOS.items():
            v = val(nome)
            if v is not None:
                params[chave] = v / 100.0 if chave.startswith("scale") else v
        if e_pan_scan(eid):
            for k in ("pos_x", "pos_y"):
                if k in params:
                    params[k] = -params[k]

    if animados:
        params["animated"] = True
        params["keyframes_avid"] = {n: ps[n][1] for n in animados}
        params["interp_avid"] = interps
        avisos.append("efeito animado: veio o valor do primeiro quadro (a animação fica na Carta)")
    return ({"name": eid, "category": "geometric", "reproducible": reproduzivel,
             "params": params}, modo, avisos)


def informativo(te) -> dict:
    eid = str(getattr(te, "effect_id", "") or "").upper()
    if "STABIL" not in eid:
        return {}
    ps = parametros(te)
    zoom = ps.get("DVE_SCALE_X_U", (None, []))[0]
    return {"zoom_pct": round(zoom, 2) if zoom else None,
            "auto_zoom": bool(ps.get("Auto-Zoom", (0, []))[0]),
            "rastreado": bool(ps.get("DVE_POS_TRACK_U", (0, []))[0])}


def nome_do_plugin(te) -> str:
    try:
        return str(dict(te.property_data.get("attributes") or {}).get("_EFFECT_PLUGIN_NAME") or "")
    except Exception:
        return ""
