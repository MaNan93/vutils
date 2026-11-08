# vutils 历史性能记录：第三步

> **历史记录：Verible 后端，2026-10-11。** 本文的“现在”“当前”“后续”均指当时版本，
> 不代表当前 Tree-sitter 实现或待办状态。原始数字保留用于追溯；当前脚本已迁移，
> 不能直接复现本文的子进程、JSON 转换和节点构造指标。
> 原始数据及复现范围见 [历史索引](docs/history/verible/README.md)，当前说明见 [迁移报告](TREE_SITTER_MIGRATION.md)。

日期：2026-10-11。本轮增加专用的只读接口 API，在端口/参数读取时减少 Python CST 节点构造。pciegen 的调用接入留到下一步。

## API 与实现

```python
from vutils import read_interface, read_interface_from_file

info = read_interface(source_text, module_name="top")
info = read_interface_from_file("top.sv", auto_select=True)

ports = info["ports"]
param_names = info["param_names"]
parameter_declaration = info["parameter_declaration"]
```

每次调用返回 `name`、`module_name`、`is_ansi`、`ports`、`param_names`、`parameter_declaration`。一次调用即可取得全部接口字段；重复调用仍会重复解析。

源码仍全部交给 Verible，原有错误诊断保持有效，包括模块体和未选中模块中的语法错误。收到 JSON 后，只将完整模块头、非 ANSI 模块体端口声明及它们的祖先路径转成 Python 节点。保留祖先路径，使多模块、嵌套模块和声明顺序与现有提取逻辑一致；不修改原始 JSON。

专用解析器是内部类，部分 CST 不通过接口 API 返回，也不进入 `vedit` 缓存。现有编辑、实例分析和 `analyze()` 继续使用完整 CST。

`read_interface()` 默认要求单模块，多个模块需给出名字。文件 API 的 `auto_select=True` 优先选择与文件名相同的模块，否则选择第一个；显式模块名优先。接口 API 不按实例选择模块。

## 测量

单个合成模块，模块体 2,500 行、15 个实例。完整读取与接口读取交替先后顺序，各测五轮，取中位数。完成其他测试后单独执行基准，避免并发测试干扰。时间不包含 Python 启动和导入；结果字典逐项相等且 SHA-256 一致。

| 指标 | ANSI 完整读取 | ANSI 接口读取 | 非 ANSI 完整读取 | 非 ANSI 接口读取 |
|---|---:|---:|---:|---:|
| 总读取时间，毫秒 | 387.2 | 285.5 | 407.0 | 268.3 |
| Python 树转换，毫秒 | 102.2 | 11.0 | 106.0 | 10.7 |
| Python 节点数 | 71,183 | 86 | 71,202 | 106 |
| Verible 调用次数 | 1 | 1 | 1 | 1 |
| 原始 JSON 字节数 | 17,579,374 | 17,579,374 | 17,581,190 | 17,581,190 |

这两个只读场景耗时分别减少约 26% 和 34%。JSON 解码仍约 42–46 毫秒，Verible 子进程仍约 201–211 毫秒。树转换计时包含接口筛选；完整读取还会遍历完整树查找模块及接口信息。独立阶段中位数不保证能精确相加。

这一步尚未接入 pciegen，不能将接口读取的百分比当作 PCIe 全流程的提速。JSON 输出量和原生解析时间没有因本次修改降低。下一步可以把 pciegen 中专门读取子系统接口、RAM 端口的调用接入新 API，再测量全流程收益。

## 验证与复现

新增 12 项测试，覆盖声明形式、带默认值/类型的参数、ANSI 分组端口、非 ANSI 端口、多模块及嵌套模块、转义名字、中文注释、预处理指令、文件选择、错误诊断、单次原生调用、原始 JSON 不变，以及编辑器仍获得完整实例树并可编辑。

vutils 目标回归 53 项、pciegen 目标回归 20 项通过；真实 RAM 生成完整流程通过，11 个 SV 文件完成语法检查。`git diff --check` 通过。未运行全部历史测试。

历史原始数据：[ANSI](docs/history/verible/measurements/interface_ansi_measurements.json)、
[non-ANSI](docs/history/verible/measurements/interface_nonansi_measurements.json)。
历史脚本和外部环境要求见[历史索引](docs/history/verible/README.md)。

当时的后续进展还引用了外部 pciegen 工程的 `PERFORMANCE_STEP_4.md`；该文件不属于本仓库，
需在原工作区查阅，这里不再提供跨仓库相对链接。

## 当前 Tree-sitter 基准

当前接口元数据还包含 `imports`；节点使用惰性包装，非 ANSI 端口读取仍会扫描模块体，
不再使用本文描述的 Verible JSON 裁剪流程。

```bash
mkdir -p artifacts
uv run python test/benchmark_interface.py --lines 2500 --repeats 5 --output artifacts/interface_ansi_current.json
uv run python test/benchmark_interface.py --lines 2500 --repeats 5 --nonansi --output artifacts/interface_nonansi_current.json
```

这些命令测量当前实现，不重现上面的历史百分比、JSON 字节数或节点构造数量。
