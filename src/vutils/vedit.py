"""Verilog/SystemVerilog module editing utilities built on vparser CST."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional, TypedDict, Union

from .vparser import (
    COMMON_TYPES,
    INVALID_POSITION,
    INVALID_RANGE,
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

_OPAQUE_SOURCE = r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\[\s\S]|[^"\\])*"|\\[^\s]+'
_PAREN_PATTERN = re.compile(_OPAQUE_SOURCE + r'|[()]')
_LIST_TOKEN_PATTERN = re.compile(
    _OPAQUE_SOURCE + r'|`(?:ifdef|ifndef|elsif|else|endif)\b[^\r\n]*|[A-Za-z_$][\w$]*|[^\s]')


# ─────────────────────────────────────────────────────────────────────────────
# Type Definitions
# ─────────────────────────────────────────────────────────────────────────────


class LineSpan(TypedDict):
    """Line span metadata for text editing."""
    start: int
    end_no_eol: int
    end_full: int
    text: str


class SignalSpec(TypedDict, total=False):
    """Signal specification for parsing and editing."""
    type: str
    width: str
    name: str
    dimension: str
    direction: str  # For ports


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


def _compose_decl(info: dict[str, str], fields: tuple[str, ...]) -> str:
    """Join non-empty signal field values with a single space."""
    return " ".join(v for f in fields if (v := info.get(f, "").strip()))


def compose_port_decl(info: dict[str, str]) -> str:
    return _compose_decl(info, ("direction", "type", "width", "name", "dimension"))


def compose_wire_decl(info: dict[str, str]) -> str:
    effective = {**info, "type": info.get("type", "wire") or "wire"}
    return _compose_decl(effective, ("type", "width", "name", "dimension"))


def get_line_spans(block_text: str) -> list[LineSpan]:
    """Split text into lines with byte-offset metadata."""
    spans: list[LineSpan] = []
    offset = 0
    for line in block_text.splitlines(keepends=True):
        no_eol = line.rstrip("\r\n")
        spans.append(
            LineSpan(
                start=offset,
                end_no_eol=offset + len(no_eol),
                end_full=offset + len(line),
                text=no_eol,
            )
        )
        offset += len(line)
    return spans


def apply_text_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    """Apply nonoverlapping edits with one assembly of the source."""
    parts = []
    cursor = 0
    for start, end, replacement in sorted(edits):
        if start < cursor or end < start or end > len(text):
            raise ValueError("Invalid or overlapping text edits")
        parts.extend((text[cursor:start], replacement))
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


# ---------------------------------------------------------------------------
# Internal CST helpers
# ---------------------------------------------------------------------------


def _find_first_identifier_child(node: Any) -> Optional[Any]:
    """Find first direct child node with identifier tag."""
    for child in getattr(node, "children", []):
        if getattr(child, "tag", "") in ("SymbolIdentifier", "EscapedIdentifier"):
            return child
    return None


def _find_first_identifier_in_subtree(node: Any) -> Optional[Any]:
    """Find first descendant node with identifier tag."""
    return node.find(
        {"tag": ["SymbolIdentifier", "EscapedIdentifier"]},
        iter_=PreOrderTreeIterator,
    )


def _new_parser() -> VeribleParser:
    return VeribleParser()


def _parse_tree(source: str, parser: Optional[VeribleParser] = None):
    p = parser if parser is not None else _new_parser()
    data = p.parse_string(source, options={"gen_tree": True, "skip_null": True})
    if data is None or data.tree is None:
        raise ValueError("Unable to parse source")
    if data.errors:
        details = "; ".join(f"{e.line + 1}:{e.column + 1}: {e.message or e.phase}" for e in data.errors)
        raise ValueError(f"Invalid Verilog syntax: {details}")
    return data.tree


def _desc_text(node: Any) -> str:
    text = getattr(node, "text", "")
    return text.strip() if isinstance(text, str) else ""


def _find_module_nodes(tree: Any) -> list[Any]:
    return tree.find_all({"tag": "kModuleDeclaration"}, iter_=PreOrderTreeIterator)


def _module_nodes(mod: Any, tags, *, stop_tags=()):
    """Traverse a module's scope without entering nested definitions."""
    stopped = {"kModuleDeclaration", "kFunctionDeclaration", "kTaskDeclaration",
               "kClassDeclaration", *stop_tags}
    stack = [mod]
    while stack:
        node = stack.pop()
        if node is not mod and getattr(node, "tag", "") in stopped:
            continue
        if getattr(node, "tag", "") in tags:
            yield node
        stack.extend(reversed(getattr(node, "children", ())))


def _byte_to_char_index(source: str, byte_index: int) -> int:
    """Convert a Verible byte offset only at the Python string-edit boundary."""
    if byte_index < 0 or source.isascii():
        return byte_index
    return len(source.encode("utf-8")[:byte_index].decode("utf-8"))


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
    # In an ANSI list, subsequent names may inherit the preceding declaration
    # (input [7:0] a, b); Verible represents those names as kPort nodes too.
    return bool(ansi_decls)


def _extract_port_fields_from_decl(pd: Any, source: str) -> dict[str, str]:
    direction = ""
    ptype = ""
    width = ""
    dimension = ""
    name = ""

    d = pd.find({"tag": ["input", "output", "inout", "ref"]}, iter_=PreOrderTreeIterator)
    if d:
        direction = getattr(d, "tag", "")

    dt = pd.find({"tag": "kDataType"}, iter_=PreOrderTreeIterator)
    if dt:
        packed = dt.find({"tag": "kPackedDimensions"}, iter_=PreOrderTreeIterator)
        if not packed:
            packed = dt.find({"tag": "kDeclarationDimensions"}, iter_=PreOrderTreeIterator)
        if packed:
            width = _desc_text(packed)

        ptype = _desc_text(dt)
        if width:
            ptype = ptype.replace(width, "", 1).strip()

    # Array-bound identifiers must not replace the actual port name.
    for child in getattr(pd, "children", []):
        tag = getattr(child, "tag", "")
        if tag == "kUnpackedDimensions":
            dimension = _desc_text(child)
        elif tag in ("SymbolIdentifier", "EscapedIdentifier", "kUnqualifiedId"):
            name = _desc_text(child)
        elif tag in ("wire", "reg", "logic", "bit", "tri"):
            ptype = " ".join(filter(None, (tag, ptype)))

    ids = pd.find_all({"tag": ["SymbolIdentifier", "EscapedIdentifier"]}, iter_=PreOrderTreeIterator)
    if not name:
        for i in ids:
            t = _desc_text(i)
            if t and t not in _KEYWORDS:
                name = t

    if not ptype and direction:
        t = pd.find({"tag": ["wire", "reg", "logic", "bit", "tri"]}, iter_=PreOrderTreeIterator)
        if t:
            ptype = getattr(t, "tag", "")

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
        previous: dict[str, str] = {}
        for pd in getattr(plist, "children", []):
            if pd.tag == "kPortDeclaration":
                info = _extract_port_fields_from_decl(pd, source)
                previous = info
            elif pd.tag == "kPort":
                sid = pd.find({"tag": ["SymbolIdentifier", "EscapedIdentifier"]}, iter_=PreOrderTreeIterator)
                dimensions = pd.find({"tag": ["kUnpackedDimensions", "kDeclarationDimensions"]}, iter_=PreOrderTreeIterator)
                info = {**previous, "name": _desc_text(sid) if sid else "",
                        "dimension": _desc_text(dimensions) if dimensions else ""}
            else:
                continue
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
    for pd in _module_nodes(mod, {"kModulePortDeclaration"}):
        info = _extract_port_fields_from_decl(pd, source)
        if not info["direction"] and not info["type"] and not info["width"]:
            continue

        declarators = pd.find_all({"tag": "kIdentifierUnpackedDimensions"}, iter_=PreOrderTreeIterator)
        for declarator in declarators:
            sid = _find_first_identifier_child(declarator)
            if sid is None:
                sid = _find_first_identifier_in_subtree(declarator)
            nm = _desc_text(sid) if sid else ""
            if nm in by_name:
                by_name[nm]["direction"] = info["direction"]
                by_name[nm]["type"] = info["type"]
                by_name[nm]["width"] = info["width"]
                dimensions = declarator.find({"tag": "kUnpackedDimensions"}, iter_=PreOrderTreeIterator)
                by_name[nm]["dimension"] = _desc_text(dimensions) if dimensions else ""

    return ports


def _extract_param_names(mod: Any) -> list[str]:
    out: list[str] = []
    header = mod.find({"tag": "kModuleHeader"}, iter_=PreOrderTreeIterator)
    if not header:
        return out
    for p in header.find_all({"tag": "kParamDeclaration"}, iter_=PreOrderTreeIterator):
        declaration = p.find({"tag": ["kParamType", "kTypeAssignment"]}, iter_=PreOrderTreeIterator)
        pid = _find_first_identifier_child(declaration) if declaration else None
        if pid is None and declaration:
            for child in getattr(declaration, "children", []):
                if child.tag == "kUnqualifiedId":
                    pid = _find_first_identifier_in_subtree(child)
                    break
        if pid:
            t = _desc_text(pid)
            if t:
                out.append(t)
    return out


def _extract_instances(mod: Any) -> list[dict[str, Any]]:
    def _pick_inst_type(inst_base: Any) -> str:
        itype = inst_base.find({"tag": "kInstantiationType"}, iter_=PreOrderTreeIterator)
        if not itype:
            return ""
        for sid in itype.find_all(
            {"tag": ["SymbolIdentifier", "EscapedIdentifier", "MacroIdentifier"]},
            iter_=PreOrderTreeIterator,
        ):
            t = _desc_text(sid)
            if t and t not in _KEYWORDS and t not in COMMON_TYPES:
                return t
        return ""

    def _pick_gate_instance_name(ginst: Any, gi_start: int, gi_end: int) -> tuple[str, int]:
        # Prefer direct child identifier, which is the instance name in GateInstance.
        sid = _find_first_identifier_child(ginst)
        if sid:
            name = _desc_text(sid)
            if name:
                return name, int(sid.start) if sid.start is not None else gi_start

        # Fallback: first identifier token in the GateInstance subtree.
        sid = _find_first_identifier_in_subtree(ginst)
        if sid:
            name = _desc_text(sid)
            if name:
                return name, int(sid.start) if sid.start is not None else gi_start

        return "", gi_start

    instances: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    for inst_base in _module_nodes(mod, {"kInstantiationBase"}):
        inst_type = _pick_inst_type(inst_base)
        if not inst_type:
            # Skip declarations/non-module-instantiation statements that may share this node kind.
            continue

        for ginst in inst_base.find_all({"tag": "kGateInstance"}, iter_=PreOrderTreeIterator):
            gs, ge = get_node_range(ginst)
            if gs >= INVALID_POSITION or ge <= gs:
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


def _module_offsets(mod: Any, source: Optional[str] = None) -> dict[str, int]:
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
            if s < INVALID_POSITION:
                decl_insert_idx = s
                break

    if decl_insert_idx < 0:
        end_kw = mod.find({"tag": "endmodule"}, iter_=PostOrderTreeIterator, reverse_children=True)
        if end_kw and end_kw.start is not None:
            decl_insert_idx = end_kw.start

    offsets = {
        "port_insert_idx": close_idx,
        "decl_insert_idx": decl_insert_idx,
        "mod_start": mod_start,
        "mod_end": mod_end,
    }
    return {key: _byte_to_char_index(source, value) for key, value in offsets.items()} if source is not None else offsets


def _analyze_modules(
    source: str,
    parser: Optional[VeribleParser] = None,
    *,
    include_instances: bool = True,
) -> list[dict[str, Any]]:
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
                "instances": _extract_instances(mod) if include_instances else [],
            }
        )
    return out


def _get_module_info(
    source: str,
    module_name: Optional[str],
    parser: Optional[VeribleParser] = None,
    *,
    include_instances: bool = True,
) -> dict[str, Any]:
    mods = _analyze_modules(source, parser, include_instances=include_instances)
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


class _InterfaceParser(VeribleParser):
    """Internal parser whose partial tree must never enter an editor cache."""

    _transform_tree = staticmethod(VeribleParser._transform_interface_tree)


def _interface_result(mi: dict[str, Any]) -> dict[str, Any]:
    header = mi["cst"].find({"tag": "kModuleHeader"}, iter_=PreOrderTreeIterator)
    params = header.find({"tag": "kFormalParameterListDeclaration"}, iter_=PreOrderTreeIterator) if header else None
    return {
        "name": mi["name"],
        "module_name": mi["name"],
        "is_ansi": mi["is_ansi"],
        "ports": mi["ports"],
        "param_names": mi["param_names"],
        "parameter_declaration": _desc_text(params) if params else "",
    }


def read_interface(source: str, module_name: Optional[str] = None) -> dict[str, Any]:
    """Read ports and header parameters after validating the complete source.

    Construct only interface CST nodes. Return plain metadata without a CST or
    instances; use ``vedit`` for source editing and instance/hierarchy analysis.
    Multiple modules require an explicit name.
    """
    mi = _get_module_info(source, module_name, _InterfaceParser(), include_instances=False)
    return _interface_result(mi)


def read_interface_from_file(
    path: Union[Path, str],
    module_name: Optional[str] = None,
    *,
    auto_select: bool = False,
) -> dict[str, Any]:
    """Read an interface; optionally prefer the filename's module, then first.

    Explicit module names take precedence over ``auto_select``. Unlike editor
    selection, this interface-only API does not select modules by instance.
    """
    fpath = Path(path)
    source = fpath.read_text(encoding="utf-8")
    parser = _InterfaceParser()
    if module_name is not None or not auto_select:
        return _interface_result(_get_module_info(source, module_name, parser, include_instances=False))
    mods = _analyze_modules(source, parser, include_instances=False)
    if not mods:
        raise ValueError("No module found in source")
    selected = next((m for m in mods if m["name"] == fpath.stem), mods[0])
    return _interface_result(selected)


# ---------------------------------------------------------------------------
# Internal text-edit helpers
# ---------------------------------------------------------------------------


def _detect_indent_at(source: str, byte_pos: int) -> str:
    line_start = source.rfind("\n", 0, byte_pos) + 1
    line_text = source[line_start:byte_pos]
    if line_text.strip():
        m = re.match(r"^(\s*)", line_text)
        return m.group(1) if m else "    "
    # Walk backward line-by-line (max 50 lines) without building the full line list.
    pos = line_start - 1
    lookback = 0
    while pos > 0 and lookback < 50:
        prev_end = pos
        pos = source.rfind("\n", 0, prev_end) + 1
        prev_line = source[pos:prev_end]
        s = prev_line.strip()
        if s and not s.startswith(("//", "/*", "*")):
            m2 = re.match(r"^(\s*)", prev_line)
            return m2.group(1) if m2 else "    "
        lookback += 1
    return "    "


def _last_content_line_before(text: str, boundary_pos: int) -> Optional[dict[str, Any]]:
    spans = get_line_spans(text[:boundary_pos])
    for row in reversed(spans):
        s = row["text"].strip()
        if s and not s.startswith(("//", "/*", "*")):
            return row
    return None


def _append_connection_line_before_close(source: str, close_idx: int, new_content: str) -> str:
    """Insert outside comments, locating the last token of this exact list."""
    newline = detect_newline(source)
    indent = _detect_indent_at(source, close_idx)
    openings = []
    for token in _PAREN_PATTERN.finditer(source, 0, close_idx):
        if token.group() == "(":
            openings.append(token.start())
        elif token.group() == ")" and openings:
            openings.pop()
    if not openings:
        raise ValueError("Cannot locate connection list opening parenthesis")
    last = None
    conditional = False
    for token in _LIST_TOKEN_PATTERN.finditer(source, openings[-1] + 1, close_idx):
        if token.group().startswith("`") and len(token.group()) > 1:
            conditional = True
            continue
        if not token.group().startswith(("//", "/*")):
            last = token
    if last is not None and last.group() != ",":
        if conditional:
            # Keep the separator outside every branch and off directive lines.
            new_content = ", " + new_content
        else:
            source = source[:last.end()] + "," + source[last.end():]
            close_idx += 1
    line_start = source.rfind("\n", 0, close_idx) + 1
    if not source[line_start:close_idx].strip():
        return source[:line_start] + indent + new_content + newline + source[line_start:]
    return (source[:close_idx] + newline + indent + new_content + newline
            + source[close_idx:])

def _append_port_line_before_close(source: str, close_idx: int, new_content: str) -> str:
    """Insert module port on its own line before ')'."""
    return _append_connection_line_before_close(source, close_idx, new_content)


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
        # Validation needs diagnostics, not an exported/rebuilt CST.
        p = parser if parser is not None else _new_parser()
        data = p.parse_string(edited_source, options={"gen_tree": False})
        if data is None:
            raise ValueError("No syntax result")
        if data.errors:
            details = "; ".join(f"{e.line + 1}:{e.column + 1}: {e.message or e.phase}" for e in data.errors)
            raise ValueError(details)
    except Exception as exc:
        # Provide detailed diagnostic information
        error_details = str(exc)
        raise ValueError(
            f"Edit operation '{action}' produced invalid Verilog syntax.\n"
            f"Change was not applied.\n"
            f"Diagnostic: {error_details}"
        )
    return edited_source


def _insert_decl_before_body(source: str, decl_insert_idx: int, decl_text: str) -> str:
    newline = detect_newline(source)
    body_indent = _detect_indent_at(source, decl_insert_idx)
    line_start = source.rfind("\n", 0, decl_insert_idx) + 1
    if source[line_start:decl_insert_idx].strip():
        insert_text = newline + body_indent + decl_text + newline
        return source[:decl_insert_idx] + insert_text + source[decl_insert_idx:]
    insert_text = body_indent + decl_text + newline
    return source[:line_start] + insert_text + source[line_start:]


def _insert_decl_after_index(source: str, after_idx: int, decl_text: str) -> str:
    """Insert declaration on a new line after a known statement end index."""
    newline = detect_newline(source)
    line_start = source.rfind("\n", 0, after_idx) + 1
    indent = _detect_indent_at(source, line_start)
    line_end = source.find("\n", after_idx)
    suffix = source[after_idx + 1:line_end if line_end >= 0 else len(source)].strip()
    if suffix and not suffix.startswith("//"):
        insert_text = newline + indent + decl_text + newline
        return source[:after_idx + 1] + insert_text + source[after_idx + 1:]
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
                if s < INVALID_POSITION:
                    next_non_port_start = _byte_to_char_index(source, s)
                break
            last_port_decl = c

        if last_port_decl is not None:
            s, e = get_node_range(last_port_decl)
            if s < INVALID_POSITION:
                # Search from the END of the declaration, not before its start.
                # Using s-2 could match the ';' on the previous line.
                e = _byte_to_char_index(source, e)
                search_start = e
                if next_non_port_start > search_start:
                    search_end = next_non_port_start
                else:
                    search_end = min(len(source), max(e + 128, search_start + 1))

                semi = source.find(";", search_start, search_end)
                if semi >= 0:
                    return semi

    return _module_offsets(mod_node, source)["decl_insert_idx"]


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
    """Find matching ')' from a known '(' index, skipping string literals and comments."""
    if open_idx < 0 or open_idx >= len(source) or source[open_idx] != "(":
        return -1
    depth = 0
    i = open_idx
    in_string = False
    in_block_comment = False
    while i < len(source):
        ch = source[i]
        if in_block_comment:
            if ch == "*" and i + 1 < len(source) and source[i + 1] == "/":
                in_block_comment = False
                i += 2
                continue
        elif in_string:
            if ch == "\\":
                i += 2  # skip escaped character
                continue
            if ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "/" and i + 1 < len(source):
            if source[i + 1] == "/":
                end = source.find("\n", i)
                i = end + 1 if end >= 0 else len(source)
                continue
            elif source[i + 1] == "*":
                in_block_comment = True
                i += 2
                continue
        if not in_block_comment and not in_string:
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
    *,
    mi: Optional[dict] = None,
    validate: bool = True,
) -> str:
    """Add port to module (idempotent).

    ANSI: insert full declaration before ')'.
    non-ANSI: insert port name before ')' and declaration in module body.
    """
    if mi is None:
        mi = _get_module_info(source, module_name, parser)
    offsets = _module_offsets(mi["cst"], source)
    port_insert_idx = offsets["port_insert_idx"]
    if port_insert_idx < 0:
        raise ValueError(
            f"Cannot locate port list close parenthesis in module '{module_name}'.\n"
            f"Module may not have a port list or may be non-ANSI style."
        )
    port_insert_idx = _align_to_char(source, port_insert_idx, ")")
    if port_insert_idx < 0:
        raise ValueError(
            f"Unable to align module port insertion point for '{module_name}'.\n"
            f"Port list close parenthesis location could not be verified."
        )

    if any(p.get("name") == port_spec.get("name") for p in mi.get("ports", [])):
        return source

    if mi["is_ansi"]:
        edited = _apply_ansi_add_port(source, port_insert_idx, port_spec)
        return _validate_edit_or_raise(source, edited, parser, "add_port(ansi)") if validate else edited

    edited = _apply_nonansi_add_port(source, parser, module_name, port_insert_idx, port_spec)
    return _validate_edit_or_raise(source, edited, parser, "add_port(non-ansi)") if validate else edited


_DECL_TAGS = [
    "kDataDeclaration",
    "kNetDeclaration",
    "kVariableDeclarationAssign",
    "kModulePortDeclaration",
]


def _signal_declared_in_module(mod_node: Any, signal_name: str) -> bool:
    # Only declarator names count, never identifiers in types, widths or RHSs.
    declarators = {"kPortDeclaration", "kPort", "kIdentifierUnpackedDimensions",
                   "kNetVariable", "kNetDeclarationAssignment", "kRegisterVariable",
                   "kVariableDeclarationAssign"}
    for node in _module_nodes(mod_node, declarators,
                              stop_tags={"kGenerateBlock", "kSeqBlock", "kParBlock"}):
        sid = _find_first_identifier_child(node)
        if sid is None:
            for child in getattr(node, "children", ()):
                if getattr(child, "tag", "") == "kUnqualifiedId":
                    sid = _find_first_identifier_in_subtree(child)
                    break
        if sid is not None and _desc_text(sid) == signal_name:
            return True
    return False


def add_wire(
    source: str,
    parser: Optional[VeribleParser],
    module_name: str,
    wire_spec: dict[str, str],
    *,
    mi: Optional[dict] = None,
    validate: bool = True,
) -> str:
    """Add wire/logic declaration before first body item (idempotent)."""
    if mi is None:
        mi = _get_module_info(source, module_name, parser)
    decl_insert_idx = _module_offsets(mi["cst"], source)["decl_insert_idx"]
    if decl_insert_idx < 0:
        raise ValueError("Cannot locate module body")

    name = wire_spec.get("name", "")
    if not name:
        raise ValueError("Wire name is required in wire specification")

    if _signal_declared_in_module(mi["cst"], name):
        return source

    decl = compose_wire_decl(wire_spec) + ";"
    edited = _insert_decl_before_body(source, decl_insert_idx, decl)
    return _validate_edit_or_raise(source, edited, parser, f"add_wire '{name}'") if validate else edited


def _find_gate_instance_node(mod_node: Any, instname: str, offset: Optional[int] = None) -> Optional[Any]:
    candidates: list[Any] = []
    for ginst in _module_nodes(mod_node, {"kGateInstance"}):
        sid = _find_first_identifier_child(ginst)
        if sid is None:
            sid = _find_first_identifier_in_subtree(ginst)
        if sid and _desc_text(sid) == instname:
            candidates.append(ginst)

    if not candidates:
        return None
    if offset is None:
        return candidates[0]

    def _distance(node: Any) -> int:
        s, _ = get_node_range(node)
        return abs(s - offset) if s < INVALID_POSITION else 1_000_000_000

    return min(candidates, key=_distance)


def _existing_named_port_wire(ginst: Any, port_name: str, source: str) -> Optional[str]:
    for ap in ginst.find_all({"tag": "kActualNamedPort"}, iter_=PreOrderTreeIterator):
        # Find port name from direct child identifier
        pname_node = _find_first_identifier_child(ap)
        if pname_node is None:
            pname_node = _find_first_identifier_in_subtree(ap)
        pname = _desc_text(pname_node) if pname_node else ""

        if pname != port_name:
            continue

        expr = ap.find({"tag": "kExpression"}, iter_=PreOrderTreeIterator)
        if not expr:
            # .name is an implicit connection; .name() is explicitly open.
            return "" if ap.find({"tag": "("}) is not None else pname
        es, ee = get_node_range(expr)
        if es < INVALID_POSITION and ee > es:
            return _desc_text(expr)
        return _desc_text(expr)

    return None


def add_inst_port(
    source: str,
    parser: Optional[VeribleParser],
    module_name: str,
    spec: dict[str, str],
    *,
    mi: Optional[dict] = None,
    validate: bool = True,
) -> str:
    """Add .port(wire) to instance connection list (idempotent)."""
    instname = spec["instname"]
    port_name = spec["port"]
    wire = spec.get("wire", "")

    if mi is None:
        mi = _get_module_info(source, module_name, parser)
    inst = next((i for i in mi.get("instances", []) if i.get("name") == instname), None)
    if inst is None:
        raise ValueError(f"Instance '{instname}' not found in module '{module_name}'")

    close_idx = _byte_to_char_index(source, int(inst.get("port_close_idx", -1)))
    if close_idx < 0:
        raise ValueError(f"Cannot locate closing ')' for instance '{instname}'")
    close_idx = _align_to_char(source, close_idx, ")")
    if close_idx < 0:
        raise ValueError(f"Unable to align closing ')' for instance '{instname}'")

    # Derive the instance-local paren range from its own offset.
    inst_offset = int(inst.get("offset", -1))
    local_open_idx = -1
    if inst_offset >= 0:
        local_open_idx = source.find("(", _byte_to_char_index(source, inst_offset), close_idx + 1)
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
    return _validate_edit_or_raise(source, edited, parser, "add_inst_port") if validate else edited


# ---------------------------------------------------------------------------
# Instantiation template
# ---------------------------------------------------------------------------


def gen_sig_decl(module_info: dict) -> str:
    """Generate signal declarations for all ports of a module.

    inout  → wire
    others → logic
    One declaration per line; columns aligned; direction comment at end.
    """
    ports = module_info.get("ports", [])
    valid_ports = [p for p in ports if p.get("name", "").strip()]
    if not valid_ports:
        return ""

    rows = []
    for p in valid_ports:
        direction = p.get("direction", "").strip().lower()
        sig_type  = "wire" if direction == "inout" else "logic"
        width     = p.get("width", "").strip()
        name      = p.get("name", "").strip()
        dimension = p.get("dimension", "").strip()
        # unpacked dimension attaches after name: name [dim]
        name_part = f"{name} {dimension}" if dimension else name
        rows.append((sig_type, width, name_part, direction))

    max_type  = max(len(r[0]) for r in rows)
    max_width = max(len(r[1]) for r in rows)
    max_name  = max(len(r[2]) for r in rows)

    lines = []
    for sig_type, width, name_part, direction in rows:
        decl = f"{name_part};"
        comment = f"// {direction}" if direction else ""
        if max_width:
            line = f"{sig_type:<{max_type}} {width:<{max_width}} {decl:<{max_name + 1}} {comment}"
        else:
            line = f"{sig_type:<{max_type}} {decl:<{max_name + 1}} {comment}"
        lines.append(line.rstrip())

    return "\n".join(lines)


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
        self._parser: VeribleParser = _new_parser()   # shared, stateless — reuse across calls
        self._mi_cache: Optional[dict] = None         # invalidated whenever _source changes
        self._instances_cached = False

    # ── Cache management ────────────────────────────────────────────────────

    def _invalidate(self) -> None:
        """Drop cached module info after source has been mutated."""
        self._mi_cache = None
        self._instances_cached = False

    def _mi(self, include_instances: bool = False) -> dict:
        """Return module info, parsing only when the cache is cold."""
        if self._mi_cache is None:
            self._mi_cache = _get_module_info(
                self._source, self._module_name, self._parser, include_instances=False
            )
            self._instances_cached = False
        if include_instances and not self._instances_cached:
            self._mi_cache["instances"] = _extract_instances(self._mi_cache["cst"])
            self._instances_cached = True
        return self._mi_cache

    # ── Class methods ───────────────────────────────────────────────────────

    @classmethod
    def from_file(
        cls,
        path: Union[Path, str],
        module_name: Optional[str] = None,
        *,
        auto_select: bool = False,
        inst_name: Optional[str] = None,
    ) -> "vedit":
        fpath = Path(path)
        if not fpath.exists():
            raise FileNotFoundError(f"file not found: {fpath}")
        source = fpath.read_text(encoding="utf-8")
        if module_name is None:
            mods = _analyze_modules(source, _new_parser(), include_instances=False)
            if not mods:
                raise ValueError("No module found in source")
            if len(mods) > 1 and not auto_select:
                names = "\n  - ".join(m["name"] for m in mods)
                raise ValueError(f"multiple modules:\n  - {names}\nSpecify --module")
            selected = next((m for m in mods if m["name"] == fpath.stem), mods[0]) if auto_select else mods[0]
            instances_cached = False
            if auto_select and inst_name and len(mods) > 1:
                for mod in mods:
                    mod["instances"] = _extract_instances(mod["cst"])
                    if any(i["name"] == inst_name for i in mod["instances"]):
                        selected = mod
                        instances_cached = True
                        break
            # Pre-populate MI cache so first _mi() call costs nothing.
            inst = cls(source, selected["name"])
            inst._mi_cache = selected
            inst._instances_cached = instances_cached
            return inst
        return cls(source, module_name)

    @classmethod
    def list_modules_in_file(cls, path: Union[Path, str]) -> list[str]:
        source_text = Path(path).read_text(encoding="utf-8")
        return [m["name"] for m in _analyze_modules(source_text, _new_parser(), include_instances=False)]

    @classmethod
    def list_modules(cls, source_text: str) -> list[str]:
        """List module names from already-loaded source text (no file I/O)."""
        return [m["name"] for m in _analyze_modules(source_text, _new_parser(), include_instances=False)]

    # ── Properties (all share the cached parse) ─────────────────────────────

    module_text  = property(lambda self: self._source)
    module_name  = property(lambda self: self._module_name)
    ports        = property(lambda self: self._mi().get("ports", []))
    instances    = property(lambda self: self._mi(True).get("instances", []))
    param_names  = property(lambda self: self._mi().get("param_names", []))
    is_ansi      = property(lambda self: self._mi().get("is_ansi", True))

    @property
    def parameter_declaration(self) -> str:
        """Complete header #(parameters), preserving defaults and types."""
        header = self._mi()["cst"].find({"tag": "kModuleHeader"}, iter_=PreOrderTreeIterator)
        params = header.find({"tag": "kFormalParameterListDeclaration"}, iter_=PreOrderTreeIterator) if header else None
        return _desc_text(params) if params else ""

    # ── Edit methods ────────────────────────────────────────────────────────

    def add_port(self, port_dict: dict[str, dict[str, str]], *, _validate: bool = True) -> "vedit":
        """Add one or more ports (idempotent).

        ANSI: all ports are batched into a single string operation using drift
        tracking — only one syntax check for final validation. non-ANSI ports
        and body declarations are also batched using the original CST offsets.
        Rolls back ``self._source`` on validation failure.
        """
        if not port_dict:
            return self

        original_source = self._source
        try:
            mi = self._mi()
            is_ansi = mi.get("is_ansi", True)
            changed = False

            if is_ansi:
                offsets = _module_offsets(mi["cst"], self._source)
                close_idx = offsets["port_insert_idx"]
                close_idx = _align_to_char(self._source, close_idx, ")")
                if close_idx < 0:
                    raise ValueError(
                        f"Unable to locate port list close parenthesis in module "
                        f"'{self._module_name}'"
                    )
                # Insert all ports with drift tracking — no re-parse between iterations.
                for name, attrs in port_dict.items():
                    name = name.strip()
                    direction = attrs.get("direction", "").strip().lower()
                    if not name:
                        raise ValueError("name is required")
                    if direction and direction not in {"input", "output", "inout", "ref"}:
                        raise ValueError("direction must be input/output/inout/ref")
                    info = {
                        "direction": direction,
                        "type":      attrs.get("type", "").strip(),
                        "width":     normalize_width(attrs.get("width", "")),
                        "name":      name,
                        "dimension": normalize_dimension(attrs.get("dimension", "")),
                    }
                    if any(p.get("name") == name for p in mi.get("ports", [])):
                        continue
                    before_len = len(self._source)
                    self._source = _apply_ansi_add_port(self._source, close_idx, info)
                    close_idx += len(self._source) - before_len
                    changed = True
            else:
                # Track header growth and insert body declarations together.
                close_idx = _module_offsets(mi["cst"], self._source)["port_insert_idx"]
                close_idx = _align_to_char(self._source, close_idx, ")")
                after_idx = _nonansi_decl_insert_idx(self._source, mi["cst"])
                if close_idx < 0 or after_idx < 0:
                    raise ValueError(f"Cannot locate port/declaration insertion point in '{self._module_name}'")
                declarations = []
                for name, attrs in port_dict.items():
                    name = name.strip()
                    direction = attrs.get("direction", "").strip().lower()
                    if not name:
                        raise ValueError("name is required")
                    if direction and direction not in {"input", "output", "inout", "ref"}:
                        raise ValueError("direction must be input/output/inout/ref")
                    info = {
                        "direction": direction,
                        "type":      attrs.get("type", "").strip(),
                        "width":     normalize_width(attrs.get("width", "")),
                        "name":      name,
                        "dimension": normalize_dimension(attrs.get("dimension", "")),
                    }
                    if any(p.get("name") == name for p in mi.get("ports", [])):
                        continue
                    before_len = len(self._source)
                    self._source = _append_port_line_before_close(self._source, close_idx, name)
                    growth = len(self._source) - before_len
                    close_idx += growth
                    after_idx += growth
                    declarations.append(compose_port_decl(info) + ";")
                    changed = True
                if declarations:
                    decl_text = detect_newline(self._source).join(declarations)
                    if after_idx < len(self._source) and self._source[after_idx] == ";":
                        self._source = _insert_decl_after_index(self._source, after_idx, decl_text)
                    else:
                        self._source = _insert_decl_before_body(self._source, after_idx, decl_text)

            if changed:
                self._invalidate()
                if _validate:
                    _validate_edit_or_raise(self._source, self._source, self._parser, "add_port")

        except Exception:
            self._source = original_source
            self._mi_cache = None
            raise

        return self

    def add_wire(self, wire_dict: dict[str, dict[str, str]]) -> "vedit":
        """Add one or more wire/logic declarations (idempotent).

        All declarations are batch-inserted with drift tracking — no re-parse
        between iterations.  Rolls back on validation failure.
        """
        if not wire_dict:
            return self

        original_source = self._source
        try:
            mi = self._mi()
            decl_insert_idx = _module_offsets(mi["cst"], self._source)["decl_insert_idx"]
            if decl_insert_idx < 0:
                raise ValueError("Cannot locate module body")

            changed = False
            for name, attrs in wire_dict.items():
                name = name.strip()
                if not name:
                    raise ValueError("name is required")
                info = {
                    "type":      attrs.get("type", "wire").strip() or "wire",
                    "width":     normalize_width(attrs.get("width", "")),
                    "name":      name,
                    "dimension": normalize_dimension(attrs.get("dimension", "")),
                }
                if _signal_declared_in_module(mi["cst"], name):
                    continue
                decl = compose_wire_decl(info) + ";"
                before_len = len(self._source)
                self._source = _insert_decl_before_body(self._source, decl_insert_idx, decl)
                decl_insert_idx += len(self._source) - before_len
                changed = True

            if changed:
                self._invalidate()
                _validate_edit_or_raise(self._source, self._source, self._parser, "add_wire")

        except Exception:
            self._source = original_source
            self._mi_cache = None
            raise

        return self

    def add_inst_port(self, inst_port_dict: dict[str, dict[str, dict[str, str]]], *, _validate: bool = True) -> "vedit":
        """Add .port(wire) connections to instances.

        Rolls back ``self._source`` on validation failure.
        """
        if not inst_port_dict:
            return self

        original_source = self._source
        try:
            mi = self._mi(True)
            edits = []
            instances_by_name = {i["name"]: i for i in reversed(mi.get("instances", []))}
            gates_by_name = {}
            for gate in _module_nodes(mi["cst"], {"kGateInstance"}):
                sid = _find_first_identifier_child(gate) or _find_first_identifier_in_subtree(gate)
                if sid:
                    gates_by_name.setdefault(_desc_text(sid), []).append(gate)
            for instname, ports in inst_port_dict.items():
                inst = instances_by_name.get(instname)
                if inst is None:
                    raise ValueError(f"Instance '{instname}' not found in module '{self._module_name}'")
                candidates = gates_by_name.get(instname, [])
                ginst = min(candidates, key=lambda g: abs(get_node_range(g)[0] - inst["offset"])) if candidates else None
                close_idx = _align_to_char(original_source, _byte_to_char_index(original_source, inst["port_close_idx"]), ")")
                if close_idx < 0:
                    raise ValueError(f"Cannot locate closing ')' for instance '{instname}'")
                local_source = original_source
                for port_name, wire_info in ports.items():
                    wire = wire_info.get("wire", "").strip()
                    old_wire = _existing_named_port_wire(ginst, port_name, original_source) if ginst else None
                    if old_wire is not None:
                        if old_wire.strip() == wire:
                            continue
                        raise ValueError(f"Instance '{instname}' port '{port_name}' already connected to '{old_wire}', requested '{wire}'")
                    before_len = len(local_source)
                    local_source = _append_connection_line_before_close(local_source, close_idx, f".{port_name}({wire})")
                    close_idx += len(local_source) - before_len
                if local_source != original_source:
                    start, end = (_byte_to_char_index(original_source, inst[key])
                                  for key in ("offset", "port_close_idx"))
                    growth = len(local_source) - len(original_source)
                    edits.append((start, end, local_source[start:end + growth]))

            if edits:
                self._source = apply_text_edits(original_source, edits)
                self._invalidate()
                if _validate:
                    _validate_edit_or_raise(self._source, self._source, self._parser, "add_inst_port")

        except Exception:
            self._source = original_source
            self._mi_cache = None
            raise

        return self

    def add_ports_and_inst_ports(self, port_dict, inst_port_dict) -> "vedit":
        """Atomically edit module ports and instance pins with one syntax check.

        Instance connections follow the header and leading port declarations,
        so inserting them first preserves the original port insertion offsets.
        Both edits can therefore reuse the same immutable CST.
        """
        if not port_dict and not inst_port_dict:
            return self
        original = self._source
        mi = self._mi(bool(inst_port_dict))
        instances_cached = self._instances_cached
        try:
            self.add_inst_port(inst_port_dict, _validate=False)
            self._mi_cache = mi
            self._instances_cached = instances_cached
            self.add_port(port_dict, _validate=False)
            if self._source != original:
                _validate_edit_or_raise(original, self._source, self._parser, "add_ports_and_inst_ports")
                self._invalidate()
        except Exception:
            self._source = original
            self._mi_cache = mi
            self._instances_cached = instances_cached
            raise
        return self

    def add_inst_ports(self, instname: str, port_wires: dict[str, str]) -> "vedit":
        nested = {instname: {p: {"wire": w} for p, w in port_wires.items()}}
        return self.add_inst_port(nested)

    # ── Analysis ────────────────────────────────────────────────────────────

    def gen_inst(
        self,
        include_params: bool = True,
        instance_name: Optional[str] = None,
        port_map: Optional[dict[str, str]] = None,
    ) -> str:
        # Keep the lightweight result for later reads; instances remain lazy.
        return gen_inst(
            self._mi(),
            include_params=include_params,
            instance_name=instance_name,
            port_map=port_map,
        )

    def analyze(self, include_instances: bool = True) -> dict[str, Any]:
        mi = self._mi(include_instances)
        result = dict(mi)
        if not include_instances:
            result["instances"] = []
        result.setdefault("module_name", result.get("name", ""))
        result["module_text"] = self._source
        return result

    def get_hier(self) -> dict[str, Any]:
        mi = self._mi(True)
        return {"module": mi.get("name", ""), "instances": mi.get("instances", [])}
