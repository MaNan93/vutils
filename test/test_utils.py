"""Exercise free-function edits on the repository's real example fixture."""
import unittest
from pathlib import Path

from vutils import vedit
from vutils.vedit import add_port, add_wire, add_inst_port
from vutils.vparser import TreeSitterParser

SOURCE = (Path(__file__).resolve().parents[1]/'examples'/'test_parser.sv').read_text()


class FreeFunctionEditingTests(unittest.TestCase):
    def test_combined_nonansi_edits_and_idempotence(self):
        parser = TreeSitterParser()
        ports = {'name': 'new_sig', 'direction': 'input', 'type': 'wire', 'width': '[3:0]'}
        wire = {'name': 'new_wire', 'type': 'wire', 'width': '[7:0]'}
        pin = {'instname': 'u_adder0', 'port': 'new_pin', 'wire': 'new_wire'}
        text = add_port(SOURCE, parser, 'top', ports)
        text = add_wire(text, parser, 'top', wire)
        text = add_inst_port(text, parser, 'top', pin)
        self.assertEqual(len(vedit(text, 'top').ports), 5)
        self.assertIn('input wire [3:0] new_sig;', text)
        self.assertIn('.new_pin(new_wire)', text)
        self.assertEqual(add_port(text, parser, 'top', ports), text)
        self.assertEqual(add_wire(text, parser, 'top', wire), text)
        self.assertEqual(add_inst_port(text, parser, 'top', pin), text)
        self.assertEqual(text[text.index('module alu'):], SOURCE[SOURCE.index('module alu'):])

    def test_ansi_edit_and_empty_module_wire(self):
        text = add_port(SOURCE, None, 'alu', {'name':'overflow', 'direction':'output', 'type':'reg', 'width':'[FULL-1:0]'})
        self.assertEqual(len(vedit(text, 'alu').ports), 6)
        text = add_wire(text, None, 'empty_mod', {'name':'new_flag'})
        self.assertIn('wire new_flag;', text)
        self.assertFalse(TreeSitterParser().parse_string(text).errors)


if __name__ == '__main__':
    unittest.main()
