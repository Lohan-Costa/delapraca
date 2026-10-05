from __future__ import annotations

import struct
import uuid

import zstandard

COR_DE_CLIPE = {"Orange": 28, "Apricot": 16, "Yellow": 32, "Lime": 22, "Olive": 26, "Green": 2,
                "Teal": 4, "Navy": 24, "Blue": 6, "Purple": 8, "Violet": 30, "Pink": 10, "Tan": 12,
                "Beige": 18, "Brown": 14, "Chocolate": 20}
COR_DE_MARCADOR = {"Blue": 2, "Cyan": 4, "Green": 8, "Yellow": 16, "Red": 32, "Pink": 64,
                   "Purple": 128, "Fuchsia": 512, "Rose": 1024, "Lavender": 2048, "Sky": 4096,
                   "Mint": 8192, "Lemon": 16384, "Sand": 32768, "Cocoa": 65536, "Cream": 131072}
CODIGO = {"distorcao": {"marcador": "Fuchsia", "clipe": "Pink"},
          "refazer": {"marcador": "Red", "clipe": "Chocolate"},
          "conferir": {"marcador": "Yellow", "clipe": "Yellow"}}
PALAVRA_CHAVE = "DLPC"
CATEGORIAS = {
    "distorcao": {"rotulo": "Possível distorção", "sub": "avisar a produção",
                  "quais": ["Reformat Stretch"]},
    "refazer": {"rotulo": "Refazer", "sub": "no destino",
                "quais": ["estabilizador", "efeito não traduzido", "efeito animado que não se reproduz"]},
    "conferir": {"rotulo": "Conferir", "sub": "",
                 "quais": ["cor no Avid", "velocidade", "efeito animado traduzido"]},
}
MAX_PALAVRA = 32


def de_fabrica() -> dict:
    return {"cores": {c: dict(v) for c, v in CODIGO.items()}, "palavra": PALAVRA_CHAVE}


def validar_codigo(d) -> dict:
    fora = de_fabrica()
    d = d if isinstance(d, dict) else {}
    cores = d.get("cores") if isinstance(d.get("cores"), dict) else {}
    for cat in fora["cores"]:
        v = cores.get(cat) if isinstance(cores.get(cat), dict) else {}
        if v.get("marcador") in COR_DE_MARCADOR:
            fora["cores"][cat]["marcador"] = v["marcador"]
        if v.get("clipe") in COR_DE_CLIPE:
            fora["cores"][cat]["clipe"] = v["clipe"]
    p = d.get("palavra")
    if isinstance(p, str):
        p = "".join(ch for ch in p.strip() if ch.isprintable() and ch not in ",;")[:MAX_PALAVRA]
        if p:
            fora["palavra"] = p
    return fora


def repeticoes(codigo: dict) -> list[str]:
    avisos = []
    cats = list(CATEGORIAS)
    for campo, nome in (("marcador", "marcador"), ("clipe", "segmento")):
        for i, a in enumerate(cats):
            for b in cats[i + 1:]:
                ca, cb = codigo["cores"][a][campo], codigo["cores"][b][campo]
                if ca == cb:
                    avisos.append(f"{CATEGORIAS[a]['rotulo']} e {CATEGORIAS[b]['rotulo']} estão com o "
                                  f"mesmo {nome} {ca}")
    return avisos


def _varint(n: int) -> bytes:
    fora = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        fora.append(b | (0x80 if n else 0))
        if not n:
            return bytes(fora)


def _campo(n: int, dado) -> bytes:
    if isinstance(dado, int):
        return _varint(n << 3) + _varint(dado)
    return _varint((n << 3) | 2) + _varint(len(dado)) + dado


def _blob_data(pb: bytes, comprimir: bool = False) -> bytes:
    corpo = (b"\x81" + zstandard.ZstdCompressor().compress(pb)) if comprimir else (b"\x80" + pb)
    return struct.pack(">II", 0x2711, len(corpo)) + corpo


def _fields_blob(dado: bytes) -> str:
    chave = "BlobData".encode("utf-16-be")
    return (struct.pack(">III", 1, 1, len(chave)) + chave + struct.pack(">I", 0x0C) + b"\x00"
            + struct.pack(">I", len(dado)) + dado).hex()


def _elemento(tipo: str, dono: str, dado: bytes) -> str:
    return (f"<Element>\n     <{tipo} DbId=\"{uuid.uuid4()}\">\n      <FieldsBlob>{_fields_blob(dado)}"
            f"</FieldsBlob>\n      <BlobOwner>{dono}</BlobOwner>\n      <DbSavedTime>0</DbSavedTime>\n"
            f"     </{tipo}>\n    </Element>\n    ")


def cor_local(dono: str, cor: str) -> str:
    return _elemento("Sm2TiItemLockableBlob", dono, _blob_data(_campo(5, COR_DE_CLIPE[cor])))


def pb_marcadores(marcadores: list[dict]) -> bytes:
    entradas = b""
    for m in marcadores:
        dentro = _campo(1, COR_DE_MARCADOR[m["cor"]])
        for p in m.get("palavras") or []:
            dentro += _campo(2, p.encode("utf-8"))
        dentro += (_campo(3, (m.get("nota") or "").encode("utf-8"))
                   + _campo(3, str(int(m.get("duracao") or 1)).encode("utf-8"))
                   + _campo(3, (m.get("nome") or "").encode("utf-8")))
        inner = _campo(1, dentro)
        entradas += _campo(1, _campo(1, int(m["quadro"])) + _campo(2, struct.pack(">II", 2, len(inner)) + inner))
    return _campo(2, entradas)


def marcadores(dono: str, lista: list[dict]) -> str:
    return _elemento("Sm2SequenceLockableBlob", dono, _blob_data(pb_marcadores(lista), comprimir=True))


def no_project_xml(project_xml: str, elementos: list[str]) -> str:
    if not elementos:
        return project_xml
    return project_xml.replace("<LocableBlobSet>\n", "<LocableBlobSet>\n    " + "".join(elementos), 1)
