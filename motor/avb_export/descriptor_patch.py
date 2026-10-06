#!/usr/bin/env python3
import struct

from . import offsets as O


def _next_s32(log, byoff, off):
    i = byoff[off] + 1
    while log[i]["kind"] not in ("read_s32le", "read_u32le"):
        i += 1
    return log[i]["off"]


def plan_video_geometry(log, tpl_pd, target_pd):
    byoff = {e["off"]: i for i, e in enumerate(log)}
    P = {}
    w_offs = O.find_field(log, tpl_pd["stored_width"])
    if not w_offs:
        raise ValueError("stored_width do template nao localizado no log")
    if target_pd["stored_width"] != tpl_pd["stored_width"]:
        for off in w_offs:
            P[off] = target_pd["stored_width"]
    anchor = min(w_offs)
    if target_pd["stored_height"] != tpl_pd["stored_height"]:
        P[anchor - 4] = target_pd["stored_height"]
    if list(target_pd["aspect_ratio"]) != list(tpl_pd["aspect_ratio"]):
        P[anchor + 38] = target_pd["aspect_ratio"][0]
        P[_next_s32(log, byoff, anchor + 38)] = target_pd["aspect_ratio"][1]
    for comp in (0, 2):
        tval = tpl_pd["valid_box"][comp]
        nval = target_pd["valid_box"][comp]
        if list(nval) == list(tval):
            continue
        for off in O.find_field(log, tval[0]):
            P[off] = nval[0]
            P[_next_s32(log, byoff, off)] = nval[1]
    return P


def apply_patches(raw, base, patches):
    for off, val in patches.items():
        raw[base + off:base + off + 4] = struct.pack("<i", int(val))
    return raw
