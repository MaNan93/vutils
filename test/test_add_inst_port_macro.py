#!/usr/bin/env python3
"""Test add_inst_port when instantiated module name is a macro."""

import unittest
import tempfile
from pathlib import Path

from vutils.vedit import vedit
from vutils.vparser import VeribleParser


MACRO_SV = """\
`define MY_ADDER adder

module top(
    input  wire        clk,
    input  wire        rst,
    input  wire [7:0]  a,
    input  wire [7:0]  b,
    output wire [7:0]  sum
);

    `MY_ADDER u_adder0 (
        .clk(clk),
        .rst(rst),
        .a(a),
        .b(b)
    );

    adder u_adder1 (
        .clk(clk),
        .rst(rst),
        .a(a),
        .b(b)
    );

endmodule
"""

MACRO_SV_SINGLE_LINE = """\
`define MY_ADDER adder

module top(
    input  wire        clk,
    input  wire        rst,
    input  wire [7:0]  a,
    input  wire [7:0]  b,
    output wire [7:0]  sum
);

    `MY_ADDER u_adder0 (.clk(clk), .rst(rst), .a(a), .b(b));

endmodule
"""


class TestAddInstPortMacro(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="vutils_test_macro_"))

    def _write_and_open(self, sv_content: str, suffix: str = ".sv") -> vedit:
        p = self.tmpdir / f"test_macro{suffix}"
        p.write_text(sv_content, encoding="utf-8")
        return vedit.from_file(str(p), "top")

    # ------------------------------------------------------------------
    def test_macro_instance_list_instances(self):
        """Macro-instantiated module should appear in instance list."""
        ed = self._write_and_open(MACRO_SV)
        inst_names = [i["name"] for i in ed.instances]
        self.assertIn("u_adder0", inst_names)
        self.assertIn("u_adder1", inst_names)

    def test_macro_instance_type_extracted(self):
        """Instance type should be the macro text."""
        ed = self._write_and_open(MACRO_SV)
        inst = next(i for i in ed.instances if i["name"] == "u_adder0")
        # verible expands macros, so the type should resolve to 'adder'
        # or be the macro reference
        self.assertIn(inst["type"], ("adder", "`MY_ADDER"))

    def test_add_inst_port_to_macro_instance(self):
        """add_inst_port should work when instance was macro-instantiated."""
        ed = self._write_and_open(MACRO_SV)
        ed.add_inst_port({"u_adder0": {"sum": {"wire": "sum"}}})

        result = ed.module_text
        self.assertIn(".sum(sum)", result)
        # instance name unchanged
        self.assertIn("`MY_ADDER u_adder0", result)

    def test_add_inst_port_to_macro_instance_single_line(self):
        """add_inst_port should work on single-line macro instance."""
        ed = self._write_and_open(MACRO_SV_SINGLE_LINE)
        ed.add_inst_port({"u_adder0": {"sum": {"wire": "sum"}}})

        result = ed.module_text
        self.assertIn(".sum(sum)", result)

    def test_add_inst_port_duplicate_to_macro_instance(self):
        """Duplicate port add should be idempotent (skip)."""
        ed = self._write_and_open(MACRO_SV)
        ed.add_inst_port({"u_adder0": {"clk": {"wire": "clk"}}})
        # clk already connected to clk → should be unchanged
        self.assertEqual(ed.module_text, MACRO_SV)

    def test_add_inst_port_conflict_to_macro_instance(self):
        """Conflicting port connection should raise ValueError."""
        ed = self._write_and_open(MACRO_SV)
        with self.assertRaises(ValueError) as ctx:
            ed.add_inst_port({"u_adder0": {"clk": {"wire": "sys_clk"}}})
        self.assertIn("already connected", str(ctx.exception))

    # ------------------------------------------------------------------
    def test_parse_debug(self):
        """Raw CST inspection: what does verible produce for macro inst."""
        source = """\
`define MY_ADDER adder
module top();
    `MY_ADDER u0 (.a(a), .b(b));
endmodule
"""
        parser = VeribleParser()
        data = parser.parse_string(source, options={"gen_tree": True, "skip_null": True})
        self.assertIsNotNone(data)
        self.assertIsNotNone(data.tree)

        # Find kModuleDeclaration
        mod = data.tree.find({"tag": "kModuleDeclaration"})
        self.assertIsNotNone(mod, "kModuleDeclaration not found in CST")

        # Find kInstantiationBase
        inst_base = mod.find({"tag": "kInstantiationBase"})
        self.assertIsNotNone(inst_base, "kInstantiationBase not found - macro may affect CST")

        # Print CST tags under kInstantiationBase for diagnosis
        def collect_tags(node, depth=0):
            lines = []
            tag = getattr(node, "tag", "?")
            lines.append(f"{'  ' * depth}{tag}")
            for child in getattr(node, "children", []):
                lines.extend(collect_tags(child, depth + 1))
            return lines

        tags = collect_tags(inst_base)
        print("\n--- CST tags under kInstantiationBase ---")
        for t in tags:
            print(t)

        # The kInstantiationType exists even with macro?
        itype = inst_base.find({"tag": "kInstantiationType"})
        print(f"\nkInstantiationType found: {itype is not None}")
        if itype:
            print(f"kInstantiationType text: '{getattr(itype, 'text', '')}'")
            # Check children of itype
            for c in getattr(itype, "children", []):
                print(f"  child: tag={getattr(c, 'tag', '?')} text='{getattr(c, 'text', '')}'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
