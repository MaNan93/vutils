#!/usr/bin/env python3
"""Regression tests for add_inst_port on parameterized and multi-instance declarations."""

import unittest

from vutils.vedit import vedit


class InstPortRegressionTests(unittest.TestCase):
    def test_parameterized_multi_instance_support(self):
        src = (
            "module top(input logic clk);\n"
            "  sub #(.W(8)) u0(.clk(clk)), u1(.clk(clk));\n"
            "endmodule\n"
        )

        e1 = vedit(src, "top")
        e1.add_inst_port({"u1": {"rst_n": {"wire": "rst_n"}}})
        self.assertIn("u1(.clk(clk),", e1.module_text)
        self.assertIn(".rst_n(rst_n)", e1.module_text)

        e2 = vedit(src, "top")
        e2.add_inst_port({"u0": {"rst_n": {"wire": "rst_n"}}})
        self.assertIn("u0(.clk(clk),", e2.module_text)
        self.assertIn(".rst_n(rst_n)", e2.module_text)

    def test_idempotent_same_mapping_noop(self):
        src = (
            "module top(input logic clk, input logic rst_n);\n"
            "  sub u0(\n"
            "    .clk(clk),\n"
            "    .rst_n(rst_n)\n"
            "  );\n"
            "endmodule\n"
        )

        e = vedit(src, "top")
        out_before = e.module_text
        e.add_inst_port({"u0": {"rst_n": {"wire": "rst_n"}}})
        self.assertEqual(out_before, e.module_text)

    def test_conflict_same_port_different_wire_raises(self):
        src = (
            "module top(input logic clk, input logic rst_n, input logic rst_n2);\n"
            "  sub u0(\n"
            "    .clk(clk),\n"
            "    .rst_n(rst_n)\n"
            "  );\n"
            "endmodule\n"
        )

        e = vedit(src, "top")
        with self.assertRaises(ValueError) as ctx:
            e.add_inst_port({"u0": {"rst_n": {"wire": "rst_n2"}}})

        msg = str(ctx.exception)
        self.assertIn("already connected", msg)
        self.assertIn("rst_n", msg)
        self.assertIn("rst_n2", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
