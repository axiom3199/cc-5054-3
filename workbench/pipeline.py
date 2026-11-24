"""End-to-end workbench pipeline.

Runs both inset variants (text / video), both chroma samplings (4:2:0 / 4:4:4)
and both codec configurations (generic / scc), then evaluates every candidate
against the user's ROI box, error cap and bit budget.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from . import codec, scene as scene_mod, selector, visualize
from .metrics import block_kind_counts, evaluate_pair, render_glyphs
from .report import build_report

ROI_TEXT = "File  Edit  View  Codec  SCC  Help"
ROI_FONT_SIZE = 12


def _load_frames(scene_dir: Path) -> list[np.ndarray]:
    return [np.asarray(Image.open(p))
            for p in sorted((scene_dir / "frames").glob("f*.png"))]


def run(work_root: Path, n_frames: int = 24, cq_level: int = 34,
        cpu_used: int = 2, eval_frame: int = 0,
        roi_box: tuple[int, int, int, int] | None = None,
        roi_psnr_min: float = 38.0, stroke_gap_max: float = 0.08,
        bit_budget_bytes: int | None = None) -> dict:
    work_root = Path(work_root)
    work_root.mkdir(parents=True, exist_ok=True)

    # 1. Generate both color-matched scenes.
    scenes = {}
    for variant in ("text", "video"):
        sd = work_root / f"scene_{variant}"
        scenes[variant] = scene_mod.generate(sd, variant, n_frames)
    meta = scenes["text"]
    if roi_box is None:
        roi_box = tuple(meta["regions"]["roi_default"])

    # Verify the two variants share per-frame color histograms.
    hist_match = scenes["text"]["histograms"] == scenes["video"]["histograms"]

    # 2. Encode every (variant, pix_fmt, config) combination.
    candidates = []
    recon_cache: dict[str, list[np.ndarray]] = {}
    for variant in ("text", "video"):
        sd = work_root / f"scene_{variant}"
        originals = _load_frames(sd)
        for pix_fmt in codec.PIXEL_FORMATS:
            for config in codec.CONFIGS:
                req = codec.EncodeRequest(
                    scene_dir=sd, variant=variant, pix_fmt=pix_fmt,
                    config=config, cq_level=cq_level, cpu_used=cpu_used,
                    n_frames=n_frames, fps=meta["fps"])
                result = codec.encode(req, work_root)
                recon = codec.load_recon(result, work_root,
                                         meta["width"], meta["height"])
                recon_cache[result["tag"]] = recon

                f = min(eval_frame, n_frames - 1)
                glyph_origin = (roi_box[0], roi_box[1])
                glyphs = render_glyphs(ROI_TEXT, glyph_origin, ROI_FONT_SIZE)
                metrics = evaluate_pair(
                    originals[f], recon[f], roi_box, glyphs=glyphs)
                # Frame-level bitstream evidence for the evaluated frame.
                frame_status = next(
                    (r for r in result["frame_status"]
                     if r["frame"] == f), {})
                mode_counts = block_kind_counts(result["blocks"], f, roi_box)
                candidate = {
                    "tag": result["tag"],
                    "variant": variant,
                    "pix_fmt": pix_fmt,
                    "config": config,
                    "bitstream_bytes": result["bitstream_bytes"],
                    "bits_per_pixel": result["bits_per_pixel"],
                    "frame_status": frame_status,
                    "roi_mode_area_px": mode_counts,
                    "metrics": metrics,
                    "result_path": f"runs/{result['tag']}/result.json",
                }
                candidates.append(candidate)

    # 3. Same-budget comparison: derive the budget from the generic 4:2:0 run.
    anchor = next(c for c in candidates
                  if c["pix_fmt"] == "yuv420p" and c["config"] == "generic"
                  and c["variant"] == "text")
    if bit_budget_bytes is None:
        bit_budget_bytes = anchor["bitstream_bytes"]
    cap = selector.Cap(bit_budget_bytes, roi_psnr_min, stroke_gap_max)

    decisions = {}
    for variant in ("text", "video"):
        pool = [c for c in candidates if c["variant"] == variant]
        chosen = selector.pick(
            [{k: v for k, v in c.items()} for c in pool], cap)
        for c in pool:
            c.setdefault("feasible", False)
        judged = {c["tag"]: selector.judge(c, cap) for c in pool}
        decisions[variant] = {
            "chosen_tag": chosen["tag"],
            "selection": chosen["selection"],
            "candidates": [
                {
                    "tag": j["tag"], "pix_fmt": j["pix_fmt"],
                    "config": j["config"],
                    "bitstream_bytes": j["bitstream_bytes"],
                    "psnr_full": j["metrics"]["psnr_full"],
                    "psnr_roi": j["metrics"]["psnr_roi"],
                    "stroke_gap_ratio": j["metrics"]["stroke_gap_ratio"],
                    "feasible": j["feasible"],
                    "reject_reasons": j["reject_reasons"],
                }
                for j in judged.values()
            ],
        }

    # 4. Chroma-sampling attribution per (variant, config).
    sampling = []
    for variant in ("text", "video"):
        for config in codec.CONFIGS:
            c420 = next(c for c in candidates if c["variant"] == variant
                        and c["pix_fmt"] == "yuv420p" and c["config"] == config)
            c444 = next(c for c in candidates if c["variant"] == variant
                        and c["pix_fmt"] == "yuv444p" and c["config"] == config)
            sampling.append({
                "variant": variant, "config": config,
                **selector.compare_sampling(c420, c444),
            })

    # 5. Visual evidence for every candidate at the eval frame.
    f = min(eval_frame, n_frames - 1)
    glyph_origin = (roi_box[0], roi_box[1])
    for variant in ("text", "video"):
        sd = work_root / f"scene_{variant}"
        originals = _load_frames(sd)
        for cand in [c for c in candidates if c["variant"] == variant]:
            result = json.loads(
                (work_root / cand["result_path"]).read_text())
            vis_paths = visualize.save_all(
                work_root / "runs" / cand["tag"], work_root, f, originals[f],
                recon_cache[cand["tag"]][f], result["blocks"], roi_box,
                ROI_TEXT, glyph_origin, ROI_FONT_SIZE)
            cand["visuals"] = vis_paths

    summary = {
        "work_root": str(work_root),
        "width": meta["width"],
        "height": meta["height"],
        "n_frames": n_frames,
        "eval_frame": f,
        "roi_box": list(roi_box),
        "cap": {"bit_budget_bytes": cap.bit_budget_bytes,
                "roi_psnr_min": cap.roi_psnr_min,
                "stroke_gap_max": cap.stroke_gap_max},
        "histogram_match_between_variants": hist_match,
        "decisions": decisions,
        "sampling_attribution": sampling,
        "candidates": [
            {k: v for k, v in c.items() if k not in ("metrics",)}
            | {"metrics": {kk: vv for kk, vv in c["metrics"].items()
                           if kk != "glyph_reports"}}
            for c in candidates
        ],
    }
    (work_root / "summary.json").write_text(json.dumps(summary, indent=2))
    summary["report_path"] = str(build_report(work_root))
    (work_root / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary
