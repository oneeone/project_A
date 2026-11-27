# 成员C工作区

负责：

- WorldPop人口；
- OSM建筑、道路和设施；
- 暴露度和救援困难度；
- 综合风险；
- 救援需求；
- 候选救援点；
- 有容量约束的最大覆盖选址；
- 基线比较、场景和敏感性分析；
- 管理建议函。

在B正式网格完成前，使用data/mock中的标准字段开发和测试。正式网格到达后只替换输入。

## 已实现模块

- schemas.py：检查B到C的接口、候选点和分配结果。
- risk_assessment.py：计算暴露度、困难度、综合风险和救援需求。
- candidate_sites.py：候选点去重、安全筛选和默认容量。
- travel_time.py：生成仅用于开发的欧氏距离时间矩阵。
- road_network.py：从OSMnx GraphML计算正式道路时间。
- facility_location.py：SciPy MILP容量约束最大覆盖模型。
- baselines.py：随机、人口优先和风险优先基线。
- scenarios.py：乐观、中性和悲观情景。
- sensitivity.py：救援点数量、时间和容量敏感性。
- visualize.py：风险和救援点快速检查图。

## 立即运行mock流程

在项目根目录执行：

    python scripts/generate_member_c_mock.py --output-dir data/mock/member_c

    python scripts/run_member_c_pipeline.py +      --assessment-grid data/mock/member_c/assessment_grid_mock_v01.csv +      --raw-sites data/mock/member_c/candidate_sites_raw_mock_v01.csv +      --output-dir outputs/member_c_mock +      --allow-mock-euclidean +      --with-sensitivity +      --with-scenarios

PowerShell可以将反斜杠续行改成一行运行。

正式计算时必须提供基于道路网络的travel matrix，并添加--formal。正式模式会拒绝data_status=mock的网格。
