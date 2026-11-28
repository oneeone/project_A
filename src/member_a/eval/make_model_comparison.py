"""汇总 RF / U-Net / 融合各结构在 test 上的表现，生成模型对比表。

用法：python -m src.member_a.eval.make_model_comparison
产物：outputs/tables/q2_model_comparison_candidate_v01.csv
"""

from __future__ import annotations

import csv

from src.member_a.common import ROOT

TAB = ROOT / "outputs" / "tables"


def rows(path_: str) -> list[dict]:
    with open(TAB / path_, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main() -> int:
    out = []

    base = next(r for r in rows("q1_baseline_metrics_candidate_v01.csv")
                if r["model"] == "rf" and r["split"] == "test" and r["region"] == "all")
    out.append({"model": "RF（像元随机森林，5 通道）", "family": "baseline",
                "threshold": base["threshold"], "iou": base["iou"], "f1": base["f1"],
                "precision": base["precision"], "recall": base["recall"],
                "area_rel_err": base["area_rel_err"], "params_m": "-",
                "note": "阈值网格搜索，未做概率校准"})

    un = next(r for r in rows("unet_ensemble_metrics.csv")
              if r["criterion"] == "iou" and r["split"] == "test" and r["region"] == "all")
    out.append({"model": "U-Net（单枝，3 种子，isotonic 校准）", "family": "single_branch",
                "threshold": un["threshold_calibrated"], "iou": un["iou"], "f1": un["f1"],
                "precision": un["precision"], "recall": un["recall"],
                "area_rel_err": un["area_rel_err"], "params_m": 14.3,
                "note": "问题一最终模型，概率经等渗校准"})

    name = {"fusion_optical_3seed": "融合-仅光学 4 通道",
            "fusion_sar_3seed": "融合-仅 SAR 1 通道",
            "fusion_early_3seed": "融合-早期拼接 5 通道",
            "fusion_dual_3seed": "融合-双分支+通道注意力"}
    params = {"fusion_optical_3seed": 14.3, "fusion_sar_3seed": 14.3,
              "fusion_early_3seed": 14.3, "fusion_dual_3seed": 26.3}
    for r in rows("q2_ablation_candidate_v01.csv"):
        if r["criterion"] != "iou":
            continue
        out.append({"model": name.get(r["model"], r["model"]), "family": "fusion",
                    "threshold": r["threshold"], "iou": r["iou"], "f1": r["f1"],
                    "precision": r["precision"], "recall": r["recall"],
                    "area_rel_err": r["area_rel_err"], "params_m": params.get(r["model"], "-"),
                    "note": "30 epoch，3 种子集成，原始概率（未校准）"})

    keys = ["model", "family", "threshold", "iou", "f1", "precision", "recall",
            "area_rel_err", "params_m", "note"]
    with open(TAB / "q2_model_comparison_candidate_v01.csv", "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(out)

    print(f"{'模型':32s} {'IoU':>7s} {'F1':>7s} {'P':>7s} {'R':>7s} {'面积误差':>8s}")
    for r in out:
        print(f"{r['model']:32s} {float(r['iou']):.4f} {float(r['f1']):.4f} "
              f"{float(r['precision']):.4f} {float(r['recall']):.4f} "
              f"{float(r['area_rel_err']) * 100:+.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
