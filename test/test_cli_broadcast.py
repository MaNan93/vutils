"""Broadcast CLI selectors edit one selected module without recursion."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vutils.vcli import main
from vutils.vedit import vedit


class BroadcastTests(unittest.TestCase):
    def invoke(self, path, *args):
        output = io.StringIO()
        with patch('sys.argv', ['vutils', str(path), *args]), contextlib.redirect_stdout(output):
            code = main()
        return code, output.getvalue()

    def test_all_instances_generate_array_and_module_selection(self):
        source = '''module top(input clk);
sub first(.clk(clk));
sub array_inst[1:0](.clk(clk));
if (1) begin : g1
  sub same(.clk(clk));
end
if (1) begin : g2
  sub same(.clk(clk));
end
endmodule
module sub(input clk); leaf untouched(.clk(clk)); endmodule
'''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'top.sv'
            path.write_text(source)
            flags = ('--module', 'top', '--add-port', 'input,logic,a', '--add-inst-port', '*,a,a')
            code, diff = self.invoke(path, *flags, '--diff')
            self.assertEqual(code, 0, diff)
            self.assertEqual(diff.count('.a(a)'), 4)
            self.assertEqual(path.read_text(), source)
            code, text = self.invoke(path, *flags, '--inplace')
            self.assertEqual(code, 0, text)
            result = path.read_text()
            self.assertEqual(result.count('.a(a)'), 4)
            self.assertIn('module sub(input clk); leaf untouched(.clk(clk)); endmodule', result)
            self.assertIn('a', {p['name'] for p in vedit(result, 'top').ports})
            code, diff = self.invoke(path, *flags, '--diff', '--check')
            self.assertEqual((code, diff), (0, ''))

    def test_conflict_and_empty_selection_do_not_write(self):
        for source in ('module top(input a,b); sub u0(.a(a)); sub u1(.a(b)); endmodule',
                       'module top(input a); endmodule'):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'top.sv'
                path.write_text(source)
                code, text = self.invoke(path, '--add-inst-port', '*,a,a', '--inplace')
                self.assertEqual(code, 3, text)
                self.assertEqual(path.read_text(), source)

    def test_repeat_specs_and_explicit_selector(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'top.sv'
            path.write_text('module top(input a,b); sub u0(); sub u1(); endmodule')
            code, text = self.invoke(path, '--add-inst-port', '*,a,a',
                                     '--add-inst-port', 'u0,a,a', '--add-inst-port', '*,b,b')
            self.assertEqual(code, 0, text)
            self.assertEqual(text.count('.a(a)'), 2)
            self.assertEqual(text.count('.b(b)'), 2)
