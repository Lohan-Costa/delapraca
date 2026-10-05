from __future__ import annotations

import base64
import math
import re
import struct

from .projeto import Projeto, Obj


def _vertices(b64: str) -> list[tuple[bool, tuple, tuple, tuple]]:
    raw = base64.b64decode(b64)
    n = struct.unpack("<I", raw[17:21])[0]
    out = []
    for v in range(n):
        f = struct.unpack("<I6f", raw[21 + 28 * v:21 + 28 * (v + 1)])
        out.append((bool(f[0]), (f[1], f[2]), (f[3], f[4]), (f[5], f[6])))
    return out


def _pontos(proj: Projeto, prm: Obj) -> list[tuple[int | None, list]]:
    kfs = proj.campo(prm, "Keyframes")
    if kfs:
        out = []
        for k in kfs.strip(";").split(";"):
            partes = k.split(",")
            if len(partes) >= 2:
                out.append((int(partes[0]), _vertices(partes[1])))
        return out
    b64 = proj.valor_inicial(prm)
    return [(None, _vertices(b64))] if b64 else []


def ler_mascaras(proj: Projeto, componente: Obj) -> list[dict]:
    out = []
    for sub in proj.refs(componente, "SubComponent"):
        if proj.campo(sub, "MatchName") != "AE.ADBE AEMask2":
            continue
        params = proj.refs(sub, "Param")
        d: dict = {"contornos": [], "invertida": False}
        for i, prm in enumerate(params):
            nome = proj.campo(prm, "Name")
            sk = (proj.campo(prm, "StartKeyframe") or "").split(",")
            kfs = proj.campo(prm, "Keyframes")
            valor = (kfs.strip(";").split(";")[-1].split(",")[1] if kfs else (sk[1] if len(sk) > 1 else None))
            if nome == "Path":
                d["contornos"] = _pontos(proj, prm)
            elif nome in ("Position", "Anchor Point", "Rotation", "Scale Height", "Scale Width",
                          "Feather", "Opacity", "Expansion"):
                d[nome] = valor
            elif nome is None and i == 22:
                d["invertida"] = (valor or "").lower() == "true"
        if d["contornos"]:
            out.append(d)
    return out


def _transformada(vertices: list, m: dict, fonte: tuple[int, int]) -> list:
    from .leitura import _float, _ponto
    px, py = _ponto(m.get("Position"), (0.0, 0.0))
    ax, ay = _ponto(m.get("Anchor Point"), (px, py))
    sx = (_float(m.get("Scale Width"), 100.0) or 0.0) / 100.0
    sy = (_float(m.get("Scale Height"), 100.0) or 0.0) / 100.0
    rot = _float(m.get("Rotation"), 0.0) or 0.0
    if abs(px - ax) < 1e-9 and abs(py - ay) < 1e-9 and sx == sy == 1.0 and rot == 0.0:
        return vertices
    fw, fh = fonte if fonte[0] and fonte[1] else (1, 1)
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))

    def ponto(x, y):
        dx, dy = (x - ax) * fw * sx, (y - ay) * fh * sy
        return (px + (c * dx - s * dy) / fw, py + (s * dx + c * dy) / fh)
    return [(su, ponto(*v), ponto(*i), ponto(*o)) for su, v, i, o in vertices]


def para_fonte_de_dentro(vertices: list, g, quadro_take: tuple[int, int],
                         fonte: tuple[int, int]) -> list:
    qw, qh = quadro_take
    fw, fh = fonte
    c, s = math.cos(math.radians(-g.rot)), math.sin(math.radians(-g.rot))

    def ponto(x, y):
        X, Y = (x - 0.5) * qw - g.tx, (y - 0.5) * qh - g.ty
        X, Y = (c * X - s * Y) / g.esc, (s * X + c * Y) / g.esc
        return (X / fw + 0.5, Y / fh + 0.5)
    return [(su, ponto(*v), ponto(*i), ponto(*o)) for su, v, i, o in vertices]


def receita(efeito: str, mascaras: list[dict], fonte: tuple[int, int], avisos: list,
            **extra) -> dict:
    from .leitura import _float
    out = []
    for m in mascaras:
        if len(m["contornos"]) > 1:
            avisos.append("máscara animada (contorno com keyframes): usado o 1º contorno, conferir")
        verts = _transformada(m["contornos"][0][1], m, fonte)
        if (_float(m.get("Expansion"), 0.0) or 0.0) != 0.0:
            avisos.append("máscara com Expansion: não traduzido")
        if (_float(m.get("Opacity"), 100.0) or 100.0) < 100.0:
            avisos.append("máscara com Opacity < 100: não traduzido")
        out.append({"vertices": [[su, list(v), list(i), list(o)] for su, v, i, o in verts],
                    "invertida": m["invertida"],
                    "suave_px": _float(m.get("Feather"), 0.0) or 0.0})
    return {"efeito": efeito, "mascaras": out, "fonte_px": list(fonte), **extra}
