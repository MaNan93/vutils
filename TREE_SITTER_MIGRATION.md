# Tree-sitter 迁移结果

日期：2026-11-08。

默认解析后端已替换为进程内 Tree-sitter，不再查找或启动 Verible。
依赖：`tree-sitter==0.25.2`，语法库固定到上游修复提交
`08c5e379b9b6022d4c433a9249e6d54dedd051ba`（包版本仍为 0.4.1），由 `uv.lock` 锁定。
首次源码安装需要 C 编译器；当前平台的离线 wheel 已生成到 `artifacts/wheels/`。
Python 最低版本调整为 3.10，仓库默认仍为 3.12。

## 实现

- 用原生语法节点和字段提取 ANSI/non-ANSI 端口、参数、实例和 generate 作用域。
- 原文上的 UTF-8 字节范围用于定位；编辑保留注释和 CRLF/LF，失败回滚。
- 按需构造 Python 节点包装，复用现有 editor 缓存；实例分析保持延迟执行。
- 为宏模块名、宏类型/延迟和系统函数位宽转换提供内部解析视图及原文位置映射。
- 重名实例要求作用域路径；实例数组直接使用连接列表的括号，避免取到维度表达式。
- 模板保留普通信号类型、signedness、维度和包导入；具体 interface 类型生成 interface 实例，泛型 interface 提示连接已有实例。
- CLI 调用方式保留；低层节点标签改为 Tree-sitter 语法名，新增 `TreeSitterParser`。
  `VeribleParser`/`VeribleVerilogSyntax` 只保留导入别名，`executable=` 不再使用。
- 旧 Windows 路径、旧 parser API 和 subprocess mock 测试已迁移；三个基准脚本改为测量进程内解析。

## 验证

```bash
uv sync --locked
uv run python -m unittest discover -s test
uv run python examples/check_src.py ../src --output artifacts/tree-sitter-review.json
```

- 100 项测试全部通过，包括 CLI、批量编辑、幂等性、冲突回滚、缓存、宏、interface、generate 路径、实例数组及 Unicode/CRLF。
- `code/src` 共 808 个 `.sv`：全部完成检查，其中 2 个不含模块，仅检查语法。
  迁移初版失败的 6 个文件已全部支持；具体统计见 JSON。
- 每个已解析模块检查生成模板语法、添加端口和 wire、重复添加；存在实例时，按作用域路径给首个实例添加连接并检查重复执行。
- 最终全目录检查耗时约 91 秒，无模板或编辑阶段失败。
- 与旧检查可比较的文件中，端口计数差异是此前未提取的条件编译端口；新统计也包含源文件中条件分支里的实例。它们不是某个配置 elaboration 后的数量。
- 缓存基准在 2,500 行声明、15 个实例的合成输入上连续读取模板和元数据，三次运行均只解析一次。

详细 JSON 在 `artifacts/tree-sitter-review.json`，测试日志在 `artifacts/unittest.log`。
示例输出：`artifacts/xtlh_tlp_len_calc.inst.sv`、`artifacts/xtlh_ms.edited.sv`、
`artifacts/phy_if_cpcie_shadow.edited.sv`。这些是本地验证产物，未提交到版本控制。

## 已解决的六个兼容性问题

所有最小复现均为合成语法示例，位于 `examples/parser_repros/`，并纳入自动测试。

| 原始失败文件 | 最小复现 | 原因与处理 |
| --- | --- | --- |
| `Adm_ms/atu_out_match.sv` | `01_binary_hierarchy.sv` | 复杂层级引用/二元表达式歧义；固定上游修复版本 |
| `Adm_ms/atu_out_translate.sv` | `02_conditional_concat.sv` | 拼接内的条件表达式歧义；固定上游修复版本 |
| `Adm_ms/radm_anti_addr_ctrl.sv` | `03_parenthesized_concat.sv` | 带括号的多成员拼接歧义；固定上游修复版本 |
| `Adm_ms/target_cpl_lut_ms.sv` | `04_opaque_packed_range.sv` | packed range 中的宏；保留原文的不透明范围适配 |
| `Adm_ms/xadm_fc_dllp_proc_ms.sv` | `05_selected_concat_compare.sv` | 多重选择/拼接/比较表达式歧义；固定上游修复版本 |
| `Edmapf/DWC_pcie_edmapf_ctrl_nullFilter.sv` | `06_nested_index.sv` | 多层成员选择作为索引的歧义；内部索引加括号并映射回原文 |

宏范围适配仅在 packed dimension 的错误节点上识别单独的宏引用，给内部视图添加语法占位，
不会将普通非法的 `[WIDTH]` packed range 当作宏接受，也不把占位值当作宏展开结果。
嵌套索引适配不移除表达式；仍不合法的输入保留错误并拒绝编辑。

## 格式、预览与全局安装

- 新增端口继承相邻字段列和缩进；分隔符按最近的顶层逗号选择行尾、行首或独立行风格，忽略嵌套调用中的逗号。
- `--diff` 预览 unified diff，`--check` 在需要修改时返回 1、无变化时返回 0，均不写文件；可以组合。
- CLI 通过 `vedit.from_source_all` 复用首次解析。测试确认多模块 hierarchy 也只解析一次，单次编辑只有初始解析和结果校验。
- 已用可编辑工具安装到 `~/.local/bin/vutils`；在 home 和 `/tmp` 验证 `uv run vutils`。
- 全局安装、离线 wheel 和检查返回码的使用方法见 [README](README.md)。

Tree-sitter 校验仅覆盖内部视图的语法；工具不展开 include、不选择条件编译活跃分支，
不进行类型检查或完整 elaboration。生成模板的包、参数、宏及 interface 时钟连接等仍由使用方提供。
