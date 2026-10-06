from __future__ import annotations

import logging
import os
import random
import time
from pathlib import Path

from aaf.masters import chave_mob
from avid.acoes import Acoes
from relink import Progresso, Trabalho

log = logging.getLogger("delapraca.relink_avb")


def esperar_bin_estabilizar(caminho: Path, quietos: int = 3,
                            limite_s: float = 120.0) -> None:
    anterior, estaveis = None, 0
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim and estaveis < quietos:
        try:
            st = caminho.stat()
        except OSError:
            time.sleep(0.5)
            continue
        atual = (st.st_size, st.st_mtime_ns)
        estaveis = estaveis + 1 if atual == anterior else 0
        anterior = atual
        time.sleep(0.5)


def garantir_bin_no_disco(mc, bin_origem: str) -> int:
    Acoes(mc).salvar()
    esperar_bin_estabilizar(Path(bin_origem))
    from avb_export.reader import topen

    try:
        with topen(str(bin_origem)) as f:
            return len(list(f.content.mobs))
    except Exception as e:
        log.warning("não consegui ler %s: %s", bin_origem, e)
        return 0


def mobid_pyavb(mob_id_api: str):
    from avb.mobid import MobID
    from avid.mcapi import mob_id_para_pyavb

    return MobID(bytes_le=bytes.fromhex(mob_id_para_pyavb(mob_id_api)))


def aplicar(mc, trabalho: Trabalho, bin_origem: str, pasta_trabalho: str,
            nome_bin: str, progresso: Progresso | None = None) -> dict:
    from avb_bin import audio as avb_audio
    from avb_bin import denest as avb_denest
    from avb_bin import masters as avb_masters
    from avb_bin import percurso as avb_percurso
    from avb_bin import remap as avb_remap
    from avb_bin import visibilidade as avb_vis
    from avb_export.merge import materialize, serialize
    from avb_export.reader import topen

    p = progresso or Progresso()
    acoes = Acoes(mc)
    decididos = trabalho.decididos()
    if not decididos:
        raise ValueError("nada decidido — não há o que relinkar")

    raiz = Path(bin_origem).parent
    por_mob = {m.mob_id: m for m in trabalho.masters}
    video = {k: v for k, v in decididos.items() if not avb_audio.e_audio(v)}
    audio = {k: v for k, v in decididos.items() if avb_audio.e_audio(v)}

    p("preparando a bin", 0, 1, nome_bin)
    saida = raiz / f"{nome_bin}.avb"
    with topen(str(bin_origem)) as f:
        f.content.uid = random.getrandbits(63)
        f.write(str(saida))
    mc.abrir_bin(str(saida))

    mapa: dict[str, object] = {}
    falhas: list[dict] = []
    total = len(video)
    t0 = time.time()
    for i, (mob_id, caminho) in enumerate(sorted(video.items(), key=lambda kv: kv[1])):
        p("trazendo o vídeo", i, total, Path(caminho).name)
        try:
            novo = acoes.link_file(caminho, saida.name)
        except Exception as e:
            falhas.append({"arquivo": caminho, "erro": str(e)})
            continue
        if novo:
            mapa[mob_id] = mobid_pyavb(novo)
        else:
            falhas.append({"arquivo": caminho, "erro": "LinkFile não devolveu mob_id"})
    if total:
        log.info("LinkFile: %d/%d em %.1fs", len(mapa), total, time.time() - t0)

    p("salvando", total, total, "")
    acoes.salvar()
    esperar_bin_estabilizar(saida)

    p("fechando a bin no Media Composer", 0, 1, saida.name)
    saida_final = saida
    fechada = True
    t_fechar = time.time()
    try:
        mc.fechar_bin(str(saida))
    except Exception as e:
        fechada = False
        log.warning("não consegui fechar %s (%s) — entregando com outro nome",
                    saida.name, e)
    log.info("CloseBin(%s): %.1fs (%s)", saida.name, time.time() - t_fechar,
             "ok" if fechada else "falhou")
    if fechada:
        esperar_bin_estabilizar(saida)
    else:
        saida_final = raiz / f"{nome_bin} (relinkada).avb"
        log.warning("entregando em %s", saida_final.name)

    p("montando a bin", 0, 1, nome_bin)
    bin_audio = Path(pasta_trabalho) / f"{nome_bin} (audio).avb"
    fps = next((m.edit_rate for m in trabalho.masters if m.edit_rate), None)
    r_audio = {"clipes": 0, "mapa": {}, "pulados": [], "sem_template": [],
               "esticados": [], "curtos_demais": []}
    temporaria = saida_final.with_name(saida_final.name + ".novo")
    with topen(str(saida)) as f:
        r_denest = avb_denest.achatar(f)

        if audio:
            uso = avb_masters.uso_maximo(f)
            minimos = {caminho: uso.get(mob_id, 0) for mob_id, caminho in audio.items()}
            r_audio = avb_audio.autorar_masters(
                sorted(audio.values()), str(bin_audio), fps=fps, minimos=minimos,
                progresso=lambda feito, tot, n: p("preparando o áudio", feito, tot, n))
            for mob_id, caminho in audio.items():
                novo = r_audio["mapa"].get(caminho)
                if novo:
                    mapa[mob_id] = novo
            for nome, erro in r_audio["pulados"]:
                falhas.append({"arquivo": nome, "erro": erro})
            for nome, falta in r_audio["curtos_demais"]:
                falhas.append({"arquivo": nome,
                               "erro": f"o arquivo é {falta} frames mais curto que a "
                                       f"timeline pede — não é a mídia desta edição"})

            if r_audio["clipes"]:
                tem = {m.mob_id for m in f.content.mobs}
                with topen(str(bin_audio)) as fa:
                    for m in fa.content.mobs:
                        if m.mob_id in tem:
                            continue
                        f.content.add_mob(materialize(f, serialize(m)))
                        tem.add(m.mob_id)

        if not mapa:
            raise RuntimeError("nenhum arquivo pôde ser trazido para o Media Composer")

        por_texto = {str(m.mob_id): m.mob_id for m in f.content.mobs}
        mapa_final = {
            chave_mob(velho): (por_texto.get(novo, novo) if isinstance(novo, str) else novo)
            for velho, novo in mapa.items()
        }
        r_remap = avb_remap.repontar(f, mapa_final)

        ids_online = {str(v) for v in mapa_final.values()}
        try:
            r_vis = avb_vis.ajustar(f, avb_percurso.timeline(f), ids_online)
        except Exception as e:
            log.warning("não consegui ajustar a visibilidade da bin (%s) — "
                        "entregando com todos os itens visíveis", e, exc_info=True)
            r_vis = {"visiveis": 0, "ocultos": 0, "erro": str(e)}

        f.content.uid = random.getrandbits(63)
        f.write(str(temporaria))

    try:
        mc.fechar_bin(str(saida_final))
    except Exception:
        pass

    try:
        os.replace(temporaria, saida_final)
    except OSError as e:
        log.warning("a troca falhou mesmo depois de fechar a bin (%s)", e)
        saida_final = raiz / f"{nome_bin} (relinkada).avb"
        log.warning("entregando como %s", saida_final.name)
        os.replace(temporaria, saida_final)

    p("abrindo no Media Composer", 0, 1, saida_final.stem)
    aberta = True
    try:
        mc.abrir_bin(str(saida_final))
    except Exception as e:
        aberta = False
        log.warning("a bin foi gravada mas não abriu sozinha (%s): %s", e, saida_final)

    resultado = {
        "bin": saida_final.stem,
        "avb": str(saida_final),
        "aberta_no_mc": aberta,
        "masters_online": len(mapa),
        "masters_pedidos": len(decididos),
        "video_linkado": len(video) - sum(1 for x in falhas if x["arquivo"] in video.values()),
        "audio_autorado": r_audio["clipes"],
        "audio_sem_template": r_audio["sem_template"],
        "audio_esticado": r_audio["esticados"],
        "audio_curto_demais": r_audio["curtos_demais"],
        "falhas": falhas,
        "clipes_visiveis": r_vis["visiveis"],
        "clipes_ocultos": r_vis["ocultos"],
        "subclipes_achatados": r_denest["achatados"],
        "subclipes_restantes": r_denest["subclipes_restantes"],
        "referencias_trocadas": r_remap["repontados"],
        "masters_remapeados": r_remap["masters_trocados"],
        "segmentos_cobertos": sum(por_mob[m].segmentos for m in mapa if m in por_mob),
    }
    p("pronto", 1, 1, "")
    log.info("relink por bin aplicado: %s", resultado)
    return resultado
