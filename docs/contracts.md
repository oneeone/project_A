# 成员间接口协议

## 1. B交付A：横州市模型输入

完整规格见 `docs/handoff1_b_to_a_input_spec.md`（文件名、波段顺序、数值标度、NoData、空网格方案以该文件为准）。

必须包含（带状态与版本后缀）：

- hengzhou_reference_grid_final_v01.tif
- hengzhou_stack_final_v01.tif
- permanent_water_final_v01.tif
- hengzhou_input_metadata.json

要求：

- 所有栅格具有相同CRS（EPSG:32649）、Transform、Width和Height，分辨率10 m。
- input_metadata说明通道顺序、数值标度、日期、轨道、极化和预处理。
- 影像栈固定6波段，第6波段为原始dB备份；NoData用-9999，不得用0填充。

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

### 6.3 标签语义（已核验，2026-11-27 定稿）

核验方式：146 个分层抽样 patch，用灾前光学影像做对照，脚本 src/member_a/prepare/check_label_semantics.py。

| 指标 | 淹没区(label=1) | 背景(label=0) |
|---|---|---|
| 灾前光学亮度 | 73.7 | 63.5 |
| 灾前即为暗水体比例 | 0.255 | 0.419 |
| 灾后 SAR 亮度 | 48.1 | 130.8 |

结论：label_semantics = newly_inundated_only，定稿，不再讨论。

三条证据：淹没像素的灾前亮度反而高于背景（73.7 > 63.5）；只有 25.5% 的淹没像素在灾前是暗水体；灾后 SAR 亮度比 0.368 与水体一致。
因此 B 不要再对 A 的 mask 做一次“减去灾前水体”的差集，否则会重复扣除；直接沿用标签口径即可。

### 6.4 分块划分（已诊断，2026-11-27 定稿）

训练、验证、测试必须按空间块划分（见 configs/spatial.yaml 的 block_split_mandatory）。

实测结构（修正“约 90 个连续块”的说法）：1000 个 patch 落在 5 片互不相连的镶嵌上，片大小 512 / 450 / 16 / 12 / 10；同片内瓦片间距 2.078 km × 2.359 km，瓦片无重叠。文件按行优先顺序编号，所以“相邻 patch 中心距 2.09 km”只是同一片镶嵌内的行内步长，不是 90 个独立区域。

做法：把每片镶嵌按 4×4 瓦片切成子块（8.3 km × 9.4 km），共 90 个子块，再按块水体占比分层分配，保证三个 split 的洪水分层一致：

- train 692 / val 151 / test 157（69% / 15% / 16%）
- 各 split 水体占比均值 0.196 / 0.144 / 0.209
- 块间无重叠，训练块与测试块中心最近距 5.7 km

清单：outputs/tables/provided_split_manifest_v03.json，三名成员共用，禁止各自重切。
