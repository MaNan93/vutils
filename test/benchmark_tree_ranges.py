"""Split parsing stages and measure CST range queries independently."""

import argparse
import gc
import hashlib
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vutils.vparser import BranchNode, PreOrderTreeIterator, VERIBLE, VeribleParser, VeribleVerilogSyntax


def benchmark(lines=2500, repeats=5):
    source = ("module top #(parameter W=8)(input clk, input [W-1:0] a, output [W-1:0] b);\n"
              + "".join(f"wire [7:0] x{i} = a;\n" for i in range(lines))
              + "".join(f"sub #(.W(W)) u{i}(.clk(clk), .a(a), .b(b));\n" for i in range(15))
              + "endmodule\n")
    real_process = subprocess.run
    real_loads = json.loads
    real_transform = VeribleVerilogSyntax._transform_tree
    parse_runs = []
    data = None
    for _ in range(repeats):
        data = None
        gc.collect()
        stages = {"process": 0.0, "json_decode": 0.0, "tree_conversion": 0.0}

        def measured(name, function):
            def call(*args, **kwargs):
                start = time.perf_counter()
                try:
                    return function(*args, **kwargs)
                finally:
                    stages[name] += time.perf_counter() - start
            return call

        with patch("vutils.vparser.subprocess.run", new=measured("process", real_process)), \
             patch("vutils.vparser.json.loads", new=measured("json_decode", real_loads)), \
             patch.object(VeribleVerilogSyntax, "_transform_tree",
                          new=staticmethod(measured("tree_conversion", real_transform))):
            start = time.perf_counter()
            data = VeribleParser().parse_string(source)
            total = time.perf_counter() - start
        assert data.tree is not None and not data.errors
        parse_runs.append({"total_seconds": round(total, 6),
                           **{name + "_seconds": round(value, 6) for name, value in stages.items()}})

    branches = [node for node in PreOrderTreeIterator(data.tree) if isinstance(node, BranchNode)]
    range_runs = []
    for _ in range(repeats):
        gc.collect()
        start = time.perf_counter()
        ranges = [(node.start, node.end) for node in branches]
        elapsed = time.perf_counter() - start
        digest = hashlib.sha256(json.dumps(ranges).encode()).hexdigest()
        range_runs.append({"seconds": round(elapsed, 6), "output_sha256": digest})
    assert len({r["output_sha256"] for r in range_runs}) == 1
    return {"binary": VERIBLE, "body_lines": lines, "instances": 15, "repeats": repeats,
            "source_bytes": len(source.encode("utf-8")), "branch_nodes": len(branches),
            "parse_medians_seconds": {key: round(statistics.median(r[key] for r in parse_runs), 6)
                                      for key in parse_runs[0]},
            "range_median_seconds": round(statistics.median(r["seconds"] for r in range_runs), 6),
            "parse_runs": parse_runs, "range_runs": range_runs,
            "range_queries_exclude_parsing": True, "excludes_startup_and_imports": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lines", type=int, default=2500)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = benchmark(args.lines, args.repeats)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"parse_medians_seconds": result["parse_medians_seconds"],
                      "range_median_seconds": result["range_median_seconds"],
                      "branch_nodes": result["branch_nodes"],
                      "output_sha256": result["range_runs"][0]["output_sha256"]}))
