from __future__ import annotations

from . import mqp

EID_TITLER = "E4EEBB07-AFAA-4122-BFA8-0A838BB40A8C"
EID_LEGACY = "8BA34E51-D647-443E-BFB5-30DFCECE5415"
UUID_MQP = "67a614ac-29b6-4afe-b9cc-8107a5dd3468"

_FACES = ("Bold Italic", "Semibold Italic", "Light Italic", "Black Italic", "Medium Italic",
          "Extrabold", "ExtraBold", "Semibold", "SemiBold", "Bold", "Italic", "Light", "Medium",
          "Black", "Regular", "Thin", "Oblique")


def _attrs(te) -> dict:
    try:
        return dict(te.property_data.get("attributes") or {})
    except Exception:
        return {}


def e_titulo(te) -> bool:
    eid = str(getattr(te, "effect_id", "") or "").upper()
    if eid in (EID_TITLER, EID_LEGACY):
        return True
    return _attrs(te).get("_EFFECT_PLUGIN_CLASS") == "Title"


def _blobs(te) -> list[tuple[str, bytes]]:
    fora = []
    for p in te.property_data.get("param_list") or []:
        pd = getattr(p, "property_data", {}) or {}
        v = pd.get("value")
        if type(v).__name__ == "CFUserParam":
            try:
                fora.append((str(pd.get("uuid") or ""), bytes(v.property_data["data"])))
            except Exception:
                continue
    return fora


def fonte(nome: str) -> dict:
    nome = (nome or "").strip().strip("{}").strip()
    for face in _FACES:
        if nome.lower().endswith(" " + face.lower()):
            return {"familia": nome[: -len(face)].strip(), "estilo": face}
    return {"familia": nome, "estilo": ""}


def _num(v, padrao: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return padrao


def _sim(v) -> bool:
    return str(v).strip().lower() in ("true", "1")


def _cor(m: mqp.Mqp, lista: int, prefixo: str) -> list:
    return [round(min(1.0, max(0.0, _num(m.valor(lista, f"{prefixo}.{c}")))), 4)
            for c in ("Red", "Green", "Blue")]


_ALINHA = {"Left": "esquerda", "Center": "centro", "Right": "direita"}


def _caixa(m: mqp.Mqp, cx: dict) -> dict:
    letra = next((c["lista"] for c in cx["caracteres"] if c.get("lista")), 0)
    caixa = {
        "texto": mqp.texto_da_caixa(cx),
        "fonte": fonte(m.valor(letra, "Text.Font.Name") or ""),
        "corpo": round(_num(m.valor(letra, "Text.Font.Size"), 0.0833333), 6),
        "cor": _cor(m, letra, "Material.Main.Base"),
        "alinhamento": _ALINHA.get(cx.get("justify") or "Left", "esquerda"),
        "posicao": {"x": round(_num(m.valor(cx["lista"], "Transform.Position.X")), 6),
                    "y": round(_num(m.valor(cx["lista"], "Transform.Position.Y")), 6)},
        "contorno": None,
        "sombra": None,
    }
    if _sim(m.valor(letra, "Material.Profile.Outline.Enable")):
        caixa["contorno"] = {"cor": _cor(m, letra, "Material.Profile.Outline.Solid"),
                             "espessura": _num(m.valor(letra, "Effect.ProfileScale"), 1.0)}
    if _sim(m.valor(letra, "Shadow.Enable")):
        caixa["sombra"] = {"cor": _cor(m, letra, "Shadow.Color"),
                           "deslocamento": [_num(m.valor(letra, "Shadow.Offset.X")),
                                            _num(m.valor(letra, "Shadow.Offset.Y"))],
                           "suavidade": _num(m.valor(letra, "Shadow.Softness"))}
    return caixa


def ler(te) -> dict | None:
    if not e_titulo(te):
        return None
    eid = str(getattr(te, "effect_id", "") or "").upper()
    attrs = _attrs(te)
    blobs = _blobs(te)
    mqp_txt = next((b.decode("utf-8", "replace") for u, b in blobs if u.lower() == UUID_MQP), None)

    if mqp_txt:
        return bloco(eid, "", [], mqp_txt)

    textos, deko = [], None
    for _u, b in blobs:
        if b.startswith(b"<deko>"):
            deko = b.decode("utf-8", "replace")
        elif b.endswith(b"\x00") and 1 < len(b) < 4096 and b"\x00" not in b[:-1]:
            try:
                textos.append(b[:-1].decode("utf-8"))
            except UnicodeDecodeError:
                continue
    if not textos:
        tt = attrs.get("_TTEXT")
        if tt:
            textos = [bytes(tt).split(b"T+: ", 1)[-1].strip(b" \x00").decode("utf-8", "replace")]
    return bloco(eid, attrs.get("_EFFECT_PLUGIN_NAME") or "", textos, deko=deko)


def bloco(eid: str, nome: str, textos: list[str], mqp_txt: str | None = None,
          deko: str | None = None, titulador: str | None = None) -> dict:
    if mqp_txt:
        m = mqp.ler(mqp_txt)
        caixas = [_caixa(m, cx) for cx in m.caixas if cx["caracteres"]]
        if not caixas and any((t or "").strip() for t in textos):
            return bloco(eid, nome, textos, None, deko, titulador)
        fundo = None
        if _sim(m.valor(m.lista_cena, "Global.BG.Enable")):
            fundo = {"cor": _cor(m, m.lista_cena, "Global.BG.Color"),
                     "opacidade": _num(m.valor(m.lista_cena, "Global.BG.Color.Opacity"), 1.0),
                     "tela_cheia": True}
        return {"titulador": "Avid Titler+", "estilo_lido": True, "caixas": caixas,
                "fundo": fundo, "avid": {"effect_id": eid, "mqp": mqp_txt}}
    vistos: list[str] = []
    for t in textos:
        if t not in vistos:
            vistos.append(t)
    caixas = [{"texto": t, "fonte": {"familia": "", "estilo": ""}, "corpo": None, "cor": None,
               "alinhamento": "centro", "posicao": None, "contorno": None, "sombra": None}
              for t in vistos]
    return {"titulador": titulador or (f"{nome} Legacy" if eid == EID_LEGACY else (nome or "título do Avid")),
            "estilo_lido": False, "caixas": caixas, "fundo": None,
            "avid": {"effect_id": eid, "deko": deko}}


def do_aaf(operacao: str, textos: list[str], mqp_txt: str | None = None) -> dict | None:
    if not mqp_txt and not any((t or "").strip() for t in textos):
        return None
    legado = "legacy" in (operacao or "").lower() or "title tool" in (operacao or "").lower()
    eid = EID_LEGACY if legado else EID_TITLER
    return bloco(eid, (operacao or "").replace(" Legacy", ""), textos, mqp_txt,
                 titulador=None if mqp_txt else (operacao or None))
