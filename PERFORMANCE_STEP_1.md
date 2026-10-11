# vutils 渐进优化：第一步

日期：2026-10-11。本轮在此前已有改动基础上，生产代码只调整 `vedit.gen_inst()` 的缓存使用。

## 问题与修改

缓存为空时，原 `gen_inst()` 会解析源码并提取端口、参数，但不保存结果。因此连续调用两次 `gen_inst()`，随后读取 `ports`，会启动三次 Verible，重复生成和构造相同的完整 CST。

现在 `gen_inst()` 调用已有的 `_mi()`：第一次读取缓存模块信息，后续只读操作复用它；实例信息仍然按需提取，不因模板生成而提前分析。源码修改后仍沿用原有缓存失效机制，避免旧端口进入新的模板。

## 对比测量

输入：一个约 2,500 行的合成模块，54,610 字节，包含位宽声明、一个参数和 15 个实例。连续生成两个不同选项的实例模板，再读取端口、参数、实例。各测五轮，源码构造、程序启动和依赖导入不计时。

| 指标 | 修改前 | 修改后 |
|---|---:|---:|
| 连续操作中位耗时 | 1.4431 秒 | 0.4461 秒 |
| Verible 调用 / 完整 CST 导出次数 | 3 / 3 | 1 / 1 |
| 累计 JSON 输出量 | 52.74 MB | 17.58 MB |

模板和元数据的 SHA-256 在前后及各轮均一致。这是特定连续 API 调用的约 3.2 倍收益，不能当作整个 pciegen 流程的加速倍数。当前 pciegen 多数使用已缓存的 `from_file()`，因此此处的直接全流程收益预计有限。

## 验证

- 新增 4 项回归：模板与元数据共用一次解析；源码编辑后缓存失效；多模块中指定非 ANSI 目标；非法源码不进入缓存。
- 相关 vutils 目标测试合计 34 项通过。
- 真实 RAM 生成的 PCIe 完整流程通过，11 个 SV 文件语法检查通过。
- `git diff --check` 通过。

## 后续检查方向

单次解析仍会导出并构造整棵 CST；减少重复调用后，这部分仍是明显开销。CLI 还存在先 `list_modules()` 解析，再为选中模块建立编辑器重新解析的路径。后续可分别验证和修改，避免一次混合多个优化。本轮未改变 CLI、CST 转换、pciegen 或 ramgen 的实现。

本次测量的带位宽声明比之前简单标量声明产生更多 JSON，说明行数相近的 RTL 也可能具有不同的解析成本。

## 复现

在 `C:/Users/1/Desktop/code/vutils` 执行：

```powershell
python test/benchmark_inst_cache.py --lines 2500 --repeats 5 --output test/inst_cache_recheck.json
python -c "import sys, unittest; sys.path[:0]=['src', 'test']; unittest.main(module=None, argv=['unittest', 'test_gen_inst_cache', 'test_edit_batch', 'test_inst_port_regression', 'test_add_inst_port_macro'])"
```

原始数据：`test/inst_cache_before.json`、`test/inst_cache_after.json`。这两个文件记录本轮修改前后，不是最初 Git 版本的整体对比。
