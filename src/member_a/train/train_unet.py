"""U-Net 训练：5 通道输入（SAR + R/G/B/Aux），BCE+Dice，AMP，按块划分。

用法：
    python -m src.member_a.train.train_unet --seed 2026 --epochs 24
产物：
    成员A产出/02_模型权重/unet_seed{seed}_v01.pth
    outputs/tables/unet_train_history_seed{seed}.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

import segmentation_models_pytorch as smp

from src.member_a.common import ROOT, list_patches, load_paths, read_label, read_optical, read_sar

IN_CH = 5
MEAN = 0.5
STD = 0.25


class PatchDS(Dataset):
    """把 1000 个 patch 里属于某个 split 的读进内存。"""

    def __init__(self, records: list[dict], augment: bool = False) -> None:
        self.augment = augment
        self.items = []
        for r in records:
            sar = read_sar(r["sar"])
            opt = read_optical(r["optical"])
            img = np.concatenate([sar[None], opt], axis=0).astype(np.uint8)  # (5,H,W)
            lab = read_label(r["label"])
            self.items.append((img, lab))

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, i: int):
        img, lab = self.items[i]
        x = img.astype(np.float32) / 255.0
        y = lab.astype(np.float32)
        valid = (lab != 255).astype(np.float32)
        y = np.where(lab == 255, 0.0, y)

        if self.augment:
            if np.random.rand() < 0.5:
                x, y, valid = x[:, :, ::-1], y[:, ::-1], valid[:, ::-1]
            if np.random.rand() < 0.5:
                x, y, valid = x[:, ::-1], y[::-1], valid[::-1]
            k = np.random.randint(4)
            if k:
                x, y, valid = np.rot90(x, k, (1, 2)), np.rot90(y, k), np.rot90(valid, k)
            x = np.ascontiguousarray(x)
            y = np.ascontiguousarray(y)
            valid = np.ascontiguousarray(valid)

        x = (x - MEAN) / STD
        return torch.from_numpy(x), torch.from_numpy(y), torch.from_numpy(valid)


def dice_loss(logits: torch.Tensor, target: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    prob = torch.sigmoid(logits) * valid
    target = target * valid
    inter = (prob * target).sum(dim=(1, 2))
    union = prob.sum(dim=(1, 2)) + target.sum(dim=(1, 2))
    return (1 - (2 * inter + 1) / (union + 1)).mean()


def masked_bce(logits: torch.Tensor, target: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    loss = F.binary_cross_entropy_with_logits(logits, target, reduction="none")
    return (loss * valid).sum() / valid.sum().clamp_min(1.0)


@torch.no_grad()
def evaluate(model: nn.Module, loader: DataLoader, device: torch.device) -> dict:
    model.eval()
    tp = fp = fn = 0
    for x, y, valid in loader:
        x = x.to(device, non_blocking=True)
        prob = torch.sigmoid(model(x)).squeeze(1).cpu().numpy()
        pred = prob >= 0.5
        pos = y.numpy() >= 0.5
        ok = valid.numpy() > 0.5
        tp += int((pred & pos & ok).sum())
        fp += int((pred & ~pos & ok).sum())
        fn += int((~pred & pos & ok).sum())
    iou = tp / max(tp + fp + fn, 1)
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    return {"iou": iou, "precision": prec, "recall": rec,
            "f1": 2 * prec * rec / max(prec + rec, 1e-9)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--epochs", type=int, default=24)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--encoder", default="resnet18")
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    torch.backends.cudnn.benchmark = True
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    with open(ROOT / "outputs" / "tables" / "provided_split_manifest_v03.json", encoding="utf-8") as f:
        split_of = {p["patch_id"]: p["split"] for p in json.load(f)["patches"]}
    patches = list_patches(load_paths())
    train_rec = [p for p in patches if split_of[p["patch_id"]] == "train"]
    val_rec = [p for p in patches if split_of[p["patch_id"]] == "val"]
    print(f"train {len(train_rec)} / val {len(val_rec)} / device {device}")

    t0 = time.time()
    ds_tr = PatchDS(train_rec, augment=True)
    ds_va = PatchDS(val_rec, augment=False)
    dl_tr = DataLoader(ds_tr, batch_size=args.batch, shuffle=True, num_workers=0, drop_last=True)
    dl_va = DataLoader(ds_va, batch_size=args.batch, shuffle=False, num_workers=0)
    print(f"数据载入 {time.time() - t0:.1f}s")

    model = smp.Unet(
        encoder_name=args.encoder, encoder_weights=None,
        in_channels=IN_CH, classes=1,
    ).to(device)
    n_param = sum(p.numel() for p in model.parameters()) / 1e6
    print(f"模型 {args.encoder}-unet, {n_param:.1f}M 参数")

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-2)
    sched = torch.optim.lr_scheduler.OneCycleLR(
        opt, max_lr=args.lr, total_steps=args.epochs * len(dl_tr), pct_start=0.15)
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")

    out_dir = ROOT / "outputs" / "tables"
    out_dir.mkdir(parents=True, exist_ok=True)
    hist_path = out_dir / f"unet_train_history_seed{args.seed}.csv"
    model_dir = ROOT / "成员A产出" / "02_模型权重"
    model_dir.mkdir(parents=True, exist_ok=True)
    best_iou, best_epoch, rows = 0.0, -1, []

    for ep in range(1, args.epochs + 1):
        model.train()
        run_loss, nb = 0.0, 0
        te = time.time()
        for x, y, valid in dl_tr:
            x = x.to(device, non_blocking=True)
            y = y.to(device, non_blocking=True)
            valid = valid.to(device, non_blocking=True)
            opt.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", enabled=device.type == "cuda"):
                logits = model(x).squeeze(1)
                loss = 0.5 * masked_bce(logits, y, valid) + 0.5 * dice_loss(logits, y, valid)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            sched.step()
            run_loss += loss.item()
            nb += 1

        msg = f"epoch {ep:>2}/{args.epochs} loss {run_loss / max(nb, 1):.4f}"
        if ep % 2 == 0 or ep == args.epochs:
            st = evaluate(model, dl_va, device)
            msg += f"  val IoU {st['iou']:.4f}  F1 {st['f1']:.4f}  R {st['recall']:.3f}"
            rows.append({"epoch": ep, "loss": round(run_loss / max(nb, 1), 4),
                         **{k: round(v, 4) for k, v in st.items()}})
            if st["iou"] > best_iou:
                best_iou, best_epoch = st["iou"], ep
                torch.save({"state_dict": model.state_dict(), "encoder": args.encoder,
                            "in_ch": IN_CH, "seed": args.seed, "epoch": ep,
                            "val_iou": st["iou"], "mean": MEAN, "std": STD},
                           model_dir / f"unet_seed{args.seed}_v01.pth")
        print(msg + f"  {time.time() - te:.1f}s")

    with open(hist_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["epoch", "loss", "iou", "precision", "recall", "f1"])
        w.writeheader()
        w.writerows(rows)

    print(f"\nseed {args.seed} 最佳 val IoU {best_iou:.4f} @ epoch {best_epoch}")
    print(f"  权重 {(model_dir / f'unet_seed{args.seed}_v01.pth').relative_to(ROOT)}")
    print(f"  总用时 {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
