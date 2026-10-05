from __future__ import annotations

import re
from dataclasses import dataclass, field

_VALOR = re.compile(r"^\t(\S+) = \{\n\t\tValue = (.*?)\n\t\}", re.MULTILINE)
_LISTA = re.compile(r"^\tList = \{\n(.*?)^\t\}", re.MULTILINE | re.DOTALL)
_PROP = re.compile(r"Prop = (\d+):(\S+)")


@dataclass
class Objeto:
    lista: int = 0
    campos: dict = field(default_factory=dict)


@dataclass
class Mqp:
    valores: list
    listas: list
    caixas: list
    lista_cena: int = 0

    def padrao(self, chave: str):
        for k, v in self.valores:
            if k == chave:
                return v
        return None

    def valor(self, lista: int, chave: str):
        if 1 <= lista <= len(self.listas):
            n = self.listas[lista - 1].get(chave)
            if n:
                perto = [i for i, (k, _v) in enumerate(self.valores) if k == chave]
                if perto:
                    return self.valores[min(perto, key=lambda i: abs(i - (n - 1)))][1]
        return self.padrao(chave)


def _bloco(texto: str, nome: str, inicio: int = 0) -> tuple[str, int]:
    i = texto.find(nome + " = {", inicio)
    if i < 0:
        return "", -1
    j = texto.index("{", i) + 1
    prof = 1
    k = j
    while prof and k < len(texto):
        c = texto[k]
        if c == "{":
            prof += 1
        elif c == "}":
            prof -= 1
        k += 1
    return texto[j:k - 1], k


def ler(texto: str) -> Mqp:
    cache, _ = _bloco(texto, "\nPropCache")
    valores = [(k, v.strip()) for k, v in _VALOR.findall(cache)]
    listas = [{nome: int(n) for n, nome in _PROP.findall(corpo)} for corpo in _LISTA.findall(cache)]
    cena, _ = _bloco(texto, "\nScene")
    caixas = []
    pos = 0
    while True:
        flow, fim = _bloco(cena, "Flow", pos)
        if fim < 0:
            break
        pos = fim
        m = re.search(r"^\t{3}PropList = (\d+)", flow, re.MULTILINE)
        caixa = {
            "lista": int(m.group(1)) if m else 0,
            "justify": (re.search(r"\bJustify = (\w+)", flow) or [None, "Left"])[1],
            "justify_v": (re.search(r"\bJustifyVertical = (\w+)", flow) or [None, "Top"])[1],
            "caracteres": [],
        }
        for corpo in re.findall(r"Character = \{(.*?)\n\t+\}", flow, re.DOTALL):
            lista = re.search(r"PropList = (\d+)", corpo)
            codigo = re.search(r"Code = (.*)", corpo)
            caixa["caracteres"].append({"lista": int(lista.group(1)) if lista else 0,
                                        "codigo": codigo.group(1) if codigo else ""})
        caixas.append(caixa)
    m = re.search(r"^\tPropList = (\d+)", cena, re.MULTILINE)
    return Mqp(valores=valores, listas=listas, caixas=caixas, lista_cena=int(m.group(1)) if m else 0)


def texto_da_caixa(caixa: dict) -> str:
    partes = []
    for c in caixa.get("caracteres") or []:
        cod = c.get("codigo") or ""
        if cod.startswith("0x"):
            try:
                partes.append(chr(int(cod, 16)))
            except ValueError:
                continue
        elif cod.startswith("{") and cod.endswith("}"):
            partes.append(cod[1:-1])
        else:
            partes.append(cod)
    return "".join(partes).rstrip("\n")
