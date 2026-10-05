from __future__ import annotations

import io
import json
import logging
import shutil
import sys
import zipfile
from pathlib import Path

import plataforma

log = logging.getLogger("delapraca.integracoes")

NOME_AVPI = "com.ciclomedia.delapraca.mc.avpi"
ID_CASCA = "com.ciclomedia.delapraca"
ARQUIVOS_CASCA = ("manifest.xml", "package.json", "main.js", "preload.js", "carregando.html")
ID_SPIKE = "com.ciclomedia.delapraca.spike"
SCRIPT_RESOLVE_ANTIGO = "delapraca_receber.lua"
IGNORAR = {".DS_Store", "Thumbs.db", "desktop.ini", "version.js"}
NOME_PROC_RESOLVE = "resolve.exe" if plataforma.E_WINDOWS else "resolve"


class Impedido(Exception):
    pass


def _raiz_fontes() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", "")) / "integracoes"
    return Path(__file__).resolve().parents[2]


def fonte_mc() -> Path:
    return _raiz_fontes() / "media-composer" / "painel"


def fonte_resolve() -> Path:
    return _raiz_fontes() / "resolve" / "casca"


def versao_do_app() -> str:
    return plataforma.versao()


def avpi(versao: str) -> bytes:
    fonte = fonte_mc()
    manifesto = json.loads((fonte / "avid-manifest.json").read_text(encoding="utf-8"))
    manifesto["version"] = versao
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("avid-manifest.json", json.dumps(manifesto, indent=2, ensure_ascii=False) + "\n")
        z.writestr("static/version.js", f"window.DELAPRACA_VERSAO={json.dumps(versao)};\n")
        for p in sorted(fonte.rglob("*")):
            nome = p.relative_to(fonte).as_posix()
            if (not p.is_file() or p.name in IGNORAR or p.name.startswith("._")
                    or nome == "avid-manifest.json"):
                continue
            z.write(p, nome)
    return buf.getvalue()


def _destino_mc() -> Path:
    return plataforma.raiz_paineis() / NOME_AVPI


def _versao_mc() -> str | None:
    try:
        with zipfile.ZipFile(_destino_mc()) as z:
            return str(json.loads(z.read("avid-manifest.json")).get("version") or "?")
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None


def _mc_encontrado() -> bool:
    return plataforma.executavel_mc() is not None or plataforma.raiz_settings_avid() is not None


def instalar_mc(forcar: bool = False) -> dict:
    if not forcar and plataforma.media_composer_aberto():
        raise Impedido("Feche o Media Composer: ele só registra o painel quando abre.")
    pasta = plataforma.raiz_paineis()
    if not pasta.is_dir():
        if not _mc_encontrado():
            raise Impedido("Não encontrei o Media Composer nesta máquina.")
        try:
            pasta.mkdir(parents=True)
        except PermissionError:
            raise Impedido(f"Sem permissão para criar {pasta}. Peça a quem administra esta máquina.") from None
    destino = _destino_mc()
    tmp = destino.with_name(destino.name + ".parcial")
    tmp.write_bytes(avpi(versao_do_app()))
    tmp.replace(destino)
    gateway = {}
    if not plataforma.media_composer_aberto():
        gateway = plataforma.liberar_gateway()
    log.info("painel do Media Composer instalado em %s (gateway órfão encerrado: %s)",
             destino, gateway.get("mortos") or "nenhum")
    return {"destino": str(destino), "gateway_encerrado": bool(gateway.get("mortos"))}


def remover_mc() -> None:
    if plataforma.media_composer_aberto():
        raise Impedido("Feche o Media Composer antes de remover o painel.")
    _destino_mc().unlink(missing_ok=True)
    log.info("painel do Media Composer removido")


def _destino_resolve() -> Path:
    return plataforma.raiz_workflow_integrations() / ID_CASCA


def _versao_resolve() -> str | None:
    try:
        d = json.loads((_destino_resolve() / "package.json").read_text(encoding="utf-8"))
        return str(d.get("version") or "?")
    except (OSError, ValueError):
        return None


def _resolve_encontrado() -> bool:
    return plataforma.raiz_resolve_usuario().is_dir() or plataforma.origem_workflow_integration_node().is_file()


def resolve_aberto() -> bool:
    return any(nome.lower().rsplit("/", 1)[-1] == NOME_PROC_RESOLVE for _, _, nome in plataforma.processos())


def instalar_resolve(forcar: bool = False) -> dict:
    if not forcar and resolve_aberto():
        raise Impedido("Feche o DaVinci Resolve: ele só lê o plugin quando abre.")
    node = plataforma.origem_workflow_integration_node()
    destino = _destino_resolve()
    if not node.is_file() and not (destino / node.name).is_file():
        raise Impedido("Não encontrei o DaVinci Resolve Studio nesta máquina "
                       f"(falta {node.name}, que vem com ele).")
    fonte = fonte_resolve()
    destino.mkdir(parents=True, exist_ok=True)
    for nome in ARQUIVOS_CASCA:
        dado = (fonte / nome).read_bytes()
        if nome == "package.json":
            pacote = json.loads(dado)
            pacote["version"] = versao_do_app()
            dado = (json.dumps(pacote, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        (destino / nome).write_bytes(dado)
    if node.is_file():
        novo = node.read_bytes()
        alvo = destino / node.name
        if not alvo.is_file() or alvo.read_bytes() != novo:
            alvo.write_bytes(novo)
    raiz = plataforma.raiz_workflow_integrations()
    shutil.rmtree(raiz / ID_SPIKE, ignore_errors=True)
    try:
        (plataforma.raiz_scripts_resolve() / SCRIPT_RESOLVE_ANTIGO).unlink(missing_ok=True)
    except OSError:
        pass
    log.info("painel do Resolve instalado em %s", destino)
    return {"destino": str(destino)}


def remover_resolve() -> None:
    if resolve_aberto():
        raise Impedido("Feche o DaVinci Resolve antes de remover o plugin.")
    destino = _destino_resolve()
    if destino.exists():
        shutil.rmtree(destino)
    log.info("painel do Resolve removido")


def estado() -> list[dict]:
    app = versao_do_app()

    def item(id_, nome, encontrado, aberto, versao, em_breve=False) -> dict:
        instalado = versao is not None
        impedimento = ""
        if aberto:
            impedimento = f"Feche o {nome} para instalar, atualizar ou remover: ele só lê o plugin quando abre."
        elif not encontrado and not instalado:
            impedimento = f"Não encontrei o {nome} nesta máquina."
        return {"id": id_, "nome": nome, "em_breve": em_breve, "encontrado": encontrado, "aberto": aberto,
                "instalado": instalado, "versao": versao, "versao_app": app,
                "desatualizado": instalado and versao != app, "impedimento": impedimento}

    return [
        item("mc", "Media Composer", _mc_encontrado(), plataforma.media_composer_aberto(), _versao_mc()),
        item("resolve", "DaVinci Resolve", _resolve_encontrado(), resolve_aberto(), _versao_resolve()),
        {"id": "premiere", "nome": "Premiere Pro", "em_breve": True, "encontrado": False, "aberto": False,
         "instalado": False, "versao": None, "versao_app": app, "desatualizado": False, "impedimento": ""},
    ]


def tudo_em_dia(itens: list[dict] | None = None) -> bool:
    achados = [i for i in (itens if itens is not None else estado()) if i["encontrado"] and not i["em_breve"]]
    return bool(achados) and all(i["instalado"] and not i["desatualizado"] for i in achados)


ACOES = {"mc": (instalar_mc, remover_mc), "resolve": (instalar_resolve, remover_resolve)}
