from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("delapraca.plataforma")

E_WINDOWS = sys.platform == "win32"
E_MACOS = sys.platform == "darwin"

SEM_JANELA: dict = {"creationflags": 0x08000000} if E_WINDOWS else {}


PASTAS_DE_INSTALADORES_MAC = ("/opt/homebrew/bin", "/usr/local/bin")


def completar_path() -> list[str]:
    if not E_MACOS:
        return []
    atual = os.environ.get("PATH", "").split(os.pathsep)
    novas = [p for p in PASTAS_DE_INSTALADORES_MAC if p not in atual and os.path.isdir(p)]
    if novas:
        os.environ["PATH"] = os.pathsep.join([*atual, *novas]).strip(os.pathsep)
    return novas


def versao() -> str:
    raiz = (Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[3])
    try:
        return (raiz / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def pasta_dados() -> Path:
    return _dados_padrao()


def _dados_padrao() -> Path:
    if E_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
        return Path(base) / "DeLaPraCa"
    if E_MACOS:
        return Path.home() / "Library/Application Support/DeLaPraCa"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "DeLaPraCa"


def _dados_desviados() -> Path | None:
    d = pasta_dados()
    return d if d != _dados_padrao() else None


def pasta_cache_padrao() -> Path:
    desviada = _dados_desviados()
    if desviada is not None:
        return desviada / "Cache"
    if E_MACOS:
        return Path.home() / "Library/Caches/DeLaPraCa"
    if E_WINDOWS:
        return pasta_dados() / "Cache"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "DeLaPraCa"


def pasta_logs() -> Path:
    desviada = _dados_desviados()
    if desviada is not None:
        return desviada / "Logs"
    if E_MACOS:
        return Path.home() / "Library/Logs/DeLaPraCa"
    return pasta_dados() / "Logs"


def pasta_trabalho() -> Path:
    import cache

    return cache.sub("trabalho")


def raiz_resolve_usuario() -> Path:
    if E_WINDOWS:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
        return Path(base) / "Blackmagic Design" / "DaVinci Resolve" / "Support"
    return Path.home() / "Library/Application Support/Blackmagic Design/DaVinci Resolve"


def raiz_scripts_resolve() -> Path:
    return raiz_resolve_usuario() / "Fusion" / "Scripts" / "Utility"


def raiz_workflow_integrations() -> Path:
    if E_WINDOWS:
        base = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        return Path(base) / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Workflow Integration Plugins"
    return Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins")


def origem_workflow_integration_node() -> Path:
    if E_WINDOWS:
        base = Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData") / "Blackmagic Design" / "DaVinci Resolve" / "Support"
    else:
        base = Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve")
    return base / "Developer" / "Workflow Integrations" / "Examples" / "SamplePlugin" / "WorkflowIntegration.node"


def raiz_paineis() -> Path:
    if E_WINDOWS:
        base = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        return Path(base) / "Avid" / "PanelSDKPlugins"
    return Path("/Library/Application Support/Avid/PanelSDKPlugins")


def raiz_settings_avid() -> Path | None:
    candidatos = (
        [Path(r"C:\Users\Public\Documents\Avid Media Composer\Avid Users")]
        if E_WINDOWS else
        [Path("/Users/Shared/AvidMediaComposer/Avid Users")]
    )
    for c in candidatos:
        if c.is_dir():
            return c
    return None


def executavel_mc() -> Path | None:
    if E_WINDOWS:
        p = Path(r"C:\Program Files\Avid\Avid Media Composer\AvidMediaComposer.exe")
    else:
        p = Path("/Applications/Avid Media Composer/AvidMediaComposer.app"
                 "/Contents/MacOS/AvidMediaComposer")
    return p if p.exists() else None


NOME_PROC_MC = "AvidMediaComposer.exe" if E_WINDOWS else "AvidMediaComposer"
NOME_PROC_GATEWAY = "avid-api-gateway.exe" if E_WINDOWS else "avid-api-gateway"


def processos() -> list[tuple[int, int, str]]:
    if E_WINDOWS:
        return _processos_windows()
    try:
        r = subprocess.run(["ps", "-axo", "pid=,ppid=,comm="],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return []
    fora = []
    for linha in (r.stdout or "").splitlines():
        partes = linha.split(None, 2)
        if len(partes) == 3 and partes[0].isdigit() and partes[1].isdigit():
            fora.append((int(partes[0]), int(partes[1]), partes[2].strip()))
    return fora


def _processos_windows() -> list[tuple[int, int, str]]:
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID = ctypes.c_void_p(-1).value

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID:
        log.debug("CreateToolhelp32Snapshot falhou")
        return []
    try:
        entrada = PROCESSENTRY32W()
        entrada.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not k32.Process32FirstW(snap, ctypes.byref(entrada)):
            return []
        fora = []
        while True:
            fora.append((int(entrada.th32ProcessID),
                         int(entrada.th32ParentProcessID),
                         entrada.szExeFile))
            if not k32.Process32NextW(snap, ctypes.byref(entrada)):
                break
        return fora
    finally:
        k32.CloseHandle(snap)


def media_composer_aberto() -> bool:
    alvo = NOME_PROC_MC.lower()
    return any(nome.lower() == alvo or nome.lower().endswith("/" + alvo)
               for _, _, nome in processos())


def gateways_orfaos() -> list[int]:
    procs = processos()
    vivos = {pid for pid, _, _ in procs}
    alvo = NOME_PROC_GATEWAY.lower()
    fora = []
    for pid, ppid, nome in procs:
        base = nome.lower().rsplit("/", 1)[-1]
        if base != alvo and alvo not in base:
            continue
        orfao = (ppid == 1) if E_MACOS else (ppid not in vivos)
        if orfao:
            fora.append(pid)
    return fora


def raiz_do_volume(drive: str) -> Path | None:
    drive = (drive or "").strip()
    if not drive:
        return None

    if not E_WINDOWS:
        p = Path("/Volumes") / drive
        return p if p.is_dir() else None

    letra = letra_do_drive(drive)
    if letra:
        raiz = Path(f"{letra}:\\")
        if raiz.is_dir():
            return raiz

    for letra, nome in volumes_montados().items():
        if nome and nome.casefold() == rotulo_do_drive(drive).casefold():
            return Path(f"{letra}:\\")
    return None


def letra_do_drive(drive: str) -> str | None:
    import re

    drive = (drive or "").strip()
    if not drive:
        return None
    m = re.search(r"\(([A-Za-z]):\)\s*$", drive)
    if m:
        return m.group(1).upper()
    cru = drive.rstrip("\\/")
    if re.fullmatch(r"[A-Za-z]:?", cru):
        return cru[0].upper()
    return None


def rotulo_do_drive(drive: str) -> str:
    import re

    return re.sub(r"\s*\([A-Za-z]:\)\s*$", "", (drive or "").strip()).strip()


def volumes_montados() -> dict[str, str]:
    if not E_WINDOWS:
        return {}
    import ctypes

    k32 = ctypes.windll.kernel32
    anterior = k32.SetErrorMode(0x0001)
    try:
        montadas = k32.GetLogicalDrives()
        buf = ctypes.create_unicode_buffer(261)
        fora: dict[str, str] = {}
        for i in range(26):
            if not montadas & (1 << i):
                continue
            letra = chr(ord("A") + i)
            raiz = ctypes.c_wchar_p(f"{letra}:\\")
            if k32.GetVolumeInformationW(raiz, buf, 261, None, None, None, None, 0):
                fora[letra] = buf.value
            else:
                fora[letra] = ""
        return fora
    finally:
        k32.SetErrorMode(anterior)


def abrir_url(url: str) -> None:
    if E_WINDOWS:
        os.startfile(url)
        return
    if E_MACOS:
        subprocess.run(["open", url], timeout=10, check=False)
        return
    subprocess.run(["xdg-open", url], timeout=10, check=False)


def abrir_pasta(pasta: Path) -> None:
    if not pasta.is_dir():
        raise FileNotFoundError(str(pasta))
    if E_WINDOWS:
        os.startfile(str(pasta))
        return
    if E_MACOS:
        subprocess.run(["open", str(pasta)], timeout=10, check=False)
        return
    subprocess.run(["xdg-open", str(pasta)], timeout=10, check=False)


def matar_processo(pid: int, forcado: bool = False) -> None:
    if E_WINDOWS:
        import ctypes

        PROCESS_TERMINATE = 0x0001
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if h:
            try:
                ctypes.windll.kernel32.TerminateProcess(h, 1)
            finally:
                ctypes.windll.kernel32.CloseHandle(h)
        return
    import signal as _signal

    os.kill(pid, _signal.SIGKILL if forcado else _signal.SIGTERM)


def liberar_gateway(todos: bool = False) -> dict:
    import time as _time

    alvo = NOME_PROC_GATEWAY.lower()

    def gateways_vivos() -> list[int]:
        return [pid for pid, _pai, nome in processos()
                if alvo in nome.lower().rsplit("/", 1)[-1]]

    pids = gateways_vivos() if todos else gateways_orfaos()
    resultado = {"mortos": [], "restantes": [], "mc_aberto": media_composer_aberto(),
                 "alvos": list(pids)}
    if not pids:
        return resultado

    for pid in pids:
        try:
            matar_processo(pid)
        except Exception:
            pass
    _time.sleep(1.0)

    teimosos = [p for p in gateways_vivos() if p in pids]
    for pid in teimosos:
        try:
            matar_processo(pid, forcado=True)
        except Exception:
            pass
    if teimosos:
        _time.sleep(1.0)

    restantes = gateways_vivos()
    resultado["restantes"] = restantes
    resultado["mortos"] = [p for p in pids if p not in restantes]
    return resultado
