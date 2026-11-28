# 正式交接检查表

## 发送方填写

- 交付人：
- 接收人：
- 时间：
- 文件版本：
- 数据状态：mock / candidate / final
- 输入数据：
- 主要参数：
- 输出文件：
- Git提交：
- 已完成检查：
- 已知问题：
- 接收方下一步：

## 接收方检查

- [ ] 文件能够正常打开。
- [ ] 文件名、版本和状态正确。
- [ ] CRS正确。
- [ ] 分辨率、范围和尺寸正确。
- [ ] NoData正确。
- [ ] 字段完整。
- [ ] 字段单位明确。
- [ ] 不存在重复grid_id。
- [ ] 包含metadata。
- [ ] 能够由配置文件重新运行。

## 三次正式交接

### 交接一：B到A

规格见 `docs/handoff1_b_to_a_input_spec.md`。

- hengzhou_reference_grid_final_v01.tif
- hengzhou_stack_final_v01.tif（6波段，第6波段为原始dB）
- permanent_water_final_v01.tif
- hengzhou_input_metadata.json

### 交接二：A到B

- flood_probability
- flood_mask
- uncertainty
- model_metadata

### 交接三：B到C

- assessment_grid.csv
- assessment_grid.gpkg
- flood_summary
- intensity_thresholds
- assessment_metadata
