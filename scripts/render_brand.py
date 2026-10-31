"""Render SolarSplit brand assets: icon, logo and dark logo, at 1x and 2x.

Usage: python scripts/render_brand.py custom_components/solarsplit/brand (needs Pillow; uses Segoe UI Bold).
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
S = 2048  # supersampled canvas


def icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # vertical amber -> deep orange gradient inside a rounded square
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    top, bot = (251, 191, 36), (234, 88, 12)
    for y in range(S):
        t = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bot)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([64, 64, S - 64, S - 64], radius=448, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    # sun
    cx, cy, r = S / 2, 690, 250
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=white)
    for k in range(8):
        a = math.pi * k / 4
        r1, r2 = r + 90, r + 230
        d.line([(cx + r1 * math.cos(a), cy + r1 * math.sin(a)), (cx + r2 * math.cos(a), cy + r2 * math.sin(a))],
               fill=white, width=100)
        for rr in (r1, r2):
            px, py = cx + rr * math.cos(a), cy + rr * math.sin(a)
            d.ellipse([px - 50, py - 50, px + 50, py + 50], fill=white)
    # three loads by priority: tallest first
    base, w, gap = 1730, 300, 110
    x0 = S / 2 - (3 * w + 2 * gap) / 2
    for i, h in enumerate((560, 420, 280)):
        x = x0 + i * (w + gap)
        d.rounded_rectangle([x, base - h, x + w, base], radius=70, fill=(255, 255, 255, 255 - i * 55))
    return img.resize((size, size), Image.LANCZOS)


def logo(height: int, text_rgb: tuple[int, int, int]) -> Image.Image:
    W, H = 700 * height // 256, height
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.alpha_composite(icon(H), (0, 0))
    font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", round(H * 0.36))
    d = ImageDraw.Draw(img)
    d.text((H + round(H * 0.08), H / 2), "SolarSplit", font=font, fill=text_rgb + (255,), anchor="lm")
    return img


for scale, suffix in ((1, ""), (2, "@2x")):
    icon(256 * scale).save(out / f"icon{suffix}.png", optimize=True)
    logo(256 * scale, (31, 41, 55)).save(out / f"logo{suffix}.png", optimize=True)
    logo(256 * scale, (243, 244, 246)).save(out / f"dark_logo{suffix}.png", optimize=True)
print(sorted(p.name for p in out.iterdir()))
