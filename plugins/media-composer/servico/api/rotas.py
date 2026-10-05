from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from media.safepath import CaminhoInvalido

from .portao import ROTA_UI
from .servidor import rota, servir_arquivo_da_ui

log = logging.getLogger("delapraca.rotas")

EXTENSOES_TIMELINE = ("aaf",)


@rota("GET", "/health")
def health(ctx):
    return 200, {
        "ok": True,
        "app": "De Lá Pra Cá",
        "nle": "avid",
        "versao": ctx.servico.versao,
        "ui": ROTA_UI,
        "ocupada": ctx.servico.sessao.ocupada,
    }


@rota("GET", "/prontidao")
def prontidao(ctx):
    import prontidao as P
    forcar = "forcar=1" in (ctx.query or "")
    return 200, P.conferir(ctx.servico, forcar=forcar)


@rota("POST", "/receber/motor/ceder")
def motor_ceder(ctx):
    import threading
    s = ctx.servico
    if getattr(s, "app_exe", None):
        return 409, {"erro": "este motor é de um aplicativo aberto"}
    if s.sessao.ocupada:
        return 409, {"erro": "o motor está no meio de um trabalho"}
    parar = getattr(s, "parar", None)
    if parar is None:
        return 409, {"erro": "este motor não sabe encerrar"}
    log.info("o aplicativo pediu a porta: este motor (sem app) encerra")
    threading.Timer(0.3, parar.set).start()
    return 200, {"ok": True}


@rota("GET", ROTA_UI)
def painel(ctx):
    return servir_arquivo_da_ui("painel.html")


@rota("GET", "/static/painel.js")
def static_js(ctx):
    return servir_arquivo_da_ui("painel.js")


@rota("GET", "/static/painel.css")
def static_css(ctx):
    return servir_arquivo_da_ui("painel.css")


@rota("GET", "/mc/info")
def mc_info(ctx):
    with ctx.servico.media_composer() as mc:
        if not mc.esta_vivo():
            return 200, {"aberto": False}
        resposta = {"aberto": True}
        for chave, fn in (("app", mc.info), ("projeto", mc.projeto)):
            try:
                resposta[chave] = fn()
            except Exception as e:
                resposta[f"{chave}_erro"] = str(e)
        return 200, resposta


@rota("GET", "/mc/sequences")
def mc_sequences(ctx):
    with ctx.servico.media_composer() as mc:
        if not mc.esta_vivo():
            return 200, {"aberto": False, "bins": []}

        nomes_abertos = mc.bins_abertas()
        sabemos = nomes_abertos is not None
        if sabemos:
            candidatas = [n if n.lower().endswith(".avb") else f"{n}.avb"
                          for n in nomes_abertos]
        else:
            log.info("esta versão do MC não lista janelas — perguntando a todas as bins")
            candidatas = [mc.caminho_relativo_da_bin(c) for c in mc.bins()]

        selecionados = set()
        for nome in candidatas if sabemos else ():
            try:
                for item in mc.itens_da_bin(nome, flags=("sequences",),
                                            apenas_selecionados=True):
                    if item.mob_id:
                        selecionados.add(item.mob_id)
            except Exception as e:
                log.debug("seleção de %r não veio: %s", nome, e)

        bins = []
        for nome in candidatas:
            try:
                seqs = mc.itens_da_bin(nome, flags=("sequences",))
            except Exception as e:
                log.debug("bin %r não respondeu: %s", nome, e)
                continue
            if seqs:
                bins.append({
                    "bin": Path(nome).stem,
                    "sequences": [{"nome": s.name, "mob_id": s.mob_id,
                                   "selecionada": s.mob_id in selecionados}
                                  for s in seqs],
                })
        return 200, {"aberto": True, "bins": bins}


@rota("POST", "/mc/mob")
def mc_mob(ctx):
    from avid.mcapi import mob_id_valido

    mob_id = (ctx.json().get("mob_id") or "").strip()
    if not mob_id_valido(mob_id):
        return 400, {"erro": "MobID inválido — esperava 64 dígitos hexadecimais"}

    with ctx.servico.media_composer() as mc:
        if not mc.esta_vivo():
            return 409, {"erro": "o Media Composer não respondeu"}
        try:
            colunas = mc.colunas_do_master(mob_id)
        except Exception as e:
            return 409, {"erro": str(e)}

    if not colunas:
        return 404, {"erro": "o Media Composer não conhece esse mob — ele ainda está "
                             "no projeto aberto?"}

    nome = (colunas.get("Name") or "").strip()
    if not nome:
        log.info("mob %s não trouxe coluna Name (colunas: %s)", mob_id, list(colunas))
    return 200, {"mob_id": mob_id, "nome": nome, "colunas": colunas}


@rota("POST", "/diag/arrasto")
def diag_arrasto_gravar(ctx):
    corpo = ctx.json()
    ctx.servico.arrastos.append(corpo)
    del ctx.servico.arrastos[:-ctx.servico.MAX_ARRASTOS]
    tipos = corpo.get("tipos") or []
    log.info("arrasto recebido: efeito=%s tipos=%s", corpo.get("efeito"), tipos)
    log.debug("arrasto completo: %s", corpo)

    itens = _itens_do_arrasto(corpo)
    if itens and (len(itens) > 1 or any(i.get("type") != "sequence" for i in itens)):
        threading.Thread(target=_classificar_arrasto, args=(ctx.servico, itens),
                         daemon=True, name="classifica-arrasto").start()
    return 200, {"ok": True}


def _itens_do_arrasto(corpo: dict) -> list[dict]:
    bruto = (corpo.get("dados") or {}).get("text/x.avid.mc-api-asset-list+json") or ""
    try:
        dados = json.loads(bruto)
    except (ValueError, TypeError):
        return []
    return [i for i in (dados if isinstance(dados, list) else [dados]) if isinstance(i, dict)]


def _classificar_arrasto(servico, itens: list[dict], teto: int = 12) -> None:
    from avid.mcapi import mob_id_valido

    fora = []
    try:
        with servico.media_composer() as mc:
            if not mc.esta_vivo():
                return
            for item in itens[:teto]:
                mob_id = (item.get("id") or "").strip()
                registro = {"type": item.get("type"), "campos": sorted(item)}
                if mob_id_valido(mob_id):
                    try:
                        colunas = mc.colunas_do_master(mob_id)
                        registro["nome"] = colunas.get("Name", "")
                        registro["colunas"] = sorted(colunas)
                        registro["pistas"] = {k: colunas[k] for k in
                                              ("Drive", "Source File", "Source Path",
                                               "Tape", "Tracks", "Video", "Offline")
                                              if k in colunas}
                    except Exception as e:
                        registro["erro"] = str(e)
                fora.append(registro)
    except Exception as e:
        log.debug("classificação do arrasto falhou: %s", e)
        return

    log.info("arrasto classificado: %s", [(r.get("type"), r.get("nome")) for r in fora])
    if servico.arrastos and isinstance(servico.arrastos[-1], dict):
        servico.arrastos[-1]["classificacao"] = fora


@rota("GET", "/diag/arrasto")
def diag_arrasto_ler(ctx):
    recentes = list(reversed(ctx.servico.arrastos))
    if not recentes:
        return 200, {"vazio": True}
    return 200, {"ultimo": recentes[0], "anteriores": recentes[1:],
                 "quantos": len(recentes)}


@rota("GET", "/mc/preset")
def mc_preset(ctx):
    from presets import diagnostico
    return 200, diagnostico()


@rota("POST", "/mc/preset/instalar")
def mc_preset_instalar(ctx):
    from presets import instalar
    r = instalar(forcar=bool(ctx.json().get("forcar")))
    return (200 if r.get("ok") else 409), r


LINKS = {
    "autor": "https://www.linkedin.com/in/lohan-costa/",
}


@rota("POST", "/link/abrir")
def link_abrir(ctx):
    import plataforma

    destino = LINKS.get(ctx.json().get("qual") or "")
    if not destino:
        return 400, {"erro": "destino desconhecido"}
    try:
        plataforma.abrir_url(destino)
    except Exception as e:
        return 500, {"erro": str(e)}
    return 200, {"ok": True}


@rota("POST", "/app/revisao")
def app_revisao(ctx):
    return _chamar_app(ctx, "--revisao")


@rota("POST", "/app/revisao/fechar")
def app_revisao_fechar(ctx):
    return _chamar_app(ctx, "--fechar-revisao")


def _chamar_app(ctx, argumento: str):
    import subprocess

    import plataforma

    exe = ctx.servico.app_exe
    if not exe:
        return 200, {"janela": False, "motivo": "sem aplicativo"}
    try:
        subprocess.Popen([exe, argumento], **plataforma.SEM_JANELA)
    except OSError as e:
        log.warning("não consegui chamar o aplicativo (%s %s): %s", exe, argumento, e)
        return 200, {"janela": False, "motivo": str(e)}
    return 200, {"janela": True}


@rota("POST", "/escolher/pasta")
def escolher_pasta_rota(ctx):
    import escolher as escolher_mod
    from escolher import NaoSuportado, escolher_pastas
    try:
        caminhos = escolher_pastas()
    except NaoSuportado as e:
        return 501, {"erro": str(e)}
    except RuntimeError as e:
        return 500, {"erro": str(e)}
    resposta = {"caminhos": caminhos, "caminho": caminhos[0] if caminhos else None}
    if escolher_mod.ultimo_erro:
        resposta["aviso"] = ("o seletor moderno falhou e usei o antigo: "
                             + escolher_mod.ultimo_erro)
    return 200, resposta


def _pasta_do_projeto(ctx) -> str:
    from avid.mcapi import McApiIndisponivel

    with ctx.servico.media_composer() as mc:
        if not mc.esta_vivo():
            return ""
        try:
            return (mc.projeto() or {}).get("path") or ""
        except McApiIndisponivel:
            return ""


@rota("GET", "/banco")
def banco_estado(ctx):
    import banco

    pasta = _pasta_do_projeto(ctx)
    if not pasta:
        return 409, {"erro": "o Media Composer não respondeu"}
    return 200, banco.estado(pasta)


@rota("POST", "/banco/criar")
def banco_criar(ctx):
    import banco
    from escolher import NaoSuportado, nomear_arquivo

    pasta = _pasta_do_projeto(ctx)
    if not pasta:
        return 409, {"erro": "o Media Composer não respondeu"}
    try:
        escolhido = nomear_arquivo("Onde criar o banco de mídias", Path(pasta).name)
    except NaoSuportado as e:
        return 501, {"erro": str(e)}
    if not escolhido:
        return 200, {"cancelado": True, **banco.estado(pasta)}
    try:
        return 200, banco.criar(pasta, escolhido)
    except (ValueError, RuntimeError) as e:
        return 400, {"erro": str(e)}


@rota("POST", "/banco/conectar")
def banco_conectar(ctx):
    import banco
    from escolher import NaoSuportado, escolher_arquivo

    pasta = _pasta_do_projeto(ctx)
    if not pasta:
        return 409, {"erro": "o Media Composer não respondeu"}
    try:
        escolhido = escolher_arquivo("Escolha o banco.json do banco de mídias", ("json",))
    except NaoSuportado as e:
        return 501, {"erro": str(e)}
    except RuntimeError as e:
        return 500, {"erro": str(e)}
    if not escolhido:
        return 200, {"cancelado": True, **banco.estado(pasta)}
    try:
        return 200, banco.conectar(pasta, escolhido)
    except (ValueError, RuntimeError) as e:
        return 400, {"erro": str(e)}


@rota("POST", "/banco/desconectar")
def banco_desconectar(ctx):
    import banco

    pasta = _pasta_do_projeto(ctx)
    if not pasta:
        return 409, {"erro": "o Media Composer não respondeu"}
    return 200, banco.desconectar(pasta)


@rota("POST", "/escolher/timeline")
def escolher_timeline_rota(ctx):
    from escolher import NaoSuportado, escolher_arquivo
    try:
        caminho = escolher_arquivo("Escolha o AAF da montagem", EXTENSOES_TIMELINE)
    except NaoSuportado as e:
        return 501, {"erro": str(e)}
    except RuntimeError as e:
        return 500, {"erro": str(e)}
    if caminho and Path(caminho).suffix.lower().lstrip(".") not in EXTENSOES_TIMELINE:
        return 400, {"erro": f"só sei ler {', '.join(EXTENSOES_TIMELINE)} por enquanto"}
    return 200, {"caminho": caminho}


@rota("GET", "/trabalho")
def trabalho_estado(ctx):
    return 200, ctx.servico.sessao.instantaneo()


@rota("GET", "/trabalho/masters")
def trabalho_masters(ctx):
    so_revisao = "revisao=1" in (ctx.query or "")
    return 200, {"masters": ctx.servico.sessao.masters(so_revisao=so_revisao)}


@rota("POST", "/trabalho/preparar")
def trabalho_preparar(ctx):
    corpo = ctx.json()
    servico = ctx.servico
    pastas = [p for p in (corpo.get("pastas") or []) if p]
    try:
        pastas = [_pasta_valida(p) for p in pastas]
    except CaminhoInvalido as e:
        return 400, {"erro": str(e)}

    projeto_aberto = _pasta_do_projeto(ctx)

    if not pastas:
        return 400, {"erro": "aponte ao menos uma pasta de mídia"}

    aaf = corpo.get("aaf") or ""
    if aaf:
        try:
            aaf = _arquivo_valido(aaf, ".aaf")
        except CaminhoInvalido as e:
            return 400, {"erro": str(e)}
        sequence = corpo.get("sequence") or Path(aaf).stem
    else:
        mob_id = corpo.get("sequence_mob_id")
        if not mob_id:
            return 400, {"erro": "informe `aaf` ou `sequence_mob_id`"}

        if (corpo.get("rota") or "avb") == "avb":
            with servico.media_composer() as mc:
                if not mc.esta_vivo():
                    return 409, {"erro": "o Media Composer não respondeu"}
                bin_origem = mc.bin_do_mob(mob_id)
                sequence = corpo.get("sequence") or "sequence"
                mobs = 0
                if bin_origem and Path(bin_origem).is_file():
                    import relink_avb

                    mobs = relink_avb.garantir_bin_no_disco(mc, bin_origem)
                    if not mobs:
                        log.warning("a bin %s não tem mobs no disco — rota AAF",
                                    bin_origem)
            if bin_origem and mobs and Path(bin_origem).is_file():
                sessao = servico.sessao
                sessao.trabalho.achatar_subclipes = bool(
                    corpo.get("achatar_subclipes", True))
                sessao.trabalho.achatar_group_clips = bool(
                    corpo.get("achatar_group_clips", True))
                try:
                    sessao.preparar("", pastas, sequence, rota="avb",
                                    bin_origem=bin_origem,
                                    so_importados=bool(corpo.get("so_importados")),
                                    so_offline=bool(corpo.get("so_offline")),
                                    pasta_projeto=projeto_aberto)
                except RuntimeError as e:
                    return 409, {"erro": str(e)}
                return 200, {"ok": True, "rota": "avb", "bin": bin_origem}
            log.info("sem bin para o mob %s — caindo na rota AAF", mob_id)

        from presets import NOME_PRESET, diagnostico
        from relink import exportar_aaf

        preset = NOME_PRESET
        d = diagnostico()
        if d["situacao"] != "ok" and not corpo.get("forcar_preset"):
            return 409, {
                "erro": (f"o preset “{NOME_PRESET}” "
                         + {"ausente": "não está instalado no Media Composer.",
                            "divergente": d.get("motivo", "está diferente do esperado."),
                            "sem_settings": "não pôde ser verificado — não achei as "
                                            "settings do Media Composer."}
                           .get(d["situacao"], "não pôde ser verificado.")),
                "preset": d,
            }

        with servico.media_composer() as mc:
            if not mc.esta_vivo():
                return 409, {"erro": "o Media Composer não respondeu"}
            projeto = (mc.projeto() or {}).get("path") or ""
            sequence = corpo.get("sequence") or "sequence"
            try:
                aaf = exportar_aaf(mc, mob_id, projeto,
                                   _nome_de_arquivo(sequence), preset=preset)
            except Exception as e:
                return 409, {"erro": str(e)}

    sessao = servico.sessao
    sessao.trabalho.achatar_subclipes = bool(corpo.get("achatar_subclipes", True))
    sessao.trabalho.achatar_group_clips = bool(corpo.get("achatar_group_clips", True))
    try:
        sessao.preparar(aaf, pastas, sequence,
                        so_importados=bool(corpo.get("so_importados")),
                        so_offline=bool(corpo.get("so_offline")),
                        pasta_projeto=projeto_aberto)
    except RuntimeError as e:
        return 409, {"erro": str(e)}
    return 200, {"ok": True, "aaf": aaf}


@rota("POST", "/trabalho/escolher")
def trabalho_escolher(ctx):
    corpo = ctx.json()
    mob_id = corpo.get("mob_id")
    if not mob_id:
        return 400, {"erro": "informe `mob_id`"}
    escolhas = ctx.servico.sessao.trabalho.escolhas
    arquivo = corpo.get("arquivo")
    if arquivo:
        try:
            escolhas[mob_id] = _arquivo_valido(arquivo)
        except CaminhoInvalido as e:
            return 400, {"erro": str(e)}
    else:
        escolhas.pop(mob_id, None)
    return 200, {"ok": True, "decididos": len(ctx.servico.sessao.trabalho.decididos())}


@rota("POST", "/trabalho/opcoes")
def trabalho_opcoes(ctx):
    corpo = ctx.json()
    t = ctx.servico.sessao.trabalho
    if "achatar_subclipes" in corpo:
        t.achatar_subclipes = bool(corpo["achatar_subclipes"])
    if "achatar_group_clips" in corpo:
        t.achatar_group_clips = bool(corpo["achatar_group_clips"])
    return 200, {"achatar_subclipes": t.achatar_subclipes,
                 "achatar_group_clips": t.achatar_group_clips}


@rota("POST", "/trabalho/aplicar")
def trabalho_aplicar(ctx):
    servico = ctx.servico
    with servico.media_composer() as mc:
        if not mc.esta_vivo():
            return 409, {"erro": "o Media Composer não respondeu"}
        projeto = (mc.projeto() or {}).get("path") or ""
        from avid.acoes import Acoes
        pedido = (ctx.json().get("nome_bin") or "").strip()
        automatico = not pedido
        base = pedido or Path(projeto).name or "RELINK"
        nome_bin = Acoes(mc).nome_de_bin_livre(
            projeto, _nome_de_arquivo(base), sempre_numerar=automatico)

    try:
        servico.sessao.aplicar(projeto, nome_bin, str(servico.pasta_trabalho))
    except (RuntimeError, ValueError) as e:
        return 409, {"erro": str(e)}
    return 200, {"ok": True, "bin": nome_bin}


@rota("POST", "/trabalho/cancelar")
def trabalho_cancelar(ctx):
    return 200, {"cancelado": ctx.servico.sessao.cancelar()}


@rota("GET", "/progresso")
def progresso(ctx):
    return ("sse", ctx.servico.sessao.instantaneo)


@rota("POST", "/export/preparar")
def export_preparar(ctx):
    from avid.mcapi import mob_id_valido

    corpo = ctx.json()
    mob_id = (corpo.get("mob_id") or "").strip()
    if not mob_id_valido(mob_id):
        return 400, {"erro": "MobID inválido — esperava 64 dígitos hexadecimais"}
    try:
        ctx.servico.sessao_export.preparar(mob_id, (corpo.get("sequence") or "").strip())
    except RuntimeError as e:
        return 409, {"erro": str(e)}
    return 200, {"ok": True}


@rota("GET", "/export")
def export_estado(ctx):
    return 200, ctx.servico.sessao_export.instantaneo()


@rota("GET", "/export/progresso")
def export_progresso(ctx):
    return ("sse", ctx.servico.sessao_export.instantaneo)


@rota("POST", "/export/nome")
def export_nome(ctx):
    from nomes import export as nomes_export

    corpo = ctx.json()
    modo, campos = _campos_do_nome(ctx, corpo.get("modo"), corpo.get("tipo"))
    r = nomes_export.analisar(corpo.get("padrao") or "", campos)
    r["campos"] = [{"campo": c, "rotulo": rot, "valor": campos[c]}
                   for c, rot in nomes_export.CAMPOS
                   if modo == "insert" or c not in nomes_export.SO_INSERT]
    return 200, r


def _campos_do_nome(ctx, modo, tipo) -> tuple[str, dict]:
    import magic_link
    from nomes import export as nomes_export

    s = ctx.servico.sessao_export
    if modo == "insert":
        seq = (ctx.servico.coleta.sequencia or {}).get("nome") or ""
        rotulo = {"insert_cor": "COR", "insert_vfx": "VFX"}.get(
            magic_link.TIPOS_INSERT.get(str(tipo or "")), "")
        return "insert", nomes_export.valores(seq, s.projeto, tipo=rotulo)
    return "timeline", nomes_export.valores(s.nome_da_sequencia(), s.projeto)


def _nome_do_envio(ctx, corpo: dict, modo: str) -> str:
    from nomes import export as nomes_export

    _modo, campos = _campos_do_nome(ctx, modo, corpo.get("canal") or corpo.get("tipo"))
    padrao = str(corpo.get("padrao") or "")
    if not padrao.strip():
        return campos["sequencia"]
    r = nomes_export.analisar(padrao, campos)
    if r["erro"]:
        raise ValueError("nome: " + r["erro"])
    return r["nome"]


@rota("POST", "/escolher/referencia")
def escolher_referencia_rota(ctx):
    from escolher import NaoSuportado, escolher_arquivo
    try:
        caminho = escolher_arquivo("Escolha o vídeo de referência", ("mp4", "mov", "mxf"))
    except NaoSuportado as e:
        return 501, {"erro": str(e)}
    except RuntimeError as e:
        return 500, {"erro": str(e)}
    return 200, {"caminho": caminho}


@rota("POST", "/escolher/destino")
def escolher_destino_rota(ctx):
    from escolher import NaoSuportado, escolher_pasta
    try:
        pasta = escolher_pasta("Onde salvar a timeline exportada")
    except NaoSuportado as e:
        return 501, {"erro": str(e)}
    except RuntimeError as e:
        return 500, {"erro": str(e)}
    return 200, {"caminho": pasta}


@rota("POST", "/export/gerar")
def export_gerar(ctx):
    from nomes import export as nomes_export
    from sessao_export import com_extensao, formato_valido

    corpo = ctx.json()
    s = ctx.servico.sessao_export
    try:
        formato = formato_valido((corpo.get("formato") or "").strip())
    except ValueError as e:
        return 400, {"erro": str(e)}
    try:
        pasta = _pasta_valida(corpo.get("pasta") or "")
    except CaminhoInvalido as e:
        return 400, {"erro": "escolha onde salvar — " + str(e)}

    r = nomes_export.analisar(corpo.get("padrao") or "",
                              nomes_export.valores(s.nome_da_sequencia(), s.projeto))
    if r["erro"]:
        return 400, {"erro": "nome: " + r["erro"]}
    caminho = _arquivo_livre(com_extensao(str(Path(pasta) / r["nome"]), formato))
    try:
        s.gerar(formato, caminho, Path(caminho).stem, corpo.get("comentario") or "")
    except (RuntimeError, ValueError) as e:
        return 409, {"erro": str(e)}
    return 200, {"ok": True, "arquivo": caminho}


@rota("POST", "/export/cancelar")
def export_cancelar(ctx):
    return 200, {"cancelado": ctx.servico.sessao_export.cancelar()}


def _nle(ctx) -> str:
    from urllib.parse import parse_qs

    valor = (parse_qs(ctx.query or "").get("nle") or [""])[0]
    if not valor and ctx.metodo == "POST":
        try:
            valor = str(ctx.json().get("nle") or "")
        except Exception:
            valor = ""
    if not valor and getattr(ctx.servico, "receptor", False):
        return "resolve"
    return "resolve" if valor == "resolve" else "mc"


def _canais_cfg(ctx, projeto: str | None = None) -> tuple[dict, str]:
    import canais
    import magic_link

    if _nle(ctx) == "resolve":
        return canais.canais_da_maquina(), ""
    if projeto is None:
        try:
            projeto = _pasta_do_projeto(ctx)
        except Exception:
            projeto = ""
    return (magic_link.canais_do_projeto(projeto) if projeto else {}), projeto


def _estado_canais(ctx, projeto: str | None = None) -> dict:
    import canais

    cfg, _projeto = _canais_cfg(ctx, projeto)
    lista = []
    for setor, s in canais.SETORES.items():
        lista.append({"setor": setor, "rotulo": s["rotulo"], "de": s["de"], "para": s["para"],
                      "pasta": cfg.get(setor), "conectado": bool(canais.pasta_do_canal(cfg, setor))})
    return {"canais": lista, "lados": canais.ROTULO_LADO}


def _estado_magic(ctx, projeto: str | None = None) -> dict:
    return {**_estado_canais(ctx, projeto),
            "coleta": ctx.servico.coleta.estado(),
            "ultimo_envio": ctx.servico.ultimo_envio}


@rota("GET", "/magic")
def magic_estado(ctx):
    return 200, _estado_magic(ctx)


@rota("GET", "/canais")
def canais_estado(ctx):
    return 200, _estado_canais(ctx)


@rota("POST", "/canais/conectar")
def canais_conectar(ctx):
    import canais
    import magic_link
    from escolher import NaoSuportado, escolher_pasta

    setor = str(ctx.json().get("setor") or "")
    if not canais.e_setor(setor):
        return 400, {"erro": "canal desconhecido"}
    rotulo = canais.SETORES[setor]["rotulo"]
    try:
        pasta = escolher_pasta(f"Pasta do canal {rotulo} do Magic Link (edição ⇄ {rotulo.lower()})")
    except NaoSuportado as e:
        return 501, {"erro": str(e)}
    except RuntimeError as e:
        return 500, {"erro": str(e)}
    if not pasta:
        return 200, {"cancelado": True, **_estado_canais(ctx)}
    try:
        pasta = canais.validar_pasta(pasta)
        canais.garantir_pastas(pasta, setor)
        if _nle(ctx) == "resolve":
            canais.guardar_canal_na_maquina(setor, pasta)
            projeto = None
        else:
            projeto = _pasta_do_projeto(ctx)
            if not projeto:
                return 503, {"erro": "o Media Composer não respondeu — o canal fica no projeto"}
            magic_link.guardar_canal_no_projeto(projeto, setor, pasta)
    except (ValueError, RuntimeError) as e:
        return 400, {"erro": str(e)}
    except OSError as e:
        return 400, {"erro": f"não consegui criar as caixas do canal: {e.strerror or e}"}
    return 200, _estado_canais(ctx, projeto)


@rota("GET", "/ajustes/sinais")
def ajustes_sinais(ctx):
    import codigo_de_cores

    return 200, codigo_de_cores.estado()


@rota("POST", "/ajustes/sinais")
def ajustes_sinais_gravar(ctx):
    import codigo_de_cores
    from drp import sinais

    corpo = ctx.json()
    novo = sinais.de_fabrica() if corpo.get("fabrica") is True else corpo.get("codigo")
    try:
        return 200, codigo_de_cores.estado(codigo_de_cores.gravar(novo))
    except OSError as e:
        return 500, {"erro": f"não consegui guardar o código de cores: {e.strerror or e}"}


@rota("POST", "/insert/coletar")
def insert_coletar(ctx):
    from carta_aberta.insert import selecao_da_copia
    from coleta import ColetaFalhou, capturar

    if not ctx.servico.trava_magic.acquire(blocking=False):
        return 409, {"erro": "já há uma coleta ou um envio em andamento"}
    try:
        with ctx.servico.media_composer() as mc:
            if not mc.esta_vivo():
                return 503, {"erro": "o Media Composer não respondeu — ele está aberto?"}
            sequencia, copia, leitura = capturar(mc)
        itens, avisos = selecao_da_copia(copia, leitura)
        decisao = ctx.servico.coleta.decidir(sequencia, itens, avisos)
    except ColetaFalhou as e:
        return 409, {"erro": str(e)}
    finally:
        ctx.servico.trava_magic.release()
    return 200, {"decisao": decisao, **_estado_magic(ctx)}


@rota("POST", "/insert/confirmar")
def insert_confirmar(ctx):
    decisao = ctx.servico.coleta.confirmar(bool(ctx.json().get("ok")))
    return 200, {"decisao": decisao, **_estado_magic(ctx)}


@rota("POST", "/insert/remover")
def insert_remover(ctx):
    chave = ctx.json().get("chave")
    if not isinstance(chave, list) or len(chave) != 3:
        return 400, {"erro": "item inválido"}
    try:
        removido = ctx.servico.coleta.remover(chave)
    except (TypeError, ValueError):
        return 400, {"erro": "item inválido"}
    return 200, {"removido": removido, **_estado_magic(ctx)}


@rota("POST", "/insert/limpar")
def insert_limpar(ctx):
    ctx.servico.coleta.limpar()
    return 200, _estado_magic(ctx)


@rota("POST", "/magic/enviar")
def magic_enviar(ctx):
    import canais
    import magic_link

    corpo = ctx.json()
    tipo = (corpo.get("tipo") or "").strip()
    canal = str(corpo.get("canal") or "").strip()
    if tipo in magic_link.TIPOS_INSERT:
        tipo, canal = "insert", canal or tipo
    comentario = str(corpo.get("comentario") or "")
    if tipo not in ("timeline", "insert"):
        return 400, {"erro": f"tipo de envio desconhecido: {tipo!r}"}
    if not canais.e_setor(canal):
        return 400, {"erro": "escolha para qual canal enviar"}
    try:
        projeto = _pasta_do_projeto(ctx)
    except Exception:
        projeto = ""
    if not projeto:
        return 503, {"erro": "o Media Composer não respondeu — ele está aberto?"}
    cfg, _p = _canais_cfg(ctx, projeto)
    try:
        pasta = canais.caixa_de_saida(cfg, canal)
        tipo_insert = canais.tipo_do_insert(canal) if tipo == "insert" else None
    except ValueError as e:
        return 400, {"erro": str(e)}
    except OSError as e:
        return 503, {"erro": f"não consegui preparar a pasta do canal: {e.strerror or e}"}

    try:
        nome = _nome_do_envio(ctx, {**corpo, "canal": canal}, tipo)
    except ValueError as e:
        return 400, {"erro": str(e)}

    if tipo == "timeline":
        try:
            caminho = ctx.servico.sessao_export.enviar(pasta, comentario, nome=nome,
                                                        canal=canal)
        except (RuntimeError, ValueError) as e:
            return 409, {"erro": str(e)}
        ctx.servico.ultimo_envio = {"tipo": "timeline", "canal": canal, "arquivo": caminho}
        return 200, {"ok": True, "canal": canal, "arquivo": caminho}

    if not ctx.servico.trava_magic.acquire(blocking=False):
        return 409, {"erro": "já há uma coleta ou um envio em andamento"}
    try:
        with ctx.servico.media_composer() as mc:
            resumo = magic_link.enviar_insert(mc, ctx.servico.coleta, tipo_insert, pasta,
                                              comentario, so_video=bool(corpo.get("so_video")),
                                              nome=nome, canal=canal)
    except (LookupError, ValueError, RuntimeError) as e:
        return 409, {"erro": str(e)}
    finally:
        ctx.servico.trava_magic.release()
    ctx.servico.ultimo_envio = {**resumo, "canal": canal}
    return 200, {"ok": True, "canal": canal, **resumo}


@rota("POST", "/insert/exportar")
def insert_exportar(ctx):
    import magic_link
    from escolher import NaoSuportado, escolher_pasta

    corpo = ctx.json()
    try:
        nome = _nome_do_envio(ctx, corpo, "insert")
    except ValueError as e:
        return 400, {"erro": str(e)}
    try:
        pasta = escolher_pasta("Onde salvar a Carta do insert")
    except NaoSuportado as e:
        return 501, {"erro": str(e)}
    except RuntimeError as e:
        return 500, {"erro": str(e)}
    if not pasta:
        return 200, {"cancelado": True}
    if not ctx.servico.trava_magic.acquire(blocking=False):
        return 409, {"erro": "já há uma coleta ou um envio em andamento"}
    try:
        with ctx.servico.media_composer() as mc:
            resumo = magic_link.enviar_insert(mc, ctx.servico.coleta, magic_link.INSERT_EXPORTADO,
                                              pasta, str(corpo.get("comentario") or ""),
                                              so_video=bool(corpo.get("so_video")), nome=nome)
    except (LookupError, ValueError, RuntimeError) as e:
        return 409, {"erro": str(e)}
    finally:
        ctx.servico.trava_magic.release()
    return 200, {"ok": True, **resumo}


def _tocar_receptor(ctx) -> None:
    import time as _t

    ctx.servico.ultimo_uso_receptor = _t.monotonic()


@rota("GET", "/receber/estado")
def receber_estado(ctx):
    _tocar_receptor(ctx)
    return 200, {"versao": ctx.servico.versao, **_estado_canais(ctx)}


@rota("GET", "/receber/caixa")
def receber_caixa(ctx):
    _tocar_receptor(ctx)
    cfg, _p = _canais_cfg(ctx)
    return 200, ctx.servico.receber.caixa(cfg)


@rota("POST", "/receber/abrir")
def receber_abrir(ctx):
    from escolher import NaoSuportado, escolher_arquivo
    from sessao_receber import EXTENSOES_RECEBER

    _tocar_receptor(ctx)
    try:
        caminho = escolher_arquivo("Abrir uma timeline", EXTENSOES_RECEBER)
    except NaoSuportado as e:
        return 501, {"erro": str(e)}
    except RuntimeError as e:
        return 500, {"erro": str(e)}
    if not caminho:
        return 200, {"cancelado": True}
    try:
        return 200, ctx.servico.receber.abrir(caminho)
    except ValueError as e:
        return 400, {"erro": str(e)}


def _pedido_de(corpo: dict) -> dict:
    if corpo.get("avulso"):
        return {"avulso": str(corpo.get("avulso") or ""), "mob_id": str(corpo.get("mob_id") or "")}
    return {"id": str(corpo.get("id") or "")}


@rota("POST", "/receber/originais")
def receber_originais(ctx):
    from carta_aberta.reader import CartaInvalida

    _tocar_receptor(ctx)
    corpo = ctx.json()
    try:
        pastas = [_pasta_valida(p) for p in (corpo.get("pastas") or []) if p]
    except CaminhoInvalido as e:
        return 400, {"erro": str(e)}
    if not pastas:
        return 400, {"erro": "aponte ao menos uma pasta dos originais"}
    pedido = _pedido_de(corpo)
    try:
        carta = ctx.servico.receber.carta_do_pedido(pedido)
        ctx.servico.sessao.preparar_carta(carta, pastas, pedido)
    except (CartaInvalida, LookupError, ValueError) as e:
        return 409, {"erro": str(e)}
    except RuntimeError as e:
        return 409, {"erro": str(e)}
    return 200, {"ok": True}


@rota("POST", "/receber/prproj/analisar")
def receber_prproj_analisar(ctx):
    from sessao_receber import Cancelado

    _tocar_receptor(ctx)
    corpo = ctx.json()
    try:
        pastas = [_pasta_valida(p) for p in (corpo.get("pastas") or []) if p]
    except CaminhoInvalido as e:
        return 400, {"erro": str(e)}
    try:
        return 200, ctx.servico.receber.analisar_prproj(str(corpo.get("avulso") or ""),
                                                        str(corpo.get("mob_id") or ""), pastas)
    except Cancelado:
        return 409, {"erro": "cancelado", "cancelado": True}
    except (LookupError, ValueError, RuntimeError) as e:
        return 409, {"erro": str(e)}


@rota("POST", "/receber/preparar")
def receber_preparar(ctx):
    from carta_aberta.reader import CartaInvalida
    from sessao_receber import Cancelado

    _tocar_receptor(ctx)
    corpo = ctx.json()
    originais, raizes = None, None
    if corpo.get("originais"):
        s = ctx.servico.sessao
        t = s.trabalho
        if s.ocupada or t.rota != "carta" or t.pedido != _pedido_de(corpo):
            return 409, {"erro": "o casamento com os originais não é desta timeline — "
                                 "aponte as pastas e importe de novo"}
        originais, raizes = t.decididos(), list(t.pastas)
    notas = corpo.get("notas") is True
    sinais = {"marcar": corpo.get("marcar") is True, "colorir": corpo.get("colorir") is True,
              "audio": corpo.get("audio") is not False,
              "compound": corpo.get("compound") is not False,
              "som_referencia": corpo.get("som_referencia") if corpo.get("som_referencia")
              in ("A", "B", "nenhum") else "A"}
    if corpo.get("referencia"):
        try:
            sinais["referencia"] = _arquivo_valido(str(corpo.get("referencia")))
        except CaminhoInvalido as e:
            return 400, {"erro": str(e)}
    try:
        if corpo.get("avulso"):
            return 200, ctx.servico.receber.preparar_avulso(str(corpo.get("avulso") or ""),
                                                            str(corpo.get("mob_id") or ""),
                                                            originais, raizes, notas=notas,
                                                            sinais=sinais)
        return 200, ctx.servico.receber.preparar(str(corpo.get("id") or ""), originais, raizes,
                                                 notas=notas, sinais=sinais)
    except Cancelado:
        return 409, {"erro": "cancelado", "cancelado": True}
    except (CartaInvalida, LookupError, ValueError, RuntimeError) as e:
        return 409, {"erro": str(e)}


@rota("GET", "/receber/progresso")
def receber_progresso(ctx):
    _tocar_receptor(ctx)
    return 200, ctx.servico.receber.progresso()


@rota("POST", "/receber/cancelar")
def receber_cancelar(ctx):
    _tocar_receptor(ctx)
    return 200, {"ok": ctx.servico.receber.cancelar()}


@rota("POST", "/conferencia/iniciar")
def conferencia_iniciar(ctx):
    from conferencia import conferencias

    _tocar_receptor(ctx)
    corpo = ctx.json()
    try:
        return 200, conferencias().iniciar(str(corpo.get("id") or ""), corpo.get("do_comeco") is True)
    except (LookupError, ValueError) as e:
        return 409, {"erro": str(e)}


@rota("POST", "/conferencia/quadros")
def conferencia_quadros(ctx):
    from conferencia import conferencias

    _tocar_receptor(ctx)
    corpo = ctx.json()
    q = corpo.get("quadros")
    try:
        return 200, {"na_fila": conferencias().comparar(str(corpo.get("id") or ""),
                                                        q[:500] if isinstance(q, list) else [])}
    except (LookupError, ValueError) as e:
        return 409, {"erro": str(e)}


@rota("POST", "/conferencia/estado")
def conferencia_estado(ctx):
    from conferencia import conferencias

    _tocar_receptor(ctx)
    try:
        return 200, conferencias().estado(str(ctx.json().get("id") or ""))
    except (LookupError, ValueError) as e:
        return 409, {"erro": str(e)}


@rota("POST", "/conferencia/marcas")
def conferencia_marcas(ctx):
    from conferencia import conferencias

    _tocar_receptor(ctx)
    try:
        return 200, conferencias().marcas(str(ctx.json().get("id") or ""))
    except (LookupError, ValueError) as e:
        return 409, {"erro": str(e)}


@rota("POST", "/receber/trazido")
def receber_trazido(ctx):
    _tocar_receptor(ctx)
    corpo = ctx.json()
    try:
        tl = corpo.get("timeline") if isinstance(corpo.get("timeline"), dict) else None
        r = ctx.servico.receber.trazido(str(corpo.get("id") or ""), bool(corpo.get("ok")),
                                        str(corpo.get("detalhe") or ""), timeline=tl,
                                        substitui=str(corpo.get("substitui") or ""))
    except (OSError, ValueError) as e:
        return 400, {"erro": str(e)}
    return 200, {"ok": True, **r}


@rota("POST", "/receber/partidas")
def receber_partidas(ctx):
    _tocar_receptor(ctx)
    corpo = ctx.json()
    tl = corpo.get("timeline") if isinstance(corpo.get("timeline"), dict) else None
    try:
        return 200, ctx.servico.receber.partidas_para(str(corpo.get("id") or ""), timeline=tl)
    except (LookupError, ValueError) as e:
        return 409, {"erro": str(e)}


def _versoes(corpo: dict) -> tuple[dict, dict]:
    def uma(v) -> dict:
        v = v if isinstance(v, dict) else {}
        for k in ("envio", "partida", "avulso"):
            if v.get(k):
                return {k: str(v[k]), **({"mob_id": str(v.get("mob_id") or "")} if k == "avulso" else {})}
        return {}
    antiga = uma(corpo.get("antiga")) or ({"partida": str(corpo["partida"])} if corpo.get("partida") else {})
    nova = uma(corpo.get("nova")) or ({"envio": str(corpo["id"])} if corpo.get("id") else {})
    return antiga, nova


def _inicio(corpo: dict) -> int | None:
    v = corpo.get("inicio")
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


@rota("POST", "/receber/comparar")
def receber_comparar(ctx):
    from carta_aberta.reader import CartaInvalida

    _tocar_receptor(ctx)
    corpo = ctx.json()
    antiga, nova = _versoes(corpo)
    contagem = corpo.get("contagem") if isinstance(corpo.get("contagem"), dict) else {}
    try:
        return 200, ctx.servico.receber.comparar_versoes(
            antiga, nova, contar_audio=corpo.get("audio") is True,
            trabalho=str(corpo.get("trabalho") or ""), contagem_api=contagem, inicio=_inicio(corpo))
    except (CartaInvalida, LookupError, OSError, ValueError) as e:
        return 409, {"erro": str(e)}


@rota("POST", "/receber/atualizar")
def receber_atualizar(ctx):
    from carta_aberta.reader import CartaInvalida

    _tocar_receptor(ctx)
    corpo = ctx.json()
    antiga, nova = _versoes(corpo)
    contagem = corpo.get("contagem") if isinstance(corpo.get("contagem"), dict) else {}
    try:
        return 200, ctx.servico.receber.preparar_versoes(
            antiga, nova, str(corpo.get("trabalho") or ""), contagem,
            sinais={"marcar": corpo.get("marcar") is True, "colorir": corpo.get("colorir") is True},
            inicio=_inicio(corpo))
    except (CartaInvalida, LookupError, OSError, ValueError) as e:
        return 409, {"erro": str(e)}


@rota("POST", "/receber/atualizada")
def receber_atualizada(ctx):
    from carta_aberta.reader import CartaInvalida

    _tocar_receptor(ctx)
    corpo = ctx.json()
    _antiga, nova = _versoes(corpo)
    tl = corpo.get("timeline") if isinstance(corpo.get("timeline"), dict) else None
    try:
        r = ctx.servico.receber.atualizada(nova, tl, substitui=str(corpo.get("substitui") or ""),
                                           registro=str(corpo.get("registro") or ""))
    except (CartaInvalida, LookupError, OSError, ValueError) as e:
        return 409, {"erro": str(e)}
    return 200, {"ok": True, **r}


@rota("GET", "/ajustes/atualizar")
def ajustes_atualizar(ctx):
    import partidas

    return 200, {"faixas": list(partidas.faixas()), "fabrica": list(partidas.FAIXAS_DE_FABRICA)}


@rota("POST", "/ajustes/atualizar")
def ajustes_atualizar_gravar(ctx):
    import partidas

    corpo = ctx.json()
    f = corpo.get("faixas") if isinstance(corpo.get("faixas"), list) and len(corpo["faixas"]) == 2         else partidas.FAIXAS_DE_FABRICA
    if corpo.get("fabrica") is True:
        f = partidas.FAIXAS_DE_FABRICA
    try:
        return 200, {"faixas": list(partidas.gravar_faixas(*f)), "fabrica": list(partidas.FAIXAS_DE_FABRICA)}
    except OSError as e:
        return 500, {"erro": f"não consegui guardar as faixas: {e.strerror or e}"}


def _ocupado(servico) -> str:
    from conferencia import conferencias

    if servico.sessao.ocupada or servico.sessao_export.ocupada:
        return "o De Lá Pra Cá está no meio de um trabalho no Media Composer"
    if servico.receber.progresso().get("ativo"):
        return "o De Lá Pra Cá está no meio de um Importar"
    if conferencias().em_uso():
        return "há uma conferência em curso (ou que acabou de parar)"
    return ""


def _estado_do_cache(servico) -> dict:
    import cache

    return {**cache.estatisticas(), "antigo": cache.antigo(), "ocupado": _ocupado(servico)}


@rota("GET", "/cache")
def cache_estado(ctx):
    return 200, _estado_do_cache(ctx.servico)


@rota("POST", "/cache/configurar")
def cache_configurar(ctx):
    import cache
    import configuracoes

    corpo = ctx.json()
    mudancas = {}
    if "dir" in corpo:
        bruto = corpo.get("dir")
        escolhida = configuracoes.pasta_aceitavel(bruto) if bruto else None
        if bruto and not escolhida:
            return 400, {"erro": "escolha uma pasta deste computador (caminho de rede não vale)"}
        if escolhida:
            try:
                Path(escolhida).mkdir(parents=True, exist_ok=True)
                teste = Path(escolhida) / f".dlpc-{threading.get_ident()}.tmp"
                teste.write_bytes(b"ok")
                teste.unlink()
            except OSError as e:
                return 409, {"erro": f"não consigo gravar nessa pasta ({e.strerror or e})"}
        mudancas["cache_dir"] = escolhida
    if "max_gb" in corpo:
        mudancas["cache_max_gb"] = corpo.get("max_gb")
    try:
        configuracoes.gravar(mudancas)
    except OSError as e:
        return 500, {"erro": f"não consegui guardar as configurações: {e.strerror or e}"}
    log.info("cache: configurado em %s (teto %s GB)", cache.raiz(), configuracoes.ler()["cache_max_gb"])
    return 200, _estado_do_cache(ctx.servico)


@rota("POST", "/cache/limpar")
def cache_limpar(ctx):
    import cache

    motivo = _ocupado(ctx.servico)
    if motivo:
        return 409, {"erro": f"agora não: {motivo}. Tente quando terminar."}
    return 200, {"liberado": cache.limpar(), **_estado_do_cache(ctx.servico)}


@rota("POST", "/cache/antigo")
def cache_antigo(ctx):
    import cache
    import configuracoes

    acao = ctx.json().get("acao")
    if acao == "apagar":
        motivo = _ocupado(ctx.servico)
        if motivo:
            return 409, {"erro": f"agora não: {motivo}"}
        liberado = cache.apagar_antigo()
    elif acao == "manter":
        configuracoes.gravar({"cache_antigo_dispensado": True})
        liberado = None
    else:
        return 400, {"erro": "ação desconhecida"}
    return 200, {"liberado": liberado, **_estado_do_cache(ctx.servico)}


@rota("POST", "/pasta/abrir")
def pasta_abrir(ctx):
    import cache
    import plataforma
    from conferencia import conferencias

    corpo = ctx.json()
    qual = corpo.get("qual")
    try:
        if qual == "conferencia":
            pasta = Path(conferencias().pasta(str(corpo.get("id") or "")))
        else:
            pasta = cache.pasta_conhecida(str(qual or ""))
        plataforma.abrir_pasta(pasta)
    except (LookupError, ValueError) as e:
        return 409, {"erro": str(e)}
    except OSError as e:
        return 500, {"erro": f"não consegui abrir a pasta: {e.strerror or e}"}
    return 200, {"ok": True}


@rota("GET", "/versao")
def versao_estado(ctx):
    import versao_nova

    return 200, versao_nova.estado()


@rota("POST", "/versao/conferir")
def versao_conferir(ctx):
    import versao_nova

    return 200, versao_nova.estado(forcar=True)


@rota("POST", "/versao/avisar")
def versao_avisar(ctx):
    import configuracoes
    import versao_nova

    ligado = ctx.json().get("ligado")
    if not isinstance(ligado, bool):
        return 400, {"erro": "ligado tem de ser verdadeiro ou falso"}
    try:
        configuracoes.gravar({"avisar_versao_nova": ligado})
    except OSError as e:
        return 500, {"erro": f"não consegui guardar as configurações: {e.strerror or e}"}
    return 200, versao_nova.estado()


@rota("POST", "/versao/baixar")
def versao_baixar(ctx):
    import plataforma
    import versao_nova

    e = versao_nova.estado()
    if e["estado"] != "nova" or not versao_nova.url_confiavel(e.get("url")):
        return 409, {"erro": "não há versão nova para baixar", **e}
    plataforma.abrir_url(e["url"])
    return 200, e


def _estado_integracoes() -> dict:
    import integracoes

    return {"integracoes": integracoes.estado()}


@rota("GET", "/integracoes")
def integracoes_estado(ctx):
    return 200, _estado_integracoes()


@rota("POST", "/integracoes/acao")
def integracoes_acao(ctx):
    import integracoes

    corpo = ctx.json()
    par = integracoes.ACOES.get(str(corpo.get("id") or ""))
    acao = corpo.get("acao")
    if not par or acao not in ("instalar", "remover"):
        return 400, {"erro": "integração ou ação desconhecida"}
    try:
        r = par[0]() if acao == "instalar" else par[1]()
    except integracoes.Impedido as e:
        return 409, {"erro": str(e), **_estado_integracoes()}
    except OSError as e:
        log.warning("integração %s/%s falhou", corpo.get("id"), acao, exc_info=True)
        return 500, {"erro": f"não consegui {acao}: {e.strerror or e}", **_estado_integracoes()}
    return 200, {"resultado": r or {}, **_estado_integracoes()}




def _arquivo_livre(caminho: str) -> str:
    p = Path(caminho)
    n = 2
    while p.exists():
        p = p.with_name(f"{Path(caminho).stem}_{n}{p.suffix}")
        n += 1
    return str(p)


def _pasta_valida(caminho: str) -> str:
    p = Path(caminho).expanduser()
    if not p.is_absolute():
        raise CaminhoInvalido(f"o caminho precisa ser absoluto: {caminho!r}")
    if "://" in caminho:
        raise CaminhoInvalido("URL não é pasta de mídia")
    p = p.resolve()
    if not p.is_dir():
        raise CaminhoInvalido(f"pasta não encontrada: {caminho!r}")
    return str(p)


def _arquivo_valido(caminho: str, extensao: str = "") -> str:
    p = Path(caminho).expanduser()
    if not p.is_absolute() or "://" in caminho:
        raise CaminhoInvalido(f"caminho de arquivo inválido: {caminho!r}")
    p = p.resolve()
    if not p.is_file():
        raise CaminhoInvalido(f"arquivo não encontrado: {caminho!r}")
    if extensao and p.suffix.lower() != extensao:
        raise CaminhoInvalido(f"esperava um {extensao}: {p.name!r}")
    return str(p)


def _nome_de_arquivo(nome: str) -> str:
    limpo = "".join(c for c in (nome or "") if c.isalnum() or c in " -_.").strip()
    return (limpo or "RELINK")[:60]
