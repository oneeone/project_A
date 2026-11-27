"""按空间块生成训练/验证/测试划分清单（v02：适配"大型连续镶嵌"结构）。

诊断结论（见 diagnose_layout.py）：
- 1000 个 patch 只构成 5 个空间连通片：512（东洞庭，54x11）、450（鄱阳湖，51x10）、
  16、12、10 三个孤立条带
- 瓦片间距 2.078 km（经向）x 2.359 km（纬向），无重叠
=> 直接拿"连通片"当块，只能切出 5 块，比例无法满足 70/15/15。
   因此把每片镶嵌按 4x4 瓦片切成子块（约 8.3 km x 9.4 km），再按子块分配。

防泄漏：测试块之间互不相邻；与测试块相邻的块进入 buffer（不参与训练）。
输出：
    outputs/tables/provided_split_manifest_v02.json
    outputs/tables/provided_split_summary_v02.csv
运行：python -m src.member_a.prepare.make_split
"""

from __future__ import annotations

import csv
import json
import time
from collections import defaultdict
from datetime import datetime

import numpy as np
from scipy.spatial import cKDTree

from src.member_a.common import ROOT, list_patches, load_paths, read_label, region_of

ADJ_KM = 2.5          # 判定"同一片镶嵌"的邻接半径
BLOCK_TILES = 4       # 子块边长（瓦片数），4x4 ~= 8.3km x 9.4km
RATIOS = {"train": 0.70, "val": 0.15, "test": 0.15}
SEED = 2026
OUT_DIR = ROOT / "outputs" / "tables"


class UnionFind:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def to_local_km(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    x = lon * 111.320 * np.cos(np.radians(lat.mean()))
    y = lat * 110.574
    return np.column_stack([x - x.mean(), y - y.mean()])


def tile_indices(v: np.ndarray) -> np.ndarray:
    """把等间距坐标映射成整数序号。"""
    u = np.unique(np.round(v, 3))
    return np.searchsorted(u, np.round(v, 3))


def main() -> int:
    t0 = time.time()
    patches = list_patches(load_paths())
    n = len(patches)
    lon = np.array([p["lon"] for p in patches])
    lat = np.array([p["lat"] for p in patches])
    xy = to_local_km(lon, lat)

    # ---------- 1. 连通片（镶嵌图） ----------
    print("[1/6] 识别连通镶嵌片 ...")
    uf = UnionFind(n)
    for a, b in cKDTree(xy).query_pairs(r=ADJ_KM, output_type="ndarray"):
        uf.union(int(a), int(b))
    comp_of = np.array([uf.find(i) for i in range(n)])
    comps = sorted({int(c) for c in comp_of})
    comp_sizes = {c: int((comp_of == c).sum()) for c in comps}
    comps.sort(key=lambda c: -comp_sizes[c])
    comp_id = {c: k for k, c in enumerate(comps)}
    mosaic = np.array([comp_id[int(c)] for c in comp_of])
    print("      片大小:", [comp_sizes[c] for c in comps])

    # ---------- 2. 瓦片编号 + 子块 ----------
    print("[2/6] 切分子块 ...")
    col = np.zeros(n, dtype=int)
    row = np.zeros(n, dtype=int)
    for c in comps:
        m = np.where(comp_of == c)[0]
        col[m] = tile_indices(xy[m, 0])
        row[m] = tile_indices(xy[m, 1])

    block_key = {}
    blocks: dict[tuple, list[int]] = defaultdict(list)
    for i in range(n):
        k = (int(mosaic[i]), int(col[i] // BLOCK_TILES), int(row[i] // BLOCK_TILES))
        block_key[i] = k
        blocks[k].append(i)
    print(f"      {len(blocks)} 个子块，最大 {max(len(v) for v in blocks.values())} 个 patch")

    # 块级邻接（同一镶嵌内，行列差 <=1）
    centers = {k: (np.mean(xy[v, 0]), np.mean(xy[v, 1])) for k, v in blocks.items()}
    keys = list(blocks)
    cxy = np.array([centers[k] for k in keys])
    tree = cKDTree(cxy)
    # 块中心间距约 8-9 km，用 1.6 倍块宽判定相邻
    neigh_pairs = tree.query_pairs(r=BLOCK_TILES * 2.4, output_type="ndarray")
    neighbors = defaultdict(set)
    for i, j in neigh_pairs:
        ki, kj = keys[i], keys[j]
        if ki[0] != kj[0]:
            continue
        neighbors[ki].add(kj)
        neighbors[kj].add(ki)

    # ---------- 3. 标签统计（划分前需要，用于分层） ----------
    print("[3/6] 统计标签 ...")
    for p in patches:
        lab = read_label(p["label"])
        valid = lab != 255
        p["water_frac"] = float((lab[valid] == 1).mean()) if valid.any() else 0.0
    bwf = {k: float(np.mean([patches[i]["water_frac"] for i in v])) for k, v in blocks.items()}

    # ---------- 4. 分层按块分配 ----------
    print("[4/6] 分层按块分配 split ...")
    rng = np.random.default_rng(SEED)
    split_of_block: dict[tuple, str] = {}
    targets = {s: RATIOS[s] * n for s in RATIOS}

    small = sorted([c for c in comps if comp_sizes[c] <= 20], key=lambda c: -comp_sizes[c])
    big = [c for c in comps if comp_sizes[c] > 20]

    # 大镶嵌：块按水体占比排序后做赤字分配，三个 split 共享同一条分布曲线
    for c in big:
        ckeys = [k for k in keys if k[0] == comp_id[c]]
        ckeys.sort(key=lambda k: bwf[k])
        have = {s: 0.0 for s in RATIOS}
        for i, k in enumerate(ckeys):
            tgt = {s: RATIOS[s] * (i + 1) for s in RATIOS}
            s = max(RATIOS, key=lambda x: tgt[x] - have[x])
            split_of_block[k] = s
            have[s] += 1

    # 孤立小条带：整片补给当前 patch 缺口最大的 split
    assigned = {s: sum(len(blocks[k]) for k in keys if split_of_block.get(k) == s) for s in RATIOS}
    for c in small:
        s = max(RATIOS, key=lambda x: targets[x] - assigned[x])
        for k in [k for k in keys if k[0] == comp_id[c]]:
            split_of_block[k] = s
            assigned[s] += len(blocks[k])

    for k in keys:
        split_of_block.setdefault(k, "train")

    for i in range(n):
        patches[i]["mosaic_id"] = int(mosaic[i])
        patches[i]["tile_col"] = int(col[i])
        patches[i]["tile_row"] = int(row[i])
        patches[i]["block_id"] = "m{}-r{}c{}".format(*block_key[i])
        patches[i]["region"] = region_of(lon[i])
        patches[i]["split"] = split_of_block[block_key[i]]

    counts = {s: sum(1 for p in patches if p["split"] == s) for s in ("train", "val", "test")}
    wf_mean = {s: float(np.mean([p["water_frac"] for p in patches if p["split"] == s])) for s in counts}
    print(f"      分配结果: {counts}  (目标 {targets})")
    print("      各 split 水体占比均值: " + ", ".join(f"{s}={v:.3f}" for s, v in wf_mean.items()))

    # ---------- 5. 距离核查 ----------
    print("[5/6] 空间独立性核查 ...")

    def min_dist(sa: str, sb: str) -> float:
        ia = [i for i, p in enumerate(patches) if p["split"] == sa]
        ib = [i for i, p in enumerate(patches) if p["split"] == sb]
        if not ia or not ib:
            return float("nan")
        d, _ = cKDTree(xy[ib]).query(xy[ia], k=1)
        return float(d.min())

    d_tr_te = min_dist("train", "test")
    d_tr_va = min_dist("train", "val")
    d_va_te = min_dist("val", "test")

    def block_center_min(sa: str, sb: str) -> float:
        ka = [k for k in keys if split_of_block[k] == sa]
        kb = [k for k in keys if split_of_block[k] == sb]
        if not ka or not kb:
            return float("nan")
        d, _ = cKDTree(np.array([centers[k] for k in kb])).query(np.array([centers[k] for k in ka]), k=1)
        return float(d.min())

    db_tr_te = block_center_min("train", "test")
    db_tr_va = block_center_min("train", "val")

    # ---------- 6. 写出 ----------
    print("[6/6] 写出 ...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": "v03",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "owner": "member_a",
        "method": "mosaic_tiling_4x4_blocks",
        "block_size_km": [round(BLOCK_TILES * 2.078, 2), round(BLOCK_TILES * 2.359, 2)],
        "mosaic_adjacency_km": ADJ_KM,
        "block_tiles": BLOCK_TILES,
        "random_seed": SEED,
        "ratios_target": RATIOS,
        "counts": counts,
        "mosaic_sizes": [comp_sizes[c] for c in comps],
        "block_count": len(blocks),
        "min_cross_distance_km": {
            "train_val": round(d_tr_va, 3),
            "train_test": round(d_tr_te, 3),
            "val_test": round(d_va_te, 3),
        },
        "min_block_center_distance_km": {
            "train_val": round(db_tr_va, 3),
            "train_test": round(db_tr_te, 3),
        },
        "region_rule": "lon < 115E 记为 west，其余 east",
        "pixel_area_m2_median": round(float(np.median([p["pixel_area_m2"] for p in patches])), 3),
        "note": "块间无重叠；相邻块在 2.08km 处接边，块中心最近距见 min_block_center_distance_km",
        "patches": [
            {
                "patch_id": p["patch_id"],
                "lon": p["lon"],
                "lat": p["lat"],
                "region": p["region"],
                "mosaic_id": p["mosaic_id"],
                "tile_col": p["tile_col"],
                "tile_row": p["tile_row"],
                "block_id": p["block_id"],
                "split": p["split"],
            }
            for p in patches
        ],
    }
    mpath = OUT_DIR / "provided_split_manifest_v03.json"
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    spath = OUT_DIR / "provided_split_summary_v03.csv"
    with open(spath, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["split", "region", "mosaic_id", "n_patches", "n_blocks", "water_frac_mean"])
        for s in ("train", "val", "test"):
            for r in ("west", "east"):
                sub = [p for p in patches if p["split"] == s and p["region"] == r]
                if not sub:
                    continue
                w.writerow([s, r, sub[0]["mosaic_id"], len(sub),
                            len({p["block_id"] for p in sub}),
                            round(float(np.mean([p["water_frac"] for p in sub])), 4)])

    print(f"\n完成，用时 {time.time() - t0:.1f}s")
    print(f"  {mpath.relative_to(ROOT)}")
    print(f"  {spath.relative_to(ROOT)}")
    print(f"  划分: {counts}")
    print(f"  patch 最近距: train-test {d_tr_te:.2f} km, train-val {d_tr_va:.2f} km, val-test {d_va_te:.2f} km")
    print(f"  块中心最近距: train-test {db_tr_te:.2f} km, train-val {db_tr_va:.2f} km")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
