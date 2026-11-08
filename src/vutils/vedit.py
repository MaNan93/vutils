"""Verilog/SystemVerilog module editing utilities built on vparser CST."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional, TypedDict, Union

from .vparser import (
    COMMON_TYPES,
    PreOrderTreeIterator,
    TreeSitterParser,
    module_nodes,
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


_IDENTIFIERS = {"simple_identifier", "escaped_identifier"}
_HEADER_TAGS = {"module_ansi_header", "module_nonansi_header"}
_PORT_LIST_TAGS = {"list_of_port_declarations", "list_of_ports"}


def _direct(node, tags):
    return next((child for child in node.children if child.tag in tags), None) if node else None


def _field(node, name):
    return node.field(name) if hasattr(node, "field") else None


def _find_first_identifier_child(node):
    return _direct(node, _IDENTIFIERS)


def _new_parser():
    return TreeSitterParser()


def _parse_tree(source, parser=None):
    data = (parser or _new_parser()).parse_string(source)
    if data.errors:
        details = "; ".join(f"{e.line + 1}:{e.column + 1}: {e.message or e.phase}" for e in data.errors)
        raise ValueError(f"Invalid Verilog syntax: {details}")
    return data.tree


def _desc_text(node):
    return node.text.strip() if node is not None else ""


def _find_module_nodes(tree):
    return module_nodes(tree)


def _module_nodes(mod, tags, *, stop_tags=()):
    """Traverse a module's lexical scope, never nested definitions."""
    stopped = {"module_declaration", "function_declaration", "task_declaration",
               "class_declaration", *stop_tags}
    stack = [mod]
    while stack:
        node = stack.pop()
        if node is not mod and node.tag in stopped:
            continue
        if node.tag in tags:
            yield node
        if hasattr(node, "children") and node.tag not in tags:
            stack.extend(reversed(node.children))


def _byte_to_char_index(source, byte_index):
    if byte_index < 0 or source.isascii():
        return byte_index
    return len(source.encode("utf-8")[:byte_index].decode("utf-8"))


def _header(mod):
    return _direct(mod, _HEADER_TAGS)


def _header_port_decl_list(header):
    return _direct(header, _PORT_LIST_TAGS)


def _module_name(mod, source):
    return _desc_text(_field(_header(mod), "name"))


def _is_ansi_module(mod):
    plist = _header_port_decl_list(_header(mod))
    return plist is not None and plist.tag == "list_of_port_declarations"


def _declaration_type(decl, name):
    """Separate the declaration prefix and only its outer packed dimensions."""
    raw = decl.syntax_data.source_code
    prefix = raw[decl.start:name.start]
    dt = decl.find({"tag": ["data_type", "implicit_data_type"]}, iter_=PreOrderTreeIterator)
    dims = [c for c in dt.children if c.tag == "packed_dimension"] if dt else []
    dims = [c for c in dims if c.end <= name.start]
    width = "".join(c.text for c in dims)
    for dim in reversed(dims):
        prefix = prefix[:dim.start-decl.start] + prefix[dim.end-decl.start:]
    typ = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", " ", prefix.decode("utf-8"))
    typ = re.sub(r"^\s*(?:input|output|inout|ref)\b\s*", "", typ).strip()
    return typ, width


def _extract_ports(mod, source, is_ansi):
    plist = _header_port_decl_list(_header(mod))
    if plist is None:
        return []
    ports = []
    if is_ansi:
        previous = {"direction": "inout", "type": "", "width": ""}
        for pd in plist.children:
            if pd.tag != "ansi_port_declaration":
                continue
            name = _field(pd, "port_name")
            if name is None:
                raise ValueError("Unsupported ANSI port declaration: " + pd.text)
            header = _direct(pd, {"net_port_header", "variable_port_header", "interface_port_header"})
            direction = header.find({"tag": "port_direction"}) if header else None
            if header:
                typ, width = _declaration_type(pd, name)
                info = {"direction": _desc_text(direction) or previous["direction"], "type": typ, "width": width}
                if header.tag == "interface_port_header":
                    info.update(direction="", interface=True,
                                interface_type=_desc_text(_field(header, "interface_name")),
                                modport=_desc_text(_field(header, "modport_name")))
            else:
                info = {key: value for key, value in previous.items() if key not in {"name", "dimension"}}
            info.update(name=_desc_text(name), dimension="".join(c.text for c in pd.children
                                                                if c.tag == "unpacked_dimension"))
            ports.append(info)
            previous = info
        return ports

    for pd in plist.children:
        if pd.tag != "port":
            continue
        name = _find_first_identifier_child(pd)
        if name is None:
            raise ValueError("Unsupported non-ANSI port expression: " + pd.text)
        ports.append({"name": _desc_text(name), "direction": "", "type": "", "width": "", "dimension": ""})
    by_name = {p["name"]: p for p in ports}
    for pd in _module_nodes(mod, {"port_declaration"}):
        lists = pd.find_all({"tag": ["list_of_port_identifiers", "list_of_variable_identifiers",
                                    "list_of_variable_port_identifiers"]}, iter_=PreOrderTreeIterator)
        for declarations in lists:
            names = [c for c in declarations.children if c.tag in _IDENTIFIERS]
            if not names:
                continue
            typ, width = _declaration_type(pd, names[0])
            direction = pd.find({"tag": ["input", "output", "inout", "ref"]})
            current = None
            for child in declarations.children:
                if child.tag in _IDENTIFIERS:
                    current = by_name.get(_desc_text(child))
                    if current is not None:
                        current.update(direction=_desc_text(direction), type=typ, width=width, dimension="")
                elif child.tag == "unpacked_dimension" and current is not None:
                    current["dimension"] += child.text
    return ports


def _extract_param_names(mod):
    params = _direct(_header(mod), {"parameter_port_list"})
    if params is None:
        return []
    names = []
    for node in params.find_all({"tag": ["param_assignment", "type_assignment"]}, iter_=PreOrderTreeIterator):
        ancestor = node.parent
        while ancestor is not params and ancestor is not None and ancestor.tag != "local_parameter_declaration":
            ancestor = ancestor.parent
        if ancestor is not None and ancestor.tag == "local_parameter_declaration":
            continue
        name = _field(node, "name") or _find_first_identifier_child(node)
        if name:
            names.append(_desc_text(name))
    return names


def _instance_name(ginst):
    return _field(_direct(ginst, {"name_of_instance"}), "instance_name")


def _extract_instances(mod):
    instances = []
    for inst in _module_nodes(mod, {"module_instantiation"}):
        typ = _field(inst, "instance_type")
        for gate in inst.children:
            if gate.tag != "hierarchical_instance":
                continue
            name = _instance_name(gate)
            close = next((c for c in reversed(gate.children) if c.tag == ")"), None)
            if name is None or close is None:
                continue
            scopes = []
            parent = gate.parent
            while parent is not None and parent is not mod:
                if parent.tag == "generate_block":
                    label = _field(parent, "name")
                    if label:
                        scopes.append(_desc_text(label))
                parent = parent.parent
            instances.append({"name": _desc_text(name), "type": _desc_text(typ),
                              "path": ".".join([*reversed(scopes), _desc_text(name)]),
                              "offset": name.start, "port_close_idx": close.start})
    return instances


def _select_instance(instances, name, module_name):
    matches = [i for i in instances if i["name"] == name or i.get("path") == name]
    if not matches:
        raise ValueError(f"Instance '{name}' not found in module '{module_name}'")
    if len(matches) > 1:
        paths = ", ".join(i.get("path", i["name"]) for i in matches)
        raise ValueError(f"Ambiguous instance '{name}' in module '{module_name}': {paths}. Specify a scope path.")
    return matches[0]


def _module_offsets(mod, source=None):
    plist = _header_port_decl_list(_header(mod))
    close = next((c for c in reversed(plist.children) if c.tag == ")"), None) if plist else None
    body = next((c for c in mod.children if c.tag not in _HEADER_TAGS and c.tag != "comment"), None)
    offsets = {"port_insert_idx": close.start if close else -1,
               "decl_insert_idx": body.start if body else -1,
               "mod_start": mod.start, "mod_end": mod.end}
    return {key: _byte_to_char_index(source, value) for key, value in offsets.items()} if source is not None else offsets


def _analyze_modules(
    source: str,
    parser: Optional[TreeSitterParser] = None,
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
                "imports": [c.text for c in _header(mod).children if c.tag == "package_import_declaration"],
                "instances": _extract_instances(mod) if include_instances else [],
            }
        )
    return out


def _get_module_info(
    source: str,
    module_name: Optional[str],
    parser: Optional[TreeSitterParser] = None,
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


class _InterfaceParser(TreeSitterParser):
    """Interface reads use lazy native wrappers; body nodes stay unallocated."""


def _interface_result(mi: dict[str, Any]) -> dict[str, Any]:
    params = _direct(_header(mi["cst"]), {"parameter_port_list"})
    return {
        "name": mi["name"],
        "module_name": mi["name"],
        "is_ansi": mi["is_ansi"],
        "ports": mi["ports"],
        "param_names": mi["param_names"],
        "imports": mi["imports"],
        "parameter_declaration": _desc_text(params) if params else "",
    }


def read_interface(source: str, module_name: Optional[str] = None) -> dict[str, Any]:
    """Read ports and header parameters after validating the complete source.

    Wrap only the CST nodes needed by interface extraction. Return plain metadata without a CST or
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
    source = fpath.read_bytes().decode("utf-8")
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
    parser: Optional[TreeSitterParser],
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


def _nonansi_decl_insert_idx(source, mod_node):
    """Insert immediately after the last leading body port declaration."""
    last = None
    for child in mod_node.children:
        if child.tag in _HEADER_TAGS or child.tag == "comment":
            continue
        if child.tag == "module_item" and _direct(child, {"port_declaration"}):
            last = child
        else:
            break
    if last is not None:
        return _byte_to_char_index(source, last.end - 1)
    return _module_offsets(mod_node, source)["decl_insert_idx"]


def _apply_ansi_add_port(source: str, close_idx: int, port_spec: dict[str, str]) -> str:
    return _append_port_line_before_close(source, close_idx, compose_port_decl(port_spec))


def _apply_nonansi_add_port(
    source: str,
    parser: Optional[TreeSitterParser],
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


def add_port(
    source: str,
    parser: Optional[TreeSitterParser],
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


def _signal_declared_in_module(mod_node, signal_name):
    if any(p["name"] == signal_name for p in _extract_ports(mod_node, "", _is_ansi_module(mod_node))):
        return True
    for node in _module_nodes(mod_node, {"net_decl_assignment", "variable_decl_assignment"},
                              stop_tags={"generate_block", "seq_block", "par_block", "sequential_block", "parallel_block"}):
        name = _field(node, "name") or _find_first_identifier_child(node)
        if _desc_text(name) == signal_name:
            return True
    return False


def add_wire(
    source: str,
    parser: Optional[TreeSitterParser],
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


def _find_gate_instance_node(mod_node, instname, offset=None):
    for gate in _module_nodes(mod_node, {"hierarchical_instance"}):
        name = _instance_name(gate)
        if name and (name.start == offset if offset is not None else _desc_text(name) == instname):
            return gate
    return None


def _existing_named_port_wire(ginst, port_name, source):
    connections = _direct(ginst, {"list_of_port_connections"})
    if connections is None:
        return None
    for ap in connections.children:
        if ap.tag != "named_port_connection" or _desc_text(_field(ap, "port_name")) != port_name:
            continue
        expr = _field(ap, "connection")
        if expr is not None:
            return _desc_text(expr)
        return "" if _direct(ap, {"("}) else port_name
    return None


def add_inst_port(
    source: str,
    parser: Optional[TreeSitterParser],
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
    inst = _select_instance(mi.get("instances", []), instname, module_name)

    close_idx = _byte_to_char_index(source, int(inst.get("port_close_idx", -1)))
    if close_idx < 0:
        raise ValueError(f"Cannot locate closing ')' for instance '{instname}'")
    close_idx = _align_to_char(source, close_idx, ")")
    if close_idx < 0:
        raise ValueError(f"Unable to align closing ')' for instance '{instname}'")

    inst_offset = inst["offset"]

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
    """Generate declarations preserving port types, dimensions and imports."""
    ports = module_info.get("ports", [])
    valid_ports = [p for p in ports if p.get("name", "").strip()]
    if not valid_ports:
        return ""

    rows, interface_lines = [], []
    for p in valid_ports:
        if p.get("interface"):
            name, dimension = p["name"].strip(), p.get("dimension", "").strip()
            if p.get("interface_type"):
                name_part = f"{name} {dimension}" if dimension else name
                interface_lines.append(f"{p['interface_type']} {name_part} (); // interface {p.get('modport', '')}".rstrip())
            else:
                interface_lines.append(f"// Connect {name} to an existing interface instance ({p.get('type', 'interface')}).")
            continue
        direction = p.get("direction", "").strip().lower()
        sig_type = p.get("type", "").strip()
        if not sig_type or sig_type in {"signed", "unsigned"}:
            base = "wire" if direction == "inout" else "logic"
            sig_type = " ".join(filter(None, (base, sig_type)))
        width     = p.get("width", "").strip()
        name      = p.get("name", "").strip()
        dimension = p.get("dimension", "").strip()
        # unpacked dimension attaches after name: name [dim]
        name_part = f"{name} {dimension}" if dimension else name
        rows.append((sig_type, width, name_part, direction))

    max_type  = max((len(r[0]) for r in rows), default=0)
    max_width = max((len(r[1]) for r in rows), default=0)
    max_name  = max((len(r[2]) for r in rows), default=0)

    lines = []
    for sig_type, width, name_part, direction in rows:
        decl = f"{name_part};"
        comment = f"// {direction}" if direction else ""
        if max_width:
            line = f"{sig_type:<{max_type}} {width:<{max_width}} {decl:<{max_name + 1}} {comment}"
        else:
            line = f"{sig_type:<{max_type}} {decl:<{max_name + 1}} {comment}"
        lines.append(line.rstrip())

    lines.extend(interface_lines)
    imports = module_info.get("imports", [])
    return "\n".join([*imports, "", *lines] if imports else lines)


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
    """Module editor using the Tree-sitter SystemVerilog grammar."""

    def __init__(self, source: str, module_name: str) -> None:
        self._source = source
        self._module_name = module_name
        self._parser: TreeSitterParser = _new_parser()   # shared, stateless — reuse across calls
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
        source = fpath.read_bytes().decode("utf-8")
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
                    if any(i["name"] == inst_name or i.get("path") == inst_name for i in mod["instances"]):
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
        source_text = Path(path).read_bytes().decode("utf-8")
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
        params = _direct(_header(self._mi()["cst"]), {"parameter_port_list"})
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
            for instname, ports in inst_port_dict.items():
                inst = _select_instance(mi.get("instances", []), instname, self._module_name)
                ginst = _find_gate_instance_node(mi["cst"], inst["name"], inst["offset"])
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
