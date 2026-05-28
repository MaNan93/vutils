#!/usr/bin/env python3
"""Command-line interface for Verilog/SystemVerilog module editing.

Uses verible-verilog-syntax CST for precise source code navigation and editing.
CLI interface compatible with modeditor.
"""

import argparse
import sys
from pathlib import Path
from typing import Any

from .vedit import (
    COMMON_TYPES,
    vedit,
    gen_inst,
    gen_sig_decl,
    looks_like_name,
    looks_like_width_or_dimension,
    normalize_dimension,
    normalize_width,
    split_csv,
)


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

class ExitCode:
    """Named exit codes for CLI operations.

    Follows UNIX convention: 0 = success, non-zero = failure.
    """
    OK = 0
    ERR_FILE_NOT_FOUND = 1
    ERR_PARSE_FAIL = 2
    ERR_EDIT_FAIL = 3
    ERR_GEN_INST_FAIL = 4


# ---------------------------------------------------------------------------
# CLI Argument Parsers
# ---------------------------------------------------------------------------

def _infer_signal_parts(
    rest: list[str], *, default_type: str = "wire"
) -> dict[str, str]:
    """Infer type, width, name, dimension from token list after direction removal.

    Args:
        rest: Remaining tokens after stripping the leading direction (for ports)
              or the raw token list (for wires).
        default_type: Default signal type when none is specified.

    Returns:
        Dict with keys: type, width, name, dimension
    """
    typ = width = name = dimension = ""
    n = len(rest)

    if n == 0:
        pass  # all empty — caller should validate name
    elif n == 1:
        name = rest[0]
    elif n == 2:
        if looks_like_width_or_dimension(rest[0]) and looks_like_name(rest[1]):
            # Check if rest[0] is actually "TYPE WIDTH" combined (e.g. "logic [7:0]")
            # rather than a bare width like "[7:0]"
            sub_parts = rest[0].split(None, 1)
            if (len(sub_parts) == 2
                    and sub_parts[0] in COMMON_TYPES
                    and looks_like_width_or_dimension(sub_parts[1])):
                typ, width, name = sub_parts[0], sub_parts[1], rest[1]
            else:
                width, name = rest[0], rest[1]
        else:
            typ, name = rest[0], rest[1]
    elif n == 3:
        if (rest[0] in COMMON_TYPES
                and looks_like_width_or_dimension(rest[1])
                and looks_like_name(rest[2])):
            typ, width, name = rest[0], rest[1], rest[2]
        elif looks_like_width_or_dimension(rest[0]) and looks_like_name(rest[1]):
            width, name = rest[0], rest[1]
            dimension = rest[2]
        else:
            typ, width, name = rest[0], rest[1], rest[2]
    else:  # n >= 4
        if (rest[0] in COMMON_TYPES
                and looks_like_width_or_dimension(rest[1])
                and looks_like_name(rest[2])):
            typ, width, name = rest[0], rest[1], rest[2]
            dimension = ",".join(rest[3:]).strip()
        elif looks_like_width_or_dimension(rest[0]) and looks_like_name(rest[1]):
            width, name = rest[0], rest[1]
            dimension = ",".join(rest[2:]).strip()
        else:
            typ, width, name = rest[0], rest[1], rest[2]
            dimension = ",".join(rest[3:]).strip()

    return {
        "type": typ.strip() or default_type,
        "width": normalize_width(width),
        "name": name.strip(),
        "dimension": normalize_dimension(dimension),
    }


def parse_add_port_spec(port_csv: str) -> dict[str, str]:
    """Parse --add-port CSV string into port info dict.
    
    Type is not auto-filled if unspecified; user must provide it explicitly.
    """
    parts = split_csv(port_csv)
    if len(parts) < 2:
        raise ValueError("--add-port needs at least 'direction, name'")
    direction = parts[0].strip().lower()
    if direction not in {"input", "output", "inout", "ref"}:
        raise ValueError("--add-port direction must be input/output/inout/ref")

    info = _infer_signal_parts(parts[1:], default_type="")
    if not info["name"]:
        raise ValueError("--add-port name is required")
    info["direction"] = direction
    return info


def parse_add_wire_spec(wire_csv: str) -> dict[str, str]:
    """Parse --add-wire CSV string into wire info dict.

    Supports both comma-separated (``\"wire, [31:0], addr\"``) and
    space-separated (``\"wire [31:0] addr\"``) formats.
    """
    parts = split_csv(wire_csv)
    if len(parts) == 0:
        raise ValueError("--add-wire needs at least a signal name")
    # If split_csv returned a single token that contains spaces, the user
    # likely used space-separated format (e.g. "wire [31:0] addr") rather
    # than CSV.  Split it further so _infer_signal_parts can classify each
    # field correctly.
    if len(parts) == 1 and " " in parts[0]:
        parts = parts[0].split()
    return _infer_signal_parts(parts, default_type="wire")


def parse_add_inst_port_spec(csv_str: str) -> dict[str, str]:
    """Parse --add-inst-port CSV string."""
    parts = split_csv(csv_str)
    if len(parts) < 2:
        raise ValueError("--add-inst-port needs 'instname, port[, wire]'")
    return {
        "instname": parts[0].strip(),
        "port": parts[1].strip(),
        "wire": parts[2].strip() if len(parts) >= 3 else "",
    }


# ---------------------------------------------------------------------------
# Output Helpers
# ---------------------------------------------------------------------------

def print_port_list(module_info: dict, direction: str = "all") -> None:
    """Print port list, optionally filtered by direction."""
    ports = module_info.get("ports", [])
    entries = []
    for p in ports:
        d = p.get("direction", "").strip().lower()
        if direction != "all" and d != direction:
            continue
        name    = p.get("name", "").strip()
        dir_    = p.get("direction", "").strip()
        ptype   = p.get("type", "").strip()
        width   = p.get("width", "").strip()
        type_width = f"{ptype} {width}".strip() if ptype else width
        entries.append((name, dir_, type_width))

    if not entries:
        return

    name_w = max(len(e[0]) for e in entries)
    dir_w  = max(len(e[1]) for e in entries)
    for name, dir_, type_width in entries:
        tw_part = f", {type_width}" if type_width else ""
        print(f"{name:<{name_w}}, {dir_:<{dir_w}}{tw_part}")


def print_instance_tree(
    module_name: str,
    module_index: dict[str, dict[str, Any]],
    prefix: str,
    seen: set[str],
) -> None:
    row = module_index.get(module_name)
    if row is None:
        return

    instances = row.get("instances", [])
    for idx, inst in enumerate(instances):
        branch = "\u2514\u2500 " if idx == len(instances) - 1 else "\u251c\u2500 "
        print(f"{prefix}{branch}{inst['name']} ({inst['type']})")
        next_prefix = prefix + ("   " if idx == len(instances) - 1 else "\u2502  ")
        if inst["type"] in seen:
            continue
        seen.add(inst["type"])
        print_instance_tree(inst["type"], module_index, next_prefix, seen)
        seen.remove(inst["type"])


def print_hierarchy(all_modules: list[dict]) -> None:
    """Print hierarchy for all modules."""
    if not all_modules:
        print("No module declaration found.")
        return

    module_index: dict[str, dict[str, Any]] = {}
    for mod in all_modules:
        module_index[mod["name"]] = mod

    for mod in all_modules:
        name = mod["name"]
        print(f". {name}")
        print_instance_tree(name, module_index, "", set())
        print()


# ---------------------------------------------------------------------------
# Edit Dispatcher
# ---------------------------------------------------------------------------

def apply_edit_actions(source_text: str, mod_name: str, args: argparse.Namespace) -> tuple[str, bool]:
    """Apply all edit flags to source text. Returns (new_text, changed)."""
    original = source_text
    ed = vedit(source_text, mod_name)

    for spec_str in (args.add_port or []):
        spec = parse_add_port_spec(spec_str)
        ed.add_port({spec["name"]: spec})
    for spec_str in (args.add_wire or []):
        spec = parse_add_wire_spec(spec_str)
        ed.add_wire({spec["name"]: spec})
    for spec_str in (args.add_inst_port or []):
        spec = parse_add_inst_port_spec(spec_str)
        ed.add_inst_port({
            spec["instname"]: {
                spec["port"]: {"wire": spec["wire"]},
            },
        })

    new_source = ed.module_text
    return new_source, new_source != original


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_module_name(
    module_names: list[str],
    requested: str | None,
    sv_file: Path,
) -> str:
    """Return the single module name to operate on, or raise ValueError."""
    if requested:
        if requested not in module_names:
            names = ", ".join(module_names)
            raise ValueError(
                f"Module '{requested}' not found in {sv_file}\n"
                f"Available modules: {names}"
            )
        return requested
    if len(module_names) == 1:
        return module_names[0]
    names = ", ".join(module_names)
    raise ValueError(
        f"File {sv_file} contains multiple modules: {names}\n"
        f"Please specify --module to select one"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse / edit Verilog module ports with verible-verilog-syntax",
    )
    parser.add_argument("sv_file", type=Path, help="Path to .v/.sv file")

    parser.add_argument(
        "--list-port",
        choices=["all", "input", "output", "inout", "ref"],
        dest="list_port",
        metavar="FILTER",
        help="List ports filtered by direction: all, input, output, inout, ref",
    )
    parser.add_argument("--hier", "--hierarchy", action="store_true", dest="hierarchy",
                        help="Print module hierarchy")
    parser.add_argument("--module", dest="module_name",
                        help="Specify module name if file contains multiple modules")
    parser.add_argument("--inst", action="store_true", dest="inst",
                        help="Print signal declarations + instantiation template")
    parser.add_argument("--inst-name", dest="inst_name",
                        help="Instance name used with --inst")
    parser.add_argument(
        "--inst-no-param",
        action="store_true",
        dest="inst_no_param",
        help="With --inst, skip parameter block",
    )
    parser.add_argument(
        "--inst-no-decl",
        action="store_true",
        dest="inst_no_decl",
        help="With --inst, suppress signal declarations (inst template only)",
    )

    parser.add_argument("--add-port", dest="add_port", action="append", metavar="SPEC",
                        help="Add port: 'direction, type, width, name, dimension' (repeatable)")
    parser.add_argument("--add-wire", dest="add_wire", action="append", metavar="SPEC",
                        help="Add wire: 'type, width, name, dimension' (repeatable)")
    parser.add_argument("--add-inst-port", dest="add_inst_port", action="append", metavar="SPEC",
                        help="Add instance port: 'instname, port, wire' (repeatable)")

    out_group = parser.add_mutually_exclusive_group()
    out_group.add_argument("--inplace", action="store_true", dest="inplace",
                           help="Write edits back to input file")
    out_group.add_argument("--output", dest="output_file", type=Path,
                           help="Write edited output to another file")

    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if not args.sv_file.exists():
        print(f"Error: Input file not found: {args.sv_file}")
        return ExitCode.ERR_FILE_NOT_FOUND

    has_edit_action = any((
        args.add_port, args.add_wire, args.add_inst_port,
    ))

    try:
        source_text = args.sv_file.read_text(encoding="utf-8")
        module_names = vedit.list_modules(source_text)
    except UnicodeDecodeError as e:
        print(f"Error: File encoding issue in {args.sv_file}")
        print(f"  Details: {e}")
        return ExitCode.ERR_PARSE_FAIL
    except Exception as exc:
        print(f"Error: Failed to parse {args.sv_file}")
        print(f"  Details: {exc}")
        return ExitCode.ERR_PARSE_FAIL

    if has_edit_action:
        try:
            mod_name = _resolve_module_name(module_names, args.module_name, args.sv_file)
            new_text, changed = apply_edit_actions(source_text, mod_name, args)
        except Exception as exc:
            print(f"Error: Edit operation failed")
            print(f"  Details: {exc}")
            return ExitCode.ERR_EDIT_FAIL

        if args.inplace:
            args.sv_file.write_bytes(new_text.encode("utf-8"))
            print("Edit applied in-place.")
            return ExitCode.OK

        if args.output_file:
            args.output_file.write_bytes(new_text.encode("utf-8"))
            print(f"Edited file written to: {args.output_file}")
            return ExitCode.OK

        if changed:
            print(new_text)
        else:
            print("No changes needed.")
        return ExitCode.OK

    if args.inst:
        try:
            mod_name = _resolve_module_name(module_names, args.module_name, args.sv_file)
            module_info = vedit(source_text, mod_name).analyze(include_instances=False)
            parts: list[str] = []
            if not args.inst_no_decl:
                decl = gen_sig_decl(module_info)
                if decl:
                    parts.append(decl)
            parts.append(gen_inst(
                module_info,
                include_params=not args.inst_no_param,
                instance_name=args.inst_name,
            ))
            print("\n\n".join(parts))
        except Exception as exc:
            print(f"Error: Failed to generate instantiation template")
            print(f"  Details: {exc}")
            return ExitCode.ERR_GEN_INST_FAIL
        return ExitCode.OK

    if args.hierarchy:
        try:
            if args.module_name:
                mod_name = _resolve_module_name(module_names, args.module_name, args.sv_file)
                all_mods = [vedit(source_text, mod_name).analyze()]
            else:
                all_mods = [vedit(source_text, name).analyze() for name in module_names]
        except Exception as exc:
            print(f"Error: Failed to analyze module hierarchy")
            print(f"  Details: {exc}")
            return ExitCode.ERR_PARSE_FAIL
        print_hierarchy(all_mods)
        return ExitCode.OK

    if args.list_port is None:
        parser.print_help()
        return ExitCode.OK

    try:
        mod_name = _resolve_module_name(module_names, args.module_name, args.sv_file)
        module_info = vedit(source_text, mod_name).analyze(include_instances=False)
    except Exception as exc:
        print(f"Error: Failed to analyze module")
        print(f"  Details: {exc}")
        return ExitCode.ERR_PARSE_FAIL

    print_port_list(module_info, args.list_port)
    return ExitCode.OK


if __name__ == "__main__":
    raise SystemExit(main())
