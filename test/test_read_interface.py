"""Interface-only reads must match complete CST analysis and validate bodies."""

from vutils.vparser import _parse_source
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vutils import read_interface, read_interface_from_file
from vutils.vedit import _InterfaceParser, vedit
from vutils.vparser import PreOrderTreeIterator, SyntaxData, TreeSitterParser


def full_interface(source, name):
    editor = vedit(source, name)
    return {"name": editor.module_name, "module_name": editor.module_name,
            "ports": editor.ports, "param_names": editor.param_names, "imports": editor.analyze(False)["imports"],
            "is_ansi": editor.is_ansi, "parameter_declaration": editor.parameter_declaration}


class ReadInterfaceTests(unittest.TestCase):
    def test_declaration_variants_match_full_analysis(self):
        sources = [
            "module top(input clk, output logic signed [7:0] q [0:3]); endmodule",
            "module top #(parameter int W=8, D=4, parameter type T=logic)"
            "(input signed [W-1:0] a, b, output T q); endmodule",
            "module top(a,b,q); input signed [7:0] a,b; output reg [7:0] q; endmodule",
            "module top; wire x; endmodule",
            "// 中文前缀\nmodule top #(parameter W=8)(input [W-1:0] a); endmodule",
            "module top(input \\a.b , output \\c.d ); endmodule",
            "`define W 8\nmodule top(input [`W-1:0] a);\n`ifdef FOO\n"
            "wire unused;\n`endif\nendmodule",
            "module other(input wrong); endmodule\n"
            "module top(a); input [7:0] a; sub u0(.a(a)); endmodule",
            "module top(input clk); generate if (1) begin : g "
            "sub u0(.clk(clk)); end endgenerate endmodule",
        ]
        for source in sources:
            with self.subTest(source=source):
                self.assertEqual(read_interface(source, "top"), full_interface(source, "top"))

    def test_nested_modules_keep_selection_and_body_port_order(self):
        source = ("module top(a); input a; module inner(b); input [3:0] b; "
                  "endmodule endmodule")
        for name in ("top", "inner"):
            self.assertEqual(read_interface(source, name), full_interface(source, name))
        with self.assertRaisesRegex(ValueError, "multiple modules"):
            read_interface(source)

    def test_default_and_explicit_module_selection(self):
        self.assertEqual(read_interface("module top; endmodule")["name"], "top")
        source = "module first; endmodule module top(input a); endmodule"
        self.assertEqual(read_interface(source, "top")["ports"][0]["name"], "a")
        with self.assertRaisesRegex(ValueError, "multiple modules"):
            read_interface(source)
        with self.assertRaisesRegex(ValueError, "not found.*first, top"):
            read_interface(source, "missing")

    def test_empty_source_has_no_module(self):
        with self.assertRaisesRegex(ValueError, "No module"):
            read_interface("// nothing here\n")

    def test_invalid_body_is_rejected(self):
        with self.assertRaises(ValueError):
            read_interface("module top(input a); assign = ; endmodule", "top")

    def test_invalid_unselected_module_is_rejected(self):
        source = "module top(input a); endmodule module bad; assign = ; endmodule"
        with self.assertRaises(ValueError):
            read_interface(source, "top")

    def test_invalid_header_is_rejected(self):
        with self.assertRaises(ValueError):
            read_interface("module top(input); endmodule", "top")

    def test_file_auto_selection_and_explicit_override(self):
        source = "module first(input a); endmodule module top(input b); endmodule"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "top.sv"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "multiple modules"):
                read_interface_from_file(path)
            self.assertEqual(read_interface_from_file(path, auto_select=True)["name"], "top")
            self.assertEqual(read_interface_from_file(path, "first", auto_select=True)["name"], "first")
            path = Path(tmp) / "unmatched.sv"
            path.write_text(source, encoding="utf-8")
            self.assertEqual(read_interface_from_file(path, auto_select=True)["name"], "first")

    def test_file_errors_are_propagated(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                read_interface_from_file(Path(tmp) / "missing.sv")
            path = Path(tmp) / "bad.sv"
            path.write_text("module bad; assign = ; endmodule", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_interface_from_file(path)

    def test_one_native_parse_without_instance_extraction(self):
        source = "module top(input a); sub u0(.a(a)); endmodule"
        with patch("vutils.vparser._parse_source", wraps=_parse_source) as run, \
             patch("vutils.vedit._extract_instances", side_effect=AssertionError("instance scan")):
            result = read_interface(source)
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("cst", result)
        self.assertNotIn("instances", result)

    def test_interface_read_keeps_body_wrappers_lazy(self):
        source = "module top(input a); " + "wire [7:0] x; " * 30 + "sub u0(.a(a)); endmodule"
        parser = _InterfaceParser()
        data = parser.parse_string(source)
        self.assertIsNone(data.tree._children)
        from vutils.vedit import _find_module_nodes, _extract_ports, _is_ansi_module
        mod = _find_module_nodes(data.tree)[0]
        self.assertEqual(_extract_ports(mod, source, _is_ansi_module(mod))[0]["name"], "a")
        body = next(c for c in mod.children if c.tag == "net_declaration")
        self.assertIsNone(body._children)
        editor = vedit(source, "top")
        self.assertEqual(editor.instances[0]["name"], "u0")
        editor.add_inst_ports("u0", {"b": "b"})
        self.assertIn(".b(b)", editor.module_text)


if __name__ == "__main__":
    unittest.main()
