"""Editing correctness and bounded parser-call regressions."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vutils.vedit import vedit
from vutils.vparser import VeribleParser
from vutils.vparser import BranchNode, TokenNode, PreOrderTreeIterator, PostOrderTreeIterator


class BatchEditingTests(unittest.TestCase):
    def assert_valid(self, source):
        self.assertFalse(VeribleParser().parse_string(source, {"gen_tree": False}).errors, source)

    def test_combined_ports_and_connections_use_one_tree_and_one_check(self):
        for source in ("module top(input a); sub u0(.a(a)); endmodule",
                       "module top(a); input a; sub u0(.a(a)); endmodule",
                       "module top(a);\ninput a;\n// retain comment\nsub #(.N(2)) u0(.a(a));\nendmodule"):
            with self.subTest(source=source):
                editor = vedit(source, "top")
                ports = {"b": {"direction": "input"}, "c": {"direction": "output", "width": "[7:0]"}}
                pins = {"u0": {"b": {"wire": "b"}, "c": {"wire": "c"}}}
                with patch("vutils.vparser.subprocess.run", wraps=subprocess.run) as run:
                    editor.add_ports_and_inst_ports(ports, pins)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(sum("--printtree" in call.args[0] for call in run.call_args_list), 1)
                self.assertEqual({p["name"] for p in editor.ports}, {"a", "b", "c"})
                self.assertIn(".b(b)", editor.module_text)
                self.assertIn(".c(c)", editor.module_text)
                sequential = vedit(source, "top").add_port(ports).add_inst_port(pins)
                self.assertEqual(editor.module_text, sequential.module_text)
                self.assert_valid(editor.module_text)
                original = editor.module_text
                with patch("vutils.vparser.subprocess.run", wraps=subprocess.run) as run:
                    editor.add_ports_and_inst_ports(ports, pins)
                self.assertEqual(editor.module_text, original)
                self.assertEqual(run.call_count, 0)

    def test_combined_invalid_port_rolls_back_successful_instance_edit(self):
        source = "module top(input a); sub u0(.a(a)); endmodule"
        editor = vedit(source, "top")
        with self.assertRaises(ValueError):
            editor.add_ports_and_inst_ports({"b": {"direction": "input", "width": "[7:"}},
                                           {"u0": {"b": {"wire": "b"}}})
        self.assertEqual(editor.module_text, source)
        self.assertEqual([p["name"] for p in editor.ports], ["a"])
        editor.add_ports_and_inst_ports({"b": {"direction": "input"}}, {"u0": {"b": {"wire": "b"}}})
        self.assert_valid(editor.module_text)

    def test_ports_only_loading_defers_instances_without_reparsing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "top.sv"
            path.write_text("module top(input a); sub u0(.a(a)); endmodule", encoding="utf-8")
            import importlib
            edit_module = importlib.import_module("vutils.vedit")
            with patch("vutils.vparser.subprocess.run", wraps=subprocess.run) as run, \
                 patch("vutils.vedit._extract_instances", wraps=edit_module._extract_instances) as extract:
                editor = vedit.from_file(path)
                self.assertEqual(editor.ports[0]["name"], "a")
                self.assertFalse(editor.analyze(include_instances=False)["instances"])
                self.assertEqual(extract.call_count, 0)
                self.assertEqual(editor.instances[0]["name"], "u0")
                self.assertEqual(editor.instances[0]["name"], "u0")
                self.assertEqual(extract.call_count, 1)
                self.assertEqual(run.call_count, 1)

    def test_multi_module_selection_reuses_original_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "helper.sv"
            path.write_text("module helper; endmodule\nmodule target(input a); sub u0(.a(a)); endmodule",
                            encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "multiple modules"):
                vedit.from_file(path)
            with patch("vutils.vparser.subprocess.run", wraps=subprocess.run) as run:
                editor = vedit.from_file(path, auto_select=True, inst_name="u0")
                self.assertEqual(editor.module_name, "target")
                editor.add_ports_and_inst_ports({"b": {"direction": "input"}}, {"u0": {"b": {"wire": "b"}}})
            self.assertEqual(run.call_count, 2)
            self.assertTrue(editor.module_text.startswith("module helper; endmodule"))
            self.assert_valid(editor.module_text)

    def test_iterative_tree_traversal_order_and_reverse(self):
        tree = BranchNode("root", children=[BranchNode("left", children=[TokenNode("a", 0, 1), TokenNode("b", 1, 2)]),
                                            TokenNode("c", 2, 3)])
        for iterator, reverse, expected in (
            (PreOrderTreeIterator, False, ["root", "left", "a", "b", "c"]),
            (PreOrderTreeIterator, True, ["root", "c", "left", "b", "a"]),
            (PostOrderTreeIterator, False, ["a", "b", "left", "c", "root"]),
            (PostOrderTreeIterator, True, ["c", "b", "a", "left", "root"]),
        ):
            self.assertEqual([n.tag for n in iterator(tree, reverse_children=reverse)], expected)
        self.assertEqual([n.tag for n in tree.find_all({"tag": ["a", "c"]}, iter_=PreOrderTreeIterator)], ["a", "c"])
        self.assertEqual((tree.start, tree.end), (0, 3))

    def test_iterative_traversal_handles_deep_trees(self):
        tree = TokenNode("leaf", 0, 1)
        for _ in range(1500):
            tree = BranchNode("branch", children=[tree])
        self.assertEqual(sum(1 for _ in PreOrderTreeIterator(tree)), 1501)
        self.assertEqual(sum(1 for _ in PostOrderTreeIterator(tree)), 1501)

    def test_nonansi_ports_are_batched_with_two_parser_calls(self):
        editor = vedit("module top(a);\ninput a;\nwire x=a;\nendmodule\n", "top")
        ports = {f"p{i}": {"direction": "input", "width": "[7:0]"} for i in range(12)}
        with patch("vutils.vparser.subprocess.run", wraps=subprocess.run) as run:
            editor.add_port(ports)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(len(editor.ports), 13)
        self.assertTrue(all(p["width"] == "[7:0]" for p in editor.ports if p["name"] != "a"))
        before = editor.module_text
        editor.add_port(ports)
        self.assertEqual(before, editor.module_text)
        self.assert_valid(before)

    def test_single_line_nonansi_declarations_remain_in_module(self):
        for source in ("module top(a); input a; endmodule",
                       "module top(a); input a; wire x=a; endmodule"):
            with self.subTest(source=source):
                editor = vedit(source, "top")
                editor.add_port({"b": {"direction": "output"}, "c": {"direction": "input"}})
                self.assert_valid(editor.module_text)
                self.assertEqual({p["name"] for p in editor.ports}, {"a", "b", "c"})
                self.assertLess(editor.module_text.index("output b;"), editor.module_text.index("endmodule"))

    def test_instance_ports_are_batched_with_two_parser_calls(self):
        editor = vedit("module top;\nsub u0(.a(a));\nendmodule", "top")
        pins = {f"p{i}": {"wire": f"w{i}"} for i in range(12)}
        with patch("vutils.vparser.subprocess.run", wraps=subprocess.run) as run:
            editor.add_inst_port({"u0": pins})
        self.assertEqual(run.call_count, 2)
        for i in range(12):
            self.assertIn(f".p{i}(w{i})", editor.module_text)
        self.assert_valid(editor.module_text)

    def test_batch_edits_multiple_parameterized_instances(self):
        source = "module top;\nsub #(.N(8)) u0(.a(a)), u1(.a(b));\nsub u2(.a(c));\nendmodule"
        editor = vedit(source, "top")
        editor.add_inst_port({"u0": {"rst": {"wire": "r0"}},
                              "u1": {"rst": {"wire": "r1"}},
                              "u2": {"rst": {"wire": "r2"}}})
        self.assert_valid(editor.module_text)
        self.assertEqual([i["name"] for i in editor.instances], ["u0", "u1", "u2"])
        self.assertIn("#(.N(8))", editor.module_text)
        for i in range(3):
            self.assertIn(f".rst(r{i})", editor.module_text)

    def test_instance_batch_conflict_rolls_back_all_edits(self):
        source = "module top;\nsub u0(.a(a));\nsub u1(.a(b));\nendmodule"
        editor = vedit(source, "top")
        with self.assertRaisesRegex(ValueError, "already connected"):
            editor.add_inst_port({"u0": {"rst": {"wire": "rst"}}, "u1": {"a": {"wire": "c"}}})
        self.assertEqual(editor.module_text, source)

    def test_invalid_added_port_rolls_back(self):
        source = "module top(input a); endmodule"
        editor = vedit(source, "top")
        with self.assertRaisesRegex(ValueError, "invalid Verilog syntax"):
            editor.add_port({"bad-name": {"direction": "input"}})
        self.assertEqual(editor.module_text, source)

    def test_partial_tree_does_not_hide_syntax_errors(self):
        editor = vedit("module top;\nwire wire [7:0] data;\nendmodule", "top")
        with self.assertRaisesRegex(ValueError, "Invalid Verilog syntax"):
            _ = editor.ports

    def test_diagnostics_only_success_has_no_errors(self):
        self.assert_valid("module top; endmodule")

    def test_diagnostics_only_failure_has_errors(self):
        data = VeribleParser().parse_string("module top; wire wire a; endmodule", {"gen_tree": False})
        self.assertTrue(data.errors)
        self.assertIn("wire", data.errors[0].message)

    def test_batch_failure_does_not_mark_valid_file_as_invalid(self):
        with tempfile.TemporaryDirectory(prefix="vutils_batch_") as temp:
            valid, invalid = Path(temp) / "a.sv", Path(temp) / "b.sv"
            valid.write_text("module a; endmodule")
            invalid.write_text("module b; wire wire a; endmodule")
            parsed = VeribleParser().parse_files([str(valid), str(invalid)], {"gen_tree": False})
            self.assertFalse(parsed[str(valid)].errors)
            self.assertTrue(parsed[str(invalid)].errors)

    def test_array_bound_identifier_is_not_port_name(self):
        editor = vedit("module top #(parameter N=3)(input wire signed [7:0] data [0:N]); endmodule", "top")
        self.assertEqual(editor.ports[0], {"name": "data", "direction": "input", "type": "wire signed",
                                          "width": "[7:0]", "dimension": "[0:N]"})

    def test_user_defined_port_type_is_preserved(self):
        editor = vedit("module top(input pkg::data_t data); endmodule", "top")
        self.assertEqual(editor.ports[0]["name"], "data")
        self.assertEqual(editor.ports[0]["type"], "pkg::data_t")

    def test_ansi_grouped_ports_inherit_direction_and_width(self):
        editor = vedit("module top(input wire [7:0] a,b, output c,d); endmodule", "top")
        self.assertTrue(editor.is_ansi)
        self.assertEqual([p["name"] for p in editor.ports], ["a", "b", "c", "d"])
        self.assertEqual([p["direction"] for p in editor.ports], ["input", "input", "output", "output"])
        self.assertEqual([p["width"] for p in editor.ports], ["[7:0]", "[7:0]", "", ""])
        editor.add_port({"e": {"direction": "input"}})
        self.assertEqual([p["name"] for p in editor.ports], ["a", "b", "c", "d", "e"])
        self.assert_valid(editor.module_text)

    def test_nonansi_arrays_have_each_declarator_dimension(self):
        editor = vedit("module top(a,b); input wire [7:0] a [0:3], b [0:7]; endmodule", "top")
        self.assertEqual([p["name"] for p in editor.ports], ["a", "b"])
        self.assertEqual([p["dimension"] for p in editor.ports], ["[0:3]", "[0:7]"])


if __name__ == "__main__":
    unittest.main()
