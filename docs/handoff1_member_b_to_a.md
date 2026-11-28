# 交接一：成员B到成员A

交付日期：2026-11-28

交付人：成员B

接收人：成员A

版本：v01

状态：P0 geometry final

## 已交付

- `outputs/samples/hengzhou_reference_grid_final_v01.zip`
  - 内含 `hengzhou_reference_grid_final_v01.tif`
  - `EPSG:32649`，10 m，float32，全 `-9999`
- `outputs/tables/hengzhou_grid_1km.json`
  - 与参考栅格同原点
  - 每单元100×100像元
  - `grid_id`按自左向右、自上向下连续编号
- `outputs/tables/hengzhou_input_metadata.json`
  - 宽、高、transform均为参考栅格真实值
  - 未取得的卫星产品字段明确写为`pending`
- `outputs/tables/hengzhou_p0_verification_final_v01.json`
  - P0自动验收结果

边界来源为 OpenStreetMap relation 2775762，查询日期为2026-11-28，许可为ODbL 1.0。

## 给A的三项确认

1. 参考网格已于2026-11-28交付，不再等待卫星影像。
2. 同意固定6波段顺序及标度；第6波段保留原始SAR dB，模型仅使用前5波段。
3. 第5波段（第四个光学通道）确认为Sentinel-2 B08（NIR）。

P1影像栈与永久水体尚未生成，共享目录暂记为`pending`。取得同轨Sentinel-1、灾前Sentinel-2和JRC数据后，将按同一参考网格生成并另行通知。

## 复现

在项目根目录执行：

```powershell
$env:PYTHONPATH = ".codex_deps"
python scripts/prepare_member_b_p0.py
```

交付前已检查：

- 参考栅格CRS、分辨率、数据类型和NoData；
- 全栅格均为`-9999`；
- metadata的transform、width和height与栅格一致；
- 1 km网格编号连续且无重复；
- 第5波段为`before_nir`，第6波段为`after_sar_db`。
