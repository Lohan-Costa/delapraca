from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path

import plataforma

log = logging.getLogger("delapraca.presets")

NOME_PRESET = "AAF-DLPC"

FORMATO_AAF = "20"

_NOME = re.compile(r'name="name" type="string">([^<]*)')
_RECURSO = Path(__file__).resolve().parent / "recursos" / "preset_aaf.xml"

_TAG_AVCLASS = re.compile(r"<AvClass\b[^>]*?(/?)>|</AvClass>")


def blocos_de_export(texto: str) -> list[str]:
    fora: list[str] = []
    for m in re.finditer(r'<AvClass id="EXst">', texto):
        profundidade = 0
        for t in _TAG_AVCLASS.finditer(texto, m.start()):
            if t.group(0).startswith("</"):
                profundidade -= 1
            elif t.group(1) != "/":
                profundidade += 1
            if profundidade == 0:
                fora.append(texto[m.start():t.end()])
                break
    return fora


def caminho_das_settings(usuario: str | None = None) -> Path | None:
    raiz = plataforma.raiz_settings_avid()
    if raiz is None:
        return None
    pastas = [raiz / usuario] if usuario else sorted(
        (p for p in raiz.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime, reverse=True)
    for pasta in pastas:
        alvo = pasta / f"{pasta.name} Settings.xml"
        if alvo.is_file():
            return alvo
    return None


def _nome_do(bloco: str) -> str:
    m = _NOME.search(bloco)
    return m.group(1) if m else ""


def formato_de(bloco: str) -> str | None:
    m = re.search(
        r'name="OMFI:ATTB:Name" type="string">_EXPORTFORMAT</AvProp>\s*'
        r'<AvProp id="ATTR" name="OMFI:ATTB:IntAttribute"[^>]*>([^<]*)', bloco)
    return m.group(1) if m else None


def preset_embarcado() -> str | None:
    try:
        return _RECURSO.read_text(encoding="utf-8")
    except OSError as e:
        log.error("recurso do preset não encontrado (%s): %s", _RECURSO, e)
        return None


def presets_de_export(caminho: Path | None = None) -> dict[str, str]:
    arquivo = caminho or caminho_das_settings()
    if not arquivo or not arquivo.is_file():
        return {}
    try:
        texto = arquivo.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.debug("não consegui ler %s: %s", arquivo, e)
        return {}
    return {n: b for b in blocos_de_export(texto) if (n := _nome_do(b))}


def diagnostico(caminho: Path | None = None) -> dict:
    arquivo = caminho or caminho_das_settings()
    if not arquivo:
        return {"situacao": "sem_settings", "settings": None}

    todos = presets_de_export(arquivo)
    nosso = todos.get(NOME_PRESET)
    embarcado = preset_embarcado()

    base = {
        "settings": str(arquivo),
        "preset": NOME_PRESET,
        "formato_esperado": FORMATO_AAF,
        "mc_aberto": media_composer_aberto(),
    }
    if nosso is None:
        return {**base, "situacao": "ausente"}

    fmt = formato_de(nosso)
    igual = embarcado is not None and _comparavel(nosso) == _comparavel(embarcado)
    if igual and fmt == FORMATO_AAF:
        return {**base, "situacao": "ok", "formato": fmt}
    return {**base, "situacao": "divergente", "formato": fmt,
            "motivo": ("o preset no Media Composer não exporta AAF"
                       if fmt != FORMATO_AAF
                       else "o preset no Media Composer difere do que o plugin espera")}


def _comparavel(bloco: str) -> str:
    return re.sub(r"\s+", " ", bloco).strip()


def media_composer_aberto() -> bool:
    try:
        return plataforma.media_composer_aberto()
    except Exception:
        log.debug("não consegui enumerar processos", exc_info=True)
        return True


def instalar(caminho: Path | None = None, *, forcar: bool = False) -> dict:
    arquivo = caminho or caminho_das_settings()
    if not arquivo:
        return {"ok": False, "erro": "não achei o Settings.xml do Media Composer"}
    if media_composer_aberto() and not forcar:
        return {"ok": False, "erro":
                "feche o Media Composer antes de instalar o preset — ele reescreve as "
                "settings ao sair, e desfaria o que fizermos agora."}

    embarcado = preset_embarcado()
    if not embarcado:
        return {"ok": False, "erro": "o preset embarcado não veio no pacote"}
    if formato_de(embarcado) != FORMATO_AAF:
        return {"ok": False, "erro": "o preset embarcado não é de AAF — pacote inválido"}

    try:
        texto = arquivo.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "erro": f"não consegui ler as settings: {e}"}

    copia = arquivo.with_suffix(f".xml.delapraca-{time.strftime('%Y%m%d-%H%M%S')}.bak")
    try:
        shutil.copy2(arquivo, copia)
    except OSError as e:
        return {"ok": False, "erro": f"não consegui fazer a cópia de segurança: {e}"}

    antigos = [b for b in blocos_de_export(texto) if _nome_do(b) == NOME_PRESET]
    if antigos:
        novo_texto = texto.replace(antigos[0], embarcado, 1)
        for extra in antigos[1:]:
            novo_texto = novo_texto.replace(extra, "", 1)
        acao = "substituído"
    else:
        outros = [b for b in blocos_de_export(texto) if b != embarcado]
        if not outros:
            return {"ok": False, "erro": "não achei onde inserir — nenhum preset de "
                                         "export no arquivo"}
        novo_texto = texto.replace(outros[-1], outros[-1] + "\n    " + embarcado, 1)
        acao = "inserido"

    try:
        arquivo.write_text(novo_texto, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "erro": f"não consegui escrever as settings: {e}"}

    log.info("preset %r %s em %s (backup: %s)", NOME_PRESET, acao, arquivo, copia.name)
    return {"ok": True, "acao": acao, "backup": str(copia), "preset": NOME_PRESET}
