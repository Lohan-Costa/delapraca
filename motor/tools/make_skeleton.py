from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

_PLACEHOLDER = "DELAPRACA SKELETON"
_CAMINHO = re.compile(r"(?:/Volumes/|[A-Za-z]:\\\\?|[A-Za-z]//)[^<\"]*")


def _primeiro(xml: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}\b.*?</{tag}>", xml, re.DOTALL)
    return m.group(0) if m else None


def _limpar_clipe(bloco: str, nome: str) -> str:
    bloco = re.sub(r"<Name>.*?</Name>", f"<Name>{nome}</Name>", bloco, count=1, flags=re.DOTALL)
    for tag in ("MediaFilePath", "MediaReelNumber", "MediaRef"):
        bloco = re.sub(rf"<{tag}>.*?</{tag}>", f"<{tag}/>", bloco, flags=re.DOTALL)
    return bloco


def _limpar_blobs_clip(xml: str) -> str:
    import zstandard
    from tools.inspect_drp import pb_decode, pb_encode

    def troca(m):
        raw = bytes.fromhex(m.group(1))
        if len(raw) < 13 or raw[9:13].hex() != "28b52ffd":
            return m.group(0)
        ver, flag = raw[0:4], raw[8:9]
        campos = pb_decode(zstandard.ZstdDecompressor().decompress(raw[9:]))
        for f in campos:
            if f["wire"] == 2 and f["field"] in (1, 3):
                f["value"] = b""
            elif f["wire"] == 2 and f["field"] in (2, 6):
                f["value"] = b"SHAPE.mov"
        payload = flag + zstandard.ZstdCompressor().compress(pb_encode(campos))
        return "<Clip>" + (ver + len(payload).to_bytes(4, "big") + payload).hex() + "</Clip>"

    return re.sub(r"<Clip>([0-9a-fA-F]{40,})</Clip>", troca, xml)


def _reduzir_pool(xml: str) -> str:
    mvec = re.search(r"<MediaVec>.*?</MediaVec>", xml, re.DOTALL)
    if not mvec:
        return xml
    timelines = re.findall(r"<Element>\s*<Sm2MpTimelineClip\b.*?</Sm2MpTimelineClip>\s*</Element>",
                           mvec.group(0), re.DOTALL)
    elems = []
    for tag, nome in (("Sm2MpVideoClip", "VIDEO_SHAPE"), ("Sm2MpAudioClip", "AUDIO_SHAPE")):
        b = _primeiro(mvec.group(0), tag)
        if b:
            elems.append("<Element>" + _limpar_clipe(b, nome) + "</Element>")
    novo = "<MediaVec>" + "".join(elems) + "".join(timelines) + "</MediaVec>"
    return xml.replace(mvec.group(0), novo, 1)


def _reduzir_sequencia(xml: str) -> str:
    v = _primeiro(xml, "Sm2TiVideoClip")
    a = _primeiro(xml, "Sm2TiAudioClip")
    formas = {"VideoTrackVec": "<Element>" + _limpar_clipe(v, "VIDEO_SHAPE") + "</Element>" if v else "",
              "AudioTrackVec": "<Element>" + _limpar_clipe(a, "AUDIO_SHAPE") + "</Element>" if a else ""}
    for vec_tag, forma in formas.items():
        vec = re.search(rf"<{vec_tag}>.*?</{vec_tag}>", xml, re.DOTALL)
        if not vec:
            continue
        trilhas = re.findall(r"<Element>\s*<Sm2TiTrack\b.*?</Sm2TiTrack>\s*</Element>", vec.group(0),
                             re.DOTALL)
        if not trilhas:
            continue
        primeira = re.sub(r"<Items>.*?</Items>|<Items/>", f"<Items>{forma}</Items>", trilhas[0],
                          count=1, flags=re.DOTALL)
        xml = xml.replace(vec.group(0), f"<{vec_tag}>{primeira}</{vec_tag}>", 1)
    return xml


def make_skeleton(modelo: str, saida: str, nomes_a_limpar: list[str]) -> dict:
    with zipfile.ZipFile(modelo) as z:
        arquivos = {n: z.read(n) for n in z.namelist()}
    fora: dict[str, bytes] = {}
    for nome, dado in arquivos.items():
        t = dado.decode("utf-8", errors="replace")
        for n in nomes_a_limpar:
            t = t.replace(n, _PLACEHOLDER)
        if "SeqContainer" in nome:
            t = _reduzir_sequencia(t)
        if "MpFolder.xml" in nome and "Sm2MpVideoClip" in t:
            t = _reduzir_pool(t)
            t = _limpar_blobs_clip(t)
        if nome.endswith("project.xml"):
            from drp.writer import sem_marcadores_da_timeline
            t = sem_marcadores_da_timeline(t)
        t = _CAMINHO.sub("", t)
        fora[nome] = t.encode("utf-8")

    Path(saida).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in fora.items():
            z.writestr(n, d)

    import zstandard

    vazamentos = []
    tudo = b"".join(fora.values())
    for n in nomes_a_limpar:
        if n.encode() in tudo:
            vazamentos.append(n)
    for nome, dado in fora.items():
        for m in re.finditer(rb">([0-9a-fA-F]{40,})<", dado):
            rb = bytes.fromhex(m.group(1).decode())
            if len(rb) > 13 and rb[9:13].hex() == "28b52ffd":
                try:
                    d = zstandard.ZstdDecompressor().decompress(rb[9:])
                except Exception:
                    continue
                if any(n.encode() in d or n.encode("utf-16-be") in d for n in nomes_a_limpar):
                    vazamentos.append(f"blob em {nome}")
    if b"Sm2SequenceLockableBlob" in tudo:
        vazamentos.append("marcadores da timeline-modelo (Sm2SequenceLockableBlob)")
    versao = re.search(rb'DbAppVer="([^"]+)" DbPrjVer="([^"]+)"', tudo)
    return {"saida": saida, "arquivos": list(fora),
            "versao": versao.group(1).decode() + " / formato " + versao.group(2).decode() if versao else "?",
            "timeline_preservada": b"Sm2MpTimelineClip" in tudo,
            "vazamentos": sorted(set(vazamentos))}


if __name__ == "__main__":
    r = make_skeleton(sys.argv[1], sys.argv[2], sys.argv[3:])
    for k, v in r.items():
        print(f"{k}: {v}")
