from __future__ import annotations

import json
import subprocess
import threading

from media import fftools
from media.fftools import SEM_JANELA

CAMPOS = ("stream=index,codec_type,codec_name,codec_tag_string,width,height,r_frame_rate,avg_frame_rate,nb_frames,"
          "duration,sample_rate,channels,duration_ts:stream_tags=timecode"
          ":format=duration:format_tags=timecode,time_reference")

LIMITE_S = 60

_CACHE: dict[str, dict] = {}
_TRAVA = threading.Lock()
leituras = 0


def ler(caminho: str) -> dict:
    global leituras
    if caminho in _CACHE:
        return _CACHE[caminho]
    with _TRAVA:
        leituras += 1
    try:
        out = subprocess.run([fftools.ffprobe_exe(), "-v", "error", "-show_entries", CAMPOS, "-of", "json",
                              caminho], capture_output=True, text=True, timeout=LIMITE_S, **SEM_JANELA)
        j = json.loads(out.stdout or "{}")
        if not isinstance(j, dict):
            j = {}
    except subprocess.TimeoutExpired:
        raise
    except (OSError, ValueError, subprocess.SubprocessError):
        j = {}
    _CACHE[caminho] = j
    return j


def primeiro(j: dict, tipo: str) -> dict:
    return next((s for s in j.get("streams") or [] if s.get("codec_type") == tipo), {})


def _razao(txt) -> float | None:
    num, _, den = str(txt or "").partition("/")
    try:
        n, d = float(num), float(den or 1)
    except ValueError:
        return None
    return n / d if n > 0 and d > 0 else None


def taxa_variavel(caminho: str) -> tuple[float, float] | None:
    v = primeiro(ler(caminho), "video")
    r, a = _razao(v.get("r_frame_rate")), _razao(v.get("avg_frame_rate"))
    if r and a and a < r * (1 - 1e-3):
        return r, a
    return None


def esquecer(caminho: str) -> None:
    _CACHE.pop(caminho, None)
