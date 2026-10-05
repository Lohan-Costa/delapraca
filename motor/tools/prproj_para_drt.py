from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drp.fusion_clipe import agrupar
from drp.writer import _media_meta, build_drt
from media.sonda import taxa_variavel
from prproj.leitura import Leitor, frames_para_tc
from prproj.originais import casar, sondar
from prproj.projeto import Projeto
from tools.conferir_conform import SEGUNDOS_POR_PONTO, pontos_visiveis


def _tc_frames(tc: str, n: int) -> int:
    h, m, s, f = (int(x) for x in tc.split(":"))
    return ((h * 60 + m) * 60 + s) * n + f


_MOTIVO = {"incompleto": "o original está vazio, ilegível ou truncado: confira o arquivo",
           "ambiguo": "mais de um original possível: escolher",
           "lento": "o disco não respondeu a tempo ao ler o original (lento ou com defeito?): "
                    "confira o disco e importe de novo",
           "sem_original": "original não encontrado nas pastas (se este arquivo já é o original, "
                           "ignore)"}


def analisar(prproj: str, sequencia: str, originais: list[str], progresso=None,
             confiavel=None) -> dict:
    leitura = Leitor(Projeto(prproj)).ler(sequencia)
    if confiavel is not None:
        for s in leitura["segmentos"]:
            ref = s.get("media_ref")
            if ref and ref.get("local_path"):
                seguro, _motivo = confiavel(ref["local_path"])
                ref["local_path"] = seguro or ""
    casamento = casar([x for x in leitura["segmentos"] if not x.get("referencia")], originais,
                      progresso)
    resumo = resumir(leitura["segmentos"], casamento)
    n = len(pontos_visiveis(leitura["segmentos"], round(leitura["fps"])))
    resumo["conferencia"] = {"pontos": n, "segundos": round(n * SEGUNDOS_POR_PONTO)}
    return {"leitura": leitura, "casamento": casamento, "resumo": resumo}


def _resumo_audio(segs: list[dict]) -> dict:
    clipes = [s for s in segs if s.get("is_audio") and not (s.get("is_gap") or s.get("is_transition"))]
    efeitos: dict[str, int] = {}
    for s in clipes:
        for e in s.get("effects") or []:
            if e["name"] not in ("Volume", "Channel Volume", "Panner"):
                efeitos[e["name"]] = efeitos.get(e["name"], 0) + 1
    return {"clipes": len(clipes), "trilhas": len({s["track"] for s in clipes}),
            "transicoes": sum(1 for s in segs if s.get("is_audio") and s.get("is_transition")),
            "desligados": sum(1 for s in clipes if s.get("disabled")),
            "volume": sum(1 for s in clipes if any(e["name"] in ("Volume", "Channel Volume")
                                                    for e in s.get("effects") or [])),
            "efeitos": dict(sorted(efeitos.items(), key=lambda kv: -kv[1]))}


def resumir(segs: list[dict], casamento: dict) -> dict:
    video = [s for s in segs if not (s.get("is_gap") or s.get("is_transition") or s.get("is_audio")
                                     or s.get("referencia"))]
    planos = [s for s in video if not s.get("is_effect")]
    ref = next((s for s in segs if s.get("referencia") and not s.get("is_audio")), None)
    no_original = sum(1 for s in planos
                      if (casamento.get(s["media_ref"]["local_path"]) or {}).get("estado")
                      in ("original", "proprio"))
    estados: dict[str, int] = {}
    for c in casamento.values():
        estados[c["estado"]] = estados.get(c["estado"], 0) + 1
    conta = {"velocidade": 0, "keyframes": 0, "crop_flip": 0, "fusion": 0, "luma_key": 0,
             "graficos": sum(1 for s in video if s.get("is_effect")),
             "nests": len({s["composto"]["id"] for s in video if s.get("composto")})}
    sem_traducao: dict[str, int] = {}
    for s in planos:
        p = ((s.get("effects") or [{}])[0] or {}).get("params") or {}
        conta["velocidade"] += bool(s.get("has_motion_effect"))
        conta["keyframes"] += bool(p.get("kf"))
        conta["crop_flip"] += bool(p.get("crop_frac") or p.get("flip_x") or p.get("flip_y"))
        conta["luma_key"] += p.get("composite") == "screen"
        conta["fusion"] += any(e.get("category") == "fusion" for e in s.get("effects") or [])
        for e in s.get("effects") or []:
            if not e.get("reproducible"):
                sem_traducao[e["name"]] = sem_traducao.get(e["name"], 0) + 1
    faltam = sorted(os.path.basename(p) or "(caminho recusado)" for p, c in casamento.items()
                    if c["estado"] not in ("original", "proprio"))
    return {"planos": len(planos), "no_original": no_original, "arquivos": len(casamento),
            "casamento": estados, "faltam": faltam, "efeitos": conta,
            "sem_traducao": dict(sorted(sem_traducao.items(), key=lambda kv: -kv[1])),
            "audio": _resumo_audio([s for s in segs if not s.get("referencia")]),
            "referencia": {"arquivo": ref["clip_name"], "trilha": ref["track"],
                           "com_som": any(s.get("is_audio") and s.get("referencia") for s in segs)}
                          if ref else None}


def montar(prproj: str, sequencia: str, saida: str, originais: list[str],
           referencia: str | None = None, analise: dict | None = None, marcar: bool = False,
           colorir: bool = False, codigo_de_cores: dict | None = None, audio: bool = True,
           som_da_referencia: str | None = "A", compostos: bool = True,
           andamento=None) -> dict:
    import copy

    analise = analise or analisar(prproj, sequencia, originais)
    leitura, casamento = analise["leitura"], analise["casamento"]
    segs = copy.deepcopy(leitura["segmentos"])
    fps = leitura["fps"]
    nominal = round(fps)
    segs = _referencia(segs, referencia, som_da_referencia, fps, nominal)
    if not audio and som_da_referencia != "B":
        segs = [x for x in segs if not x.get("is_audio")]

    match_results, marcas, fusion = [], [], []
    audio_sem_arquivo = 0
    for i, s in enumerate(segs):
        if s.get("is_gap") or s.get("is_transition"):
            continue
        if s.get("referencia"):
            caminho = s["media_ref"]["local_path"]
            if caminho and os.path.isfile(caminho):
                s["arquivo"] = caminho
                match_results.append({"segment_index": i, "matched_path": caminho})
            continue
        if s.get("is_audio"):
            proxy = s["media_ref"]["local_path"]
            c = casamento.get(proxy) or {}
            caminho = c["original"] if c.get("estado") == "original" else proxy
            if not caminho or not os.path.isfile(caminho):
                audio_sem_arquivo += 1
                continue
            s["arquivo"] = caminho
            match_results.append({"segment_index": i, "matched_path": caminho})
            continue
        ini = _tc_frames(s["timeline_tc_in"], nominal)
        if s.get("is_effect"):
            titulo = any(e.get("category") == "title" for e in s.get("effects") or [])
            nota = ("título do Premiere recriado como Text: conferir cor, corpo e posição"
                    if titulo else "gráfico do Premiere sem tradução: refazer")
            marcas.append({"quadro": ini, "cor": "Yellow", "nota": nota, "segmento": i,
                           "tipo": "Título recriado" if titulo else "Gráfico sem tradução",
                           "trilha": s["track"], "duracao": s["duration_frames"]})
            continue
        proxy = s["media_ref"]["local_path"]
        c = casamento.get(proxy) or {}
        if c.get("estado") in ("original", "proprio"):
            caminho = c["original"]
        elif os.path.exists(proxy):
            caminho = proxy
            motivo = _MOTIVO.get(c.get("estado"), "sem original")
            marcas.append({"quadro": ini, "cor": "Red", "nota": f"REFAZER no original: {motivo}",
                           "tipo": "Sem o original",
                           "trilha": s["track"], "duracao": s["duration_frames"], "segmento": i,
                           "arquivo": os.path.basename(proxy)})
        else:
            marcas.append({"quadro": ini, "cor": "Red", "nota": "arquivo não encontrado (nem proxy)",
                           "tipo": "Arquivo não encontrado",
                           "trilha": s["track"], "duracao": s["duration_frames"], "segmento": i,
                           "arquivo": os.path.basename(proxy)})
            continue
        s["arquivo"] = caminho
        match_results.append({"segment_index": i, "matched_path": caminho})
        variavel = taxa_variavel(caminho) if os.path.isfile(caminho) else None
        if variavel:
            marcas.append({"quadro": ini, "cor": "Yellow", "trilha": s["track"],
                           "duracao": s["duration_frames"], "segmento": i, "tipo": "Taxa variável",
                           "arquivo": os.path.basename(caminho),
                           "nota": f"conferir o tempo: o arquivo tem taxa variável ({variavel[0]:.3g} q/s no "
                                   f"cabeçalho, {variavel[1]:.3g} de média) e o Resolve pode mostrar outro quadro"})
        if s.get("motion_effect_type") == "reverse":
            nb, fps_m, _tc = _media_meta(caminho)
            if not (nb and fps_m):
                marcas.append({"quadro": ini, "cor": "Red", "trilha": s["track"],
                               "duracao": s["duration_frames"], "segmento": i,
                               "tipo": "Reverso sem duração",
                               "nota": "REFAZER o reverso: o arquivo não informou a duração, o "
                                       "plano entrou tocando para frente"})
        for e in s.get("effects", []):
            if e.get("category") == "fusion":
                fusion.append({"quadro": ini, "trilha": s["track"], "duracao": s["duration_frames"],
                               "arquivo": os.path.basename(caminho), "nome": e["name"],
                               "receita": e["params"], "segmento": i})
                marcas.append({"quadro": ini, "cor": "Yellow", "segmento": i,
                               "tipo": "Efeito no Fusion",
                               "nota": f"conferir: {e['name']}", "trilha": s["track"],
                               "duracao": s["duration_frames"]})
        nao = [e["name"] for e in s.get("effects", []) if not e.get("reproducible")]
        if nao:
            marcas.append({"quadro": ini, "cor": "Yellow", "nota": "conferir: " + ", ".join(nao),
                           "tipo": "Efeito sem tradução",
                           "trilha": s["track"], "duracao": s["duration_frames"], "segmento": i})

    planos = agrupar(fusion)
    for g in planos:
        if not g.get("plano"):
            marcas.append({"quadro": g["quadro"], "cor": "Red", "trilha": g["trilha"],
                           "duracao": g.get("duracao") or 1, "segmento": g.get("segmento"),
                           "tipo": "Refazer no Fusion",
                           "nota": f"REFAZER à mão no Fusion: {' + '.join(g['nomes'])} ({g['erro']})"})
    grupos = _compostos(segs, match_results, nominal) if compostos else []
    res = build_drt(segs, match_results, saida, timeline_name=leitura["nome"], timeline_fps=fps,
                    timeline_size=(leitura["largura"], leitura["altura"]),
                    timeline_start_frames=leitura.get("inicio", 0),
                    sinais=_sinais_das_marcas(marcas), marcar=marcar, colorir=colorir,
                    codigo_de_cores=codigo_de_cores, andamento=andamento)
    relatorio = {
        "prproj": prproj, "premiere": leitura["premiere"], "sequencia": leitura["nome"],
        "sequencia_uid": sequencia if sequencia != leitura["nome"] else None,
        "fps": fps, "quadro": [leitura["largura"], leitura["altura"]],
        "planos": sum(1 for s in segs if not (s.get("is_gap") or s.get("is_transition"))),
        "arquivos": len(casamento), "casamento": analise["resumo"]["casamento"],
        "resumo": analise["resumo"],
        "audio": {"trazido": audio, "sem_arquivo": audio_sem_arquivo},
        "trilha_referencia": next((x["track"] for x in segs if x.get("referencia")
                                   and not x.get("is_audio")), None),
        "avisos": leitura["avisos"], "marcadores": marcas,
        "fusion": planos,
        "compostos": grupos,
        "conferencia": _conferencia(segs, nominal, fps, leitura),
        "pendentes": {p: {k: c[k] for k in ("estado", "candidatos", "recusados")}
                      for p, c in casamento.items() if c["estado"] not in ("original", "proprio")},
        "drt": res,
    }
    with open(os.path.splitext(saida)[0] + ".relatorio.json", "w", encoding="utf-8") as f:
        json.dump(relatorio, f, ensure_ascii=False, indent=1, default=str)
    return relatorio


def _conferencia(segs: list[dict], nominal: int, fps: float, leitura: dict) -> dict | None:
    ref = next((x for x in segs if x.get("referencia") and not x.get("is_audio")
                and (x.get("media_ref") or {}).get("local_path")), None)
    if ref is None:
        return None
    return {"referencia": ref["media_ref"]["local_path"], "trilha_referencia": ref["track"],
            "ref_na_timeline": _tc_frames(ref["timeline_tc_in"], nominal),
            "ref_entrada": int(ref.get("source_start_frames") or 0),
            "ref_fps": float(ref.get("source_fps") or fps), "fps": fps, "nominal": nominal,
            "inicio": int(leitura.get("inicio") or 0),
            "quadro_px": [leitura["largura"], leitura["altura"]],
            "pontos": pontos_visiveis(segs, nominal)}


def _compostos(segs: list[dict], match_results: list[dict], nominal: int) -> list[dict]:
    na_timeline = {m["segment_index"]: m["matched_path"] for m in match_results}
    grupos: dict[str, dict] = {}
    for i, s in enumerate(segs):
        c = s.get("composto")
        if not c or s.get("is_audio") or i not in na_timeline:
            continue
        g = grupos.setdefault(c["id"], {"nome": c["nome"], "clipes": []})
        g["clipes"].append({"trilha": s["track"],
                            "quadro": _tc_frames(s["timeline_tc_in"], nominal),
                            "arquivo": os.path.basename(na_timeline[i])})
    return [g for g in grupos.values() if g["clipes"]]


def _referencia(segs: list[dict], arquivo: str | None, som: str | None, fps: float,
                nominal: int) -> list[dict]:
    if arquivo:
        for x in segs:
            x.pop("referencia", None)
            x.pop("trilha_desligada", None)
        r = sondar(arquivo)
        n = round((r.get("dur_s") or 0) * (r.get("fps") or fps))
        topo = max((x["track"] for x in segs if not x.get("is_audio")), default=0) + 1
        nome = os.path.basename(arquivo)
        segs.append({"timeline_tc_in": frames_para_tc(0, nominal),
                     "timeline_tc_out": frames_para_tc(n, nominal), "duration_frames": n,
                     "source_start_frames": 0, "source_fps": r.get("fps") or fps,
                     "clip_name": nome, "track": topo, "referencia": True, "trilha_desligada": True,
                     "media_ref": {"mob_name": nome, "url": "", "local_path": arquivo,
                                   "descriptor_type": "referencia", "source_duration_frames": n,
                                   "source_fps": r.get("fps") or fps},
                     "is_gap": False, "is_transition": False, "is_effect": False,
                     "is_audio": False, "effects": [], "reframe_mode": "pillarbox",
                     "speed_ratio": 1.0, "relative_speed": 1.0, "conform_ratio": 1.0})
        from drp.writer import _audio_meta
        canais = (_audio_meta(arquivo)[3] or 0) if som else 0
        for k in range(min(canais, 2)):
            segs.append({"timeline_tc_in": frames_para_tc(0, nominal),
                         "timeline_tc_out": frames_para_tc(n, nominal), "duration_frames": n,
                         "source_start_frames": 0, "source_fps": fps, "clip_name": nome,
                         "track": 0, "canal_arquivo": k + 1, "referencia": True,
                         "media_ref": {"mob_name": nome, "url": "", "local_path": arquivo,
                                       "descriptor_type": "referencia"},
                         "is_gap": False, "is_transition": False, "is_effect": False,
                         "is_audio": True, "effects": [], "speed_ratio": 1.0,
                         "relative_speed": 1.0, "conform_ratio": 1.0})
    som_ref = [x for x in segs if x.get("is_audio") and x.get("referencia")
               and not (x.get("is_gap") or x.get("is_transition"))]
    resto = [x for x in segs if not (x.get("is_audio") and x.get("referencia"))]
    if som is None or not som_ref:
        return resto
    if som == "B":
        resto = [x for x in resto if not x.get("is_audio")]
        base = 1000
    else:
        base = max((x["track"] for x in resto if x.get("is_audio")), default=999) + 1
    canais = sorted({x.get("canal_arquivo") or 1 for x in som_ref})
    for x in som_ref:
        x["track"] = base + canais.index(x.get("canal_arquivo") or 1)
        x["solo"] = som == "A"
    return resto + som_ref


def trilha_rotulo(trilha, audio: bool = False) -> str:
    return f"Trk {'A' if audio else 'V'}{trilha}"


def _sinais_das_marcas(marcas: list[dict]) -> list[dict]:
    por: dict[int, dict] = {}
    for m in marcas:
        if m.get("segmento") is None:
            continue
        n = por.setdefault(m["segmento"], {"inicio": m["quadro"], "dur": m.get("duracao") or 1,
                                           "segmento": m["segmento"], "grupo": "efeito",
                                           "cor": "conferir", "linhas": [], "tipos": []})
        if m["cor"] == "Red":
            n["cor"] = "refazer"
        forte = m["cor"] == "Red"
        n["tipos"].append((not forte, m.get("tipo") or ("Refazer" if forte else "Conferir")))
        n["linhas"].append([(f"{trilha_rotulo(m['trilha'])} · {m['nota']}", None)])
    for n in por.values():
        n["linhas"].sort(key=lambda linha: not linha[0][0].split(" · ", 1)[-1].startswith(("REFAZER", "arquivo")))
        n["tipo"] = sorted(n.pop("tipos"))[0][1]
        n["nome"] = " | ".join(linha[0][0] for linha in n["linhas"])
    return list(por.values())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Uma sequência do Premiere → DRT do Resolve religado aos ORIGINAIS de câmera.")
    ap.add_argument("prproj")
    ap.add_argument("sequencia")
    ap.add_argument("saida")
    ap.add_argument("--originais", action="append", default=[])
    ap.add_argument("--referencia")
    ap.add_argument("--sem-audio", action="store_true", help="só a imagem")
    ap.add_argument("--som-da-referencia", choices=["A", "B", "nenhum"], default="A",
                    help="A = trilha nova abaixo de tudo, com solo; B = só o som da referência")
    ap.add_argument("--sinais", action="store_true",
                    help="marcador de timeline + cor local nos planos a refazer/conferir")
    ap.add_argument("--nest-aberto", action="store_true",
                    help="não pedir Compound Clip: o nest fica aberto em trilhas")
    a = ap.parse_args(argv)
    r = montar(a.prproj, a.sequencia, a.saida, a.originais, a.referencia,
               marcar=a.sinais, colorir=a.sinais, audio=not a.sem_audio,
               som_da_referencia=None if a.som_da_referencia == "nenhum" else a.som_da_referencia,
               compostos=not a.nest_aberto)
    print(json.dumps({k: r[k] for k in ("sequencia", "planos", "arquivos", "casamento",
                                        "trilha_referencia")}, ensure_ascii=False, indent=1))
    for g in r["compostos"]:
        print(f"compound: {g['nome']} ({len(g['clipes'])} clipes, a partir do quadro "
              f"{min(c['quadro'] for c in g['clipes'])})")
    print(f"marcadores: {len(r['marcadores'])}  avisos: {len(r['avisos'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
