from __future__ import annotations

import gzip
import re

TPS = 254016000000

_INICIO = re.compile(r"\n\t<([A-Za-z0-9_.]+)([^>]*)>")


class Obj:
    __slots__ = ("tag", "oid", "uid", "ini", "fim")

    def __init__(self, tag, oid, uid, ini, fim):
        self.tag, self.oid, self.uid, self.ini, self.fim = tag, oid, uid, ini, fim

    def __repr__(self):
        return f"<{self.tag} {self.oid or self.uid}>"


class Projeto:
    def __init__(self, caminho: str):
        self.caminho = caminho
        with gzip.open(caminho, "rb") as fh:
            self.texto = fh.read().decode("utf-8", "replace")
        self.objetos: list[Obj] = []
        self._por_id: dict[str, Obj] = {}
        self._binarios: dict[str, str] | None = None
        t = self.texto
        for m in _INICIO.finditer(t):
            tag, attrs = m.group(1), m.group(2)
            if attrs.endswith("/"):
                continue
            fim = t.find("\n\t</%s>" % tag, m.end())
            if fim < 0:
                continue
            oid = re.search(r'ObjectID="(\d+)"', attrs)
            uid = re.search(r'ObjectUID="([^"]+)"', attrs)
            o = Obj(tag, oid.group(1) if oid else None, uid.group(1) if uid else None,
                    m.end(), fim)
            self.objetos.append(o)
            for k in (o.oid, o.uid):
                if k and k not in self._por_id:
                    self._por_id[k] = o

    def corpo(self, o: Obj) -> str:
        return self.texto[o.ini:o.fim]

    def valor_inicial(self, prm: Obj) -> str | None:
        m = re.search(r"<StartKeyframeValue([^>]*?)(?:/>|>([^<]*)<)", self.corpo(prm))
        if not m:
            return None
        if (m.group(2) or "").strip():
            return m.group(2).strip()
        h = re.search(r'BinaryHash="([^"]+)"', m.group(1))
        if not h:
            return None
        if self._binarios is None:
            self._binarios = {}
            for b in re.finditer(r'BinaryHash="([^"]+)">([^<]+)<', self.texto):
                self._binarios.setdefault(b.group(1), b.group(2).strip())
        return self._binarios.get(h.group(1))

    def obj(self, ref: str | None) -> Obj | None:
        return self._por_id.get(ref) if ref else None

    def refs(self, o: Obj, tag: str) -> list[Obj]:
        achados = re.findall(r'<%s\b[^>]*Object(?:U)?Ref="([^"]+)"' % re.escape(tag), self.corpo(o))
        return [x for x in (self.obj(r) for r in achados) if x is not None]

    def ref(self, o: Obj, tag: str) -> Obj | None:
        r = self.refs(o, tag)
        return r[0] if r else None

    def campo(self, o: Obj, tag: str) -> str | None:
        m = re.search(r"<%s>([^<]*)</%s>" % (re.escape(tag), re.escape(tag)), self.corpo(o))
        return _desescapar(m.group(1)) if m else None

    def de_tipo(self, tag: str) -> list[Obj]:
        return [o for o in self.objetos if o.tag == tag]

    def versao(self) -> str | None:
        m = re.search(r"<MZ\.BuildVersion\.Modified>([^<]*)<", self.texto)
        return m.group(1) if m else None

    def nome_da_sequencia(self, s: Obj) -> str:
        m = re.search(r"\n\t\t<Name>([^<]*)</Name>", self.corpo(s))
        return _desescapar(m.group(1)) if m else (self.campo(s, "Name") or "")


def _desescapar(s: str) -> str:
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
            .replace("&apos;", "'").replace("&amp;", "&"))
