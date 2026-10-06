from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request

import configuracoes
import plataforma

log = logging.getLogger("delapraca.versao")

REPO = "Lohan-Costa/delapraca"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
_URL = re.compile(r"https://github\.com/Lohan-Costa/delapraca/releases/tag/v\d{1,4}\.\d{1,4}\.\d{1,6}")
_VERSAO = re.compile(r"v?(\d{1,4})\.(\d{1,4})\.(\d{1,6})")

INTERVALO_S = 6 * 3600
REPETIR_ERRO_S = 10 * 60
ARQUIVO = "versao.json"

_trava = threading.Lock()
_ultimo: dict | None = None


def numeros(v) -> tuple[int, int, int] | None:
    m = _VERSAO.fullmatch(v.strip()) if isinstance(v, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


def mais_nova(remota: str, atual: str) -> bool:
    r, a = numeros(remota), numeros(atual)
    return bool(r and a and r > a)


def url_confiavel(url) -> bool:
    return isinstance(url, str) and _URL.fullmatch(url) is not None


def _consultar(atual: str, abrir=None) -> dict:
    pedido = urllib.request.Request(API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"delapraca/{atual}"})
    try:
        with (abrir or urllib.request.urlopen)(pedido, timeout=5) as r:
            dado = json.loads(r.read(256_000))
    except (OSError, ValueError) as e:
        log.info("versão nova: não consegui conferir (%s)", e)
        return {"estado": "erro"}
    tag = dado.get("tag_name") if isinstance(dado, dict) else None
    url = dado.get("html_url") if isinstance(dado, dict) else None
    if not numeros(tag) or not url_confiavel(url) or dado.get("draft") or dado.get("prerelease"):
        log.warning("versão nova: resposta inesperada do GitHub (tag %r, url %r)", tag, url)
        return {"estado": "erro"}
    if mais_nova(tag, atual):
        return {"estado": "nova", "versao": ".".join(map(str, numeros(tag))), "url": url}
    return {"estado": "em_dia"}


def _gravar(e: dict) -> None:
    try:
        (plataforma.pasta_dados() / ARQUIVO).write_text(
            json.dumps({k: e.get(k) for k in ("estado", "versao", "url")}), encoding="utf-8")
    except OSError as erro:
        log.debug("não gravei %s: %s", ARQUIVO, erro)


def estado(forcar: bool = False, abrir=None) -> dict:
    global _ultimo
    atual = plataforma.versao()
    if not configuracoes.ler()["avisar_versao_nova"]:
        _gravar({"estado": "desligado"})
        return {"atual": atual, "avisar": False, "estado": "desligado"}
    with _trava:
        e = _ultimo
        prazo = REPETIR_ERRO_S if e and e["estado"] == "erro" else INTERVALO_S
        if forcar or e is None or e.get("atual") != atual or time.time() - e["conferido"] >= prazo:
            e = {**_consultar(atual, abrir), "atual": atual, "conferido": time.time()}
            _ultimo = e
            _gravar(e)
            if e["estado"] == "nova":
                log.info("versão nova disponível: %s (instalada %s)", e["versao"], atual)
    return {"atual": atual, "avisar": True, **{k: v for k, v in e.items() if k in ("estado", "versao", "url")}}


def vigiar(parar: threading.Event) -> None:
    while True:
        try:
            e = estado()
            espera = REPETIR_ERRO_S if e["estado"] == "erro" else INTERVALO_S
        except Exception:
            log.debug("versão nova: a conferência falhou", exc_info=True)
            espera = REPETIR_ERRO_S
        if parar.wait(espera):
            return
