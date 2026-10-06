from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import numpy as np
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from media import fftools

W, H = 160, 90


def _cinza(caminho: str | None = None, ref: str | None = None, quadro: int | None = None,
           fps: float = 24.0) -> np.ndarray | None:
    if ref is not None:
        cmd = [fftools.ffmpeg(), "-v", "error", "-ss", f"{quadro / fps:.6f}", "-i", ref, "-frames:v", "1"]
    else:
        cmd = [fftools.ffmpeg(), "-v", "error", "-i", caminho]
    cmd += ["-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"]
    out = subprocess.run(cmd, capture_output=True).stdout
    if len(out) < W * H:
        return None
    a = np.frombuffer(out[:W * H], dtype=np.uint8).astype(np.float64).reshape(H, W)
    return a


def _norm(a: np.ndarray) -> np.ndarray:
    a = a - a.mean()
    d = a.std()
    return a / d if d > 1e-6 else a


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    return float((_norm(a) * _norm(b)).mean())


_M = 8


def _melhor_deslocamento(a: np.ndarray, b: np.ndarray, r: int = 8) -> tuple[float, int, int]:
    best = (-2.0, 0, 0)
    m = r
    h, w = a.shape
    ac = a[m:h - m, m:w - m]
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            bc = b[m + dy:h - m + dy, m + dx:w - m + dx]
            c = _corr(ac, bc)
            if c > best[0]:
                best = (c, dx, dy)
    return best


LIMIAR = 0.75
SEGUNDOS_POR_PONTO = 0.5


def comparar_um(imagem: str, referencia: str, quadro_ref: int, fps: float = 24.0,
                limiar: float = LIMIAR, quadro_px: tuple[int, int] = (2880, 1620)) -> dict:
    a = _cinza(imagem)
    b = _cinza(ref=referencia, quadro=quadro_ref, fps=fps)
    if a is None or b is None:
        return {"erro": "não leu" if a is None else "a referência não tem esse quadro"}
    a, b = a[_M:H - _M, _M:W - _M], b[_M:H - _M, _M:W - _M]
    if a.std() < 6 and b.std() < 6:
        return {"corr": 1.0, "preto": True}
    c = _corr(a, b)
    item = {"corr": round(c, 3)}
    if c < limiar:
        cd, dx, dy = _melhor_deslocamento(a, b, r=4)
        item.update(corr_deslocada=round(cd, 3), dx_px=dx * quadro_px[0] // W,
                    dy_px=dy * quadro_px[1] // H)
    return item


def conferir(pasta: str, referencia: str, fps: float = 24.0, limiar: float = LIMIAR) -> list[dict]:
    res = []
    for nome in sorted(os.listdir(pasta), key=lambda n: (len(n), n)):
        base, ext = os.path.splitext(nome)
        if ext.lower() not in (".png", ".jpg") or not base.isdigit():
            continue
        q = int(base)
        res.append({"quadro": q, **comparar_um(os.path.join(pasta, nome), referencia, q, fps, limiar)})
    with open(os.path.join(pasta, "conferencia.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    return res


_IMAGENS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".psd", ".tga", ".exr", ".dpx"}


def _cobre(s: dict) -> bool:
    if s.get("is_gap") or s.get("is_transition") or s.get("is_effect") or s.get("disabled"):
        return False
    ref = (s.get("media_ref") or {}).get("local_path") or ""
    if os.path.splitext(ref)[1].lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".psd"}:
        return False
    p = ((s.get("effects") or [{}])[0] or {}).get("params") or {}
    if (p.get("scale_x") or 1.0) < 0.999 or any((p.get("crop_frac") or {}).values()):
        return False
    return True


def pontos_visiveis(segmentos: list[dict], fps: int = 24, margem: int = 1) -> list[dict]:
    def tcq(tc):
        h, m, s_, f = (int(x) for x in tc.split(":"))
        return ((h * 60 + m) * 60 + s_) * fps + f
    planos = [s for s in segmentos if not (s.get("is_gap") or s.get("is_transition")
                                            or s.get("is_audio") or s.get("disabled")
                                            or s.get("referencia") or s.get("trilha_desligada"))]
    trans = [(tcq(s["timeline_tc_in"]), tcq(s["timeline_tc_in"]) + s["duration_frames"], s["track"])
             for s in segmentos if s.get("is_transition") and not s.get("is_audio")]
    out = []
    for s in planos:
        if s.get("is_effect"):
            continue
        a = tcq(s["timeline_tc_in"])
        b = a + s["duration_frames"]
        ref_s = (s.get("media_ref") or {}).get("local_path") or s.get("clip_name") or ""
        if os.path.splitext(ref_s)[1].lower() in _IMAGENS and any(
                o["track"] < s["track"] and not o.get("is_effect")
                and tcq(o["timeline_tc_in"]) < b and tcq(o["timeline_tc_in"]) + o["duration_frames"] > a
                for o in planos):
            continue
        livres = [(a, b)]
        cobertas = [(tcq(o["timeline_tc_in"]), tcq(o["timeline_tc_in"]) + o["duration_frames"])
                    for o in planos if o["track"] > s["track"] and _cobre(o)]
        cobertas += [(x, y) for x, y, tr in trans if tr == s["track"]]
        for x, y in cobertas:
            novo = []
            for u, v in livres:
                if y <= u or x >= v:
                    novo.append((u, v))
                    continue
                if u < x:
                    novo.append((u, x))
                if y < v:
                    novo.append((y, v))
            livres = novo
        livres = [(u, v) for u, v in livres if v - u > 2 * margem]
        if not livres:
            continue
        nome = (s.get("prproj") or {}).get("plano") or s.get("clip_name")
        out.append({"quadro": livres[0][0] + margem, "ponta": "entrada", "plano": nome,
                    "trilha": s["track"], "arquivo": s.get("clip_name")})
        out.append({"quadro": livres[-1][1] - 1 - margem, "ponta": "saída", "plano": nome,
                    "trilha": s["track"], "arquivo": s.get("clip_name")})
    return out


def _tc(q: int, fps: int = 24, base_h: int = 1) -> str:
    q += base_h * 3600 * fps
    s, f = divmod(q, fps)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pasta")
    ap.add_argument("referencia")
    ap.add_argument("--fps", type=float, default=24.0)
    ap.add_argument("--limiar", type=float, default=0.75)
    a = ap.parse_args(argv)
    res = conferir(a.pasta, a.referencia, a.fps, a.limiar)
    ruins = sorted((r for r in res if r.get("corr", 1) < a.limiar), key=lambda r: r["corr"])
    print(f"{len(res)} pontos, {len(ruins)} abaixo de {a.limiar}")
    for r in ruins[:40]:
        print(f"  {_tc(r['quadro'])}  corr {r['corr']:.2f}  deslocada {r.get('corr_deslocada')} "
              f"({r.get('dx_px')}, {r.get('dy_px')}) px")
    return 0


if __name__ == "__main__":
    sys.exit(main())
