"""Reproductions from the cross-tool correctness review."""
import tempfile
import unittest
from pathlib import Path

from vutils import read_interface, vedit
from vutils.vedit import add_inst_port, add_port, add_wire


class ReviewRegressionTests(unittest.TestCase):
    def assert_valid(self, editor):
        from vutils.vparser import VeribleParser
        self.assertFalse(VeribleParser().parse_string(editor.module_text, {"gen_tree": False}).errors)

    def test_unicode_combined_edit_and_idempotence(self):
        for source in (
            "// 中文注释：模块接口\nmodule top(input a); sub u0(.a(a)); endmodule",
            "// 中文注释\nmodule top(a); input a; sub u0(.a(a)); endmodule",
        ):
            with self.subTest(source=source):
                editor = vedit(source, "top")
                ports = {"b": {"direction": "input"}}
                pins = {"u0": {"b": {"wire": "b"}}}
                editor.add_ports_and_inst_ports(ports, pins)
                self.assert_valid(editor)
                self.assertEqual([p["name"] for p in editor.ports], ["a", "b"])
                self.assertIn(".b(b)", editor.module_text)
                after = editor.module_text
                editor.add_inst_ports("u0", {"b": "b"})
                self.assertEqual(editor.module_text, after)
                after = editor.module_text
                editor.add_ports_and_inst_ports(ports, pins)
                self.assertEqual(editor.module_text, after)

    def test_unicode_wire_insertion(self):
        editor = vedit("// 中文注释\nmodule top(input a); assign a=a; endmodule", "top")
        editor.add_wire({"payload": {"width": "[7:0]"}})
        self.assertIn("wire [7:0] payload;", editor.module_text)
        self.assert_valid(editor)

    def test_unicode_free_edit_functions(self):
        source = "// 中文注释\nmodule top(input a); sub u0(.a(a)); endmodule"
        source = add_port(source, None, "top", {"name": "b", "direction": "input"})
        source = add_wire(source, None, "top", {"name": "w", "type": "wire"})
        source = add_inst_port(source, None, "top", {"instname": "u0", "port": "b", "wire": "b"})
        self.assert_valid(vedit(source, "top"))
        self.assertEqual(add_inst_port(source, None, "top", {"instname": "u0", "port": "b", "wire": "b"}), source)

    def test_nested_module_cannot_override_outer_port(self):
        source = "module top(a); input [7:0] a; module inner(a); output [3:0] a; endmodule endmodule"
        for info in (read_interface(source, "top"), vedit(source, "top").analyze(False)):
            self.assertEqual(info["ports"][0]["direction"], "input")
            self.assertEqual(info["ports"][0]["width"], "[7:0]")

    def test_nested_instances_belong_only_to_inner_module(self):
        source = "module top(input a); sub u0(.a(a)); module inner(input b); sub u1(.b(b)); endmodule endmodule"
        self.assertEqual([i["name"] for i in vedit(source, "top").instances], ["u0"])
        self.assertEqual([i["name"] for i in vedit(source, "inner").instances], ["u1"])
        editor = vedit(source, "top")
        with self.assertRaisesRegex(ValueError, "not found"):
            editor.add_inst_ports("u1", {"c": "c"})
        self.assertEqual(editor.module_text, source)

    def test_selection_by_instance_picks_inner_owner(self):
        source = "module top; module inner; sub u1(); endmodule endmodule"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "top.sv"
            path.write_text(source, encoding="utf-8")
            self.assertEqual(vedit.from_file(path, auto_select=True, inst_name="u1").module_name, "inner")

    def test_rhs_and_instance_connections_do_not_declare_wires(self):
        for body in ("wire x = y;", "sub u0(.a(y));", "logic [y-1:0] x;",
                     "module inner; wire y; endmodule"):
            with self.subTest(body=body):
                editor = vedit("module top(input a); " + body + " endmodule", "top")
                editor.add_wire({"y": {"width": "[7:0]"}})
                self.assertIn("wire [7:0] y;", editor.module_text)
                self.assert_valid(editor)

    def test_actual_declarations_make_add_wire_idempotent(self):
        for body, name in (("", "a"), ("wire x = y;", "x"),
                           ("wire x, y;", "y"), ("logic q;", "q"),
                           ("reg [7:0] q = '0;", "q")):
            with self.subTest(body=body):
                source = "module top(input a); " + body + " endmodule"
                self.assertEqual(vedit(source, "top").add_wire({name: {}}).module_text, source)

    def test_implicit_named_connection_is_idempotent(self):
        source = "module top(input a); sub u0(.a); endmodule"
        self.assertEqual(vedit(source, "top").add_inst_ports("u0", {"a": "a"}).module_text, source)

    def test_empty_and_commented_connection_lists(self):
        for connection in ("", "\n", "/* empty */", ".a(a) /* trailing */",
                           ".a(a) /*\nplain comment\n*/\n", ".a(a) // trailing\n"):
            with self.subTest(connection=connection):
                source = "module top(input a); sub u0(" + connection + "); endmodule"
                editor = vedit(source, "top").add_inst_ports("u0", {"b": "b"})
                self.assert_valid(editor)
                self.assertIn(".b(b)", editor.module_text)
                if "comment" in connection or "trailing" in connection or "empty" in connection:
                    self.assertIn(connection.replace(".a(a)", ""), editor.module_text)
                after = editor.module_text
                editor.add_inst_ports("u0", {"b": "b"})
                self.assertEqual(editor.module_text, after)

    def test_port_insertion_preserves_trailing_comments(self):
        for source in ("module top(input a /* trailing */); endmodule",
                       "module top(input a /*\nplain comment\n*/\n); endmodule"):
            with self.subTest(source=source):
                editor = vedit(source, "top").add_port({"b": {"direction": "input"}})
                self.assert_valid(editor)
                self.assertEqual([p["name"] for p in editor.ports], ["a", "b"])

    def test_insertion_after_conditional_ports_and_connections(self):
        source = ("module top(input a\n`ifdef EXTRA\n, input extra\n`endif\n);\n"
                  "sub u0(.a(a)\n`ifdef EXTRA\n, .extra(extra)\n`endif\n); endmodule")
        editor = vedit(source, "top")
        editor.add_ports_and_inst_ports({"b": {"direction": "input"}},
                                       {"u0": {"b": {"wire": "b"}}})
        self.assertNotIn("`endif,", editor.module_text)
        self.assert_valid(editor)
        self.assertIn("b", [p["name"] for p in editor.ports])
        after = editor.module_text
        editor.add_inst_ports("u0", {"b": "b"})
        self.assertEqual(editor.module_text, after)


if __name__ == "__main__":
    unittest.main()
