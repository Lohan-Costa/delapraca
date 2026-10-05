from __future__ import annotations

import logging
import subprocess
import time

from plataforma import E_MACOS, E_WINDOWS

log = logging.getLogger("delapraca.teclado")

VK_CONTROL, VK_0, VK_C = 0x11, 0x30, 0x43
_KEYEVENTF_KEYUP = 0x0002
_INPUT_KEYBOARD = 1

PROCESSO_MC_WIN = "AvidMediaComposer.exe"
PROCESSO_MC_MAC = "Avid Media Composer"


class TecladoIndisponivel(RuntimeError):
    pass


def disponivel() -> bool:
    return E_WINDOWS or E_MACOS


def _estruturas():
    import ctypes
    import ctypes.wintypes as w

    PUL = ctypes.POINTER(ctypes.c_ulong)

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                    ("time", w.DWORD), ("dwExtraInfo", PUL)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
                    ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", PUL)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]

    class UNIAO(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", w.DWORD), ("u", UNIAO)]

    return INPUT, KEYBDINPUT


def _atalho_win(*vks) -> bool:
    import ctypes

    INPUT, KEYBDINPUT = _estruturas()
    eventos = [(v, False) for v in vks] + [(v, True) for v in reversed(vks)]
    pacote = (INPUT * len(eventos))()
    for i, (vk, solta) in enumerate(eventos):
        pacote[i].type = _INPUT_KEYBOARD
        pacote[i].ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=_KEYEVENTF_KEYUP if solta else 0,
                                  time=0, dwExtraInfo=None)
    return ctypes.windll.user32.SendInput(len(pacote), pacote, ctypes.sizeof(INPUT)) == len(eventos)


def _primeiro_plano_win() -> str:
    import ctypes
    import ctypes.wintypes as w

    hwnd = ctypes.windll.user32.GetForegroundWindow()
    pid = w.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        tam = w.DWORD(len(buf))
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(tam)):
            return buf.value.replace("/", "\\").rsplit("\\", 1)[-1]
        return ""
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def _osascript(*linhas: str) -> subprocess.CompletedProcess:
    argv = ["osascript"]
    for linha in linhas:
        argv += ["-e", linha]
    return subprocess.run(argv, capture_output=True, text=True, timeout=10)


def _primeiro_plano_mac() -> str:
    r = _osascript('tell application "System Events" to get name of first application '
                   'process whose frontmost is true')
    return (r.stdout or "").strip()


def _atalho_mac(tecla: str) -> bool:
    assert tecla in ("0", "c")
    r = _osascript(f'tell application "System Events" to keystroke "{tecla}" using command down')
    if r.returncode != 0:
        log.warning("osascript recusou (%s): %s", r.returncode, (r.stderr or "").strip())
    return r.returncode == 0


def em_primeiro_plano() -> str:
    try:
        return _primeiro_plano_win() if E_WINDOWS else _primeiro_plano_mac() if E_MACOS else ""
    except Exception:
        log.debug("não consegui saber a janela da frente", exc_info=True)
        return ""


def copiar_selecao(pausa: float = 0.3) -> None:
    if not disponivel():
        raise TecladoIndisponivel("coletar a seleção ainda não funciona neste sistema")
    frente = em_primeiro_plano()
    alvo = PROCESSO_MC_WIN if E_WINDOWS else PROCESSO_MC_MAC
    if alvo.lower() not in frente.lower():
        raise TecladoIndisponivel("o Media Composer não está na frente — clique na timeline e "
                                  "aperte Coletar de novo")
    if E_WINDOWS:
        if not _atalho_win(VK_CONTROL, VK_0):
            raise TecladoIndisponivel("o Windows recusou o atalho de focar a timeline")
        time.sleep(pausa)
        if not _atalho_win(VK_CONTROL, VK_C):
            raise TecladoIndisponivel("o Windows recusou o atalho de copiar")
    else:
        if not (_atalho_mac("0") and (time.sleep(pausa) or True) and _atalho_mac("c")):
            raise TecladoIndisponivel("o macOS recusou o atalho — dê ao De Lá Pra Cá a "
                                      "permissão de Acessibilidade (Ajustes › Privacidade)")
    time.sleep(pausa)
