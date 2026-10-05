from __future__ import annotations

import logging
import subprocess
import sys

log = logging.getLogger("delapraca.escolher")

TEMPO_LIMITE_S = 300


class NaoSuportado(RuntimeError):
    pass


def _osascript(script: str) -> str | None:
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True,
                           text=True, timeout=TEMPO_LIMITE_S)
    except subprocess.TimeoutExpired:
        raise RuntimeError("o seletor ficou aberto tempo demais") from None
    except FileNotFoundError:
        raise NaoSuportado("osascript não encontrado") from None

    saida = (r.stdout or "").strip()
    if r.returncode != 0:
        erro = (r.stderr or "").strip()
        if "-128" in erro or "User canceled" in erro:
            return None
        raise RuntimeError(erro.splitlines()[-1] if erro else "o seletor falhou")
    return saida or None


_PREAMBULO = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$dono = New-Object System.Windows.Forms.Form
$dono.TopMost = $true
$dono.ShowInTaskbar = $false
$dono.Opacity = 0
$dono.Size = New-Object System.Drawing.Size(1,1)
$dono.StartPosition = 'CenterScreen'
$dono.Show()
$dono.Activate()
"""

_EPILOGO = r"""
$dono.Close()
$dono.Dispose()
"""


def _powershell(corpo: str) -> str | None:
    script = _PREAMBULO + corpo + _EPILOGO
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-WindowStyle", "Hidden",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=TEMPO_LIMITE_S,
            creationflags=0x08000000,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("o seletor ficou aberto tempo demais") from None
    except FileNotFoundError:
        raise NaoSuportado("powershell.exe não encontrado") from None

    saida = (r.stdout or "").strip()
    if r.returncode != 0 and not saida:
        erro = (r.stderr or "").strip()
        raise RuntimeError(erro.splitlines()[-1] if erro else "o seletor falhou")
    return saida or None


ultimo_erro: str | None = None


def _dialogo_win_moderno(titulo: str, *extras: str) -> list[str]:
    from pathlib import Path

    if getattr(sys, "frozen", False):
        comando = [sys.executable, "--dialogo-win"]
    else:
        script = Path(__file__).resolve().with_name("dialogo_win.py")
        if not script.exists():
            raise RuntimeError(f"{script.name} não foi encontrado ao lado do serviço")
        comando = [sys.executable, str(script)]

    try:
        r = subprocess.run(
            [*comando, "--titulo", titulo, *extras],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=TEMPO_LIMITE_S,
            creationflags=0x08000000,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("o seletor ficou aberto tempo demais") from None

    if r.returncode != 0:
        erro = (r.stderr or "").strip()
        raise RuntimeError(erro.splitlines()[-1] if erro else "o seletor moderno falhou")
    return [linha.strip() for linha in (r.stdout or "").splitlines() if linha.strip()]


def _dialogo_win_antigo(titulo: str) -> list[str]:
    um = _powershell(f"""
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = "{_escapar_ps(titulo)}"
$d.ShowNewFolderButton = $false
if ($d.ShowDialog($dono) -eq [System.Windows.Forms.DialogResult]::OK) {{
  [Console]::Out.WriteLine($d.SelectedPath)
}}
""")
    return [um] if um else []


def escolher_pastas(titulo: str = "Onde estão as mídias originais") -> list[str]:
    global ultimo_erro
    ultimo_erro = None

    if sys.platform == "win32":
        try:
            return _dialogo_win_moderno(titulo)
        except (RuntimeError, OSError) as e:
            ultimo_erro = str(e)
            log.warning("seletor moderno indisponível (%s) — caindo para o antigo", e)
            return _dialogo_win_antigo(titulo)

    um = escolher_pasta(titulo)
    return [um] if um else []


def escolher_pasta(titulo: str = "Onde estão as mídias originais") -> str | None:
    if sys.platform == "win32":
        pastas = escolher_pastas(titulo)
        return pastas[0] if pastas else None
    if sys.platform == "darwin":
        return _osascript(
            'tell application "System Events" to activate\n'
            f'set d to choose folder with prompt "{_escapar(titulo)}"\n'
            "POSIX path of d"
        )
    raise NaoSuportado("não há seletor nativo nesta plataforma")


def nomear_arquivo(titulo: str = "Dê um nome e escolha onde",
                   sugestao: str = "") -> str | None:
    global ultimo_erro
    ultimo_erro = None

    if sys.platform == "win32":
        try:
            achados = _dialogo_win_moderno(titulo, "--salvar", "--sugestao", sugestao)
        except (RuntimeError, OSError) as e:
            ultimo_erro = str(e)
            log.warning("diálogo de salvar indisponível (%s)", e)
            return None
        return achados[0] if achados else None

    if sys.platform == "darwin":
        script = ('tell application "System Events" to activate\n'
                  f'set f to choose file name with prompt "{_escapar(titulo)}"')
        if sugestao:
            script += f' default name "{_escapar(sugestao)}"'
        return _osascript(script + "\nPOSIX path of f")

    raise NaoSuportado("não há seletor nativo nesta plataforma")


def escolher_arquivo(titulo: str = "Escolha a timeline",
                     extensoes: tuple[str, ...] = ("aaf",)) -> str | None:
    if sys.platform == "win32":
        padroes = ";".join(f"*.{e.lstrip('.')}" for e in extensoes)
        rotulo = " ".join(e.upper().lstrip(".") for e in extensoes)
        return _powershell(f"""
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = "{_escapar_ps(titulo)}"
$d.Filter = "{rotulo}|{padroes}|Todos os arquivos|*.*"
$d.Multiselect = $false
$d.CheckFileExists = $true
if ($d.ShowDialog($dono) -eq [System.Windows.Forms.DialogResult]::OK) {{
  [Console]::Out.WriteLine($d.FileName)
}}
""")

    if sys.platform == "darwin":
        lista = ", ".join(f'"{e}"' for e in extensoes)
        com_filtro = (
            'tell application "System Events" to activate\n'
            f'set f to choose file with prompt "{_escapar(titulo)}" of type {{{lista}}}\n'
            "POSIX path of f"
        )
        try:
            return _osascript(com_filtro)
        except RuntimeError as e:
            log.info("filtro de tipo recusado (%s) — abrindo sem filtro", e)
            return _osascript(
                'tell application "System Events" to activate\n'
                f'set f to choose file with prompt "{_escapar(titulo)}"\n'
                "POSIX path of f"
            )
    raise NaoSuportado("não há seletor nativo nesta plataforma")


def _escapar(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace('"', '\\"')


def _escapar_ps(texto: str) -> str:
    return (texto.replace("`", "``").replace("$", "`$")
            .replace('"', '`"'))
