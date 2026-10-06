from __future__ import annotations

import logging
import os
import struct
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("delapraca.media.pmr")

INDICE = "msmFMID.pmr"
PASTAS_DE_MIDIA = ("MXF", "UME")
RAIZ_DO_AVID = "avid mediafiles"

_MAGIC = 0x7A9
_MAX_NOME = 260


@dataclass(frozen=True)
class Leitura:

    por_master: dict
    indices_lidos: int = 0
    pastas_sem_indice: int = 0
    ignoradas: tuple = ()
    por_arquivo: dict | None = None

    @property
    def masters(self) -> int:
        return len(self.por_master)


def _urn(cabeca: bytes) -> str:
    g = [bytearray(cabeca[i:i + 4]) for i in range(0, 32, 4)]
    g[4] = g[4][::-1]
    g[5] = g[5][1::-1] + g[5][3:1:-1]
    h = b"".join(bytes(x) for x in g).hex()
    return "urn:smpte:umid:" + ".".join(h[i:i + 8] for i in range(0, 64, 8))


def ler(caminho) -> list:
    return [(nome, master) for nome, _arquivo, master in ler_registros(caminho)]


def ler_registros(caminho) -> list:
    caminho = Path(caminho)
    try:
        dados = caminho.read_bytes()
    except OSError as e:
        log.info("não deu para ler %s: %s", caminho.name, e)
        return []
    if len(dados) < 12:
        return []
    magic, _a, n = struct.unpack_from("<III", dados, 0)
    if magic != _MAGIC:
        log.info("%s: magic 0x%x (esperado 0x%x) — ignorado", caminho.name, magic, _MAGIC)
        return []

    saida: list = []
    off, vistos, pendente = 12, 0, None
    while vistos < n and off + 32 <= len(dados):
        cabeca = dados[off:off + 32]
        off += 32
        if off + 2 > len(dados):
            break
        (tam,) = struct.unpack_from("<H", dados, off)
        espia = dados[off + 2:off + 2 + tam] if tam else b""
        e_longo = 0 < tam < _MAX_NOME and len(espia) == tam and all(
            32 <= c < 127 for c in espia)
        if e_longo:
            off += 2
            nome = dados[off:off + tam].decode("ascii", "replace")
            off += tam
            if off + 2 > len(dados):
                break
            (tape,) = struct.unpack_from("<H", dados, off)
            off += 2 + tape
            pendente = (nome, _urn(cabeca))
            continue
        off += 4
        if pendente is not None:
            saida.append((pendente[0], pendente[1], _urn(cabeca)))
            pendente = None
            vistos += 1
    return saida


def volumes_montados() -> list:
    if os.name != "nt":
        raiz = Path("/Volumes")
        if raiz.is_dir():
            try:
                return [str(p) for p in raiz.iterdir() if p.is_dir()]
            except OSError:
                return []
        return []

    import ctypes

    k32 = ctypes.windll.kernel32
    anterior = k32.SetErrorMode(0x0001)
    try:
        montadas = k32.GetLogicalDrives()
        return [f"{chr(ord('A') + i)}:\\" for i in range(26) if montadas & (1 << i)]
    finally:
        k32.SetErrorMode(anterior)


def pastas_de_midia(raiz) -> tuple:
    base = Path(raiz)
    if base.name.lower() != RAIZ_DO_AVID:
        achada = None
        try:
            for filho in base.iterdir():
                if filho.is_dir() and filho.name.lower() == RAIZ_DO_AVID:
                    achada = filho
                    break
        except OSError:
            return [], []
        if achada is None:
            return [], []
        base = achada

    boas, ignoradas = [], []
    try:
        for filho in sorted(base.iterdir()):
            if not filho.is_dir():
                continue
            if filho.name.upper() in PASTAS_DE_MIDIA:
                try:
                    boas.extend(sorted(d for d in filho.iterdir() if d.is_dir()))
                except OSError:
                    continue
            else:
                ignoradas.append(str(filho))
    except OSError:
        return [], []
    return boas, ignoradas


def raizes_de_midia() -> list:
    fora: list = []
    for volume in volumes_montados():
        try:
            for filho in Path(volume).iterdir():
                if filho.is_dir() and filho.name.lower() == RAIZ_DO_AVID:
                    fora.append(str(filho))
                    break
        except OSError:
            continue
    return fora


def indice_de(raizes) -> Leitura:
    por_master: dict = {}
    por_arquivo: dict = {}
    lidos = sem_indice = 0
    ignoradas: list = []

    for raiz in raizes or []:
        if not raiz:
            continue
        pastas, fora = pastas_de_midia(raiz)
        ignoradas.extend(fora)
        for sub in pastas:
            alvo = sub / INDICE
            if not alvo.is_file():
                sem_indice += 1
                continue
            lidos += 1
            for nome, arquivo, urn in ler_registros(alvo):
                por_master.setdefault(urn, []).append(str(sub / nome))
                por_arquivo.setdefault(arquivo, str(sub / nome))

    for urn in por_master:
        por_master[urn] = sorted(dict.fromkeys(por_master[urn]))

    log.info("pmr: %d índice(s), %d master(s), %d sem índice, %d pasta(s) ignorada(s)",
             lidos, len(por_master), sem_indice, len(ignoradas))
    return Leitura(por_master=por_master, indices_lidos=lidos,
                   pastas_sem_indice=sem_indice, ignoradas=tuple(ignoradas),
                   por_arquivo=por_arquivo)
