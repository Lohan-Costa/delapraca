from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

import plataforma

log = logging.getLogger("delapraca.token")

NOME = "token"


def caminho() -> Path:
    return plataforma.pasta_dados() / NOME


def publicar() -> str:
    token = secrets.token_hex(32)
    alvo = caminho()
    alvo.parent.mkdir(parents=True, exist_ok=True)
    try:
        alvo.unlink()
    except FileNotFoundError:
        pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(alvo, flags, 0o600)
    try:
        os.write(fd, token.encode("ascii"))
    finally:
        os.close(fd)
    log.info("token do receptor publicado em %s", alvo)
    return token
