"""Verifiable checks for the workbench outputs.

Run after a full pipeline:  python3 -I verify.py work/
Asserts the evidence chain rather than trusting summary numbers.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/home/node/.local/lib/python3.11/site-packages")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from workbench.codec import PIXEL_FORMATS, CONFIGS  # noqa: E402


def check(work_root: Path) -> None:
    summary = json.loads((work_root / "summary.json").read_text())
    failures: list[str] = []

    # 1. The two inset variants share per-frame color histograms.
    if not summary["histogram_match_between_variants"]:
        failures.append("color histograms diverge between text/video variants")

    # 2. Every (variant, pix_fmt, config) run exists with raw evidence.
    for variant in ("text", "video"):
        for pix_fmt in PIXEL_FORMATS:
            for config in CONFIGS:
                tag = f"{variant}_{pix_fmt}_{config}"
                run = work_root / "runs" / tag
                for name in ("out.ivf", "recon.yuv", "blocks.log",
                             "obu.txt", "aomenc.log", "result.json"):
                    if not (run / name).exists():
                        failures.append(f"{tag}: missing {name}")

    # 3. SCC tools are verifiable: PALETTE/IBC only appear in scc runs.
    for variant in ("text", "video"):
        for pix_fmt in PIXEL_FORMATS:
            for config in CONFIGS:
                tag = f"{variant}_{pix_fmt}_{config}"
                kinds = Counter(
                    line.split()[7] for line in
                    (work_root / "runs" / tag / "blocks.log").read_text()
                    .splitlines() if not line.startswith("#"))
                if config == "generic" and (
                        "PALETTE" in kinds or "IBC" in kinds):
                    failures.append(
                        f"{tag}: generic run unexpectedly contains SCC modes")
                if config == "scc" and "PALETTE" not in kinds:
                    failures.append(f"{tag}: scc run shows no PALETTE blocks")

    # 4. dump_obu temporal-unit sizes match the parsed per-frame bytes.
    for cand in summary["candidates"]:
        result = json.loads(
            (work_root / cand["result_path"]).read_text())
        tu = [u["size"] for u in result["obu_units"]
              if u.get("size") is not None]
        got = [r["bytes"] for r in result["frame_status"]]
        if tu[:len(got)] != got:
            failures.append(
                f"{cand['tag']}: OBU TU sizes {tu[:len(got)]} != "
                f"parsed frame bytes {got}")

    # 5. Sampling attribution is produced for every variant/config pair.
    pairs = {(a["variant"], a["config"])
             for a in summary["sampling_attribution"]}
    for variant in ("text", "video"):
        for config in CONFIGS:
            if (variant, config) not in pairs:
                failures.append(
                    f"missing sampling attribution for {variant}/{config}")

    # 6. Every candidate carries the five visual evidence views.
    for cand in summary["candidates"]:
        vis = cand.get("visuals", {})
        for key in ("block_overlay", "block_overlay_roi", "roi_zoom",
                    "damage_map", "glyph_panel"):
            if key not in vis or not (work_root / vis[key]).exists():
                failures.append(f"{cand['tag']}: missing visual {key}")

    # 7. The decision only marks feasible candidates when all caps pass.
    cap = summary["cap"]
    for variant, decision in summary["decisions"].items():
        for c in decision["candidates"]:
            expect_feasible = (
                c["bitstream_bytes"] <= cap["bit_budget_bytes"]
                and c["psnr_roi"] >= cap["roi_psnr_min"]
                and c["stroke_gap_ratio"] <= cap["stroke_gap_max"])
            if c["feasible"] != expect_feasible:
                failures.append(
                    f"{variant}/{c['tag']}: feasibility flag inconsistent")

    if failures:
        print("FAIL")
        for f in failures:
            print(" -", f)
        sys.exit(1)
    print(f"OK: {len(summary['candidates'])} candidates, all evidence "
          f"checks passed under {work_root}")


if __name__ == "__main__":
    check(Path(sys.argv[1] if len(sys.argv) > 1 else "work"))
