"""Verilog/SystemVerilog module editing utilities built on vparser CST."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional, Union

from .vparser import (
    COMMON_TYPES,
    PostOrderTreeIterator,
    PreOrderTreeIterator,
    VeribleParser,
    find_all,
    get_node_range,
)

_KEYWORDS = frozenset(
    {
        "module",
        "endmodule",
        "input",
        "output",
        "inout",
        "ref",
        "wire",
        "reg",
        "logic",
        "bit",
        "parameter",
        "localparam",
    }
)


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------


def split_csv(value: str) -> list[str]:
    """Split by top-level commas (ignores commas inside () [] {})."""
    depth, parts, prev = 0, [], 0
    for m in re.finditer(r"[()[\]{},]", value):
        ch = m.group()
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth > 0:
                depth -= 1
        elif depth == 0:
            token = value[prev : m.start()].strip()
            if token:
                parts.append(token)
            prev = m.end()
    tail = value[prev:].strip()
    if tail:
        parts.append(tail)
    return parts


def normalize_width(width: str) -> str:
    """Normalize width string to [msb:lsb] form."""
    w = width.strip()
    return "" if not w else w if w.startswith("[") else f"[{w}]"


def normalize_dimension(dimension: str) -> str:
    """Normalize dimension string to [a:b][c:d] form."""
    d = dimension.strip()
    if not d:
        return ""
    if groups := re.findall(r"\[\s*([^\]]+?)\s*\]", d):
        return "".join(f"[{g.strip()}]" for g in groups)
    parts = [p.strip() for p in split_csv(d) if p.strip()]
    return "".join(f"[{p}]" for p in parts) if parts else f"[{d}]"


def detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def looks_like_width_or_dimension(token: str) -> bool:
    t = token.strip()
    return bool(t) and (t.startswith("[") or ":" in t)


def looks_like_name(token: str) -> bool:
    return bool(re.match(r"^`?[A-Za-z_][A-Za-z0-9_$]*$", token.strip()))


def _split_line_parts(line: str) -> tuple[str, str, str, str]:
    """Return (core, code_no_comment, comment, newline)."""
    nl = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
    core = line[: len(line) - len(nl)]
    i = core.find("//")
    return (core, core[:i].rstrip(), core[i:], nl) if i >= 0 else (core, core.rstrip(), "", nl)


def append_comma_to_port_line(line: str) -> str:
    """Append comma to code end, preserving inline comments and newline."""
    core, code, comment, newline = _split_line_parts(line)
    if code.endswith(","):
        return core + newline
    code += ","
    if comment:
        code += " " + comment.lstrip()
    return code + newline


def compose_port_decl(info: dict[str, str]) -> str:
    return " ".join(
        p
        for p in [
            info.get("direction", ""),
            info.get("type", ""),
            info.get("width", ""),
            info.get("name", ""),
            info.get("dimension", ""),
        ]
        if p
    )


def compose_wire_decl(info: dict[str, str]) -> str:
    return " ".join(
        p
        for p in [
            info.get("type", "wire"),
            info.get("width", ""),
            info.get("name", ""),
            info.get("dimension", ""),
        ]
        if p
    )


def get_line_spans(block_text: str) -> list[dict[str, Any]]:
    """Split text into lines with byte-offset metadata."""
    spans: list[dict[str, Any]] = []
    offset = 0
    for line in block_text.splitlines(keepends=True):
        no_eol = line.rstrip("\r\n")
        spans.append(
            {
                "start": offset,
                "end_no_eol": offset + len(no_eol),
                "end_full": offset + len(line),
                "text": no_eol,
            }
        )
        offset += len(line)
    return spans


def apply_text_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    """Apply (start, end, replacement) edits back-to-front."""
    for s, e, r in sorted(edits, reverse=True):
        text = text[:s] + r + text[e:]
    return text


# ---------------------------------------------------------------------------
# Internal CST helpers
# ---------------------------------------------------------------------------


def _new_parser() -> VeribleParser:
    return VeribleParser()


def _parse_tree(source: str, parser: Optional[VeribleParser] = None):
    p = parser if parser is not None else _new_parser()
    data = p.parse_string(source, options={"gen_tree": True, "skip_null": True})
    if data is None or data.tree is None:
        raise ValueError("Unable to parse source")
    if data.errors:
        # Keep behavior permissive: allow edits when tree exists even with warnings.
        pass
    return data.tree


def _desc_text(node: Any) -> str:
    text = getattr(node, "text", "")
    return text.strip() if isinstance(text, str) else ""


def _find_module_nodes(tree: Any) -> list[Any]:
    return tree.find_all({"tag": "kModuleDeclaration"}, iter_=PreOrderTreeIterator)


def _header_port_decl_list(header: Any) -> Optional[Any]:
    if not header:
        return None
    return header.find({"tag": "kPortDeclarationList"}, iter_=PreOrderTreeIterator)


def _header_port_paren_group(header: Any) -> Optional[Any]:
    plist = _header_port_decl_list(header)
    cur = plist
    while cur is not None:
        if getattr(cur, "tag", "") == "kParenGroup":
            return cur
        cur = getattr(cur, "parent", None)
    return None


def _module_name(mod: Any, source: str) -> str:
    header = mod.find({"tag": "kModuleHeader"}, iter_=PreOrderTreeIterator)
    if not header:
        return "<unknown>"

    # Prefer direct header child identifier to avoid picking nested identifiers.
    for child in getattr(header, "children", []):
        if getattr(child, "tag", "") in ("SymbolIdentifier", "EscapedIdentifier"):
            name = _desc_text(child)
            if name:
                return name

    n = header.find({"tag": ["SymbolIdentifier", "EscapedIdentifier"]}, iter_=PreOrderTreeIterator)
    return _desc_text(n) if n else "<unknown>"


def _is_ansi_module(mod: Any) -> bool:
    header = mod.find({"tag": "kModuleHeader"}, iter_=PreOrderTreeIterator)
    if not header:
        return False
    plist = _header_port_decl_list(header)
    if not plist:
        return False
    ansi_decls = plist.find_all({"tag": "kPortDeclaration"}, iter_=PreOrderTreeIterator)
    non_ansi_ports = plist.find_all({"tag": "kPort"}, iter_=PreOrderTreeIterator)
    return bool(ansi_decls) and not non_ansi_ports


def _extract_port_fields_from_decl(pd: Any, source: str) -> dict[str, str]:
    direction = ""
    ptype = ""
    width = ""
    dimension = ""
    name = ""

    for tag in ("input", "output", "inout", "ref"):
        d = pd.find({"tag": tag}, iter_=PreOrderTreeIterator)
        if d:
            direction = tag
            break

    dt = pd.find({"tag": "kDataType"}, iter_=PreOrderTreeIterator)
    if dt:
        packed = dt.find({"tag": "kPackedDimensions"}, iter_=PreOrderTreeIterator)
        if not packed:
            packed = dt.find({"tag": "kDeclarationDimensions"}, iter_=PreOrderTreeIterator)
        if packed:
            width = _desc_text(packed)

        unpacked = dt.find({"tag": "kUnpackedDimensions"}, iter_=PreOrderTreeIterator)
        if unpacked:
            dimension = _desc_text(unpacked)

        for tag in COMMON_TYPES:
            t = dt.find({"tag": tag}, iter_=PreOrderTreeIterator)
            if t:
                ptype = tag
                break

    ids = pd.find_all({"tag": ["SymbolIdentifier", "EscapedIdentifier"]}, iter_=PreOrderTreeIterator)
    for i in ids:
        t = _desc_text(i)
        if t and t not in _KEYWORDS:
            name = t

    if not ptype and direction:
        for tag in ("wire", "reg", "logic", "bit", "tri"):
            t = pd.find({"tag": tag}, iter_=PreOrderTreeIterator)
            if t:
                ptype = tag
                break

    return {
        "name": name,
        "direction": direction,
        "type": ptype,
        "width": width,
        "dimension": dimension,
    }


def _extract_ports(mod: Any, source: str, is_ansi: bool) -> list[dict[str, str]]:
    ports: list[dict[str, str]] = []
    header = mod.find({"tag": "kModuleHeader"}, iter_=PreOrderTreeIterator)
    if not header:
        return ports

    plist = _header_port_decl_list(header)
    if not plist:
        return ports

    if is_ansi:
        for pd in plist.find_all({"tag": "kPortDeclaration"}, iter_=PreOrderTreeIterator):
            info = _extract_port_fields_from_decl(pd, source)
            if info["name"]:
                ports.append(info)
        return ports

    # non-ANSI: header has names only; direction/type/width come from body declarations
    for p in plist.find_all({"tag": "kPort"}, iter_=PreOrderTreeIterator):
        pid = p.find({"tag": ["SymbolIdentifier", "EscapedIdentifier"]}, iter_=PreOrderTreeIterator)
        if pid:
            nm = _desc_text(pid)
            if nm:
                ports.append(
                    {
                        "name": nm,
                        "direction": "",
                        "type": "",
                        "width": "",
                        "dimension": "",
                    }
                )

    by_name = {p["name"]: p for p in ports}
    for pd in mod.find_all({"tag": "kModulePortDeclaration"}, iter_=PreOrderTreeIterator):
        info = _extract_port_fields_from_decl(pd, source)
        if not info["direction"] and not info["type"] and not info["width"]:
            continue

        ids = pd.find_all({"tag": ["SymbolIdentifier", "EscapedIdentifier"]}, iter_=PreOrderTreeIterator)
        for sid in ids:
            nm = _desc_text(sid)
            if nm in by_name:
                by_name[nm]["direction"] = info["direction"]
                by_name[nm]["type"] = info["type"]
                by_name[nm]["width"] = info["width"]
                by_name[nm]["dimension"] = info["dimension"]

    return ports


def _extract_param_names(mod: Any) -> list[str]:
    out: list[str] = []
    header = mod.find({"tag": "kModuleHeader"}, iter_=PreOrderTreeIterator)
    if not header:
        return out
    for p in header.find_all({"tag": "kParamDeclaration"}, iter_=PreOrderTreeIterator):
        pid = p.find({"tag": ["SymbolIdentifier", "EscapedIdentifier"]}, iter_=PreOrderTreeIterator)
        if pid:
            t = _desc_text(pid)
            if t:
                out.append(t)
    return out


def _extract_instances(mod: Any) -> list[dict[str, Any]]:
    source_bytes = getattr(mod.syntax_data, "source_code", b"") if getattr(mod, "syntax_data", None) else b""
    source = source_bytes.decode("utf-8", errors="ignore") if source_bytes else ""
    if not source:
        return []

    def _pick_inst_type(inst_base: Any) -> str:
        itype = inst_base.find({"tag": "kInstantiationType"}, iter_=PreOrderTreeIterator)
        if not itype:
            return ""
        for sid in itype.find_all({"tag": ["SymbolIdentifier", "EscapedIdentifier"]}, iter_=PreOrderTreeIterator):
            t = _desc_text(sid)
            if t and t not in _KEYWORDS and t not in COMMON_TYPES:
                return t
        return ""

    def _pick_gate_instance_name(ginst: Any, gi_start: int, gi_end: int) -> tuple[str, int]:
        # Prefer direct child identifier, which is the instance name in GateInstance.
        for child in getattr(ginst, "children", []):
            if getattr(child, "tag", "") in ("SymbolIdentifier", "EscapedIdentifier"):
                name = _desc_text(child)
                if name:
                    return name, int(child.start) if child.start is not None else gi_start

        # Fallback: first identifier token in the GateInstance subtree.
        sid = ginst.find({"tag": ["SymbolIdentifier", "EscapedIdentifier"]}, iter_=PreOrderTreeIterator)
        if sid:
            name = _desc_text(sid)
            if name:
                return name, int(sid.start) if sid.start is not None else gi_start

        return "", gi_start

    instances: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    for inst_base in mod.find_all({"tag": "kInstantiationBase"}, iter_=PreOrderTreeIterator):
        inst_type = _pick_inst_type(inst_base)
        if not inst_type:
            # Skip declarations/non-module-instantiation statements that may share this node kind.
            continue

        for ginst in inst_base.find_all({"tag": "kGateInstance"}, iter_=PreOrderTreeIterator):
            gs, ge = get_node_range(ginst)
            if gs >= 999999 or ge <= gs:
                continue

            inst_name, inst_offset = _pick_gate_instance_name(ginst, gs, ge)
            if not inst_name or inst_name in _KEYWORDS:
                continue

            port_close_idx = -1
            paren = ginst.find({"tag": "kParenGroup"}, iter_=PreOrderTreeIterator)
            if paren:
                close_leaf = paren.find({"tag": ")"}, iter_=PostOrderTreeIterator, reverse_children=True)
                if close_leaf and close_leaf.start is not None:
                    port_close_idx = int(close_leaf.start)

            if port_close_idx < 0:
                continue

            key = (inst_name, inst_offset)
            if key in seen:
                continue
            seen.add(key)

            instances.append(
                {
                    "name": inst_name,
                    "type": inst_type,
                    "offset": inst_offset,
                    "port_close_idx": port_close_idx,
                }
            )

    return instances


def _module_offsets(mod: Any) -> dict[str, int]:
    header = mod.find({"tag": "kModuleHeader"}, iter_=PreOrderTreeIterator)
    close_idx = -1
    if header:
        paren = _header_port_paren_group(header)
        if paren:
            close_leaf = paren.find({"tag": ")"}, iter_=PostOrderTreeIterator, reverse_children=True)
            if close_leaf and close_leaf.start is not None:
                close_idx = close_leaf.start

    mod_start, mod_end = get_node_range(mod)

    decl_insert_idx = -1
    item_list = mod.find({"tag": "kModuleItemList"}, iter_=PreOrderTreeIterator)
    if item_list:
        for c in getattr(item_list, "children", []):
            s, _ = get_node_range(c)
            if s < 999999:
                decl_insert_idx = s
                break

    if decl_insert_idx < 0:
        end_kw = mod.find({"tag": "endmodule"}, iter_=PostOrderTreeIterator, reverse_children=True)
        if end_kw and end_kw.start is not None:
            decl_insert_idx = end_kw.start

    return {
        "port_insert_idx": close_idx,
        "decl_insert_idx": decl_insert_idx,
        "mod_start": mod_start,
        "mod_end": mod_end,
    }


def _analyze_modules(source: str, parser: Optional[VeribleParser] = None) -> list[dict[str, Any]]:
    tree = _parse_tree(source, parser)
    out: list[dict[str, Any]] = []
    for mod in _find_module_nodes(tree):
        name = _module_name(mod, source)
        is_ansi = _is_ansi_module(mod)
        out.append(
            {
                "name": name,
                "is_ansi": is_ansi,
                "cst": mod,
                "ports": _extract_ports(mod, source, is_ansi),
                "param_names": _extract_param_names(mod),
                "instances": _extract_instances(mod),
            }
        )
    return out


def _get_module_info(source: str, module_name: Optional[str], parser: Optional[VeribleParser] = None) -> dict[str, Any]:
    mods = _analyze_modules(source, parser)
    if not mods:
        raise ValueError("No module found in source")
    if module_name:
        for m in mods:
            if m["name"] == module_name:
                return m
        available = ", ".join(m["name"] for m in mods)
        raise ValueError(f"Module '{module_name}' not found. Available: {available}")
    if len(mods) > 1:
        names = "\n  - ".join(m["name"] for m in mods)
        raise ValueError(f"File contains multiple modules:\n  - {names}\nPlease specify --module.")
    return mods[0]


# ---------------------------------------------------------------------------
# Internal text-edit helpers
# ---------------------------------------------------------------------------


def _detect_indent_at(source: str, byte_pos: int) -> str:
    line_start = source.rfind("\n", 0, byte_pos) + 1
    line_text = source[line_start:byte_pos]
    if line_text.strip():
        m = re.match(r"^(\s*)", line_text)
        return m.group(1) if m else "    "
    for prev_line in reversed(source[:line_start].splitlines()):
        s = prev_line.strip()
        if s and not s.startswith(("//", "/*", "*")):
            m2 = re.match(r"^(\s*)", prev_line)
            return m2.group(1) if m2 else "    "
    return "    "


def _last_content_line_before(text: str, boundary_pos: int) -> Optional[dict[str, Any]]:
    spans = get_line_spans(text[:boundary_pos])
    for row in reversed(spans):
        s = row["text"].strip()
        if s and not s.startswith(("//", "/*", "*")):
            return row
    return None


def _append_line_before_close(source: str, close_idx: int, new_content: str) -> str:
    newline = detect_newline(source)
    indent = _detect_indent_at(source, close_idx)
    last = _last_content_line_before(source, close_idx)
    if last:
        abs_start, abs_end = last["start"], last["end_full"]
        updated = append_comma_to_port_line(source[abs_start:abs_end])
        updated += indent + new_content + newline
        return source[:abs_start] + updated + source[abs_end:]
    return source[:close_idx] + newline + indent + new_content + newline + source[close_idx:]


def _append_port_line_before_close(source: str, close_idx: int, new_content: str) -> str:
    """Insert module port on its own line before ')'."""
    newline = detect_newline(source)
    indent = _detect_indent_at(source, close_idx)

    # Single-line header case: module m(input logic a);
    # Force a multiline layout for newly inserted ports.
    open_idx = source.rfind("(", 0, close_idx)
    if open_idx >= 0:
        line_start = source.rfind("\n", 0, open_idx) + 1
        if "\n" not in source[open_idx:close_idx]:
            j = close_idx - 1
            while j >= 0 and source[j].isspace():
                j -= 1
            out = source
            if j >= 0 and out[j] not in "(,":
                out = out[: j + 1] + "," + out[j + 1 :]
                close_idx += 1

            cont_indent = indent
            insert = newline + cont_indent + new_content + newline
            return out[:close_idx] + insert + out[close_idx:]

    last = _last_content_line_before(source, close_idx)
    if last:
        abs_start, abs_end = last["start"], last["end_full"]
        updated = append_comma_to_port_line(source[abs_start:abs_end])
        updated += indent + new_content + newline
        return source[:abs_start] + updated + source[abs_end:]
    return source[:close_idx] + newline + indent + new_content + newline + source[close_idx:]


def _append_connection_line_before_close(source: str, close_idx: int, new_content: str) -> str:
    """Insert instance connection on its own line before ')'."""
    newline = detect_newline(source)
    indent = _detect_indent_at(source, close_idx)
    last = _last_content_line_before(source, close_idx)
    if last:
        abs_start, abs_end = last["start"], last["end_full"]
        updated = append_comma_to_port_line(source[abs_start:abs_end])
        if not (updated.endswith("\n") or updated.endswith("\r\n")):
            updated += newline
        updated += indent + new_content + newline
        return source[:abs_start] + updated + source[abs_end:]
    return source[:close_idx] + newline + indent + new_content + newline + source[close_idx:]


def _align_to_char(source: str, idx: int, ch: str) -> int:
    """Align index to a nearby target character within a strict local window.

    Returns -1 if no nearby match is found.
    """
    if not source:
        return -1
    i = max(0, min(idx, len(source) - 1))
    if source[i] == ch:
        return i

    # Keep correction local to avoid latching onto unrelated parentheses.
    for d in range(1, 3):
        l = i - d
        r = i + d
        if l >= 0 and source[l] == ch:
            return l
        if r < len(source) and source[r] == ch:
            return r
    return -1


def _validate_edit_or_raise(
    original_source: str,
    edited_source: str,
    parser: Optional[VeribleParser],
    action: str,
) -> str:
    """Validate edited source by reparsing; raise without mutating on failure."""
    try:
        _parse_tree(edited_source, parser)
    except Exception as exc:
        raise ValueError(f"{action} produced invalid syntax; change reverted: {exc}")
    return edited_source


def _insert_decl_before_body(source: str, decl_insert_idx: int, decl_text: str) -> str:
    newline = detect_newline(source)
    body_indent = _detect_indent_at(source, decl_insert_idx)
    line_start = source.rfind("\n", 0, decl_insert_idx) + 1
    insert_text = body_indent + decl_text + newline
    return source[:line_start] + insert_text + source[line_start:]


def _insert_decl_after_index(source: str, after_idx: int, decl_text: str) -> str:
    """Insert declaration on a new line after a known statement end index."""
    newline = detect_newline(source)
    line_start = source.rfind("\n", 0, after_idx) + 1
    indent = _detect_indent_at(source, line_start)
    line_end = source.find("\n", after_idx)
    if line_end < 0:
        line_end = len(source)
    else:
        line_end += 1
    insert_text = indent + decl_text + newline
    return source[:line_end] + insert_text + source[line_end:]


def _nonansi_decl_insert_idx(source: str, mod_node: Any) -> int:
    """Prefer insertion after ';' of leading non-ANSI port declarations."""
    item_list = mod_node.find({"tag": "kModuleItemList"}, iter_=PreOrderTreeIterator)
    if item_list:
        children = list(getattr(item_list, "children", []))
        last_port_decl = None
        next_non_port_start = -1
        for idx, c in enumerate(children):
            if getattr(c, "tag", "") != "kModulePortDeclaration":
                s, _ = get_node_range(c)
                if s < 999999:
                    next_non_port_start = s
                break
            last_port_decl = c

        if last_port_decl is not None:
            s, e = get_node_range(last_port_decl)
            if s < 999999:
                search_start = max(0, s - 2)
                if next_non_port_start > search_start:
                    search_end = next_non_port_start
                else:
                    search_end = min(len(source), max(e + 128, search_start + 1))

                semi = source.find(";", search_start, search_end)
                if semi >= 0:
                    return semi

    return _module_offsets(mod_node)["decl_insert_idx"]


def _apply_ansi_add_port(source: str, close_idx: int, port_spec: dict[str, str]) -> str:
    return _append_port_line_before_close(source, close_idx, compose_port_decl(port_spec))


def _apply_nonansi_add_port(
    source: str,
    parser: Optional[VeribleParser],
    module_name: str,
    close_idx: int,
    port_spec: dict[str, str],
) -> str:
    # 1) add port name in header list
    with_name = _append_port_line_before_close(source, close_idx, port_spec["name"])

    # 2) reparse and add declaration after existing non-ANSI port declarations
    mi2 = _get_module_info(with_name, module_name, parser)
    after_idx = _nonansi_decl_insert_idx(with_name, mi2["cst"])
    if after_idx < 0:
        return with_name

    decl = compose_port_decl(port_spec) + ";"
    if after_idx >= len(with_name):
        return _insert_decl_before_body(with_name, after_idx, decl)
    if with_name[after_idx] != ";":
        return _insert_decl_before_body(with_name, after_idx, decl)
    return _insert_decl_after_index(with_name, after_idx, decl)


def _find_matching_paren_from(source: str, open_idx: int) -> int:
    """Find matching ')' from a known opening '(' index in source text."""
    if open_idx < 0 or open_idx >= len(source) or source[open_idx] != "(":
        return -1
    depth = 0
    i = open_idx
    while i < len(source):
        ch = source[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


# ---------------------------------------------------------------------------
# Edit operations
# ---------------------------------------------------------------------------


def add_port(
    source: str,
    parser: Optional[VeribleParser],
    module_name: str,
    port_spec: dict[str, str],
) -> str:
    """Add port to module (idempotent).

    ANSI: insert full declaration before ')'.
    non-ANSI: insert port name before ')' and declaration in module body.
    """
    mi = _get_module_info(source, module_name, parser)
    offsets = _module_offsets(mi["cst"])
    port_insert_idx = offsets["port_insert_idx"]
    if port_insert_idx < 0:
        raise ValueError("Cannot locate port list close parenthesis")
    port_insert_idx = _align_to_char(source, port_insert_idx, ")")
    if port_insert_idx < 0:
        raise ValueError("Unable to align module port insertion point")

    if any(p.get("name") == port_spec.get("name") for p in mi.get("ports", [])):
        return source

    if mi["is_ansi"]:
        edited = _apply_ansi_add_port(source, port_insert_idx, port_spec)
        return _validate_edit_or_raise(source, edited, parser, "add_port(ansi)")

    edited = _apply_nonansi_add_port(source, parser, module_name, port_insert_idx, port_spec)
    return _validate_edit_or_raise(source, edited, parser, "add_port(non-ansi)")


def _signal_declared_in_module(mod_node: Any, signal_name: str) -> bool:
    for tag in (
        "kDataDeclaration",
        "kNetDeclaration",
        "kVariableDeclarationAssign",
        "kModulePortDeclaration",
    ):
        for node in mod_node.find_all({"tag": tag}, iter_=PreOrderTreeIterator):
            for sid in node.find_all({"tag": ["SymbolIdentifier", "EscapedIdentifier"]}, iter_=PreOrderTreeIterator):
                if _desc_text(sid) == signal_name:
                    return True
    return False


def add_wire(
    source: str,
    parser: Optional[VeribleParser],
    module_name: str,
    wire_spec: dict[str, str],
) -> str:
    """Add wire/logic declaration before first body item (idempotent)."""
    mi = _get_module_info(source, module_name, parser)
    decl_insert_idx = _module_offsets(mi["cst"])["decl_insert_idx"]
    if decl_insert_idx < 0:
        raise ValueError("Cannot locate module body")

    name = wire_spec.get("name", "")
    if not name:
        raise ValueError("wire name is required")

    if _signal_declared_in_module(mi["cst"], name):
        return source

    decl = compose_wire_decl(wire_spec) + ";"
    edited = _insert_decl_before_body(source, decl_insert_idx, decl)
    return _validate_edit_or_raise(source, edited, parser, "add_wire")


def _find_gate_instance_node(mod_node: Any, instname: str, offset: Optional[int] = None) -> Optional[Any]:
    candidates: list[Any] = []
    for ginst in mod_node.find_all({"tag": "kGateInstance"}, iter_=PreOrderTreeIterator):
        sid = None
        for child in getattr(ginst, "children", []):
            if getattr(child, "tag", "") in ("SymbolIdentifier", "EscapedIdentifier"):
                sid = child
                break
        if sid is None:
            sid = ginst.find({"tag": ["SymbolIdentifier", "EscapedIdentifier"]}, iter_=PreOrderTreeIterator)
        if sid and _desc_text(sid) == instname:
            candidates.append(ginst)

    if not candidates:
        return None
    if offset is None:
        return candidates[0]

    def _distance(node: Any) -> int:
        s, _ = get_node_range(node)
        return abs(s - offset) if s < 999999 else 1_000_000_000

    return min(candidates, key=_distance)


def _existing_named_port_wire(ginst: Any, port_name: str, source: str) -> Optional[str]:
    for ap in ginst.find_all({"tag": "kActualNamedPort"}, iter_=PreOrderTreeIterator):
        pname = ""
        for child in getattr(ap, "children", []):
            if getattr(child, "tag", "") in ("SymbolIdentifier", "EscapedIdentifier"):
                pname = _desc_text(child)
                if pname:
                    break

        if not pname:
            sid = ap.find({"tag": ["SymbolIdentifier", "EscapedIdentifier"]}, iter_=PreOrderTreeIterator)
            pname = _desc_text(sid) if sid else ""

        if pname != port_name:
            continue

        expr = ap.find({"tag": "kExpression"}, iter_=PreOrderTreeIterator)
        if not expr:
            return ""
        es, ee = get_node_range(expr)
        if es < 999999 and ee > es:
            return source[es:ee].strip()
        return _desc_text(expr)

    return None


def add_inst_port(
    source: str,
    parser: Optional[VeribleParser],
    module_name: str,
    spec: dict[str, str],
) -> str:
    """Add .port(wire) to instance connection list (idempotent)."""
    instname = spec["instname"]
    port_name = spec["port"]
    wire = spec.get("wire", "")

    mi = _get_module_info(source, module_name, parser)
    inst = next((i for i in mi.get("instances", []) if i.get("name") == instname), None)
    if inst is None:
        raise ValueError(f"Instance '{instname}' not found in module '{module_name}'")

    close_idx = int(inst.get("port_close_idx", -1))
    if close_idx < 0:
        raise ValueError(f"Cannot locate closing ')' for instance '{instname}'")
    close_idx = _align_to_char(source, close_idx, ")")
    if close_idx < 0:
        raise ValueError(f"Unable to align closing ')' for instance '{instname}'")

    # Derive the instance-local paren range from its own offset.
    inst_offset = int(inst.get("offset", -1))
    local_open_idx = -1
    if inst_offset >= 0:
        local_open_idx = source.find("(", inst_offset, close_idx + 1)
        if local_open_idx >= 0:
            local_close = _find_matching_paren_from(source, local_open_idx)
            if local_close >= 0:
                close_idx = local_close

    if local_open_idx < 0:
        local_open_idx = source.rfind("(", 0, close_idx)

    ginst = _find_gate_instance_node(mi["cst"], instname, inst_offset if inst_offset >= 0 else None)
    if ginst is not None:
        old_wire = _existing_named_port_wire(ginst, port_name, source)
        if old_wire is not None:
            new_wire = wire.strip()
            if old_wire.strip() == new_wire:
                return source
            raise ValueError(
                f"Instance '{instname}' port '{port_name}' already connected to "
                f"'{old_wire}', requested '{new_wire}'"
            )

    new_conn = f".{port_name}({wire})" if wire else f".{port_name}()"
    edited = _append_connection_line_before_close(source, close_idx, new_conn)
    return _validate_edit_or_raise(source, edited, parser, "add_inst_port")


# ---------------------------------------------------------------------------
# Instantiation template
# ---------------------------------------------------------------------------


def gen_inst(
    module_info: dict,
    include_params: bool = True,
    instance_name: Optional[str] = None,
    port_map: Optional[dict[str, str]] = None,
) -> str:
    """Generate instantiation template for a module."""
    mod_name = module_info["name"]
    ports = module_info.get("ports", [])
    param_names = module_info.get("param_names", [])
    indent = "    "

    inst_name = (instance_name or f"u_{mod_name}").strip() or f"u_{mod_name}"
    valid_ports = [p for p in ports if p.get("name", "").strip()]
    port_names = [p["name"].strip() for p in valid_ports]
    mapped_names = [(port_map or {}).get(pn, pn) for pn in port_names]
    params_to_use = param_names if (include_params and param_names) else []

    lines: list[str] = []
    if params_to_use:
        lines.append(f"{mod_name} #(")
        for idx, pname in enumerate(params_to_use):
            comma = "," if idx < len(params_to_use) - 1 else ""
            lines.append(f"{indent}.{pname}({pname}){comma}")
        lines.append(f") {inst_name} (")
    else:
        lines.append(f"{mod_name} {inst_name} (")

    max_pn = max((len(pn) for pn in port_names), default=0)
    max_wn = max((len(wn) for wn in mapped_names), default=0)

    cores, comments = [], []
    for idx, p in enumerate(valid_ports):
        pname, wname = port_names[idx], mapped_names[idx]
        comma = "," if idx < len(valid_ports) - 1 else ""
        direction = p.get("direction", "").strip()
        ptype = p.get("type", "").strip()
        cparts = [x for x in (direction, ptype) if x]
        comment = "// " + " ".join(cparts) if cparts else ""
        core = f"{indent}.{pname:<{max_pn}}({wname:<{max_wn}}){comma}"
        cores.append(core)
        comments.append(comment)

    line_w = max((len(c) for c in cores), default=0)
    for core, comment in zip(cores, comments):
        lines.append(f"{core:<{line_w}} {comment}" if comment else core)

    lines.append(");")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# vedit – OOP API
# ---------------------------------------------------------------------------


class vedit:
    """Module editor using verible-verilog-syntax as parsing backend."""

    def __init__(self, source: str, module_name: str) -> None:
        self._source = source
        self._module_name = module_name

    def _get_parser(self) -> VeribleParser:
        return _new_parser()

    def _get_module_info(self) -> dict[str, Any]:
        return _get_module_info(self._source, self._module_name, self._get_parser())

    @classmethod
    def from_file(
        cls,
        path: Union[Path, str],
        module_name: Optional[str] = None,
    ) -> "vedit":
        fpath = Path(path)
        if not fpath.exists():
            raise FileNotFoundError(f"file not found: {fpath}")
        source = fpath.read_text(encoding="utf-8")
        if module_name is None:
            mods = _analyze_modules(source, _new_parser())
            if len(mods) > 1:
                names = "\n  - ".join(m["name"] for m in mods)
                raise ValueError(f"multiple modules:\n  - {names}\nSpecify --module")
            module_name = mods[0]["name"]
        return cls(source, module_name)

    @classmethod
    def list_modules_in_file(cls, path: Union[Path, str]) -> list[str]:
        source_text = Path(path).read_text(encoding="utf-8")
        return [m["name"] for m in _analyze_modules(source_text, _new_parser())]

    module_text = property(lambda self: self._source)
    module_name = property(lambda self: self._module_name)
    ports = property(lambda self: self._get_module_info().get("ports", []))
    instances = property(lambda self: self._get_module_info().get("instances", []))
    param_names = property(lambda self: self._get_module_info().get("param_names", []))
    is_ansi = property(lambda self: self._get_module_info().get("is_ansi", True))

    def add_port(self, port_dict: dict[str, dict[str, str]]) -> "vedit":
        for name, attrs in port_dict.items():
            name = name.strip()
            direction = attrs.get("direction", "").strip().lower()
            if not name:
                raise ValueError("name is required")
            if direction and direction not in {"input", "output", "inout", "ref"}:
                raise ValueError("direction must be input/output/inout/ref")

            info = {
                "direction": direction,
                "type": attrs.get("type", "").strip(),
                "width": normalize_width(attrs.get("width", "")),
                "name": name,
                "dimension": normalize_dimension(attrs.get("dimension", "")),
            }
            self._source = add_port(self._source, self._get_parser(), self._module_name, info)
        return self

    def add_wire(self, wire_dict: dict[str, dict[str, str]]) -> "vedit":
        for name, attrs in wire_dict.items():
            name = name.strip()
            if not name:
                raise ValueError("name is required")
            info = {
                "type": attrs.get("type", "wire").strip() or "wire",
                "width": normalize_width(attrs.get("width", "")),
                "name": name,
                "dimension": normalize_dimension(attrs.get("dimension", "")),
            }
            self._source = add_wire(self._source, self._get_parser(), self._module_name, info)
        return self

    def add_inst_port(self, inst_port_dict: dict[str, dict[str, dict[str, str]]]) -> "vedit":
        for instname, ports in inst_port_dict.items():
            for port_name, wire_info in ports.items():
                self._source = add_inst_port(
                    self._source,
                    self._get_parser(),
                    self._module_name,
                    {
                        "instname": instname,
                        "port": port_name,
                        "wire": wire_info.get("wire", ""),
                    },
                )
        return self

    def add_inst_ports(self, instname: str, port_wires: dict[str, str]) -> "vedit":
        nested = {instname: {p: {"wire": w} for p, w in port_wires.items()}}
        return self.add_inst_port(nested)

    def gen_inst(
        self,
        include_params: bool = True,
        instance_name: Optional[str] = None,
        port_map: Optional[dict[str, str]] = None,
    ) -> str:
        return gen_inst(
            self._get_module_info(),
            include_params=include_params,
            instance_name=instance_name,
            port_map=port_map,
        )

    def analyze(self) -> dict[str, Any]:
        mi = self._get_module_info()
        result = dict(mi)
        result.setdefault("module_name", result.get("name", ""))
        result["module_text"] = self._source
        return result

    def get_hier(self) -> dict[str, Any]:
        mi = self._get_module_info()
        return {"module": mi.get("name", ""), "instances": mi.get("instances", [])}
