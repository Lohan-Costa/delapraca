from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("relinker")

_TANGENT_KEYS = {
    "PP_OUT_TANGENT_POS_U": "out_dt",
    "PP_OUT_TANGENT_VAL_U": "out_dv",
    "PP_IN_TANGENT_POS_U": "in_dt",
    "PP_IN_TANGENT_VAL_U": "in_dv",
}


def _control_point_handles(cp) -> dict:
    h = {"out_dt": 0.0, "out_dv": 0.0, "in_dt": 0.0, "in_dv": 0.0, "mode": None}
    try:
        for _pid, prop in cp.property_entries.items():
            try:
                val = prop.value
            except Exception:
                continue
            if isinstance(val, list) and val and hasattr(val[0], "name"):
                for cv in val:
                    nm = getattr(cv, "name", "")
                    key = _TANGENT_KEYS.get(nm)
                    if key is not None:
                        try:
                            h[key] = float(cv.value_at(0))
                        except Exception:
                            pass
                    elif nm == "PP_TANGENT_MODE_U":
                        try:
                            h["mode"] = int(cv.value_at(0))
                        except Exception:
                            pass
    except Exception:
        pass
    return h


def extract_speed_keyframes(speed_map_param) -> list[dict]:
    kfs: list[dict] = []
    try:
        points = list(speed_map_param["PointList"].value)
    except Exception:
        return kfs
    for cp in points:
        try:
            t = float(cp.time)
            v = float(cp.value)
        except Exception:
            continue
        h = _control_point_handles(cp)
        kfs.append({"t": round(t, 4), "speed": round(v, 6), **h})
    kfs.sort(key=lambda k: k["t"])
    return kfs


def _bez(p, u):
    mt = 1.0 - u
    return (mt * mt * mt * p[0] + 3 * mt * mt * u * p[1]
            + 3 * mt * u * u * p[2] + u * u * u * p[3])


def eval_speed(keyframes: list[dict], x: float) -> float:
    if not keyframes:
        return 1.0
    if x <= keyframes[0]["t"]:
        return keyframes[0]["speed"]
    if x >= keyframes[-1]["t"]:
        return keyframes[-1]["speed"]
    for i in range(len(keyframes) - 1):
        k0, k1 = keyframes[i], keyframes[i + 1]
        if k0["t"] <= x <= k1["t"]:
            xs = (k0["t"], k0["t"] + k0["out_dt"], k1["t"] + k1["in_dt"], k1["t"])
            ys = (k0["speed"], k0["speed"] + k0["out_dv"],
                  k1["speed"] + k1["in_dv"], k1["speed"])
            lo, hi = 0.0, 1.0
            for _ in range(50):
                u = (lo + hi) * 0.5
                if _bez(xs, u) < x:
                    lo = u
                else:
                    hi = u
            return _bez(ys, (lo + hi) * 0.5)
    return keyframes[-1]["speed"]


def integrate_offset_map(keyframes: list[dict], length: int,
                         step: float = 0.5) -> list[tuple]:
    if not keyframes or length <= 0:
        return []
    out = [(0.0, 0.0)]
    acc = 0.0
    f = 0.0
    while f < length - 1e-9:
        a = eval_speed(keyframes, f)
        m = eval_speed(keyframes, f + 0.5)
        b = eval_speed(keyframes, f + 1.0)
        acc += (a + 4 * m + b) / 6.0
        f += 1.0
        out.append((round(f, 3), round(acc, 3)))
    return out


def average_speed(keyframes: list[dict], length: int) -> float:
    om = integrate_offset_map(keyframes, length)
    if not om or length <= 0:
        return 100.0
    return round(om[-1][1] / length * 100.0, 1)
