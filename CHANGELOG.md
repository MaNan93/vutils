# 变更历史

## 2026-11-08 — 迁移到 Tree-sitter

### 解析与编辑

- 将默认解析后端从外部 Verible 进程替换为进程内 Tree-sitter SystemVerilog，移除可执行文件及 PATH 配置要求。
- 添加 `tree-sitter` 和 `tree-sitter-systemverilog` 依赖并更新锁文件；最低 Python 版本调整为 3.10，默认环境保持 3.12。
- 基于原生节点字段读取 ANSI/non-ANSI 端口、参数、实例和 generate 作用域；按需包装节点并复用解析缓存。
- 保留原始 UTF-8 字节位置、注释及 CRLF/LF；编辑后校验语法，失败回滚。
- 为简单宏引用和系统函数位宽转换添加内部解析适配及原文位置映射。

### 修复

- 模板声明保留用户数据类型、signedness、数组维度及模块头部的包导入。
- 具体 interface 类型生成 interface 实例；泛型 interface 提示连接已有实例。
- 重名 generate 实例不再静默选择第一个，支持作用域路径选择。
- 修复实例数组维度中的括号被误认为端口连接列表的问题。

### API 与验证

- 保留 CLI 和 `vedit` 的调用方式，新增 `TreeSitterParser`；旧 parser 名称保留为导入别名。
- 低层 CST 标签改为 Tree-sitter 语法名；接口元数据新增 `imports`，实例元数据新增 `path`；`executable=` 不再使用。
- 迁移旧测试和三个性能基准，新增 `examples/check_src.py` 目录验证示例。
- 86 项测试全部通过；808 个真实 `.sv` 文件中 802 个完成检查，6 个在解析阶段失败，没有模板或编辑阶段失败。完整目录验证约 89 秒，包含模板生成、多次编辑及幂等性检查。

### 性能实测

当前机器上多次运行的中位数；文件缓存已预热。进程内测量不含 Python 启动和导入，CLI 测量包含 `uv run` 启动开销。

| 输入大小 | 读取接口 | 完整分析 | CLI 查看端口 |
| --- | ---: | ---: | ---: |
| 4.5 KiB | 1.4 ms | 2.6 ms | 88 ms |
| 25.5 KiB | 6.7 ms | 9.2 ms | 113 ms |
| 327.5 KiB | 108 ms | 187 ms | 315 ms |

327.5 KiB 文件复用同一 editor 缓存生成模板约 1.5 ms。未进行同条件 Verible 对比，不据此声明加速倍数。

### 限制

不执行完整宏展开、include 解析、活跃条件分支选择、类型检查或 elaboration。当前尚有 6 个真实文件无法解析；清单、复现命令及兼容性说明见 [迁移报告](TREE_SITTER_MIGRATION.md)。本地 RTL 验证产物在忽略目录 `artifacts/`，不随代码提交。
