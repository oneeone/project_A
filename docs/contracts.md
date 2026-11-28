# 成员间接口协议

## 1. B交付A：横州市模型输入

必须包含：

- `hengzhou_boundary_final_v01.geojson`
- `hengzhou_reference_grid_final_v01.tif`
- `study_area_mask_final_v01.tif`
- `hengzhou_grid_1km_final_v01.gpkg`
- `hengzhou_stack_final_v01.tif`
- `permanent_water_final_v01.tif`
- `hengzhou_input_metadata.json`

要求：

- 所有栅格具有相同CRS、Transform、Width和Height。
- input_metadata说明通道顺序、日期、轨道、极化和预处理。
- 全`-9999`参考栅格仅定义空间几何；研究区有效范围必须读取`study_area_mask_final_v01.tif == 1`。
- 1 km网格固定为3690格，`grid_id`按行优先连续编号为`G000001`至`G003690`。

## 2. A交付B：淹没识别结果

### flood_probability.tif

- 类型：float32。
- 范围：0至1。
- NoData：-9999。

### flood_mask.tif

- 类型：uint8。
- 0：非新增淹没。
- 1：新增淹没。
- 255：NoData。

### uncertainty.tif

- 类型：float32。
- 具体含义写入model_metadata.json。

### model_metadata.json

至少包含：

- model_version
- input_channels
- threshold
- random_seed
- validation_iou
- validation_dice
- area_relative_error
- uncertainty_definition
- source_image_dates
- git_commit

## 3. B交付C：正式评估网格

权威空间文件为assessment_grid.gpkg，同时提供assessment_grid.csv。

两者必须沿用冻结网格的几何与`grid_id`，不得在运行时重新生成`G0000_0000`形式的编号。

必需字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| grid_id | string | 唯一编号 |
| center_x | float | 投影坐标 |
| center_y | float | 投影坐标 |
| grid_area_km2 | float | 网格面积 |
| flood_area_km2 | float | 新增淹没面积 |
| flood_ratio | float | 0至1 |
| mean_flood_prob | float | 平均淹没概率 |
| mean_uncertainty | float | 平均不确定性 |
| intensity_score | float | 洪灾强度分数 |
| intensity_class | string | 一般、较强、强、极强 |
| intensity_confidence | float | 0至1 |
| data_status | string | mock、candidate或final |

成员C补充：

- affected_population
- affected_buildings
- flooded_road_km
- affected_facilities
- accessibility_score
- risk_score
- demand_score

## 4. C的优化输出

candidate_sites.csv必须包含：

- site_id
- site_type
- x
- y
- capacity
- safe_flag
- flood_prob
- uncertainty
- elevation_m
- road_connected

selected_sites.csv增加：

- selected
- assigned_demand
- covered_population
- mean_travel_time_min
- max_travel_time_min
- utilization_ratio

service_assignment.csv必须包含：

- grid_id
- site_id
- travel_time_min
- assigned_demand
- covered

## 5. 状态和版本

文件名包含状态和版本，例如：

    hengzhou_flood_probability_candidate_v02.tif

禁止覆盖final结果。正式变更必须增加版本号并填写docs/change_log.md。
