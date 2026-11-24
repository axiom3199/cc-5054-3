"""Synthetic desktop-frame generator for the screen-content coding workbench.

A frame contains:
  * a menu bar with small colored text,
  * a scrolling code panel (thin strokes, limited palette),
  * an inset window that exists in two color-matched variants:
      - "text":  small colored glyphs (fine strokes only),
      - "video": moving blocks with the same palette/histogram,
  * a small playing video window.

Both inset variants draw from the same palette and the same per-frame color
counts, so full-frame scores stay close while local glyph damage differs.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 640, 480
FPS = 10
N_FRAMES = 24

FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

# Limited, deliberately saturated screen-content palette (RGB).
PALETTE = [
    (24, 26, 31),      # 0 panel background
    (214, 220, 230),   # 1 main text
    (120, 200, 255),   # 2 cyan
    (255, 170, 90),    # 3 orange
    (180, 255, 140),   # 4 green
    (255, 120, 200),   # 5 magenta
    (200, 160, 255),   # 6 violet
    (255, 240, 150),   # 7 yellow
    (48, 52, 62),      # 8 window chrome
    (240, 240, 245),   # 9 title text
]
BG = PALETTE[0]
CHROME = PALETTE[8]

# Fixed geometry (also written into scene metadata for the UI).
MENU = (0, 0, WIDTH, 26)
CODE_PANEL = (12, 38, 372, 330)
INSET = (392, 38, 624, 302)       # color-matched pair (232x264, /8 clean)
VIDEO_WIN = (392, 312, 628, 468)
# The line the user is expected to box-select (small colored menu text).
ROI_DEFAULT = (16, 5, 250, 21)

CODE_LINES = [
    ("def rd_pick(cands, budget, err_cap):", 1),
    ("    for c in sorted(cands, key=lambda c: c.bits):", 1),
    ("        if c.bits <= budget and c.roi_psnr >= err_cap:", 4),
    ("            return c  # first feasible under the cap", 2),
    ("    return min(cands, key=lambda c: c.roi_gap)", 6),
    ("", 1),
    ("class Block:  # verified against the bitstream log", 1),
    ("    kind: str  # INTRA | INTER | PALETTE | IBC", 5),
    ("    bits: int", 7),
    ("    def reject(self, why: str) -> None:", 1),
    ("        self.rejected = why", 3),
    ("", 1),
    ("for f in range(frames):", 1),
    ("    encode(f, scc=enable_palette or enable_intrabc)", 2),
    ("    audit(frames[f], recon[f], blocks[f])", 6),
]
MENU_ITEMS = ["File", "Edit", "View", "Codec", "SCC", "Help"]
GLYPH_WORDS = ["def", "var", "roi", "bit", "psnr", "ibc", "scc", "q420", "q444"]


@dataclass
class SceneConfig:
    variant: str          # "text" or "video"
    n_frames: int = N_FRAMES
    seed: int = 7

    def to_json(self) -> dict:
        return {"variant": self.variant, "n_frames": self.n_frames,
                "seed": self.seed}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_MONO_BOLD if bold else FONT_MONO
    return ImageFont.truetype(path, size)


def _rng(cfg: SceneConfig) -> np.random.Generator:
    return np.random.default_rng(cfg.seed)


def _draw_menu(img: Image.Image, rng: np.random.Generator) -> None:
    d = ImageDraw.Draw(img)
    d.rectangle(MENU, fill=CHROME)
    x = 14
    f = _font(12)
    for i, item in enumerate(MENU_ITEMS):
        color = PALETTE[2 + ((i + rng.integers(0, 3)) % 6)]
        d.text((x, 6), item, font=f, fill=color)
        x += d.textlength(item, font=f) + 22
    d.text((WIDTH - 96, 6), "SCC bench", font=_font(12, True), fill=PALETTE[9])


def _draw_code(img: Image.Image, scroll: int, rng: np.random.Generator) -> None:
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = CODE_PANEL
    d.rectangle(CODE_PANEL, fill=(16, 18, 22), outline=(60, 64, 74))
    f = _font(13)
    lh = 17
    # Reserve the bottom 56px for the IBC strip and status line so scrolling
    # code never overlaps the repeated-tile band.
    text_bottom = y1 - 56
    top = y0 + 8 - scroll
    # Clip code text to the panel text area so long lines never overflow.
    layer = Image.new("RGB", (x1 - x0, text_bottom - y0), (16, 18, 22))
    ld = ImageDraw.Draw(layer)
    for i, (text, color_idx) in enumerate(CODE_LINES):
        y = top + i * lh
        if y + lh < y0 or y > text_bottom:
            continue
        ld.text((10, y - y0), text, font=f, fill=PALETTE[color_idx])
    img.paste(layer, (x0, y0))
    d = ImageDraw.Draw(img)
    # status line, fixed position
    d.rectangle((x0, y1 - 20, x1, y1), fill=(28, 30, 36))
    d.text((x0 + 8, y1 - 17), "utf-8  AV1  line %d" % (scroll // lh + 1),
           font=_font(11), fill=PALETTE[4])
    # Repeated horizontal glyph strip: identical 32px tiles give intra block
    # copy (IBC) a usable reference on intra-only frames.
    band_y0, band_y1 = y1 - 52, y1 - 24
    d.rectangle((x0 + 4, band_y0, x1 - 4, band_y1), fill=(12, 14, 18))
    tile_w, tile_h = 32, band_y1 - band_y0
    tile = Image.new("RGB", (tile_w, tile_h), (12, 14, 18))
    td = ImageDraw.Draw(tile)
    for i, c in enumerate((PALETTE[3], PALETTE[5], PALETTE[6])):
        td.rectangle((4 + i * 9, 6, 9 + i * 9, tile_h - 6), fill=c)
        td.text((2 + i * 9, tile_h - 12), "ab", font=_font(9), fill=PALETTE[1])
    band_w = x1 - x0 - 8
    for tx in range(0, band_w, tile_w):
        img.paste(tile, (x0 + 4 + tx, band_y0))


def _inset_text_frame(size: tuple[int, int], t: int,
                      rng: np.random.Generator) -> np.ndarray:
    """Fine-stroke content: small colored glyphs on the panel background."""
    w, h = size
    arr = np.full((h, w, 3), PALETTE[0], np.uint8)
    img = Image.fromarray(arr)
    d = ImageDraw.Draw(img)
    f = _font(11)
    lh = 15
    shift = (t * 6) % lh
    for row in range(-1, h // lh + 1):
        y = row * lh - shift
        word = GLYPH_WORDS[(row + t) % len(GLYPH_WORDS)]
        color = PALETTE[2 + ((row * 2 + t) % 6)]
        for col in range(w // 52):
            x = 6 + col * 52 + ((t * 3) % 18)
            d.text((x, y), word, font=f, fill=color)
    return np.asarray(img)


def _palette_index_map(rgb: np.ndarray) -> np.ndarray:
    """Nearest-palette index for every pixel (same rule as color_histogram).

    Implemented as a strict-minimum scan so equal-distance ties always resolve
    to the lower palette index, independent of array layout.
    """
    pal = np.array(PALETTE, np.uint8)
    dist = ((rgb.astype(int)[:, :, None, :] - pal[None, None]) ** 2).sum(3)
    idx = np.zeros(dist.shape[:2], np.uint8)
    best = dist[..., 0].copy()
    for p in range(1, len(PALETTE)):
        closer = dist[..., p] < best
        idx[closer] = p
        best[closer] = dist[..., p][closer]
    return idx


def _inset_video_frame(size: tuple[int, int], t: int,
                       rng: np.random.Generator) -> np.ndarray:
    """Moving-block content reusing the text variant's exact palette indices.

    The text frame is mapped to palette indices, then those indices are
    permuted into moving 8x8 blocks. No glyph geometry survives while the
    nearest-palette histogram is identical frame for frame.
    """
    w, h = size
    text = _inset_text_frame(size, t, rng)
    idx = _palette_index_map(text)
    # A single frame-wide circular shift is a strict pixel permutation, so the
    # palette-index multiset is preserved exactly while glyph geometry breaks.
    # Rows use different shifts so the output reads as moving blocks rather
    # than a translated image.
    row_shifts = (np.arange(h) * 7 + t * 13) % w
    cols = (np.arange(w)[None, :] - row_shifts[:, None]) % w
    out = idx[np.arange(h)[:, None], cols]
    return np.array(PALETTE, np.uint8)[out]


def _draw_video_window(img: Image.Image, t: int,
                       rng: np.random.Generator) -> None:
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = VIDEO_WIN
    d.rectangle(VIDEO_WIN, fill=CHROME)
    d.rectangle((x0, y0, x1, y0 + 16), fill=(62, 66, 78))
    d.text((x0 + 8, y0 + 2), "camera-720p.mp4", font=_font(10), fill=PALETTE[9])
    vw, vh = x1 - x0 - 8, y1 - y0 - 24
    vx, vy = x0 + 4, y0 + 20
    grad = np.zeros((vh, vw, 3), np.uint8)
    yy, xx = np.mgrid[0:vh, 0:vw]
    r = ((xx - (vw / 2 + 30 * math.sin(t * 0.6))) ** 2 +
         (yy - (vh / 2 + 20 * math.cos(t * 0.5))) ** 2) ** 0.5
    grad[..., 0] = np.clip(40 + r * 1.6, 0, 255)
    grad[..., 1] = np.clip(120 + 60 * np.sin(t * 0.4 + xx * 0.05), 0, 255)
    grad[..., 2] = np.clip(200 - r, 0, 255)
    img.paste(Image.fromarray(grad), (vx, vy))


def render_frame(cfg: SceneConfig, t: int) -> np.ndarray:
    rng = _rng(cfg)
    # Dedicated stream for inset content: both variants draw the same menu
    # and code panel before touching it, so those regions stay pixel-identical.
    inset_rng = np.random.default_rng(cfg.seed + 1000 + t)
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    _draw_menu(img, rng)
    _draw_code(img, scroll=t * 14, rng=rng)
    ix0, iy0, ix1, iy1 = INSET
    size = (ix1 - ix0 - 2, iy1 - iy0 - 2)  # 1px chrome margin on every side
    d0 = ImageDraw.Draw(img)
    d0.rectangle(INSET, fill=PALETTE[0], outline=(70, 74, 86))
    if cfg.variant == "text":
        inset = _inset_text_frame(size, t, inset_rng)
    else:
        inset = _inset_video_frame(size, t, inset_rng)
    img.paste(Image.fromarray(inset), (ix0 + 1, iy0 + 1))
    d = ImageDraw.Draw(img)
    d.rectangle(INSET, outline=(70, 74, 86))
    # Identical label in both variants so full-frame color histograms match.
    d.text((ix0 + 6, iy0 - 14), "inset:pair",
           font=_font(11), fill=PALETTE[7])
    _draw_video_window(img, t, rng)
    return np.asarray(img)


def color_histogram(frame: np.ndarray) -> dict[str, int]:
    idx = _palette_index_map(frame)
    counts = np.bincount(idx.ravel(), minlength=len(PALETTE))
    return {str(i): int(c) for i, c in enumerate(counts)}


def generate(out_dir: Path, variant: str, n_frames: int = N_FRAMES,
             seed: int = 7) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = SceneConfig(variant=variant, n_frames=n_frames, seed=seed)
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    hists = []
    for t in range(n_frames):
        frame = render_frame(cfg, t)
        Image.fromarray(frame).save(frames_dir / f"f{t:03d}.png")
        hists.append(color_histogram(frame))
    meta = {
        **cfg.to_json(),
        "width": WIDTH,
        "height": HEIGHT,
        "fps": FPS,
        "regions": {
            "menu": MENU,
            "code_panel": CODE_PANEL,
            "inset": INSET,
            "video_window": VIDEO_WIN,
            "roi_default": ROI_DEFAULT,
        },
        "palette": [list(c) for c in PALETTE],
        "histograms": hists,
    }
    (out_dir / "scene.json").write_text(json.dumps(meta, indent=2))
    return meta
