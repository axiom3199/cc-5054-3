"""Candidate selection under a total-bit budget and an ROI error cap."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Cap:
    bit_budget_bytes: int          # total bitstream bytes allowed
    roi_psnr_min: float            # minimum acceptable ROI PSNR (dB)
    stroke_gap_max: float          # maximum acceptable stroke-gap ratio


def judge(candidate: dict, cap: Cap) -> dict:
    """Annotate one candidate with feasibility and rejection reasons."""
    reasons = []
    if candidate["bitstream_bytes"] > cap.bit_budget_bytes:
        reasons.append("over_budget")
    if candidate["metrics"]["psnr_roi"] < cap.roi_psnr_min:
        reasons.append("roi_psnr_below_cap")
    if candidate["metrics"]["stroke_gap_ratio"] > cap.stroke_gap_max:
        reasons.append("stroke_gap_over_cap")
    return {**candidate, "feasible": not reasons, "reject_reasons": reasons}


def pick(candidates: list[dict], cap: Cap) -> dict:
    """Pick the feasible candidate with fewest bytes; tie-break on ROI PSNR.

    When nothing is feasible, the least-violating candidate is returned and
    flagged so the UI can show why every option was rejected.
    """
    judged = [judge(c, cap) for c in candidates]
    feasible = [c for c in judged if c["feasible"]]
    if feasible:
        chosen = min(feasible,
                     key=lambda c: (c["bitstream_bytes"],
                                    -c["metrics"]["psnr_roi"]))
        chosen["selection"] = "feasible"
        return chosen
    # Rank infeasible candidates: fewest violated constraints, then smallest
    # budget overshoot, then best ROI PSNR.
    chosen = min(judged, key=lambda c: (
        len(c["reject_reasons"]),
        max(0, c["bitstream_bytes"] - cap.bit_budget_bytes),
        -c["metrics"]["psnr_roi"]))
    chosen["selection"] = "no_feasible_candidate"
    return chosen


def compare_sampling(candidate_420: dict, candidate_444: dict) -> dict:
    """Attribute ROI damage to chroma subsampling vs. prediction/quantization.

    Both candidates share the same codec config. A large ROI gap in 4:2:0 that
    disappears in 4:4:4 (at similar full-frame PSNR) indicates chroma
    subsampling damage; residual damage in 4:4:4 is prediction/quantization.
    """
    m420, m444 = candidate_420["metrics"], candidate_444["metrics"]
    delta_full = m444["psnr_full"] - m420["psnr_full"]
    delta_roi = m444["psnr_roi"] - m420["psnr_roi"]
    # A clear 4:4:4 improvement means the 4:2:0 chroma filters caused the
    # damage; little improvement means prediction/quantization dominate.
    if delta_roi >= 3.0 and delta_roi >= delta_full:
        attribution = "chroma_subsampling"
    elif delta_roi < 1.0:
        attribution = "prediction_or_quantization"
    else:
        attribution = "mixed"
    return {
        "pix_fmt_pair": ["yuv420p", "yuv444p"],
        "psnr_full_420": m420["psnr_full"],
        "psnr_full_444": m444["psnr_full"],
        "psnr_roi_420": m420["psnr_roi"],
        "psnr_roi_444": m444["psnr_roi"],
        "stroke_gap_420": m420["stroke_gap_ratio"],
        "stroke_gap_444": m444["stroke_gap_ratio"],
        "delta_roi_db": delta_roi,
        "delta_full_db": delta_full,
        "attribution": attribution,
    }
