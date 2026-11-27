"""Q2 论文用图（读已落盘产物，不重新推理）。

输出到 outputs/figures/：
    q2_ablation.png    四种融合结构的 test IoU / F1 对比
    q2_landcover.png   各地类 IoU 与占比
    q2_uncertainty.png 不确定性最高的 3 个 test patch（SAR / 概率 / 方差）

用法：python -m src.member_a.eval.make_q2_figures [--suffix v02]
"""

from __future__ import annotations

import argparse
import csv

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from src.member_a.common import ROOT, list_patches, load_paths, read_sar

plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False
FIG = ROOT / "outputs" / "figures"
TAB = ROOT / "outputs" / "tables"

NAME = {"optical": "仅光学 4 通道", "sar": "仅 SAR 1 通道",
        "early": "早期拼接 5 通道", "dual": "双分支+通道注意力"}


def read_csv(path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def fig_ablation(sfx: str) -> None:
    rows = [r for r in read_csv(TAB / f"q2_ablation{sfx}_candidate_v01.csv") if r["criterion"] == "iou"]
    rows.sort(key=lambda r: float(r["iou"]))
    names = [NAME.get(r["model"].split("_")[1], r["model"]) for r in rows]
    iou = [float(r["iou"]) for r in rows]
    f1 = [float(r["f1"]) for r in rows]
    thr = [float(r["threshold"]) for r in rows]

    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    x = np.arange(len(names))
    bars = ax.bar(x - 0.19, iou, 0.38, label="IoU", color="#3b6ea5")
    ax.bar(x + 0.19, f1, 0.38, label="F1", color="#e08a3c")
    for i, (b, v, t) in enumerate(zip(bars, iou, thr)):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.012, f"{v:.4f}\nthr={t:.2f}",
                ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=9)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("test 集指标（阈值由 val 选定）")
    ax.set_title("多源融合结构消融（3 种子集成，test 157 patch）", fontsize=11)
    ax.legend(loc="upper left", framealpha=0.9)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG / "q2_ablation.png", dpi=180)
    plt.close(fig)


def fig_landcover(sfx: str) -> None:
    rows = read_csv(TAB / f"landcover_error{sfx}_candidate_v01.csv")
    names = [f"{r['landcover_id']}\nSAR均值 {float(r['sar_mean']):.0f}" for r in rows]
    iou = [float(r["iou"]) for r in rows]
    share = [float(r["share_pct"]) for r in rows]

    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    x = np.arange(len(rows))
    bars = ax.bar(x, iou, 0.55, color="#4f8a5b")
    for b, v, s in zip(bars, iou, share):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.015, f"{v:.3f}\n({s:.1f}%)",
                ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=8.5)
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("地类内 IoU")
    ax.set_title("各地类误差（KMeans k=5，按 SAR 均值由暗到亮；括号为像元占比）", fontsize=10.5)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG / "q2_landcover.png", dpi=180)
    plt.close(fig)


def fig_uncertainty(suffix: str) -> None:
    npz = np.load(ROOT / "成员A产出" / "04_栅格结果" / f"fusion_probs_{suffix}" / "test_probs_dual.npz")
    pid_arr = npz["patch_id"]
    prob, unc = npz["prob"], npz["unc"]
    order = np.argsort([u.mean() for u in unc])[::-1][:3]
    patches = {p["patch_id"]: p for p in list_patches(load_paths())}

    fig, axes = plt.subplots(3, 3, figsize=(9.6, 9.4))
    titles = ["灾后 SAR (HH)", "融合淹没概率", "种子间方差（不确定性）"]
    for r, k in enumerate(order):
        pid = int(pid_arr[k])
        sar = read_sar(patches[pid]["sar"])
        axes[r, 0].imshow(sar, cmap="gray", vmin=0, vmax=255)
        axes[r, 1].imshow(prob[k], cmap="magma", vmin=0, vmax=1)
        im = axes[r, 2].imshow(unc[k], cmap="viridis", vmin=0, vmax=0.25)
        for c in range(3):
            axes[r, c].set_xticks([])
            axes[r, c].set_yticks([])
            if r == 0:
                axes[r, c].set_title(titles[c], fontsize=10.5)
        axes[r, 0].set_ylabel(f"patch {pid}\nmean unc {unc[k].mean():.3f}", fontsize=9)
    fig.tight_layout(rect=(0, 0, 0.92, 1))
    cax = fig.add_axes([0.935, 0.32, 0.012, 0.36])
    fig.colorbar(im, cax=cax, label="概率方差")
    fig.savefig(FIG / "q2_uncertainty.png", dpi=180)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suffix", default="v01", help="与 eval_fusion 的 --suffix 一致")
    args = ap.parse_args()
    sfx = "" if args.suffix == "v01" else f"_{args.suffix}"
    FIG.mkdir(parents=True, exist_ok=True)
    fig_ablation(sfx)
    fig_landcover(sfx)
    fig_uncertainty(args.suffix)
    print(f"图已生成：q2_ablation.png / q2_landcover.png / q2_uncertainty.png（suffix={args.suffix}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
