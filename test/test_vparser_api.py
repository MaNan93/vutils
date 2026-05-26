#!/usr/bin/env python3
"""Unit tests for vutils.vparser official-style parse API."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from vutils.vparser import ParseError, VeribleParser


class VeribleParserApiTests(unittest.TestCase):
    def _mock_result(self, payload: dict) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    @patch("vutils.vparser.subprocess.run")
    def test_parse_string_with_options(self, run_mock):
        run_mock.return_value = self._mock_result({
            "-": {
                "tree": {
                    "tag": "kSource",
                    "children": [None, {"tag": "module", "start": 0, "end": 6}],
                },
                "tokens": [{"tag": "SymbolIdentifier", "start": 7, "end": 8}],
                "rawtokens": [{"tag": "SymbolIdentifier", "start": 7, "end": 8}],
                "errors": [{"line": 1, "column": 1, "phase": "parse", "message": "x"}],
            }
        })

        parser = VeribleParser(executable="verible-verilog-syntax")
        data = parser.parse_string(
            "module X(); endmodule;",
            options={"gen_tokens": True, "gen_rawtokens": True, "skip_null": True},
        )

        self.assertIsNotNone(data)
        self.assertIsNotNone(data.tree)
        self.assertEqual(data.tree.get("tag"), "kSource")
        self.assertEqual(len(data.tree.get("children", [])), 1)
        self.assertIsNotNone(data.tokens)
        self.assertEqual(data.tokens[0]["tag"], "SymbolIdentifier")
        self.assertIsNotNone(data.rawtokens)
        self.assertEqual(data.rawtokens[0]["tag"], "SymbolIdentifier")
        self.assertIsNotNone(data.errors)
        self.assertIsInstance(data.errors[0], ParseError)
        self.assertEqual(data.errors[0].message, "x")
        self.assertEqual(data.source_code, b"module X(); endmodule;")

        call_args = run_mock.call_args.args[0]
        self.assertIn("--export_json", call_args)
        self.assertIn("--printtree", call_args)
        self.assertIn("--printtokens", call_args)
        self.assertIn("--printrawtokens", call_args)

    @patch("vutils.vparser.subprocess.run")
    def test_parse_files_returns_mapping(self, run_mock):
        run_mock.return_value = self._mock_result({
            "a.sv": {
                "tree": {"tag": "kSource", "children": []},
                "errors": [],
            },
            "b.sv": {
                "tree": {"tag": "kSource", "children": []},
                "errors": [],
            },
        })

        parser = VeribleParser(executable="verible-verilog-syntax")
        out = parser.parse_files(["a.sv", "b.sv"])

        self.assertIn("a.sv", out)
        self.assertIn("b.sv", out)
        self.assertEqual(out["a.sv"].tree["tag"], "kSource")

    @patch("vutils.vparser.subprocess.run")
    def test_legacy_tree_property_compatible(self, run_mock):
        run_mock.return_value = self._mock_result({
            "-": {
                "tree": {"tag": "kSource", "children": []},
                "errors": [],
            }
        })

        parser = VeribleParser(source="module X(); endmodule;", executable="verible-verilog-syntax")
        self.assertEqual(parser.tree.get("tag"), "kSource")
        self.assertEqual(parser.data.get("tree", {}).get("tag"), "kSource")


if __name__ == "__main__":
    unittest.main(verbosity=2)
