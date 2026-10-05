#!/usr/bin/env python3
import json, os, subprocess
from fractions import Fraction
from media import fftools

CONTAINER_HANDLER = {
    ".mov": "73dc063c76024c4cba3f3f47120cd1e9", ".mp4": "73dc063c76024c4cba3f3f47120cd1e9",
    ".m4v": "73dc063c76024c4cba3f3f47120cd1e9",
    ".wav": "3711d3cc62d049d7b0ae c118101d1a16".replace(" ", ""),
    ".aif": "3711d3cc62d049d7b0aec118101d1a16", ".aiff": "3711d3cc62d049d7b0aec118101d1a16",
    ".mp3": "6229779f27ce4d6cb0aec118101d1a16",
    ".png": "d945322d5e774c4bb0aec118101d1a16",
}
_AVC_PROFILE = {"High": 100, "Main": 77, "Baseline": 66, "Constrained Baseline": 66,
                "High 10": 110, "High 4:2:2": 122, "High 4:4:4": 244}
_UNIT = Fraction(5, 6)


def _ceil16(n):
    return ((int(n) + 15) // 16) * 16


def probe_video(path):
    o = subprocess.check_output([
        fftools.ffprobe_exe(), "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=width,height,display_aspect_ratio,profile,level,nb_frames,r_frame_rate,codec_name",
        "-of", "json", path])
    s = json.loads(o)["streams"][0]
    W, H = int(s["width"]), int(s["height"])
    dar = s.get("display_aspect_ratio", "")
    if dar and ":" in dar and "0" not in dar.split(":"):
        dn, dd = (int(x) for x in dar.split(":"))
    else:
        fr = Fraction(W, H); dn, dd = fr.numerator, fr.denominator
    num, den = (int(x) for x in s["r_frame_rate"].split("/"))
    nb = int(s["nb_frames"]) if str(s.get("nb_frames", "")).isdigit() else None
    return {"W": W, "H": H, "dar": [dn, dd], "level": int(s.get("level", 0) or 0),
            "profile": _AVC_PROFILE.get(s.get("profile"), 100), "nb_frames": nb,
            "fps": (num, den), "codec": s.get("codec_name")}


def _frac(x):
    x = Fraction(x)
    return [x.numerator, x.denominator]


def patch_cdci_geometry(descriptor, meta, ext=None):
    pd = descriptor.property_data
    W, H = meta["W"], meta["H"]
    Wb = W * _UNIT; Xb = -Wb / 2
    pd["stored_width"] = W; pd["sampled_width"] = W; pd["display_width"] = W
    pd["stored_height"] = _ceil16(H); pd["sampled_height"] = H; pd["display_height"] = H
    pd["aspect_ratio"] = list(meta["dar"])
    for bk in ("valid_box", "essence_box", "source_box"):
        b = [list(p) for p in pd[bk]]
        b[0] = _frac(Xb); b[2] = _frac(Wb)
        pd[bk] = b
    if meta.get("fps"):
        n, d = meta["fps"]; pd["edit_rate"] = round(n / d, 3)
    if meta.get("nb_frames"):
        pd["length"] = meta["nb_frames"]
    attrs = pd.get("attributes")
    if attrs is not None:
        sub = attrs.get("_SD_AVC_SUB_DESCRIPTOR")
        if sub is not None:
            if meta.get("level"):
                sub["ATN_AVCLevel"] = meta["level"]
            sub["ATN_AVCProfile"] = meta["profile"]
        if ext:
            h = CONTAINER_HANDLER.get(ext.lower())
            if h:
                attrs["_CONTAINER_HANDLER_GUID"] = bytearray(bytes.fromhex(h))
    return descriptor


def compute_boxes(W, H):
    Wb = W * _UNIT; Xb = -Wb / 2
    Hv = H * _UNIT; Yv = -Hv / 2
    Hs = _ceil16(H) * _UNIT; Ys = -Hs / 2
    valid = [_frac(Xb), _frac(Yv), _frac(Wb), _frac(Hv)]
    source = [_frac(Xb), _frac(Ys), _frac(Wb), _frac(Hs)]
    return {"valid_box": valid, "essence_box": list(valid), "source_box": source}


if __name__ == "__main__":
    import sys
    print(json.dumps(probe_video(sys.argv[1]), indent=2))
