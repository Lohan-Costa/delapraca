from __future__ import annotations

import json
import re
import uuid
import zipfile
from pathlib import Path

RE_DRT = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.drt$")
SINAL = {"novo": "refazer", "trocado": "refazer", "esticado": "conferir", "aparado": "conferir",
         "camada": "conferir"}
ROTULO = {"novo": "NOVO NO CORTE: sem correção", "trocado": "TAKE TROCADO: sem correção",
          "esticado": "PLANO ESTICADO: o pedaço novo está sem correção",
          "aparado": "PLANO APARADO: conferir a correção nas pontas",
          "camada": "CAMADA SOBRE UM TRECHO QUE SAIU: conferir"}


def arquivo_de_trabalho(caminho: str, recebidos: Path) -> str:
    bruto = str(caminho or "")
    if bruto.replace("\\", "/").startswith("//"):
        raise ValueError("o arquivo da timeline de trabalho não veio da casca do De Lá Pra Cá")
    p = Path(bruto).resolve()
    if p.parent != recebidos.resolve() or not RE_DRT.match(p.name) or not p.is_file():
        raise ValueError("o arquivo da timeline de trabalho não veio da casca do De Lá Pra Cá")
    return str(p)


def devolvido_por(arquivos: dict):
    def devolvido(seg: dict, master: dict) -> bool:
        c = str(arquivos.get(seg.get("mob_id") or "") or master.get("caminho_na_bin") or "")
        return "para-edicao" in c.replace("\\", "/").lower().split("/")
    return devolvido


def ler_trabalho(partida, trabalho: str, contagem_api: dict, inicio: int | None = None):
    import atualizar as A
    from drp import reescrever as RW

    tsf = partida.resultado.get("timeline_start_frames")
    ini = int(inicio) if inicio is not None else int(tsf) if tsf is not None else RW.inicio_timeline(trabalho)
    try:
        itens = RW.itens(trabalho, inicio=ini)
    except (zipfile.BadZipFile, StopIteration, KeyError, UnicodeDecodeError):
        raise ValueError("o Resolve exportou a timeline de trabalho num formato que não é DRT — "
                         "nada foi alterado") from None
    no_arquivo = RW.contagem(trabalho)
    api = {str(k): int(v) for k, v in (contagem_api or {}).items() if int(v or 0) > 0}
    if no_arquivo != api:
        raise ValueError("o Resolve exportou uma timeline diferente da de trabalho "
                         f"(arquivo {no_arquivo}, Resolve {api}) — abra a timeline de trabalho no "
                         "Resolve e tente de novo; nada foi alterado")
    usadas = A.trilhas_usadas(partida.resultado)
    return ini, itens, no_arquivo, A.trilhas_da_carta(itens, usadas)


def _regioes_de_transicao(carta, usadas: dict, ini: int) -> dict:
    import atualizar as A

    fora = {}
    for t in A.transicoes(carta.resultado):
        idx, nova_trilha = A.trilha_do_drt(t["trilha"], usadas)
        if not nova_trilha:
            fora[(f"{t['trilha'][0]}{idx}", ini + t["inicio"], t["dur"])] = t
    return fora


def ajustar_transicoes(nova, drt: str, usadas: dict, ini: int, planos_novos: list | None = None,
                       do_editor: set | None = None, colorista_tirou: set | None = None) -> tuple[dict, list[str]]:
    from aaf.parser import frames_to_tc
    from drp import reescrever as RW

    na_nova = _regioes_de_transicao(nova, usadas, ini)
    no_drt = {(t["trilha"], t["start"], t["dur"]) for t in RW.transicoes(drt)}
    pedacos = [(i["trilha"], i["start"], i["start"] + i["dur"]) for i in RW.itens(drt, absoluto=True)]
    pedacos += list(planos_novos or [])
    fps = float(nova.resultado.get("timeline_fps") or 24)
    tirar = (no_drt & set(do_editor or ())) - set(na_nova)
    acrescentar, avisos = [], []
    for k in sorted(set(na_nova) & set(colorista_tirou or ()) - no_drt, key=lambda k: (k[1], k[0])):
        avisos.append(f"transição de {k[2]} quadro(s) na {k[0]} em {frames_to_tc(k[1], fps)}: está na versão do "
                      "editor e não estava na sua timeline — não foi reposta; conferir")
    for k in sorted(set(na_nova) - no_drt - set(colorista_tirou or ()), key=lambda k: (k[1], k[0])):
        t, (trilha, a, d) = na_nova[k], k
        b = a + d
        fins = [f for (tr, _i, f) in pedacos if tr == trilha and a <= f <= b]
        inis = [i for (tr, i, _f) in pedacos if tr == trilha and a <= i <= b]
        corte = next((f for f in fins if f in inis), None)
        if corte is not None:
            align = 1 if corte == a else (3 if corte == b else 2)
        elif a in inis and not fins:
            align = 1
        elif b in fins and not inis:
            align = 3
        else:
            avisos.append(f"transição de {d} quadro(s) na {trilha} em {frames_to_tc(a, fps)}: os planos em "
                          "volta dela não estão na timeline como no Avid — não foi escrita; conferir")
            continue
        acrescentar.append({"trilha": trilha, "start": a, "dur": d, "audio": trilha[0] == "A", "align": align})
        ef = (t.get("efeito") or "").upper()
        if trilha[0] == "V" and ef and "DISSOLVE" not in ef:
            avisos.append(f"transição {t.get('efeito')} do Avid na {trilha}: entrou como Cross Dissolve — conferir")
    if not acrescentar and not tirar:
        return {}, avisos
    return RW.ajustar_transicoes(drt, acrescentar, tirar), avisos


def encaixe(partida, itens_c: list[dict], arquivos: dict) -> dict:
    import atualizar as A

    e = A.encaixe(partida.resultado, itens_c, devolvido=devolvido_por(arquivos))
    return {**e, "minimo": A.ENCAIXE_MINIMO, "bate": e["pct"] >= A.ENCAIXE_MINIMO}


def texto_nao_bate(e: dict) -> str:
    return (f"A versão antiga não bate com a timeline de trabalho: só {e['no_lugar']} de "
            f"{e['planos']} planos dela estão no lugar ({e['pct']:g}% da imagem). Nada foi alterado. "
            "Confira se escolheu o arquivo de onde esta timeline veio.")


def preparar(partida, nova, trabalho: str, contagem_api: dict, recebidos: Path, midia,
             sinais: dict | None = None, codigo: dict | None = None, inicio: int | None = None) -> dict:
    import atualizar as A
    from drp import reescrever as RW
    from drp import sinais as SN

    ini, itens, no_arquivo, itens_c = ler_trabalho(partida, trabalho, contagem_api, inicio)
    usadas = A.trilhas_usadas(partida.resultado)
    arquivos = {**partida.arquivos, **nova.arquivos}
    e = encaixe(partida, itens_c, arquivos)
    if not e["bate"]:
        raise ValueError(texto_nao_bate(e))
    pe = A.plano_de_edicao(partida.resultado, nova.resultado, itens_c, devolvido=devolvido_por(arquivos))

    segs, casados = midia(nova) if pe["inserir"] else ([], [])
    arquivo_do_seg = {c["segment_index"]: c for c in casados}
    com_midia = lambda x: (c := arquivo_do_seg.get(x["segmento"])) and not c.get("offline")

    cortes = set()
    for x in pe["inserir"]:
        idx, nova_trilha = A.trilha_do_drt(x["trilha"], usadas)
        if not nova_trilha and com_midia(x):
            cortes |= {(x["trilha"][0], idx, ini + int(x["inicio"])),
                       (x["trilha"][0], idx, ini + int(x["inicio"]) + int(x["dur"]))}
    na_velha = _regioes_de_transicao(partida, usadas, ini)
    no_trabalho = {(t["trilha"], t["start"], t["dur"]) for t in RW.transicoes(trabalho)}
    rastro: dict = {}
    destino = recebidos / f"{uuid.uuid4()}.drt"
    conta = RW.aplicar(trabalho, str(destino), pe["itens"], ini,
                       mover_quadro=lambda q: A.quadro_novo(pe["mapa"], q), cortes=cortes, rastro=rastro)
    do_editor = {(k[0], rastro[k], k[2]) for k in no_trabalho & set(na_velha) if rastro.get(k) is not None}
    colorista_tirou = set()
    for k in set(na_velha) - no_trabalho:
        q, ok = A.quadro_novo(pe["mapa"], k[1] - ini)
        if ok:
            colorista_tirou.add((k[0], ini + int(q), k[2]))
    n_trilhas = {"V": max([int(t[1:]) for t in no_arquivo if t[0] == "V"] or [0]),
                 "A": max([int(t[1:]) for t in no_arquivo if t[0] == "A"] or [0])}
    novas_trilhas: dict[str, int] = {}
    inserir, avisos = [], []
    for x in pe["inserir"]:
        c = arquivo_do_seg.get(x["segmento"])
        s = segs[x["segmento"]] if x["segmento"] < len(segs) else {}
        letra = x["trilha"][0]
        idx, nova_trilha = A.trilha_do_drt(x["trilha"], usadas)
        if nova_trilha:
            if x["trilha"] not in novas_trilhas:
                n_trilhas[letra] += 1
                novas_trilhas[x["trilha"]] = n_trilhas[letra]
            idx = novas_trilhas[x["trilha"]]
        if not c or c.get("offline"):
            avisos.append(f"{x['nome']}: sem a mídia nesta máquina — o plano não foi inserido")
            continue
        classe = str(s.get("motion_class") or "native")
        rel = float(s.get("relative_speed") or 1.0) or 1.0
        editor = classe not in ("native", "conform")
        inicio_fonte = int(s.get("source_start_frames") or 0) + int(x.get("fonte_offset") or 0)
        quadros_fonte = max(1, round(int(x["dur"]) / rel)) if editor and rel > 0 else int(x["dur"])
        if editor:
            avisos.append(f"{x['nome']}: tem velocidade {round(100 / rel) if rel else 0}% no Avid "
                          f"({classe}); entrou em 100% — refazer")
        canal = int(s.get("canal_arquivo") or s.get("source_slot_id") or 1)
        if letra == "A" and canal > 1:
            avisos.append(f"{x['nome']}: o Avid usava o canal {canal} do arquivo — conferir o canal no Resolve")
        inserir.append({"arquivo": c["matched_path"], "inicio_fonte": inicio_fonte,
                        "fim_fonte": inicio_fonte + quadros_fonte, "tipo": letra, "trilha": idx,
                        "trilha_nova": nova_trilha, "quadro": ini + int(x["inicio"]), "dur": int(x["dur"]),
                        "classe": x["classe"], "nome": x["nome"],
                        "canal": canal})
    novos = [(f"{x['tipo']}{x['trilha']}", int(x["quadro"]), int(x["quadro"]) + int(x["fim_fonte"]) - int(x["inicio_fonte"]))
             for x in inserir if not x.get("trilha_nova")]
    novas_tr, avisos_tr = ajustar_transicoes(nova, str(destino), usadas, ini, novos, do_editor, colorista_tirou)
    conta.update({k: v for k, v in novas_tr.items() if v})
    esperado = RW.contagem(str(destino))
    avisos += avisos_tr
    for t, i in novas_trilhas.items():
        avisos.append(f"a montagem nova usa uma trilha que a de trabalho não tinha ({t}): criada como "
                      f"{t[0]}{i}")
    if conta.get("marcadores_fora_do_lugar"):
        avisos.append(f"{conta['marcadores_fora_do_lugar']} marcador(es) seu(s) estava(m) num trecho que "
                      "saiu: ficaram no ponto do corte")

    sinais = sinais or {}
    codigo = SN.validar_codigo(codigo)
    marcas = []

    def sinal(classe, tipo, trilha_drt, quadro_abs, dur):
        if tipo != "V":
            return
        cat = SINAL[classe]
        marcas.append({"quadro": quadro_abs - ini, "quadro_abs": quadro_abs, "trilha": trilha_drt,
                       "dur": dur, "classe": classe, "cor_marcador": codigo["cores"][cat]["marcador"],
                       "cor_clipe": codigo["cores"][cat]["clipe"], "nome": ROTULO[classe],
                       "palavra": codigo["palavra"]})

    for x in inserir:
        sinal(x["classe"], x["tipo"], x["trilha"], x["quadro"], x["dur"])
    por_id = {i["id"]: i for i in itens}
    for a in pe["aparados"]:
        it = por_id[a["id"]]
        sinal("aparado", it["trilha"][0], int(it["trilha"][1:]), ini + int(a["inicio"]), int(a["dur"]))
    for a in pe["conferir"]:
        it = por_id[a["id"]]
        sinal("camada", it["trilha"][0], int(it["trilha"][1:]), ini + int(a["inicio"]), int(a["dur"]))

    Path(str(destino)[:-4] + ".midias.json").write_text(json.dumps(
        {"drt": destino.name, "midias": sorted({x["arquivo"] for x in inserir})}, ensure_ascii=False),
        encoding="utf-8")
    return {"drt": str(destino), "esperado": esperado, "inserir": inserir,
            "marcas": marcas if (sinais.get("marcar") or sinais.get("colorir")) else [],
            "marcar": bool(sinais.get("marcar")), "colorir": bool(sinais.get("colorir")),
            "acoes": {k: v for k, v in conta.items() if k != "marcadores_fora_do_lugar"},
            "marcadores_fora_do_lugar": conta.get("marcadores_fora_do_lugar", 0),
            "avisos": avisos, "inicio": ini, "encaixe": e}
