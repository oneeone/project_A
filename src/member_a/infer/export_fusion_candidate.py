"""按消融结果导出问题二的最终融合候选模型（3 种子集成打包）与 metadata。

选择规则：
  - 取 test IoU 最高的结构；
  - 若第二名与第一名差距 ≤ --tol（默认 0.003），选参数量更小的那个
    （14.3M 的早期拼接优于 26.3M 的双分支，推理更快、依赖更少）。

用法：
    python -m src.member_a.infer.export_fusion_candidate --suffix v02
产物：
    成员A产出/02_模型权重/fusion_model_candidate_v01.pth
    成员A产出/02_模型权重/fusion_model_candidate_v01_metadata.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import torch

from src.member_a.common import ROOT
from src.member_a.infer.contract_meta import (INPUT_CHANNELS, SOURCE_IMAGE_DATES,
                                             UNCERTAINTY_DEF, git_state)

WEIGHT_DIR = ROOT / "成员A产出" / "02_模型权重"
TAB = ROOT / "outputs" / "tables"
SEEDS = (2026, 2027, 2028)
PARAMS_M = {"dual": 26.3, "early": 14.3, "optical": 14.3, "sar": 14.3}
CHANNELS = {
    "early": INPUT_CHANNELS,
    "optical": INPUT_CHANNELS[1:],
    "sar": INPUT_CHANNELS[:1],
    "dual": {"branch_optical": INPUT_CHANNELS[1:], "branch_sar": INPUT_CHANNELS[:1],
             "fusion": "逐级 1x1 降维 + 通道注意力（SE）"},
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suffix", default="v02")
    ap.add_argument("--tol", type=float, default=0.003)
    ap.add_argument("--arch", default="auto", choices=["auto", "dual", "early", "optical", "sar"])
    args = ap.parse_args()
    sfx = "" if args.suffix == "v01" else f"_{args.suffix}"

    with open(TAB / f"fusion_eval_summary{sfx}.json", encoding="utf-8") as f:
        summary = json.load(f)
    with open(TAB / "provided_split_manifest_v03.json", encoding="utf-8") as f:
        split_rule = json.load(f)["method"]
    detail = summary["detail"]

    if args.arch == "auto":
        archs = [a for a in ("dual", "early", "optical", "sar") if isinstance(detail.get(a), dict)]
        ranked = sorted(archs, key=lambda a: -detail[a]["test_iou"])
        best = ranked[0]
        arch = min([a for a in ranked if detail[best]["test_iou"] - detail[a]["test_iou"] <= args.tol],
                   key=lambda a: PARAMS_M[a])
        print(f"自动选择：{arch}（候选 {[(a, detail[a]['test_iou']) for a in ranked]}）")
    else:
        arch = args.arch

    members, seeds = [], []
    for s in SEEDS:
        p = WEIGHT_DIR / f"fusion_{arch}_seed{s}_{args.suffix}.pth"
        if not p.exists():
            print(f"  [缺] {p.name}")
            continue
        ck = torch.load(p, map_location="cpu", weights_only=False)
        members.append(ck["state_dict"])
        seeds.append(s)
    if not members:
        raise SystemExit(f"{arch} 没有可用权重")

    d = detail[arch]
    payload = {
        "arch": arch, "encoder": d["encoder"], "seeds": seeds, "members": members,
        "input_layout": CHANNELS[arch], "mean": 0.5, "std": 0.25,
        "threshold": d["threshold_val_iou"], "threshold_selected_on": "val_iou",
        "aggregate": "mean of per-seed sigmoid probabilities",
        "metrics": {"val_iou": d["val_iou"], "test_iou": d["test_iou"],
                    "test_f1": d["test_f1"], "test_area_rel_err": d["test_area_rel_err"]},
        "weight_suffix": args.suffix,
    }
    out = WEIGHT_DIR / "fusion_model_candidate_v01.pth"
    torch.save(payload, out)

    sha, dirty = git_state()
    meta = {
        "model_version": f"fusion_{arch}_{args.suffix}_ensemble3_v01",
        "status": "candidate",
        "question": "Q2 多源融合（灾前光学 + 灾后 SAR）",
        "arch": arch,
        "params_m": PARAMS_M[arch],
        "ensemble_members": len(members),
        "training_seed": seeds,
        "input_layout": CHANNELS[arch],
        "input_normalization": {"mean": 0.5, "std": 0.25, "range": "uint8/255"},
        "threshold": d["threshold_val_iou"],
        "threshold_selected_on": "val",
        "threshold_area_calibrated": d.get("threshold_area"),
        "calibration": {"method": "none（直接使用原始概率，未做 isotonic）"},
        "label_semantics": "newly_inundated_only",
        "uncertainty_definition": UNCERTAINTY_DEF,
        "source_image_dates": SOURCE_IMAGE_DATES,
        "crs": "EPSG:4326",
        "reference_grid": "provided_native_4326",
        "validation_iou": d["val_iou"],
        "evaluation": {
            "split": "test", "n_patch": 157,
            "iou": d["test_iou"], "f1": d["test_f1"],
            "area_rel_err": d["test_area_rel_err"],
            "note": "3 种子集成，阈值由 val 选定；未做 isotonic 校准，故与问题一指标不完全同口径",
        },
        "ablation_table": f"outputs/tables/q2_ablation{sfx}_candidate_v01.csv",
        "landcover_table": f"outputs/tables/landcover_error{sfx}_candidate_v01.csv",
        "split_rule": split_rule,
        "git_commit": sha,
        "git_dirty": dirty,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(WEIGHT_DIR / "fusion_model_candidate_v01_metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    print(f"已导出 fusion_model_candidate_v01.pth：arch={arch} seeds={seeds} "
          f"test IoU {d['test_iou']:.4f} thr {d['threshold_val_iou']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
