# 脚本目录

- validate_project.py：检查目录、必要文件和关键冻结配置。
- audit_provided_archives.py：不解压审计三份主办方ZIP的完整性、数量、编号和样例格式。
- prepare_member_b_p0.py：获取横州市OSM边界，生成并验收B到A的空参考栅格、1 km网格和metadata骨架。
- build_hengzhou_inputs.py：从Planetary Computer缓存并处理Sentinel-1/2和JRC，生成B到A正式六波段影像栈与永久水体。
- run_member_b_assessment.py：校验A的正式栅格，并生成B到C的CSV、GPKG和JSON交付。

正式脚本必须从配置文件读取路径和参数，不得写死个人盘符。
