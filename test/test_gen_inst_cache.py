"""Template generation must share the editor's source-version cache."""

import importlib
import subprocess
import unittest
from unittest.mock import patch

from vutils.vedit import vedit

edit_module = importlib.import_module("vutils.vedit")
SOURCE = "module top #(parameter W=8)(input clk, input [W-1:0] a); sub u0(.clk(clk)); endmodule"


class InstantiationCacheTests(unittest.TestCase):
    def test_template_and_metadata_reads_share_one_parse(self):
        editor = vedit(SOURCE, "top")
        with patch("vutils.vparser.subprocess.run", wraps=subprocess.run) as run, \
             patch("vutils.vedit._extract_instances", wraps=edit_module._extract_instances) as extract:
            template = editor.gen_inst(instance_name="u_top", port_map={"a": "payload"})
            self.assertIn("top #(", template)
            self.assertIn(".W(W)", template)
            self.assertRegex(template, r"\.a\s*\(payload\s*\)")
            plain = editor.gen_inst(include_params=False, instance_name="u_plain")
            self.assertTrue(plain.startswith("top u_plain ("))
            self.assertNotIn(".W(", plain)
            self.assertEqual([p["name"] for p in editor.ports], ["clk", "a"])
            self.assertEqual(editor.param_names, ["W"])
            self.assertEqual(run.call_count, 1)
            self.assertEqual(extract.call_count, 0)
            self.assertEqual(editor.instances[0]["name"], "u0")
            self.assertEqual(run.call_count, 1)
            self.assertEqual(extract.call_count, 1)

    def test_source_edit_invalidates_template_metadata(self):
        editor = vedit(SOURCE, "top")
        before = editor.gen_inst()
        editor.add_port({"rst_n": {"direction": "input"}})
        with patch("vutils.vparser.subprocess.run", wraps=subprocess.run) as run:
            after = editor.gen_inst()
            self.assertIn(".rst_n(rst_n)", after)
            self.assertNotIn("rst_n", before)
            self.assertEqual(editor.gen_inst(), after)
            self.assertEqual([p["name"] for p in editor.ports], ["clk", "a", "rst_n"])
            self.assertEqual(run.call_count, 1)

    def test_nonansi_named_module_in_multi_module_source(self):
        source = "module other(input wrong); endmodule\nmodule top(a); input [7:0] a; endmodule"
        editor = vedit(source, "top")
        with patch("vutils.vparser.subprocess.run", wraps=subprocess.run) as run:
            template = editor.gen_inst(instance_name="u_selected")
            self.assertTrue(template.startswith("top u_selected ("))
            self.assertRegex(template, r"\.a\s*\(a\s*\)")
            self.assertNotIn("wrong", template)
            self.assertEqual(editor.ports[0]["width"], "[7:0]")
            self.assertFalse(editor.is_ansi)
            self.assertEqual(editor.gen_inst(instance_name="u_selected"), template)
            self.assertEqual(run.call_count, 1)

    def test_invalid_source_does_not_populate_cache(self):
        editor = vedit("module top(input); endmodule", "top")
        with self.assertRaises(ValueError):
            editor.gen_inst()
        self.assertIsNone(editor._mi_cache)


if __name__ == "__main__":
    unittest.main()
