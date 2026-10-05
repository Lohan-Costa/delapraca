from __future__ import annotations

import json
import logging
import mimetypes
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .portao import CABECALHO_TOKEN, PORTA, ROTA_UI, portao

log = logging.getLogger("delapraca.api")

DIR_UI = Path(__file__).resolve().parent.parent / "ui"

_ROTAS: dict[tuple[str, str], callable] = {}


def rota(metodo: str, caminho: str):

    def registrar(fn):
        _ROTAS[(metodo.upper(), caminho)] = fn
        return fn

    return registrar


class Contexto:

    __slots__ = ("metodo", "rota", "query", "corpo", "servico")

    def __init__(self, metodo, rota, query, corpo, servico):
        self.metodo = metodo
        self.rota = rota
        self.query = query
        self.corpo = corpo
        self.servico = servico

    def json(self) -> dict:
        if not self.corpo:
            return {}
        try:
            valor = json.loads(self.corpo)
        except (ValueError, UnicodeDecodeError):
            return {}
        return valor if isinstance(valor, dict) else {}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DeLaPraCa"
    sys_version = ""

    def log_message(self, formato, *args):
        log.debug("%s %s", self.address_string(), formato % args)

    def _responder(self, status: int, corpo: bytes, tipo: str, *, cors: bool = True):
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        if cors:
            origem = self.headers.get("Origin")
            if origem and origem.strip().lower() == "avpi://":
                self.send_header("Access-Control-Allow-Origin", "avpi://")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "sameorigin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; frame-ancestors 'self' avpi:; "
            "base-uri 'none'; form-action 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)

    def _json(self, status: int, valor, *, cors: bool = True):
        corpo = json.dumps(valor, ensure_ascii=False).encode("utf-8")
        self._responder(status, corpo, "application/json; charset=utf-8", cors=cors)

    def do_OPTIONS(self):
        self._responder(204, b"", "text/plain")

    def do_GET(self):
        self._atender("GET")

    def do_POST(self):
        self._atender("POST")

    def _atender(self, metodo: str):
        url = urlparse(self.path)
        caminho = url.path

        tamanho = self.headers.get("Content-Length")
        corpo_len = int(tamanho) if (tamanho or "").isdigit() else None

        recusa = portao(
            host=self.headers.get("Host"),
            origin=self.headers.get("Origin"),
            sec_fetch_site=self.headers.get("Sec-Fetch-Site"),
            corpo_len=corpo_len,
            rota=caminho,
            token=self.headers.get(CABECALHO_TOKEN),
            token_esperado=getattr(self.server.servico, "token", None),
        )
        if recusa:
            motivo, status = recusa
            log.info("recusado %s %s: %s", metodo, caminho, motivo)
            self._json(status, {"erro": motivo}, cors=(motivo != "origem não permitida"))
            return

        corpo = self.rfile.read(corpo_len) if corpo_len else b""

        handler = _ROTAS.get((metodo, caminho))
        if handler is None:
            self._json(404, {"erro": "rota desconhecida"})
            return

        ctx = Contexto(metodo, caminho, url.query, corpo, self.server.servico)
        try:
            resultado = handler(ctx)
        except Exception as e:
            log.exception("erro em %s %s", metodo, caminho)
            self._json(500, {"erro": f"{type(e).__name__}: {e}"})
            return

        if resultado and resultado[0] == "sse":
            self._sse(resultado[1])
        elif len(resultado) == 3:
            status, dados, tipo = resultado
            self._responder(status, dados, tipo)
        else:
            status, valor = resultado
            self._json(status, valor)

    def _sse(self, ler_estado, intervalo: float = 0.4, teto_s: float = 600.0):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        limite = time.monotonic() + teto_s
        anterior = None
        try:
            while time.monotonic() < limite:
                estado = ler_estado()
                chave = {k: v for k, v in estado.items() if k != "decorrido"}
                if chave != anterior:
                    anterior = chave
                    corpo = json.dumps(estado, ensure_ascii=False)
                    self.wfile.write(f"data: {corpo}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    if estado.get("fase") in ("feito", "erro") and not estado.get("ocupada"):
                        break
                time.sleep(intervalo)
        except (BrokenPipeError, ConnectionResetError):
            log.debug("cliente SSE desconectou")


class _Servidor(ThreadingHTTPServer):
    daemon_threads = True

    allow_reuse_address = sys.platform != "win32"

    def __init__(self, endereco, handler, servico):
        self.servico = servico
        super().__init__(endereco, handler)

    def handle_error(self, request, client_address):
        import sys
        excecao = sys.exc_info()[1]
        if isinstance(excecao, (ConnectionResetError, BrokenPipeError,
                                ConnectionAbortedError)):
            log.debug("cliente %s desconectou (%s)", client_address[0],
                      type(excecao).__name__)
            return
        super().handle_error(request, client_address)

    def server_bind(self):
        import socketserver

        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def servir_arquivo_da_ui(nome: str) -> tuple[int, bytes, str]:
    alvo = (DIR_UI / nome).resolve()
    if not alvo.is_relative_to(DIR_UI.resolve()) or not alvo.is_file():
        return 404, b'{"erro":"nao encontrado"}', "application/json; charset=utf-8"
    tipo = mimetypes.guess_type(alvo.name)[0] or "application/octet-stream"
    if tipo.startswith(("text/", "application/javascript")):
        tipo += "; charset=utf-8"
    return 200, alvo.read_bytes(), tipo


def iniciar(servico, porta: int = PORTA) -> tuple[_Servidor, threading.Thread]:
    from . import rotas

    servidor = _Servidor(("127.0.0.1", porta), _Handler, servico)
    thread = threading.Thread(target=servidor.serve_forever, name="http", daemon=True)
    thread.start()
    log.info("servidor no ar em http://127.0.0.1:%d (UI em %s)", porta, ROTA_UI)
    return servidor, thread
