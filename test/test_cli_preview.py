"""Preview exit codes, no-write guarantees, and single-parse CLI reads."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vutils.vcli import main
from vutils.vparser import _parse_source


class CliPreviewTests(unittest.TestCase):
    def invoke(self, args):
        output = io.StringIO()
        with patch('sys.argv', ['vutils', *map(str, args)]), contextlib.redirect_stdout(output):
            result = main()
        return result, output.getvalue()

    def test_preview_check_and_noop_do_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'top.sv'
            source = b'module top(input a); endmodule'
            path.write_bytes(source)
            modified = path.stat().st_mtime_ns
            rc, diff = self.invoke([path, '--add-port', 'input,b', '--diff'])
            self.assertEqual(rc, 0)
            self.assertIn('--- '+str(path), diff)
            self.assertIn('+input b', diff)
            self.assertIn('\\ No newline at end of file', diff)
            rc, _ = self.invoke([path, '--add-port', 'input,b', '--check'])
            self.assertEqual(rc, 1)
            rc, diff = self.invoke([path, '--add-port', 'input,b', '--diff', '--check'])
            self.assertEqual(rc, 1)
            self.assertIn('@@', diff)
            for mode in [['--check'], ['--diff'], ['--diff','--check']]:
                rc, text = self.invoke([path, '--add-port', 'input,a', *mode])
                self.assertEqual(rc, 0)
                if '--diff' in mode:
                    self.assertEqual(text, '')
            self.assertEqual(path.read_bytes(), source)
            self.assertEqual(path.stat().st_mtime_ns, modified)

    def test_preview_argument_conflicts(self):
        for flags in [['--diff'], ['--check'], ['--diff','--inplace','--add-port','input,b'],
                      ['--check','--output','/tmp/unused.sv','--add-port','input,b'],
                      ['--check','--inplace','--add-port','input,b']]:
            with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    self.invoke(['missing.sv',*flags])
                self.assertEqual(error.exception.code,2)

    def test_cli_reads_parse_once_and_edit_only_adds_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'top.sv'
            path.write_text('module top(input a); sub u0(.a(a)); endmodule\nmodule other; endmodule')
            for flags, expected in [(['--module','top','--list-port','all'],1),
                                    (['--module','top','--inst'],1), (['--hier'],1),
                                    (['--module','top','--add-port','input,b','--diff'],2)]:
                with self.subTest(flags=flags), patch('vutils.vparser._parse_source',wraps=_parse_source) as parse:
                    rc, _ = self.invoke([path,*flags])
                    self.assertEqual(rc,0)
                    self.assertEqual(parse.call_count,expected)

    def test_invalid_edit_is_an_error_even_with_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'top.sv'
            path.write_text('module top(input a); endmodule')
            rc, text = self.invoke([path, '--add-port', 'input,bad-name', '--check','--diff'])
            self.assertEqual(rc,3)
            self.assertIn('invalid Verilog syntax',text)


if __name__ == '__main__':
    unittest.main()
