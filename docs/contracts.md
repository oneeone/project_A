# 成员间接口协议

## 1. B交付A：横州市模型输入

必须包含：

- hengzhou_reference_grid.tif
- hengzhou_input_stack.tif
- permanent_water.tif
- input_metadata.json

要求：

- 所有栅格具有相同CRS、Transform、Width和Height。
- input_metadata说明通道顺序、日期、轨道、极化和预处理。

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

## 6. A交付B的强制校验

交付前A必须本地跑通，B收到后复跑一次；退出码为0才允许进入下一环节。

    python scripts/verify_raster_contract.py --dir <A输出目录> --ref hengzhou_reference_grid.tif

脚本检查：

1. 四个文件齐备（probability、mask、uncertainty、metadata）；
2. CRS、transform、width、height、nodata 与参考栅格完全一致；
3. dtype 为 float32、uint8、float32；
4. 概率值域0至1，不确定性0至0.25，掩膜只含0、1、255；
5. 无 NaN 或 Inf；
6. mask等于1处 probability 大于等于 threshold 的比例不低于99%；
7. metadata 必填字段齐全。

### 6.1 metadata 追加必填字段

在本文第2节清单之外追加：

| 字段 | 含义 |
|---|---|
| crs | 输出栅格坐标系，固定 EPSG:32649 |
| resolution_m | 输出分辨率，固定 10 |
| label_semantics | newly_inundated_only 或 post_event_water_including_permanent |
| ensemble_members | 集成成员数，不小于3 |
| split_rule | 数据划分方式标识 |
| calibration.method | none、platt 或 isotonic |
| calibration.brier | Brier 分数 |
| calibration.ece | 期望校准误差 |

### 6.2 概率必须校准

成员C用 flood_prob 小于 0.20 判定安全候选点、用概率加权人口估计受灾人口，这两处都默认A输出的是校准概率，而不是原始模型打分。
因此 final 版本必须同时提供 reliability.csv（列：bin、mean_pred、frac_pos、count）与 calibration 指标。

### 6.3 标签语义提醒

标签语义按 docs/frozen_constraints.md 第2节暂定为新增淹没。A在首个实验必须做一次核验：
若标签值为1的区域在灾前光学影像中已是大片水体，说明标签包含永久水体，此时必须立即写入 docs/change_log.md 并通知B调整差集逻辑。

### 6.4 分块划分

训练、验证、测试必须按空间块划分（见 configs/spatial.yaml 的 block_split_mandatory）。
附件约90个连续块，相邻 patch 中心距 2.09 km，存在 89 处超过 10 km 的块间跳变；把相邻 patch 随机分到训练与测试会让 IoU 虚高。
划分结果写入 provided_split_manifest_v01.json，三名成员共用同一份。
