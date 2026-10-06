from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import configuracoes
import plataforma

log = logging.getLogger("delapraca.cache")

CONFERENCIAS = "Conferências"
RECEBIDOS = "recebidos"
TRABALHO = "trabalho"
SUBPASTAS = (CONFERENCIAS, RECEBIDOS, TRABALHO)
ANTIGAS = ("conferencia", "recebidos", "trabalho")

_INTERVALO_TETO_S = 30.0
_ultimo_teto = 0.0
_trava = threading.Lock()
_avisados: set[str] = set()


def _escolhida() -> Path | None:
    c = configuracoes.ler().get("cache_dir")
    return Path(c) if c else None


def indisponivel() -> bool:
    c = _escolhida()
    return bool(c) and not Path(c.anchor).exists()


def raiz() -> Path:
    c = _escolhida()
    if c and not indisponivel():
        return c
    if c and str(c) not in _avisados:
        _avisados.add(str(c))
        log.warning("o cache escolhido (%s) não está acessível: usando o padrão", c)
    return plataforma.pasta_cache_padrao()


def sub(nome: str) -> Path:
    if nome not in SUBPASTAS:
        raise ValueError(f"subpasta desconhecida: {nome}")
    p = raiz() / nome
    p.mkdir(parents=True, exist_ok=True)
    return p


def _tamanho_da_pasta(pasta: Path) -> tuple[int, float, int]:
    total, novo, n = 0, 0.0, 0
    try:
        novo = pasta.stat().st_mtime
    except OSError:
        pass
    for dirpath, _dirs, arquivos in os.walk(pasta):
        for a in arquivos:
            try:
                st = os.stat(os.path.join(dirpath, a))
            except OSError:
                continue
            total += st.st_size
            novo = max(novo, st.st_mtime)
            n += 1
    return total, novo, n


def _irmaos(arquivo: Path) -> str:
    return arquivo.name.split(".", 1)[0]


def unidades(base: Path | None = None) -> list[dict]:
    base = base or raiz()
    fora: list[dict] = []
    conf = base / CONFERENCIAS
    try:
        filhos = list(os.scandir(conf))
    except OSError:
        filhos = []
    for e in filhos:
        p = Path(e.path)
        try:
            if e.is_dir():
                b, m, n = _tamanho_da_pasta(p)
            else:
                st = e.stat()
                b, m, n = st.st_size, st.st_mtime, 1
        except OSError:
            continue
        fora.append({"caminhos": [p], "bytes": b, "usado": m, "arquivos": n})
    for nome in (RECEBIDOS, TRABALHO):
        grupos: dict[str, dict] = {}
        try:
            filhos = list(os.scandir(base / nome))
        except OSError:
            continue
        for e in filhos:
            p = Path(e.path)
            try:
                if e.is_dir():
                    b, m, n = _tamanho_da_pasta(p)
                else:
                    st = e.stat()
                    b, m, n = st.st_size, st.st_mtime, 1
            except OSError:
                continue
            chave = f"{nome}/{_irmaos(p) if nome == RECEBIDOS else p.name}"
            g = grupos.setdefault(chave, {"caminhos": [], "bytes": 0, "usado": 0.0, "arquivos": 0})
            g["caminhos"].append(p)
            g["bytes"] += b
            g["usado"] = max(g["usado"], m)
            g["arquivos"] += n
        fora.extend(grupos.values())
    return fora


def estatisticas() -> dict:
    cfg = configuracoes.ler()
    us = unidades()
    return {
        "dir": str(raiz()),
        "padrao": str(plataforma.pasta_cache_padrao()),
        "escolhida": cfg.get("cache_dir"),
        "indisponivel": indisponivel(),
        "bytes": sum(u["bytes"] for u in us),
        "arquivos": sum(u["arquivos"] for u in us),
        "max_gb": cfg["cache_max_gb"],
        "logs": str(plataforma.pasta_logs()),
    }


def tocar(caminho) -> None:
    try:
        os.utime(caminho, None)
    except OSError:
        pass


def _apagar(u: dict) -> bool:
    ok = True
    for p in u["caminhos"]:
        try:
            if p.is_dir():
                plataforma.apagar_arvore(p)
            else:
                plataforma.apagar_arquivo(p)
        except FileNotFoundError:
            continue
        except OSError as e:
            log.info("cache: não apaguei %s (%s)", p, e)
            ok = False
    return ok


def _protegida(u: dict, protegidos) -> bool:
    prot = {os.path.normcase(os.path.abspath(p)) for p in protegidos or ()}
    return any(os.path.normcase(os.path.abspath(p)) in prot for p in u["caminhos"])


def limpar(protegidos=()) -> dict:
    livres = arquivos = 0
    with _trava:
        for u in unidades():
            if _protegida(u, protegidos):
                continue
            if _apagar(u):
                livres += u["bytes"]
                arquivos += u["arquivos"]
    log.info("cache limpo: %d arquivo(s), %.1f MB", arquivos, livres / 1e6)
    return {"bytes": livres, "arquivos": arquivos}


def aplicar_teto(max_bytes: int | None = None, protegidos=()) -> dict:
    if max_bytes is None:
        max_bytes = int(configuracoes.ler()["cache_max_gb"]) * 1024 ** 3
    livres = arquivos = 0
    with _trava:
        us = unidades()
        total = sum(u["bytes"] for u in us)
        if total <= max_bytes:
            return {"bytes": 0, "arquivos": 0}
        for u in sorted(us, key=lambda u: u["usado"]):
            if total - livres <= max_bytes:
                break
            if _protegida(u, protegidos):
                continue
            if _apagar(u):
                livres += u["bytes"]
                arquivos += u["arquivos"]
    if arquivos:
        log.info("cache acima do teto: %d arquivo(s) usados há mais tempo saíram (%.1f MB)",
                 arquivos, livres / 1e6)
    return {"bytes": livres, "arquivos": arquivos}


def talvez_aplicar_teto(protegidos=()) -> None:
    global _ultimo_teto
    agora = time.monotonic()
    if agora - _ultimo_teto < _INTERVALO_TETO_S:
        return
    _ultimo_teto = agora
    try:
        aplicar_teto(protegidos=protegidos)
    except Exception:
        log.debug("teto do cache falhou", exc_info=True)


def _antigas() -> list[Path]:
    dados = plataforma.pasta_dados()
    atual = {os.path.normcase(os.path.abspath(raiz() / s)) for s in SUBPASTAS}
    return [dados / n for n in ANTIGAS
            if (dados / n).is_dir() and os.path.normcase(os.path.abspath(dados / n)) not in atual]


def antigo() -> dict | None:
    if configuracoes.ler().get("cache_antigo_dispensado"):
        return None
    total = n = 0
    pastas = []
    for p in _antigas():
        b, _m, k = _tamanho_da_pasta(p)
        if k:
            total += b
            n += k
            pastas.append(str(p))
    return {"bytes": total, "arquivos": n, "pastas": pastas} if n else None


def apagar_antigo() -> dict:
    total = n = 0
    for p in _antigas():
        b, _m, k = _tamanho_da_pasta(p)
        try:
            plataforma.apagar_arvore(p)
        except OSError as e:
            log.info("cache antigo: não apaguei tudo em %s (%s)", p, e)
        if not p.exists():
            total += b
            n += k
    log.info("cache antigo apagado: %d arquivo(s), %.1f MB", n, total / 1e6)
    return {"bytes": total, "arquivos": n}


def pasta_conhecida(qual: str) -> Path:
    if qual == "cache":
        p = raiz()
    elif qual == "logs":
        p = plataforma.pasta_logs()
    else:
        raise ValueError("pasta desconhecida")
    p.mkdir(parents=True, exist_ok=True)
    return p
