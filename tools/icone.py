from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "app" / "src-tauri" / "icons"

LADO = 1024
COR = (55, 138, 221, 255)
TRACO = 24 / 24 * 2


def _e(v: float) -> float:
    return v * LADO / 24.0


def _linha(d: ImageDraw.ImageDraw, pontos, largura: float) -> None:
    d.line([(_e(x), _e(y)) for x, y in pontos], fill=COR,
           width=int(largura), joint="curve")
    r = largura / 2
    for x, y in (pontos[0], pontos[-1]):
        d.ellipse([_e(x) - r, _e(y) - r, _e(x) + r, _e(y) + r], fill=COR)


def _arco(d: ImageDraw.ImageDraw, cx, cy, raio, ini, fim, largura: float) -> None:
    passos = 24
    pontos = []
    for i in range(passos + 1):
        a = math.radians(ini + (fim - ini) * i / passos)
        pontos.append((cx + raio * math.cos(a), cy + raio * math.sin(a)))
    _linha(d, pontos, largura)


def desenhar() -> Image.Image:
    img = Image.new("RGBA", (LADO, LADO), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = _e(TRACO)

    for cx, cy in ((6, 6), (18, 18)):
        r = _e(3)
        d.ellipse([_e(cx) - r, _e(cy) - r, _e(cx) + r, _e(cy) + r],
                  outline=COR, width=int(w))

    _linha(d, [(21, 11), (21, 8)], w)
    _arco(d, 19, 8, 2, 0, -90, w)
    _linha(d, [(19, 6), (13, 6)], w)
    _linha(d, [(16, 9), (13, 6), (16, 3)], w)

    _linha(d, [(3, 13), (3, 16)], w)
    _arco(d, 5, 16, 2, 180, 90, w)
    _linha(d, [(5, 18), (11, 18)], w)
    _linha(d, [(8, 15), (11, 18), (8, 21)], w)

    return img


def main() -> int:
    DESTINO.mkdir(parents=True, exist_ok=True)
    grande = desenhar()

    tamanhos = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    grande.resize((256, 256), Image.LANCZOS).save(
        DESTINO / "icon.ico", format="ICO", sizes=tamanhos)

    for lado in (32, 128, 256, 512, 1024):
        grande.resize((lado, lado), Image.LANCZOS).save(DESTINO / f"{lado}x{lado}.png")
    grande.save(DESTINO / "icon.png")

    print(f"ícones em {DESTINO}:")
    for f in sorted(DESTINO.iterdir()):
        print(f"   {f.name:<16} {f.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
