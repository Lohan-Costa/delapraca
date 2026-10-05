from __future__ import annotations

import copy

from aaf.parser import frames_to_tc


def _tc(tc: str | None, fps: float) -> int:
    h, m, s, f = (int(x) for x in (tc or "00:00:00:00").replace(";", ":").split(":"))
    return ((h * 60 + m) * 60 + s) * round(fps) + f


def _e_clipe(s: dict) -> bool:
    return bool(s.get("mob_id")) and not s.get("is_gap") and not s.get("is_transition")


def _trilha(track: int) -> str:
    return f"V{track}" if track < 1000 else f"A{track - 999}"


def chave(item: dict) -> tuple:
    return (int(item["track"]), item["timeline_tc_in"], item["mob_id"])


def selecao_da_copia(copia: dict, sequencia: dict) -> tuple[list[dict], list[str]]:
    fps = float(sequencia.get("timeline_fps") or 0) or 25.0
    fps_copia = float(copia.get("timeline_fps") or 0) or fps
    if abs(fps_copia - fps) > 0.001:
        return [], [f"a cópia é de outra taxa ({fps_copia:g} × {fps:g}) — não é desta sequência"]
    deslocamento = int(copia.get("timeline_start_frames") or 0) - \
        int(sequencia.get("timeline_start_frames") or 0)

    por_trilha: dict[int, list[dict]] = {}
    for s in sequencia.get("segments") or []:
        if _e_clipe(s):
            por_trilha.setdefault(int(s["track"]), []).append(s)

    itens: list[dict] = []
    avisos: list[str] = []
    vistos: set[tuple] = set()
    for p in copia.get("segments") or []:
        if not _e_clipe(p):
            continue
        track = int(p["track"])
        ini = deslocamento + _tc(p["timeline_tc_in"], fps)
        fim = deslocamento + _tc(p["timeline_tc_out"], fps)
        alvo = None
        for s in por_trilha.get(track, []):
            s_ini, s_fim = _tc(s["timeline_tc_in"], fps), _tc(s["timeline_tc_out"], fps)
            if s.get("mob_id") != p.get("mob_id") or not (s_ini <= ini and fim <= s_fim):
                continue
            if not s.get("has_motion_effect"):
                esperado = int(s.get("source_start_frames") or 0) + (ini - s_ini)
                if int(p.get("source_start_frames") or 0) != esperado:
                    continue
            alvo = (s, s_ini, s_fim)
            break
        if alvo is None:
            avisos.append(f"{_trilha(track)} {frames_to_tc(ini, fps)}: "
                          f"“{p.get('clip_name') or '?'}” não está nesta sequência")
            continue
        s, s_ini, s_fim = alvo
        item = {"track": track,
                "timeline_tc_in": frames_to_tc(ini, fps),
                "timeline_tc_out": frames_to_tc(fim, fps),
                "mob_id": s["mob_id"],
                "clip_name": s.get("clip_name") or "",
                "inteiro": ini == s_ini and fim == s_fim}
        if chave(item) not in vistos:
            vistos.add(chave(item))
            itens.append(item)
    itens.sort(key=lambda i: (i["track"], _tc(i["timeline_tc_in"], fps)))
    return itens, avisos


def trilhas_da_selecao(itens: list[dict]) -> list[str]:
    return [_trilha(t) for t in sorted({int(i["track"]) for i in itens})]


def impressao_digital(itens: list[dict]) -> tuple:
    return tuple(sorted((chave(i), i["timeline_tc_out"]) for i in itens))


def _vazio(s: dict) -> dict:
    return {**s, "mob_id": None, "clip_name": "(gap)", "media_ref": None, "is_gap": True,
            "is_transition": False, "is_effect": False, "has_motion_effect": False,
            "effects": [], "source_start_frames": 0, "cutpoint": None}


def _aparar(s: dict, ini: int, fim: int, fps: float) -> dict:
    s_ini = _tc(s["timeline_tc_in"], fps)
    avanco = ini - s_ini
    razao = (float(s.get("source_fps") or 0) or fps) / fps
    return {**s,
            "timeline_tc_in": frames_to_tc(ini, fps),
            "timeline_tc_out": frames_to_tc(fim, fps),
            "duration_frames": fim - ini,
            "source_start_frames": int(s.get("source_start_frames") or 0) + round(avanco * razao)}


def filtrar_insert(resultado: dict, selecao: list[dict]) -> dict:
    fps = float(resultado.get("timeline_fps") or 0) or 25.0
    fora = copy.deepcopy(resultado)
    segs = fora.get("segments") or []
    avisos: list[str] = []

    faixas: dict[tuple, list[tuple[int, int]]] = {}
    for it in selecao:
        faixas.setdefault((int(it["track"]), it["mob_id"]), []).append(
            (_tc(it["timeline_tc_in"], fps), _tc(it["timeline_tc_out"], fps)))
    escolhido: dict[int, tuple[int, int]] = {}
    for i, s in enumerate(segs):
        if not _e_clipe(s):
            continue
        s_ini, s_fim = _tc(s["timeline_tc_in"], fps), _tc(s["timeline_tc_out"], fps)
        dentro = [(max(a, s_ini), min(b, s_fim))
                  for a, b in faixas.get((int(s["track"]), s["mob_id"]), []) if a < s_fim and b > s_ini]
        if dentro:
            escolhido[i] = (min(a for a, _ in dentro), max(b for _, b in dentro))

    cortes: dict[int, dict] = {}
    remover: set[int] = set()
    for i, s in enumerate(segs):
        if not s.get("is_transition"):
            continue
        t_ini, t_fim = _tc(s["timeline_tc_in"], fps), _tc(s["timeline_tc_out"], fps)
        corte = t_ini + int(s.get("cutpoint") if s.get("cutpoint") is not None
                            else (t_fim - t_ini) // 2)
        mesma = [(j, x) for j, x in enumerate(segs) if j != i and int(x.get("track") or 0)
                 == int(s.get("track") or 0) and _e_clipe(x)]
        a = next((j for j, x in mesma if _tc(x["timeline_tc_out"], fps) == t_fim), None)
        b = next((j for j, x in mesma if _tc(x["timeline_tc_in"], fps) == t_ini), None)

        def inteiro(j):
            return j is not None and j in escolhido and escolhido[j] == (
                _tc(segs[j]["timeline_tc_in"], fps), _tc(segs[j]["timeline_tc_out"], fps))

        algum = (a is not None and a in escolhido) or (b is not None and b in escolhido)
        if algum and (a is None or inteiro(a)) and (b is None or inteiro(b)):
            continue
        remover.add(i)
        if not algum:
            continue
        remover.add(i)
        if a is not None and a in escolhido:
            cortes.setdefault(a, {})["fim"] = corte
        if b is not None and b in escolhido:
            cortes.setdefault(b, {})["ini"] = corte

    novos = []
    for i, s in enumerate(segs):
        if i in remover:
            continue
        if not _e_clipe(s):
            novos.append(s)
            continue
        if i not in escolhido:
            novos.append(_vazio(s))
            continue
        s_ini, s_fim = _tc(s["timeline_tc_in"], fps), _tc(s["timeline_tc_out"], fps)
        ini, fim = escolhido[i]
        ini = max(ini, cortes.get(i, {}).get("ini", ini))
        fim = min(fim, cortes.get(i, {}).get("fim", fim))
        if (ini, fim) == (s_ini, s_fim):
            novos.append(s)
        elif s.get("has_motion_effect"):
            avisos.append(f"{_trilha(int(s['track']))} {s['timeline_tc_in']} “{s.get('clip_name')}”: "
                          "tem velocidade — foi inteiro, sem aparar ao corte/trecho")
            novos.append(s)
        else:
            novos.append(_aparar(s, ini, fim, fps))

    fora["segments"] = novos
    fora["total_segments"] = len(novos)
    fora["avisos"] = list(fora.get("avisos") or []) + [{"texto": t, "vezes": 1} for t in avisos]
    return fora
