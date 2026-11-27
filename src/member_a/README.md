# 成员A工作区

负责：

- 主办方附件读取和空间划分；
- RF或XGBoost基线；
- U-Net及多源融合；
- 独立样本评价；
- 土地类型误差；
- 不确定性；
- 横州市推理。

正式输入：

- B提供的hengzhou_reference_grid；
- B提供的hengzhou_input_stack；
- B提供的input_metadata。

正式输出：

- flood_probability；
- flood_mask；
- uncertainty；
- model_metadata。

代码不得假设SAR是VV或VH。主办方SAR为GF-3 HH单通道uint8。
