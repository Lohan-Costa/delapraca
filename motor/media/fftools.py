from __future__ import annotations

import os
import shutil
import sys


def _empacotado() -> bool:
    return getattr(sys, "frozen", False)


def _resolve(env_var: str, name: str) -> str | None:
    for var in (env_var.replace("ORBITA_", "DLPC_"), env_var):
        embedded = os.environ.get(var)
        if embedded and os.path.isfile(embedded):
            return embedded

    if _empacotado():
        ao_lado = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                               name + (".exe" if sys.platform == "win32" else ""))
        return ao_lado if os.path.isfile(ao_lado) else None
    return shutil.which(name)


def ffmpeg() -> str:
    return _resolve("ORBITA_FFMPEG", "ffmpeg") or "ffmpeg"


def ffprobe() -> str | None:
    return _resolve("ORBITA_FFPROBE", "ffprobe")


def ffprobe_exe() -> str:
    return ffprobe() or "ffprobe"


SEM_JANELA: dict = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
