from __future__ import annotations

NOME_DO_EFEITO = {
    "EFF2_SBLEND": "3D Warp", "EFF2_BLEND_RESIZE": "Resize", "EFF2_BLEND_PIP": "Picture-in-Picture",
    "INT_EFF_SPATIAL_ADAPTER": "FrameFlex", "EFF2_STABILIZE": "Stabilize",
    "EFF2_RGB_COLOR_CORRECTION": "Color Correction", "EFF2_BLEND_MATTE_KEY": "Matte Key",
    "EFF2_LUTSFX": "LUT", "EFF2_BLEND_PAN_SCAN": "Pan & Scan",
}
PARAMETRO = {
    "DVE_POS_X_U": ("posição X", ""), "DVE_POS_Y_U": ("posição Y", ""),
    "DVE_SCALE_X_U": ("escala X", "%"), "DVE_SCALE_Y_U": ("escala Y", "%"),
    "DVE_ROT_Z_U": ("rotação", "°"), "DVE_FG_KEY_OPACITY_U": ("opacidade", "%"),
    "DVE_CROP_LEFT_U": ("crop esq.", ""), "DVE_CROP_RIGHT_U": ("crop dir.", ""),
    "DVE_CROP_TOP_U": ("crop topo", ""), "DVE_CROP_BOTTOM_U": ("crop base", ""),
    "AFX_POS_X_U": ("posição X", ""), "AFX_POS_Y_U": ("posição Y", ""),
    "AFX_SCALE_X_U": ("escala X", "%"), "AFX_SCALE_Y_U": ("escala Y", "%"),
    "AFX_SPATIAL_FRAMING_WID": ("tamanho X", "%"), "AFX_SPATIAL_FRAMING_HEI": ("tamanho Y", "%"),
    "AFX_SPATIAL_FRAMING_POSX": ("posição X", ""), "AFX_SPATIAL_FRAMING_POSY": ("posição Y", ""),
}
_SEM_NOTA = ("EFF_SKIP_FOR_SIGNATURE", "Audio Pan Volume")
MIN_QUADROS = 6
MAX_PARAMETROS = 3


def _nome(eid: str, label: str = "") -> str:
    return NOME_DO_EFEITO.get(eid) or label or eid.replace("EFF2_", "").replace("EFF_", "").replace("_", " ").title()


def _num(v: float, unidade: str = "") -> str:
    t = f"{v:.1f}".rstrip("0").rstrip(".") if abs(v - round(v)) > 1e-6 else str(int(round(v)))
    return t.replace(".", ",") + unidade


def _cadeia(pontos: list, i: int, unidade: str) -> list:
    vals = [_num(v, unidade) for _q, v in pontos]
    sep = " → " if any(v < 0 for _q, v in pontos) else "-"
    if len(vals) == 2:
        return [(f"{vals[0]}{sep}{vals[1]}", False)]
    tre = [(f"{vals[i]}{sep}{vals[i + 1]} (", False)]
    if i:
        tre.append((sep.join(vals[:i]) + sep, False))
    tre.append((f"{vals[i]}{sep}{vals[i + 1]}", True))
    if i + 2 < len(vals):
        tre.append((sep + sep.join(vals[i + 2:]), False))
    tre.append((")", False))
    return tre


def _linha_animada(nome: str, unidade: str, interp: str, pontos: list, a: float) -> list:
    rotulo = f"{nome}{' · ' + interp if interp else ''}: "
    if a < pontos[0][0]:
        return [(rotulo + f"parado em {_num(pontos[0][1], unidade)}", False)]
    for i in range(len(pontos) - 1):
        if pontos[i][0] <= a < pontos[i + 1][0]:
            return [(rotulo, False)] + _cadeia(pontos, i, unidade)
    return [(rotulo + f"parado em {_num(pontos[-1][1], unidade)}", False)]


def _cortes(dur: int, pontos_por_param: list) -> list[int]:
    qs = sorted({0, dur} | {int(round(q)) for pts in pontos_por_param for q, _v in pts if 0 < q < dur})
    fora = [qs[0]]
    for q in qs[1:]:
        if q - fora[-1] >= MIN_QUADROS or q == dur:
            fora.append(q)
    if len(fora) > 2 and fora[-1] - fora[-2] < MIN_QUADROS:
        del fora[-2]
    return fora


def _estaticas(efeitos: list) -> tuple[list, str | None]:
    linhas, cor = [], None
    for e in efeitos:
        eid, cat, p = e.get("name") or "", e.get("category"), e.get("params") or {}
        if eid in _SEM_NOTA or cat in ("title", "transition", "gain") or p.get("animated"):
            continue
        nome = _nome(eid, e.get("label") or "")
        if "STABIL" in eid.upper():
            z = p.get("zoom_pct")
            linhas.append([(f"ESTABILIZADO NO AVID · {nome}" + (f" · zoom {_num(z, '%')}" if z else ""), False)])
            cor = "refazer"
        elif cat == "color":
            linhas.append([(f"COR NO AVID · {nome}", False)])
            cor = cor or "conferir"
        elif cat == "geometric" and e.get("reproducible"):
            partes = []
            if "pos_x" in p or "pos_y" in p:
                partes.append(f"posição {_num(p.get('pos_x', 0))}, {_num(p.get('pos_y', 0))}")
            if "scale_x" in p:
                sx, sy = p["scale_x"] * 100, p.get("scale_y", p["scale_x"]) * 100
                partes.append(f"escala {_num(sx, '%')}" if abs(sx - sy) < 1e-6
                              else f"escala {_num(sx, '%')} × {_num(sy, '%')}")
            if p.get("rot_z"):
                partes.append(f"rotação {_num(p['rot_z'], '°')}")
            linhas.append([(" · ".join([nome.upper()] + partes), False)])
        elif cat == "geometric":
            continue
        else:
            linhas.append([(f"EFEITO NÃO TRADUZIDO · {nome}", False)])
            cor = "refazer"
    return linhas, cor


_FORCA = ("distorcao", "refazer", "conferir")


def _mais_forte(a, b):
    return next((c for c in _FORCA if c in (a, b)), a or b)


def _proporcao(w: int, h: int) -> str:
    return _num(round(w / h, 2)) + ":1"


def _stretch(s: dict, master: dict) -> list:
    if s.get("reframe_mode") != "stretch":
        return []
    ident = (master or {}).get("identidade") or {}
    w, h = int(ident.get("largura") or 0), int(ident.get("altura") or 0)
    midia = f" · mídia {w}×{h} ({_proporcao(w, h)})" if w and h else ""
    return [[("STRETCH: POSSÍVEL DISTORÇÃO DA IMAGEM", True)],
             [(f"Reformat Stretch no Avid{midia}: conferir e avisar a produção", False)]]


def _pct_avid(tl_por_fonte: float) -> str:
    return _num(round(100 / tl_por_fonte), "%") if tl_por_fonte else "?"


def _velocidade(s: dict) -> list:
    classe = s.get("motion_class") or ""
    if classe == "freeze":
        return [[("FREEZE FRAME", False)]]
    if classe == "ramp":
        mapa = sorted((float(t), float(v)) for t, v in (s.get("source_offset_map") or []))
        vels = [(v1 - v0) / (t1 - t0) * 100 for (t0, v0), (t1, v1) in zip(mapa, mapa[1:]) if t1 > t0]
        faixa = f" · de {_num(round(min(vels)), '%')} a {_num(round(max(vels)), '%')}" if vels else ""
        return [[(f"RAMPA DE VELOCIDADE{faixa}", False)]]
    if classe == "reverse":
        return [[(f"VELOCIDADE REVERSA · {_pct_avid(float(s.get('relative_speed') or 1))}", False)]]
    if classe in ("creative", "conform+creative"):
        rel = float(s.get("relative_speed") or s.get("speed_ratio") or 1)
        jeito = "câmera lenta" if rel > 1 else "acelerado"
        return [[(f"VELOCIDADE {_pct_avid(rel)} ({jeito})", False)]]
    return []


def _tc(frames: int, fps: int) -> str:
    fps = max(1, fps)
    f = int(frames)
    return f"{f // (3600 * fps) % 24:02d}:{f // (60 * fps) % 60:02d}:{f // fps % 60:02d}:{f % fps:02d}"


def _rampa(s: dict, inicio: int, master: dict) -> list:
    mapa = sorted((float(t), float(v)) for t, v in (s.get("source_offset_map") or []))
    if len(mapa) < 2:
        return []
    ident = (master or {}).get("identidade") or {}
    tc_fps = int(ident.get("tc_fps") or round(float(s.get("source_fps") or 24)))
    base = int(ident.get("tc_inicio") or 0) + int(s.get("source_start_frames") or 0)

    def off(t: float) -> float:
        for (t0, v0), (t1, v1) in zip(mapa, mapa[1:]):
            if t0 <= t <= t1:
                return v0 + (v1 - v0) * ((t - t0) / (t1 - t0) if t1 > t0 else 0)
        return mapa[-1][1] if t > mapa[-1][0] else mapa[0][1]

    fora = []
    for q in range(int(s["duration_frames"])):
        fonte = base + int(round(off(q)))
        vel = (off(q + 1) - off(q)) * 100
        txt = f"RAMPA · fonte {_tc(fonte, tc_fps)} · {_num(vel, '%')}"
        fora.append({"inicio": inicio + q, "dur": 1, "linhas": [[(txt, False)]], "cor": None, "nome": txt,
                     "grupo": "rampa"})
    return fora


def _quadros(tc: str, fps: float) -> int:
    h, m, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
    return ((h * 60 + m) * 60 + s) * round(fps) + f


def das_notas(resultado: dict) -> list[dict]:
    fps = float(resultado.get("timeline_fps") or 24)
    masters = resultado.get("masters") or {}
    notas = []
    for i, s in enumerate(resultado.get("segments") or []):
        if not s or s.get("is_audio") or s.get("is_gap") or s.get("is_transition") or s.get("disabled"):
            continue
        inicio, dur = _quadros(s["timeline_tc_in"], fps), int(s.get("duration_frames") or 0)
        if dur <= 0:
            continue
        master = masters.get(s.get("mob_id") or "") or {}
        if s.get("motion_effect_type") == "ramp":
            notas += _rampa(s, inicio, master)
        efeitos = s.get("effects") or []
        linhas_fixas, cor = _estaticas(efeitos)
        esticado, veloz = _stretch(s, master), _velocidade(s)
        if veloz:
            linhas_fixas, cor = veloz + linhas_fixas, _mais_forte(cor, "conferir")
        if esticado:
            linhas_fixas, cor = esticado + linhas_fixas, "distorcao"
        animados = [e for e in efeitos if (e.get("params") or {}).get("animated")]
        if not animados:
            if linhas_fixas:
                notas.append(_nota(inicio, dur, linhas_fixas, cor, i))
            continue
        params = []
        for e in animados:
            p = e["params"]
            for n, pts in sorted((p.get("keyframes_avid") or {}).items()):
                if len(pts) >= 1 and n in PARAMETRO:
                    params.append((_nome(e["name"]), *PARAMETRO[n], (p.get("interp_avid") or {}).get(n, ""),
                                   sorted((float(q), float(v)) for q, v in pts)))
            cor = _mais_forte(cor, "refazer" if not e.get("reproducible") else "conferir")
        cortes = _cortes(dur, [x[4] for x in params])
        for a, b in zip(cortes, cortes[1:]):
            linhas = [[(f"{', '.join(sorted({x[0] for x in params})).upper()} ANIMADO", False)]]
            linhas += [_linha_animada(nome, uni, interp, pts, a)
                       for _ef, nome, uni, interp, pts in params[:MAX_PARAMETROS]]
            if len(params) > MAX_PARAMETROS:
                linhas.append([(f"+{len(params) - MAX_PARAMETROS} parâmetros", False)])
            notas.append(_nota(inicio + a, b - a, linhas + linhas_fixas, cor, i))
    return notas


def _nota(inicio: int, dur: int, linhas: list, cor, segmento: int | None = None) -> dict:
    return {"inicio": inicio, "dur": dur, "linhas": linhas, "cor": cor, "grupo": "efeito",
            "segmento": segmento,
            "nome": " | ".join("".join(t for t, _d in linha) for linha in linhas)}
