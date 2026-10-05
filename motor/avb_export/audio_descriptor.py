#!/usr/bin/env python3
import json, subprocess
from media import fftools


def probe_audio(path, stream="a:0"):
    o = subprocess.check_output([
        fftools.ffprobe_exe(), "-v", "error", "-select_streams", stream, "-show_entries",
        "stream=channels,sample_rate,sample_fmt,codec_name,bits_per_sample,bits_per_raw_sample",
        "-of", "json", path])
    s = json.loads(o)["streams"][0]
    codec = s.get("codec_name", "") or ""
    fmt = s.get("sample_fmt", "") or ""
    is_float = codec.startswith("pcm_f") or fmt.startswith(("flt", "dbl"))
    bits = int(s.get("bits_per_sample") or 0) or int(s.get("bits_per_raw_sample") or 0)
    if not bits:
        bits = {"s16": 16, "s32": 32, "flt": 32, "dbl": 64}.get(fmt.rstrip("p"), 16)
    return {"channels": int(s["channels"]), "bits": bits, "sample_rate": float(s["sample_rate"]),
            "coding_format": 10 if is_float else 1}


def patch_pcma(descriptor, meta):
    pd = descriptor.property_data
    ch = meta["channels"]; bits = meta["bits"]; sr = meta["sample_rate"]
    pd["channels"] = ch
    pd["quantization_bits"] = bits
    pd["sample_rate"] = sr
    pd["edit_rate"] = sr
    pd["coding_format"] = meta["coding_format"]
    pd["block_align"] = ch * (bits // 8)
    pd["average_bps"] = ch * (bits // 8) * int(sr)
    attrs = pd.get("attributes")
    if attrs is not None:
        if meta["coding_format"] == 10:
            attrs["SoundEssenceCoding"] = bytearray.fromhex("060e2b34040101010e04020201010000")
        elif "SoundEssenceCoding" in attrs:
            del attrs["SoundEssenceCoding"]
    return descriptor


if __name__ == "__main__":
    import sys
    print(json.dumps(probe_audio(sys.argv[1]), indent=2))
