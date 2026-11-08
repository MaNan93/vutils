"""Native parsing, diagnostics, original byte offsets, and compatibility imports."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vutils.vparser import ParseError, TreeSitterParser, VeribleParser


class TreeSitterParserApiTests(unittest.TestCase):
    def test_native_parse_does_not_launch_an_executable(self):
        with patch('subprocess.run', side_effect=AssertionError('external process')):
            data = TreeSitterParser().parse_string('// 中文\nmodule top(input a); endmodule',
                                                   {'gen_tokens': True, 'gen_rawtokens': True})
        self.assertFalse(data.errors)
        self.assertEqual(data.tree.tag, 'source_file')
        self.assertTrue(data.tree.find({'tag': 'module_declaration'}))
        self.assertNotIn('one_line_comment', [t.tag for t in data.tokens])
        self.assertIn('one_line_comment', [t.tag for t in data.rawtokens])
        self.assertEqual([t.text for t in data.tokens if t.tag == 'simple_identifier'], ['top', 'a'])
        self.assertEqual(data.source_code, '// 中文\nmodule top(input a); endmodule'.encode())

    def test_parse_files_diagnostics_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp)/'a.sv', Path(tmp)/'b.sv'
            a.write_text('module a; endmodule')
            b.write_text('module b; assign = ; endmodule')
            out = TreeSitterParser().parse_files([str(a), str(b)], {'gen_tree': False})
        self.assertIsNone(out[str(a)].tree)
        self.assertFalse(out[str(a)].errors)
        self.assertTrue(out[str(b)].errors)
        self.assertIsInstance(out[str(b)].errors[0], ParseError)

    def test_missing_tokens_are_errors(self):
        data = TreeSitterParser().parse_string('module top(input a);', {'gen_tree': False})
        self.assertTrue(data.errors)
        self.assertTrue(any('missing' in e.message for e in data.errors))

    def test_old_import_name_uses_tree_sitter(self):
        self.assertIs(VeribleParser, TreeSitterParser)
        self.assertFalse(VeribleParser(executable='/does/not/exist').parse_string('module x; endmodule').errors)


if __name__ == '__main__':
    unittest.main()
