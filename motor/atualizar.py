from __future__ import annotations

from dataclasses import dataclass, field

FAIXAS = (25.0, 60.0)
IGUAL, DESLOCADO, APARADO, NOVO, RETIRADO, TROCADO = (
    "igual", "deslocado", "aparado", "novo", "retirado", "trocado")


@dataclass
class Plano:
    indice: int
    trilha: tuple
    inicio: int
    dur: int
    chave: str
    fonte: int
    fonte_dur: float
    velocidade: float
    nome: str
    par: "Plano | None" = field(default=None, repr=False)
    classe: str = ""


def _quadros(tc: str, fps: float) -> int:
    h, m, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
    return ((h * 60 + m) * 60 + s) * round(fps) + f


def planos(resultado: dict, audio: bool = False, devolvido=None) -> tuple[list[Plano], int]:
    fps = float(resultado.get("timeline_fps") or 24)
    masters = resultado.get("masters") or {}
    fora, n_devolvidos = [], 0
    for i, s in enumerate(resultado.get("segments") or []):
        if not s or s.get("is_gap") or s.get("is_transition") or bool(s.get("is_audio")) != audio:
            continue
        dur = int(s.get("duration_frames") or 0)
        if dur <= 0:
            continue
        m = masters.get(s.get("mob_id") or "") or {}
        if devolvido and devolvido(s, m):
            n_devolvidos += 1
            continue
        ident = m.get("identidade") or {}
        reel = str(ident.get("reel") or "").strip()
        chave = (f"reel:{reel}" if reel else f"mob:{s['mob_id']}" if s.get("mob_id")
                 else f"nome:{s.get('clip_name') or ''}")
        vel = float(s.get("speed_ratio") or 1.0)
        fonte_dur = dur / vel if vel > 0 else 1.0
        fonte = int(ident.get("tc_inicio") or 0) + int(s.get("source_start_frames") or 0)
        if not audio and s.get("disabled"):
            continue
        fora.append(Plano(i, ("A" if audio else "V", int(s.get("track") or 1)),
                          _quadros(s["timeline_tc_in"], fps), dur, chave, fonte, fonte_dur, vel,
                          str(s.get("clip_name") or "")))
    return fora, n_devolvidos


def _sobreposicao(a: Plano, b: Plano) -> float:
    return max(0.0, min(a.fonte + a.fonte_dur, b.fonte + b.fonte_dur) - max(a.fonte, b.fonte))


def _em_ordem(bs: list[Plano], ns: list[Plano]) -> None:
    nb, nn = len(bs), len(ns)
    if not nb or not nn:
        return
    if nb * nn > 4_000_000:
        return
    peso = [[(1.0 + _sobreposicao(b, n) / 1e7) if b.chave == n.chave and _sobreposicao(b, n) > 0 else 0.0
             for n in ns] for b in bs]
    melhor = [[0.0] * (nn + 1) for _ in range(nb + 1)]
    for i in range(nb - 1, -1, -1):
        linha, abaixo, pi = melhor[i], melhor[i + 1], peso[i]
        for j in range(nn - 1, -1, -1):
            v = max(abaixo[j], linha[j + 1])
            if pi[j]:
                v = max(v, abaixo[j + 1] + pi[j])
            linha[j] = v
    i = j = 0
    while i < nb and j < nn:
        if peso[i][j] and abs(melhor[i][j] - (melhor[i + 1][j + 1] + peso[i][j])) < 1e-12 \
                and not abs(melhor[i][j] - melhor[i + 1][j]) < 1e-12:
            bs[i].par, ns[j].par = ns[j], bs[i]
            i += 1
            j += 1
        elif abs(melhor[i][j] - melhor[i + 1][j]) < 1e-12:
            i += 1
        else:
            j += 1


def _casar(base: list[Plano], nova: list[Plano]) -> None:
    trilhas = {p.trilha for p in base} | {n.trilha for n in nova}
    for t in sorted(trilhas):
        _em_ordem(sorted((p for p in base if p.trilha == t), key=lambda p: p.inicio),
                  sorted((n for n in nova if n.trilha == t), key=lambda n: n.inicio))
    base = [p for p in base if p.par is None]
    nova = [n for n in nova if n.par is None]
    por_chave: dict[str, list[Plano]] = {}
    for p in base:
        por_chave.setdefault(p.chave, []).append(p)
    candidatos = []
    for n in nova:
        for p in por_chave.get(n.chave, []):
            s = _sobreposicao(p, n)
            if s > 0:
                candidatos.append((s, p.trilha == n.trilha, -abs(p.inicio - n.inicio), id(p), p, n))
    for *_c, p, n in sorted(candidatos, key=lambda c: c[:4], reverse=True):
        if p.par is None and n.par is None:
            p.par, n.par = n, p


def _classificar(base: list[Plano], nova: list[Plano]) -> None:
    for n in nova:
        p = n.par
        if p is None:
            n.classe = NOVO
            continue
        mesmo_trecho = (p.fonte == n.fonte and abs(p.fonte_dur - n.fonte_dur) < 0.5
                        and abs(p.velocidade - n.velocidade) < 1e-6)
        if not mesmo_trecho:
            n.classe = p.classe = APARADO
        elif p.inicio == n.inicio and p.trilha == n.trilha:
            n.classe = p.classe = IGUAL
        else:
            n.classe = p.classe = DESLOCADO
    for p in base:
        if p.par is None:
            p.classe = RETIRADO
    _trocas(base, nova)


def _trocas(base: list[Plano], nova: list[Plano]) -> None:
    casados = sorted((n for n in nova if n.par is not None), key=lambda n: n.inicio)
    retirados = [p for p in base if p.classe == RETIRADO]
    for n in sorted((n for n in nova if n.classe == NOVO), key=lambda n: n.inicio):
        antes = [c for c in casados if c.trilha == n.trilha and c.inicio <= n.inicio]
        desloc = (antes[-1].inicio - antes[-1].par.inicio) if antes else 0
        ini, fim = n.inicio - desloc, n.inicio - desloc + n.dur
        for p in retirados:
            if p.par is None and p.trilha == n.trilha and min(fim, p.inicio + p.dur) - max(ini, p.inicio) > 0:
                n.classe = p.classe = TROCADO
                n.par, p.par = p, n
                break


def _mudanca(base: list[Plano], nova: list[Plano]) -> tuple[int, int]:
    mudou = sum(n.dur for n in nova if n.classe in (NOVO, TROCADO))
    mudou += sum(p.dur for p in base if p.classe == RETIRADO)
    for n in nova:
        if n.classe == APARADO:
            p = n.par
            cabeca = abs(n.fonte - p.fonte)
            rabo = abs((n.fonte + n.fonte_dur) - (p.fonte + p.fonte_dur))
            mudou += min(n.dur + p.dur, round(cabeca + rabo))
    ref = max(sum(p.dur for p in base), sum(n.dur for n in nova), 1)
    return mudou, ref


def faixa(pct: float, faixas: tuple = FAIXAS) -> str:
    return "atualizacao" if pct <= faixas[0] else "grande" if pct <= faixas[1] else "nova"


def comparar(partida: dict, nova: dict, contar_audio: bool = False, devolvido=None,
             faixas: tuple = FAIXAS) -> dict:
    b, nd_b = planos(partida, devolvido=devolvido)
    n, nd_n = planos(nova, devolvido=devolvido)
    _casar(b, n)
    _classificar(b, n)
    mudou, ref = _mudanca(b, n)
    pct = round(100.0 * mudou / ref, 1)
    contagem = {c: 0 for c in (IGUAL, DESLOCADO, APARADO, NOVO, RETIRADO, TROCADO)}
    for x in n:
        contagem[x.classe] += 1
    contagem[RETIRADO] = sum(1 for p in b if p.classe == RETIRADO)
    fora = {"contagem": contagem, "pct_imagem": pct, "faixa": faixa(pct, faixas),
            "quadros_mudados": mudou, "quadros_referencia": ref,
            "devolvidos": max(nd_b, nd_n),
            "planos": [{"classe": x.classe, "nova": x.indice, "partida": x.par.indice if x.par else None,
                        "trilha": f"{x.trilha[0]}{x.trilha[1]}", "inicio": x.inicio, "dur": x.dur,
                        "nome": x.nome} for x in sorted(n, key=lambda x: (x.inicio, x.trilha))]
                      + [{"classe": RETIRADO, "nova": None, "partida": p.indice,
                          "trilha": f"{p.trilha[0]}{p.trilha[1]}", "inicio": p.inicio, "dur": p.dur,
                          "nome": p.nome} for p in b if p.classe == RETIRADO]}
    if contar_audio:
        ba, _ = planos(partida, audio=True, devolvido=devolvido)
        na, _ = planos(nova, audio=True, devolvido=devolvido)
        _casar(ba, na)
        _classificar(ba, na)
        m_a, r_a = _mudanca(ba, na)
        fora["pct_audio"] = round(100.0 * m_a / r_a, 1)
    return fora


APAGAR, MANTER, MOVER, APARAR, CONFERIR = "apagar", "manter", "mover", "aparar", "conferir"


def _partes(base: list[Plano]) -> dict:
    fora: dict = {}
    for p in base:
        fora.setdefault(p.trilha, []).append(p)
    for v in fora.values():
        v.sort(key=lambda p: p.inicio)
    return fora


def trilhas_usadas(resultado: dict) -> dict[str, list[int]]:
    fora: dict[str, set] = {"V": set(), "A": set()}
    for s in resultado.get("segments") or []:
        if not s or s.get("is_gap") or s.get("is_transition"):
            continue
        fora["A" if s.get("is_audio") else "V"].add(int(s.get("track") or 1))
    return {k: sorted(v) for k, v in fora.items()}


def trilhas_da_carta(itens: list[dict], usadas: dict[str, list[int]]) -> list[dict]:
    fora = []
    for it in itens:
        letra, k = str(it["trilha"])[0].upper(), int(str(it["trilha"])[1:])
        lista = usadas.get(letra) or []
        t = lista[k - 1] if k <= len(lista) else 1000 + k
        fora.append({**it, "trilha_drt": it["trilha"], "trilha": f"{letra}{t}"})
    return fora


def trilha_do_drt(trilha_carta: str, usadas: dict[str, list[int]]) -> tuple[int, bool]:
    letra, t = trilha_carta[0].upper(), int(trilha_carta[1:])
    lista = usadas.get(letra) or []
    return (lista.index(t) + 1, False) if t in lista else (0, True)


def mapa_de_tempo(base: list[Plano]) -> list[tuple[int, int, int]]:
    trechos = []
    for p in base:
        if p.par is not None and p.classe in (IGUAL, DESLOCADO, APARADO):
            trechos.append((p.trilha[1], p.inicio, p.inicio + p.dur, p.par.inicio - p.inicio
                            - (p.par.fonte - p.fonte if p.classe == APARADO else 0)))
    trechos.sort()
    return [(a, b, d) for _t, a, b, d in trechos]


def _deslocamento(mapa, quadro: int):
    for a, b, d in mapa:
        if a <= quadro < b:
            return d
    return None


def quadro_novo(mapa, quadro: int) -> tuple[int, bool]:
    d = _deslocamento(mapa, quadro)
    if d is not None:
        return quadro + d, True
    depois = [(a, dd) for a, _b, dd in mapa if a > quadro]
    if depois:
        a, dd = min(depois)
        return a + dd, False
    antes = [(b, dd) for _a, b, dd in mapa if b <= quadro]
    if antes:
        b, dd = max(antes)
        return b + dd, False
    return quadro, False


ENCAIXE_MINIMO = 60.0


def encaixe(partida: dict, itens: list[dict], devolvido=None) -> dict:
    b, _ = planos(partida, devolvido=devolvido)
    cortes: dict[tuple, set] = {}
    for it in itens:
        t = str(it["trilha"]).upper()
        if not t.startswith("V"):
            continue
        ini = int(it["inicio"])
        cortes.setdefault(("V", int(t[1:])), set()).update((ini, ini + int(it["dur"])))
    no_lugar = [p for p in b if {p.inicio, p.inicio + p.dur} <= cortes.get(p.trilha, set())]
    total = sum(p.dur for p in b)
    pct = round(100.0 * sum(p.dur for p in no_lugar) / total, 1) if total else 100.0
    return {"pct": pct, "planos": len(b), "no_lugar": len(no_lugar)}


def plano_de_edicao(partida: dict, nova: dict, itens: list[dict], devolvido=None,
                    audio: bool = True) -> dict:
    b, _ = planos(partida, devolvido=devolvido)
    n, _ = planos(nova, devolvido=devolvido)
    _casar(b, n)
    _classificar(b, n)
    mapa = mapa_de_tempo(b)
    if audio:
        ba, _ = planos(partida, audio=True, devolvido=devolvido)
        na, _ = planos(nova, audio=True, devolvido=devolvido)
        _casar(ba, na)
        _classificar(ba, na)
        b, n = b + ba, n + na
    por_trilha = _partes(b)
    fora: dict = {}
    for it in itens:
        tr = ("V" if str(it["trilha"]).upper().startswith("V") else "A", int(str(it["trilha"])[1:]))
        ini, dur, fin = int(it["inicio"]), int(it["dur"]), int(it.get("fonte_in") or 0)
        dono = next((p for p in por_trilha.get(tr, []) if p.inicio <= ini and ini + dur <= p.inicio + p.dur), None)
        if dono is None:
            d = _deslocamento(mapa, ini)
            if d is None:
                fora[it["id"]] = {"acao": CONFERIR, "inicio": ini, "dur": dur, "fonte_in": fin,
                                  "motivo": "fora da montagem: o trecho embaixo dele saiu"}
            else:
                fora[it["id"]] = {"acao": MOVER if d else MANTER, "inicio": ini + d, "dur": dur,
                                  "fonte_in": fin, "motivo": "camada do colorista"}
            continue
        if dono.classe in (RETIRADO, TROCADO):
            fora[it["id"]] = {"acao": APAGAR, "inicio": ini, "dur": dur, "fonte_in": fin,
                              "motivo": "saiu da montagem" if dono.classe == RETIRADO else "take trocado"}
            continue
        novo = dono.par
        desloc = novo.inicio - dono.inicio
        if dono.classe in (IGUAL, DESLOCADO):
            fora[it["id"]] = {"acao": MOVER if desloc else MANTER, "inicio": ini + desloc, "dur": dur,
                              "fonte_in": fin, "motivo": dono.classe}
            continue
        vel = dono.velocidade if dono.velocidade > 0 else 1.0
        f0 = dono.fonte + (ini - dono.inicio) / vel
        f1 = f0 + dur / vel
        g0, g1 = max(f0, novo.fonte), min(f1, novo.fonte + novo.fonte_dur)
        if g1 <= g0:
            fora[it["id"]] = {"acao": APAGAR, "inicio": ini, "dur": dur, "fonte_in": fin,
                              "motivo": "o pedaço dele foi aparado para fora"}
            continue
        nvel = novo.velocidade if novo.velocidade > 0 else 1.0
        novo_ini = novo.inicio + round((g0 - novo.fonte) * nvel)
        nova_dur = round((g1 - g0) * nvel)
        novo_in = fin + round((g0 - f0))
        if nova_dur == dur and novo_in == fin:
            acao = MOVER if novo_ini != ini else MANTER
        else:
            acao = APARAR
        fora[it["id"]] = {"acao": acao, "inicio": novo_ini, "dur": nova_dur, "fonte_in": novo_in,
                          "motivo": APARADO}
    inserir = [{"segmento": x.indice, "trilha": f"{x.trilha[0]}{x.trilha[1]}", "inicio": x.inicio,
                "dur": x.dur, "nome": x.nome, "classe": x.classe, "fonte_offset": 0,
                "velocidade": x.velocidade}
               for x in sorted(n, key=lambda x: (x.inicio, x.trilha)) if x.classe in (NOVO, TROCADO)]
    for p in b:
        if p.classe != APARADO:
            continue
        q = p.par
        nvel = q.velocidade if q.velocidade > 0 else 1.0
        if q.fonte < p.fonte:
            inserir.append({"segmento": q.indice, "trilha": f"{q.trilha[0]}{q.trilha[1]}", "inicio": q.inicio,
                            "dur": round((p.fonte - q.fonte) * nvel), "nome": q.nome, "classe": "esticado",
                            "fonte_offset": 0, "velocidade": q.velocidade})
        if q.fonte + q.fonte_dur > p.fonte + p.fonte_dur:
            extra = round((q.fonte + q.fonte_dur - p.fonte - p.fonte_dur) * nvel)
            inserir.append({"segmento": q.indice, "trilha": f"{q.trilha[0]}{q.trilha[1]}",
                            "inicio": q.inicio + q.dur - extra, "dur": extra, "nome": q.nome, "classe": "esticado",
                            "fonte_offset": round((q.dur - extra) / nvel), "velocidade": q.velocidade})
    trans = _transicoes_por_trilha(nova)
    for x in inserir:
        _de_corte_a_corte(x, trans.get(x["trilha"]) or [])
    aparados = [{"id": k, **v} for k, v in fora.items() if v["acao"] == APARAR]
    conferir = [{"id": k, **v} for k, v in fora.items() if v["acao"] == CONFERIR]
    return {"itens": fora, "inserir": inserir, "mapa": mapa, "aparados": aparados, "conferir": conferir}


def transicoes(resultado: dict) -> list[dict]:
    fps = float(resultado.get("timeline_fps") or 24)
    fora = []
    for s in resultado.get("segments") or []:
        if not s or not s.get("is_transition"):
            continue
        ini = _quadros(s["timeline_tc_in"], fps)
        fora.append({"trilha": f"{'A' if s.get('is_audio') else 'V'}{int(s.get('track') or 1)}",
                     "inicio": ini, "dur": int(s.get("duration_frames") or 0),
                     "corte": ini + int(s.get("cutpoint") or 0), "nome": str(s.get("clip_name") or ""),
                     "efeito": next((str(e.get("name") or "") for e in s.get("effects") or []), "")})
    return fora


def _transicoes_por_trilha(resultado: dict) -> dict[str, list[dict]]:
    fora: dict[str, list[dict]] = {}
    for t in transicoes(resultado):
        fora.setdefault(t["trilha"], []).append(t)
    return fora


def _de_corte_a_corte(x: dict, trans: list[dict]) -> None:
    ini, fim = int(x["inicio"]), int(x["inicio"]) + int(x["dur"])
    for t in trans:
        c, a, b = t["corte"], t["inicio"], t["inicio"] + t["dur"]
        if not ini < c < fim:
            continue
        if fim <= b:
            fim = c
        elif ini >= a:
            vel = float(x.get("velocidade") or 1.0) or 1.0
            x["fonte_offset"] = int(x.get("fonte_offset") or 0) + round((c - ini) / vel)
            ini = c
    x["inicio"], x["dur"] = ini, max(1, fim - ini)
