from __future__ import annotations

import logging
import os
import re
from pathlib import Path, PurePath

from aaf.masters import chave_mob
from aaf.parser import (MediaRef, Segment, _categorize_effect, _classify_motion,
                        _frames_to_tc_df, frames_to_tc)

from titulos.avid import e_titulo as _e_titulo
from titulos.avid import ler as _titulo

from .efeitos import enquadramento, informativo, nome_do_plugin
from .percurso import e_composicao, e_master, e_subclipe, tipo, track_de

log = logging.getLogger("delapraca.avb_bin.segmentos")

PROFUNDIDADE_MAX = 16

_EMBRULHOS = ("TrackEffect", "PanVolumeEffect", "AudioSuitePluginEffect", "EqualizerMultiBand",
              "MotionEffect", "CaptureMask", "StrobeEffect", "Repeat", "TimeWarp",
              "EssenceGroup")


def deref(x):
    return getattr(x, "value", x) if type(x).__name__ == "AVBObjectRef" else x


def _lista(valor) -> list:
    if valor is None or callable(valor):
        return []
    try:
        return list(valor)
    except TypeError:
        return []


def _tracks(c) -> list:
    return [deref(getattr(t, "component", t)) for t in _lista(getattr(c, "tracks", None))]


def _racional(v) -> float | None:
    if isinstance(v, (list, tuple)) and len(v) == 2 and v[1]:
        return float(v[0]) / float(v[1])
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pontos(param) -> list[tuple[float, float]]:
    param = deref(param)
    try:
        ct = deref(param.property_data.get("control_track"))
        pts = _lista(ct.property_data.get("control_points"))
    except AttributeError:
        return []
    fora = []
    for p in pts:
        p = deref(p)
        try:
            t = _racional(p.property_data.get("offset"))
            v = float(p.property_data.get("value"))
        except (AttributeError, TypeError, ValueError):
            continue
        if t is not None:
            fora.append((round(t, 3), round(v, 3)))
    return fora


_LETRA_DO_AVID = re.compile(r"^([A-Za-z])//")
_LETRA = re.compile(r"^[A-Za-z]:[\\/]")


def caminho_do_locator(mob) -> str:
    try:
        desc = getattr(mob, "descriptor", None)
    except ValueError as e:
        log.debug("descritor ilegível em %r: %s", getattr(mob, "name", "?"), e)
        return ""
    if desc is None:
        return ""
    for cand in [desc] + _lista(getattr(desc, "descriptors", None)):
        locs = [getattr(cand, "locator", None)] + _lista(getattr(cand, "locators", None))
        for loc in locs:
            if loc is None:
                continue
            dados = getattr(loc, "property_data", None) or {}
            for campo in ("path_utf8", "path_posix", "path"):
                valor = dados.get(campo) if hasattr(dados, "get") else None
                valor = valor or getattr(loc, campo, None)
                if valor:
                    return _LETRA_DO_AVID.sub(r"\1:/", str(valor).strip())
    return ""


def caminho_confiavel(caminho: str) -> tuple[str | None, str]:
    c = (caminho or "").strip()
    if not c:
        return None, "sem caminho na bin"
    barras = c.replace("\\", "/")
    if barras.startswith("//"):
        return None, "caminho de rede (UNC) — não verificado, por segurança"
    if any(p == ".." for p in barras.split("/")):
        c = _resolver_pontos(c)
        if c is None:
            return None, "caminho com '..' relativo ou acima da raiz — recusado"
    if os.name != "nt" and _LETRA.match(c):
        return _no_disco_desta_maquina(c)
    if os.name == "nt" and _VOLUME_MAC.match(c):
        return _no_volume_desta_maquina(c) or c, ""
    return c, ""


def _resolver_pontos(c: str) -> str | None:
    if _LETRA.match(c):
        raiz, resto, sep = c[:3], c[3:], "\\" if "\\" in c else "/"
    elif c.startswith("/"):
        raiz, resto, sep = "/", c[1:], "/"
    else:
        return None
    pilha: list[str] = []
    for parte in re.split(r"[\\/]+", resto):
        if parte in ("", "."):
            continue
        if parte == "..":
            if not pilha:
                return None
            pilha.pop()
        else:
            pilha.append(parte)
    return raiz + sep.join(pilha)


_VOLUME_MAC = re.compile(r"^/Volumes/[^/]+/.")
_VOLUMES_CACHE: list = [0.0, {}, {}]


def _volumes_windows() -> dict[str, str]:
    import ctypes
    import time

    if time.monotonic() - _VOLUMES_CACHE[0] < 10:
        return _VOLUMES_CACHE[1]
    k32 = ctypes.windll.kernel32
    anterior = k32.SetErrorMode(0x0001)
    try:
        montadas, buf, fora = k32.GetLogicalDrives(), ctypes.create_unicode_buffer(261), {}
        for i in range(26):
            if montadas & (1 << i):
                letra = chr(ord("A") + i)
                raiz = ctypes.c_wchar_p(f"{letra}:\\")
                if k32.GetDriveTypeW(raiz) not in (2, 3):
                    continue
                if k32.GetVolumeInformationW(raiz, buf, 261, None, None, None, None, 0):
                    fora[letra] = buf.value
    finally:
        k32.SetErrorMode(anterior)
    _VOLUMES_CACHE[:] = [time.monotonic(), fora, {}]
    return fora


def _no_volume_desta_maquina(caminho: str, volumes: dict[str, str] | None = None) -> str | None:
    _, _vol, nome, *resto = caminho.split("/")
    if not resto:
        return None
    vols = _volumes_windows() if volumes is None else volumes
    sub = "\\".join(resto)
    memo = _VOLUMES_CACHE[2] if volumes is None else {}
    if (nome, sub) in memo:
        return memo[(nome, sub)]

    def achados(letras):
        return [f"{l}:\\{sub}" for l in letras if os.path.isfile(f"{l}:\\{sub}")]

    rotulo = [l for l, r in vols.items() if r and r.casefold() == nome.casefold()]
    pelo_rotulo = achados(rotulo)
    if len(pelo_rotulo) == 1:
        achado = pelo_rotulo[0]
    else:
        todos = pelo_rotulo + achados(l for l in vols if l not in rotulo)
        achado = todos[0] if len(todos) == 1 else None
    memo[(nome, sub)] = achado
    return achado


def _no_disco_desta_maquina(caminho: str, volumes: Path = Path("/Volumes")) -> tuple[str | None, str]:
    resto = re.sub(r"^[A-Za-z]:[\\/]", "", caminho).replace("\\", "/")
    try:
        candidatos = [v / resto for v in volumes.iterdir() if (v / resto).is_file()]
    except OSError:
        return None, "não consegui listar /Volumes"
    if len(candidatos) == 1:
        return str(candidatos[0]), ""
    return None, ("nenhum volume tem esse caminho" if not candidatos
                  else "mais de um volume tem esse caminho — ambíguo")


def arquivo_na_bin(master, por_id: dict) -> tuple[str, str]:
    vistos: set[str] = set()
    fila = [master]
    while fila:
        mob = fila.pop(0)
        chave = str(getattr(mob, "mob_id", ""))
        if chave in vistos or len(vistos) > 24:
            continue
        vistos.add(chave)
        caminho = caminho_do_locator(mob)
        if caminho:
            try:
                desc = type(getattr(mob, "descriptor", None)).__name__
            except ValueError:
                desc = "?"
            return caminho, desc
        for comp in _tracks(mob):
            for sc in _source_clips(comp):
                prox = por_id.get(str(sc.mob_id))
                if prox is not None:
                    fila.append(prox)
    return "", ""


def essencias_do_master(master) -> dict[str, str]:
    fora: dict[str, str] = {}
    for tr in _lista(getattr(master, "tracks", None)):
        mk = str(getattr(tr, "media_kind", "") or "")
        if mk not in ("picture", "sound"):
            continue
        scs = _source_clips(getattr(tr, "component", None))
        if scs:
            fora[f"{mk}:{int(getattr(tr, 'index', 1) or 1)}"] = str(scs[0].mob_id)
    return fora


def _timecode_do_mob(mob) -> tuple[int, int, int] | None:
    for tr in _lista(getattr(mob, "tracks", None)):
        comp = deref(getattr(tr, "component", None))
        pos = 0
        for c in ([comp] if tipo(comp) == "Timecode" else _lista(getattr(comp, "components", None))):
            c = deref(c)
            if tipo(c) == "Timecode":
                return int(getattr(c, "start", 0) or 0), int(getattr(c, "fps", 0) or 0), pos
            pos += int(getattr(c, "length", 0) or 0)
    return None


def identidade_na_bin(master, por_id: dict) -> dict:
    fora: dict = {"reel": "", "tc_inicio": None, "tc_fps": None,
                  "largura": None, "altura": None, "fps": None,
                  "audio_sr": None,
                  "audio_canais": sum(1 for t in _lista(getattr(master, "tracks", None))
                                      if str(getattr(t, "media_kind", "")) == "sound")}
    for t in _lista(getattr(master, "tracks", None)):
        if str(getattr(t, "media_kind", "")) != "sound":
            continue
        for sc in _source_clips(getattr(t, "component", None))[:1]:
            essencia = por_id.get(str(sc.mob_id))
            try:
                sr = getattr(getattr(essencia, "descriptor", None), "sample_rate", None)
            except ValueError:
                sr = None
            if sr:
                fora["audio_sr"] = int(float(sr))
        break
    trilhas = sorted(_lista(getattr(master, "tracks", None)),
                     key=lambda t: 0 if str(getattr(t, "media_kind", "")) == "picture" else 1)
    for tr in trilhas:
        if str(getattr(tr, "media_kind", "")) not in ("picture", "sound"):
            continue
        cadeia: list = [(master, 0)]
        offset, vistos = 0, set()
        scs = _source_clips(getattr(tr, "component", None))
        while scs:
            sc = scs[0]
            prox = por_id.get(str(sc.mob_id))
            if prox is None or str(prox.mob_id) in vistos or len(vistos) > 8:
                break
            vistos.add(str(prox.mob_id))
            offset += int(getattr(sc, "start_time", 0) or 0)
            cadeia.append((prox, offset))
            try:
                desc = getattr(prox, "descriptor", None)
            except ValueError:
                desc = None
            if desc is not None and fora["largura"] is None and getattr(desc, "stored_width", None):
                fora["largura"] = int(desc.stored_width)
                altura = int(getattr(desc, "stored_height", 0) or 0) or None
                if altura and int(getattr(desc, "frame_layout", 0) or 0) != 0:
                    altura *= 2
                fora["altura"] = altura
                er = getattr(desc, "edit_rate", None)
                fora["fps"] = float(er) if er else None
            proximas = [sc2 for t in _tracks(prox)
                        for sc2 in _source_clips(t)]
            scs = proximas[:1]

        for mob, desloc in reversed(cadeia):
            tc = _timecode_do_mob(mob)
            if tc is not None and tc[1] > 0:
                inicio, tfps, pos = tc
                fora["tc_inicio"], fora["tc_fps"] = inicio + desloc - pos, tfps
                break
        for mob, _desloc in reversed(cadeia):
            nome = (getattr(mob, "name", "") or "").strip()
            if nome and not _NOME_INTERNO.match(nome):
                fora["reel"] = nome
                break
        if fora["reel"] or fora["tc_inicio"] is not None:
            return fora
    return fora


_NOME_INTERNO = re.compile(r"^msmMMOB\.\d+$", re.I)


def _source_clips(c, prof: int = 0) -> list:
    c = deref(c)
    if c is None or prof > PROFUNDIDADE_MAX:
        return []
    if tipo(c) == "SourceClip":
        return [c]
    fora = []
    for filho in _lista(getattr(c, "components", None)) + _tracks(c):
        fora.extend(_source_clips(filho, prof + 1))
    return fora


def _taxa_nativa(master, media_kind: str | None) -> float | None:
    alvo = str(media_kind or "").lower()
    melhor = None
    for tr in _lista(getattr(master, "tracks", None)):
        comp = deref(getattr(tr, "component", None))
        er = getattr(comp, "edit_rate", None)
        if not er:
            continue
        if str(getattr(comp, "media_kind", "") or "").lower() == alvo:
            return float(er)
        melhor = melhor or float(er)
    return melhor


def _comprimento(mob) -> int | None:
    for tr in _lista(getattr(mob, "tracks", None)):
        n = getattr(deref(getattr(tr, "component", None)), "length", None)
        if n:
            return int(n)
    return None


class _Leitor:
    def __init__(self, f, fps: float):
        self.por_id = {str(m.mob_id): m for m in f.content.mobs}
        self.fps = fps
        self.avisos: dict[str, int] = {}
        self.masters: dict[str, dict] = {}

    def avisar(self, texto: str) -> None:
        self.avisos[texto] = self.avisos.get(texto, 0) + 1

    def resolver(self, sc) -> tuple:
        inicio = int(getattr(sc, "start_time", 0) or 0)
        ref = self.por_id.get(str(sc.mob_id))
        if ref is None:
            self.avisar("clipe aponta para um mob que não está na bin")
            return None, inicio, "(mob ausente)", getattr(sc, "track_id", None)
        nome = getattr(ref, "name", None) or ""
        if e_master(ref):
            return ref, inicio, nome, getattr(sc, "track_id", None)
        if e_subclipe(ref):
            tr = track_de(ref, getattr(sc, "track_id", None), getattr(sc, "media_kind", None))
            seq = deref(getattr(tr, "component", None)) if tr is not None else None
            pos = 0
            for comp in _lista(getattr(seq, "components", None)) or ([seq] if seq is not None else []):
                comp = deref(comp)
                n = int(getattr(comp, "length", 0) or 0)
                if tipo(comp) != "Filler" and pos <= inicio < pos + max(n, 1):
                    dentro = comp if tipo(comp) == "SourceClip" else next(iter(_source_clips(comp)), None)
                    destino = self.por_id.get(str(dentro.mob_id)) if dentro is not None else None
                    if destino is not None and e_master(destino):
                        fonte = int(getattr(dentro, "start_time", 0) or 0) + (inicio - pos)
                        return destino, fonte, nome, getattr(dentro, "track_id", None)
                pos += n
            self.avisar("subclipe sem clipe de master na posição pedida")
            return None, inicio, nome, getattr(sc, "track_id", None)
        if e_composicao(ref):
            self.avisar("multicam/grupo (fora do escopo desta versão) — clipe exportado sem mídia")
            return None, inicio, nome, getattr(sc, "track_id", None)
        return None, inicio, nome, getattr(sc, "track_id", None)

    def media_ref(self, master, media_kind) -> MediaRef:
        chave = str(master.mob_id)
        info = self.masters.get(chave)
        if info is None:
            bruto, desc = arquivo_na_bin(master, self.por_id)
            info = {
                "nome": getattr(master, "name", "") or "",
                "mob_id": chave,
                "caminho_na_bin": bruto,
                "descritor": desc,
                "fps": _taxa_nativa(master, media_kind),
                "duracao_frames": _comprimento(master),
                "identidade": identidade_na_bin(master, self.por_id),
                "essencias": essencias_do_master(master),
            }
            self.masters[chave] = info
        return MediaRef(
            mob_name=info["nome"], url="", local_path="",
            descriptor_type=info["descritor"] or "",
            source_duration_frames=info["duracao_frames"],
            source_fps=_taxa_nativa(master, media_kind) or info["fps"],
        )

    def desembrulhar(self, c, efeitos: list, movimento: dict, prof: int = 0,
                     extra: dict | None = None):
        c = deref(c)
        if c is None or prof > PROFUNDIDADE_MAX:
            return None, 0
        k = tipo(c)
        if k == "SourceClip":
            return c, 0
        if k == "Sequence":
            pos = 0
            for comp in _lista(getattr(c, "components", None)):
                comp = deref(comp)
                achado, sub = self.desembrulhar(comp, efeitos, movimento, prof + 1, extra)
                if achado is not None:
                    return achado, pos + sub
                pos += int(getattr(comp, "length", 0) or 0)
            return None, 0
        if k == "Selector":
            return self.desembrulhar(self._ramo(c), efeitos, movimento, prof + 1, extra)
        if k in _EMBRULHOS or getattr(c, "tracks", None) is not None:
            if k == "MotionEffect" and not movimento:
                movimento.update(self._velocidade(c))
            elif k == "PanVolumeEffect":
                pd = c.property_data
                efeitos.append({"name": "Audio Pan Volume", "category": "gain",
                                "reproducible": False,
                                "params": {"level": pd.get("level"), "pan": pd.get("pan")}})
            elif k != "MotionEffect" and getattr(c, "effect_id", None) and _e_titulo(c):
                efeitos.append({"name": str(c.effect_id), "category": "title",
                                "reproducible": True, "params": {"titulo": _titulo(c)}})
            elif k != "MotionEffect" and getattr(c, "effect_id", None):
                eid = str(c.effect_id)
                efeito, modo, avisos = enquadramento(c)
                if modo and extra is not None and extra.get("reframe_mode") in (None, "pillarbox"):
                    extra["reframe_mode"] = modo
                for a in avisos:
                    self.avisar(a)
                if efeito is not None:
                    efeitos.append(efeito)
                elif not modo:
                    categoria, _ = _categorize_effect(eid)
                    efeitos.append({"name": eid, "category": categoria,
                                    "reproducible": False, "params": informativo(c),
                                    "label": nome_do_plugin(c)})
            for filho in _tracks(c):
                achado, sub = self.desembrulhar(filho, efeitos, movimento, prof + 1, extra)
                if achado is not None:
                    return achado, sub
        return None, 0

    def _ramo(self, sel):
        ramos = _tracks(sel)
        i = int(sel.property_data.get("selected", 0) or 0)
        return ramos[i] if 0 <= i < len(ramos) else None

    def _velocidade(self, me) -> dict:
        pd = me.property_data
        sr = _racional(pd.get("speed_ratio")) or 1.0
        pl = _lista(pd.get("param_list"))
        curva = _pontos(pl[0]) if pl else []
        offmap = _pontos(pl[1]) if len(pl) > 1 else []
        dentro = _source_clips(_tracks(me)[0]) if _tracks(me) else []
        fonte_len = int(getattr(dentro[0], "length", 0) or 0) if dentro else 0
        comprimento = int(getattr(me, "length", 0) or 0)

        if sr < 0:
            tipo_me = "reverse"
        elif fonte_len <= 1 or (offmap and len({v for _, v in offmap}) == 1 and comprimento > 1):
            tipo_me = "freeze"
        elif len({v for _, v in curva}) > 1:
            tipo_me = "ramp"
        else:
            tipo_me = "constant"
        if not offmap and comprimento:
            offmap = [(0.0, 0.0), (float(comprimento), round(comprimento / abs(sr), 3))]
        if tipo_me in ("ramp", "freeze", "reverse"):
            self.avisar("velocidade %s: exportada, mas ainda não conferida no Resolve" % tipo_me)
        return {"speed_ratio": abs(sr), "motion_effect_type": tipo_me,
                "source_offset_map": offmap, "speed_curve": curva,
                "phase_offset": int(pd.get("phase_offset", 0) or 0)}

    def sequencia(self, seq, trilha: int, e_audio: bool, fora: list) -> int:
        pos = 0
        for comp in _lista(getattr(seq, "components", None)):
            comp = deref(comp)
            k = tipo(comp)
            n = int(getattr(comp, "length", 0) or 0)

            if k == "Filler":
                if n > 0:
                    fora.append(self._vazio(pos, n, trilha, e_audio, "(gap)"))
                pos += n
                continue

            if k == "TransitionEffect":
                t_in = max(0, pos - n)
                s = Segment(timeline_tc_in=frames_to_tc(t_in, self.fps),
                            timeline_tc_out=frames_to_tc(pos, self.fps),
                            duration_frames=n, source_start_frames=0, source_fps=self.fps,
                            clip_name="(transition)", track=trilha, is_transition=True,
                            cutpoint=int(getattr(comp, "cutpoint", 0) or 0), is_audio=e_audio)
                s.effects = [{"name": str(getattr(comp, "effect_id", "") or ""),
                              "category": "transition", "reproducible": False, "params": {}}]
                fora.append(s)
                pos -= n
                continue

            if k in ("TrackRef", "Timecode", "Edgecode") or n == 0 and k != "SourceClip":
                pos += n
                continue

            desabilitado = False
            if k == "Selector":
                ramo = self._ramo(comp)
                if ramo is None or tipo(ramo) == "Filler":
                    outro = next((r for r in _tracks(comp) if r is not None and tipo(r) != "Filler"),
                                 None)
                    if outro is None:
                        s = self._vazio(pos, n, trilha, e_audio, "(desabilitado)")
                        s.disabled = True
                        fora.append(s)
                        pos += n
                        continue
                    comp_efetivo, desabilitado = outro, True
                else:
                    comp_efetivo = ramo
            else:
                comp_efetivo = comp

            if k == "Sequence":
                sub: list = []
                self.sequencia(comp, trilha, e_audio, sub)
                for s in sub:
                    s.timeline_tc_in = frames_to_tc(_tc(s.timeline_tc_in, self.fps) + pos, self.fps)
                    s.timeline_tc_out = frames_to_tc(_tc(s.timeline_tc_out, self.fps) + pos, self.fps)
                fora.extend(sub)
                pos += n
                continue

            efeitos: list = []
            movimento: dict = {}
            extra: dict = {}
            sc, dentro = self.desembrulhar(comp_efetivo, efeitos, movimento, extra=extra)
            if sc is None:
                s = self._vazio(pos, n, trilha, e_audio,
                                str(getattr(comp_efetivo, "effect_id", "") or k))
                s.is_gap = False
                s.is_effect = True
                s.effects = efeitos
                fora.append(s)
                pos += n
                continue

            master, fonte, nome, canal = self.resolver(sc)
            if movimento:
                inicio, duracao = pos, n
            else:
                inicio = pos + dentro
                duracao = max(0, min(int(getattr(sc, "length", 0) or 0), n - dentro))
            s = Segment(timeline_tc_in=frames_to_tc(inicio, self.fps),
                        timeline_tc_out=frames_to_tc(inicio + duracao, self.fps),
                        duration_frames=duracao, source_start_frames=fonte,
                        source_fps=self.fps, clip_name=nome or "(sem nome)", track=trilha,
                        mob_id=str(master.mob_id) if master is not None else None,
                        source_slot_id=canal, is_audio=e_audio)
            s.effects = efeitos
            s.disabled = desabilitado
            if extra.get("reframe_mode"):
                s.has_reframe = True
                s.reframe_mode = extra["reframe_mode"]
            if master is not None:
                s.media_ref = self.media_ref(master, "sound" if e_audio else "picture")
            if movimento:
                s.has_motion_effect = True
                s.speed_ratio = movimento["speed_ratio"]
                s.motion_effect_type = movimento["motion_effect_type"]
                s.source_offset_map = movimento["source_offset_map"]
                s.speed_curve = movimento["speed_curve"]
                s.phase_offset = movimento["phase_offset"]
            s.motion_class, s.conform_ratio, s.relative_speed = _classify_motion(
                s.speed_ratio, s.motion_effect_type if movimento else None,
                s.media_ref.source_fps if s.media_ref else None, self.fps)
            fora.append(s)
            pos += n
        return pos

    def _vazio(self, pos: int, n: int, trilha: int, e_audio: bool, nome: str) -> Segment:
        return Segment(timeline_tc_in=frames_to_tc(pos, self.fps),
                       timeline_tc_out=frames_to_tc(pos + n, self.fps),
                       duration_frames=n, source_start_frames=0, source_fps=self.fps,
                       clip_name=nome, track=trilha, is_gap=True, is_audio=e_audio)


def _tc(tc: str, fps: float) -> int:
    h, m, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
    return ((h * 60 + m) * 60 + s) * round(fps) + f


def _inicio(seq, fps: float) -> tuple[int, bool]:
    nominal = round(fps) or 25
    alternativa = None
    for tr in sorted(_lista(getattr(seq, "tracks", None)), key=lambda t: getattr(t, "index", 0) or 0):
        comp = deref(getattr(tr, "component", None))
        if tipo(comp) != "Timecode":
            continue
        tfps = int(getattr(comp, "fps", 0) or 0)
        inicio = int(getattr(comp, "start", 0) or 0)
        if tfps == nominal:
            return inicio, False
        if tfps > 0 and alternativa is None:
            alternativa = (round(inicio * nominal / tfps), False)
    return alternativa or (0, False)


def sequencias(f) -> list:
    return [m for m in f.content.mobs
            if e_composicao(m) and int(getattr(m, "usage_code", 0) or 0) == 0]


def escolher_sequencia(f, mob_id: str | None = None, nome: str | None = None):
    candidatas = sequencias(f)
    if mob_id:
        alvo = chave_mob(mob_id)
        for m in candidatas:
            if chave_mob(str(m.mob_id)) == alvo:
                return m
    if nome:
        for m in candidatas:
            if (getattr(m, "name", "") or "") == nome:
                return m
    if mob_id or nome:
        return None
    return candidatas[0] if len(candidatas) == 1 else None


def ler_timeline(f, alvo) -> dict:
    fps = float(getattr(alvo, "edit_rate", 0) or 0) or 25.0
    leitor = _Leitor(f, fps)
    segmentos: list[Segment] = []
    comprimento = int(getattr(alvo, "length", 0) or 0)
    nao_fecharam = []

    trilhas = sorted(_lista(getattr(alvo, "tracks", None)),
                     key=lambda t: (str(getattr(t, "media_kind", "")), getattr(t, "index", 0) or 0))
    for tr in trilhas:
        kind = str(getattr(tr, "media_kind", "") or "").lower()
        if kind not in ("picture", "sound"):
            continue
        idx = int(getattr(tr, "index", 1) or 1)
        numero = idx if kind == "picture" else 1000 + idx - 1
        seq = deref(getattr(tr, "component", None))
        fim = leitor.sequencia(seq, numero, kind == "sound", segmentos)
        if comprimento and fim != comprimento:
            nao_fecharam.append({"trilha": ("V%d" if kind == "picture" else "A%d") % idx,
                                 "fim": fim, "esperado": comprimento})

    inicio, drop = _inicio(alvo, fps)
    for s in nao_fecharam:
        leitor.avisar("trilha %s não fechou no comprimento da sequência (%d de %d)"
                      % (s["trilha"], s["fim"], s["esperado"]))

    log.info("timeline %r: %d segmentos, %d masters, %d avisos",
             getattr(alvo, "name", ""), len(segmentos), len(leitor.masters), len(leitor.avisos))
    dicts = []
    for s in segmentos:
        d = s.to_dict()
        if s.is_audio and s.mob_id and s.source_slot_id:
            d["canal_arquivo"] = int(s.source_slot_id)
        dicts.append(d)
    return {
        "segments": dicts,
        "timeline_fps": fps,
        "total_segments": len(segmentos),
        "has_nested_scope": False,
        "composition_name": getattr(alvo, "name", "") or "",
        "sequence_mob_id": str(alvo.mob_id),
        "timeline_start_frames": inicio,
        "timeline_drop_frame": drop,
        "timeline_start_tc": _frames_to_tc_df(inicio, fps, drop),
        "markers": [],
        "masters": leitor.masters,
        "avisos": [{"texto": t, "vezes": n} for t, n in leitor.avisos.items()],
        "conferencia": nao_fecharam,
        "fonte": "avb",
    }


def ler_timeline_de_arquivo(caminho_avb: str, mob_id: str | None = None,
                            nome: str | None = None) -> dict:
    from avb_export.reader import topen

    with topen(caminho_avb) as f:
        alvo = escolher_sequencia(f, mob_id, nome)
        if alvo is None:
            raise LookupError("a sequência não está nesta bin (salve a bin no Media Composer "
                              "e tente de novo)")
        return ler_timeline(f, alvo)


def referencia_offline(info: dict, pasta: str, source_path: str = "",
                       source_file: str = "") -> tuple[str, dict]:
    from nomes.export import nome_de_arquivo

    ident = info.get("identidade") or {}
    arquivo = PurePath((source_file or "").replace("\\", "/")).name
    if not arquivo:
        arquivo = PurePath((info.get("caminho_na_bin") or "").replace("\\", "/")).name
        if "." not in arquivo:
            arquivo = ""
    nome = nome_de_arquivo(arquivo or ident.get("reel") or info.get("nome") or "") or "offline"
    caminho = os.path.join((source_path or "").strip() or pasta, nome)

    fps = float(ident.get("fps") or info.get("fps") or 0) or None
    tc_fps = int(ident.get("tc_fps") or (round(fps) if fps else 0))
    inicio = ident.get("tc_inicio")
    return caminho, {
        "nb": info.get("duracao_frames"),
        "fps": fps,
        "tc": frames_to_tc(int(inicio), tc_fps) if inicio is not None and tc_fps else None,
        "tc_segundos": (int(inicio) / fps) if inicio is not None and fps else 0.0,
        "audio_sr": ident.get("audio_sr"),
        "audio_canais": ident.get("audio_canais"),
        "reel": ident.get("reel") or "",
    }


def montar_midia(resultado: dict, arquivos: dict[str, str], pasta: str,
                 colunas_por_mob: dict[str, dict] | None = None,
                 gerenciados: dict[str, str] | None = None,
                 preferir: dict[str, str] | None = None) -> tuple[list, list, dict]:
    import copy

    gerenciados = gerenciados or {}
    preferir = preferir or {}
    masters = resultado.get("masters") or {}
    segmentos = copy.deepcopy(resultado.get("segments") or [])

    por_segmento: dict[int, str] = {}
    na_gerenciada: set[int] = set()
    precisa_ref: set[str] = set()
    for i, s in enumerate(segmentos):
        chave = s.get("mob_id")
        if not chave or s.get("is_gap") or s.get("is_transition"):
            continue
        tipo_m = "sound" if s.get("is_audio") else "picture"
        canal = int(s.get("canal_arquivo") or s.get("source_slot_id") or 1)
        essencia = ((masters.get(chave) or {}).get("essencias") or {}).get(f"{tipo_m}:{canal}")
        mxf = gerenciados.get(essencia) if essencia else None
        if chave in preferir:
            por_segmento[i] = preferir[chave]
        elif mxf:
            por_segmento[i] = mxf
            na_gerenciada.add(i)
            if s.get("is_audio"):
                s["canal_arquivo"] = 1
        elif chave in arquivos:
            por_segmento[i] = arquivos[chave]
        else:
            precisa_ref.add(chave)

    sem_master = {k for k in precisa_ref if k not in masters}
    masters_ref = {k: m for k, m in masters.items() if k in precisa_ref}
    for s in segmentos:
        k = s.get("mob_id")
        if k in sem_master:
            ponta = int(s.get("source_start_frames") or 0) + int(s.get("duration_frames") or 0) + 1
            m = masters_ref.setdefault(k, {"nome": s.get("clip_name") or str(k), "caminho_na_bin": "",
                                           "descritor": "", "fps": float(s.get("source_fps") or 0) or 25.0,
                                           "duracao_frames": 0, "essencias": {}})
            m["duracao_frames"] = max(m["duracao_frames"], ponta)
    metadados, offline_por_mob, deslocamento = _referencias_offline(masters_ref, pasta, colunas_por_mob or {})

    casados = []
    for i, s in enumerate(segmentos):
        chave = s.get("mob_id")
        if not chave or s.get("is_gap") or s.get("is_transition"):
            continue
        if i in por_segmento:
            caminho, e_offline = por_segmento[i], False
        elif chave in offline_por_mob:
            caminho, e_offline = offline_por_mob[chave], True
            d = deslocamento.get(chave)
            if d:
                s["source_start_frames"] = int(s.get("source_start_frames") or 0) + d
        else:
            continue
        if s.get("media_ref") is not None:
            s["media_ref"]["local_path"] = caminho
        casados.append({"segment_index": i, "matched_path": caminho, "rejected": False,
                        "source_offset_correction": 0, "offline": e_offline,
                        "gerenciada": i in na_gerenciada})
    return segmentos, casados, metadados


def montar_offline(resultado: dict, arquivos: dict[str, str], pasta: str,
                   colunas_por_mob: dict[str, dict] | None = None) -> tuple[list, list, dict]:
    return montar_midia(resultado, arquivos, pasta, colunas_por_mob)


def _referencias_offline(masters: dict, pasta: str, colunas_por_mob: dict) -> tuple:
    grupos: dict[tuple, dict] = {}
    for chave, m in masters.items():
        col = colunas_por_mob.get(chave) or {}
        ref, meta = referencia_offline(m, pasta, col.get("Source Path", ""),
                                       col.get("Source File", ""))
        ident = m.get("identidade") or {}
        chave_grupo = (ref, meta["fps"], ident.get("tc_fps"))
        g = grupos.setdefault(chave_grupo, {"meta": meta, "membros": {}})
        g["membros"][chave] = (ident.get("tc_inicio"), int(m.get("duracao_frames") or 0))

    offline_por_mob: dict[str, str] = {}
    metadados: dict[str, dict] = {}
    deslocamento: dict[str, int] = {}
    usados: dict[str, int] = {}
    for (ref, fps, tc_fps), g in grupos.items():
        n = usados.get(ref, 0)
        usados[ref] = n + 1
        caminho = ref if not n else f"{os.path.splitext(ref)[0]} ({n + 1}){os.path.splitext(ref)[1]}"
        meta = dict(g["meta"])
        com_tc = [(tc, nb) for tc, nb in g["membros"].values() if tc is not None]
        if com_tc and tc_fps:
            inicio = min(tc for tc, _ in com_tc)
            fim = max(tc + nb for tc, nb in com_tc)
            meta["nb"] = fim - inicio
            meta["tc"] = frames_to_tc(inicio, tc_fps)
            meta["tc_segundos"] = inicio / fps if fps else 0.0
            for chave, (tc, _nb) in g["membros"].items():
                if tc is not None and tc != inicio:
                    deslocamento[chave] = tc - inicio
        for chave in g["membros"]:
            offline_por_mob[chave] = caminho
        metadados[caminho] = meta
    return metadados, offline_por_mob, deslocamento


def casamentos(resultado: dict, arquivos: dict[str, str],
               offline: dict[str, str] | None = None) -> list[dict]:
    offline = offline or {}
    fora = []
    for i, s in enumerate(resultado.get("segments") or []):
        chave = s.get("mob_id") or ""
        caminho = arquivos.get(chave) or offline.get(chave)
        if not caminho or s.get("is_gap") or s.get("is_transition"):
            continue
        e_offline = chave not in arquivos
        if s.get("media_ref") is not None:
            s["media_ref"]["local_path"] = caminho
            s["media_ref"]["url"] = (Path(caminho).as_uri()
                                     if not e_offline and Path(caminho).is_absolute() else "")
        fora.append({"segment_index": i, "matched_path": caminho, "rejected": False,
                     "source_offset_correction": 0, "offline": e_offline})
    return fora
