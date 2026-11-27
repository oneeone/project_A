"""诊断附件 patch 的空间布局：连通块数量、尺寸、瓦片网格间距。

用途：确定"按块划分"到底应该切到多细。运行：
    python -m src.member_a.prepare.diagnose_layout
"""

from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from src.member_a.common import list_patches, load_paths

RADII_KM = [2.2, 2.5, 3.0, 5.0, 10.0]


class UF:
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


def main() -> int:
    patches = list_patches(load_paths())
    lon = np.array([p["lon"] for p in patches])
    lat = np.array([p["lat"] for p in patches])
    x = lon * 111.320 * np.cos(np.radians(lat.mean()))
    y = lat * 110.574
    xy = np.column_stack([x - x.mean(), y - y.mean()])
    print(f"patch 数 {len(patches)}   经度 {lon.min():.4f}~{lon.max():.4f}   纬度 {lat.min():.4f}~{lat.max():.4f}")

    # 网格间距
    ux = np.unique(np.round(x, 3))
    uy = np.unique(np.round(y, 3))
    print(f"x 唯一值 {len(ux)} 个，间距 {np.diff(ux)[:3]} km")
    print(f"y 唯一值 {len(uy)} 个，间距 {np.diff(uy)[:3]} km")

    for r in RADII_KM:
        tree = cKDTree(xy)
        pairs = tree.query_pairs(r=r, output_type="ndarray")
        uf = UF(len(patches))
        for a, b in pairs:
            uf.union(int(a), int(b))
        roots: dict[int, list[int]] = {}
        for i in range(len(patches)):
            roots.setdefault(uf.find(i), []).append(i)
        sizes = sorted((len(v) for v in roots.values()), reverse=True)
        print(f"\n半径 {r:>4} km: {len(sizes)} 个块, 前10大 {sizes[:10]}")

        if r == 3.0:
            for k, (root, idx) in enumerate(sorted(roots.items(), key=lambda kv: -len(kv[1]))):
                sub = xy[idx]
                w = sub[:, 0].max() - sub[:, 0].min()
                h = sub[:, 1].max() - sub[:, 1].min()
                nx = len(np.unique(np.round(sub[:, 0], 2)))
                ny = len(np.unique(np.round(sub[:, 1], 2)))
                print(f"  块{k}: n={len(idx):>4}  宽{w:>6.1f}km 高{h:>6.1f}km  网格 {nx}x{ny}  "
                      f"中心({lon[idx].mean():.3f}E, {lat[idx].mean():.3f}N)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
