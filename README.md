# vutils

使用 Tree-sitter 分析与编辑 Verilog/SystemVerilog 模块接口。解析在 Python 进程内完成，
不需要安装或配置 Verible。需要 Python 3.10+；仓库默认使用 Python 3.12。

历史记录见 [CHANGELOG.md](CHANGELOG.md)，本次迁移验证见 [TREE_SITTER_MIGRATION.md](TREE_SITTER_MIGRATION.md)。
当前待办见 [TODO.md](TODO.md)；Verible 时期的分析与性能数据见
[历史资料索引](docs/history/verible/README.md)，不作为当前性能指标。

```bash
uv sync --locked
uv run vutils examples/test_parser.sv --module top --list-port all
uv run vutils examples/test_parser.sv --module alu --inst --inst-name u_alu
uv run python -m unittest discover -s test
```

使用 [tree-sitter-systemverilog](https://github.com/gmlarumbe/tree-sitter-systemverilog)
语法库和 [Python Tree-sitter 绑定](https://tree-sitter.github.io/py-tree-sitter/)。
语法库固定到上游修复提交 `08c5e379b9b6022d4c433a9249e6d54dedd051ba`，而不是
PyPI 的旧版 0.4.1 wheel。首次源码安装需要 C 编译器；运行时无需编译器。

## 全局使用

在仓库根目录安装一次，之后可以从任意目录调用：

```bash
uv tool install --editable . --python 3.12
cd /tmp
uv run vutils --help
# 也可以直接执行：
vutils --help
```

可执行文件安装到 `~/.local/bin/vutils`，该目录需在 PATH 中。可编辑安装指向仓库，
代码修改直接生效；依赖改变后用 `uv tool install --force --editable . --python 3.12` 更新。

可以为当前平台构建全部离线 wheel：

```bash
uv run --with pip python -m pip wheel --wheel-dir artifacts/wheels .
```

在同系统、架构和 Python ABI 的离线环境中，将这些 wheel 一起安装。使用 `--no-deps`
避免包元数据中的固定源码 URL 再触发网络下载；需要把目录内三个依赖/项目 wheel 全部提供：

```bash
pip install --no-index --no-deps --force-reinstall artifacts/wheels/*.whl
```

## 编辑预览和检查

编辑操作默认输出到终端；通过 `--output` 指定新文件，或用 `--inplace` 原地修改。
保留原始 UTF-8 源码、注释和换行风格；编辑完成后重新检查语法，失败则回滚。
新增端口继承相邻声明的缩进、字段列位置和逗号风格，不重排已有行。

```bash
uv run vutils ../src/Layer3/xtlh_ms.sv --add-port 'input, logic, review_enable' --diff
uv run vutils ../src/Layer3/xtlh_ms.sv --add-port 'input, logic, review_enable' --check
```

`--diff` 输出 unified diff，不写文件。`--check` 不写文件，无需修改时返回 0，
需要修改时返回 1；语法或编辑错误仍返回对应错误码。两者可以组合用于 CI。
这两个选项都需要编辑参数，不能与 `--inplace` 或 `--output` 一起使用。

## 常用命令与参数

| 选项 | 用途 |
| --- | --- |
| `--module NAME` | 选择文件内的模块；编辑多模块文件时必须指定 |
| `--list-port all/input/output/inout/ref` | 按方向列出端口 |
| `--hier` | 显示本文件内模块的源码实例关系，不展开实际设计 |
| `--inst --inst-name u_top` | 生成信号声明、参数传递和实例化模板 |
| `--inst-no-param` / `--inst-no-decl` | 生成模板时省略参数块或信号声明 |
| `--add-port SPEC` | 添加模块端口，可重复 |
| `--add-wire SPEC` | 添加内部信号，可重复 |
| `--add-inst-port SPEC` | 添加实例引脚连接，可重复；支持 `*` 广播 |
| `--diff` | 预览差异，不写文件 |
| `--check` | 检查是否需要修改，不写文件；可与 `--diff` 组合 |
| `--output FILE` | 写入另一个文件 |
| `--inplace` | 修改输入文件 |

不指定输出选项时，编辑后的源码打印到终端。`--diff`、`--output`、`--inplace`
互斥；`--check` 不能与后两者组合。编辑和检查语法失败时不写输出文件。

端口格式为 `direction, [type,] [width,] name[, dimension]`，例如：

```bash
vutils design.sv --add-port 'input,a' --diff
vutils design.sv --add-port 'input,logic,[7:0],data' --diff
vutils design.sv --add-port 'output,logic,[7:0],samples,[0:3]' --diff
vutils design.sv --add-wire 'logic,[7:0],internal_data' --diff
```

`type` 省略时模块端口不自动补类型，内部信号默认 `wire`；位宽和数组维度可以省略。
包含空格、方括号、星号或表达式的 SPEC 应使用引号，避免 shell 展开。

## 向所有直接子实例广播信号

例如给父模块添加一位输入 `a`，并给所有直接子实例连接 `.a(a)`：

```bash
# 只查看差异
vutils design.sv --module top \
  --add-port 'input,logic,a' \
  --add-inst-port '*,a,a' --diff

# 确认后保存到新文件
vutils design.sv --module top \
  --add-port 'input,logic,a' \
  --add-inst-port '*,a,a' --output design.edited.sv
```

`--add-inst-port` 的格式是 `selector, port[, expression]`：

```bash
# 单个实例、generate 作用域内实例
vutils design.sv --add-inst-port 'u0,a,a' --diff
vutils design.sv --add-inst-port 'g_lane.u0,a,a' --diff

# 所有直接实例同时添加两组连接
vutils design.sv --add-inst-port '*,a,a' --add-inst-port '*,b,b' --diff

# 引脚悬空（省略 expression 也表示悬空）
vutils design.sv --add-inst-port 'u0,unused,' --diff
```

- `*` 精确表示选中模块的全部直接实例，包括 generate 内的实例及实例数组声明。
  不递归修改子模块定义，不展开 generate 循环或实例数组。
- 当前只支持完整的 `*`，不支持 `u_*` 等局部通配模式。
- 子模块定义必须已有对应引脚；本操作不检查跨文件接口，也不自动声明父模块信号。
- 已有相同的命名连接会跳过；同一引脚已连接到其他表达式则报错，不覆盖。
- 没有子实例时广播报错；短实例名有歧义时应使用 `--hier` 显示的完整源码路径。
- `*` 是 CLI 功能；Python API 可枚举 `editor.instances` 的 `path`，再调用
  `add_inst_port` 或 `add_ports_and_inst_ports` 批量编辑。

## 批量给多个文件添加模块端口

每个文件只有一个模块时，用 shell 循环即可。先预览：

```bash
for file in ./*.sv; do
  vutils "$file" --add-port 'input,logic,a' --diff || break
done
```

确认后，把 `--diff` 换成 `--inplace`。文件名通过引号保留空格；出错即停止，
但已经保存的前面文件不会回滚。当前目录需有匹配的 `.sv` 文件。
每次调用只操作一个文件中的一个模块，不添加子实例连接。
多模块文件必须加 `--module NAME`；不同文件的目标名称不同时，应逐文件指定。
若希望修改某文件里的全部模块，可使用 Python API：

```python
from pathlib import Path
from vutils import vedit

path = Path("design.sv")
source = path.read_bytes().decode("utf-8")
module_names = vedit.list_modules(source)
for name in module_names:
    # 每次基于上一步结果创建 editor，保留同文件中其他模块的改动。
    editor = vedit(source, name)
    editor.add_port({"a": {"direction": "input", "type": "logic"}})
    source = editor.module_text
print(source)  # 先预览；需要保存时使用 path.write_bytes(source.encode("utf-8"))
```

退出码：`0` 成功；`1` 输入文件不存在或 `--check` 检测到需要修改；
`2` 参数/解析错误；`3` 编辑错误；`4` 实例模板生成错误。
`--check` 返回 `1` 可以是正常的差异结果，批处理时不要将其一律视为解析失败。

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

`vedit.from_source_all(source)` 可以一次解析创建多个模块的 editor，分别编辑时互不影响。
CLI 用它复用首次解析；正常读取路径只解析一次，单次编辑另做一次结果校验。
这里按源码级解析调用计数；特定语法恢复适配可能在一次调用内重试原生解析。
接口读取按需包装节点，非 ANSI 端口提取仍会遍历模块体。

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
单独占据 packed range 的宏（如 ``[`RANGE]``）保留为不透明范围，不推断其值；
内部语法占位不会出现在输出文件或宽度元数据中。
需要宏展开才能形成合法语法的结构（例如宏展开整个端口列表或任意语句片段），
以及无法组合解析的条件分支，可能报错；此时不会执行编辑。

语法库不能直接解析的系统函数位宽转换（如 `$bits(x)'(value)`）在内部视图中为
位宽表达式加括号，并用稀疏位置映射恢复原始偏移。原始表达式、输出文件不会被改写。
嵌套成员用作索引时的语法歧义也通过内部括号和位置映射适配。
`ERROR` 和缺失 token 都作为失败处理；语法通过不等于完整设计编译通过。

工程兼容性扫描结果及已知问题见 [2026-11-09 测试记录](docs/compatibility-2026-11-09.md)。
