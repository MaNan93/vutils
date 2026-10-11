"""Measure consecutive vedit reads on one unchanged source, excluding startup."""

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vutils.vedit import vedit
from vutils.vparser import VERIBLE


def benchmark(lines=2500, repeats=5):
    source = (
        "module top #(parameter W=8)(input clk, input [W-1:0] a, output [W-1:0] b);\n"
        + "".join(f"wire [7:0] x{i} = a;\n" for i in range(lines))
        + "".join(f"sub #(.W(W)) u{i}(.clk(clk), .a(a), .b(b));\n" for i in range(15))
        + "endmodule\n"
    )
    runs = []
    real_run = subprocess.run
    for _ in range(repeats):
        counts = {"calls": 0, "tree_calls": 0, "stdout_bytes": 0}

        def measured_run(*args, **kwargs):
            result = real_run(*args, **kwargs)
            counts["calls"] += 1
            counts["tree_calls"] += int("--printtree" in args[0])
            counts["stdout_bytes"] += len(result.stdout or b"")
            return result

        with patch("vutils.vparser.subprocess.run", side_effect=measured_run):
            start = time.perf_counter()
            editor = vedit(source, "top")
            first = editor.gen_inst(instance_name="u_top", port_map={"a": "data_in"})
            second = editor.gen_inst(include_params=False, instance_name="u_other")
            ports = editor.ports
            params = editor.param_names
            instances = editor.instances
            elapsed = time.perf_counter() - start
        assert len(ports) == 3 and params == ["W"] and len(instances) == 15
        digest = hashlib.sha256(json.dumps([first, second, ports, params, instances],
                                          sort_keys=True).encode("utf-8")).hexdigest()
        runs.append({"seconds": round(elapsed, 4), **counts, "output_sha256": digest})
    assert len({r["output_sha256"] for r in runs}) == 1
    return {"binary": VERIBLE, "body_lines": lines, "instances": 15, "repeats": repeats,
            "source_bytes": len(source.encode("utf-8")),
            "median_seconds": round(statistics.median(r["seconds"] for r in runs), 4),
            "excludes_startup_and_imports": True, "runs": runs}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lines", type=int, default=2500)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = benchmark(args.lines, args.repeats)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"median_seconds": result["median_seconds"],
                      "parser_calls": [r["calls"] for r in result["runs"]],
                      "output_sha256": result["runs"][0]["output_sha256"]}))
