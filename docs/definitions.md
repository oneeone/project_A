# 统一定义

## 1. 水体与面积

灾前水体集合记为 W_before，灾中水体集合记为 W_during。

新增淹没区：

    W_new = W_during \ W_before

新增淹没面积：

    A_new = area(W_new)

消退水体：

    W_receded = W_before \ W_during

消退面积：

    A_receded = area(W_receded)

净增加面积：

    Delta_A_net = A_new - A_receded

区域新增淹没比例：

    p_region = A_new / A_study

有效陆地新增淹没比例：

    p_land = A_new / (A_study - A_permanent_water)

论文必须明确使用哪个比例。

## 2. 概率与不确定性

- flood_probability：像素属于新增淹没区的预测概率，范围0至1。
- flood_mask：根据验证集阈值生成的二值结果。
- uncertainty：模型集成预测的方差或标准差，具体定义写入模型元数据。
- 二值化阈值只由验证集选择。

## 3. 洪灾强度与综合风险

洪灾强度描述洪水本身，核心变量为新增淹没面积和比例。

综合风险同时考虑：

- 洪灾危险性；
- 人口和设施暴露；
- 救援困难度。

洪灾强度不能与综合风险混用。

## 4. 救援需求

demand_score必须明确属于以下一种：

1. 0至1相对需求分数；
2. 待服务人口；
3. 实际物资需求量。

基准优化模型不得混用不同量纲。

## 5. 单位

| 变量 | 单位 |
|---|---|
| 面积 | km² |
| 距离 | km |
| 道路长度 | km |
| 高程 | m |
| 救援时间 | min |
| 人口 | 人 |
| 概率和比例 | 0至1 |
