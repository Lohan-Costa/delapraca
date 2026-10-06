from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

CAMPO_VALIDO = re.compile(r"[a-z_][a-z0-9_]*")

_TOKEN_GULOSO = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_FECHADO = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

_ABERTO = re.compile(r"\{[A-Za-z0-9_]*")


class PadraoInvalido(ValueError):

    def __init__(self, tokens):
        self.tokens = list(tokens)
        ruins = ", ".join(repr(t.bruto) for t in self.tokens)
        super().__init__(f"campo(s) que não existem no catálogo: {ruins}")


@dataclass(frozen=True)
class Token:

    inicio: int
    fim: int
    bruto: str
    campo: str | None
    cauda: str = ""


def normalizar(nome: str) -> str:
    cru = unicodedata.normalize("NFKD", str(nome or "").strip())
    cru = "".join(c for c in cru if not unicodedata.combining(c)).lower()
    saida = re.sub(r"[^a-z0-9]+", "_", cru).strip("_")
    if not saida:
        return "_"
    if saida[0].isdigit():
        saida = "_" + saida
    return saida


def _catalogo(campos) -> set[str]:
    if hasattr(campos, "keys"):
        return set(campos.keys())
    return set(campos or ())


def _recuar(token: str, catalogo: set[str]) -> tuple[str | None, str]:
    if token in catalogo:
        return token, ""
    corte = len(token)
    while True:
        corte = token.rfind("_", 0, corte)
        if corte <= 0:
            return None, ""
        prefixo = token[:corte]
        if prefixo in catalogo:
            return prefixo, token[corte:]


def _varrer(padrao: str, campos) -> list:
    catalogo = _catalogo(campos)
    padrao = str(padrao or "")
    fora: list = []
    literal: list[str] = []
    i = 0
    n = len(padrao)

    def _fechar_literal():
        if literal:
            fora.append(("lit", "".join(literal)))
            literal.clear()

    while i < n:
        if padrao[i] != "%":
            literal.append(padrao[i])
            i += 1
            continue
        if i + 1 < n and padrao[i + 1] == "%":
            literal.append("%")
            i += 2
            continue
        if i + 1 < n and padrao[i + 1] == "{":
            fechado = _FECHADO.match(padrao, i + 1)
            if fechado:
                nome = fechado.group(1).lower()
                _fechar_literal()
                fora.append(("campo", Token(
                    inicio=i, fim=fechado.end(),
                    bruto=padrao[i:fechado.end()],
                    campo=nome if nome in catalogo else None,
                    cauda="",
                )))
                i = fechado.end()
                continue
            aberto = _ABERTO.match(padrao, i + 1)
            fim = aberto.end() if aberto else i + 2
            _fechar_literal()
            fora.append(("campo", Token(i, fim, padrao[i:fim], None, "")))
            i = fim
            continue
        m = _TOKEN_GULOSO.match(padrao, i + 1)
        if not m:
            _fechar_literal()
            fora.append(("campo", Token(i, i + 1, "%", None, "")))
            i += 1
            continue
        guloso = m.group(0)
        fim = m.end()
        campo, cauda = _recuar(guloso.lower(), catalogo)
        _fechar_literal()
        consumido = len(guloso) - len(cauda)
        fora.append(("campo", Token(
            inicio=i, fim=i + 1 + consumido,
            bruto="%" + guloso[:consumido],
            campo=campo, cauda=guloso[consumido:],
        )))
        if cauda:
            literal.append(guloso[consumido:])
        i = fim

    _fechar_literal()
    return fora


def analisar(padrao: str, campos) -> list[Token]:
    return [t for tipo, t in _varrer(padrao, campos) if tipo == "campo"]


def validar(padrao: str, campos) -> list[Token]:
    return [t for t in analisar(padrao, campos) if t.campo is None]


def campos_usados(padrao: str, campos) -> list[str]:
    return [t.campo for t in analisar(padrao, campos) if t.campo]


def render(padrao: str, campos: dict) -> str:
    pedacos = _varrer(padrao, campos)
    ruins = [t for tipo, t in pedacos if tipo == "campo" and t.campo is None]
    if ruins:
        raise PadraoInvalido(ruins)
    fora: list[str] = []
    for tipo, valor in pedacos:
        if tipo == "lit":
            fora.append(valor)
        else:
            bruto = campos.get(valor.campo)
            fora.append("" if bruto is None else str(bruto))
    return "".join(fora)
