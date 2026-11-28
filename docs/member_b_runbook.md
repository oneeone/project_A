# 成员B执行手册

## 1. 当前输入状态

本机三份主办方附件已审计通过：

- 灾前光学1000幅，编号1001至2000，样例为256乘256四通道。
- 灾后GF-3 HH SAR 1000幅，编号1001至2000，样例为256乘256单通道。
- 标签1000幅，编号1001至2000，样例为256乘256单通道。

审计结果保存在`outputs/tables/provided_archives_audit_candidate_v01.json`。Pillow将光学影像报告为`RGBA`仅表示四通道存储，不得把第四通道作为透明度丢弃。

## 2. 横州外部数据顺序

1. 行政边界：优先使用可说明许可和版本的权威边界；若使用OpenStreetMap，记录查询时间、relation ID和许可证。
2. Sentinel-1：灾前与灾期必须同轨道方向、同相对轨道和同极化。优先选择共极化VV以减小与GF-3 HH的物理差异，但域差异仍需在论文中讨论。
3. Sentinel-2：选择灾前最近的低云L2A影像，保留Red、Green、Blue及经A成员确认的第四通道。
4. DEM：Copernicus DEM GLO-30，作为地形校正和后续风险评估输入。
5. 永久水体：JRC Global Surface Water occurrence产品。正式阈值必须写入元数据和变更记录。

Sentinel-1/2的具体产品ID、日期、轨道、极化、云量和处理软件版本必须登记到`docs/data_catalog.csv`。

## 3. 空间冻结

- 目标投影固定为`EPSG:32649`。
- 参考分辨率10 m。
- 参考范围按边界外包矩形向10 m网格向外取整。
- 连续变量使用双线性插值。
- 分类变量和掩膜使用最近邻插值。
- 浮点NoData为`-9999`，掩膜NoData为`255`。
- 1 km单元与10 m参考栅格同原点，每边100个像素。
- 全`-9999`的参考栅格仅用于空间几何合同，不能作为研究区有效掩膜。
- 正式研究区掩膜为`study_area_mask_final_v01.tif`，横州市内像元值为1。
- 正式1 km网格为`hengzhou_grid_1km_final_v01.gpkg`，编号固定为`G000001`至`G003690`。

参考网格一旦交付A成员，不得静默修改。

## 4. 临时SAR洪水图

mock流程对同轨SAR计算

```text
change = log((during + epsilon) / (before + epsilon))
```

变化值越负表示散射变暗。用Otsu阈值产生候选水体，再剔除永久水体。该结果只能用于打通面积和网格程序，不能进入最终摘要或替代A成员模型。

## 5. 面积定义

- 新增淹没：灾中水体减灾前水体。
- 消退水体：灾前水体减灾中水体。
- 净增加：新增面积减消退面积。
- 区域比例：新增面积除以研究区有效面积。
- 有效陆地比例：新增面积除以研究区面积减永久水体面积。

面积只在投影参考栅格上计算。对10 m正方形像素，单像素面积为100平方米；若变换含旋转，程序使用仿射变换行列式。

## 6. 强度与不确定性

每个1 km单元的强度分数为

```text
I = 0.5 * robust_scale(flood_area_km2)
  + 0.5 * robust_scale(flood_ratio)
```

在所有有效网格上使用Jenks自然断点生成三个阈值，对应“一般、较强、强、极强”。阈值保存在独立JSON中。`intensity_confidence`同时考虑分数距最近阈值的距离和平均模型不确定性；靠近阈值或模型不确定性较高时置信度下降。

## 7. 交付前检查

- 三个A输出栅格与参考栅格的CRS、Transform、Width和Height完全一致。
- 研究区掩膜与参考栅格空间签名一致，类型为uint8，值域只含0、1、255，且存在值为1的像元。
- probability为float32且有效值在0至1。
- mask为uint8且仅有0、1、255。
- uncertainty非负，定义与`model_metadata.json`一致。
- `grid_id`唯一且严格连续为`G000001`至`G003690`，JSON、CSV和GPKG一致。
- 文件名含状态和版本，final文件不覆盖。
- 所有数据源、阈值和处理版本写入元数据。

## 8. 当前外部依赖状态

横州市边界、参考栅格、研究区掩膜、1 km网格、灾前光学合成、灾期SAR和JRC永久水体均已冻结为v01。正式面积和强度结果仅等待成员A按冻结空间合同交付三个模型输出栅格。

正式评估命令必须显式提供研究区掩膜和冻结网格：

```powershell
python scripts/run_member_b_assessment.py `
  --reference data/processed/hengzhou_reference_grid_final_v01.tif `
  --study-mask data/processed/study_area_mask_final_v01.tif `
  --assessment-grid data/processed/hengzhou_grid_1km_final_v01.gpkg `
  --probability <flood_probability.tif> `
  --mask <flood_mask.tif> `
  --uncertainty <uncertainty.tif> `
  --output-dir outputs
```
