from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import fftools, safepath

AVID_DEFAULT_TC = "01:00:00:00"

TC_SENTINELAS = (AVID_DEFAULT_TC, "00:00:00:00")


@dataclass
class OpAtomFile:

    path: str
    clip_umid: str
    clip_name: str | None
    file_umid: str | None
    kind: str
    track_index: int | None
    fps_num: int | None = None
    fps_den: int | None = None
    duration_frames: int | None = None
    duration_s: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    tc_start: str | None = None
    reel_name: str | None = None
    op_pattern: str | None = None

    @property
    def fps(self) -> float | None:
        if not self.fps_num or not self.fps_den:
            return None
        return self.fps_num / self.fps_den


@dataclass
class OpAtomClip:

    umid: str
    name: str | None
    video: str | None = None
    audio: list[str] = field(default_factory=list)
    data: list[str] = field(default_factory=list)
    fps_num: int | None = None
    fps_den: int | None = None
    duration_frames: int | None = None
    duration_s: float | None = None
    sample_rate: int | None = None
    channels_total: int = 0
    tc_start: str | None = None
    tc_is_avid_default: bool = False

    @property
    def fps(self) -> float | None:
        if not self.fps_num or not self.fps_den:
            return None
        return self.fps_num / self.fps_den

    @property
    def paths(self) -> list[str]:
        return ([self.video] if self.video else []) + self.audio + self.data

    @property
    def has_video(self) -> bool:
        return self.video is not None


@dataclass
class ScanResult:
    clips: list[OpAtomClip] = field(default_factory=list)
    ignored: list[tuple[str, str]] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)


def is_op_atom(op_pattern: str | None) -> bool:
    if not op_pattern:
        return False
    grupos = op_pattern.strip().lower().split(".")
    if len(grupos) != 4:
        return False
    return grupos[2] == "0d010201" and grupos[3][:2] == "10"


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _fraction(raw: str | None) -> tuple[int | None, int | None]:
    if not raw or "/" not in raw:
        return None, None
    num, _, den = raw.partition("/")
    n, d = _int(num), _int(den)
    if not n or not d:
        return None, None
    return n, d


def _video_rate(stream: dict) -> tuple[int | None, int | None]:
    candidatas: list[tuple[int, int]] = []

    tb_num, tb_den = _fraction(stream.get("time_base"))
    pelo_time_base = (tb_den, tb_num) if tb_num and tb_den else (None, None)

    for chave in ("avg_frame_rate", "r_frame_rate"):
        n, d = _fraction(stream.get(chave))
        if n and d:
            candidatas.append((n, d))
    if pelo_time_base[0] and pelo_time_base[1]:
        candidatas.append(pelo_time_base)

    if not candidatas:
        return None, None

    def valor(par):
        return par[0] / par[1]

    for par in candidatas:
        if sum(1 for outra in candidatas if abs(valor(outra) - valor(par)) < 1e-6) >= 2:
            return par
    return pelo_time_base if pelo_time_base[0] else candidatas[0]


def _essence(streams: list[dict]) -> tuple[dict | None, int | None]:
    for pos, s in enumerate(streams):
        if s.get("codec_type") in ("video", "audio"):
            return s, _int(s.get("index")) if s.get("index") is not None else pos
    return None, None


def parse_probe(doc: dict, path: str) -> OpAtomFile | str:
    fmt = doc.get("format") or {}
    ftags = fmt.get("tags") or {}
    streams = doc.get("streams") or []

    op = ftags.get("operational_pattern_ul")
    if not is_op_atom(op):
        return f"não é OP-Atom (operational_pattern_ul={op or 'ausente'})"

    umid = ftags.get("material_package_umid")
    if not umid:
        return "sem material_package_umid (chave de agrupamento)"

    essence, track_index = _essence(streams)
    if essence is None:
        return _data_file(path, umid, ftags, op)

    kind = essence["codec_type"]
    stags = essence.get("tags") or {}
    fps_num, fps_den = _video_rate(essence) if kind == "video" else (None, None)

    duration_s = None
    try:
        duration_s = float(essence["duration"])
    except (KeyError, TypeError, ValueError):
        pass

    duration_frames = None
    tb_num, tb_den = _fraction(essence.get("time_base"))
    dur_ts = _int(essence.get("duration_ts"))
    if kind == "video" and dur_ts and tb_num and tb_den and fps_num and fps_den:
        duration_frames = round(dur_ts * tb_num * fps_num / (tb_den * fps_den))

    return OpAtomFile(
        path=path,
        clip_umid=umid,
        clip_name=ftags.get("material_package_name"),
        file_umid=stags.get("file_package_umid"),
        kind=kind,
        track_index=track_index,
        fps_num=fps_num,
        fps_den=fps_den,
        duration_frames=duration_frames,
        duration_s=duration_s,
        sample_rate=_int(essence.get("sample_rate")),
        channels=_int(essence.get("channels")),
        tc_start=stags.get("timecode"),
        reel_name=stags.get("reel_name"),
        op_pattern=op,
    )


def _data_file(path: str, umid: str, ftags: dict, op: str | None) -> OpAtomFile:
    return OpAtomFile(
        path=path,
        clip_umid=umid,
        clip_name=ftags.get("material_package_name"),
        file_umid=None,
        kind="data",
        track_index=None,
        op_pattern=op,
    )


def group(files: list[OpAtomFile]) -> tuple[list[OpAtomClip], list[str]]:
    por_clipe: dict[str, list[OpAtomFile]] = {}
    vistos: set[str] = set()
    duplicatas: list[str] = []

    for f in files:
        if f.file_umid:
            if f.file_umid in vistos:
                duplicatas.append(f.path)
                continue
            vistos.add(f.file_umid)
        por_clipe.setdefault(f.clip_umid, []).append(f)

    clipes: list[OpAtomClip] = []
    for umid, tracks in por_clipe.items():
        video = next((t for t in tracks if t.kind == "video"), None)
        audios = sorted(
            (t for t in tracks if t.kind == "audio"),
            key=lambda t: (t.track_index if t.track_index is not None else 1 << 30, t.path),
        )
        dados = [t for t in tracks if t.kind == "data"]

        fonte = video or (audios[0] if audios else tracks[0])
        tc = fonte.tc_start

        clipes.append(
            OpAtomClip(
                umid=umid,
                name=next((t.clip_name for t in tracks if t.clip_name), None),
                video=video.path if video else None,
                audio=[t.path for t in audios],
                data=[t.path for t in dados],
                fps_num=fonte.fps_num,
                fps_den=fonte.fps_den,
                duration_frames=fonte.duration_frames,
                duration_s=fonte.duration_s,
                sample_rate=next((t.sample_rate for t in audios if t.sample_rate), None),
                channels_total=sum(t.channels or 0 for t in audios),
                tc_start=tc,
                tc_is_avid_default=(tc == AVID_DEFAULT_TC),
            )
        )

    return clipes, duplicatas


def timecode_report(clips: list[OpAtomClip]) -> dict:
    com_tc = [c.tc_start for c in clips if c.tc_start]
    distintos = sorted(set(com_tc))
    repetidos = sorted({tc for tc in distintos if com_tc.count(tc) > 1})
    sentinelas = [tc for tc in repetidos if tc in TC_SENTINELAS]

    if not com_tc:
        veredicto = "ausente"
    elif len(clips) < 2:
        veredicto = "indeterminado"
    elif len(distintos) == 1:
        veredicto = "suspeito_material"
    elif sentinelas:
        veredicto = "misto_suspeito"
    else:
        veredicto = "utilizavel"

    return {
        "veredicto": veredicto,
        "com_tc": len(com_tc),
        "sem_tc": len(clips) - len(com_tc),
        "distintos": len(distintos),
        "repetidos": repetidos,
        "sentinelas_repetidos": sentinelas,
        "em_padrao_avid": sum(1 for c in clips if c.tc_is_avid_default),
    }


def timecode_verdict(clips: list[OpAtomClip]) -> str:
    return timecode_report(clips)["veredicto"]


def probe_file(path: str, timeout: int = 30) -> dict | None:
    exe = fftools.ffprobe()
    if not exe:
        return None
    try:
        saida = subprocess.run(
            [exe, "-v", "error", "-show_format", "-show_streams", "-of", "json",
             safepath.ff_input(path)],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=True, **fftools.SEM_JANELA,
        ).stdout
        return json.loads(saida)
    except Exception:
        return None


def _mxf_files(roots) -> list[str]:
    achados: list[str] = []
    for root in roots:
        p = Path(root)
        if p.is_file():
            candidatos = [p]
        else:
            candidatos = sorted(q for q in p.rglob("*") if q.is_file())
        for q in candidatos:
            if q.suffix.lower() == ".mxf" and not q.name.startswith("._"):
                achados.append(str(q))
    return sorted(achados)


def colapsar_entradas(entries: list[dict], on_progress=None) -> list[dict]:
    saida: list[dict] = []
    mxf: list[dict] = []
    for e in entries:
        entrada = {"path": e, "group_id": e, "group_order": None} if isinstance(e, str) else dict(e)
        if entrada.get("audio_path"):
            saida.append(entrada)
        elif Path(entrada["path"]).suffix.lower() == ".mxf":
            mxf.append(entrada)
        else:
            saida.append(entrada)

    if not mxf:
        return saida

    por_caminho = {e["path"]: e for e in mxf}
    arquivos: list[OpAtomFile] = []
    total = len(mxf)
    for i, e in enumerate(mxf, 1):
        if on_progress:
            on_progress(i, total, e["path"])
        doc = probe_file(e["path"])
        lido = parse_probe(doc, e["path"]) if doc is not None else "ffprobe não leu"
        if isinstance(lido, str):
            saida.append(e)
        else:
            arquivos.append(lido)

    clipes, _duplicatas = group(arquivos)
    for c in clipes:
        base = por_caminho.get(c.video or (c.audio[0] if c.audio else ""), {})
        if not base:
            base = next((por_caminho[p] for p in c.paths if p in por_caminho), {})
        entrada = dict(base)
        entrada["path"] = c.video or (c.audio[0] if c.audio else base.get("path"))
        if c.audio:
            entrada["audio_path"] = c.audio[0]
            entrada["audio_channels"] = c.channels_total or len(c.audio)
        if not c.has_video:
            entrada["kind"] = "sound"
        saida.append(entrada)
    return saida


def scan(roots, on_progress=None) -> ScanResult:
    caminhos = _mxf_files(roots)
    total = len(caminhos)
    arquivos: list[OpAtomFile] = []
    ignorados: list[tuple[str, str]] = []

    for i, caminho in enumerate(caminhos, 1):
        if on_progress:
            on_progress(i, total, caminho)
        doc = probe_file(caminho)
        if doc is None:
            ignorados.append((caminho, "ffprobe não leu o arquivo"))
            continue
        lido = parse_probe(doc, caminho)
        if isinstance(lido, str):
            ignorados.append((caminho, lido))
        else:
            arquivos.append(lido)

    clipes, duplicatas = group(arquivos)
    clipes.sort(key=lambda c: (c.name or "", c.umid))
    return ScanResult(clips=clipes, ignored=ignorados, duplicates=duplicatas)
