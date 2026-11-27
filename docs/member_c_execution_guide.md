# 成员C执行指南

## 1. 当前已经实现

- B到C接口校验；
- WorldPop概率加权人口统计；
- OSM建筑、道路和设施聚合；
- 洪灾危险性、暴露度、救援困难度和综合风险；
- 候选点去重与安全过滤；
- mock欧氏时间矩阵和正式OSM道路时间矩阵；
- SciPy MILP容量约束最大覆盖模型；
- 随机、人口优先和风险优先基线；
- 乐观、中性和悲观情景；
- 救援点数量、时间限制和容量敏感性；
- 风险和救援点检查图。

## 2. 首次验证

在项目根目录运行：

    python -m unittest discover -s tests -v
    python scripts/validate_project.py

生成mock输入：

    python scripts/generate_member_c_mock.py --output-dir data/mock/member_c

运行完整mock流程：

    python scripts/run_member_c_pipeline.py --assessment-grid data/mock/member_c/assessment_grid_mock_v01.csv --raw-sites data/mock/member_c/candidate_sites_raw_mock_v01.csv --output-dir outputs/member_c_mock --allow-mock-euclidean --with-sensitivity --with-scenarios

mock时间矩阵不能用于论文正式结果。

## 3. 正式数据准备顺序

### 第一步：从B取得

- assessment_grid.gpkg；
- assessment_grid.csv；
- 横州市边界；
- 目标投影；
- DEM或坡度；
- A生成的flood_probability和uncertainty。

### 第二步：下载OSM

    python -m src.member_c.osm_extract --boundary <boundary.gpkg> --output-dir <osm_dir>

### 第三步：计算人口

    python -m src.member_c.worldpop --grid <assessment_grid.gpkg> --population-raster <worldpop.tif> --flood-probability <flood_probability.tif> --output <population.csv>

### 第四步：聚合空间暴露

    python -m src.member_c.spatial_exposure --grid <assessment_grid.gpkg> --buildings <osm_buildings.gpkg> --roads <osm_roads.gpkg> --facilities <osm_facilities.gpkg> --population-csv <population.csv> --output-gpkg <exposure_grid.gpkg> --output-csv <exposure_grid.csv>

### 第五步：准备候选点

    python -m src.member_c.prepare_candidate_sites --facilities <osm_facilities.gpkg> --flood-probability <flood_probability.tif> --uncertainty <uncertainty.tif> --dem <dem.tif> --output-crs EPSG:32649 --output <raw_sites.csv>

再运行安全过滤：

    python -m src.member_c.candidate_sites --input <raw_sites.csv> --output <candidate_sites.csv>

### 第六步：计算正式道路时间

    python -m src.member_c.road_network --graphml <osm_drive.graphml> --grids <exposure_grid.csv> --sites <candidate_sites.csv> --point-crs EPSG:32649 --output <travel_time_matrix.csv>

正式论文若考虑道路中断，应先从道路图中删除与高置信淹没区相交的边，再生成GraphML；当前road_network模块只负责对传入道路图计算最短时间。

### 第七步：运行正式流程

    python scripts/run_member_c_pipeline.py --assessment-grid <exposure_grid.csv> --raw-sites <raw_sites.csv> --travel-matrix <travel_time_matrix.csv> --output-dir <final_output_dir> --formal --with-sensitivity --with-scenarios

formal模式会拒绝任何data_status为mock的输入。

## 4. 正式输出

- risk_grid.csv；
- candidate_sites.csv；
- travel_time_matrix.csv；
- selected_sites.csv；
- service_assignment.csv；
- optimization_metrics.json；
- baseline_comparison.csv；
- sensitivity_analysis.csv；
- scenario_analysis.csv；
- risk_and_selected_sites.png。

正式结果需要另外使用GeoPandas或QGIS制作带行政边界、道路、比例尺和指北针的论文地图。

## 5. 当前默认参数

- 最多8个救援点；
- 最大服务时间30分钟；
- 默认容量1000人；
- 安全淹没概率小于0.20；
- 不确定性不高于候选点75%分位数；
- 时间惩罚权重0.05；
- 随机基线重复100次；
- 随机种子2026。

敏感性组合：

- 救援点5、8、10个；
- 最大服务时间20、30、40分钟；
- 容量500、1000、1500人。

## 6. 正式运行前必须确认

- B的grid_id已经冻结；
- 所有坐标为同一投影；
- 候选点坐标单位为米；
- WorldPop人口总量重采样前后基本守恒；
- 正式道路矩阵不是mock欧氏距离；
- final结果不包含mock状态；
- 默认容量等假设已在论文中说明；
- 摘要只引用final输出。
