"""VeribleParser — parse SystemVerilog files via verible-verilog-syntax CST."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COMMON_TYPES = frozenset({
    "wire", "reg", "logic", "bit", "tri", "supply0", "supply1",
    "triand", "trior", "trireg", "uwire", "real", "realtime",
    "integer", "time", "signed", "unsigned",
})

_KEYWORD_SET = frozenset({
    "module", "endmodule", "function", "endfunction",
    "task", "endtask", "always", "assign", "generate",
    "if", "else", "for", "case", "endcase", "begin",
    "end", "initial", "final", "class", "endclass",
    "input", "output", "inout", "wire", "reg", "logic",
    "parameter", "localparam", "integer", "bit", "tri",
    "always_ff", "always_comb", "always_latch", "string",
    "virtual", "interface", "endinterface", "program", "endprogram",
    "package", "endpackage", "config", "endconfig",
    "primitive", "endprimitive", "table", "endtable",
    "specify", "endspecify", "generate", "endgenerate",
})


# ---------------------------------------------------------------------------
# Verible executable
# ---------------------------------------------------------------------------

def _find_verible_binary() -> str:
    """Locate verible-verilog-syntax binary.

    Search order:
    1. VERIBLE_BIN environment variable (path to file or containing directory)
    2. System PATH via shutil.which
    3. A 'verible' directory found by walking up from this file (up to 6 levels)
    """
    name = "verible-verilog-syntax"
    # 1. Environment variable override
    if env := os.environ.get("VERIBLE_BIN", ""):
        p = Path(env)
        if p.is_file():
            return str(p)
        for ext in ("", ".exe"):
            if (c := p / (name + ext)).is_file():
                return str(c)
    # 2. System PATH
    if found := shutil.which(name):
        return found
    # 3. Walk up directory tree looking for a sibling 'verible' directory
    here = Path(__file__).resolve().parent
    for _ in range(6):
        for ext in ("", ".exe"):
            if (c := here / "verible" / (name + ext)).is_file():
                return str(c)
        here = here.parent
    # Fallback: rely on PATH at call time
    return name


VERIBLE = _find_verible_binary()


# ---------------------------------------------------------------------------
# CST Traversal Utilities
# ---------------------------------------------------------------------------

def find_all(node: Any, tag: str) -> list[dict]:
    """Find all descendant nodes with given tag (iterative DFS, left-to-right)."""
    results: list[dict] = []
    stack = [node]
    while stack:
        n = stack.pop()
        if not isinstance(n, dict):
            continue
        if n.get("tag") == tag:
            results.append(n)
        # push children in reverse so the leftmost child is processed first
        stack.extend(
            c for c in reversed(n.get("children", [])) if isinstance(c, dict)
        )
    return results


def find_child(node: Any, tag: str) -> Optional[dict]:
    """Find first direct child with given tag."""
    if not isinstance(node, dict):
        return None
    for c in node.get("children", []):
        if isinstance(c, dict) and c.get("tag") == tag:
            return c
    return None


def find_children(node: Any, tag: str) -> list[dict]:
    """Find all direct children with given tag."""
    if not isinstance(node, dict):
        return []
    return [c for c in node.get("children", []) if isinstance(c, dict) and c.get("tag") == tag]


def find_descendant(node: Any, tag: str) -> Optional[dict]:
    """Find first descendant with given tag (iterative DFS, left-to-right)."""
    stack = [node]
    while stack:
        n = stack.pop()
        if not isinstance(n, dict):
            continue
        if n.get("tag") == tag:
            return n
        # push children in reverse so the leftmost child is processed first
        stack.extend(
            c for c in reversed(n.get("children", [])) if isinstance(c, dict)
        )
    return None


def get_node_range(node: dict) -> tuple[int, int]:
    """Get (min_start, max_end) from all leaf nodes in subtree (iterative)."""
    if not isinstance(node, dict):
        return (999999, -1)
    if "start" in node and "end" in node:
        return (node["start"], node["end"])
    starts: list[int] = []
    ends: list[int] = []
    stack = [node]
    while stack:
        n = stack.pop()
        if not isinstance(n, dict):
            continue
        if "start" in n and "end" in n:
            starts.append(n["start"])
            ends.append(n["end"])
        else:
            stack.extend(
                c for c in reversed(n.get("children", [])) if isinstance(c, dict)
            )
    return (min(starts, default=999999), max(ends, default=-1))


def get_text(node: dict, source: str) -> str:
    """Get text from a CST node.

    For leaf nodes with ``text`` or ``start``/``end``, returns directly.
    For non-leaf nodes, computes the range from descendant leaves and
    slices *source*.  Returns empty string when the node has no concrete
    leaf content (e.g. a null-child placeholder from verible).
    """
    if "text" in node:
        return node["text"]
    if "start" in node and "end" in node:
        return source[node["start"]:node["end"]]
    # Non-leaf: compute range from all descendant leaves
    s, e = get_node_range(node)
    if s < 999999 and e > 0:
        return source[s:e]
    return ""


class VeribleParser:
    """Parse SystemVerilog file using verible-verilog-syntax.

    Wraps the verible-verilog-syntax CLI tool to obtain CST JSON and provides
    methods for module discovery, port extraction, parameter extraction, and
    instance extraction.
    """

    def __init__(self, filepath: str = "", source: str = ""):
        if filepath:
            self.filepath = str(Path(filepath).resolve())
            self.source = Path(self.filepath).read_bytes().decode("utf-8")
            self._use_stdin = False
        elif source:
            self.filepath = "<stdin>"
            self.source = source
            self._use_stdin = True
        else:
            raise ValueError("Provide either filepath or source")
        self._data: Optional[dict] = None
        self._modules: Optional[list[dict[str, Any]]] = None

    @property
    def data(self) -> dict:
        if self._data is None:
            self._parse()
        return self._data

    def _parse(self) -> None:
        if self._use_stdin:
            result = subprocess.run(
                [VERIBLE, "--export_json", "--printtree", "-"],
                input=self.source.encode("utf-8"),
                capture_output=True, text=True,
            )
        else:
            result = subprocess.run(
                [VERIBLE, "--export_json", "--printtree", self.filepath],
                capture_output=True, text=True,
            )

        # ── No output at all: binary not found or crashed before writing JSON ──
        if not result.stdout.strip():
            stderr = result.stderr.strip() or "(no stderr)"
            hint = (
                f"verible-verilog-syntax not found on PATH"
                if result.returncode != 0 and not result.stderr
                else stderr[:400]
            )
            raise ValueError(
                f"verible produced no output (exit={result.returncode}): {hint}\n"
                f"  binary used: {VERIBLE}\n"
                f"  tip: install verible or set the VERIBLE_BIN environment variable"
            )

        # ── JSON parse failure ────────────────────────────────────────────────
        try:
            raw = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            stderr = result.stderr.strip() if result.stderr else result.stdout[:200]
            raise ValueError(f"verible produced invalid JSON output: {stderr}")

        # ── Find a valid tree entry ───────────────────────────────────────────
        # Guard against both null-valued entries (file-level parse failure) and
        # entries where the "tree" key itself maps to None.
        for v in raw.values():
            if isinstance(v, dict) and v.get("tree") is not None:
                self._data = v
                return

        # ── No usable tree: collect verible's own error messages ─────────────
        error_lines: list[str] = []
        for v in raw.values():
            if isinstance(v, dict):
                for err in v.get("errors", []) or []:
                    msg = err.get("message") or str(err)
                    if msg:
                        error_lines.append(msg)
            elif v is None:
                # Verible returns null for a file it could not parse at all
                error_lines.append("(verible returned null — likely a syntax error in the file)")

        stderr_text = result.stderr.strip()
        if stderr_text and not error_lines:
            error_lines.append(stderr_text[:400])

        detail = "\n  ".join(error_lines) if error_lines else "(no additional detail)"
        raise ValueError(
            f"verible returned no parse result for: {self.filepath}\n"
            f"  {detail}"
        )

    @property
    def tree(self) -> dict:
        return self.data["tree"]

    @property
    def tokens(self) -> list[dict]:
        return self.data.get("tokens", [])

    @property
    def errors(self) -> list[dict]:
        return self.data.get("errors", [])

    def get_text(self, node: dict) -> str:
        return get_text(node, self.source)

    # --- Module discovery ---

    def get_modules(self) -> list[dict[str, Any]]:
        """Return list of module info dicts.

        Each dict contains semantic information (name, ports, params, instances)
        and the raw CST node.  Byte offsets are computed on demand by the edit
        operations rather than stored here.
        """
        if self._modules is not None:
            return self._modules
        mods = find_all(self.tree, "kModuleDeclaration")
        result: list[dict[str, Any]] = []
        for m in mods:
            header = find_child(m, "kModuleHeader")
            if not header:
                continue
            name_node = find_descendant(header, "SymbolIdentifier")
            name = self.get_text(name_node) if name_node else "<unknown>"

            # ANSI vs non-ANSI
            paren = find_child(header, "kParenGroup")
            is_ansi = False
            if paren:
                port_list = find_child(paren, "kPortDeclarationList")
                if port_list:
                    ansi_decls = find_children(port_list, "kPortDeclaration")
                    non_ansi_ports = find_children(port_list, "kPort")
                    is_ansi = len(ansi_decls) > 0 and len(non_ansi_ports) == 0

            # Extract ports, params, instances
            ports = self._extract_ports(m, is_ansi)
            param_names = self._extract_params(m)
            instances = self._extract_instances(m)

            result.append({
                "name": name,
                "is_ansi": is_ansi,
                "cst": m,
                "ports": ports,
                "param_names": param_names,
                "instances": instances,
            })
        self._modules = result
        return result

    def _extract_ports(self, mod_node: dict, is_ansi: bool) -> list[dict[str, str]]:
        """Extract port info from CST."""
        ports: list[dict[str, str]] = []
        header = find_child(mod_node, "kModuleHeader")
        if not header:
            return ports
        paren = find_child(header, "kParenGroup")
        if not paren:
            return ports

        if is_ansi:
            port_list = find_child(paren, "kPortDeclarationList")
            if not port_list:
                return ports
            for pd in find_children(port_list, "kPortDeclaration"):
                port_info = self._parse_ansi_port(pd)
                if port_info:
                    ports.append(port_info)
        else:
            # Non-ANSI: extract port names from kPort in header, then
            # fill direction/type/width from body kModulePortDeclaration nodes.
            port_list = find_child(paren, "kPortDeclarationList")
            if port_list:
                for p in find_children(port_list, "kPort"):
                    id_node = find_descendant(p, "SymbolIdentifier")
                    if id_node:
                        ports.append({
                            "name": self.get_text(id_node),
                            "direction": "",
                            "type": "",
                            "width": "",
                        })
            self._fill_non_ansi_ports(ports, mod_node)

        return ports

    def _parse_ansi_port(self, pd_node: dict) -> Optional[dict[str, str]]:
        """Parse an ANSI kPortDeclaration node."""
        direction = ""
        ptype = ""
        width = ""
        name = ""

        # --- Direction: direct keyword child of kPortDeclaration ---
        for c in pd_node.get("children", []):
            if isinstance(c, dict) and c.get("tag") in ("input", "output", "inout", "ref"):
                direction = c["tag"]
                break

        # --- Type: keyword child of kPortDeclaration (wire/reg/…) OR
        #     kDataTypePrimitive inside kDataType (logic/bit/…) ---
        for c in pd_node.get("children", []):
            if not isinstance(c, dict):
                continue
            tag = c.get("tag", "")
            if tag in COMMON_TYPES and tag not in ("input", "output", "inout", "ref"):
                ptype = tag
                break
            if tag == "kDataType":
                prim = find_descendant(c, "kDataTypePrimitive")
                if prim:
                    for gc in prim.get("children", []):
                        if isinstance(gc, dict) and gc.get("tag") in COMMON_TYPES:
                            ptype = gc["tag"]
                            break
                break

        # --- Port name: last SymbolIdentifier not in keyword/type sets ---
        candidate_ids = []
        for leaf_id in find_all(pd_node, "SymbolIdentifier"):
            text = self.get_text(leaf_id).strip()
            if text not in _KEYWORD_SET and text not in COMMON_TYPES:
                candidate_ids.append(text)
        if candidate_ids:
            name = candidate_ids[-1]

        # --- Width: kPackedDimensions from kDataType ---
        dt = find_child(pd_node, "kDataType")
        if dt:
            packed = find_child(dt, "kPackedDimensions")
            if packed:
                width = self.get_text(packed).strip()

        if not name:
            return None

        return {"name": name, "direction": direction, "type": ptype, "width": width}

    def _fill_non_ansi_ports(self, ports: list[dict[str, str]],
                              mod_node: dict) -> None:
        """Fill direction/type/width for non-ANSI ports from body CST.

        Walks kModulePortDeclaration nodes in the module body, extracts
        direction, type, width, and all signal names from each declaration
        (handles multi-port declarations like ``input wire [7:0] a, b;``),
        then matches against the port name list by name.
        """
        name_to_port = {p["name"]: p for p in ports}
        for pd in find_all(mod_node, "kModulePortDeclaration"):
            direction = ""
            ptype = ""
            width = ""

            # --- Direction: direct keyword child ---
            for c in pd.get("children", []):
                if isinstance(c, dict) and c.get("tag") in ("input", "output", "inout", "ref"):
                    direction = c["tag"]
                    break

            # --- Type: kDataType → kDataTypePrimitive keyword or direct tag ---
            # Note: Verible may emit null children for keywords like 'reg' in
            # non-ANSI port declarations.  We first try the CST children, then
            # fall back to scanning the source text between direction and width.
            dt = find_child(pd, "kDataType")
            if dt:
                for c in dt.get("children", []):
                    if isinstance(c, dict):
                        tag = c.get("tag", "")
                        if tag in COMMON_TYPES and tag not in ("input", "output", "inout", "ref"):
                            ptype = tag
                            break
                        if tag == "kDataTypePrimitive":
                            prim = find_descendant(c, "kDataTypePrimitive")
                            if prim:
                                for gc in prim.get("children", []):
                                    if isinstance(gc, dict) and gc.get("tag") in COMMON_TYPES:
                                        ptype = gc["tag"]
                                        break
                # Width from kPackedDimensions / kDeclarationDimensions
                packed = find_child(dt, "kPackedDimensions")
                if not packed:
                    packed = find_child(dt, "kDeclarationDimensions")
                if packed:
                    width = self.get_text(packed).strip()
                # Fallback type from source when CST has null children
                if not ptype and direction:
                    pd_s, pd_e = get_node_range(pd)
                    pd_text = self.source[pd_s:pd_e]
                    _TYPE_RE = re.compile(
                        rf"^{direction}\s+(wire|reg|logic|bit|tri)\b", re.IGNORECASE
                    )
                    tm = _TYPE_RE.match(pd_text)
                    if tm:
                        ptype = tm.group(1)

            # --- Signal names: two possible CST structures ---
            # Multi-name:  kIdentifierUnpackedDimensionsList → kIdentifierUnpackedDimensions → SymbolIdentifier
            # Single-name:  kPortIdentifierList → kPortIdentifier → SymbolIdentifier
            names: list[str] = []
            id_list = find_child(pd, "kIdentifierUnpackedDimensionsList")
            if id_list:
                for iud in find_children(id_list, "kIdentifierUnpackedDimensions"):
                    id_node = find_child(iud, "SymbolIdentifier")
                    if id_node:
                        text = self.get_text(id_node).strip()
                        if text and text not in _KEYWORD_SET:
                            names.append(text)
            else:
                port_id_list = find_child(pd, "kPortIdentifierList")
                if port_id_list:
                    for pid in find_children(port_id_list, "kPortIdentifier"):
                        id_node = find_descendant(pid, "SymbolIdentifier")
                        if id_node:
                            text = self.get_text(id_node).strip()
                            if text and text not in _KEYWORD_SET:
                                names.append(text)

            for n in names:
                if n in name_to_port:
                    name_to_port[n]["direction"] = direction
                    name_to_port[n]["type"] = ptype
                    name_to_port[n]["width"] = width

    def _extract_params(self, mod_node: dict) -> list[str]:
        """Extract parameter names (including localparam) from CST."""
        params: list[str] = []
        header = find_child(mod_node, "kModuleHeader")
        if not header:
            return params

        for pd in find_all(header, "kParamDeclaration"):
            id_node = find_descendant(pd, "SymbolIdentifier")
            if id_node:
                text = self.get_text(id_node).strip()
                if text and text not in _KEYWORD_SET:
                    params.append(text)

        return params

    def _extract_instances(self, mod_node: dict) -> list[dict[str, Any]]:
        """Extract module instances from module body using CST traversal.

        Uses kInstantiationBase nodes from the Verible CST.  A node is treated
        as a module instantiation only when its kInstantiationType subtree
        contains a kLocalRoot (i.e. a user-defined type name); nodes with
        kDataTypePrimitive (reg/wire/logic/…) are variable declarations and
        are skipped.

        Each instance dict contains:
          name:            instance name
          type:            module type name
          offset:          byte offset of the instantiation in source (or -1)
          port_close_idx:  byte offset of ')' closing the port connection list (or -1)
        """
        instances: list[dict[str, Any]] = []
        for inst_base in find_all(mod_node, "kInstantiationBase"):
            inst_type_node = find_child(inst_base, "kInstantiationType")
            if not inst_type_node:
                continue
            # Module instantiation: kInstantiationType contains kLocalRoot
            # Variable declaration: kInstantiationType contains kDataTypePrimitive
            local_root = find_descendant(inst_type_node, "kLocalRoot")
            if not local_root:
                continue
            type_id = find_descendant(local_root, "SymbolIdentifier")
            if not type_id:
                continue
            inst_type = self.get_text(type_id)
            if inst_type in _KEYWORD_SET:
                continue
            # Byte offset of the entire instantiation
            inst_start, _ = get_node_range(inst_base)
            # Each instantiated unit is a kGateInstance inside kGateInstanceRegisterVariableList
            for gate_inst in find_all(inst_base, "kGateInstance"):
                name_id = find_child(gate_inst, "SymbolIdentifier")
                if name_id:
                    inst_name = self.get_text(name_id)
                    if inst_name and inst_name not in _KEYWORD_SET:
                        # port_close_idx: last ')' of the port connection paren group
                        port_close_idx = -1
                        paren = find_child(gate_inst, "kParenGroup")
                        if paren:
                            close_leaves = find_all(paren, ")")
                            if close_leaves:
                                port_close_idx = close_leaves[-1]["start"]
                        instances.append({
                            "name": inst_name,
                            "type": inst_type,
                            "offset": inst_start,
                            "port_close_idx": port_close_idx,
                        })
        return instances

    def get_module(self, module_name: Optional[str] = None) -> dict:
        """Get module info, raising error for ambiguity."""
        mods = self.get_modules()
        if not mods:
            raise ValueError("No module found in file")
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

    # --- Position helpers (computed on demand from CST) ---

    def get_module_offsets(self, mod_node: dict) -> dict[str, int]:
        """Compute insertion-point byte offsets for a module.

        Returns dict with:
          port_insert_idx:  byte offset to insert a new port in port list (')' position, or -1)
          decl_insert_idx:  byte offset to insert a signal declaration in body (before first item, or -1)
          mod_start:        byte offset of 'module' keyword
          mod_end:          byte offset right after 'endmodule'
        """
        header = find_child(mod_node, "kModuleHeader")
        close_idx = -1
        if header:
            paren = find_child(header, "kParenGroup")
            if paren:
                close_leaf = find_descendant(paren, ")")
                close_idx = close_leaf["start"] if close_leaf else -1
        mod_start, mod_end = get_node_range(mod_node)

        # port_insert_idx: insert before ')'
        port_insert_idx = close_idx

        # decl_insert_idx: start of first item in kModuleItemList
        decl_insert_idx = -1
        item_list = find_child(mod_node, "kModuleItemList")
        if item_list:
            for c in item_list.get("children", []):
                if isinstance(c, dict) and c.get("tag"):
                    s, _ = get_node_range(c)
                    if s < 999999:
                        decl_insert_idx = s
                        break

        return {
            "port_insert_idx": port_insert_idx,
            "decl_insert_idx": decl_insert_idx,
            "mod_start": mod_start,
            "mod_end": mod_end,
        }
