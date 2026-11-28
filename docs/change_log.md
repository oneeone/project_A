# 冻结项变更记录

任何已冻结内容发生变化时，必须新增记录。

| 时间 | 修改人 | 冻结项 | 原版本 | 新版本 | 修改原因 | 受影响任务 | 需要重跑 |
|---|---|---|---|---|---|---|---|
| 2026-11-27 | A | 标签含义 | 暂定 newly_inundated_only | 核验后固定 newly_inundated_only | 146 patch 灾前光学对照：淹没像素灾前亮度 73.7 > 背景 63.5，仅 25.5% 灾前是暗水体 | B 的差集逻辑、C 的面积口径 | 否 |
| 2026-11-27 | A | 测试集 | 无清单 | provided_split_manifest_v03.json | 实测只有 5 片镶嵌，原“约 90 个连续块”是行内步长误读；改为 4x4 瓦片子块共 90 块 | 三人全部实验 | 是（首个实验即用 v03） |
| 2026-11-27 | A | 二值化阈值 | 未定 | 0.46（isotonic 校准后概率空间，仅 val 选定） | 校准后 val IoU 最高 0.8081；raw 空间最优 0.38 但概率语义不可解释；面积准则 0.34 在 test 上恶化到 +16.3%，不可跨区迁移 | B 的阈值敏感性分析、C 的 0.20 安全判定 | 否（首次冻结） |
| 2026-11-27 | A | 正式文件接口（model metadata 字段） | 仅满足校验器 15 项必填 | 补齐 contracts.md §2 的 10 项：input_channels / random_seed / validation_iou / validation_dice / area_relative_error / uncertainty_definition / source_image_dates / git_commit，evaluation 扩为 iou·dice·f1·precision·recall·area_abs_error_km2·area_rel_error·val 子块 | B 读 metadata 时拿不到验证指标与通道顺序，无法判断是否可直接复用 | 否（只增字段，已有字段含义不变；area_relative_error 取 test，另设 area_relative_error_split 标注） |

| 2026-11-28 | A | 交接一输入接口（文件名/波段/标度） | contracts.md §1 无版本名、未定义标度 | handoff1_b_to_a_input_spec.md v01：4 文件带 final_v01 后缀；6 波段（含原始 dB 备份）；SAR 按 1%/99% 分位映射到 uint8；第四通道固定 B08；NoData -9999/255 | 原接口无法确定横州推理输出尺寸与数值语义，B 按旧名交付会直接对不上 | B 的预处理与输入栈制作、A 的横州推理 | 否（首次冻结） |
| 2026-11-28 | A | metadata 字段（新增 verification） | 无自检记录 | 新增 verification 块：a_verifier.exit + b_contract（pass / skipped / 问题列表）+ checked_at，随四件套一起交付 | B 读 metadata 即可知道产物是否过了双道契约校验，不必自行猜测 | B 的输入验收与入库 | 否（只增字段） |

## 变更原则

禁止静默修改以下内容：

- CRS和参考栅格；
- 分辨率和网格大小；
- 标签含义；
- 测试集；
- 二值化阈值；
- 洪灾强度方法；
- 风险定义；
- 优化目标与约束；
- 正式文件接口。
