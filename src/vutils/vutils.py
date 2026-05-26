"""Verilog/SystemVerilog module editing utilities.

All edit operations rely on verible_parser's insertion-point offsets:
  - port_insert_idx:  ')' byte offset in port list
  - decl_insert_idx:  first body item byte offset in kModuleItemList
  - port_close_idx:   last ')' byte offset in instance port connection list
"""

import re
from pathlib import Path
from typing import Any, Optional, Union

from .vparser import COMMON_TYPES, VeribleParser, find_all, get_node_range

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Re-export COMMON_TYPES for cli.py
# (defined in verible_parser.py)

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
            token = value[prev:m.start()].strip()
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
    core = line[:len(line) - len(nl)]
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
    return " ".join(p for p in [info["direction"], info.get("type", ""),
                                 info.get("width", ""), info["name"],
                                 info.get("dimension", "")] if p)


def compose_wire_decl(info: dict[str, str]) -> str:
    return " ".join(p for p in [info["type"], info.get("width", ""),
                                 info["name"], info.get("dimension", "")] if p)


def get_line_spans(block_text: str) -> list[dict[str, Any]]:
    """Split text into lines with byte-offset metadata."""
    spans, offset = [], 0
    for line in block_text.splitlines(keepends=True):
        no_eol = line.rstrip("\r\n")
        spans.append({"start": offset, "end_no_eol": offset + len(no_eol),
                       "end_full": offset + len(line), "text": no_eol})
        offset += len(line)
    return spans


def apply_text_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    """Apply (start, end, replacement) edits back-to-front."""
    for s, e, r in sorted(edits, reverse=True):
        text = text[:s] + r + text[e:]
    return text


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_indent_at(source: str, byte_pos: int) -> str:
    """Detect indentation at byte_pos: use that line's indent if it has
    content, otherwise walk backwards to the previous non-empty line."""
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
    """Find the last non-comment, non-empty line before boundary_pos."""
    spans = get_line_spans(text[:boundary_pos])
    for row in reversed(spans):
        s = row["text"].strip()
        if s and not s.startswith(("//", "/*", "*")):
            return row
    return None


def _append_line_before_close(source: str, close_idx: int, new_content: str) -> str:
    """Insert new_content as a new line before the ')' at close_idx.

    Handles: append comma to last content line, then add new line with
    correct indentation.  Returns modified source.
    """
    newline = detect_newline(source)
    indent = _detect_indent_at(source, close_idx)
    last = _last_content_line_before(source, close_idx)
    if last:
        abs_start, abs_end = last["start"], last["end_full"]
        updated = append_comma_to_port_line(source[abs_start:abs_end])
        updated += indent + new_content
        return source[:abs_start] + updated + source[abs_end:]
    # No existing content — insert directly before ')'
    return source[:close_idx] + newline + indent + new_content + source[close_idx:]


def _insert_decl_before_body(source: str, decl_insert_idx: int, decl_text: str) -> str:
    """Insert declaration at line_start of the line containing decl_insert_idx."""
    newline = detect_newline(source)
    body_indent = _detect_indent_at(source, decl_insert_idx)
    line_start = source.rfind("\n", 0, decl_insert_idx) + 1
    insert_text = body_indent + decl_text + newline
    return source[:line_start] + insert_text + source[line_start:]


def _reparse_decl_insert(source: str, module_name: str) -> int:
    """Re-parse modified source and return fresh decl_insert_idx."""
    p2 = VeribleParser(source=source)
    mi2 = p2.get_module(module_name)
    return p2.get_module_offsets(mi2["cst"])["decl_insert_idx"]


# ---------------------------------------------------------------------------
# Edit operations
# ---------------------------------------------------------------------------

def add_port(source: str, parser: "VeribleParser", module_name: str,
                 port_spec: dict[str, str]) -> str:
    """Add port to module (idempotent).

    ANSI: insert full declaration before ')'.
    Non-ANSI: insert port name before ')', then declaration before first body item.
    """
    mi = parser.get_module(module_name)
    offsets = parser.get_module_offsets(mi["cst"])
    port_insert_idx = offsets["port_insert_idx"]
    if port_insert_idx < 0:
        raise ValueError("Cannot locate port list close parenthesis")

    # Idempotency
    if any(p["name"] == port_spec["name"] for p in mi.get("ports", [])):
        return source

    if mi["is_ansi"]:
        source = _append_line_before_close(source, port_insert_idx,
                                            compose_port_decl(port_spec))
    else:
        # 1) Port name in port list
        source = _append_line_before_close(source, port_insert_idx, port_spec["name"])

        # 2) Declaration in body (re-parse for fresh offset)
        if offsets["decl_insert_idx"] < 0:
            return source
        new_decl_insert = _reparse_decl_insert(source, module_name)
        if new_decl_insert < 0:
            return source
        decl = compose_port_decl(port_spec) + ";"
        source = _insert_decl_before_body(source, new_decl_insert, decl)

    return source


def add_wire(source: str, parser: "VeribleParser", module_name: str,
                 wire_spec: dict[str, str]) -> str:
    """Add wire/logic declaration before first body item (idempotent)."""
    mi = parser.get_module(module_name)
    offsets = parser.get_module_offsets(mi["cst"])
    decl_insert_idx = offsets["decl_insert_idx"]
    if decl_insert_idx < 0:
        raise ValueError("Cannot locate module body")

    # Idempotency via CST
    cst = mi.get("cst")
    mod_start, mod_end = offsets.get("mod_start", 0), offsets.get("mod_end", len(source))
    if cst is not None:
        for tag in ("kDataDeclaration", "kNetDeclaration", "kVariableDeclarationAssign"):
            for node in find_all(cst, tag):
                s, e = get_node_range(node)
                if s < mod_start or e > mod_end:
                    continue
                for sid in find_all(node, "SymbolIdentifier"):
                    if (sid.get("text") or "").strip() == wire_spec["name"]:
                        return source

    decl = compose_wire_decl(wire_spec) + ";"
    return _insert_decl_before_body(source, decl_insert_idx, decl)


def add_inst_port(source: str, parser: "VeribleParser", module_name: str,
                      spec: dict[str, str]) -> str:
    """Add .port(wire) to instance connection list (idempotent)."""
    instname, port_name, wire = spec["instname"], spec["port"], spec["wire"]
    newline = detect_newline(source)

    # Find instance → get port_close_idx
    mi = parser.get_module(module_name)
    inst = next((i for i in mi.get("instances", []) if i["name"] == instname), None)
    if inst is None:
        raise ValueError(f"Instance '{instname}' not found in module '{module_name}'")
    port_close_idx = inst["port_close_idx"]
    if port_close_idx < 0:
        raise ValueError(f"Cannot locate port list for instance '{instname}'")

    # Idempotency
    conn_block = source[source.rfind("\n", 0, port_close_idx) + 1:port_close_idx]
    if re.search(rf"\.\s*{re.escape(port_name)}\s*\(", conn_block):
        return source

    new_conn = f".{port_name}({wire})" if wire else f".{port_name}()"
    indent = _detect_indent_at(source, port_close_idx)

    # Replace from last content line through ');' line
    last = _last_content_line_before(source, port_close_idx)
    close_semi_end = source.find(";", port_close_idx)
    if close_semi_end < 0:
        close_semi_end = port_close_idx + 1
    else:
        close_semi_end += 1
    # Include newline after ');'
    if close_semi_end < len(source) and source[close_semi_end] in "\r\n":
        if source[close_semi_end] == "\r" and close_semi_end + 1 < len(source) and source[close_semi_end + 1] == "\n":
            close_semi_end += 2
        else:
            close_semi_end += 1

    # Detect indent of ');' line
    close_line_start = source.rfind("\n", 0, port_close_idx) + 1
    m = re.match(r"^(\s*)", source[close_line_start:port_close_idx])
    close_indent = m.group(1) if m else ""

    if last:
        abs_start, abs_end = last["start"], close_semi_end
        core_line = append_comma_to_port_line(
            source[last["start"]:last["end_full"]].rstrip("\r\n"))
        replacement = core_line + newline + indent + new_conn + newline + close_indent + ");" + newline
        source = source[:abs_start] + replacement + source[abs_end:]
    else:
        source = source[:port_close_idx] + newline + indent + new_conn + source[port_close_idx:]

    return source


# ---------------------------------------------------------------------------
# Instantiation template
# ---------------------------------------------------------------------------

def gen_inst(module_info: dict, include_params: bool = True,
                 instance_name: Optional[str] = None,
                 port_map: Optional[dict[str, str]] = None) -> str:
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
# ModuleEditor – OOP API
# ---------------------------------------------------------------------------

class ModuleEditor:
    """Module editor using verible-verilog-syntax as parsing backend.

    Usage::

        editor = ModuleEditor.from_file("file.sv", module_name="foo")
        editor.add_port({"clk": {"direction": "input", "type": "wire"}})
        print(editor.module_text)
    """

    def __init__(self, source: str, module_name: str) -> None:
        self._source = source
        self._module_name = module_name

    def _get_parser(self) -> VeribleParser:
        return VeribleParser(source=self._source)

    def _get_module_info(self) -> dict[str, Any]:
        return self._get_parser().get_module(self._module_name)

    # -- Class methods --

    @classmethod
    def from_file(cls, path: "Union[Path, str]",
                  module_name: Optional[str] = None) -> "ModuleEditor":
        fpath = Path(path)
        if not fpath.exists():
            raise FileNotFoundError(f"file not found: {fpath}")
        source = fpath.read_text(encoding="utf-8")
        if module_name is None:
            mods = VeribleParser(source=source).get_modules()
            if len(mods) > 1:
                names = "\n  - ".join(m["name"] for m in mods)
                raise ValueError(f"multiple modules:\n  - {names}\nSpecify --module")
            module_name = mods[0]["name"]
        return cls(source, module_name)

    @classmethod
    def list_modules_in_file(cls, path: "Union[Path, str]") -> list[str]:
        source_text = Path(path).read_text(encoding="utf-8")
        return [m["name"] for m in VeribleParser(source=source_text).get_modules()]

    # -- Properties --

    module_text = property(lambda self: self._source)
    module_name = property(lambda self: self._module_name)
    ports = property(lambda self: self._get_module_info().get("ports", []))
    instances = property(lambda self: self._get_module_info().get("instances", []))
    param_names = property(lambda self: self._get_module_info().get("param_names", []))
    is_ansi = property(lambda self: self._get_module_info().get("is_ansi", True))

    # -- Edit methods --

    def add_port(self, port_dict: dict[str, dict[str, str]]) -> "ModuleEditor":
        """Add ports: {port_name: {direction, type, width, dimension}}."""
        for name, attrs in port_dict.items():
            name = name.strip()
            direction = attrs.get("direction", "").strip().lower()
            if not name:
                raise ValueError("name is required")
            if direction and direction not in {"input", "output", "inout", "ref"}:
                raise ValueError("direction must be input/output/inout/ref")
            info = {"direction": direction,
                     "type": attrs.get("type", "").strip(),
                     "width": normalize_width(attrs.get("width", "")),
                     "name": name,
                     "dimension": normalize_dimension(attrs.get("dimension", ""))}
            self._source = add_port(self._source, self._get_parser(),
                                         self._module_name, info)
        return self

    def add_wire(self, wire_dict: dict[str, dict[str, str]]) -> "ModuleEditor":
        """Add wire/logic: {signal_name: {type, width, dimension}}."""
        for name, attrs in wire_dict.items():
            name = name.strip()
            if not name:
                raise ValueError("name is required")
            info = {"type": attrs.get("type", "wire").strip() or "wire",
                     "width": normalize_width(attrs.get("width", "")),
                     "name": name,
                     "dimension": normalize_dimension(attrs.get("dimension", ""))}
            self._source = add_wire(self._source, self._get_parser(),
                                         self._module_name, info)
        return self

    def add_inst_port(self, inst_port_dict: dict[str, dict[str, dict[str, str]]]) -> "ModuleEditor":
        """Add .port(wire) to instances: {inst: {port: {"wire": sig}}}."""
        for instname, ports in inst_port_dict.items():
            for port_name, wire_info in ports.items():
                self._source = add_inst_port(
                    self._source, self._get_parser(), self._module_name,
                    {"instname": instname, "port": port_name,
                     "wire": wire_info.get("wire", "")})
        return self

    def add_inst_ports(self, instname: str, port_wires: dict[str, str]) -> "ModuleEditor":
        """Add multiple .port(wire) to one instance: {port: wire}."""
        nested = {instname: {p: {"wire": w} for p, w in port_wires.items()}}
        return self.add_inst_port(nested)

    # -- Analysis --

    def gen_inst(self, include_params: bool = True,
                 instance_name: Optional[str] = None,
                 port_map: Optional[dict[str, str]] = None) -> str:
        return gen_inst(self._get_module_info(), include_params=include_params,
                            instance_name=instance_name, port_map=port_map)

    def analyze(self) -> dict[str, Any]:
        mi = self._get_module_info()
        result = dict(mi)
        result.setdefault("module_name", result.get("name", ""))
        result["module_text"] = self._source
        return result

    def get_hier(self) -> dict[str, Any]:
        mi = self._get_module_info()
        return {"module": mi.get("name", ""), "instances": mi.get("instances", [])}
