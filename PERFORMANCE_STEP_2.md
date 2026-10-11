# vutils 渐进优化：第二步

日期：2026-10-11。本轮生产修改仅涉及 `BranchNode.start/end` 的边界 token 查找；保留上一轮的 `gen_inst()` 缓存修复。

## 分段测量与选择

使用一个 2,500 行、15 个实例、54,610 字节的合成模块，每项测五轮中位数。单次完整解析的修改前耗时约为：

| 部分 | 耗时，毫秒 |
|---|---:|
| Verible 子进程，含启动、解析及 JSON 输出 | 220.8 |
| JSON 解码 | 43.7 |
| Python CST 节点转换 | 95.5 |
| 完整解析总时间 | 377.9 |

这三部分不包含全部工作，例如 UTF-8 解码及其他处理。阶段中位数独立计算，不能要求精确相加。仅调整递归转换循环的初步试验没有显示足够清楚的收益，因此本轮转向节点转换后频繁使用的范围查询。

## 修改

原 `start/end` 每次调用通用 `find()`，建立筛选谓词、搜索迭代器和遍历状态。新的 `_boundary_token()` 用迭代器栈按子节点顺序找到最左/最右 token，跳过空分支。

- 保留按树顺序取边界的含义，而非计算数字最小/最大位置。
- 按需进入子节点，不为宽节点的全部兄弟提前分配搜索栈项。
- 使用显式栈，支持深层嵌套。
- 每次重新计算，保留修改 `children` 后范围立即变化的行为。
- 继续返回 Verible 的字节偏移；节点的文本读取仍按源字节切片。

## 范围查询结果

在同一棵真实生成的 CST 上，读取所有 45,698 个分支节点的 `start` 和 `end`，不将解析时间算入范围查询。五轮中位数：

| 指标 | 修改前 | 修改后 |
|---|---:|---:|
| 全部范围查询 | 293.4 毫秒 | 48.7 毫秒 |

该查询场景约快 6 倍，前后所有范围的 SHA-256 一致。初始解析过程不调用这些范围属性，因此不能把这个倍数解释为语法树构造或完整解析的加速。

## 对当前 PCIe 流程的实际影响

另做配对测量：7 个 SV 文件，各约 2,000 / 3,000 行，15 个 RAM；每种实现每个场景测三次，交替先后顺序，每轮在新 Python 进程和独立临时工程中执行。修改前版本通过临时恢复原有 `start/end` 属性实现得到，其他代码相同。

| 场景 | 原范围查询，全流程秒 | 新范围查询，全流程秒 |
|---|---:|---:|
| 每文件约 2,000 行 | 1.8962 | 1.9256 |
| 每文件约 3,000 行 | 2.6498 | 2.6247 |

三轮数据未显示稳定的全流程收益：两个场景方向相反，差异约 1%–2%。当前 pciegen 只查询较少节点的范围，主要成本仍来自完整 CST 导出、JSON 解码、节点创建和大范围遍历。这是范围查询 API 的局部优化，不能作为当前全流程提速的承诺。

后续更值得验证的是只读接口提取时减少模块体节点的构造，以及同一文件各修改步骤进一步复用有效分析结果。

## 验证

新增 7 项回归，覆盖空树、非 token 叶节点、空边缘、嵌套、树顺序、修改子节点、1,500 层深度、宽树、UTF-8 文本和真实 CST 与原搜索逻辑的一致性。

vutils 目标测试 41 项、pciegen 目标测试 20 项通过。真实 RAM 生成的完整流程通过，对 11 个 SV 文件完成语法检查。`git diff --check` 通过。未据此宣称已运行整个仓库所有历史测试。

## 数据与复现

- 分段与范围查询原始数据：`test/tree_ranges_before.json`、`test/tree_ranges_after.json`
- 全流程配对原始数据：`test/range_pipeline_comparison.json`
- 新增回归：`test/test_node_ranges.py`

在 `C:/Users/1/Desktop/code/vutils` 执行：

```powershell
python test/benchmark_tree_ranges.py --lines 2500 --repeats 5 --output test/tree_ranges_recheck.json
python -c "import sys, unittest; sys.path[:0]=['src', 'test']; unittest.main(module=None, argv=['unittest', 'test_node_ranges', 'test_gen_inst_cache', 'test_edit_batch', 'test_inst_port_regression', 'test_add_inst_port_macro'])"
```
