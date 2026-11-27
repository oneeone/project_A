# 2026 APMCM Problem A

项目主题：基于多源遥感融合的洪灾识别、区域评估与应急救援优化。

## 开始之前

三名成员先共同阅读并确认：

1. docs/00_START_HERE.md
2. docs/frozen_constraints.md
3. docs/definitions.md
4. docs/contracts.md
5. docs/workflow_3days.md

未完成启动检查前，不开始正式训练、正式面积计算或正式优化。

## 成员职责

| 成员 | 负责内容 | 主要交付 |
|---|---|---|
| A | 问题1、问题2识别模型、横州市推理 | 淹没概率、掩膜、不确定性 |
| B | 横州市数据、强度分类、问题3 | 面积、强度、评估网格 |
| C | 问题4、问题5 | 风险、需求、救援点选址 |

## 必要交接链

    B生成横州市正式影像栈
      → A生成正式淹没结果
      → B生成正式面积与强度
      → C生成正式风险与选址

交接前，各成员使用 mock 或 candidate 数据独立开发，不等待上游。

## 结果状态

- mock：模拟或占位结果，不能进入最终摘要。
- candidate：已运行但尚未冻结。
- final：已检查、可进入论文。

## 快速开始

1. 复制 configs/paths.example.yaml 为 configs/paths.local.yaml。
2. 修改个人本地数据路径。
3. 创建 Python 3.10 或 3.11 环境。
4. 安装 requirements.txt。
5. 运行 python scripts/validate_project.py。
6. 每个人从自己的分支和 src/member_x 目录开始工作。

## 数据管理

原始遥感影像、大型GeoTIFF、模型权重和中间数组不进入Git。data/目录只保存说明和目录占位，大文件通过统一网盘共享。

## 当前附件审计摘要

- 1000组完整配对样本，编号1001至2000。
- 光学：256×256×4，uint8，WGS 84。
- SAR：256×256×1，uint8，GF-3 HH，不是VV/VH，也不是校准dB值。
- 标签：256×256，uint8，实际取值仅0和1。
- 标签1像素占18.99%，按题意和影像关系解释为新增淹没目标。
- 单像素面积约74.78至75.39平方米，中位数约74.93平方米。
- 数据来自两个主要地理区域，不允许随机切片划分训练和测试。

详细信息见 docs/provided_data_audit.md。
