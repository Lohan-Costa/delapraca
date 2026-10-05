import json
import time
import logging
import threading
from pathlib import Path

log = logging.getLogger("relinker.bench")

_enabled = False
_log_dir = None
_fh = None
_lock = threading.Lock()


def configure(log_dir) -> None:
    global _log_dir
    _log_dir = log_dir


def set_enabled(on: bool) -> None:
    global _enabled, _fh
    on = bool(on)
    with _lock:
        if on and _fh is None and _log_dir is not None:
            try:
                Path(_log_dir).mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                _fh = open(Path(_log_dir) / f"bench_{ts}.jsonl", "a", encoding="utf-8")
            except OSError as e:
                log.warning("Não foi possível abrir o arquivo de benchmark: %s", e)
        _enabled = on
    log.info("Benchmark %s", "LIGADO" if on else "desligado")


def enabled() -> bool:
    return _enabled


def record(event: str, elapsed_ms: float, **fields) -> None:
    if not _enabled:
        return
    rec = {"t": round(time.time(), 3), "event": event,
           "ms": round(float(elapsed_ms), 2), **fields}
    try:
        line = json.dumps(rec, ensure_ascii=False)
    except (TypeError, ValueError):
        line = json.dumps({"event": event, "ms": round(float(elapsed_ms), 2)})
    with _lock:
        if _fh is not None:
            try:
                _fh.write(line + "\n")
                _fh.flush()
            except OSError:
                pass
    log.info("[BENCH] %s", line)


class span:
    __slots__ = ("event", "fields", "_t")

    def __init__(self, event: str, **fields):
        self.event = event
        self.fields = fields
        self._t = 0.0

    def set(self, **fields):
        self.fields.update(fields)
        return self

    def __enter__(self):
        self._t = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        if _enabled:
            record(self.event, (time.perf_counter() - self._t) * 1000.0,
                   ok=(exc_type is None), **self.fields)
        return False
