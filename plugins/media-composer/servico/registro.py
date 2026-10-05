from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import shutil
import sys
from datetime import datetime
from pathlib import Path

import plataforma

TETO_BYTES = 4 * 1024 * 1024
GUARDADOS = 4
NOME = "delapraca.log"
FORMATO = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_configurado: Path | None = None


class _RotativoTolerante(logging.handlers.RotatingFileHandler):

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except OSError:
            if self.stream is None:
                try:
                    self.stream = self._open()
                except OSError:
                    pass


def caminho(pasta: Path | None = None) -> Path:
    return Path(pasta or plataforma.pasta_logs()) / NOME


def configurar(origem: str, versao: str, pasta: Path | None = None) -> Path:
    global _configurado
    if _configurado is not None:
        return _configurado
    alvo = caminho(pasta)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    h = _RotativoTolerante(alvo, maxBytes=TETO_BYTES, backupCount=GUARDADOS, encoding="utf-8")
    h.setFormatter(logging.Formatter(FORMATO))
    raiz = logging.getLogger()
    raiz.addHandler(h)
    raiz.setLevel(min(raiz.level, logging.INFO))
    _configurado = alvo
    for linha in cabecalho(origem, versao):
        logging.getLogger("delapraca").info(linha)
    return alvo


def _ffprobe() -> str:
    try:
        from media import fftools

        return fftools.ffprobe() or "(NÃO encontrado)"
    except Exception:
        return shutil.which("ffprobe") or "(NÃO encontrado)"


def cabecalho(origem: str, versao: str) -> list[str]:
    import cache

    try:
        pasta_cache = cache.raiz()
    except Exception:
        pasta_cache = "?"
    return [
        "=" * 72,
        f"De Lá Pra Cá {versao} — nova execução ({origem}) em "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  SO: {platform.system()} {platform.release()} ({platform.machine()}) · "
        f"Python {platform.python_version()} · PID {os.getpid()}"
        f"{' · empacotado' if getattr(sys, 'frozen', False) else ''}",
        f"  ffprobe={_ffprobe()}",
        f"  dados={plataforma.pasta_dados()} · cache={pasta_cache}",
        "=" * 72,
    ]
