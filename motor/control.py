import signal
import threading

_evt = threading.Event()


class Cancelled(Exception):
    pass


def install() -> None:
    try:
        signal.signal(signal.SIGUSR1, lambda *_: _evt.set())
    except (ValueError, OSError, AttributeError):
        pass


def begin() -> None:
    _evt.clear()


def cancelled() -> bool:
    return _evt.is_set()


def check() -> None:
    if _evt.is_set():
        raise Cancelled()
