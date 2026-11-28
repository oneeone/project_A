# 交接一（B→A）：横州市模型输入规格

本文件由成员A编写，冻结成员B交给成员A的全部输入要求与执行步骤。凡与 `docs/contracts.md` 第1节冲突处，以本文件为准：本文件的文件名、波段顺序、数值标度已按 `并行工作流程图与交付清单.md` 的最高优先级对齐。

## 0. 一分钟版

1. **今天**：只做一件事——用横州市行政边界生成一张 10 m 全 `-9999` 空参考栅格 + `hengzhou_grid_1km.json`，zip 后提交到 `member-b`，然后通知 A。不需要任何卫星影像。
2. **同时回 A 三句话**：网格最晚何时给；波段顺序与标度是否同意（第 6 波段保留原始 dB）；第四通道确认为 B08。
3. **之后**：按第 12 节的 P1/P2 下载数据、组 6 波段栈、自检、交付。

A 拿到空网格当天即可把横州推理框架、边缘 padding、四件套导出与契约校验全部写完并自测；影像栈到位后只需替换输入，不再改代码。

## 1. 当前状态

| 项 | 状态（2026-11-29 核查） |
|---|---|
| `origin/member-b` 头部提交 | `cb18cf4`（11-29 00:09，"Add Hengzhou raster inputs via Git LFS"，已并入 `main` `edc2017`） |
| P0 空间契约 | **已交付**（`a0e9713`）：参考网格、`study_area_mask`、行政边界、3690 格 GPKG、1 km 表、input metadata |
| 1 km 网格 | **已冻结**：`grid_id` 固定 `G000001`–`G003690`，每格附 `row_start/row_stop/column_start/column_stop` 冻结窗口；A 侧已逐格对账，掩膜像元数 × 100 m² 与 `grid_area_km2` 偏差为零 |
| P1 影像栈与永久水体 | **已入库**：Git LFS 提交 `data/processed/hengzhou_stack_final_v01.tif`（481.6 MB）与 `permanent_water_final_v01.tif`（233 KB），SHA-256 与 B 交付文档一致 |
| A 侧状态 | 推理链路、四件套导出、双契约校验已跑通；`hengzhou_mock_v01` 已交付 C 联调，P1 落地后即可出真 `candidate` |

原“交付物为零”的判断已作废；第 12 节 P0/P1 步骤均已执行完成，剩余工作是 A 侧推理与交接二。

## 2. 交付清单

| 优先级 | 文件名（逐字） | 内容 | 交付渠道 | 缺失后果 |
|---|---|---|---|---|
| P0 | `hengzhou_reference_grid_final_v01.tif` | 参考网格，整幅全 NoData，只定义几何 | `hengzhou_reference_grid_final_v01.zip` 进仓库 | 无法确定输出几何，A 的推理完全无法开始 |
| P0 | `study_area_mask_final_v01.tif` | 研究区有效范围（`1`=研究区，`0`=区外，`255`=NoData） | 同上 zip | **有效范围无法界定**，会把区外像元当结果导出 |
| P0 | `hengzhou_boundary_final_v01.geojson` | 行政边界（EPSG:4326，含来源与许可证） | 同上 zip | 论文无法交代研究区来源 |
| P0 | `hengzhou_grid_1km_final_v01.gpkg` | 3690 格评估单元（含冻结像素窗口与逐格面积） | 同上 zip | 强度分级与 C 的评估网格无法建立 |
| P0 | `hengzhou_grid_1km.json` | 1 km 评估单元表（`grid_id`/中心/面积） | 提交 `member-b` | C 侧对不齐格号 |
| P1 | `hengzhou_stack_final_v01.tif` | 6 波段推理输入栈 | Git LFS `data/processed/` | 无法执行推理 |
| P1 | `permanent_water_final_v01.tif` | 永久水体二值掩膜 | Git LFS `data/processed/` | 横州新增淹没面积系统性高估（永久水体 51.8594 km²） |
| P1 | `hengzhou_input_metadata.json` | 波段、标度、日期、产品ID | 提交 `member-b` | 论文数据来源表无法填写；标度错则整幅结果作废 |

所有栅格必须来自同一套网格与同一批影像。禁止“参考网格一版、影像栈另一版”。

## 3. 交付渠道分工

仓库 `.gitignore` 全局禁 `*.tif`，所以必须分清"什么进仓库、什么走共享目录、什么只需一句话回复"。

### 3.1 必须提交到 `member-b` 分支（可追溯，A 才能开工）

| 文件 | 作用 |
|---|---|
| `hengzhou_grid_1km.json` | 1 km 网格原点与 `grid_id`，C 也要用 |
| `hengzhou_input_metadata.json` | 波段顺序、分位标度、影像日期、产品 ID |
| `docs/data_catalog.csv`（更新） | 边界、S1、S2、DEM、JRC 的产品 ID 与日期 |
| `docs/change_log.md`（更新） | 本次交付登记 |
| `outputs/samples/hengzhou_reference_grid_final_v01.zip` | **空参考栅格压缩包**，见 3.2 |

### 3.2 参考网格建议 zip 进仓库（绕过网盘）

全 `-9999` 的空栅格用 LZW/DEFLATE 压缩后通常只有几十 KB，非常适合随分支交付：

```text
outputs/samples/hengzhou_reference_grid_final_v01.zip   # 内含 tif
```

`*.zip` 不受 `.gitignore` 影响。这样 A `git pull` 就能拿到网格开始写代码，不需要等共享目录或网盘。影像栈（数百 MB 至 GB）再走共享目录。

### 3.3 只放共享目录（不要进仓库）

| 文件 | 约体积 |
|---|---|
| `hengzhou_stack_final_v01.tif` | 数百 MB |
| `permanent_water_final_v01.tif` | 数十 MB |
| 非空参考网格（若最终含数据） | 数十 MB |

放好后需把**共享目录地址**回复给 A。

### 3.4 只需回复，不需要任何文件

见第 11 节的三条确认。

### 3.5 状态与版本命名

`candidate` 为候选，`final` 为正式；文件名必须带状态与版本后缀，禁止覆盖 `final` 结果。任何正式变更写入 `docs/change_log.md`，注明负责人与最晚解决时间。

## 4. 参考网格与空间口径

- 投影 `EPSG:32649`（UTM 49N），分辨率 **10 m**，单像元面积 100 m²。
- 范围按横州市行政边界外包矩形向外取整到 10 m 网格。
- 1 km 评估单元与参考栅格**同原点**，每单元 100×100 像元，`grid_id` 自左向右、自上向下顺序编号，冻结后写入 `assessment_grid` 并同步给成员C。
- 连续量重采样用双线性，分类量与掩膜用最近邻。
- 浮点 NoData `-9999`，掩膜 NoData `255`。
- **冻结规则**：参考网格一经交付不得静默修改。确需修改必须提升版本号（`v02`），写入 `docs/change_log.md`，并由 A 重新跑一遍推理；不得只改栅格不通知。
- **有效范围（B 侧 11-28 冻结，v03 纳入本规格）**：参考栅格整幅 `-9999`，**只定义空间几何**；研究区有效范围必须读 `study_area_mask_final_v01.tif == 1`。A 侧口径：`valid = 影像栈有效 ∩ study_area_mask == 1`，掩膜外一律 NoData，**不得**用参考栅格或影像有效值推断范围。
- **1 km 网格冻结**：`grid_id` 固定 `G000001`–`G003690`，每格带 `row_start/row_stop/column_start/column_stop`；贴边格按真实裁剪计面积（例 `0.3708 / 0.5054 km²`），`Σgrid_area_km2 = 3452.8116 km²` 与掩膜面积逐格对账相等。
- **研究区基准数**：掩膜内 34,528,116 像元（3452.812 km²）、掩膜外 26,497,807 像元；`optical_unfilled_pixel_count = 8` 属源数据缺测，A 侧覆盖率门限据此定为“区内漏覆盖 > 0.1% 才判致命”。

## 5. 影像栈波段与数值标度

### 5.1 波段顺序（固定 6 波段）

| 波段 | 名称 | 类型 | 说明 |
|---|---|---|---|
| 1 | `after_sar_uint8` | uint8 | 灾后 SAR，模型实际使用 |
| 2 | `before_red` | uint8 | 灾前光学 红 |
| 3 | `before_green` | uint8 | 灾前光学 绿 |
| 4 | `before_blue` | uint8 | 灾前光学 蓝 |
| 5 | `before_nir` | uint8 | 灾前光学 近红外（第四通道） |
| 6 | `after_sar_db` | float32 | 原始 dB，仅备份，模型不使用 |

模型输入为前 5 个波段，顺序不可调换。第 6 波段是 A 侧复核映射关系的唯一依据，必须保留。

### 5.2 SAR 数值标度（最容易出错的一步）

模型在 GF-3 的 8 位拉伸图上训练，而主办方未公布 GF-3 的拉伸参数，无法反推。因此横州一侧采用**分位匹配**，而不是猜一个固定 dB 区间：

```text
在有效像元上取 dB 的 1% 分位 p1 与 99% 分位 p99
uint8 = clip( round( (dB - p1) / (p99 - p1) * 255 ), 0, 255 )
```

`p1`、`p99` 必须写入 `input_metadata.input_scaling.sar_db_percentiles`。若推理结果明显异常，A 会用第 6 波段重新映射，不需要二次索取数据。

### 5.3 光学数值标度与第四通道

Sentinel-2 L2A 反射率范围 0–10000，映射到 8 位：

```text
uint8 = clip( round( reflectance / 10000 * 255 ), 0, 255 )
```

写 `input_scaling.optical = {"gain": 0.0255, "offset": 0}`。

第四通道使用 **B08（NIR）**。附件中该通道主办方未确认为 NIR，合同与论文一律写"辅助光谱通道"，模型是按"一个额外光谱通道"学习的；因此交付时必须确认第 5 波段确实是 B08，不得用 SCL、气溶胶或云掩膜波段顶替。

### 5.4 NoData 与禁止操作

- 影像栈中 NoData 像元保持原值并置 `-9999`，**不得用 0 填充**：0 会被模型当作极暗水体，直接产生假淹没。
- 不做任何形态学处理（平滑、去噪、填补空洞、边缘增强）。
- 不改变像元对齐，不做裁剪拼接以外的重采样。

## 6. 永久水体

- 数据源：JRC Global Surface Water occurrence（或等效且可说明许可的产品）。
- 输出为二值：`1` 永久水体，`0` 非永久水体，`255` NoData。
- occurrence 阈值必须写进 `input_metadata.permanent_water_threshold`，并在论文"数据来源"表中给出产品版本与获取日期。
- A 在导出概率与掩膜时会把永久水体像元置为 `0`，但**不重算面积**；面积口径仍以成员B的 `area.py` 为准。

## 7. `hengzhou_input_metadata.json` 字段

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

未取得的数据填 `"pending"` 并在 `docs/change_log.md` 注明最晚补齐时间，不允许编造。

## 8. 最小可开工集合（建议立即执行）

完整交付依赖 Sentinel-1/2、DEM 与 JRC，短期内到不齐。但**参考网格不需要任何卫星影像**，只需要行政边界：

1. 用边界外包矩形生成一张 10 m 全 `-9999` 的空栅格 `hengzhou_reference_grid_final_v01.tif`；
2. 生成 `hengzhou_grid_1km.json`（`grid_id`、`center_x`、`center_y`、`grid_area_km2`，共 4 个字段即可）；
3. 空栅格 zip 后与 json 一起提交到 `member-b`，然后通知 A。

A 收到后即可完成：滑窗推理框架、边缘 padding 策略、四件套导出、`scripts/verify_raster_contract.py` 自检、与 `src/member_b/raster_contracts.py` 的交叉校验。影像栈到位后只需替换输入，不再改代码。

## 9. A 侧对应交付（交接二，仅供参考以便 B 排期）

| 文件 | 规格 |
|---|---|
| `hengzhou_flood_probability_candidate_v01.tif` | float32，[0,1]，NoData `-9999` |
| `hengzhou_flood_mask_candidate_v01.tif` | uint8，{0,1,255} |
| `hengzhou_uncertainty_candidate_v01.tif` | float32，三种子概率方差，NoData `-9999` |
| `hengzhou_model_metadata_candidate_v01.json` | 含 `threshold=0.36`、模型版本、`git_commit`、`uncertainty_definition` |

- 概率阈值为 **0.36**，仅在验证集上选定，横州推理不重新调参。
- 三张栅格的 `crs / transform / width / height` 与参考网格逐字段相等（容差 1e-9）。
- 交付前 A 本地跑通 `python scripts/verify_raster_contract.py --dir <输出目录> --ref hengzhou_reference_grid_final_v01.tif`，B 收到后复跑一次，退出码为 0 才进入下一环节。

## 10. 验收

### 10.1 B 交付前自查

- [ ] 四个文件齐备，命名与版本正确
- [ ] 参考网格为 `EPSG:32649`、10 m，`transform` 首位为 10、末位为 -10
- [ ] 影像栈 6 波段，顺序与 5.1 节一致
- [ ] `after_sar_db` 数值合理（水体约 −20 dB，建成区约 0 dB）
- [ ] `input_scaling.sar_db_percentiles` 与实际影像一致
- [ ] 第 5 波段为 B08，产品 ID 与日期已登记
- [ ] NoData 为 `-9999` / `255`，无 0 填充、无 NaN/Inf
- [ ] `hengzhou_grid_1km.json` 与 10 m 栅格同原点，`grid_id` 连续无重复
- [ ] `docs/change_log.md` 已登记

### 10.2 A 接收后复验

A 逐项确认 10.1 全部条目，并额外检查三张 A 侧输出与参考网格的 `transform/width/height` 逐字段一致（容差 1e-9），以及 `src/member_b/raster_contracts.py` 的 `compare_signatures` 返回空列表。

自 `v03` 起增加研究区口径复验：`python scripts/verify_raster_contract.py --dir <输出目录> --ref <参考网格> --study-mask study_area_mask_final_v01.tif`，要求“区内有效覆盖率 ≥ 99.9%”“区外全为 NoData”，且 B 的 `validate_flood_rasters` 无问题项。

## 11. 需要成员B回复的三件事

1. 参考网格能否先给（哪怕全 NoData），最晚何时；
2. 第 5 节的波段顺序与标度是否同意，特别是第 6 波段保留原始 dB；
3. 第四通道确认为 B08。

以及一句共享目录地址（影像栈放哪里）。

## 12. B 侧执行步骤（按序执行，不要跳步）

### P0：不需要任何卫星影像，今天就能完成

| # | 动作 | 产出 | 自检 |
|---|---|---|---|
| 1 | 取得横州市权威行政边界，记录来源、版本、许可证（若用 OSM，记 relation ID 与查询时间） | 边界矢量，登记进 `docs/data_catalog.csv` | 能被 GIS 正常打开，坐标系可识别 |
| 2 | 由边界外包矩形向外取整到 10 m，生成**全 `-9999` 空参考栅格** | `hengzhou_reference_grid_final_v01.tif` | `EPSG:32649`、10 m、无数据 |
| 3 | 按参考网格生成 1 km 评估单元表 | `hengzhou_grid_1km.json`（`grid_id`、`center_x`、`center_y`、`grid_area_km2`） | 与 10 m 栅格同原点，`grid_id` 连续无重复 |
| 4 | 写 `hengzhou_input_metadata.json` 骨架，宽高与 `transform` 填真实值，其余字段标 `pending` | `hengzhou_input_metadata.json` v01 | 字段与第 7 节模板一致 |
| 5 | 交付：json 提交 `member-b`，空栅格 zip 后一并提交；通知 A；写 `docs/change_log.md` | 交接记录 | A 能拉到 json 与 zip、能打开 tif |

**步 1-5 完成后立即通知 A。**

### P1：数据下载，可与 P0 并行

| # | 数据 | 要求 |
|---|---|---|
| 6 | Sentinel-1 GRD | 灾前/灾后**同轨道方向、同相对轨道、同极化**；优先 VV；与 GF-3 HH 的物理差异写进论文局限性 |
| 7 | Sentinel-2 L2A | 灾前最近的低云影像；取 B02/B03/B04/B08 四个波段 |
| 8 | JRC Global Surface Water occurrence | 裁剪到研究区，阈值写进 `permanent_water_threshold` |
| 9 | Copernicus DEM GLO-30 | B 做地形校正与 Q3 用；同时供 C 做可达性 |

### P2：预处理与组栈（P1 到齐后约半天）

| # | 动作 | 关键要求 |
|---|---|---|
| 10 | 重采样到参考网格 | 连续量双线性，分类量与掩膜最近邻；不得改变像元对齐 |
| 11 | SAR → uint8 | 取 dB 的 1%/99% 分位线性映射；`p1/p99` 写入 metadata；**第 6 波段保留原始 dB** |
| 12 | 光学 → uint8 | `clip(round(refl/10000*255), 0, 255)` |
| 13 | 组栈 | 6 波段，顺序严格按 5.1 节：`after_sar_uint8, red, green, blue, nir(B08), after_sar_db` |
| 14 | NoData 处理 | 置 `-9999`，**禁止 0 填充** |
| 15 | 自检 | 四张栅格 `crs/transform/width/height` 完全一致；波段数 6；dB 合理；无 NaN/Inf |
| 16 | 交付 | 四件套齐备 + metadata 填满 + 通知 A + 写 `change_log` |

### 三条禁令（违反会直接导致返工）

1. **不要对 A 的 mask 再做"减去灾前水体"差集**：`label_semantics = newly_inundated_only` 已定稿（见 `docs/contracts.md` §6.3），再做一次会重复扣除。
2. **不要修改 A 交付的栅格**：掩膜、概率、不确定性只读。
3. **不要用 0 填充 NoData**：0 会被模型识别为极暗水体，直接产生假淹没。

## 13. 变更记录

| 日期 | 版本 | 变更 | 负责人 |
|---|---|---|---|
| 2026-11-28 | v01 | 首次冻结：命名对齐交付清单、SAR 分位映射、NoData 与空网格方案 | 成员A |
| 2026-11-28 | v02 | 增补交付渠道分工（3.1–3.5）、一分钟版、B 侧执行步骤与三条禁令、验收自查与复验；`hengzhou_grid_1km.json` 由 P1 提为 P0 | 成员A |
| 2026-11-29 | v03 | 同步 B 已冻结口径：有效范围 = `study_area_mask == 1`、`grid_id` 固定 `G000001`–`G003690` 及逐格冻结窗口、研究区基准像元数；交付清单补 `study_area_mask`/边界/GPKG；P1 改为 Git LFS 入库；复验增加 `--study-mask` 口径 | 成员A |
