from __future__ import annotations

import os
from pathlib import Path

class CaminhoInvalido(ValueError):
    pass


def forma_windows(caminho) -> bool:
    import re
    return bool(re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", str(caminho or "")))


def _parece_url(s: str) -> bool:
    baixa = s.strip().lower()
    if "://" in baixa:
        return True
    for proto in ("concat:", "subfile:", "data:", "pipe:", "cache:", "crypto:", "async:"):
        if baixa.startswith(proto):
            return True
    return False


def ensure_local_file(caminho) -> Path:
    if not isinstance(caminho, (str, os.PathLike)):
        raise CaminhoInvalido("caminho não é texto")
    s = os.fspath(caminho)
    if not s or not s.strip():
        raise CaminhoInvalido("caminho vazio")
    if _parece_url(s):
        raise CaminhoInvalido("o Órbita só abre arquivos locais")
    if "\x00" in s:
        raise CaminhoInvalido("caminho com byte nulo")

    p = Path(s).expanduser()
    try:
        p = p.resolve(strict=False)
    except OSError as e:
        raise CaminhoInvalido("caminho ilegível") from e
    if not p.is_file():
        raise CaminhoInvalido("arquivo não encontrado")
    return p


def ff_input(caminho) -> str:
    p = caminho if isinstance(caminho, Path) else ensure_local_file(caminho)
    return "file:" + str(p)


def ensure_output_path(caminho, extensoes: tuple[str, ...]) -> Path:
    if not isinstance(caminho, (str, os.PathLike)):
        raise CaminhoInvalido("caminho de saída inválido")
    s = os.fspath(caminho)
    if not s.strip() or "\x00" in s or _parece_url(s):
        raise CaminhoInvalido("caminho de saída inválido")

    p = Path(s).expanduser()
    if not p.is_absolute():
        raise CaminhoInvalido("o caminho de saída precisa ser absoluto")
    if p.suffix.lower() not in extensoes:
        esperado = " ou ".join(extensoes)
        raise CaminhoInvalido(f"o arquivo de saída precisa terminar em {esperado}")
    if not p.parent.is_dir():
        raise CaminhoInvalido("a pasta de destino não existe")
    return p


def mensagem_publica(e: Exception) -> str:
    if isinstance(e, CaminhoInvalido):
        return str(e)
    return "não consegui ler este arquivo"
