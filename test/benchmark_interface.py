"""Compare complete and interface-only reads using paired native parses."""

import argparse
import gc
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vutils import read_interface
from vutils.vedit import _InterfaceParser, vedit
from vutils.vparser import _parse_source


def full_read(source):
    editor = vedit(source, "top")
    info = editor.analyze()
    return {"name": editor.module_name, "module_name": editor.module_name,
            "ports": editor.ports, "param_names": editor.param_names, "imports": info["imports"],
            "is_ansi": editor.is_ansi, "parameter_declaration": editor.parameter_declaration}


def benchmark(lines=2500, repeats=5, nonansi=False):
    header = ("module top #(parameter W=8)(clk, a, b);\n"
              "input clk; input [W-1:0] a; output [W-1:0] b;\n" if nonansi else
              "module top #(parameter W=8)(input clk, input [W-1:0] a, output [W-1:0] b);\n")
    source = (header + "".join(f"wire [7:0] x{i} = a;\n" for i in range(lines))
              + "".join(f"sub #(.W(W)) u{i}(.clk(clk), .a(a), .b(b));\n" for i in range(15))
              + "endmodule\n")
    modes = {"full": full_read, "interface": lambda text: read_interface(text, "top")}
    runs = {mode: [] for mode in modes}
    expected = full_read(source)
    for repeat in range(repeats):
        order = ("full", "interface") if repeat % 2 == 0 else ("interface", "full")
        for mode in order:
            gc.collect()
            read = modes[mode]
            stages = {"native_seconds": 0.0, "native_calls": 0}

            def measured_parse(*args, **kwargs):
                start = time.perf_counter()
                result = _parse_source(*args, **kwargs)
                stages["native_seconds"] += time.perf_counter() - start
                stages["native_calls"] += 1
                return result

            with patch("vutils.vparser._parse_source", side_effect=measured_parse):
                start = time.perf_counter()
                result = read(source)
                total = time.perf_counter() - start
            assert result == expected
            digest = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
            runs[mode].append({"total_seconds": total, **stages,
                               "output_sha256": digest})
    assert len({r["output_sha256"] for samples in runs.values() for r in samples}) == 1
    medians = {mode: {key: round(statistics.median(r[key] for r in samples), 6)
                      for key in samples[0] if key != "output_sha256"}
               for mode, samples in runs.items()}
    return {"backend": "tree-sitter-systemverilog", "body_lines": lines, "instances": 15,
            "source_bytes": len(source.encode()), "nonansi": nonansi, "repeats": repeats,
            "paired_alternating_order": True, "excludes_startup_and_imports": True,
            "medians": medians, "runs": runs}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lines", type=int, default=2500)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--nonansi", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.lines < 0 or args.repeats < 1:
        parser.error("lines must be nonnegative and repeats must be positive")
    result = benchmark(args.lines, args.repeats, args.nonansi)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["medians"]))
