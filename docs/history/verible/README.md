# Verible 历史记录

此目录保存 Tree-sitter 迁移前的分析和测量，供追溯使用。它们不描述当前后端的性能或安装方式。

- [原 TODO 分析](TODO_REVIEW.md)：包含已解决、已移除和未经复现证实的推断；当前清单见 [TODO](../../../TODO.md)。
- [第一步：模板缓存](../../../PERFORMANCE_STEP_1.md)
- [第二步：节点范围查询](../../../PERFORMANCE_STEP_2.md)
- [第三步：接口读取](../../../PERFORMANCE_STEP_3.md)

## 原始数据

以下文件由原 `test/` 目录移入，JSON 内容原样保留。文件中的 Windows 可执行文件路径、
Verible 子进程时间、JSON 转换时间和输出哈希都是历史测量信息，不是当前配置。

| 数据 | 对应记录 |
| --- | --- |
| [inst_cache_before.json](measurements/inst_cache_before.json)、[inst_cache_after.json](measurements/inst_cache_after.json) | 第一步 |
| [tree_ranges_before.json](measurements/tree_ranges_before.json)、[tree_ranges_after.json](measurements/tree_ranges_after.json) | 第二步 |
| [range_pipeline_comparison.json](measurements/range_pipeline_comparison.json) | 第二步的 PCIe 外部流程对照 |
| [interface_ansi_measurements.json](measurements/interface_ansi_measurements.json)、[interface_nonansi_measurements.json](measurements/interface_nonansi_measurements.json) | 第三步 |

## 版本与复现范围

这些测量记录日期为 2026-10-11，环境为当时的 Windows/Verible 工作区。
迁移前最后一个版本为 `aac5948`，可用 `git show aac5948:test/benchmark_inst_cache.py`
查看旧基准脚本。部分修改前对照使用临时恢复的实现；检出一个版本不等于能复现全部对照结果。
涉及 pciegen、ramgen 的流程还需要当时的外部工程和输入，本仓库不包含这些材料。

当前 `test/benchmark_*.py` 已改为测量进程内 Tree-sitter，输出字段也已改变。
新结果应写入被忽略的 `artifacts/`，不要覆盖历史 JSON 或直接把新旧数字作为加速比。
当前部分基准保留 `native_*` 字段名，实际统计源码级 `_parse_source` 调用，包含内部视图准备和可能的恢复重试，并非仅原生 `Parser.parse`。
当前命令、依赖和检查边界见 [README](../../../README.md) 与
[迁移报告](../../../TREE_SITTER_MIGRATION.md)。
