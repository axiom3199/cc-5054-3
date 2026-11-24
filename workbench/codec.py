"""AV1 encoding runs for the screen-content workbench.

Two codec configurations are offered for every (pixel-format, variant) pair:

  generic: screen-content tools disabled (no palette, no intra block copy)
  scc:     screen-content tools enabled (libaom defaults, explicit)

Each run produces:
  * an IVF bitstream and a raw YUV reconstruction,
  * a per-frame aomenc status log (PSNR, bytes/frame),
  * a block-level mode log from the instrumented encoder
    (AOM_BLOCK_LOG; INTRA / INTER / PALETTE / IBC with geometry),
  * an OBU dump as independent bitstream evidence.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
AOMENC = ROOT / "tools" / "aom-b3" / "aomenc"
AOMDEC = ROOT / "tools" / "aom-b3" / "aomdec"
DUMP_OBU = ROOT / "tools" / "aom-b3" / "tools" / "dump_obu"

PIXEL_FORMATS = ("yuv420p", "yuv444p")
CONFIGS = ("generic", "scc")


@dataclass
class EncodeRequest:
    scene_dir: Path
    variant: str
    pix_fmt: str          # yuv420p | yuv444p
    config: str           # generic | scc
    cq_level: int = 34
    cpu_used: int = 2
    n_frames: int = 24
    fps: int = 10


# --------------------------------------------------------------------------
# RGB <-> YUV (BT.601 limited range, matching ffmpeg's default swscale matrix)
# --------------------------------------------------------------------------

def _rgb_to_yuv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = rgb.astype(np.float64)
    r, g, b = x[..., 2], x[..., 1], x[..., 0]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    u = -0.168736 * r - 0.331264 * g + 0.5 * b + 128.0
    v = 0.5 * r - 0.418688 * g - 0.081312 * b + 128.0
    # limited range
    y = y * 219.0 / 255.0 + 16.0
    u = (u - 128.0) * 224.0 / 255.0 + 128.0
    v = (v - 128.0) * 224.0 / 255.0 + 128.0
    return (np.clip(y, 0, 255).round().astype(np.uint8),
            np.clip(u, 0, 255).round().astype(np.uint8),
            np.clip(v, 0, 255).round().astype(np.uint8))


def _yuv_to_rgb(y: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    yf = (y.astype(np.float64) - 16.0) * 255.0 / 219.0
    uf = (u.astype(np.float64) - 128.0) * 255.0 / 224.0
    vf = (v.astype(np.float64) - 128.0) * 255.0 / 224.0
    r = yf + 1.402 * vf
    g = yf - 0.344136 * uf - 0.714136 * vf
    b = yf + 1.772 * uf
    out = np.stack([b, g, r], axis=-1)
    return np.clip(out, 0, 255).round().astype(np.uint8)


def _subsample_h(plane: np.ndarray) -> np.ndarray:
    p = plane[:, 0:plane.shape[1] - plane.shape[1] % 2]
    return p.reshape(p.shape[0], p.shape[1] // 2, 2).mean(2).round().astype(np.uint8)


def _subsample_v(plane: np.ndarray) -> np.ndarray:
    p = plane[0:plane.shape[0] - plane.shape[0] % 2, :]
    return p.reshape(p.shape[0] // 2, 2, p.shape[1]).mean(1).round().astype(np.uint8)


def write_yuv(frames: list[np.ndarray], path: Path, pix_fmt: str) -> None:
    with open(path, "wb") as f:
        for rgb in frames:
            y, u, v = _rgb_to_yuv(rgb)
            if pix_fmt == "yuv420p":
                u = _subsample_v(_subsample_h(u))
                v = _subsample_v(_subsample_h(v))
            f.write(y.tobytes())
            f.write(u.tobytes())
            f.write(v.tobytes())


def read_yuv(path: Path, width: int, height: int,
             pix_fmt: str) -> list[np.ndarray]:
    if pix_fmt == "yuv420p":
        cw, ch = width // 2, height // 2
        frame_size = width * height + 2 * cw * ch
    else:
        cw, ch = width, height
        frame_size = 3 * width * height
    raw = path.read_bytes()
    frames = []
    for off in range(0, len(raw), frame_size):
        chunk = raw[off:off + frame_size]
        if len(chunk) < frame_size:
            break
        y = np.frombuffer(chunk[:width * height], np.uint8).reshape(height, width)
        u = np.frombuffer(chunk[width * height:width * height + cw * ch],
                          np.uint8).reshape(ch, cw)
        v = np.frombuffer(chunk[width * height + cw * ch:],
                          np.uint8).reshape(ch, cw)
        if pix_fmt == "yuv420p":
            u = u.repeat(2, 0).repeat(2, 1)[:height, :width]
            v = v.repeat(2, 0).repeat(2, 1)[:height, :width]
        frames.append(_yuv_to_rgb(y, u, v))
    return frames


# --------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------

def _run(cmd: list[str], env: dict | None = None,
         capture: str = "stderr") -> str:
    e = dict(os.environ)
    if env:
        e.update(env)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=e)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"{proc.stderr[-2000:]}")
    return proc.stderr if capture == "stderr" else proc.stdout


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mK]")
# Pass-2 lines look like:
#   Pass 2/2 frame    1/0  0B  ... 53.684 54.464 52.829 52.102  29987F
# The number after "/" is the actual frame index; trailing "xF" is frame bits.
_STATUS_RE = re.compile(
    r"Pass 2/2 frame\s+\d+/(\d+)\s+(\d+)B\s+.*?"
    r"([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+)\s+\d+F")


def _parse_status(log_text: str) -> list[dict]:
    """Parse aomenc pass-2 per-frame status lines.

    Reported bytes are cumulative; per-frame bytes are derived as deltas.
    PSNR order on the line is Overall, Y, U, V.
    """
    clean = _ANSI_RE.sub("", log_text)
    rows = []
    prev_bytes = 0
    for m in _STATUS_RE.finditer(clean):
        frame, nbytes, psnr_all, psnr_y, psnr_u, psnr_v = m.groups()
        total = int(nbytes)
        rows.append({
            "frame": int(frame),
            "bytes": total - prev_bytes,
            "cumulative_bytes": total,
            "psnr_all": float(psnr_all),
            "psnr_y": float(psnr_y),
            "psnr_u": float(psnr_u),
            "psnr_v": float(psnr_v),
        })
        prev_bytes = total
    return rows


def _parse_block_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path) as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 13:
                continue
            rows.append({
                "frame": int(parts[0]),
                "mi_row": int(parts[1]), "mi_col": int(parts[2]),
                "px_row": int(parts[3]), "px_col": int(parts[4]),
                "bw": int(parts[5]), "bh": int(parts[6]),
                "kind": parts[7], "mode": int(parts[8]),
                "intrabc": int(parts[9]),
                "pal_y": int(parts[10]), "pal_uv": int(parts[11]),
                "skip": int(parts[12]),
            })
    return rows


def _parse_obu(path: Path) -> list[dict]:
    if not path.exists():
        return []
    units: list[dict] = []
    current: dict | None = None
    for line in path.read_text().splitlines():
        s = line.strip()
        if s.startswith("Temporal unit"):
            current = {"temporal_unit": len(units), "obus": [], "size": None}
            units.append(current)
        elif s.startswith("OBU type:") and current is not None:
            current["obus"].append({"type": s.split(":", 1)[1].strip()})
        elif s.startswith("length:") and current is not None and current["obus"]:
            current["obus"][-1]["length"] = int(s.split()[1])
        elif s.startswith("TU size:") and current is not None:
            current["size"] = int(s.split()[2])
    return units


def encode(req: EncodeRequest, work_root: Path) -> dict:
    tag = f"{req.variant}_{req.pix_fmt}_{req.config}"
    run_dir = work_root / "runs" / tag
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    frames = [np.asarray(Image.open(p)) for p in
              sorted((req.scene_dir / "frames").glob("f*.png"))[:req.n_frames]]
    h, w = frames[0].shape[:2]
    yuv_in = run_dir / "input.yuv"
    write_yuv(frames, yuv_in, req.pix_fmt)

    ivf = run_dir / "out.ivf"
    yuv_out = run_dir / "recon.yuv"
    status_log = run_dir / "aomenc.log"
    block_log = run_dir / "blocks.log"
    obu_log = run_dir / "obu.txt"

    fmt_flag = "--i444" if req.pix_fmt == "yuv444p" else "--i420"
    cmd = [
        str(AOMENC), "--ivf", fmt_flag, "-w", str(w), "-h", str(h),
        f"--fps={req.fps}/1", f"--cpu-used={req.cpu_used}",
        "--end-usage=q", f"--cq-level={req.cq_level}",
        "--psnr", "--lag-in-frames=0", "--kf-min-dist=999",
        "--kf-max-dist=999",
        "-o", str(ivf), str(yuv_in),
    ]
    if req.config == "generic":
        cmd += ["--enable-palette=0", "--enable-intrabc=0"]
    else:
        cmd += ["--enable-palette=1", "--enable-intrabc=1"]

    text = _run(cmd, env={"AOM_BLOCK_LOG": str(block_log)})
    status_log.write_text(text)
    # --rawvideo keeps the stream's native chroma sampling (444 stays 444).
    _run([str(AOMDEC), "--rawvideo", "-o", str(yuv_out), str(ivf)])
    obu_log.write_text(_run([str(DUMP_OBU), str(ivf)], capture="stdout"))

    status = _parse_status(text)
    obus = _parse_obu(obu_log)
    blocks = _parse_block_log(block_log)
    # Authoritative per-frame byte counts come from the bitstream itself:
    # temporal-unit sizes parsed by dump_obu. aomenc's status line reports
    # cumulative bytes and lags by one frame, so it is used only for PSNR.
    tu_sizes = [u["size"] for u in obus if u.get("size") is not None]
    for row in status:
        if 0 <= row["frame"] < len(tu_sizes):
            row["bytes"] = tu_sizes[row["frame"]]
    result = {
        "tag": tag,
        "request": {
            "variant": req.variant, "pix_fmt": req.pix_fmt,
            "config": req.config, "cq_level": req.cq_level,
            "cpu_used": req.cpu_used, "n_frames": req.n_frames,
        },
        "bitstream_bytes": ivf.stat().st_size,
        "bits_per_pixel": ivf.stat().st_size * 8 / (w * h * len(frames)),
        "frame_status": status,
        "blocks": blocks,
        "obu_units": obus,
        "paths": {
            "ivf": str(ivf.relative_to(work_root)),
            "recon_yuv": str(yuv_out.relative_to(work_root)),
            "block_log": str(block_log.relative_to(work_root)),
            "obu_log": str(obu_log.relative_to(work_root)),
            "aomenc_log": str(status_log.relative_to(work_root)),
        },
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2))
    return result


def load_recon(result: dict, work_root: Path,
               width: int, height: int) -> list[np.ndarray]:
    pix_fmt = result["request"]["pix_fmt"]
    return read_yuv(work_root / result["paths"]["recon_yuv"],
                    width, height, pix_fmt)
