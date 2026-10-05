import logging
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger("relinker.frames.extractor")


def timecode_to_seconds(timecode: str, fps: float) -> float:
    tc = timecode.replace(";", ":")
    parts = tc.split(":")
    if len(parts) != 4:
        raise ValueError(f"Timecode inválido: {timecode}")

    hh, mm, ss, ff = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    fps_int = round(fps) or 1
    total_frames = ((hh * 60 + mm) * 60 + ss) * fps_int + ff
    return total_frames / (fps if fps else fps_int)


def timecode_to_frames(timecode: str, fps: float) -> int:
    tc = timecode.replace(";", ":")
    parts = tc.split(":")
    if len(parts) != 4:
        raise ValueError(f"Timecode inválido: {timecode}")
    hh, mm, ss, ff = (int(p) for p in parts)
    fps_int = round(fps) or 1
    return ((hh * 60 + mm) * 60 + ss) * fps_int + ff


def frame_key(file_path: str, timecode: str, fps: float, accurate: bool, vf):
    from frames.cache import file_signature
    p = Path(file_path)
    return (str(p.resolve()), file_signature(str(p)),
            timecode, f"{fps:.6f}", int(bool(accurate)), vf or "")


def extract_frame(file_path: str, timecode: str, fps: float = 25.0, accurate: bool = True,
                  vf: Optional[str] = None, cache_dir: Optional[str] = None,
                  cache_label: Optional[str] = None, sequence: Optional[dict] = None) -> dict:
    import ffmpeg
    import bench
    import time as _time
    from frames.cache import resolve_cache_file, image_dimensions
    from frames.decoders import (sdk_kind, decode_via_helper, image_vf_prefix,
                                 IMAGE_FORMATS, oiiotool_available, oiiotool_cmd)

    _bench_t = _time.perf_counter()

    if sequence:
        from frames.sequence import resolve_frame_file
        file_path = resolve_frame_file(sequence, timecode_to_frames(timecode, fps))
        timecode = "00:00:00:00"

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de vídeo não encontrado: {file_path}")

    seconds = timecode_to_seconds(timecode, fps)
    ext = "jpg" if accurate else "png"

    frame_path = None
    if cache_dir:
        try:
            cp = resolve_cache_file(
                cache_dir, frame_key(file_path, timecode, fps, accurate, vf),
                label=cache_label, ext=ext)
            if cp.exists():
                w, h = image_dimensions(str(cp))
                if w and h:
                    log.info("Frame em cache: %s (%s TC=%s)", cp.name, path.name, timecode)
                    bench.record("frame", (_time.perf_counter() - _bench_t) * 1000.0,
                                 file=path.name, decoder="cache", cached=True,
                                 accurate=accurate)
                    return {"frame_path": str(cp), "timecode": timecode, "seconds": seconds,
                            "width": w, "height": h, "cached": True}
            frame_path = str(cp)
        except OSError as e:
            log.warning("cache_dir inutilizável (%s); usando tempfile", e)
            frame_path = None

    log.info("Extraindo frame de %s em TC=%s (%.3fs)", path.name, timecode, seconds)

    if frame_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False)
        tmp.close()
        frame_path = tmp.name

    kind = sdk_kind(path)

    success = False
    is_image = path.suffix.lower() in IMAGE_FORMATS
    done = False
    _dec = "ffmpeg"
    try:
        if kind:
            decode_via_helper(kind, str(path), timecode_to_frames(timecode, fps),
                              frame_path, max_width=(1280 if accurate else 360))
            done = True
            _dec = "sdk:" + kind
        elif is_image and accurate and oiiotool_available():
            import subprocess

            from media import fftools
            try:
                r = subprocess.run(oiiotool_cmd(str(path), frame_path, 1280),
                                   capture_output=True, text=True, timeout=120,
                                   **fftools.SEM_JANELA)
                if r.returncode == 0 and Path(frame_path).exists():
                    done = True
                    _dec = "oiio"
                else:
                    log.warning("oiiotool falhou em '%s' (%s) — fallback ffmpeg",
                                path.name, (r.stderr or '').strip()[:200])
            except Exception as e:
                log.warning("oiiotool erro em '%s' (%s) — fallback ffmpeg", path.name, e)
        if not done:
            img_pre = image_vf_prefix(path)
            vf_chain = ",".join(p for p in (img_pre, vf) if p)
            vf_chain = (vf_chain + ",") if vf_chain else ""
            if accurate:
                pre = max(0.0, seconds - 3.0)
                out = ffmpeg.input(str(path), ss=pre).output(
                    frame_path, ss=seconds - pre, vframes=1, format="image2",
                    vcodec="mjpeg", vf=vf_chain + "scale='min(1280,iw)':-2",
                    **{"qscale:v": 3})
            else:
                out = ffmpeg.input(str(path), ss=seconds).output(
                    frame_path, vframes=1, format="image2", vcodec="png",
                    vf=vf_chain + "scale=360:-2")
            try:
                out.overwrite_output().run(quiet=True)
            except ffmpeg.Error as e:
                raise RuntimeError(
                    f"ffmpeg falhou ao extrair frame de '{path.name}' em {timecode}: "
                    f"{e.stderr.decode() if e.stderr else str(e)}"
                )

        width, height = image_dimensions(frame_path)

        log.info("Frame extraído: %s (%dx%d)", frame_path, width or 0, height or 0)
        success = True
        bench.record("frame", (_time.perf_counter() - _bench_t) * 1000.0,
                     file=path.name, decoder=_dec, cached=False, accurate=accurate)
        return {
            "frame_path": frame_path,
            "timecode": timecode,
            "seconds": seconds,
            "width": width,
            "height": height,
            "cached": False
        }
    finally:
        if not success:
            try:
                Path(frame_path).unlink(missing_ok=True)
            except OSError:
                pass
