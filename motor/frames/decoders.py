import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

SDK_FORMATS = {
    ".braw": "braw",
    ".r3d":  "r3d",
    ".ari": "arri", ".arx": "arri", ".arriraw": "arri",
    ".crm": "canon", ".rmf": "canon",
}
SDK_LABELS = {
    "braw": "Blackmagic RAW", "r3d": "RED (R3D)", "arri": "ARRI (ARRIRAW)",
    "canon": "Canon Cinema RAW Light", "sony": "Sony X-OCN / RAW",
}
SDK_URLS = {
    "braw": "https://www.blackmagicdesign.com/support/family/blackmagic-raw",
    "r3d":  "https://www.red.com/download/r3d-sdk",
    "arri": "https://www.arri.com/en/learn-help/learn-help-camera-system/pre-postproduction/file-formats-data-handling/arriraw",
    "canon": "https://www.usa.canon.com/support/software-and-drivers",
    "sony": "https://www.sony.net/Products/RAWViewer/",
}
HELPER_NAMES = {
    "braw": "relinker-decode-braw",
    "r3d":  "relinker-decode-r3d",
    "arri": "relinker-decode-arri",
    "canon": "relinker-decode-canon",
    "sony": "relinker-decode-sony",
}
IMAGE_FORMATS = {".exr", ".dpx", ".tif", ".tiff", ".jp2", ".j2k", ".hdr", ".psd"}


class SdkUnavailable(Exception):
    def __init__(self, kind: str):
        self.kind = kind
        self.label = SDK_LABELS.get(kind, kind)
        self.url = SDK_URLS.get(kind)
        super().__init__(f"Formato requer o SDK: {self.label}")


_XOCN_CACHE: dict = {}


def _mxf_is_xocn(path) -> bool:
    key = str(path)
    if key in _XOCN_CACHE:
        return _XOCN_CACHE[key]
    found = False
    try:
        from pymediainfo import MediaInfo
        for t in MediaInfo.parse(path).tracks:
            if t.track_type == "Video":
                codec = " ".join(str(x) for x in (getattr(t, "format", None),
                                                  getattr(t, "codec_id", None),
                                                  getattr(t, "commercial_name", None)) if x).upper()
                found = ("X-OCN" in codec) or ("XOCN" in codec) or ("SONY RAW" in codec)
                break
    except Exception:
        found = False
    _XOCN_CACHE[key] = found
    return found


def sdk_kind(path) -> Optional[str]:
    ext = Path(path).suffix.lower()
    k = SDK_FORMATS.get(ext)
    if k:
        return k
    if ext == ".mxf" and _mxf_is_xocn(path):
        return "sony"
    return None


def _helper_dirs():
    dirs = []
    try:
        dirs.append(Path(sys.executable).resolve().parent)
    except Exception:
        pass
    base = Path(__file__).resolve().parents[1]
    dirs.append(base / "helpers" / "bin")
    dirs.append(base.parent / "src-tauri" / "binaries")
    return dirs


def helper_path(kind: str) -> Optional[str]:
    name = HELPER_NAMES.get(kind)
    if not name:
        return None
    for d in _helper_dirs():
        p = d / name
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    return shutil.which(name)


def helper_available(kind: str) -> bool:
    return helper_path(kind) is not None


def sdk_status() -> dict:
    return {k: {"label": SDK_LABELS[k], "available": helper_available(k), "url": SDK_URLS.get(k)}
            for k in HELPER_NAMES}


_BRAW_FRAMEWORK_DIRS = [
    "/Applications/Blackmagic RAW/Blackmagic RAW SDK/Mac/Libraries",
    "/Applications/Blackmagic RAW/Blackmagic RAW Player.app/Contents/Frameworks",
    "/Applications/Blackmagic RAW/Blackmagic RAW Speed Test.app/Contents/Frameworks",
    "/Library/Frameworks",
]


def _braw_libraries() -> Optional[str]:
    if os.environ.get("BRAW_LIBRARIES"):
        return os.environ["BRAW_LIBRARIES"]
    for d in _BRAW_FRAMEWORK_DIRS:
        if (Path(d) / "BlackmagicRawAPI.framework").is_dir():
            return d
    return None


def decode_via_helper(kind: str, file: str, frame_number: int, out_path: str,
                      max_width: int = 1280, timeout: int = 180) -> str:
    exe = helper_path(kind)
    if not exe:
        raise SdkUnavailable(kind)
    cmd = [exe, "--file", str(file), "--frame", str(int(frame_number)),
           "--out", str(out_path), "--max-width", str(int(max_width))]
    env = None
    if kind == "braw":
        libs = _braw_libraries()
        if libs:
            env = {**os.environ, "BRAW_LIBRARIES": libs}
    try:
        from media import fftools
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env=env, **fftools.SEM_JANELA)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{HELPER_NAMES[kind]}: timeout ao decodificar frame {frame_number}")
    if r.returncode != 0 or not Path(out_path).exists():
        raise RuntimeError(
            f"{HELPER_NAMES[kind]} falhou (frame {frame_number}): "
            f"{(r.stderr or '').strip() or ('código ' + str(r.returncode))}")
    return str(out_path)


def image_vf_prefix(path) -> str:
    if Path(path).suffix.lower() == ".exr":
        return "eq=gamma=0.4545"
    return ""


OIIO_FORMATS = {".exr", ".dpx", ".tif", ".tiff", ".jp2", ".j2k", ".hdr", ".psd"}
_OIIOTOOL = None


def oiiotool_available() -> bool:
    return bool(oiiotool_path())


def oiiotool_path() -> Optional[str]:
    global _OIIOTOOL
    if _OIIOTOOL is None:
        for d in _helper_dirs():
            p = d / "oiiotool"
            if p.exists() and os.access(p, os.X_OK):
                _OIIOTOOL = str(p)
                break
        else:
            _OIIOTOOL = shutil.which("oiiotool") or ""
    return _OIIOTOOL or None


def oiiotool_cmd(in_path: str, out_path: str, max_width: int) -> list:
    exe = oiiotool_path()
    cmd = [exe, str(in_path)]
    if Path(in_path).suffix.lower() == ".exr":
        cmd += ["--colorconvert", "linear", "sRGB"]
    if max_width and max_width > 0:
        cmd += ["--resize", f"{int(max_width)}x0"]
    cmd += ["-o:type=uint8", str(out_path)]
    return cmd
