#!/usr/bin/env python3

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path



_AQUI = Path(__file__).resolve().parent
for _caminho in (_AQUI, _AQUI.parent.parent.parent / "motor"):
    if str(_caminho) not in sys.path:
        sys.path.insert(0, str(_caminho))

import plataforma

VERSAO = plataforma.versao()
from api import PORTA, iniciar
from avid import MediaComposer
from sessao import Sessao
from sessao_export import SessaoExport
from coleta import Coleta

log = logging.getLogger("delapraca")


class Servico:

    def __init__(self):
        self.versao = VERSAO
        self.sessao = Sessao(self)
        self.sessao_export = SessaoExport(self)
        self.coleta = Coleta()
        self.trava_magic = threading.Lock()
        self.ultimo_envio: dict = {}
        from sessao_receber import SessaoReceber, limpar_recebidos

        self.receber = SessaoReceber()
        try:
            n = limpar_recebidos()
            if n:
                log.info("recebidos: %d arquivo(s) de passagem com mais de uma semana apagados", n)
        except OSError:
            log.debug("não consegui limpar recebidos/", exc_info=True)
        self.token: str | None = None
        self.receptor = False
        self.pasta_trabalho = plataforma.pasta_trabalho()
        self.arrastos: list = []
        self.MAX_ARRASTOS = 5
        self.app_exe: str | None = None

    def media_composer(self) -> MediaComposer:
        return MediaComposer()


def _configurar_log(nivel: str, origem: str = "serviço") -> None:
    import registro

    logging.basicConfig(
        level=getattr(logging, nivel.upper(), logging.INFO),
        format=registro.FORMATO,
        datefmt="%H:%M:%S",
    )

    try:
        log.info("log em arquivo: %s", registro.configurar(origem, VERSAO))
    except OSError as e:
        log.warning("sem log em arquivo (%s) — seguindo só no terminal", e)
    if nivel != "debug":
        logging.getLogger("relinker").setLevel(logging.WARNING)


def vigiar_media_composer(servico, parar: threading.Event, carencia_s: float = 60.0,
                          intervalo_s: float = 5.0) -> None:
    from plataforma import media_composer_aberto

    ja_vi = False
    ausente_desde: float | None = None
    while not parar.wait(intervalo_s):
        try:
            aberto = media_composer_aberto()
        except Exception:
            log.debug("não consegui enumerar processos", exc_info=True)
            continue

        if aberto:
            ja_vi, ausente_desde = True, None
            continue
        if not ja_vi:
            continue
        if servico.sessao.ocupada:
            ausente_desde = None
            continue
        agora = time.monotonic()
        receptor = getattr(servico, "ultimo_uso_receptor", None)
        if receptor is not None and agora - receptor < carencia_s:
            if ausente_desde is not None:
                log.info("Media Composer fechado, mas o painel do Resolve está em uso — o serviço fica")
            ausente_desde = None
            continue

        if ausente_desde is None:
            ausente_desde = agora
            log.info("Media Composer não está mais no ar — aguardando %.0fs", carencia_s)
        elif agora - ausente_desde >= carencia_s:
            log.info("Media Composer fechado há %.0fs — encerrando o serviço", carencia_s)
            parar.set()
            return


def _app_vivo(pid: int):
    if os.name == "nt":
        import ctypes

        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x00100000 | 0x1000, False, pid)
        if not h:
            return None
        return lambda: k32.WaitForSingleObject(ctypes.c_void_p(h), 0) == 0x102
    if os.getppid() == pid:
        return lambda: os.getppid() == pid

    def vivo() -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
    return vivo


def vigiar_app(servico, parar: threading.Event, pid: int, intervalo_s: float = 2.0) -> None:
    vivo = _app_vivo(pid)
    if vivo is None:
        log.warning("não consigo vigiar o aplicativo (pid %d): o motor depende dele me encerrar", pid)
        return
    while not parar.wait(intervalo_s):
        if vivo():
            continue
        if servico.sessao.ocupada or servico.receber.progresso().get("ativo"):
            continue
        log.info("o aplicativo (pid %d) fechou — encerrando o serviço", pid)
        parar.set()
        return


def vigiar_ociosidade(servico, parar: threading.Event, ocioso_s: float,
                      intervalo_s: float = 30.0) -> None:
    servico.ultimo_uso_receptor = time.monotonic()
    while not parar.wait(intervalo_s):
        if time.monotonic() - getattr(servico, "ultimo_uso_receptor", 0) >= ocioso_s:
            log.info("receptor sem uso há %.0f min — encerrando", ocioso_s / 60)
            parar.set()
            return


ARG_DIALOGO = "--dialogo-win"


def conferir_pacote() -> int:
    import json as _json

    itens: dict[str, bool] = {"versao": VERSAO != "0.0.0"}
    import integracoes
    from api.servidor import DIR_UI
    from avb_export import media_bin, translate
    from drp import writer
    from presets import _RECURSO
    from titulos import resolve as titulos_resolve

    for nome, caminho in (("painel.html", DIR_UI / "painel.html"), ("painel.js", DIR_UI / "painel.js"),
                          ("preset_aaf.xml", _RECURSO), ("formas_r19.xml", writer.FORMAS_NATIVAS),
                          ("formas_r21_arriraw.xml", writer.FORMA_ARRIRAW),
                          ("formas_r21_still.xml", writer.FORMA_STILL), ("mesa99", writer.MESA_99),
                          ("texto_r19.xml", titulos_resolve.FORMA),
                          ("skeleton_r19.drt", os.path.join(os.path.dirname(writer.FORMAS_NATIVAS),
                                                            writer.ESQUELETO_PADRAO)),
                          ("templates.json", translate.TEMPLATES_JSON),
                          ("manifest.json", os.path.join(media_bin.TEMPLATE_DIR, "manifest.json")),
                          ("plugin do Media Composer", integracoes.fonte_mc() / "avid-manifest.json"),
                          ("plugin do Resolve", integracoes.fonte_resolve() / "main.js")):
        itens[nome] = os.path.isfile(caminho)
    for modulo in ("cv2", "numpy", "aaf2", "avb", "imagehash", "grpc") + (("dialogo_win",) if os.name == "nt" else ()):
        try:
            __import__(modulo)
            itens[modulo] = True
        except Exception:
            itens[modulo] = False
    try:
        from pymediainfo import MediaInfo

        itens["mediainfo"] = bool(MediaInfo.can_parse())
    except Exception:
        itens["mediainfo"] = False
    import subprocess as _sp

    from media import fftools

    for nome, exe in (("ffmpeg", fftools.ffmpeg()), ("ffprobe", fftools.ffprobe())):
        try:
            r = _sp.run([exe, "-version"], capture_output=True, timeout=30, **fftools.SEM_JANELA)
            itens[nome] = r.returncode == 0 and b"version" in r.stdout
        except (OSError, TypeError, _sp.SubprocessError):
            itens[nome] = False
    print(_json.dumps({"versao": VERSAO, "itens": itens}, ensure_ascii=False))
    return 0 if all(itens.values()) else 1


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == [ARG_DIALOGO]:
        import dialogo_win

        return dialogo_win.main(argv[1:])
    if argv[:1] == ["--conferir-pacote"]:
        return conferir_pacote()
    ap = argparse.ArgumentParser(description="Serviço do Media Composer — De Lá Pra Cá")
    ap.add_argument("--porta", type=int, default=PORTA)
    ap.add_argument("--log", default="info", choices=["debug", "info", "warning", "error"])
    ap.add_argument("--sair-com-o-mc", dest="vigiar", action="store_true", default=True,
                    help="encerra o serviço quando o Media Composer fechar (padrão)")
    ap.add_argument("--nao-sair-com-o-mc", dest="vigiar", action="store_false",
                    help="mantém o serviço no ar mesmo sem o Media Composer")
    ap.add_argument("--carencia", type=float, default=60.0,
                    help="segundos de ausência do MC antes de encerrar (padrão: 60)")
    ap.add_argument("--liberar-porta", action="store_true",
                    help="mata o gateway órfão do Panel SDK e sai (imprime JSON)")
    ap.add_argument("--todos", action="store_true",
                    help="com --liberar-porta: mata qualquer gateway, não só o órfão")
    ap.add_argument("--app", dest="app", default=None,
                    help="caminho do executável do aplicativo que subiu este serviço")
    ap.add_argument("--pai", type=int, default=None,
                    help="PID do aplicativo que subiu este serviço: ele fechou, o serviço encerra")
    ap.add_argument("--bancada", action="store_true",
                    help="motor da bancada de desenvolvimento (sem o aplicativo)")
    ap.add_argument("--receptor", action="store_true",
                    help="modo de quem recebe (Resolve): sem vigia do MC, encerra ocioso")
    ap.add_argument("--ocioso", type=float, default=30 * 60.0,
                    help="com --receptor: segundos sem uso antes de encerrar (padrão: 1800)")
    args = ap.parse_args(argv)

    if args.liberar_porta:
        import json as _json

        print(_json.dumps(plataforma.liberar_gateway(todos=args.todos),
                          ensure_ascii=False))
        return 0

    novas = plataforma.completar_path()
    origem = ("receptor do Resolve" if args.receptor else "bancada" if args.bancada
              else "aplicativo" if args.app else "serviço")
    _configurar_log(args.log, origem)
    log.info("De Lá Pra Cá %s — serviço do Media Composer", VERSAO)
    if novas:
        log.info("PATH completado com %s (processo lançado por app)", ", ".join(novas))
    from media import fftools as _fftools
    if not _fftools.ffprobe():
        log.warning("ffprobe NÃO encontrado no PATH (%s): sem ele, TC e duração das mídias não se "
                    "leem e a timeline sai offline", os.environ.get("PATH", ""))

    servico = Servico()
    servico.bancada = bool(args.bancada)
    if args.app:
        if Path(args.app).is_file():
            servico.app_exe = args.app
            log.info("aplicativo que me subiu: %s", args.app)
            try:
                import json as _json
                (plataforma.pasta_dados() / "aplicativo.json").write_text(
                    _json.dumps({"executavel": str(Path(args.app).resolve())}), encoding="utf-8")
            except OSError as e:
                log.warning("não gravei aplicativo.json: %s", e)
        else:
            log.warning("--app aponta para um arquivo que não existe: %s", args.app)
            log.warning("a revisão vai abrir dentro do painel, não em janela própria")

    if servico.app_exe:
        try:
            import configuracoes
            import integracoes

            if not configuracoes.ler()["convite_integracoes_visto"] and integracoes.tudo_em_dia():
                configuracoes.gravar({"convite_integracoes_visto": True})
                log.info("plugins já em dia: o convite da 1ª abertura conta como visto")
        except Exception:
            log.debug("não conferi o convite da 1ª abertura", exc_info=True)

    try:
        servidor, _ = iniciar(servico, args.porta)
    except OSError as e:
        log.error("não consegui abrir a porta %d: %s", args.porta, e)
        log.error("outra instância do serviço já está rodando?")
        return 1

    try:
        import token_local

        servico.token = token_local.publicar()
    except OSError:
        log.warning("não consegui publicar o token do receptor — o Resolve não vai "
                    "conseguir falar com este serviço", exc_info=True)

    parar = threading.Event()
    servico.parar = parar
    signal.signal(signal.SIGINT, lambda *_: parar.set())
    signal.signal(signal.SIGTERM, lambda *_: parar.set())

    servico.receptor = bool(args.receptor)
    if args.receptor:
        args.vigiar = False
        threading.Thread(target=vigiar_ociosidade, args=(servico, parar, args.ocioso),
                         name="delapraca-ocioso", daemon=True).start()
        log.info("modo receptor: encerra depois de %.0f min sem uso", args.ocioso / 60)

    if servico.app_exe and args.vigiar:
        args.vigiar = False
        log.info("subido pelo aplicativo: o serviço vive enquanto ele estiver aberto")
    if servico.app_exe and args.pai:
        threading.Thread(target=vigiar_app, args=(servico, parar, args.pai),
                         name="delapraca-app", daemon=True).start()
        log.info("vigiando o aplicativo (pid %d): fechou, o serviço encerra", args.pai)

    if args.vigiar:
        threading.Thread(target=vigiar_media_composer,
                         args=(servico, parar, args.carencia),
                         name="delapraca-vigia", daemon=True).start()
        log.info("o serviço encerra sozinho %.0fs depois de o Media Composer fechar",
                 args.carencia)

    log.info("painel: http://127.0.0.1:%d/painel", args.porta)
    try:
        parar.wait()
    finally:
        log.info("encerrando")
        servidor.shutdown()
        servidor.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
