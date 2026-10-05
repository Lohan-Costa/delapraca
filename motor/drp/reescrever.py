from __future__ import annotations

import os
import re
import uuid
import zipfile

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
U16 = re.compile(rb"(?:\x00[0-9a-f]){8}\x00-(?:(?:\x00[0-9a-f]){4}\x00-){3}(?:\x00[0-9a-f]){12}")
_ITEM = re.compile(r"(<Element>\s*<(Sm2TiVideoClip|Sm2TiAudioClip|Sm2TiGenerator) DbId=\"([^\"]+)\">(.*?)</\2>\s*</Element>)", re.S)
_TRANSICAO = re.compile(r"(<Element>\s*<Sm2TiTransition DbId=\"([^\"]+)\">(.*?)</Sm2TiTransition>\s*</Element>)", re.S)


def _seq(z: zipfile.ZipFile) -> str:
    return next(n for n in z.namelist() if n.startswith("SeqContainer/") and n.endswith(".xml"))


def _campo(item: str, nome: str) -> str:
    m = re.search(rf"<{nome}>([^<]*)</{nome}>", item)
    return m.group(1) if m else ""


def _trilhas(s: str):
    fora = []
    for vec, letra in (("VideoTrackVec", "V"), ("AudioTrackVec", "A")):
        m = re.search(rf"<{vec}>(.*?)</{vec}>", s, re.S)
        if not m:
            continue
        for n, t in enumerate(re.finditer(r"<Sm2TiTrack DbId=\"[^\"]+\">.*?</Sm2TiTrack>", m.group(1), re.S), 1):
            fora.append((letra, n, m.start(1) + t.start(), m.start(1) + t.end()))
    return fora


def inicio_timeline(drt: str) -> int:
    starts = [int(i["start"]) for i in itens(drt, absoluto=True) if i["trilha"].startswith("V")]
    return min(starts) if starts else 86400


def itens(drt: str, absoluto: bool = False, inicio: int | None = None) -> list[dict]:
    with zipfile.ZipFile(drt) as z:
        s = z.read(_seq(z)).decode("utf-8")
    fora = []
    for letra, n, a, b in _trilhas(s):
        for m in _ITEM.finditer(s, a, b):
            corpo = m.group(4)
            start = int(_campo(corpo, "Start") or 0)
            fora.append({"id": m.group(3), "trilha": f"{letra}{n}", "start": start,
                         "dur": int(_campo(corpo, "Duration") or 0),
                         "fonte_in": int(float(_campo(corpo, "In") or 0)),
                         "nome": _campo(corpo, "Name"), "tipo": _campo(corpo, "PrettyType") or m.group(2)})
    if not absoluto:
        base = inicio if inicio is not None else min((i["start"] for i in fora if i["trilha"].startswith("V")),
                                                     default=86400)
        for i in fora:
            i["inicio"] = i["start"] - base
    return fora


def contagem(drt: str) -> dict[str, int]:
    with zipfile.ZipFile(drt) as z:
        s = z.read(_seq(z)).decode("utf-8")
    fora = {}
    for letra, n, a, b in _trilhas(s):
        bloco = s[a:b]
        c = len(_ITEM.findall(bloco)) + len(_TRANSICAO.findall(bloco))
        if c:
            fora[f"{letra}{n}"] = c
    return fora


def _trocar(item: str, nome: str, valor, obrigatorio: bool = False) -> str:
    novo = f"<{nome}>{valor}</{nome}>"
    item, n = re.subn(rf"<{nome}>[^<]*</{nome}>|<{nome}/>", novo, item, count=1)
    if not n and obrigatorio:
        raise ValueError(f"um item da timeline de trabalho não tem o campo <{nome}>: não sei aparar "
                         "esse item sem perder o corte — nada foi alterado")
    return item


def aplicar(drt: str, destino: str, acoes: dict, inicio: int, nome: str | None = None,
            mover_quadro=None, so_definidos: bool = False, ids: str = "",
            cortes: set | None = None, rastro: dict | None = None) -> dict:
    with zipfile.ZipFile(drt) as z:
        arqs = {n: z.read(n) for n in z.namelist()}
        nseq = _seq(z)
    s = arqs[nseq].decode("utf-8")
    conta: dict[str, int] = {}

    def mexer(m):
        bloco, tipo, dbid, corpo = m.group(1), m.group(2), m.group(3), m.group(4)
        a = acoes.get(dbid)
        if not a:
            return bloco
        conta[a["acao"]] = conta.get(a["acao"], 0) + 1
        if a["acao"] == "apagar":
            return ""
        novo = _trocar(corpo, "Start", inicio + int(a["inicio"]), obrigatorio=True)
        if a["acao"] == "aparar":
            novo = _trocar(novo, "Duration", int(a["dur"]), obrigatorio=True)
            novo = _trocar(novo, "In", int(a["fonte_in"]), obrigatorio=True)
        return bloco.replace(corpo, novo, 1)

    apagados = {m.group(3) for m in _ITEM.finditer(s) if (acoes.get(m.group(3)) or {}).get("acao") == "apagar"}
    removidas: list[dict] = []
    for letra, n, a, b in reversed(_trilhas(s)):
        bloco = s[a:b]
        pos = {}
        for m in _ITEM.finditer(bloco):
            corpo = m.group(4)
            st, du = int(_campo(corpo, "Start") or 0), int(_campo(corpo, "Duration") or 0)
            ac = acoes.get(m.group(3))
            if ac and ac["acao"] == "apagar":
                depois = None
            elif ac:
                nst = inicio + int(ac["inicio"])
                depois = (nst, nst + (int(ac["dur"]) if ac["acao"] == "aparar" else du))
            else:
                depois = (st, st + du)
            pos[m.group(3)] = ((st, st + du), depois)
        bloco = _ITEM.sub(mexer, bloco)
        bloco = _TRANSICAO.sub(lambda m: _transicao(m, pos, letra, n, removidas, conta, cortes or set(),
                                                    rastro if rastro is not None else {}), bloco)
        s = s[:a] + bloco + s[b:]
    if removidas:
        conta["transicoes_removidas"] = len(removidas)
    if nome:
        s = re.sub(r"(<Sm2Sequence DbId=\"[^\"]+\">.*?<Name>)[^<]*(</Name>)", rf"\g<1>{nome}\g<2>", s, count=1, flags=re.S)
    arqs[nseq] = s.encode("utf-8")
    fora_do_lugar: list[dict] = []
    if "project.xml" in arqs:
        px = arqs["project.xml"].decode("utf-8")
        px = re.sub(r"<Element>\s*<Sm2TiItemLockableBlob\b(?:(?!</Element>).)*?<BlobOwner>([^<]+)</BlobOwner>"
                    r"(?:(?!</Element>).)*?</Sm2TiItemLockableBlob>\s*</Element>\s*",
                    lambda m: "" if m.group(1) in apagados else m.group(0), px, flags=re.S)
        if mover_quadro is not None:
            px, fora_do_lugar = _mover_marcadores(px, mover_quadro)
        arqs["project.xml"] = px.encode("utf-8")
    _gravar_com_ids_novos(arqs, destino, so_definidos=so_definidos, ids=ids)
    conta["marcadores_fora_do_lugar"] = len(fora_do_lugar)
    return conta


def _varint(b: bytes, i: int) -> tuple[int, int]:
    n = s = 0
    while True:
        c = b[i]
        n |= (c & 0x7F) << s
        i += 1
        if not c & 0x80:
            return n, i
        s += 7


def _campos(b: bytes) -> list[tuple[int, int, bytes | int]]:
    fora, i = [], 0
    while i < len(b):
        chave, i = _varint(b, i)
        num, tipo = chave >> 3, chave & 7
        if tipo == 0:
            v, i = _varint(b, i)
        elif tipo == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
        elif tipo == 5:
            v, i = b[i:i + 4], i + 4
        elif tipo == 1:
            v, i = b[i:i + 8], i + 8
        else:
            raise ValueError(f"protobuf com tipo {tipo} nos marcadores")
        fora.append((num, tipo, v))
    return fora


def _escrever(campos) -> bytes:
    from drp.sinais import _varint as vi
    fora = b""
    for num, tipo, v in campos:
        fora += vi((num << 3) | tipo)
        if tipo == 0:
            fora += vi(v)
        elif tipo == 2:
            fora += vi(len(v)) + v
        else:
            fora += v
    return fora


def quadros_dos_marcadores(drt: str) -> list[int]:
    with zipfile.ZipFile(drt) as z:
        px = z.read("project.xml").decode("utf-8") if "project.xml" in z.namelist() else ""
    vistos: list[int] = []
    _mover_marcadores(px, lambda q: (vistos.append(q) or q, True))
    return sorted(vistos)


def _mover_marcadores(px: str, mover_quadro) -> tuple[str, list[dict]]:
    import zstandard

    from drp import sinais
    from drp.ba_mapa import ler_mapa

    m = re.search(r"(<Sm2SequenceLockableBlob\b.*?<FieldsBlob>)(\w+)(</FieldsBlob>)", px, re.S)
    if not m:
        return px, []
    _v, itens_mapa, _r = ler_mapa(bytes.fromhex(m.group(2)))
    if len(itens_mapa) != 1:
        return px, [{"quadro": None, "novo": None, "erro": "bloco de marcadores com forma desconhecida"}]
    raw = itens_mapa[0][1][2]
    comprimido = raw[8:9] == b"\x81"
    pb = zstandard.ZstdDecompressor().decompress(raw[9:], max_output_size=1 << 24) if comprimido else raw[9:]
    fora_do_lugar = []
    topo = []
    vistos = 0
    for num, tipo, v in _campos(pb):
        if num == 2 and tipo == 2:
            entradas = []
            for n1, t1, e in _campos(v):
                if n1 in (1, 2) and t1 == 2:
                    campos = _campos(e)
                    for k, (n2, t2, q) in enumerate(campos):
                        if n2 != 1 or (n1 == 1 and t2 != 0) or (n1 == 2 and t2 != 2):
                            continue
                        if t2 == 0:
                            vistos += 1
                            novo, ok = mover_quadro(q)
                            campos[k] = (1, 0, max(0, int(novo)))
                        elif t2 == 2:
                            sub = _campos(q)
                            j = next((j for j, (n3, t3, _m) in enumerate(sub) if n3 == 1 and t3 == 0), None)
                            if j is None:
                                continue
                            meio = sub[j][2]
                            vistos += 1
                            q = meio // 2
                            novo, ok = mover_quadro(q)
                            sub[j] = (1, 0, max(0, int(novo)) * 2 + meio % 2)
                            campos[k] = (1, 2, _escrever(sub))
                        else:
                            continue
                        if not ok:
                            fora_do_lugar.append({"quadro": q, "novo": novo})
                    e = _escrever(campos)
                entradas.append((n1, t1, e))
            v = _escrever(entradas)
        topo.append((num, tipo, v))
    if not vistos and any(n == 2 and t == 2 and v for n, t, v in _campos(pb)):
        return px, [{"quadro": None, "novo": None, "erro": "bloco de marcadores com forma desconhecida"}]
    novo_blob = sinais._fields_blob(sinais._blob_data(_escrever(topo), comprimir=comprimido))
    return px[:m.start(2)] + novo_blob + px[m.end(2):], fora_do_lugar


def _transicao(m, pos: dict, letra: str, n: int, removidas: list, conta: dict, cortes: set = frozenset(),
               rastro: dict | None = None) -> str:
    bloco, corpo = m.group(1), m.group(3)
    ts, td = int(_campo(corpo, "Start") or 0), int(_campo(corpo, "Duration") or 0)
    esq = next((d for (a0, f0), d in pos.values() if ts <= f0 <= ts + td and not (ts < a0 < ts + td)), "x")
    dir_ = next((d for (a0, f0), d in pos.values() if ts <= a0 <= ts + td and not (ts < f0 < ts + td)), "x")
    tem_esq, tem_dir = esq != "x", dir_ != "x"
    antes_dir = next(((a0, f0) for (a0, f0), d in pos.values() if ts <= a0 <= ts + td and d is dir_), None)
    antes_esq = next(((a0, f0) for (a0, f0), d in pos.values() if ts <= f0 <= ts + td and d is esq), None)
    novo_ts = None
    if tem_esq and tem_dir:
        if esq is not None and dir_ is not None and esq[1] == dir_[0]:
            novo_ts = dir_[0] - (antes_dir[0] - ts)
        elif esq is None and dir_ is not None and (letra, n, dir_[0]) in cortes:
            novo_ts = dir_[0] - (antes_dir[0] - ts)
        elif dir_ is None and esq is not None and (letra, n, esq[1]) in cortes:
            novo_ts = esq[1] - (antes_esq[1] - ts)
    elif tem_dir and dir_ is not None:
        novo_ts = ts + (dir_[0] - antes_dir[0])
    elif tem_esq and esq is not None:
        novo_ts = ts + (esq[1] - antes_esq[1])
    if rastro is not None:
        rastro[(f"{letra}{n}", ts, td)] = novo_ts
    if novo_ts is None:
        removidas.append({"trilha": f"{letra}{n}", "start": ts, "dur": td, "tipo": _campo(corpo, "PrettyType")})
        return ""
    if novo_ts != ts:
        conta["transicoes_movidas"] = conta.get("transicoes_movidas", 0) + 1
        return bloco.replace(corpo, _trocar(corpo, "Start", novo_ts), 1)
    return bloco


def transicoes(drt: str) -> list[dict]:
    with zipfile.ZipFile(drt) as z:
        s = z.read(_seq(z)).decode("utf-8")
    fora = []
    for letra, n, a, b in _trilhas(s):
        for m in _TRANSICAO.finditer(s, a, b):
            corpo = m.group(3)
            fora.append({"trilha": f"{letra}{n}", "start": int(_campo(corpo, "Start") or 0),
                         "dur": int(_campo(corpo, "Duration") or 0), "nome": _campo(corpo, "PrettyType")})
    return fora


def ajustar_transicoes(drt: str, acrescentar: list[dict], tirar: set) -> dict:
    from drp.writer import _make_transition

    with zipfile.ZipFile(drt) as z:
        arqs = {n: z.read(n) for n in z.namelist()}
        nseq = _seq(z)
    s = arqs[nseq].decode("utf-8")
    conta = {"transicoes_novas": 0, "transicoes_tiradas": 0}
    por_trilha: dict[str, list[dict]] = {}
    for t in acrescentar:
        por_trilha.setdefault(t["trilha"], []).append(t)
    for letra, n, a, b in reversed(_trilhas(s)):
        nome = f"{letra}{n}"
        bloco = s[a:b]
        if tirar:
            def fora(m):
                corpo = m.group(3)
                chave = (nome, int(_campo(corpo, "Start") or 0), int(_campo(corpo, "Duration") or 0))
                if chave in tirar:
                    conta["transicoes_tiradas"] += 1
                    return ""
                return m.group(1)
            bloco = _TRANSICAO.sub(fora, bloco)
        for t in sorted(por_trilha.get(nome) or [], key=lambda t: -int(t["start"])):
            novo = "<Element>" + _make_transition(int(t["start"]), int(t["dur"]), int(t["align"]),
                                                  kind="audio" if t.get("audio") else "video") + "</Element>"
            itens = [m for rx in (_ITEM, _TRANSICAO) for m in rx.finditer(bloco)]
            depois = sorted((m.start() for m in itens if int(_campo(m.group(0), "Start") or 0) >= int(t["start"])))
            if depois:
                pos = depois[0]
            else:
                fim = bloco.rfind("</Items>")
                if fim < 0:
                    continue
                pos = fim
            bloco = bloco[:pos] + novo + bloco[pos:]
            conta["transicoes_novas"] += 1
        s = s[:a] + bloco + s[b:]
    arqs[nseq] = s.encode("utf-8")
    tmp = drt + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, v in arqs.items():
            z.writestr(n, v)
    os.replace(tmp, drt)
    return conta


def _ids_da_timeline(arqs: dict) -> set[str]:
    fora: set[str] = set()
    for n, b in arqs.items():
        if not n.endswith(".xml"):
            continue
        t = b.decode("utf-8")
        if n.startswith("SeqContainer/") or n == "project.xml":
            fora |= set(re.findall(r'DbId="([0-9a-f-]{36})"', t))
        else:
            for m in re.finditer(r"<Sm2MpTimelineClip DbId=.*?</Sm2MpTimelineClip>", t, re.S):
                fora |= set(re.findall(r'DbId="([0-9a-f-]{36})"', m.group(0)))
    return fora


def _gravar_com_ids_novos(arqs: dict, destino: str, so_definidos: bool = False, ids: str = "") -> None:
    ids = ids or ("definidos" if so_definidos else "timeline")
    mapa: dict[str, str] = {}
    trocar: set[str] | None = None
    if ids == "definidos":
        trocar = set()
        for n, b in arqs.items():
            if n.endswith(".xml"):
                trocar |= set(re.findall(r'DbId="([0-9a-f-]{36})"', b.decode("utf-8")))
    elif ids == "timeline":
        trocar = _ids_da_timeline(arqs)
    elif ids == "nenhum":
        trocar = set()

    def novo(velho: str) -> str:
        if trocar is not None and velho not in trocar:
            return velho
        return mapa.setdefault(velho, str(uuid.uuid4()))

    def blob(m):
        raw = bytes.fromhex(m.group(1))
        return ">" + U16.sub(lambda q: novo(q.group(0).decode("utf-16-be")).encode("utf-16-be"), raw).hex() + "<"

    saida = {}
    for n, b in arqs.items():
        if n.endswith(".xml"):
            t = UUID.sub(lambda m: novo(m.group(0)), b.decode("utf-8"))
            saida[n] = re.sub(r">([0-9a-f]{40,})<", blob, t).encode("utf-8")
        else:
            saida[n] = b
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in saida.items():
            z.writestr(UUID.sub(lambda m: mapa.get(m.group(0), m.group(0)), n), b)
