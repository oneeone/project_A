# 数据目录

本目录中的大文件不提交Git。

## 结构

- raw：原始数据，只读，不修改。
- interim：配准、裁剪和重采样后的中间数据。
- processed：可直接进入模型或分析的数据。
- external：WorldPop、OSM、DEM、永久水体等。
- mock：并行开发使用的模拟接口数据。

每名成员使用相同目录结构，通过configs/paths.local.yaml配置本地实际路径。
