"""Visual evidence rendering: block-mode overlays, ROI zoom, failed glyphs."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .metrics import DAMAGE_DISTANCE, render_glyphs

KIND_COLORS = {
    "INTRA": (90, 140, 230),
    "INTER": (120, 200, 120),
    "PALETTE": (245, 200, 70),
    "IBC": (235, 110, 200),
}


def draw_block_overlay(frame: np.ndarray, blocks: list[dict], frame_idx: int,
                       box: tuple[int, int, int, int] | None = None,
                       alpha: int = 110) -> Image.Image:
    """Overlay verified block boundaries and modes on a reconstructed frame.

    When ``box`` is given the output is cropped to that region so block modes
    can be read at full resolution inside the user-selected ROI.
    """
    base = Image.fromarray(frame).convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for b in blocks:
        if b["frame"] != frame_idx:
            continue
        x0, y0 = b["px_col"], b["px_row"]
        x1, y1 = x0 + b["bw"], y0 + b["bh"]
        if box is not None:
            bx0, by0, bx1, by1 = box
            if x1 <= bx0 or x0 >= bx1 or y1 <= by0 or y0 >= by1:
                continue
        color = KIND_COLORS.get(b["kind"], (200, 200, 200))
        d.rectangle((x0, y0, x1 - 1, y1 - 1), fill=color + (alpha,),
                    outline=(255, 255, 255, 170))
        if box is not None and b["bw"] >= 16 and b["bh"] >= 8:
            d.text((x0 + 1, y0), b["kind"][:3], fill=(255, 255, 255, 255))
        elif b["bw"] >= 24 and b["bh"] >= 12:
            d.text((x0 + 2, y0 + 1), b["kind"][:3],
                   fill=(20, 20, 20, 255))
    merged = Image.alpha_composite(base, overlay).convert("RGB")
    if box is not None:
        merged = merged.crop(box)
    return merged


def zoom_region(frame: np.ndarray, box: tuple[int, int, int, int],
                scale: int = 6, grid: bool = False) -> Image.Image:
    x0, y0, x1, y1 = box
    crop = Image.fromarray(frame[y0:y1, x0:x1])
    zoom = crop.resize((crop.width * scale, crop.height * scale), Image.NEAREST)
    if grid:
        d = ImageDraw.Draw(zoom)
        for x in range(0, zoom.width, scale):
            d.line((x, 0, x, zoom.height), fill=(70, 70, 70))
        for y in range(0, zoom.height, scale):
            d.line((0, y, zoom.width, y), fill=(70, 70, 70))
    return zoom


def damage_map(orig: np.ndarray, recon: np.ndarray,
               box: tuple[int, int, int, int]) -> Image.Image:
    """Red channel marks pixel damage above the glyph damage threshold."""
    x0, y0, x1, y1 = box
    a = orig[y0:y1, x0:x1].astype(np.int16)
    b = recon[y0:y1, x0:x1].astype(np.int16)
    dist = np.sqrt(((a - b) ** 2).sum(-1))
    out = np.full((*dist.shape, 3), 24, np.uint8)
    out[dist > DAMAGE_DISTANCE] = (230, 70, 70)
    return Image.fromarray(out)


def failed_glyph_panel(orig: np.ndarray, recon: np.ndarray,
                       glyph_text: str, origin: tuple[int, int],
                       font_size: int = 12, scale: int = 4) -> Image.Image:
    """Side-by-side per-glyph zoom; failed glyphs get a red marker."""
    glyphs = render_glyphs(glyph_text, origin, font_size)
    tiles = []
    for g in glyphs:
        x0, y0, x1, y1 = g.box
        so = Image.fromarray(orig[y0:y1, x0:x1]).resize(
            ((x1 - x0) * scale, (y1 - y0) * scale), Image.NEAREST)
        sr = Image.fromarray(recon[y0:y1, x0:x1]).resize(
            ((x1 - x0) * scale, (y1 - y0) * scale), Image.NEAREST)
        pair = Image.new("RGB", (so.width, so.height * 2 + 6), (40, 40, 40))
        pair.paste(so, (0, 0))
        pair.paste(sr, (0, so.height + 6))
        src = orig[y0:y1, x0:x1]
        rec = recon[y0:y1, x0:x1]
        h = min(src.shape[0], g.mask.shape[0])
        w = min(src.shape[1], g.mask.shape[1])
        m = g.mask[:h, :w]
        if m.any():
            d = np.sqrt(((src[:h, :w][m].astype(float) -
                          rec[:h, :w][m].astype(float)) ** 2).sum(1))
            failed = (d > DAMAGE_DISTANCE).mean() > 0.15
        else:
            failed = False
        if failed:
            dd = ImageDraw.Draw(pair)
            dd.rectangle((0, 0, pair.width - 1, pair.height - 1),
                         outline=(235, 70, 70), width=3)
        tiles.append(pair)
    if not tiles:
        return Image.new("RGB", (10, 10))
    gap = 4
    total_w = sum(t.width for t in tiles) + gap * (len(tiles) - 1)
    panel = Image.new("RGB", (total_w, tiles[0].height), (20, 20, 20))
    x = 0
    for t in tiles:
        panel.paste(t, (x, 0))
        x += t.width + gap
    return panel


def save_all(run_dir: Path, work_root: Path, frame_idx: int,
             orig: np.ndarray, recon: np.ndarray,
             blocks: list[dict], roi_box: tuple[int, int, int, int],
             glyph_text: str, glyph_origin: tuple[int, int],
             font_size: int = 12) -> dict:
    vis = run_dir / "vis"
    vis.mkdir(exist_ok=True)
    paths = {}
    overlay = draw_block_overlay(recon, blocks, frame_idx)
    p = vis / f"blocks_f{frame_idx:03d}.png"
    overlay.save(p)
    paths["block_overlay"] = str(p.relative_to(work_root))
    zoom = zoom_region(recon, roi_box, scale=6, grid=True)
    p = vis / f"roi_zoom_f{frame_idx:03d}.png"
    zoom.save(p)
    paths["roi_zoom"] = str(p.relative_to(work_root))
    dmg = damage_map(orig, recon, roi_box)
    p = vis / f"damage_f{frame_idx:03d}.png"
    dmg.save(p)
    paths["damage_map"] = str(p.relative_to(work_root))
    panel = failed_glyph_panel(orig, recon, glyph_text, glyph_origin, font_size)
    p = vis / f"glyphs_f{frame_idx:03d}.png"
    panel.save(p)
    paths["glyph_panel"] = str(p.relative_to(work_root))
    # Overlay restricted to the ROI for a close view of modes there.
    roi_overlay = draw_block_overlay(recon, blocks, frame_idx, box=roi_box)
    p = vis / f"blocks_roi_f{frame_idx:03d}.png"
    roi_overlay.save(p)
    paths["block_overlay_roi"] = str(p.relative_to(work_root))
    return paths
