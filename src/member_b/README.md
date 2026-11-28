# 成员B工作区

负责：

- 横州市行政边界和遥感数据；
- 参考栅格；
- 灾前与灾中影像配准；
- 永久水体；
- 临时SAR变化检测；
- 新增、消退和净增加面积；
- 洪灾强度及其不确定性；
- assessment_grid输出。

在A模型完成前，使用临时阈值洪水图开发完整流程。正式概率图到达后只替换输入，不重写程序。

## 已实现模块

- `preprocess.py`：行政边界投影、10 m参考栅格、栅格对齐和输入栈。
- `raster_contracts.py`：校验A交付的概率、掩膜、不确定性及空间签名。
- `temporary_flood.py`：同轨SAR灾前/灾中暗化变化的mock洪水图。
- `area.py`：新增、消退、净增加、区域比例和有效陆地比例。
- `intensity.py`：面积-比例综合分数、Jenks四级分类和置信度。
- `assessment.py`：按冻结原点聚合到1 km评估网格。
- `outputs.py`：写出CSV、GPKG和JSON交付文件。

## 运行顺序

1. 在`configs/paths.local.yaml`填写本机路径。
2. 用`scripts/audit_provided_archives.py`核对三份主办方压缩包。
3. 获取横州市边界，并核实`EPSG:32649`覆盖范围。
4. 用`prepare_member_b_p0.py`生成正式10 m参考栅格、研究区掩膜和冻结1 km网格。
5. 对Sentinel-2、同轨Sentinel-1、DEM和JRC永久水体分别对齐。
6. 按A成员确认的通道顺序生成输入栈及`input_metadata.json`。
7. A的三个正式结果到达后运行`scripts/run_member_b_assessment.py`。

示例：

```powershell
python scripts/run_member_b_assessment.py `
  --reference D:/APMCM/data/hengzhou/hengzhou_reference_grid_final_v01.tif `
  --study-mask D:/APMCM/data/hengzhou/study_area_mask_final_v01.tif `
  --assessment-grid D:/APMCM/data/hengzhou/hengzhou_grid_1km_final_v01.gpkg `
  --probability D:/APMCM/outputs/hengzhou_flood_probability_candidate_v01.tif `
  --mask D:/APMCM/outputs/hengzhou_flood_mask_candidate_v01.tif `
  --uncertainty D:/APMCM/outputs/hengzhou_uncertainty_candidate_v01.tif `
  --permanent-water D:/APMCM/data/hengzhou/permanent_water_final_v01.tif `
  --output-dir D:/APMCM/outputs/member_b `
  --status candidate `
  --version v01
```

参考栅格是全`-9999`的几何模板，不能当作有效区掩膜。程序只使用`study_area_mask_final_v01.tif == 1`作为横州市研究区，并沿用冻结GPKG中的`G000001`至`G003690`编号和几何。

若只有A给出的“新增淹没掩膜”，程序不会虚构消退面积和净增加量；这两个量必须由灾前、灾中总水体掩膜共同计算。
