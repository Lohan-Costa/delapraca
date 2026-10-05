import logging
from pathlib import Path

log = logging.getLogger("relinker.frames.comparator")

DEFAULT_THRESHOLD = 10


def compare_frames(frame_a: str, frame_b: str, threshold: int = DEFAULT_THRESHOLD) -> dict:
    import imagehash
    from PIL import Image

    path_a = Path(frame_a)
    path_b = Path(frame_b)

    if not path_a.exists():
        raise FileNotFoundError(f"Frame A não encontrado: {frame_a}")
    if not path_b.exists():
        raise FileNotFoundError(f"Frame B não encontrado: {frame_b}")

    log.info("Comparando frames: %s × %s", path_a.name, path_b.name)

    img_a = Image.open(str(path_a)).convert("RGB")
    img_b = Image.open(str(path_b)).convert("RGB")

    import numpy as np
    LOW_DETAIL_STD = 12.0
    std_a = float(np.asarray(img_a.convert("L"), dtype=np.float32).std())
    std_b = float(np.asarray(img_b.convert("L"), dtype=np.float32).std())
    low_detail = std_a < LOW_DETAIL_STD or std_b < LOW_DETAIL_STD

    hash_a = imagehash.phash(img_a)
    hash_b = imagehash.phash(img_b)

    distance = hash_a - hash_b
    max_bits = 64
    score = 1.0 - (distance / max_bits)

    is_match = distance <= threshold

    log.info(
        "Resultado: distância=%d bits, score=%.3f, match=%s low_detail=%s (std %.1f/%.1f)",
        distance, score, is_match, low_detail, std_a, std_b
    )

    return {
        "score": round(float(score), 4),
        "hamming_distance": int(distance),
        "is_match": bool(is_match),
        "threshold_used": int(threshold),
        "low_detail": bool(low_detail),
    }


def corr_score(path_a: str, path_b: str) -> float:
    import cv2
    a = cv2.imread(str(path_a), cv2.IMREAD_GRAYSCALE)
    b = cv2.imread(str(path_b), cv2.IMREAD_GRAYSCALE)
    if a is None or b is None:
        return -1.0
    if b.shape != a.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    try:
        res = cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)
        return float(res[0][0])
    except Exception:
        return -1.0

