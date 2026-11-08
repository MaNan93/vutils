"""CLI integration tests, using the active Python on every platform."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'examples' / 'test_parser.sv'


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'vutils', *map(str, args)],
                              capture_output=True, text=True, timeout=15)

    def test_help_and_no_action(self):
        for args in [('--help',), (FIXTURE,)]:
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertIn('Tree-sitter', result.stdout)
            self.assertIn('usage:', result.stdout)

    def test_module_selection_and_port_filters(self):
        result = self.run_cli(FIXTURE, '--list-port', 'all')
        self.assertEqual(result.returncode, 2)
        self.assertIn('multiple modules', result.stdout)
        for direction, present, absent in [('input', 'clk', 'data_out'), ('output', 'data_out', 'clk')]:
            result = self.run_cli(FIXTURE, '--module', 'top', '--list-port', direction)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn(present, result.stdout)
            self.assertNotIn(absent, result.stdout)

    def test_hierarchy_aliases(self):
        for flag in ['--hier', '--hierarchy']:
            result = self.run_cli(FIXTURE, flag)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('u_adder0 (adder)', result.stdout)

    def test_instantiation_options(self):
        result = self.run_cli(FIXTURE, '--module', 'alu', '--inst', '--inst-name', 'u_test')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('u_test', result.stdout)
        self.assertIn('#(', result.stdout)
        result = self.run_cli(FIXTURE, '--module', 'alu', '--inst', '--inst-no-param', '--inst-no-decl')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(result.stdout.startswith('alu u_alu ('))
        self.assertNotIn('#(', result.stdout)

    def test_output_inplace_and_repeatable_combined_edits(self):
        original = FIXTURE.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'edited.sv'
            flags = ['--module', 'top', '--add-port', 'input, logic, probe',
                     '--add-port', 'output, logic, probe_out', '--add-wire', 'wire, [7:0], tmp_data',
                     '--add-inst-port', 'u_adder0, probe, tmp_data']
            result = self.run_cli(FIXTURE, *flags, '--output', output)
            self.assertEqual(result.returncode, 0, result.stdout)
            text = output.read_text()
            self.assertIn('input logic probe', text)
            self.assertIn('output logic probe_out', text)
            self.assertIn('wire [7:0] tmp_data;', text)
            self.assertIn('.probe(tmp_data)', text)
            result = self.run_cli(output, *flags, '--inplace')
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(output.read_text(), text)
        self.assertEqual(FIXTURE.read_bytes(), original)

    def test_file_errors_and_invalid_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp)/'missing.sv'
            result = self.run_cli(missing, '--list-port', 'all')
            self.assertEqual(result.returncode, 1)
            invalid = Path(tmp)/'bad.sv'
            invalid.write_text('module bad; assign = ; endmodule')
            result = self.run_cli(invalid, '--list-port', 'all')
            self.assertEqual(result.returncode, 2)
        for spec in ['input,', 'bad_direction,x']:
            result = self.run_cli(FIXTURE, '--module', 'top', '--add-port', spec)
            self.assertEqual(result.returncode, 3, result.stdout)

    def test_single_module_and_syntax_error_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'top.sv'
            original = b'// \xe4\xb8\xad\xe6\x96\x87\r\nmodule top(input a);\r\nendmodule\r\n'
            path.write_bytes(original)
            result = self.run_cli(path, '--list-port', 'all')
            self.assertEqual(result.returncode, 0, result.stdout)
            result = self.run_cli(path, '--add-port', 'input,bad-name', '--inplace')
            self.assertEqual(result.returncode, 3, result.stdout)
            self.assertEqual(path.read_bytes(), original)
            result = self.run_cli(path, '--add-port', 'input,b', '--inplace')
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertNotIn(b'\n', path.read_bytes().replace(b'\r\n', b''))


if __name__ == '__main__':
    unittest.main()
