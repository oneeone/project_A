"""横州推理链路的最小单元测试：滑窗覆盖与 B 侧契约校验（不加载模型、不用 GPU）。"""

import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from src.member_a.infer.run_hengzhou_inference import b_contract_check, tile_starts

CRS = "EPSG:32649"
TR = from_origin(273290.0, 2559770.0, 10.0, 10.0)


def write(path: Path, arr: np.ndarray, dtype: str, nodata) -> None:
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1],
                       count=1, dtype=dtype, crs=CRS, transform=TR, nodata=nodata) as dst:
        dst.write(arr.astype(dtype), 1)


class TileStartsTest(unittest.TestCase):
    def test_covers_full_extent_without_overflow(self) -> None:
        size, tile, stride = 7199, 512, 256
        starts = tile_starts(size, tile, stride)
        self.assertEqual(starts[0], 0)
        self.assertEqual(starts[-1], size - tile)
        self.assertTrue(all(0 <= s <= size - tile for s in starts))
        self.assertEqual(starts, sorted(set(starts)))
        for a, b in zip(starts, starts[1:]):     # 相邻窗口必须重叠或相接，不留缝
            self.assertLessEqual(b, a + tile)

    def test_smaller_than_tile(self) -> None:
        self.assertEqual(tile_starts(300, 512, 256), [0])

    def test_exact_multiple(self) -> None:
        self.assertEqual(tile_starts(1024, 512, 512), [0, 512])

    def test_real_grid_tile_count(self) -> None:
        rows = tile_starts(7199, 512, 256)
        cols = tile_starts(8477, 512, 256)
        self.assertEqual(len(rows) * len(cols), 924)   # 与冒烟日志一致


class BContractTest(unittest.TestCase):
    """用 B 自己的 validate_flood_rasters 判 A 的四件套。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.ref = d / "ref.tif"
        write(self.ref, np.zeros((64, 64), np.uint8), "uint8", 255)

        rng = np.random.default_rng(0)
        prob = rng.random((64, 64)).astype(np.float32)
        mask = (prob >= 0.36).astype(np.uint8)
        unc = (np.abs(prob - 0.5) * 0.2).astype(np.float32)
        prob[:2], mask[:2], unc[:2] = -9999.0, 255, -9999.0

        self.prob, self.mask, self.unc = d / "p.tif", d / "m.tif", d / "u.tif"
        write(self.prob, prob, "float32", -9999.0)
        write(self.mask, mask, "uint8", 255)
        write(self.unc, unc, "float32", -9999.0)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _check(self):
        problems = b_contract_check(self.ref, self.prob, self.mask, self.unc)
        if problems is None:
            self.skipTest("未取到 b_raster_contracts.py，先跑 python -m src.member_a.infer.fetch_b_handoff")
        return problems

    def test_clean_output_passes(self) -> None:
        self.assertEqual(self._check(), [])

    def test_nodata_footprint_mismatch_is_caught(self) -> None:
        with rasterio.open(self.mask) as src:
            arr = src.read(1)
        arr[5, 5] = 255            # 概率有效但掩膜标为 NoData，footprint 不一致
        write(self.mask, arr, "uint8", 255)
        problems = self._check()
        self.assertTrue(any("NoData" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
