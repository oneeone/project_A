"""Q2 融合消融评估：三种子集成 → 阈值选择 → 指标表 + 概率缓存。

对每种结构（dual / early / optical / sar）：
  1. 加载 3 个种子的权重，对 val+test 逐 patch 推理，概率取均值、方差作不确定性
  2. 在 val 上扫描阈值，取 val IoU 最优者为该结构的判定阈值（与 Q1 同一口径）
  3. 在 test 上报告 IoU / F1 / P / R / OA / 面积误差
  4. dual 额外给一组"面积校准阈值"行，并缓存概率供不确定性图与地类误差使用

用法：
    python -m src.member_a.eval.eval_fusion
产物：
    outputs/tables/q2_ablation_candidate_v01.csv
    outputs/tables/q2_threshold_sweep.csv
    outputs/tables/fusion_eval_summary.json
    成员A产出/04_栅格结果/fusion_probs_v01/{val,test}_probs_dual.npz
"""

from __future__ import annotations

import argparse
import csv
import json
import time

import numpy as np
import torch

from src.member_a.common import ROOT, list_patches, load_paths, read_label, read_optical, read_sar
from src.member_a.train.train_fusion import MEAN, STD, build_model, slice_input

SEEDS = (2026, 2027, 2028)
ARCHS = ("dual", "early", "optical", "sar")
THRS = np.round(np.arange(0.20, 0.81, 0.02), 2)
MODEL_DIR = ROOT / "成员A产出" / "02_模型权重"
OUT = ROOT / "outputs" / "tables"


def load_models(arch: str, device: torch.device, suffix: str) -> tuple[list, str]:
    models, encoder = [], "resnet18"
    for s in SEEDS:
        p = MODEL_DIR / f"fusion_{arch}_seed{s}_{suffix}.pth"
        if not p.exists():
            print(f"  [跳过] 缺权重 {p.name}")
            continue
        ck = torch.load(p, map_location=device, weights_only=False)
        encoder = ck.get("encoder", encoder)
        m = build_model(arch, encoder).to(device)
        m.load_state_dict(ck["state_dict"])
        m.eval()
        models.append(m)
    return models, encoder


@torch.no_grad()
def infer(models: list, recs: list[dict], device: torch.device, arch: str) -> tuple[list, list]:
    probs, uncs = [], []
    for r in recs:
        x = np.concatenate([read_sar(r["sar"])[None], read_optical(r["optical"])], axis=0)
        x = (x.astype(np.float32) / 255.0 - MEAN) / STD
        t = slice_input(torch.from_numpy(x[None]).to(device), arch)
        ps = [torch.sigmoid(m(t)).squeeze().float().cpu().numpy() for m in models]
        st = np.stack(ps)
        probs.append(st.mean(0))
        uncs.append(st.var(0))
    return probs, uncs


def scores(mask: np.ndarray, label: np.ndarray, valid: np.ndarray, px_area: float) -> dict:
    pos, ok = label == 1, valid
    tp = int((mask & pos & ok).sum())
    fp = int((mask & ~pos & ok).sum())
    fn = int((~mask & pos & ok).sum())
    tn = int((~mask & ~pos & ok).sum())
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "n": int(ok.sum()),
            "pred_km2": (tp + fp) * px_area / 1e6, "true_km2": (tp + fn) * px_area / 1e6}


def aggregate(recs: list[dict], probs: list, thr: float) -> dict:
    a = {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "n": 0, "pred": 0.0, "true": 0.0}
    for p, pr in zip(recs, probs):
        s = scores(pr >= thr, p["label"], p["valid"], p["pixel_area_m2"])
        a["tp"] += s["tp"]; a["fp"] += s["fp"]; a["fn"] += s["fn"]; a["tn"] += s["tn"]
        a["n"] += s["n"]; a["pred"] += s["pred_km2"]; a["true"] += s["true_km2"]
    prec = a["tp"] / max(a["tp"] + a["fp"], 1)
    rec = a["tp"] / max(a["tp"] + a["fn"], 1)
    return {"iou": a["tp"] / max(a["tp"] + a["fp"] + a["fn"], 1),
            "f1": 2 * prec * rec / max(prec + rec, 1e-9),
            "precision": prec, "recall": rec,
            "oa": (a["tp"] + a["tn"]) / max(a["n"], 1),
            "pred_area_km2": a["pred"], "true_area_km2": a["true"],
            "area_rel_err": (a["pred"] - a["true"]) / max(a["true"], 1e-9)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suffix", default="v01", help="权重版本后缀，与训练时的 --suffix 一致")
    args = ap.parse_args()
    sfx = "" if args.suffix == "v01" else f"_{args.suffix}"
    prob_dir = ROOT / "成员A产出" / "04_栅格结果" / f"fusion_probs_{args.suffix}"
    t0 = time.time()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    with open(OUT / "provided_split_manifest_v03.json", encoding="utf-8") as f:
        split_of = {p["patch_id"]: p["split"] for p in json.load(f)["patches"]}
    patches = list_patches(load_paths())
    base = {}
    for p in patches:
        lab = read_label(p["label"])
        base[p["patch_id"]] = {"label": lab, "valid": lab != 255,
                               "pixel_area_m2": p["pixel_area_m2"]}
    recs = []
    for p in patches:
        recs.append({**p, **base[p["patch_id"]]})
    val = [p for p in recs if split_of[p["patch_id"]] == "val"]
    test = [p for p in recs if split_of[p["patch_id"]] == "test"]
    print(f"val {len(val)} / test {len(test)} / device {device}")

    rows, sweep_rows, summary = [], [], {}
    cache = {}
    for arch in ARCHS:
        models, encoder = load_models(arch, device, args.suffix)
        if not models:
            print(f"[{arch}] 无可用权重，跳过")
            continue
        t1 = time.time()
        pv, uv = infer(models, val, device, arch)
        pt, ut = infer(models, test, device, arch)
        print(f"[{arch}] {len(models)} 个种子推理完成 {time.time() - t1:.1f}s")

        best_thr, best_iou = 0.5, -1.0
        for t in THRS:
            iou = aggregate(val, pv, float(t))["iou"]
            sweep_rows.append({"arch": arch, "threshold": float(t), "val_iou": round(iou, 4)})
            if iou > best_iou:
                best_thr, best_iou = float(t), iou
        print(f"[{arch}] val 最优阈值 {best_thr:.2f} (IoU {best_iou:.4f})")

        m = aggregate(test, pt, best_thr)
        rows.append({"model": f"fusion_{arch}_3seed", "criterion": "iou", "split": "test",
                     "threshold": best_thr, "iou": round(m["iou"], 4), "f1": round(m["f1"], 4),
                     "precision": round(m["precision"], 4), "recall": round(m["recall"], 4),
                     "oa": round(m["oa"], 4), "pred_area_km2": round(m["pred_area_km2"], 3),
                     "true_area_km2": round(m["true_area_km2"], 3),
                     "area_rel_err": round(m["area_rel_err"], 4)})
        summary[arch] = {"seeds": [s for s in SEEDS if (MODEL_DIR / f"fusion_{arch}_seed{s}_{args.suffix}.pth").exists()],
                         "encoder": encoder, "threshold_val_iou": best_thr,
                         "val_iou": round(best_iou, 4),
                         "test_iou": round(m["iou"], 4), "test_f1": round(m["f1"], 4),
                         "test_area_rel_err": round(m["area_rel_err"], 4)}

        if arch == "dual":
            best_area, best_ratio = 0.5, 1e9
            for t in THRS:
                a = aggregate(val, pv, float(t))
                ratio = a["pred_area_km2"] / max(a["true_area_km2"], 1e-9)
                if abs(ratio - 1.0) < abs(best_ratio - 1.0):
                    best_area, best_ratio = float(t), ratio
            m2 = aggregate(test, pt, best_area)
            rows.append({"model": "fusion_dual_3seed", "criterion": "area", "split": "test",
                         "threshold": best_area, "iou": round(m2["iou"], 4), "f1": round(m2["f1"], 4),
                         "precision": round(m2["precision"], 4), "recall": round(m2["recall"], 4),
                         "oa": round(m2["oa"], 4), "pred_area_km2": round(m2["pred_area_km2"], 3),
                         "true_area_km2": round(m2["true_area_km2"], 3),
                         "area_rel_err": round(m2["area_rel_err"], 4)})
            summary["dual"]["threshold_area"] = best_area
            summary["dual"]["val_area_ratio"] = round(best_ratio, 4)
            print(f"[dual] 面积校准阈值 {best_area:.2f}（val 面积比 {best_ratio:.3f}）")
            cache = {"val": (pv, uv), "test": (pt, ut)}

    with open(OUT / f"q2_ablation{sfx}_candidate_v01.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    with open(OUT / f"q2_threshold_sweep{sfx}.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["arch", "threshold", "val_iou"])
        w.writeheader(); w.writerows(sweep_rows)

    if cache:
        prob_dir.mkdir(parents=True, exist_ok=True)
        for split, (pr, un) in cache.items():
            np.savez_compressed(
                prob_dir / f"{split}_probs_dual.npz",
                patch_id=np.array([p["patch_id"] for p in (val if split == "val" else test)]),
                prob=np.stack(pr).astype(np.float16),
                unc=np.stack(un).astype(np.float16),
            )
        summary["prob_cache"] = str(prob_dir.relative_to(ROOT))

    with open(OUT / f"fusion_eval_summary{sfx}.json", "w", encoding="utf-8") as f:
        json.dump({"seeds": list(SEEDS), "archs": list(ARCHS), "weight_suffix": args.suffix,
                   "detail": summary, "seconds": round(time.time() - t0, 1)},
                  f, ensure_ascii=False, indent=1)

    print("\n消融结果（test，阈值由 val 选定）：")
    for r in rows:
        print(f"  {r['model']:<24} {r['criterion']:>4} thr={r['threshold']:.2f} "
              f"IoU={r['iou']:.4f} F1={r['f1']:.4f} P={r['precision']:.3f} R={r['recall']:.3f} "
              f"面积误差={r['area_rel_err'] * 100:+.1f}%")
    print(f"\n总用时 {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
