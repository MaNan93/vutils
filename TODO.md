# vutils Bug / 优化记录

## Bugs

### 1) `_validate_edit_or_raise` 错误信息虚假声称"已回滚"

`vedit.py:665` — 报错写 `"Change has been reverted."` 但代码没有任何回滚逻辑，只是 raise ValueError。误导排查。

```python
raise ValueError(
    f"Edit operation '{action}' produced invalid Verilog syntax.\n"
    f"Change has been reverted.\n"   # ← 虚假声明
    ...
)
```

### 2) `_extract_instances` source_code 为 None 时静默返回 []

`vedit.py:394-398` — RootNode 可能没有 source_code，函数直接 return []，调用方不知道实例提取失败。

```python
source_bytes = getattr(mod.syntax_data, "source_code", b"")
source = source_bytes.decode("utf-8", errors="ignore") if source_bytes else ""
if not source:
    return []   # ← 静默跳过
```

### 3) `_align_to_char` 搜索窗口 ±2 字符太窄

`vedit.py:643` — 只搜索 ±2 字符范围的 `)`，verible 偏移稍有偏差就返回 -1 报 `"Unable to align"`。

```python
for d in range(1, 3):   # ← ±2
```

### 4) `from_file` 重复解析

`vedit.py:1057-1062` — `_analyze_modules()` 创建 parser + 完整解析取 module_name，然后 `__init__` 又 new 一个 parser。第一次解析白做。

### 5) `vedit.gen_inst` 触发不必要 CST 解析

`vedit.py:1182-1193` — `self._mi()` 触发完整 CST 解析，但 `gen_inst` 只需要 `ports` + `param_names` 两个 list。

### 6) CLI 两次读取文件

`vcli.py:233` + `vcli.py:351` — `list_modules_in_file` 读一次，`read_text` 又读一次。非编辑模式时第一次解析完全浪费。

---

## 优化点

### 7) add_port / add_wire / add_inst_port 逐端口重新解析 O(N)

`vedit.py:1090-1112` — 循环内每次 `add_port()` 都触发 `_mi()` 重新解析 CST。多端口场景 N 次 parse。应在一个 CST 快照下批量完成。

### 8) `_extract_port_fields_from_decl` COMMON_TYPES 逐个搜索

`vedit.py:301-305` — `for tag in COMMON_TYPES: dt.find({"tag": tag})`，最坏遍历全部。可改为 `dt.find({"tag": list(COMMON_TYPES)})` 一次命中。

### 9) `_pick_inst_type` 两重 not in 检查

`vedit.py:409` — `t not in _KEYWORDS and t not in COMMON_TYPES`，可预先合并为一个 frozenset 减少成员判断开销。

### 10) looks_like_name 不支持 SV escaped identifier

`vedit.py:112-113` — `\foo_bar` 无法匹配，不过触发场景极少。

---

## 优先级

| 等级 | 项 | 理由 |
|------|-----|------|
| P0 | #7 批量解析 | 多端口场景性能最差，直接影响 pciegen |
| P1 | #1 错误信息 | 误导排查 |
| P1 | #4 #6 重复解析 | 多解析一次，累积耗时 |
| P2 | #5 gen_inst | 不需要 CST |
| P2 | #2 #3 | 边界 case |
| P3 | #8 #9 #10 | 微小优化 |
