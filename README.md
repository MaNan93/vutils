# vutils

使用 Tree-sitter 分析与编辑 Verilog/SystemVerilog 模块接口。解析在 Python 进程内完成，
不需要安装或配置 Verible。需要 Python 3.10+；仓库默认使用 Python 3.12。

历史记录见 [CHANGELOG.md](CHANGELOG.md)，本次迁移验证见 [TREE_SITTER_MIGRATION.md](TREE_SITTER_MIGRATION.md)。

```bash
uv sync --locked
uv run vutils examples/test_parser.sv --module top --list-port all
uv run vutils examples/test_parser.sv --module alu --inst --inst-name u_alu
uv run python -m unittest discover -s test
```

使用 [tree-sitter-systemverilog](https://github.com/gmlarumbe/tree-sitter-systemverilog)
语法库和 [Python Tree-sitter 绑定](https://tree-sitter.github.io/py-tree-sitter/)。

编辑操作默认输出到终端；通过 `--output` 指定新文件，或用 `--inplace` 原地修改。
保留原始 UTF-8 源码、注释和换行风格；编辑完成后重新检查语法，失败则回滚。

## 使用 code/src 中的文件

在本仓库根目录执行，`../src` 即父工作区的 `code/src`：

```bash
uv run vutils ../src/Layer3/xtlh_tlp_len_calc.sv --list-port all
uv run vutils ../src/Layer3/xtlh_tlp_len_calc.sv --inst
uv run vutils ../src/Layer3/xtlh_ms.sv --hier
```

`--inst` 的信号声明保留用户类型、signedness、数组维度和模块头部的包导入。
具体类型的 interface 端口生成 interface 实例，modport 保留在注释中；泛型
`interface.modport` 端口提示调用方连接已有实例，不推断实际 interface 类型。
生成模板仍需要调用方提供相应的包、宏和参数定义。

有多个同名 generate 实例时，短名会报歧义错误。`--hier` 显示的作用域路径可用于选择：

```bash
uv run vutils ../src/Layer3/xtlh_ms.sv \
  --add-inst-port 'g_mux_packer_vc.g_vc1.u_p_mp_arb,review_probe,review_wire' \
  --output /tmp/xtlh_ms.edited.sv
```

这是文本编辑示例，新增引脚和信号需按实际子模块接口定义。
路径描述源码中的 generate 作用域，不展开循环，不代表 elaboration 后的数组索引。

可以对整个目录执行只读回归；输出包含每个文件的失败阶段和诊断，有失败时返回 1：

```bash
uv run python examples/check_src.py ../src --output artifacts/tree-sitter-review.json
```

## Python API

```python
from vutils import TreeSitterParser, read_interface, vedit

source = 'module top(input logic clk); sub u0(.clk(clk)); endmodule'
info = read_interface(source)
editor = vedit(source, 'top')
editor.add_inst_ports('u0', {'rst_n': 'rst_n'})
print(editor.module_text)

result = TreeSitterParser().parse_string(source)
assert not result.errors
module = result.tree.find({'tag': 'module_declaration'})
print(module.text, module.start, module.end)
```

`parse_string`、`parse_file`、`parse_files` 返回 `SyntaxData`；支持 `gen_tree=False`、
`gen_tokens=True`、`gen_rawtokens=True`。节点的 `tag` 使用 Tree-sitter 原生语法名，
`start/end` 是**原始源码的 UTF-8 字节偏移**。`field(name)` 支持按语法字段访问子节点。
`native` 是内部解析视图的 Tree-sitter 节点；对原始文件定位应使用包装节点的偏移。
`gen_rawtokens` 额外包含注释，不包含空白或被屏蔽的条件指令行。

`VeribleParser` 和 `VeribleVerilogSyntax` 保留为 `TreeSitterParser` 的导入别名，
`executable=` 参数不再使用。低层 CST 标签及外部进程/JSON 接口已更换；
`vedit`、接口读取和 CLI 的调用方式保留。接口元数据新增 `imports`，实例元数据新增 `path`。

## 宏和检查范围

这是源码结构编辑工具，不执行完整预处理、类型检查或 elaboration。
简单宏类型、宏模块名、延迟及表达式保持为不透明符号；条件编译指令行在内部视图中
被屏蔽以读取分支里的源码，不选择活跃分支，也不展开 `include`。
需要宏展开才能形成合法语法的结构（例如宏展开整个端口列表、范围或任意语句片段），
以及无法组合解析的条件分支，可能报错；此时不会执行编辑。

语法库不能直接解析的系统函数位宽转换（如 `$bits(x)'(value)`）在内部视图中为
位宽表达式加括号，并用稀疏位置映射恢复原始偏移。原始表达式、输出文件不会被改写。
`ERROR` 和缺失 token 都作为失败处理；语法通过不等于完整设计编译通过。
