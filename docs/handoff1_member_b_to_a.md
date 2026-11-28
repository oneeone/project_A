# 交接一：成员B到成员A

交付日期：2026-11-28

交付人：成员B

接收人：成员A

版本：v01

状态：P1 imagery final

## 已交付

- `outputs/samples/hengzhou_reference_grid_final_v01.zip`
  - 内含 `hengzhou_reference_grid_final_v01.tif`
  - `EPSG:32649`，10 m，float32，全 `-9999`
- `outputs/tables/hengzhou_grid_1km.json`
  - 与参考栅格同原点
  - 每单元100×100像元
  - `grid_id`按自左向右、自上向下连续编号
- `outputs/tables/hengzhou_input_metadata.json`
  - 包含全部实际STAC产品ID、日期、轨道和质量统计
- `outputs/tables/hengzhou_p0_verification_final_v01.json`
  - P0自动验收结果
- `outputs/tables/hengzhou_inputs_verification_final_v01.json`
  - P1影像栈与永久水体自动验收结果
- `D:\Desktop\APMCM\Hengzhou\hengzhou_stack_final_v01.tif`
  - `EPSG:32649`，10 m，8477×7199，6波段float32，NoData=`-9999`
  - SHA-256：`84523C3E17EE5201D2A4578DC78583985BC09135F01662485D3F2B259B85AAF3`
- `D:\Desktop\APMCM\Hengzhou\permanent_water_final_v01.tif`
  - `EPSG:32649`，10 m，uint8，值域`{0,1,255}`，NoData=`255`
  - SHA-256：`EF603FFFC727297E7EB5B6CC9011C4601AAAB20D3B602447E45B716271E1FB0C`

边界来源为 OpenStreetMap relation 2775762，查询日期为2026-11-28，许可为ODbL 1.0。

## 给A的三项确认

1. 参考网格已于2026-11-28交付，不再等待卫星影像。
2. 同意固定6波段顺序及标度；第6波段保留原始SAR dB，模型仅使用前5波段。
3. 第5波段（第四个光学通道）确认为Sentinel-2 B08（NIR）。

## P1影像合同

固定波段顺序：

1. `after_sar_uint8`
2. `before_red`
3. `before_green`
4. `before_blue`
5. `before_nir`
6. `after_sar_db`

灾期SAR采用2026-07-04 Sentinel-1D升轨、相对轨道157、VV极化的两景RTC。线性gamma0转换为dB后，第1/99百分位为`-19.275/1.005 dB`，用于第1波段0至255拉伸；第6波段保留原始dB。

灾前光学按2026-06-03、2026-05-26、2026-05-14、2026-04-09、2026-01-14顺序合成，使用B04/B03/B02/B08；SCL类别1、3、8、9、10、11排除。研究区34,528,116个像元中，仅8个像元在全部日期均无有效反射率，继续保持NoData，未使用0填充。

永久水体采用JRC Global Surface Water occurrence产品`100E_30Nv1_3_2020`，阈值为`occurrence >= 90%`。永久水体面积为51.8594平方千米。

共享目录：`D:\Desktop\APMCM\Hengzhou`

## 复现

在项目根目录执行：

```powershell
$env:PYTHONPATH = ".codex_deps"
python scripts/prepare_member_b_p0.py
python scripts/build_hengzhou_inputs.py `
  --output-dir "D:\Desktop\APMCM\Hengzhou"
```

交付前已检查：

- 参考栅格CRS、分辨率、数据类型和NoData；
- 全栅格均为`-9999`；
- metadata的transform、width和height与栅格一致；
- 1 km网格编号连续且无重复；
- 第5波段为`before_nir`，第6波段为`after_sar_db`；
- 影像栈和永久水体空间签名与参考栅格一致；
- 前5波段有效值均在0至255；
- 永久水体仅包含0、1和NoData 255。
