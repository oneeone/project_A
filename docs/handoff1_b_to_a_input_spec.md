# 交接一（B→A）：横州市模型输入规格

本文件由成员A编写，冻结成员B交给成员A的全部输入要求。凡与 `docs/contracts.md` 第1节冲突处，以本文件为准：本文件的文件名、波段顺序、数值标度已按 `并行工作流程图与交付清单.md` 的最高优先级对齐。

## 1. 当前状态

| 项 | 状态（2026-11-28 核查） |
|---|---|
| `origin/member-b` 头部提交 | `50d6dbe`（11-27 23:41，"add flood intensity assessment pipeline"） |
| 分支内 `.tif` / `.gpkg` / `.npz` 数量 | 0 |
| 分支内含 `hengzhou` 路径数量 | 0 |
| 参考网格 | 未冻结 |
| 影像栈 | 未生成 |

原因见 `docs/member_b_runbook.md` 第8节：横州市行政边界、同轨Sentinel-1灾前/灾中、灾前Sentinel-2、DEM、JRC裁剪结果均未取得，因此参考栅格与输入栈无法冻结。

因此本交接**当前处于"合同已冻结、交付物为零"状态**。第7节给出了一个不依赖任何卫星影像的最小可开工集合，建议优先执行。

## 2. 交付清单

| 优先级 | 文件名（逐字） | 内容 | 缺失后果 |
|---|---|---|---|
| P0 | `hengzhou_reference_grid_final_v01.tif` | 参考网格，允许全 NoData | 无法确定输出几何，A 的推理完全无法开始 |
| P1 | `hengzhou_stack_final_v01.tif` | 6 波段推理输入栈 | 无法执行推理 |
| P1 | `permanent_water_final_v01.tif` | 永久水体二值掩膜 | 横州新增淹没面积系统性高估 |
| P1 | `hengzhou_input_metadata.json` | 波段、标度、日期、产品ID | 论文数据来源表无法填写；标度错则整幅结果作废 |

四个文件必须来自同一套网格与同一批影像。禁止"参考网格一版、影像栈另一版"。

## 3. 参考网格与空间口径

- 投影 `EPSG:32649`（UTM 49N），分辨率 **10 m**，单像元面积 100 m²。
- 范围按横州市行政边界外包矩形向外取整到 10 m 网格。
- 1 km 评估单元与参考栅格**同原点**，每单元 100×100 像元，`grid_id` 自左向右、自上向下顺序编号，冻结后写入 `assessment_grid` 并同步给成员C。
- 连续量重采样用双线性，分类量与掩膜用最近邻。
- 浮点 NoData `-9999`，掩膜 NoData `255`。
- **冻结规则**：参考网格一经交付不得静默修改。确需修改必须提升版本号（`v02`），写入 `docs/change_log.md`，并由 A 重新跑一遍推理；不得只改栅格不通知。

## 4. 影像栈波段与数值标度

### 4.1 波段顺序（固定 6 波段）

| 波段 | 名称 | 类型 | 说明 |
|---|---|---|---|
| 1 | `after_sar_uint8` | uint8 | 灾后 SAR，模型实际使用 |
| 2 | `before_red` | uint8 | 灾前光学 红 |
| 3 | `before_green` | uint8 | 灾前光学 绿 |
| 4 | `before_blue` | uint8 | 灾前光学 蓝 |
| 5 | `before_nir` | uint8 | 灾前光学 近红外（第四通道） |
| 6 | `after_sar_db` | float32 | 原始 dB，仅备份，模型不使用 |

模型输入为前 5 个波段，顺序不可调换。第 6 波段是 A 侧复核映射关系的唯一依据，必须保留。

### 4.2 SAR 数值标度（最容易出错的一步）

模型在 GF-3 的 8 位拉伸图上训练，而主办方未公布 GF-3 的拉伸参数，无法反推。因此横州一侧采用**分位匹配**，而不是猜一个固定 dB 区间：

```text
在有效像元上取 dB 的 1% 分位 p1 与 99% 分位 p99
uint8 = clip( round( (dB - p1) / (p99 - p1) * 255 ), 0, 255 )
```

`p1`、`p99` 必须写入 `input_metadata.input_scaling.sar_db_percentiles`。若推理结果明显异常，A 会用第 6 波段重新映射，不需要二次索取数据。

### 4.3 光学数值标度与第四通道

Sentinel-2 L2A 反射率范围 0–10000，映射到 8 位：

```text
uint8 = clip( round( reflectance / 10000 * 255 ), 0, 255 )
```

写 `input_scaling.optical = {"gain": 0.0255, "offset": 0}`。

第四通道使用 **B08（NIR）**。附件中该通道主办方未确认为 NIR，合同与论文一律写"辅助光谱通道"，模型是按"一个额外光谱通道"学习的；因此交付时必须确认第 5 波段确实是 B08，不得用 SCL、气溶胶或云掩膜波段顶替。

### 4.4 NoData 与禁止操作

- 影像栈中 NoData 像元保持原值并置 `-9999`，**不得用 0 填充**：0 会被模型当作极暗水体，直接产生假淹没。
- 不做任何形态学处理（平滑、去噪、填补空洞、边缘增强）。
- 不改变像元对齐，不做裁剪拼接以外的重采样。

## 5. 永久水体

- 数据源：JRC Global Surface Water occurrence（或等效且可说明许可的产品）。
- 输出为二值：`1` 永久水体，`0` 非永久水体，`255` NoData。
- occurrence 阈值必须写进 `input_metadata.permanent_water_threshold`，并在论文"数据来源"表中给出产品版本与获取日期。
- A 在导出概率与掩膜时会把永久水体像元置为 `0`，但**不重算面积**；面积口径仍以成员B的 `area.py` 为准。

## 6. `hengzhou_input_metadata.json` 字段

```json
{
  "crs": "EPSG:32649",
  "resolution_m": 10,
  "transform": [10.0, 0.0, 0.0, 0.0, -10.0, 0.0],
  "width": 0,
  "height": 0,
  "nodata": {"float": -9999, "mask": 255},
  "band_order": [
    "after_sar_uint8", "before_red", "before_green",
    "before_blue", "before_nir", "after_sar_db"
  ],
  "input_scaling": {
    "sar_db_percentiles": [0.0, 0.0],
    "sar_uint8_formula": "clip(round((dB - p1) / (p99 - p1) * 255), 0, 255)",
    "optical": {"gain": 0.0255, "offset": 0}
  },
  "source_image_dates": {"sar_before": "", "sar_after": "", "optical_before": ""},
  "permanent_water_threshold": 0.0,
  "grid_1km": {"origin_m": [0.0, 0.0], "px_per_cell": 100},
  "provenance": {
    "sar_product_id": "", "optical_product_id": "",
    "orbit_direction": "", "relative_orbit": "", "polarization": ""
  }
}
```

`source_image_dates` 与 `provenance` 是论文数据来源表的唯一依据，不得留空。`width`、`height`、`transform` 必须与 `hengzhou_reference_grid_final_v01.tif` 逐字段相同。

## 7. 最小可开工集合（建议立即执行）

完整交付依赖 Sentinel-1/2、DEM 与 JRC，短期内到不齐。但**参考网格不需要任何卫星影像**，只需要行政边界：

1. 用边界外包矩形生成一张 10 m 全 `-9999` 的空栅格 `hengzhou_reference_grid_final_v01.tif`；
2. 生成 `hengzhou_grid_1km.json`（`grid_id`、`center_x`、`center_y`、`grid_area_km2`，共 4 个字段即可）；
3. 先提交这两个文件（json 进仓库，tif 走共享目录）。

A 收到后即可完成：滑窗推理框架、边缘 padding 策略、四件套导出、`scripts/verify_raster_contract.py` 自检、与 `src/member_b/raster_contracts.py` 的交叉校验。影像栈到位后只需替换输入，不再改代码。

## 8. A 侧对应交付（交接二，仅供参考以便 B 排期）

| 文件 | 规格 |
|---|---|
| `hengzhou_flood_probability_candidate_v01.tif` | float32，[0,1]，NoData `-9999` |
| `hengzhou_flood_mask_candidate_v01.tif` | uint8，{0,1,255} |
| `hengzhou_uncertainty_candidate_v01.tif` | float32，三种子概率方差，NoData `-9999` |
| `hengzhou_model_metadata_candidate_v01.json` | 含 `threshold=0.36`、模型版本、`git_commit`、`uncertainty_definition` |

- 概率阈值为 **0.36**，仅在验证集上选定，横州推理不重新调参。
- 三张栅格的 `crs / transform / width / height` 与参考网格逐字段相等（容差 1e-9）。
- 交付前 A 本地跑通 `python scripts/verify_raster_contract.py --dir <输出目录> --ref hengzhou_reference_grid_final_v01.tif`，B 收到后复跑一次，退出码为 0 才进入下一环节。

## 9. 传输与版本

- 仓库 `.gitignore` 全局禁 `*.tif`，栅格走共享目录（网盘或 U 盘），**metadata json、网格 json、校验报告提交进仓库**（`outputs/tables/`）。
- 文件名必须带状态与版本：`candidate` 为候选，`final` 为正式；禁止覆盖 `final` 结果。
- 任何正式变更写入 `docs/change_log.md`，注明负责人与最晚解决时间。

## 10. A 侧接收验收清单

收到交接一后，A 逐项确认：

- [ ] 四个文件齐备，命名与版本正确
- [ ] 参考网格 CRS 为 `EPSG:32649`，分辨率 10 m
- [ ] 三张栅格与参考网格的 `transform / width / height` 完全一致
- [ ] 1 km 网格与参考栅格同原点，`grid_id` 连续无重复
- [ ] 影像栈 6 波段，顺序与第 4.1 节一致
- [ ] `after_sar_db` 存在且数值合理（水体约 −20 dB，建成区约 0 dB）
- [ ] `input_scaling` 中的分位值与实际影像一致
- [ ] 第 5 波段为 B08，且已注明产品ID与日期
- [ ] NoData 为 `-9999`/`255`，无 0 填充
- [ ] `docs/change_log.md` 已登记本次交付

## 11. 需要成员B回复的三件事

1. 参考网格能否先给（哪怕全 NoData），最晚何时；
2. 第 4 节的波段顺序与标度是否同意，特别是第 6 波段保留原始 dB；
3. 第四通道确认为 B08。

## 12. 变更记录

| 日期 | 版本 | 变更 | 负责人 |
|---|---|---|---|
| 2026-11-28 | v01 | 首次冻结：命名对齐交付清单、SAR 分位映射、NoData 与空网格方案 | 成员A |
