from __future__ import annotations

import argparse
import ctypes
import sys
from ctypes import POINTER, byref, c_int, c_uint, c_ulong, c_void_p, c_wchar_p

ole32 = ctypes.oledll.ole32
user32 = ctypes.windll.user32

S_OK = 0
E_CANCELADO = 0x800704C7

COINIT_APARTMENTTHREADED = 0x2
CLSCTX_INPROC_SERVER = 0x1

FOS_PICKFOLDERS = 0x00000020
FOS_FORCEFILESYSTEM = 0x00000040
FOS_ALLOWMULTISELECT = 0x00000200
FOS_PATHMUSTEXIST = 0x00000800

SIGDN_FILESYSPATH = 0x80058000

WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_POPUP = 0x80000000
SW_SHOWNA = 8


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _guid(texto: str) -> GUID:
    g = GUID()
    ole32.CLSIDFromString(c_wchar_p(texto), byref(g))
    return g


CLSID_FileOpenDialog = _guid("{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}")
IID_IFileOpenDialog = _guid("{D57C7288-D4AD-4768-BE02-9D969532D960}")

CLSID_FileSaveDialog = _guid("{C0B4E2F3-BA21-4773-8DBA-335EC946EB8B}")
IID_IFileSaveDialog = _guid("{84BCCD23-5FDE-4CDB-AEA4-AF64B83D78AB}")

_RELEASE = 2
_SHOW = 3
_SET_OPTIONS = 9
_GET_OPTIONS = 10
_SET_FOLDER = 12
_SET_FILE_NAME = 15
_SET_TITLE = 17
_SET_OK_LABEL = 18
_GET_RESULT = 20
_GET_RESULTS = 27
_SI_GET_DISPLAY_NAME = 5
_SIA_GET_COUNT = 7
_SIA_GET_ITEM_AT = 8


def _metodo(ponteiro, indice, restype, *argtypes):
    vtable = ctypes.cast(ponteiro, POINTER(POINTER(c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtable[indice])


def _soltar(ponteiro) -> None:
    if ponteiro:
        _metodo(ponteiro, _RELEASE, c_ulong)(ponteiro)


def _dono() -> int:
    hwnd = user32.CreateWindowExW(
        WS_EX_TOPMOST | WS_EX_TOOLWINDOW, "STATIC", None, WS_POPUP,
        0, 0, 1, 1, None, None, None, None)
    if hwnd:
        user32.ShowWindow(hwnd, SW_SHOWNA)
        user32.SetForegroundWindow(hwnd)
    return hwnd


def conferir() -> int:
    ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    dlg = c_void_p()
    ole32.CoCreateInstance(byref(CLSID_FileOpenDialog), None, CLSCTX_INPROC_SERVER,
                           byref(IID_IFileOpenDialog), byref(dlg))
    try:
        _preparar(dlg, "conferência")
        opcoes = c_ulong()
        _metodo(dlg, _GET_OPTIONS, c_int, POINTER(c_ulong))(dlg, byref(opcoes))
        return opcoes.value
    finally:
        _soltar(dlg)


def conferir_salvar() -> int:
    ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    dlg = c_void_p()
    ole32.CoCreateInstance(byref(CLSID_FileSaveDialog), None, CLSCTX_INPROC_SERVER,
                           byref(IID_IFileSaveDialog), byref(dlg))
    try:
        opcoes = c_ulong()
        _metodo(dlg, _GET_OPTIONS, c_int, POINTER(c_ulong))(dlg, byref(opcoes))
        _metodo(dlg, _SET_OPTIONS, c_int, c_ulong)(
            dlg, opcoes.value | FOS_FORCEFILESYSTEM | FOS_OVERWRITEPROMPT)
        _metodo(dlg, _SET_FILE_NAME, c_int, c_wchar_p)(dlg, "conferência")
        _metodo(dlg, _SET_TITLE, c_int, c_wchar_p)(dlg, "conferência")
        conferido = c_ulong()
        _metodo(dlg, _GET_OPTIONS, c_int, POINTER(c_ulong))(dlg, byref(conferido))
        return conferido.value
    finally:
        _soltar(dlg)


def _preparar(dlg, titulo: str) -> None:
    opcoes = c_ulong()
    _metodo(dlg, _GET_OPTIONS, c_int, POINTER(c_ulong))(dlg, byref(opcoes))
    _metodo(dlg, _SET_OPTIONS, c_int, c_ulong)(
        dlg, opcoes.value | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM
        | FOS_PATHMUSTEXIST | FOS_ALLOWMULTISELECT)
    _metodo(dlg, _SET_TITLE, c_int, c_wchar_p)(dlg, titulo)
    _metodo(dlg, _SET_OK_LABEL, c_int, c_wchar_p)(dlg, "Usar estas pastas")


def escolher_pastas(titulo: str) -> list[str]:
    ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    dlg = c_void_p()
    ole32.CoCreateInstance(byref(CLSID_FileOpenDialog), None, CLSCTX_INPROC_SERVER,
                           byref(IID_IFileOpenDialog), byref(dlg))

    hwnd = _dono()
    try:
        _preparar(dlg, titulo)

        hr = _metodo(dlg, _SHOW, c_int, c_void_p)(dlg, hwnd or None) & 0xFFFFFFFF
        if hr == E_CANCELADO:
            return []
        if hr != S_OK:
            raise OSError(f"o seletor devolveu 0x{hr:08x}")

        arr = c_void_p()
        _metodo(dlg, _GET_RESULTS, c_int, POINTER(c_void_p))(dlg, byref(arr))
        try:
            quantas = c_uint()
            _metodo(arr, _SIA_GET_COUNT, c_int, POINTER(c_uint))(arr, byref(quantas))
            caminhos = []
            for i in range(quantas.value):
                item = c_void_p()
                _metodo(arr, _SIA_GET_ITEM_AT, c_int, c_uint, POINTER(c_void_p))(
                    arr, i, byref(item))
                try:
                    nome = c_wchar_p()
                    _metodo(item, _SI_GET_DISPLAY_NAME, c_int, c_int,
                            POINTER(c_wchar_p))(item, SIGDN_FILESYSPATH, byref(nome))
                    if nome.value:
                        caminhos.append(nome.value)
                    ole32.CoTaskMemFree(nome)
                finally:
                    _soltar(item)
            return caminhos
        finally:
            _soltar(arr)
    finally:
        _soltar(dlg)
        if hwnd:
            user32.DestroyWindow(hwnd)


FOS_OVERWRITEPROMPT = 0x00000002


def nomear_e_salvar(titulo: str, sugestao: str = "") -> str | None:
    ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    dlg = c_void_p()
    ole32.CoCreateInstance(byref(CLSID_FileSaveDialog), None, CLSCTX_INPROC_SERVER,
                           byref(IID_IFileSaveDialog), byref(dlg))
    hwnd = _dono()
    try:
        opcoes = c_ulong()
        _metodo(dlg, _GET_OPTIONS, c_int, POINTER(c_ulong))(dlg, byref(opcoes))
        _metodo(dlg, _SET_OPTIONS, c_int, c_ulong)(
            dlg, opcoes.value | FOS_FORCEFILESYSTEM | FOS_OVERWRITEPROMPT)
        _metodo(dlg, _SET_TITLE, c_int, c_wchar_p)(dlg, titulo)
        _metodo(dlg, _SET_OK_LABEL, c_int, c_wchar_p)(dlg, "Criar")
        if sugestao:
            _metodo(dlg, _SET_FILE_NAME, c_int, c_wchar_p)(dlg, sugestao)

        hr = _metodo(dlg, _SHOW, c_int, c_void_p)(dlg, hwnd or None) & 0xFFFFFFFF
        if hr == E_CANCELADO:
            return None
        if hr != S_OK:
            raise OSError(f"o seletor devolveu 0x{hr:08x}")

        item = c_void_p()
        _metodo(dlg, _GET_RESULT, c_int, POINTER(c_void_p))(dlg, byref(item))
        try:
            nome = c_wchar_p()
            _metodo(item, _SI_GET_DISPLAY_NAME, c_int, c_int, POINTER(c_wchar_p))(
                item, SIGDN_FILESYSPATH, byref(nome))
            caminho = nome.value
            ole32.CoTaskMemFree(nome)
            return caminho or None
        finally:
            _soltar(item)
    finally:
        _soltar(dlg)
        if hwnd:
            user32.DestroyWindow(hwnd)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="seletores nativos do Windows")
    ap.add_argument("--titulo", default="Onde estão as mídias originais")
    ap.add_argument("--conferir", action="store_true",
                    help="monta o diálogo sem abrir e imprime as opções (bancada)")
    ap.add_argument("--salvar", action="store_true",
                    help="abre o diálogo de SALVAR (nome + local) em vez do de pasta")
    ap.add_argument("--sugestao", default="", help="nome sugerido no diálogo de salvar")
    args = ap.parse_args(argv)

    if sys.platform != "win32":
        print("este seletor só existe no Windows", file=sys.stderr)
        return 2

    if args.conferir:
        print("0x%x" % conferir())
        return 0

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    try:
        if args.salvar:
            escolhido = nomear_e_salvar(args.titulo, args.sugestao)
            caminhos = [escolhido] if escolhido else []
        else:
            caminhos = escolher_pastas(args.titulo)
    except OSError as e:
        print(f"o seletor moderno falhou: {e}", file=sys.stderr)
        return 1

    for c in caminhos:
        print(c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
