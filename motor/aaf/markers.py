from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("relinker")


def _attr_map(marker) -> dict:
    out: dict = {}
    try:
        for tv in (marker["CommentMarkerAttributeList"].value or []):
            try:
                out[getattr(tv, "name", "")] = tv.value
            except Exception:
                continue
    except Exception:
        pass
    return out


def _prop(marker, key, default=None):
    try:
        if key in marker:
            return marker[key].value
    except Exception:
        pass
    return default


def read_markers(main_comp, fps: float = 0.0) -> list[dict]:
    markers: list[dict] = []
    try:
        slots = list(main_comp.slots)
    except Exception:
        return markers

    for slot in slots:
        if type(slot).__name__ != "EventMobSlot":
            continue
        seg = getattr(slot, "segment", None)
        if seg is None:
            continue
        comps = list(getattr(seg, "components", []) or [seg])
        for c in comps:
            if "Marker" not in type(c).__name__:
                continue
            attrs = _attr_map(c)
            described = _prop(c, "DescribedSlots", set()) or set()
            try:
                described = sorted(int(x) for x in described)
            except Exception:
                described = list(described)
            markers.append({
                "position": int(_prop(c, "Position", 0) or 0),
                "comment": _prop(c, "Comment", "") or attrs.get("_ATN_CRM_COM", "") or "",
                "color": (attrs.get("_ATN_CRM_COLOR_EXTENDED")
                          or attrs.get("_ATN_CRM_COLOR") or ""),
                "color_rgb": _prop(c, "CommentMarkerColor", None),
                "user": _prop(c, "CommentMarkerUSer", attrs.get("_ATN_CRM_USER")) or "",
                "date": attrs.get("_ATN_CRM_DATE", "") or "",
                "time": attrs.get("_ATN_CRM_TIME", "") or "",
                "described_slots": described,
                "id": attrs.get("_ATN_CRM_ID", "") or "",
                "slot_id": getattr(slot, "slot_id", None),
            })

    markers.sort(key=lambda m: m["position"])
    log.info("Locators lidos: %d", len(markers))
    return markers
