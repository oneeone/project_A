# 三日流程与强制产出

## 第一天：打通基础流程

### 成员A，独立

- 附件读取、配对和预处理。
- 空间划分训练、验证和测试。
- RF或XGBoost基线。
- U-Net初版。
- 指标和问题1初稿。

强制产出：

- provided_split_manifest_v01.json
- q1_baseline_metrics_candidate_v01.csv
- provided_flood_mask_candidate_v01.tif
- unet_v0_candidate.pth
- q1_draft

### 成员B，独立

- 下载横州市边界、Sentinel、DEM和永久水体。
- 生成参考栅格和输入影像栈。
- 使用SAR阈值法生成临时洪水图。
- 完成面积和强度程序。

强制产出：

- hengzhou_reference_grid_final_v01.tif
- hengzhou_stack_candidate_v01.tif
- temporary_flood_mask_mock_v01.tif
- intensity_grid_mock_v01.gpkg
- q3_draft

### 成员C，独立

- 下载人口、道路、建筑和设施。
- 建立1 km网格和候选点。
- 完成风险与选址程序。
- 使用mock数据完成一次求解。

强制产出：

- assessment_grid_mock_v01.csv
- candidate_sites_candidate_v01.csv
- selected_sites_mock_v01.csv
- optimization_metrics_mock_v01.csv
- q4_q5_draft

第一天结束条件：A有预测、B有临时洪水、C有临时选址，五问均有方法初稿。

## 第二天：正式交接

上午三人继续独立：

- A：融合、消融、集成和不确定性。
- B：正式横州市影像栈、永久水体和强度程序。
- C：暴露数据、道路矩阵、优化对比和场景程序。

必要串行交接：

    B正式影像栈
      → A横州市推理
      → B正式面积与强度
      → C正式风险与选址

最晚节点：

- T+32小时：B交付A。
- T+36小时：A交付B。
- T+40小时：B交付C。
- T+44小时：候选结果冻结。

第二天结束条件：五问候选结果贯通，论文完成70%以上，主要方法不再更换。

## 第三天：最终结果与论文

最终重算链：

    A final洪水结果
      → B final面积与网格
      → C final风险与选址

独立完善：

- A：最终指标、消融、误差和问题1、2正文。
- B：最终面积、强度、地图和问题3正文。
- C：敏感性、场景、建议函和问题4、5正文。

节点：

- T+56小时：所有数字冻结。
- T+62小时：论文全文完成。
- T+67小时：停止实验，开始提交检查。
- T+72小时：完成提交和备份。
