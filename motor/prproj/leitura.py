from __future__ import annotations

import math
import re
import struct

from .mascara import ler_mascaras, para_fonte_de_dentro, receita
from .projeto import TPS, Obj, Projeto

_NAO_EFEITO = {"AE.ADBE Motion", "AE.ADBE Opacity", "AE.ADBE MPEG.SourceSettings",
               "AE.ADBE Text", "AE.ADBE Graphic Group", None}


def _texto_do_blob(b64: str) -> tuple[str | None, str | None]:
    import base64
    import struct
    try:
        b = base64.b64decode(b64)
    except ValueError:
        return None, None
    achadas, i = [], 0
    while i + 4 <= len(b):
        n = struct.unpack("<I", b[i:i + 4])[0]
        if 1 <= n <= 4000 and i + 4 + n <= len(b):
            s = b[i + 4:i + 4 + n]
            try:
                txt = s.decode("utf-8")
                limpo = txt.replace("\r\n", "\n").replace("\r", "\n")
                if limpo.replace("\n", "").replace("\t", " ").isprintable() \
                        and any(c.isalpha() for c in limpo):
                    achadas.append(limpo)
                    i += 4 + n
                    continue
            except UnicodeDecodeError:
                pass
        i += 1
    fonte = next((s for s in achadas[:-1] if re.fullmatch(r"[A-Za-z0-9][\w.-]*", s)), None)
    textos = [s for s in achadas if s != fonte]
    return (textos[-1] if textos else None), fonte
def _corpo_do_blob(b64: str) -> float | None:
    import base64
    try:
        b = base64.b64decode(b64)[12:]

        def u32(o):
            return struct.unpack_from("<I", b, o)[0]

        def campo(t, i):
            v = t - struct.unpack_from("<i", b, t)[0]
            n = (struct.unpack_from("<H", b, v)[0] - 4) // 2
            return t + struct.unpack_from("<H", b, v + 4 + 2 * i)[0] if i < n else t
        raiz = u32(0)
        doc = campo(raiz, 0)
        doc += u32(doc)
        vec = campo(doc, 0)
        vec += u32(vec)
        trecho = vec + 4 + u32(vec + 4)
        estilo = campo(trecho, 1)
        if estilo == trecho:
            return None
        estilo += u32(estilo)
        o = campo(estilo, 1)
        if o == estilo:
            return None
        v = struct.unpack_from("<f", b, o)[0]
        return v if 1.0 <= v <= 2000.0 else None
    except (ValueError, struct.error, IndexError):
        return None


RETIME = {None: None, "0": "nearest", "1": "frame_blend", "2": "optical_flow"}
SINAL_ROTACAO_RESOLVE = -1.0


def _int(v, padrao=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return padrao


def _float(v, padrao=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return padrao


def frames_para_tc(f: int, fps_nominal: int) -> str:
    f = max(0, int(f))
    s, q = divmod(f, fps_nominal)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{q:02d}"


class Geo:
    __slots__ = ("esc", "rot", "tx", "ty")

    def __init__(self, esc=1.0, rot=0.0, tx=0.0, ty=0.0):
        self.esc, self.rot, self.tx, self.ty = esc, rot, tx, ty

    def apos(self, dentro: "Geo") -> "Geo":
        c, s = math.cos(math.radians(self.rot)), math.sin(math.radians(self.rot))
        x, y = dentro.tx * self.esc, dentro.ty * self.esc
        return Geo(self.esc * dentro.esc, self.rot + dentro.rot,
                   c * x - s * y + self.tx, s * x + c * y + self.ty)


def _valor(proj: Projeto, prm: Obj, t_midia: int | None):
    kfs = proj.campo(prm, "Keyframes")
    if kfs and t_midia is not None:
        pts = []
        for k in kfs.split(";"):
            partes = k.split(",")
            if len(partes) >= 2 and partes[0].lstrip("-").isdigit():
                pts.append((int(partes[0]), partes[1]))
        if pts:
            pts.sort()
            if t_midia <= pts[0][0]:
                return pts[0][1], True
            if t_midia >= pts[-1][0]:
                return pts[-1][1], True
            for (ta, va), (tb, vb) in zip(pts, pts[1:]):
                if ta <= t_midia <= tb:
                    f = (t_midia - ta) / (tb - ta) if tb > ta else 0.0
                    return _interp(va, vb, f), True
    cv = proj.campo(prm, "CurrentValue")
    if cv is not None:
        return cv, bool(kfs)
    sk = proj.campo(prm, "StartKeyframe")
    if sk:
        p = sk.split(",")
        if len(p) >= 2:
            return p[1], bool(kfs)
    return None, bool(kfs)


def _interp(a: str, b: str, f: float) -> str:
    if ":" in a and ":" in b:
        (ax, ay), (bx, by) = (map(float, a.split(":")), map(float, b.split(":")))
        return f"{ax + (bx - ax) * f}:{ay + (by - ay) * f}"
    return str(float(a) + (float(b) - float(a)) * f)


def _mascaras_ou_nada(proj: Projeto, comp: Obj) -> list[dict] | None:
    import binascii
    import struct as _struct
    try:
        return ler_mascaras(proj, comp)
    except (_struct.error, binascii.Error, ValueError, IndexError, TypeError):
        return None


class Motion:

    def __init__(self, proj: Projeto, item: Obj):
        self.estatico: dict = {}
        self.pontos: dict[str, list[tuple[int, str]]] = {}
        self.efeitos: list[str] = []
        self.curva = False
        self.crop = {"l": 0.0, "t": 0.0, "r": 0.0, "b": 0.0}
        self.crop_aviso: list[str] = []
        self.crop_suave = 0.0
        self.flip = [False, False]
        self.luma_key = False
        self.fusion: list[tuple[str, list[dict], dict]] = []
        self.texto: dict | None = None
        self.grupo_escala_kf: list[tuple[int, float]] = []
        self.grupo_curva = False
        cadeia = proj.ref(item, "Components")
        if cadeia is None:
            return
        for comp in proj.refs(cadeia, "Component"):
            mn = proj.campo(comp, "MatchName")
            if mn == "AE.ADBE Text":
                tx: dict = {"animado": False}
                for prm in proj.refs(comp, "Param"):
                    nome = proj.campo(prm, "Name")
                    if nome == "Source Text":
                        b64 = proj.valor_inicial(prm)
                        if b64:
                            tx["texto"], tx["fonte"] = _texto_do_blob(b64)
                            tx["corpo_px"] = _corpo_do_blob(b64)
                    elif nome in ("Position", "Scale", "Opacity", "Anchor Point") \
                            and nome not in tx:
                        tx[nome], a = _valor(proj, prm, None)
                        tx["animado"] = tx["animado"] or a
                self.texto = tx
                continue
            if mn == "AE.ADBE Graphic Group":
                for prm in proj.refs(comp, "Param"):
                    nome = proj.campo(prm, "Name")
                    if nome not in ("Scale", "Position", "Anchor Point"):
                        continue
                    v, a = _valor(proj, prm, None)
                    kfs = proj.campo(prm, "Keyframes")
                    if kfs:
                        v = kfs.split(";")[0].split(",")[1]
                    chave = {"Scale": "__grupo_escala", "Position": "__grupo_posicao",
                             "Anchor Point": "__grupo_ancora"}[nome]
                    self.estatico[chave] = v
                    if nome == "Scale" and kfs:
                        self.grupo_escala_kf = sorted(
                            (int(k.split(",")[0]), float(k.split(",")[1])) for k in kfs.split(";")
                            if k.count(",") >= 1 and k.split(",")[0].lstrip("-").isdigit())
                        self.grupo_curva = any(len(k.split(",")) > 2 and k.split(",")[2] not in ("0", "")
                                               for k in kfs.split(";"))
                    elif kfs:
                        self.estatico["__grupo_animado"] = True
                continue
            if mn == "AE.ADBE Horizontal Flip":
                self.flip[0] = not self.flip[0]
                continue
            if mn == "AE.ADBE Vertical Flip":
                self.flip[1] = not self.flip[1]
                continue
            if mn == "AE.ADBE Legacy Key Luma":
                self.luma_key = True
                continue
            if mn == "AE.ADBE AECrop" and proj.refs(comp, "SubComponent"):
                ms = _mascaras_ou_nada(proj, comp)
                if ms:
                    self.fusion.append(("recorte", ms, {}))
                else:
                    self.crop_aviso.append("crop com máscara ilegível: não traduzido, refazer")
                    self.efeitos.append("crop com máscara (ilegível)")
                continue
            if mn == "AE.ADBE Basic 3D":
                prm = {}
                for x in proj.refs(comp, "Param"):
                    v, animado = _valor(proj, x, None)
                    prm[proj.campo(x, "Name")] = v
                    if animado:
                        self.crop_aviso.append(f"Basic 3D com {proj.campo(x, 'Name')} animado: "
                                               "usado o valor fixo")
                if (_float(prm.get("Distance to Image"), 0.0) or 0.0) != 0.0:
                    self.crop_aviso.append("Basic 3D com Distance to Image: perspectiva não calibrada")
                if (prm.get("Specular Highlight") or "").lower() == "true":
                    self.crop_aviso.append("Basic 3D com Specular Highlight: brilho não traduzido")
                sw = _float(prm.get("Swivel"), 0.0) or 0.0
                ti = _float(prm.get("Tilt"), 0.0) or 0.0
                if sw or ti:
                    self.fusion.append(("basic3d", [], {"swivel": sw, "tilt": ti}))
                continue
            if mn == "AE.ADBE Gaussian Blur 2":
                prm = {}
                for i, x in enumerate(proj.refs(comp, "Param")):
                    v, animado = _valor(proj, x, None)
                    nome = "Repeat Edge Pixels" if i == 2 else proj.campo(x, "Name")
                    prm[nome] = v
                    if animado:
                        self.crop_aviso.append(f"blur com {nome} animado: usado o valor fixo")
                ms = _mascaras_ou_nada(proj, comp)
                if ms is None:
                    self.crop_aviso.append("blur com máscara ilegível: não traduzido, refazer")
                    self.efeitos.append("Gaussian Blur com máscara (ilegível)")
                    continue
                self.fusion.append(("desfoque", ms, {
                    "px": _float(prm.get("Blurriness"), 0.0) or 0.0,
                    "direcao": _int(prm.get("Blur Dimensions"), 0),
                    "repetir_bordas": (prm.get("Repeat Edge Pixels") or "").lower() == "true"}))
                continue
            if mn == "AE.ADBE AECrop":
                for prm in proj.refs(comp, "Param"):
                    nome = proj.campo(prm, "Name")
                    v, animado = _valor(proj, prm, None)
                    lado = {"Left": "l", "Top": "t", "Right": "r", "Bottom": "b"}.get(nome)
                    if lado:
                        self.crop[lado] = max(self.crop[lado], (_float(v, 0.0) or 0.0) / 100.0)
                        if animado:
                            self.crop_aviso.append("crop animado: usado o valor fixo")
                    elif nome == " " and (v or "").lower() == "true":
                        self.crop_aviso.append("crop com Zoom ligado: não traduzido")
                    elif nome == "Edge Feather" and (_float(v, 0.0) or 0.0) > 0:
                        self.crop_suave = max(getattr(self, "crop_suave", 0.0), _float(v, 0.0))
                continue
            if mn == "AE.ADBE Motion":
                for prm in proj.refs(comp, "Param"):
                    nome = proj.campo(prm, "Name")
                    self.estatico[nome], _ = _valor(proj, prm, None)
                    lado = {"Crop Left": "l", "Crop Top": "t", "Crop Right": "r",
                            "Crop Bottom": "b"}.get(nome)
                    if lado:
                        self.crop[lado] = max(self.crop[lado],
                                              (_float(self.estatico[nome], 0.0) or 0.0) / 100.0)
                    kfs = proj.campo(prm, "Keyframes")
                    pts = []
                    for k in (kfs or "").split(";"):
                        partes = k.split(",")
                        if len(partes) >= 2 and partes[0].lstrip("-").isdigit():
                            pts.append((int(partes[0]), partes[1]))
                            if len(partes) > 2 and partes[2] not in ("0", ""):
                                self.curva = True
                    if pts:
                        self.pontos[nome] = sorted(pts)
            elif mn not in _NAO_EFEITO:
                self.efeitos.append(proj.campo(comp, "DisplayName") or mn)

    @property
    def animado(self) -> bool:
        return bool(self.pontos)

    def tempos(self) -> list[int]:
        return sorted({t for pts in self.pontos.values() for t, _v in pts})

    def em(self, t_midia: float) -> dict:
        out = dict(self.estatico)
        for nome, pts in self.pontos.items():
            if t_midia <= pts[0][0]:
                out[nome] = pts[0][1]
            elif t_midia >= pts[-1][0]:
                out[nome] = pts[-1][1]
            else:
                for (ta, va), (tb, vb) in zip(pts, pts[1:]):
                    if ta <= t_midia <= tb:
                        out[nome] = _interp(va, vb, (t_midia - ta) / (tb - ta) if tb > ta else 0.0)
                        break
        return out


def _geo_do_item(params: dict, fonte: tuple[int, int], quadro: tuple[int, int],
                 escalar_ao_quadro: bool) -> Geo:
    fw, fh = fonte
    qw, qh = quadro
    base = min(qw / fw, qh / fh) if (escalar_ao_quadro and fw and fh) else 1.0
    esc = (_float(params.get("Scale"), 100.0) or 0.0) / 100.0
    if (params.get(" ") or "").strip().lower() in ("false", "0") and params.get("Scale Width"):
        esc = (esc + (_float(params.get("Scale Width"), 100.0) or 0.0) / 100.0) / 2
    esc *= base
    rot = _float(params.get("Rotation"), 0.0) or 0.0
    px, py = _ponto(params.get("Position"), (0.5, 0.5))
    ax, ay = _ponto(params.get("Anchor Point"), (0.5, 0.5))
    dx, dy = (0.5 - ax) * fw * esc, (0.5 - ay) * fh * esc
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    tx = (px - 0.5) * qw + c * dx - s * dy
    ty = (py - 0.5) * qh + s * dx + c * dy
    return Geo(esc, rot, tx, ty)


def _crop_para_fonte(crop: dict, g: "Geo", quadro_take: tuple[int, int],
                     fonte: tuple[int, int], crop_fonte: dict) -> tuple[dict, str | None]:
    qw, qh = quadro_take
    fw, fh = fonte
    if not (qw and qh and fw and fh and g.esc):
        return dict(crop_fonte), "crop num plano aninhado: quadro desconhecido, não traduzido"
    xl = (crop["l"] - 0.5) * qw
    xr = (0.5 - crop["r"]) * qw
    yt = (crop["t"] - 0.5) * qh
    yb = (0.5 - crop["b"]) * qh
    fl, fr = (xl - g.tx) / g.esc, (xr - g.tx) / g.esc
    ft, fb = (yt - g.ty) / g.esc, (yb - g.ty) / g.esc
    novo = {"l": fl / fw + 0.5, "r": 0.5 - fr / fw, "t": ft / fh + 0.5, "b": 0.5 - fb / fh}
    out = {k: min(1.0, max(0.0, max(novo[k], crop_fonte.get(k, 0.0)))) for k in novo}
    return out, ("crop num plano aninhado com rotação dentro do take: aproximado"
                 if abs(g.rot) > 1e-3 else None)


def _ponto(v: str | None, padrao: tuple[float, float]) -> tuple[float, float]:
    if not v or ":" not in v:
        return padrao
    try:
        x, y = v.split(":")[:2]
        return float(x), float(y)
    except ValueError:
        return padrao


def _retangulo(v: str | None) -> tuple[int, int]:
    if not v:
        return (0, 0)
    p = [_int(x) for x in v.split(",")]
    return (p[2] - p[0], p[3] - p[1]) if len(p) == 4 else (0, 0)


class Leitor:
    def __init__(self, proj: Projeto):
        self.p = proj
        self._todas = list(proj.de_tipo("Sequence"))
        self._por_uid = {s.uid: s for s in self._todas if s.uid}

    def eh_multicam(self, s: Obj) -> bool:
        return "<Source.Monitor.Multicam.SwitchOnMulticamName>" in self.p.corpo(s)

    def nest_do_item(self, item: Obj) -> Obj | None:
        p = self.p
        vc = self._clip(item)
        fonte = p.ref(vc, "Source") if vc is not None else None
        if fonte is None or fonte.tag != "VideoSequenceSource" or p.campo(vc, "IsMulticam") == "true":
            return None
        seq = p.ref(fonte, "Sequence")
        if seq is None or self.eh_multicam(seq) or self.eh_merged(seq):
            return None
        return seq

    def eh_merged(self, s: Obj) -> bool:
        return "<BE.Sequence.IsMergedClip>true</BE.Sequence.IsMergedClip>" in self.p.corpo(s)

    def trilhas(self, s: Obj, tipo: str = "Video") -> list[tuple[Obj, list[Obj]]]:
        for g in self.p.refs(s, "Second"):
            if g.tag == tipo + "TrackGroup":
                return [(t, self.p.refs(t, "TrackItem")) for t in self.p.refs(g, "Track")]
        return []

    def quadro(self, s: Obj) -> tuple[tuple[int, int], int]:
        for g in self.p.refs(s, "Second"):
            if g.tag == "VideoTrackGroup":
                return _retangulo(self.p.campo(g, "FrameRect")), _int(self.p.campo(g, "FrameRate"))
        return (0, 0), 0

    def sequencias_de_montagem(self) -> list[dict]:
        out = []
        for s in self._todas:
            nome = self.p.nome_da_sequencia(s)
            if self.eh_merged(s):
                continue
            n = sum(1 for _t, its in self.trilhas(s) for i in its if i.tag == "VideoClipTrackItem")
            if n <= 1:
                continue
            (w, h), tpf = self.quadro(s)
            fim = max((_int(self.p.campo(i, "End")) for _t, its in self.trilhas(s) for i in its),
                      default=0)
            out.append({"uid": s.uid, "nome": nome, "planos": n, "largura": w, "altura": h,
                        "fps": TPS / tpf if tpf else None,
                        "duracao_s": fim / TPS})
        return sorted(out, key=lambda d: d["nome"].lower())

    def _clip(self, item: Obj) -> Obj | None:
        sc = self.p.ref(item, "SubClip")
        return self.p.ref(sc, "Clip") if sc else None

    def _folhas(self, item: Obj, t0: int, t1: int, quadro: tuple[int, int], prof: int,
                avisos: list) -> list[dict]:
        p = self.p
        vc = self._clip(item)
        if vc is None:
            return []
        ini = _int(p.campo(item, "Start"))
        entrada = _int(p.campo(vc, "InPoint"))
        vel = _float(p.campo(vc, "PlaybackSpeed"), 1.0) or 1.0
        if vel < 0:
            avisos.append("velocidade reversa: lida como direta")
            vel = abs(vel)
        para_tras = p.campo(vc, "PlayBackwards") == "true"

        def u(t):
            return entrada + (t - ini) * vel
        fonte = p.ref(vc, "Source")
        if fonte is None:
            return []
        stf = p.campo(vc, "ScaleToFramePolicy") == "1"
        mo = Motion(p, item)
        efeitos = mo.efeitos
        if mo.curva:
            avisos.append("keyframe com curva (Bézier): aproximado como linear")
        avisos.extend(mo.crop_aviso)
        if (mo.estatico.get(" ") or "").strip().lower() in ("false", "0"):
            avisos.append("escala não uniforme: usada a média")
        kf_c = [ini + (km - entrada) / vel for km in mo.tempos()]

        if fonte.tag == "VideoMediaSource":
            media = p.ref(fonte, "Media")
            if media is None:
                return []
            vs = p.ref(media, "VideoStream")
            tam = _retangulo(p.campo(vs, "FrameRect")) if vs else (0, 0)
            tpf_m = _int(p.campo(vs, "FrameRate")) if vs else 0
            def geo_fn(t, mo=mo, tam=tam, quadro=quadro, stf=stf, u=u):
                return _geo_do_item(mo.em(u(t)), tam, quadro, stf)
            sentido, src0 = 1, u(t0)
            if para_tras:
                dur_m = _int(p.campo(vs, "Duration")) if vs else 0
                sentido = -1
                src0 = (dur_m - tpf_m) - entrada - (t0 - ini) * vel
                if mo.animado:
                    avisos.append("plano reverso com keyframe: tempo dos keyframes a conferir")
            return [{"t0": t0, "t1": t1, "media": media, "src_in": round(src0), "vel": vel,
                     "sentido": sentido,
                     "geo": geo_fn(t0), "geo_fn": geo_fn, "kf": kf_c,
                     "fonte_px": tam, "tpf_midia": tpf_m,
                     "parado": bool(vs) and p.campo(vs, "IsStill") == "true"
                               and p.campo(media, "FilePath", ) is not None
                               and not _e_video(p.campo(media, "FilePath")),
                     "efeitos": efeitos, "escalar_ao_quadro": stf, "crop": dict(mo.crop),
                     "crop_suave": mo.crop_suave,
                     "flip": list(mo.flip), "luma_key": mo.luma_key,
                     "fusion": [receita(ef, ms, tam, avisos, **prm) for ef, ms, prm in mo.fusion],
                     "texto": mo.texto, "grupo_escala": mo.estatico.get("__grupo_escala"),
                     "grupo_posicao": mo.estatico.get("__grupo_posicao"),
                     "grupo_ancora": mo.estatico.get("__grupo_ancora"),
                     "grupo_escala_kf": [(ini + (km - entrada) / vel, v)
                                         for km, v in mo.grupo_escala_kf],
                     "grupo_curva": mo.grupo_curva,
                     "grupo_animado": mo.estatico.get("__grupo_animado"),
                     "retime": RETIME.get(p.campo(vc, "TimeInterpolationType"))}]

        if fonte.tag != "VideoSequenceSource":
            avisos.append(f"fonte de tipo {fonte.tag} ignorada")
            return []
        seq = p.ref(fonte, "Sequence")
        if seq is None or prof > 8:
            return []
        tam_q, _tpf = self.quadro(seq)
        trilhas = [(t, its) for t, its in self.trilhas(seq)]
        if p.campo(vc, "IsMulticam") == "true":
            k = _int(p.campo(vc, "SelectedTrackIndex"))
            trilhas = trilhas[k:k + 1]
        multicam = p.campo(vc, "IsMulticam") == "true"
        if para_tras:
            avisos.append("sequência aninhada tocada para trás: não traduzido (lida como direta)")
        u0, u1 = u(t0), u(t1)
        out = []
        camada_base = 0
        for t, its in trilhas:
            if "<IsMuted>true</IsMuted>" in p.corpo(t):
                continue
            usou = 0
            for it in its:
                if it.tag != "VideoClipTrackItem" or "<IsMuted>true</IsMuted>" in p.corpo(it):
                    continue
                s_i = _int(p.campo(it, "Start"))
                e_i = _int(p.campo(it, "End"))
                a, b = max(u0, s_i), min(u1, e_i)
                if b <= a:
                    continue
                ta = round(ini + (a - entrada) / vel)
                tb = round(ini + (b - entrada) / vel)
                for f in self._folhas(it, a, b, tam_q, prof + 1, avisos):
                    fa = round(ini + (f["t0"] - entrada) / vel)
                    fb = round(ini + (f["t1"] - entrada) / vel)
                    if f.get("retime") is None:
                        f["retime"] = RETIME.get(p.campo(vc, "TimeInterpolationType"))

                    def geo_c(t, dentro=f["geo_fn"], mo=mo, tam_q=tam_q, quadro=quadro,
                              stf=stf, u=u):
                        return _geo_do_item(mo.em(u(t)), tam_q, quadro, stf).apos(dentro(u(t)))
                    kf = kf_c + [ini + (k - entrada) / vel for k in f["kf"]]
                    if any(v > 0 for v in mo.crop.values()):
                        f["crop"], aviso = _crop_para_fonte(mo.crop, f["geo_fn"](a), tam_q,
                                                            f["fonte_px"], f.get("crop") or {})
                        f["crop_suave"] = max(f.get("crop_suave") or 0.0, mo.crop_suave)
                        if aviso:
                            avisos.append(aviso)
                    f["flip"] = [f.get("flip", [False, False])[k] != mo.flip[k] for k in (0, 1)]
                    f["luma_key"] = f.get("luma_key") or mo.luma_key
                    for ef, ms, prm in mo.fusion:
                        if ef == "basic3d":
                            g = f["geo_fn"](a)
                            fw_d = f["fonte_px"][0]
                            if (abs(g.rot) < 1e-3 and abs(g.tx) < 0.5 and abs(g.ty) < 0.5
                                    and fw_d and abs(g.esc * fw_d - tam_q[0]) < 1):
                                f["fusion"] = (f.get("fusion") or []) + [
                                    receita(ef, ms, f["fonte_px"], avisos, **prm)]
                            else:
                                avisos.append("Basic 3D num plano aninhado: não traduzido, refazer")
                            continue
                        r = receita(ef, ms, tam_q, avisos, **prm)
                        g = f["geo_fn"](a)
                        for m in r["mascaras"]:
                            m["vertices"] = [[su, *map(list, x)] for su, *x in para_fonte_de_dentro(
                                [(su, tuple(v), tuple(i), tuple(o)) for su, v, i, o in m["vertices"]],
                                g, tam_q, f["fonte_px"])]
                            m["suave_px"] /= g.esc or 1.0
                        if ef == "desfoque":
                            r["px"] /= g.esc or 1.0
                        r["fonte_px"] = list(f["fonte_px"])
                        f["fusion"] = (f.get("fusion") or []) + [r]
                    f.update(t0=max(ta, fa), t1=min(tb, fb), vel=f["vel"] * vel,
                             geo_fn=geo_c, geo=geo_c(max(ta, fa)), kf=kf,
                             efeitos=efeitos + f["efeitos"])
                    if not multicam:
                        f["camada"] = camada_base + f.get("camada", 0)
                        usou = max(usou, f.get("camada", 0) - camada_base + 1)
                    out.append(f)
            camada_base += usou
        return out

    def _canais(self, clip: Obj) -> list[int]:
        p = self.p
        out = []
        for oid in re.findall(r'<SecondaryContentItem Index="\d+" ObjectRef="(\d+)"/>', p.corpo(clip)):
            sc = p.obj(oid)
            ci = p.campo(sc, "ChannelIndex") if sc is not None else None
            out.append(_int(ci, 0))
        return out or [0]

    def _folhas_audio(self, item: Obj, t0: int, t1: int, prof: int, avisos: list) -> list[dict]:
        p = self.p
        sc = p.ref(item, "SubClip")
        clip = p.ref(sc, "Clip") if sc else None
        if clip is None:
            return []
        ini = _int(p.campo(item, "Start"))
        entrada = _int(p.campo(clip, "InPoint"))
        vel = _float(p.campo(clip, "PlaybackSpeed"), 1.0) or 1.0
        fonte = p.ref(clip, "Source")
        aninhado = fonte is not None and fonte.tag != "AudioMediaSource"
        if abs(vel - 1.0) > 1e-6 and (vel < 0 or aninhado):
            avisos.append("áudio com velocidade mudada: entrou a 100%")
            vel = 1.0

        def u(t):
            return entrada + (t - ini) * vel
        canais = self._canais(clip)
        if fonte is None:
            return []
        if fonte.tag == "AudioMediaSource":
            media = p.ref(fonte, "Media")
            if media is None:
                return []
            return [{"t0": t0, "t1": t1, "media": media, "src_in": u(t0), "canais": canais, "vel": vel}]
        if fonte.tag != "AudioSequenceSource" or prof > 8:
            avisos.append(f"fonte de áudio {fonte.tag} ignorada")
            return []
        seq = p.ref(fonte, "Sequence")
        if seq is None:
            return []
        trilhas = self.trilhas(seq, "Audio")
        u0, u1 = u(t0), u(t1)
        out = []
        for k, c in enumerate(canais):
            if c >= len(trilhas):
                avisos.append("canal de take aninhado sem trilha correspondente: ignorado")
                continue
            trilha, its = trilhas[c]
            if "<IsMuted>true</IsMuted>" in p.corpo(trilha):
                continue
            for it in its:
                if it.tag != "AudioClipTrackItem" or "<IsMuted>true</IsMuted>" in p.corpo(it):
                    continue
                a, b = max(u0, _int(p.campo(it, "Start"))), min(u1, _int(p.campo(it, "End")))
                if b <= a:
                    continue
                for f in self._folhas_audio(it, a, b, prof + 1, avisos):
                    f.update(t0=round(ini + (f["t0"] - entrada) / vel),
                             t1=round(ini + (f["t1"] - entrada) / vel), canais=f["canais"][:1],
                             pos=k)
                    out.append(f)
        return out

    def _audio(self, s: Obj, q, nominal: int, fps: float, avisos: dict) -> list[dict]:
        p = self.p
        trilhas = self.trilhas(s, "Audio")
        brutos, largura = [], {}
        for n, (trilha, itens) in enumerate(trilhas, start=1):
            desligada = "<IsMuted>true</IsMuted>" in p.corpo(trilha)
            for it in itens:
                if it.tag != "AudioClipTrackItem":
                    continue
                av: list[str] = []
                folhas = self._folhas_audio(it, _int(p.campo(it, "Start")), _int(p.campo(it, "End")),
                                            0, av)
                for x in av:
                    avisos[x] = avisos.get(x, 0) + 1
                sc = p.ref(it, "SubClip")
                clip = p.ref(sc, "Clip") if sc else None
                nc = len(self._canais(clip)) if clip is not None else 1
                largura[n] = max(largura.get(n, 1), nc)
                brutos.append((n, it, folhas, desligada or "<IsMuted>true</IsMuted>" in p.corpo(it)))
        base, prox = {}, 1000
        for n in range(1, len(trilhas) + 1):
            base[n] = prox
            prox += largura.get(n, 1)
        tpf_tl = TPS / fps
        segs: list[dict] = []
        ocupado: dict[int, list] = {}
        for n, it, folhas, desligado in brutos:
            efeitos = []
            volume_db, volume_kf = None, []
            cad = p.ref(it, "Components")
            for c in (p.refs(cad, "Component") if cad is not None else []):
                mn = p.campo(c, "MatchName") or ""
                nome = p.campo(c, "DisplayName") or mn
                if nome == "Volume":
                    volume_db = _volume_db(p, c, avisos)
                    volume_kf = _volume_kf(p, c)
                if mn and nome:
                    efeitos.append({"name": nome, "category": "audio", "reproducible": False,
                                    "params": {}})
            for f in folhas:
                a, b = q(f["t0"]), q(f["t1"])
                if b <= a:
                    continue
                caminho = p.campo(f["media"], "FilePath") or ""
                canais = f["canais"]
                for k, canal in enumerate(canais):
                    trilha = base[n] + (f["pos"] if "pos" in f else k)
                    segs.append({
                        "timeline_tc_in": frames_para_tc(a, nominal),
                        "timeline_tc_out": frames_para_tc(b, nominal), "duration_frames": b - a,
                        "source_start_frames": round(f["src_in"] / tpf_tl), "source_fps": fps,
                        "clip_name": caminho.rsplit("/", 1)[-1] or "?", "track": trilha,
                        "canal_arquivo": canal + 1,
                        "media_ref": {"mob_name": caminho.rsplit("/", 1)[-1], "url": "",
                                      "local_path": caminho, "descriptor_type": "prproj"},
                        "is_gap": False, "is_transition": False, "is_effect": False,
                        "is_audio": True, "disabled": desligado, "effects": efeitos,
                        "speed_ratio": 1.0 / f.get("vel", 1.0), "relative_speed": 1.0 / f.get("vel", 1.0),
                        "conform_ratio": 1.0, "phase_offset": 0, "motion_effect_type": None,
                        "has_motion_effect": abs(f.get("vel", 1.0) - 1.0) > 1e-6,
                        "source_offset_map": [], "speed_curve": [], "speed_keyframes": [],
                        **_volume_no_trecho(volume_db, volume_kf, f, a, b, tpf_tl, avisos),
                        "prproj": {"audio": True, "trilha_premiere": n}})
                    ocupado.setdefault(trilha, []).append((a, b))
        for n, (trilha, itens) in enumerate(trilhas, start=1):
            for it in itens:
                if "Transition" not in it.tag:
                    continue
                t = self._transicao(it, base[n], itens, q, nominal)
                for k in range(largura.get(n, 1)):
                    segs.append({**t, "track": base[n] + k, "is_audio": True})
        for trilha in sorted(ocupado):
            for g in _lacunas(ocupado[trilha], trilha, nominal, fps):
                segs.append({**g, "is_audio": True})
        return segs

    def sequencia(self, chave: str) -> Obj:
        if chave in self._por_uid:
            return self._por_uid[chave]
        mesmas = [s for s in self._todas if self.p.nome_da_sequencia(s) == chave]
        if len(mesmas) > 1:
            montagem = {x["uid"] for x in self.sequencias_de_montagem()}
            mesmas = [s for s in mesmas if s.uid in montagem] or mesmas
        if not mesmas:
            raise KeyError(f"sequência não encontrada: {chave}")
        if len(mesmas) > 1:
            raise ValueError(f"há {len(mesmas)} sequências de montagem chamadas {chave!r}: escolha "
                             "pela lista do painel")
        return mesmas[0]

    def ler(self, nome: str, audio: bool = True) -> dict:
        p = self.p
        s = self.sequencia(nome)
        nome = p.nome_da_sequencia(s)
        quadro, tpf = self.quadro(s)
        fps = TPS / tpf if tpf else 24.0
        nominal = round(fps)
        avisos: dict[str, int] = {}
        segs: list[dict] = []

        def q(t):
            return round(t / tpf)

        brutos = []
        for n_trilha, (trilha, itens) in enumerate(self.trilhas(s), start=1):
            for it in itens:
                if it.tag == "VideoClipTrackItem":
                    av: list[str] = []
                    brutos.append((n_trilha, it, self._folhas(it, _int(p.campo(it, "Start")),
                                                              _int(p.campo(it, "End")),
                                                              quadro, 0, av), av))
        camadas: dict[int, int] = {}
        for n_trilha, _it, folhas, _av in brutos:
            for f in folhas:
                camadas[n_trilha] = max(camadas.get(n_trilha, 0), f.get("camada", 0))
        base, prox = {}, 1
        for n in range(1, len(self.trilhas(s)) + 1):
            base[n] = prox
            prox += camadas.get(n, 0) + 1
        if any(camadas.values()):
            avisos["aninhamento com várias trilhas: cada camada ganhou uma trilha própria"] = \
                sum(1 for v in camadas.values() if v)
        folhas_do = {id(it): (folhas, av) for _n, it, folhas, av in brutos}

        for n_trilha, (trilha, itens) in enumerate(self.trilhas(s), start=1):
            desligada = "<IsMuted>true</IsMuted>" in p.corpo(trilha)
            ocupado: dict[int, list] = {}
            for it in itens:
                ini, fim = _int(p.campo(it, "Start")), _int(p.campo(it, "End"))
                if it.tag == "VideoTransitionTrackItem":
                    segs.append(self._transicao(it, base[n_trilha], itens, q, nominal))
                    continue
                if it.tag != "VideoClipTrackItem":
                    continue
                folhas, av = folhas_do[id(it)]
                item_desligado = "<IsMuted>true</IsMuted>" in p.corpo(it)
                sc = p.ref(it, "SubClip")
                nome_plano = (p.campo(sc, "Name") if sc else None) or "?"
                nest = self.nest_do_item(it)
                composto = ({"id": f"{n_trilha}:{ini}", "nome": p.nome_da_sequencia(nest)}
                            if nest is not None else None)
                for f in folhas:
                    a, b = q(f["t0"]), q(f["t1"])
                    if b <= a:
                        continue
                    destino = base[n_trilha] + f.get("camada", 0)
                    seg = self._segmento(f, a, b, destino, nome_plano, nominal, fps, quadro, av)
                    seg["disabled"] = desligada or item_desligado
                    if composto:
                        seg["composto"] = dict(composto)
                    segs.append(seg)
                    ocupado.setdefault(destino, []).append((a, b))
                for x in av:
                    avisos[x] = avisos.get(x, 0) + 1
            for destino in sorted(set(ocupado) | {base[n_trilha]}):
                segs.extend(_lacunas(ocupado.get(destino, []), destino, nominal, fps))

        if audio:
            segs.extend(self._audio(s, q, nominal, fps, avisos))
        ref = _referencia_na_timeline(segs, nominal)
        if ref is not None:
            avisos[f"vídeo de referência achado na V{ref['track']}: {ref['clip_name']}"] = 1
        segs.sort(key=lambda d: (d["track"], d["timeline_tc_in"], not d["is_transition"]))
        m = re.search(r"<MZ\.ZeroPoint>(-?\d+)</MZ\.ZeroPoint>", p.corpo(s))
        inicio = max(0, round(int(m.group(1)) / tpf)) if (m and tpf) else 0
        return {"nome": nome, "fps": fps, "largura": quadro[0], "altura": quadro[1],
                "inicio": inicio,
                "premiere": p.versao(), "segmentos": segs,
                "avisos": [{"texto": k, "vezes": v} for k, v in sorted(avisos.items())]}

    def _segmento(self, f: dict, a: int, b: int, trilha: int, nome_plano: str, nominal: int,
                  fps_tl: float, quadro: tuple[int, int], av: list) -> dict:
        p = self.p
        media = f["media"]
        caminho = p.campo(media, "FilePath") or ""
        titulo = p.campo(media, "Title") or caminho.rsplit("/", 1)[-1]
        tpf_m = f["tpf_midia"] or round(TPS / fps_tl)
        fps_m = TPS / tpf_m
        sintetico = caminho.isdigit()
        geo: Geo = f["geo"]
        fw, fh = f["fonte_px"]
        qw, qh = quadro
        encaixe = min(qw / fw, qh / fh) if fw and fh else 1.0
        zoom = geo.esc / encaixe if encaixe else geo.esc
        def resolve_de(g: Geo) -> tuple[float, float, float, float]:
            return (g.esc / encaixe if encaixe else g.esc,
                    g.tx / qw if qw else 0.0, -g.ty / qh if qh else 0.0,
                    SINAL_ROTACAO_RESOLVE * g.rot)
        params = {"scale_x": zoom, "scale_y": zoom,
                  "pos_x": geo.tx / qw * 1000.0 if qw else 0.0,
                  "pos_y": -geo.ty / qh * 1000.0 if qh else 0.0,
                  "rot_z": SINAL_ROTACAO_RESOLVE * geo.rot}
        if any(f.get("flip") or []):
            params["flip_x"], params["flip_y"] = bool(f["flip"][0]), bool(f["flip"][1])
        if f.get("luma_key"):
            params["composite"] = "screen"
            av.append("Luma Key traduzido como composição Screen: conferir")
        crop = f.get("crop") or {}
        if any(v > 0 for v in crop.values()):
            params["crop_frac"] = {k: min(1.0, v) for k, v in crop.items()}
            if f.get("crop_suave"):
                params["crop_suave"] = f["crop_suave"] * SOFTNESS_POR_PX
        tempos = sorted({x for x in f.get("kf", []) if f["t0"] <= x <= f["t1"]})
        if tempos:
            tpf_tl = TPS / fps_tl
            razao = (fps_tl / fps_m) / f["vel"] if f["vel"] else 1.0
            pontos = sorted({f["t0"], *tempos, max(f["t0"], f["t1"] - tpf_tl)})
            kf: dict[str, list] = {"zoom": [], "pos_x": [], "pos_y": [], "rot": []}
            vistos = set()
            for t in pontos:
                q_src = round(f["src_in"] / tpf_m * razao) + round((t - f["t0"]) / tpf_tl)
                if q_src in vistos:
                    continue
                vistos.add(q_src)
                z, x, y, r = resolve_de(f["geo_fn"](t))
                for nome, v in (("zoom", z), ("pos_x", x), ("pos_y", y), ("rot", r)):
                    kf[nome].append((q_src, v))
            kf = {k: v for k, v in kf.items() if len(v) > 1
                  and max(x for _q, x in v) - min(x for _q, x in v) > 1e-6}
            if kf:
                params["kf"] = kf
        efeitos = [{"name": "Motion", "category": "geometric", "reproducible": True,
                    "params": params}]
        for r in f.get("fusion") or []:
            efeitos.append({"name": {"recorte": "Crop com máscara", "desfoque": "Gaussian Blur",
                                     "basic3d": "Basic 3D"}[r["efeito"]] + " (Fusion)",
                            "category": "fusion", "reproducible": True, "params": r})
            av.append(f"{efeitos[-1]['name']}: composição do Fusion, conferir")
        for e in f["efeitos"]:
            efeitos.append({"name": e, "category": "other", "reproducible": False, "params": {}})
            av.append(f"efeito sem tradução: {e}")
        if sintetico and (f.get("texto") or {}).get("texto"):
            efeitos.append({"name": "Text", "category": "title", "reproducible": True,
                            "params": {"titulo": _titulo_resolve(f, quadro, av, TPS / fps_tl)}})
        vel = f["vel"]
        conform = fps_tl / fps_m if fps_m else 1.0
        speed_ratio = conform / vel if vel else conform
        criativo = abs(vel - 1.0) > 1e-6
        reverso = f.get("sentido", 1) < 0
        classe = ("creative" if abs(conform - 1) < 1e-4 else "conform+creative") if criativo \
            else ("native" if abs(conform - 1) < 1e-4 else "conform")
        dur_fonte = None
        vs = p.ref(media, "VideoStream")
        if vs is not None and p.campo(vs, "Duration"):
            dur_fonte = round(_int(p.campo(vs, "Duration")) / tpf_m)
        return {
            "timeline_tc_in": frames_para_tc(a, nominal),
            "timeline_tc_out": frames_para_tc(b, nominal),
            "duration_frames": b - a,
            "source_start_frames": 0 if f.get("parado") else round(f["src_in"] / tpf_m),
            "source_fps": fps_m,
            "clip_name": "Graphic" if sintetico else titulo,
            "track": trilha,
            "mob_id": None,
            "source_slot_id": None,
            "media_ref": None if sintetico else {
                "mob_name": titulo, "url": "", "local_path": caminho,
                "descriptor_type": "prproj", "source_duration_frames": dur_fonte,
                "source_duration_secs": (dur_fonte / fps_m) if dur_fonte else None,
                "source_fps": fps_m},
            "is_gap": False, "is_transition": False, "is_effect": sintetico, "is_audio": False,
            "cutpoint": 0,
            "has_motion_effect": criativo or reverso,
            "motion_effect_type": "reverse" if reverso else ("constant" if criativo else None),
            "source_offset_map": [],
            "velocidade_fonte": vel if reverso else None,
            "source_start_exato": f["src_in"] / tpf_m if reverso else None,
            "speed_ratio": speed_ratio, "phase_offset": 0, "motion_class": classe,
            "conform_ratio": conform, "relative_speed": speed_ratio / conform,
            "speed_curve": [], "speed_keyframes": [],
            "effects": efeitos,
            "has_reframe": True, "reframe_mode": "pillarbox",
            "retime": f.get("retime") if criativo else None,
            "has_graphic_overlay": False, "is_never_visible": False, "disabled": False,
            "visible_tc_in": None, "visible_tc_out": None,
            "is_group_clip": False, "group_clip_name": None, "selector_alternates": [],
            "prproj": {"plano": nome_plano, "proxy": caminho, "proxy_px": [fw, fh],
                       "escalar_ao_quadro": f["escalar_ao_quadro"], "velocidade": vel,
                       "alternate_start": p.campo(media, "AlternateStart"),
                       "sintetico": sintetico},
        }

    def _transicao(self, it: Obj, trilha: int, itens: list[Obj], q, nominal: int) -> dict:
        p = self.p
        ini, fim = _int(p.campo(it, "Start")), _int(p.campo(it, "End"))
        corte = None
        for x in itens:
            if x.tag not in ("VideoClipTrackItem", "AudioClipTrackItem"):
                continue
            s_x = _int(p.campo(x, "Start"))
            if ini < s_x <= fim:
                corte = s_x
                break
        if corte is None:
            corte = ini if p.campo(it, "HasOutgoingClip") == "false" else fim
        a, b = q(ini), q(fim)
        return {"timeline_tc_in": frames_para_tc(a, nominal),
                "timeline_tc_out": frames_para_tc(b, nominal),
                "duration_frames": b - a, "source_start_frames": 0, "source_fps": None,
                "clip_name": "(transition)", "track": trilha, "is_transition": True,
                "is_gap": False, "is_audio": False, "is_effect": False,
                "cutpoint": q(corte) - a, "effects": [],
                "transicao": p.campo(it, "MatchName")}


SOFTNESS_POR_PX = 0.105

CORPO_PREMIERE = 100.0
ENTRELINHA_PREMIERE = 1.2
ALTURA_DO_SIZE_RESOLVE = 1080.0


def _titulo_resolve(f: dict, quadro: tuple[int, int], av: list, tpf_tl: float = 0.0) -> dict:
    from titulos import fontes

    tx = f["texto"]
    qw, qh = quadro
    g = (_float(f.get("grupo_escala"), 100.0) or 100.0) / 100.0
    gx, gy = _ponto(f.get("grupo_posicao"), (0.5, 0.5))
    ax, ay = _ponto(f.get("grupo_ancora"), (0.5, 0.5))
    transform = None
    if abs(ax - 0.5) < 1e-6 and abs(ay - 0.5) < 1e-6 and (abs(g - 1) > 1e-6 or f.get("grupo_escala_kf")
                                                          or abs(gx - ax) > 1e-6 or abs(gy - ay) > 1e-6):
        transform = {"zoom": g, "pos": (gx - ax, ay - gy)}
        kfs = f.get("grupo_escala_kf") or []
        if len(kfs) > 1 and tpf_tl:
            transform["kf_zoom"] = [(max(0, round((t - f["t0"]) / tpf_tl)), v / 100.0) for t, v in kfs]
            if f.get("grupo_curva"):
                av.append("zoom do título com curva (Bézier): levado como linear")
        g, gx, gy = 1.0, ax, ay
    corpo_px = (tx.get("corpo_px") or CORPO_PREMIERE) * (_float(tx.get("Scale"), 100.0) or 100.0) / 100.0 * g
    px, py = _ponto(tx.get("Position"), (0.5, 0.5))
    px, py = gx + (px - ax) * g, gy + (py - ay) * g
    ps = tx.get("fonte") or ""
    fonte = fontes.traduzir(ps)
    topo = py - fonte["ascendente"] * corpo_px / qh if qh else py
    if ps and not fonte["instalada"]:
        av.append(f"fonte “{ps}” não está instalada nesta máquina: usada a família "
                  f"“{fonte['familia']}” ({fonte['face']}) pelo nome; conferir")
    elif ps and fonte.get("adobe"):
        av.append(f"fonte “{fonte['familia']} {fonte['face']}” é do Adobe Fonts: na máquina que abre o "
                  "Resolve, o Creative Cloud tem de estar logado com ela ATIVADA (o arquivo não pode ser "
                  "copiado); sem isso o Resolve troca a fonte")
    elif ps:
        av.append(f"fonte “{fonte['familia']} {fonte['face']}”: precisa estar instalada na máquina "
                  "que abre o Resolve")
    av.append("título do Premiere: cor (branco) suposta; conferir")
    entrelinha = None
    if "\n" in (tx.get("texto") or "").strip() and qh:
        entrelinha = round((ENTRELINHA_PREMIERE - fonte["linha"]) * corpo_px
                           * ALTURA_DO_SIZE_RESOLVE / qh, 3) or None
    if f.get("grupo_animado") or tx.get("animado") or (f.get("grupo_escala_kf") and not transform):
        av.append("título animado (escala/posição): usado o valor do início")
    return {"estilo_lido": True, "fundo": None, "transform": transform, "caixas": [{
        "texto": tx["texto"], "fonte": {"familia": fonte["familia"] or "Open Sans",
                                       "estilo": fonte["face"] if ps else ""},
        "corpo": corpo_px / qh if qh else 0.08,
        "corpo_resolve": corpo_px * ALTURA_DO_SIZE_RESOLVE / qh if qh else None,
        "cor": [1, 1, 1], "alinhamento": "esquerda", "entrelinha": entrelinha,
        "ancora": "topo_esquerda", "posicao_resolve": (px, 1.0 - topo)}]}


def _volume_kf(p: Projeto, comp: Obj) -> list[tuple[int, float]]:
    for prm in p.refs(comp, "Param"):
        if p.campo(prm, "Name") != "Level":
            continue
        out = [(t, _db_do_level(v)) for t, v in _kfs_do_level(p, prm)]
        return out if len(out) > 1 else []
    return []


def _kfs_do_level(p: Projeto, prm: Obj) -> list[tuple[int, float]]:
    out = []
    for k in (p.campo(prm, "Keyframes") or "").split(";"):
        partes = k.split(",")
        if len(partes) >= 2 and _float(partes[1], None) is not None and partes[0].strip().isdigit():
            out.append((int(partes[0]), _float(partes[1], 0.0)))
    return sorted(out)


def _db_do_level(v: float) -> float:
    import math
    return round(max(-100.0, 20 * math.log10(v) + 15.0), 3) if v > 0 else -100.0


def _volume_no_trecho(volume_db, volume_kf: list, f: dict, a: int, b: int, tpf_tl: float,
                      avisos: dict) -> dict:
    if not volume_kf:
        return {"volume_db": volume_db, "volume_kf": []}
    if "pos" in f:
        chave = "volume com keyframes em take aninhado: não traduzido (fica 0 dB)"
        avisos[chave] = avisos.get(chave, 0) + 1
        return {"volume_db": None, "volume_kf": []}
    vel = f.get("vel", 1.0) or 1.0
    pts = [(t / tpf_tl / vel, 10 ** ((db - 15.0) / 20)) for t, db in volume_kf]

    def amp(q):
        if q <= pts[0][0]:
            return pts[0][1]
        for (q0, v0), (q1, v1) in zip(pts, pts[1:]):
            if q <= q1:
                return v0 + (v1 - v0) * (q - q0) / (q1 - q0) if q1 > q0 else v1
        return pts[-1][1]

    db = _db_do_level
    i0 = f["src_in"] / tpf_tl / vel
    i1 = i0 + (b - a) - 1
    saida = [(round(i0), db(amp(i0)))] + [(round(q), db(v)) for q, v in pts if i0 < q < i1] \
        + [(round(i1), db(amp(i1)))]
    vals = [d for _q, d in saida]
    if max(vals) - min(vals) < 0.01:
        return {"volume_db": vals[0] if abs(vals[0]) > 0.01 else None, "volume_kf": []}
    unicos = []
    for q, d in saida:
        if not unicos or q > unicos[-1][0]:
            unicos.append((q, d))
    return {"volume_db": None, "volume_kf": unicos}


def _volume_db(p: Projeto, comp: Obj, avisos: dict) -> float | None:
    for prm in p.refs(comp, "Param"):
        if p.campo(prm, "Name") != "Level":
            continue
        kfs = _kfs_do_level(p, prm)
        if len(kfs) > 1:
            return None
        if not kfs and (p.campo(prm, "Keyframes") or "").strip(";").strip():
            chave = "volume do clipe com keyframes ilegíveis: usado o valor fixo"
            avisos[chave] = avisos.get(chave, 0) + 1
        x = kfs[0][1] if kfs else _float(_valor(p, prm, None)[0], None)
        if x is None or x <= 0:
            return None
        db = _db_do_level(x)
        return db if abs(db) > 0.01 else None
    return None


def _quadro(tc: str, nominal: int) -> int:
    h, m, s_, f = (int(x) for x in tc.split(":"))
    return ((h * 60 + m) * 60 + s_) * nominal + f


def _referencia_na_timeline(segs: list[dict], nominal: int, tolerancia: int = 2) -> dict | None:
    video = [s for s in segs if not s.get("is_audio") and not s.get("is_gap")
             and not s.get("is_transition")]
    if not video:
        return None
    fim = max(_quadro(s["timeline_tc_in"], nominal) + s["duration_frames"] for s in segs
              if not s.get("is_gap") and not s.get("is_transition"))
    topo = max(s["track"] for s in video)
    nela = [s for s in video if s["track"] == topo]
    if len(nela) != 1:
        return None
    s = nela[0]
    caminho = (s.get("media_ref") or {}).get("local_path") or ""
    ini = _quadro(s["timeline_tc_in"], nominal)
    if (s.get("is_effect") or not _e_video(caminho) or ini > tolerancia
            or ini + s["duration_frames"] < fim - tolerancia):
        return None
    s["referencia"], s["trilha_desligada"] = True, True
    for a in segs:
        if a.get("is_audio") and (a.get("media_ref") or {}).get("local_path") == caminho:
            a["referencia"] = True
    return s


def _lacunas(ocupado: list, trilha: int, nominal: int, fps: float) -> list[dict]:
    out, pos = [], 0
    for a, b in sorted(ocupado):
        if a > pos:
            out.append({"timeline_tc_in": frames_para_tc(pos, nominal),
                        "timeline_tc_out": frames_para_tc(a, nominal), "duration_frames": a - pos,
                        "source_start_frames": 0, "source_fps": fps, "clip_name": "(gap)",
                        "track": trilha, "is_gap": True, "is_transition": False,
                        "is_audio": False, "is_effect": False, "effects": []})
        pos = max(pos, b)
    return out


def _e_video(caminho: str | None) -> bool:
    return bool(caminho) and not re.search(r"\.(png|jpe?g|tiff?|psd|bmp|gif|exr|dpx)$",
                                           caminho, re.I)


def ler_sequencia(caminho_prproj: str, nome: str) -> dict:
    return Leitor(Projeto(caminho_prproj)).ler(nome)
