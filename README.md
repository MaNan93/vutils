# vutils

使用 Verible 分析与编辑 Verilog/SystemVerilog 模块接口。

在仓库根目录运行，uv 根据 `.python-version` 使用 Python 3.12：

```powershell
uv run vutils --help
uv run vutils examples/test_parser.sv --list-port all
uv run vutils design.sv --inst --inst-name u_design
```

编辑操作可通过 `--output` 指定新文件，或显式使用 `--inplace` 原地修改。
依赖由 `uv.lock` 锁定，可执行 `uv sync --locked` 提前准备环境。

运行解析、编辑功能需要 `verible-verilog-syntax`。可通过 `VERIBLE_BIN` 或 PATH
指定，也会自动查找父工作区中的 `misc/verible/`。
