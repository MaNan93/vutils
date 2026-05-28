# vutils Bug / 优化深度分析

---

## Bugs（顺序按严重程度）

### #1 `_find_matching_paren_from` 不跳过字符串/注释中的括号

`vedit.py:754-769` — 逐字符数括号深度，遇到 `"test(1)"` 或 `/* x( */` 中的 `()` 会计入深度，导致 `add_inst_port` 找错 `)` 位置。

**触发条件**：实例化时某个参数是字符串且含括号：
```verilog
u_foo u_foo_inst (
    .info("version(2.0)"),  // ← 这里的括号会干扰匹配
    .clk(clk)
);
```

使用方：`add_inst_port` 第 935-938 行，通过 `_find_matching_paren_from` 定位实例的 `)`。

### #2 `_validate_edit_or_raise` 虚假声称"已回滚"

`vedit.py:653-670` — 报错文本写 `"Change has been reverted."`，但函数签名 `-> str` 返回编辑后的文本，调用方拿到的是已修改但未回滚的源文本。如果调用方忽略了异常或 catch 后继续使用返回值，会拿到损坏的代码。

```python
def _validate_edit_or_raise(original_source, edited_source, parser, action) -> str:
    try:
        _parse_tree(edited_source, parser)
    except Exception as exc:
        raise ValueError(
            f"Edit operation '{action}' produced invalid Verilog syntax.\n"
            f"Change has been reverted.\n"  # ← 谎言：没有任何回滚动作
```

应改为 `"Change was not applied."` 或实际回滚并返回 `original_source`。

### #3 `_extract_instances` source_code 为 None 时静默返回空列表

`vedit.py:394-398` — `RootNode.syntax_data.source_code` 在 `_parse` 的 `_load_source_bytes` 失败时可能为 None。`_extract_instances` 不报错、不返回任何提示，直接 `return []`。调用方无法区分"真的没有实例"和"解析数据丢失"。

**追溯**：`SyntaxData.source_code` 初始值就是 `None`（`vparser.py:354`），只有 `_parse` 第 538 行通过 `_load_source_bytes` 赋值。如果文件路径 `-`（stdin）且 `input_` 为 None，source_code 保持 None。

### #4 `_nonansi_decl_insert_idx` 魔数 `s - 2` 搜索 `;`

`vedit.py:713` — `search_start = max(0, s - 2)`，硬编码从声明起始位置前 2 字节搜索 `;`。应该从声明 CST 节点的 **结束位置** 后搜索，而非起始位置前。可能误匹配到上一行的 `;`。

```python
s, e = get_node_range(last_port_decl)   # s=start, e=end（未使用 e）
search_start = max(0, s - 2)            # ← 应该用 e 或 e+1
semi = source.find(";", search_start, search_end)
```

如果注释行以 `;` 结尾恰好出现在 declaration 前面 2 字节内，会匹配到错误的 `;`。

### #5 `_align_to_char` 搜索窗口 ±2 太窄

`vedit.py:643` — 当 verible 报告的 `)` 位置因注释、空白、宏展开等因素偏移超过 2 字符时，返回 -1，触发 `"Unable to align"` 异常。

建议窗口扩至 ±8 或 ±16，同时在返回 -1 时提供更详细的诊断（当前行源码、预期位置、实际位置）。

### #6 `from_file` 重复解析

`vedit.py:1057-1062` — 先 `_analyze_modules(source, _new_parser())` 取 module_name，再 `__init__` 里 new 另一个 parser。第一次解析结果（CST 树）完全丢弃。

模块级函数 `_analyze_modules` 返回 `[{name, is_ansi, cst, ports, param_names, instances}, ...]`。但 `from_file` 只需要 `name`，其他全部丢弃。

**修复方向**：在 `__init__` 之外解析一次，把结果传给 `__init__`，或者让 `from_file` 直接构造 `_mi_cache`。

### #7 `vedit.gen_inst` 触发不必要的 CST 解析

`vedit.py:1189-1193` — `self._mi()` 触发完整 verible 解析（启动 subprocess + JSON 解析 + CST 构建），但 `gen_inst` 只需要 `ports`（list of dict）和 `param_names`（list of str）。

如果 `_mi_cache` 命中则不重复解析，但如果之前没有访问过 `ports`/`param_names`，首次 `gen_inst` 就会触发解析。应该在不需要 CST 时避免 `_mi()`。

### #8 CLI 两次读取/解析文件

`vcli.py:233` `list_modules_in_file` + `vcli.py:351` `read_text` — 无编辑操作时（如 `--list-port`）也读了两次文件。`list_modules_in_file` 内部触发完整 verible 解析，`read_text` 只是文本读取。

**量化**：对一个 500KB SV 文件，`list_modules_in_file` 启动 verible subprocess 约 0.3-0.5s。如果只是 `--list-port`，`source_text` 变量在后续 `print_port_list` 中根本不用（它用 `vedit(source_text, mod_name).analyze()` 重新解析，又触发一次 subprocess）。

---

## 优化点（深度剖析 — 附执行路径 trace）

### #9 add_port / add_wire / add_inst_port O(N) 重复解析

**执行路径 trace（non-ANSI, 3 端口）**：

```
vedit.add_port({a,b,c})
│
├─ Port a:
│  ├─ self._mi()                     → parse #1
│  ├─ add_port(source, mi=#1)
│  │  └─ _apply_nonansi_add_port:
│  │     ├─ _get_module_info()       → parse #2  (step 1: re-parse after adding name)
│  │     └─ (插入 decl)
│  ├─ self._source != before → _invalidate()
│
├─ Port b:
│  ├─ self._mi()                     → parse #3  (cache cold)
│  ├─ add_port(source, mi=#3)
│  │  └─ _apply_nonansi_add_port:
│  │     └─ _get_module_info()       → parse #4
│  ├─ _invalidate()
│
├─ Port c:
│  ├─ self._mi()                     → parse #5
│  ├─ add_port(source, mi=#5)
│  │  └─ _apply_nonansi_add_port:
│  │     └─ _get_module_info()       → parse #6
│  ├─ _invalidate()
│
└─ _validate_edit_or_raise()         → parse #7  (最终校验)
```

**3 个 non-ANSI 端口 → 7 次 subprocess 调用。每次约 0.3-0.5s，合计 2-3.5s。**

对于 ANSI，路径更简单（ANSI 不需要 re-parse 插入 decl）：
```
ANSI, 3 ports: parse #1 → edit → parse #2 → edit → parse #3 → parse #4 (validate) = 4 次
```

**修复方向**：收集所有端口信息，基于一次 CST 快照计算所有编辑偏移，一次性应用全部编辑，最终一次验证。可将 3 端口 ANSI 从 4 次降至 2 次，non-ANSI 从 7 次降至 2-3 次。

### #10 `_detect_indent_at` 对长文件反向全量扫描

`vedit.py:549-560` — 当 `close_idx` 所在行是空白行时，`reversed(source[:line_start].splitlines())` 会遍历文件开头到当前位置的**所有行**，直到找到第一个非注释/空白行。

对于一个 5000 行的 SV 文件，如果 `)` 前恰好是空白行，且前面 3000 行全是注释/空白（极端情况），O(3000) 次字符串操作。

**修复方向**：限制扫描范围（如最多往回 50 行），或加入启发式（`module` 关键字后面的缩进）。

### #11 `_extract_port_fields_from_decl` COMMON_TYPES 逐个 find

`vedit.py:301-305`：
```python
for tag in COMMON_TYPES:              # frozenset 有 17 个元素
    t = dt.find({"tag": tag}, ...)    # 每个 find 走一次 PreOrderTreeIterator
    if t:
        ptype = tag
        break
```

最坏 17 次 CST 遍历。但 `_normalize_filter` 已支持 `{"tag": ["wire", "reg", "logic", ...]}` 列表匹配。改为 `dt.find({"tag": sorted(COMMON_TYPES)}, ...)` 一次命中。

### #12 verible subprocess 无超时、无信号清理

`vparser.py:489-496` — `subprocess.run()` 无 `timeout` 参数。如果 verible 卡在解析大型文件（如 10MB+ 的展开宏文件），进程永久挂起，Python 主进程也跟着 block。

另外 `subprocess.run(stdout=PIPE, stderr=PIPE)` 如果不消费管道，子进程可能因管道满而阻塞（虽然这里流程是 `run` 一次性读完，但 `check=False` 时 returncode 非 0 不抛异常）。

**修复方向**：加 `timeout=30` 参数，超时后 kill + 报错。

### #13 `vedit` 类 `_mi()` 缓存与属性的一致性问题

```python
ports = property(lambda self: self._mi().get("ports", []))
instances = property(lambda self: self._mi().get("instances", []))
```

如果外部代码：
```python
e = vedit(source, "top")
e.add_port({"a": {...}})           # 修改 source + _invalidate()
ports = e.ports                     # _mi() → parse ✓ (最新)
insts = e.instances                 # _mi() → cache hit ✓ (同一次 parse)
```

一致。但如果：
```python
e.ports                             # cache warm
# 直接修改 e._source（绕过 add_port）
e._source = e._source + "extra"
e.instances                         # cache still warm → stale data!
```

虽然用户不应该直接改 `_source`，但缺乏防御。建议 `_source` 加 setter 自动 invalidate。

### #14 `vedit` OOP API 只有增加、没有删除

`add_port`, `add_wire`, `add_inst_port` 全部是增量操作。不支持 `remove_port`、`remove_wire`。pciegen 中 `delete_subsystem_param_lines` 被迫用正则处理，说明确实有删除需求。

### #15 `_extract_ports` non-ANSI 匹配 O(n²)

`vedit.py:362-375` — 外层遍历所有 `kModulePortDeclaration`，内层遍历 `ids` + 查 `by_name` dict。对于每个 declaration，要找到所有 `[SymbolIdentifier, EscapedIdentifier]`。如果 declaration 数量与端口数相当（n），且每个 declaration 有多个标识符，总复杂度 ~O(n²)。

虽然实际场景不会太严重（端口数通常 < 1000），但可优化。

---

## 优先级总表

| 等级 | # | 问题 | 影响 |
|------|---|------|------|
| **P0** | #9 | add_port 批量解析 | non-ANSI 3端口=7次parse，pciegen 最慢路径 |
| **P0** | #10 | _detect_indent_at 全量扫描 | 长文件每次编辑都 O(lines) |
| **P1** | #1 | _find_matching_paren 字符串括号 | 含字符串参数的实例化连接错误 |
| **P1** | #2 | _validate 虚假回滚声明 | 误导排查，异常恢复路径危险 |
| **P1** | #4 | _nonansi_decl_insert_idx 魔数 s-2 | 可能误匹配 `;` 导致 decl 插入错位 |
| **P1** | #6 | from_file 重复解析 | 每次打开文件多 parse 一次 |
| **P1** | #8 | CLI 重复读取 | --list-port 等非编辑操作多读一次 |
| **P2** | #3 | _extract_instances 静默失败 | 边界条件，需源文件不可读时触发 |
| **P2** | #5 | _align_to_char ±2 太窄 | 边界 case，多空格行时触发 |
| **P2** | #7 | gen_inst 触发 CST 解析 | 首次调用慢，后续缓存命中则无影响 |
| **P2** | #12 | subprocess 无超时 | 需要 10MB+ 文件 + verible bug 才触发 |
| **P3** | #11 | COMMON_TYPES 逐个 find | 微小优化，每次端口提取省 ~10ms |
| **P3** | #13 | _mi 缓存一致性 | 正常使用路径不会触发 |
| **P3** | #14 | 缺少删除 API | 功能缺口，非当前阻塞 |
| **P3** | #15 | non-ANSI O(n²) 匹配 | 端口数 < 1000 可忽略 |
