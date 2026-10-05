import re
from pathlib import Path

_SEQ_RE = re.compile(r"^(?P<base>.*?)(?P<sep>[._-]?)(?P<num>\d+)$")


def parse_sequence_name(stem: str):
    m = _SEQ_RE.match(stem)
    if not m or not m.group("num"):
        return None
    return m.group("base"), m.group("sep"), m.group("num"), len(m.group("num"))


def resolve_frame_file(sequence: dict, frame_index: int) -> str:
    n = int(sequence["first"]) + max(0, int(frame_index))
    last = int(sequence.get("last", n))
    n = min(n, last)
    name = f"{sequence['base']}{sequence['sep']}{n:0{int(sequence['pad'])}d}{sequence['ext']}"
    return str(Path(sequence["dir"]) / name)
