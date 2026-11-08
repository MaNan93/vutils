#!/usr/bin/env python3
"""Test add_inst_port when instantiated module name is a macro."""

import unittest
import tempfile
from pathlib import Path

from vutils.vedit import vedit
from vutils.vparser import TreeSitterParser


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
        self.assertEqual(inst["type"], "`MY_ADDER")

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
    def test_macro_cst_keeps_original_text_and_offsets(self):
        source = "`define MY_ADDER adder\nmodule top(); `MY_ADDER u0 (.a(a), .b(b)); endmodule"
        data = TreeSitterParser().parse_string(source)
        self.assertFalse(data.errors)
        inst = data.tree.find({"tag": "module_instantiation"})
        self.assertIsNotNone(inst)
        self.assertEqual(inst.field("instance_type").text, "`MY_ADDER")
        self.assertEqual(source.encode()[inst.start:inst.end].decode(), inst.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
