from __future__ import annotations

import os
import re
import struct

import zstandard

FORMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "texto_r19.xml")

_PADRAO_P15 = bytes.fromhex(
    "2280010a7e427c7c0000000bb100000a0000006200610060000f0000000800000001000000380400240000001a"
    "0000004200610073006900630020005400690074006c00650002000000040137000000160000004f0070006500"
    "6e002000530061006e0073000000c042043f00120000002300460046004600460046004600ecf71e03")

CORPO_POR_ALTURA = 1152.0
ANCORA = {f"{v}_{h}": 3 * iv + ih for iv, v in enumerate(("base", "centro", "topo"))
          for ih, h in enumerate(("esquerda", "centro", "direita"))}
BASE_POR_CORPO = 0.354

def _varint(n: int) -> bytes:
    fora = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            fora.append(b | 0x80)
        else:
            fora.append(b)
            return bytes(fora)


def _campo(n: int, dado) -> bytes:
    if isinstance(dado, int):
        return _varint(n << 3) + _varint(dado)
    return _varint((n << 3) | 2) + _varint(len(dado)) + dado


def _duplo(n: int, v: float) -> bytes:
    return _varint((n << 3) | 1) + struct.pack("<d", v)


def _valor(param: int, interno: bytes, padrao: bytes = b"") -> bytes:
    return _campo(1, param) + _campo(3, _campo(1, interno)) + padrao


def _ponto(param: int, x: float, y: float) -> bytes:
    return _valor(param, _campo(7, struct.pack(">dd", x, y)))


def _qcor(rgb, alfa: float = 1.0) -> bytes:
    c = [alfa] + list(rgb)
    return b"\x01" + b"".join(struct.pack(">H", round(min(1, max(0, v)) * 0xFFFF)) for v in c) + b"\0\0"


def _filtro(tipo: int, cabeca_intocado: int | None, posicoes: list) -> bytes:
    corpo = _campo(1, tipo)
    if cabeca_intocado is not None and (tipo == 48 or not any(posicoes)):
        corpo += _campo(cabeca_intocado, 0)
    for p in posicoes:
        corpo += _campo(9, p or b"")
    return _campo(1, corpo)


def _bloco(dado: bytes) -> bytes:
    return struct.pack("<I", len(dado) + 4) + dado


def _u16(s: str) -> bytes:
    return _bloco(s.encode("utf-16-le"))


_ALINHA = {"centro": 0x04, "direita": 0x02, "justificado": 0x08}
CAIXA = {"maiusculas": 1, "minusculas": 2, "versalete": 3, "titulo": 4}
VERTICAL = {"sobrescrito": 1, "subscrito": 2}
_BIT_ESTILO, _BIT_CAIXA, _BIT_ALINHA, _BIT_TRACKING, _BIT_VERTICAL, _BIT_ENTRELINHA = 2, 3, 4, 7, 8, 9
_BIT_PESO, _BIT_ITALICO, _BIT_SOBRELINHA, _BIT_SUBLINHADO, _BIT_RISCADO = 11, 12, 13, 14, 15
_BIT_COR_SUBLINHADO, _BIT_SUBLINHADO_2, _BIT_COR2, _BIT_TRACO = 17, 18, 19, 20
_FLAGS_TUDO_AUSENTE = 0x031EFFFC | (1 << _BIT_ESTILO) | (1 << _BIT_ITALICO) | (1 << _BIT_COR2) | (1 << _BIT_TRACO)


def formato(familia: str, corpo: float, cor: str, *, estilo: str = "", alinhamento: str = "centro",
            peso: int | None = None, italico: int | None = None, cor2: str | None = None,
            traco: float | None = None, entrelinha: float | None = None,
            tracking: float | None = None, caixa: str | None = None, vertical: str | None = None,
            sobrelinha: bool = False, sublinhado: bool = False, riscado: bool = False) -> bytes:
    flags = _FLAGS_TUDO_AUSENTE
    dado = _u16(familia) + struct.pack("<f", corpo)

    def poe(bit: int, b: bytes) -> None:
        nonlocal dado, flags
        dado += b
        flags &= ~(1 << bit)

    if estilo:
        poe(_BIT_ESTILO, _u16(estilo))
    if caixa in CAIXA:
        poe(_BIT_CAIXA, bytes([CAIXA[caixa]]))
    if alinhamento in _ALINHA:
        poe(_BIT_ALINHA, bytes([_ALINHA[alinhamento]]))
    if tracking:
        poe(_BIT_TRACKING, struct.pack("<f", tracking))
    if vertical in VERTICAL:
        poe(_BIT_VERTICAL, bytes([VERTICAL[vertical]]))
    if entrelinha:
        poe(_BIT_ENTRELINHA, struct.pack("<f", entrelinha))
    if peso is not None:
        poe(_BIT_PESO, struct.pack("<H", peso))
    if italico is not None:
        poe(_BIT_ITALICO, bytes([italico]))
    if sobrelinha:
        poe(_BIT_SOBRELINHA, b"\x01")
    if sublinhado:
        poe(_BIT_SUBLINHADO, b"\x01")
    if riscado:
        poe(_BIT_RISCADO, b"\x01")
    dado += _u16(cor)
    if sublinhado:
        poe(_BIT_COR_SUBLINHADO, _u16(cor))
        poe(_BIT_SUBLINHADO_2, b"\x01")
    if cor2:
        poe(_BIT_COR2, _u16(cor2))
    if traco is not None:
        poe(_BIT_TRACO, struct.pack("<f", traco))
    return _bloco(dado + struct.pack("<I", flags))


_FECHA_LINHA, _CONTINUA = b"\x01\x00", b"\x04\x01"


def texto_rico_trechos(trechos: list[tuple[str, bytes, bool]]) -> bytes:
    n = len(trechos)
    etiquetas = "b" + "a" * n + "`" * n
    dado = b"\x0b\xb1\x00\x00" + _bloco(etiquetas.encode("utf-16-le"))
    dado += _bloco(_bloco(b"".join(struct.pack("<I", i + 1) for i in range(n))) + b"\x38\x04\x00")
    for i, (texto, _fmt, fecha) in enumerate(trechos):
        dado += _bloco(_u16(texto) + struct.pack("<I", 1 + n + i) + (_FECHA_LINHA if fecha else _CONTINUA))
    dado += b"".join(fmt for _t, fmt, _f in trechos)
    return _bloco(dado)


def texto_rico(linhas: list[str], fmt: bytes) -> bytes:
    return texto_rico_trechos([(linha, fmt, True) for linha in linhas])


def _hex(rgb) -> str:
    return "#" + "".join(f"{round(min(1, max(0, v)) * 255):02x}" for v in rgb)


def efeitos_do_texto(titulo: dict, largura: int = 1920, altura: int = 1080) -> tuple[bytes, list[str]]:
    avisos: list[str] = []
    caixas = titulo.get("caixas") or []
    cx = caixas[0] if caixas else {"texto": ""}
    if len(caixas) > 1:
        avisos.append(f"{len(caixas) - 1} caixa(s) de texto a mais ficaram só na Carta "
                      f"({', '.join(repr(c.get('texto', ''))[:40] for c in caixas[1:])})")
    padrao = not titulo.get("estilo_lido")
    fonte = cx.get("fonte") or {}
    corpo_frac = cx.get("corpo") or 0.0833333
    corpo = round(cx["corpo_resolve"] if cx.get("corpo_resolve") else corpo_frac * CORPO_POR_ALTURA, 2)
    cor = _hex(cx.get("cor") or [1, 1, 1])
    contorno = cx.get("contorno")
    estilo = fonte.get("estilo") or ""
    fmt = formato(fonte.get("familia") or "Open Sans", corpo, cor,
                  estilo=estilo, alinhamento=cx.get("alinhamento") or "centro",
                  peso=63 if not estilo else (75 if "Bold" in estilo else None),
                  italico=1 if "Italic" in estilo else None,
                  cor2=_hex(contorno["cor"]) if contorno else (cor if cor != "#ffffff" else None),
                  traco=-float(round(contorno.get("espessura", 1) * 2)) / 2 if contorno else None,
                  entrelinha=cx.get("entrelinha"))
    if cx.get("trechos"):
        runs = []
        for linha in cx["trechos"]:
            for k, (texto, rgb) in enumerate(linha):
                c = _hex(rgb)
                f = formato(fonte.get("familia") or "Open Sans", corpo, c, estilo=estilo,
                            alinhamento=cx.get("alinhamento") or "centro",
                            peso=63 if not estilo else None, cor2=c if c != "#ffffff" else None)
                runs.append((texto, f, k == len(linha) - 1))
        rico = texto_rico_trechos(runs)
    else:
        rico = texto_rico((cx.get("texto") or "").split("\n"), fmt)
    p15 = _valor(15, _campo(8, rico), _PADRAO_P15)

    pos48 = [p15, b"", b"", b"", b"", b"", b"", b""]
    if cx.get("ancora") in ANCORA and ANCORA[cx["ancora"]] != 4:
        pos48[2] = _valor(21, _campo(4, ANCORA[cx["ancora"]]))
    p = cx.get("posicao")
    if cx.get("posicao_resolve"):
        pos48[3] = _ponto(17, *cx["posicao_resolve"])
    elif p and not padrao:
        x = 0.5 + float(p.get("x") or 0) * altura / largura
        y = 0.5 + float(p.get("y") or 0) + corpo_frac * BASE_POR_CORPO
        if abs(x - 0.5) > 1e-3 or abs(y - 0.5) > 1e-3:
            pos48[3] = _ponto(17, x, y)

    sombra = [b"", b"", b"", b""]
    s = cx.get("sombra")
    if s:
        dx, dy = (s.get("deslocamento") or [0.01, -0.01])[:2]
        sombra[1] = _ponto(38, float(dx) * altura / largura, -float(dy))
        if s.get("cor"):
            sombra[0] = _valor(37, _campo(6, _qcor(s["cor"])))
        avisos.append("sombra: posição e cor trazidas; desfoque e opacidade no padrão do Resolve "
                      "(Blur 20, Opacity 75)")

    tr = None
    if contorno:
        tr = [_valor(23, _campo(6, _qcor(contorno.get("cor") or [0, 0, 0]))),
              _valor(24, _campo(1, round(float(contorno.get("espessura") or 1) * 2)),
                     _campo(4, _campo(1, _campo(1, 2)))) + _campo(9, 0),
              _valor(172, _campo(5, 1))]

    caixa = [b""] * 8
    f = titulo.get("fundo")
    if f:
        caixa[0] = _valor(27, _campo(6, _qcor(f.get("cor") or [0, 0, 0])))
        caixa[7] = _valor(34, _campo(1, round(float(f.get("opacidade", 1.0)) * 200)))
        if f.get("tela_cheia"):
            caixa[3] = _valor(31, _duplo(2, 1.0))
            caixa[4] = _valor(32, _duplo(2, 1.0))

    corpo_pb = (_filtro(48, 3, pos48) + _filtro(16, 2, sombra)
                + (_filtro(56, None, tr) if tr else b"") + _filtro(54, 2, caixa))
    if padrao:
        avisos.append(f"{titulo.get('titulador') or 'título'}: texto trazido; o estilo ficou no padrão "
                      "do Resolve")
    tf = titulo.get("transform")
    if tf:
        from tools.inspect_drp import build_effect_filters_ba
        kf = tf.get("kf_zoom")
        x, y = tf.get("pos") or (0.0, 0.0)
        cru = bytes.fromhex(build_effect_filters_ba(
            zoom_x=tf.get("zoom"), zoom_y=tf.get("zoom"),
            pos_x=x or None, pos_y=y or None,
            animado={"zoom_x": kf, "zoom_y": kf} if kf else None))
        corpo_pb = cru[9:] + corpo_pb
    comp = zstandard.ZstdCompressor().compress(corpo_pb)
    return (struct.pack(">II", 2, len(comp) + 1) + b"\x81" + comp), avisos


def forma() -> str:
    with open(FORMA, encoding="utf-8") as f:
        return re.search(r"<Sm2TiGenerator\b.*?</Sm2TiGenerator>", f.read(), re.DOTALL).group(0)
