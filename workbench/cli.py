"""Command line entry point for the coding workbench."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbench.pipeline import run


def main() -> None:
    ap = argparse.ArgumentParser(description="Screen-content coding workbench")
    ap.add_argument("--work-dir", type=Path, default=Path("work"))
    ap.add_argument("--frames", type=int, default=24)
    ap.add_argument("--cq-level", type=int, default=34)
    ap.add_argument("--cpu-used", type=int, default=2)
    ap.add_argument("--eval-frame", type=int, default=0)
    ap.add_argument("--roi", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"),
                    default=None, help="boxed small-text region of interest")
    ap.add_argument("--roi-psnr-min", type=float, default=38.0,
                    help="minimum acceptable ROI PSNR in dB (error cap)")
    ap.add_argument("--stroke-gap-max", type=float, default=0.08,
                    help="maximum acceptable fraction of broken stroke pixels")
    ap.add_argument("--bit-budget", type=int, default=None,
                    help="total bitstream byte budget (default: generic 4:2:0)")
    args = ap.parse_args()

    summary = run(
        args.work_dir, n_frames=args.frames, cq_level=args.cq_level,
        cpu_used=args.cpu_used, eval_frame=args.eval_frame,
        roi_box=tuple(args.roi) if args.roi else None,
        roi_psnr_min=args.roi_psnr_min,
        stroke_gap_max=args.stroke_gap_max,
        bit_budget_bytes=args.bit_budget)
    print(json.dumps({
        "histogram_match": summary["histogram_match_between_variants"],
        "cap": summary["cap"],
        "decisions": {
            v: {"chosen": d["chosen_tag"], "selection": d["selection"]}
            for v, d in summary["decisions"].items()},
        "sampling": summary["sampling_attribution"],
    }, indent=2))
    print("summary written to", args.work_dir / "summary.json")


if __name__ == "__main__":
    main()
