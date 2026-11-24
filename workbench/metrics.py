"""Reconstruction metrics: PSNR, stroke-gap loss, per-glyph recovery."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .scene import FONT_MONO

# A pixel differing by more than this (Euclidean RGB distance) counts as
# damaged when measuring glyph strokes.
DAMAGE_DISTANCE = 42.0


def psnr_rgb(a: np.ndarray, b: np.ndarray) -> float:
    diff = a.astype(np.float64) - b.astype(np.float64)
    mse = float((diff ** 2).mean())
    if mse == 0:
        return 99.0
    return 20.0 * math.log10(255.0 / math.sqrt(mse))


def region_crop(frame: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = box
    return frame[y0:y1, x0:x1]


@dataclass
class Glyph:
    char: str
    box: tuple[int, int, int, int]   # full-frame pixel box
    mask: np.ndarray                  # stroke mask inside the box


def render_glyphs(text: str, origin: tuple[int, int],
                  font_size: int = 12) -> list[Glyph]:
    """Rasterize each character separately and return its stroke mask."""
    font = ImageFont.truetype(FONT_MONO, font_size)
    x, y = int(origin[0]), int(origin[1])
    glyphs = []
    for ch in text:
        if ch == " ":
            x += int(font.getlength(ch))
            continue
        bbox = font.getbbox(ch)
        gw = max(1, int(font.getlength(ch)))
        gh = font_size + 4
        tile = Image.new("L", (gw, gh), 0)
        d = ImageDraw.Draw(tile)
        d.text((0, -bbox[1]), ch, font=font, fill=255)
        mask = np.asarray(tile) > 90
        box = (x, y, x + gw, y + gh)
        glyphs.append(Glyph(ch, box, mask))
        x += gw
    return glyphs


def _stroke_pixels(frame: np.ndarray, glyph: Glyph) -> np.ndarray:
    x0, y0, x1, y1 = glyph.box
    crop = frame[max(0, y0):y1, max(0, x0):x1]
    h = min(crop.shape[0], glyph.mask.shape[0])
    w = min(crop.shape[1], glyph.mask.shape[1])
    return crop[:h, :w][glyph.mask[:h, :w]]


def glyph_report(orig: np.ndarray, recon: np.ndarray,
                 glyphs: list[Glyph]) -> list[dict]:
    """Per-glyph stroke retention and gap ratio."""
    out = []
    for g in glyphs:
        src = _stroke_pixels(orig, g)
        rec = _stroke_pixels(recon, g)
        if src.size == 0:
            continue
        dist = np.sqrt(((src.astype(float) - rec.astype(float)) ** 2).sum(1))
        damaged = dist > DAMAGE_DISTANCE
        gap_ratio = float(damaged.mean())
        # Retained stroke pixels keep a color close to the original.
        out.append({
            "char": g.char,
            "box": list(g.box),
            "stroke_pixels": int(src.shape[0]),
            "gap_pixels": int(damaged.sum()),
            "gap_ratio": gap_ratio,
            "mean_distance": float(dist.mean()),
            "recovered": gap_ratio <= 0.15,
        })
    return out


def evaluate_pair(orig: np.ndarray, recon: np.ndarray,
                  roi_box: tuple[int, int, int, int],
                  glyphs: list[Glyph] | None = None,
                  glyph_text: str | None = None,
                  glyph_origin: tuple[int, int] | None = None,
                  font_size: int = 12) -> dict:
    full = psnr_rgb(orig, recon)
    roi_o = region_crop(orig, roi_box)
    roi_r = region_crop(recon, roi_box)
    roi = psnr_rgb(roi_o, roi_r)
    if glyphs is None and glyph_text is not None:
        glyphs = render_glyphs(glyph_text, glyph_origin or roi_box[:2], font_size)
    glyphs = glyphs or []
    reports = glyph_report(orig, recon, glyphs)
    total = sum(r["stroke_pixels"] for r in reports)
    gaps = sum(r["gap_pixels"] for r in reports)
    failed = [r for r in reports if not r["recovered"]]
    return {
        "psnr_full": full,
        "psnr_roi": roi,
        "stroke_gap_ratio": gaps / total if total else 0.0,
        "failed_glyphs": [
            {"char": r["char"], "box": r["box"], "gap_ratio": r["gap_ratio"]}
            for r in failed
        ],
        "glyph_reports": reports,
    }


def block_kind_counts(blocks: list[dict], frame: int,
                      box: tuple[int, int, int, int] | None = None) -> dict:
    """Count block area (in pixels) per mode kind, optionally inside a box."""
    counts: dict[str, int] = {}
    for b in blocks:
        if b["frame"] != frame:
            continue
        if box is not None:
            bx0, by0, bx1, by1 = box
            if not (b["px_col"] < bx1 and b["px_col"] + b["bw"] > bx0 and
                    b["px_row"] < by1 and b["px_row"] + b["bh"] > by0):
                continue
        area = b["bw"] * b["bh"]
        counts[b["kind"]] = counts.get(b["kind"], 0) + area
    return counts
