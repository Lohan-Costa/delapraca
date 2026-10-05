#!/usr/bin/env python3
from avb.ioctx import AVBIOContext

from . import reader as avb_read

_PRIMS = {"read_u8": 1, "read_bool": 1, "read_u16le": 2, "read_u32le": 4,
          "read_s16le": 2, "read_s32le": 4, "read_u64le": 8, "read_s64le": 8}


def map_object(path, instance_id):
    log = []
    recording = {"on": False}
    orig = {}

    def wrap(name, size, raw_fn, is_static):
        def w(f, *a, **k):
            pos = f.tell()
            v = raw_fn(f, *a, **k)
            if recording["on"]:
                log.append({"off": pos, "size": size, "kind": name, "value": v})
            return v
        return staticmethod(w) if is_static else w

    for name, size in _PRIMS.items():
        desc = AVBIOContext.__dict__.get(name)
        if desc is None:
            continue
        orig[name] = desc
        is_static = isinstance(desc, staticmethod)
        raw_fn = desc.__func__ if is_static else desc
        setattr(AVBIOContext, name, wrap(name, size, raw_fn, is_static))
    try:
        with avb_read.topen(path) as f:
            f.object_cache.pop(instance_id, None)
            recording["on"] = True
            f.read_object(instance_id)
            recording["on"] = False
    finally:
        for name, desc in orig.items():
            setattr(AVBIOContext, name, desc)
    return log


def find_field(log, value, kind=None):
    return [e["off"] for e in log if e["value"] == value and (kind is None or e["kind"] == kind)]


def file_offset(f, instance_id, off_in_obj):
    return f.object_positions[instance_id] + 8 + off_in_obj
