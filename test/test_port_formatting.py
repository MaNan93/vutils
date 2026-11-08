"""Insertion inherits adjacent columns and separator layout without reformatting."""
import unittest
from vutils import vedit


class PortFormattingTests(unittest.TestCase):
    def test_ansi_columns_and_standalone_comma(self):
        source = ('module top (\n    input  logic [7:0] a\n    ,\n'
                  '    output logic [7:0] q // keep comment\n); endmodule')
        ed = vedit(source, 'top').add_port({'enabled':{'direction':'input','type':'logic'}})
        self.assertIn('    output logic [7:0] q // keep comment\n    ,\n    input  logic       enabled\n',ed.module_text)
        self.assertEqual(ed.module_text.count('    ,\n'),2)
        self.assertEqual([p['name']for p in ed.ports],['a','q','enabled'])

    def test_trailing_comma_and_comments(self):
        source = ('module top (\n    input  logic [7:0] a,\n'
                  '    output logic [7:0] q // keep\n); endmodule')
        text = vedit(source,'top').add_port({'enabled':{'direction':'input','type':'logic'}}).module_text
        self.assertIn('q, // keep\n    input  logic       enabled\n',text)

    def test_leading_comma_spacing(self):
        source = 'module top (\n  input logic a\n  ,output logic q\n); endmodule'
        text = vedit(source,'top').add_port({'enabled':{'direction':'input','type':'logic'}}).module_text
        self.assertIn('  ,input  logic enabled\n',text)
        self.assertIn('  ,output logic q\n',text)

    def test_batches_nonansi_declarations_align_and_keep_indent(self):
        source = ('module top(a,q);\n    input  logic [7:0] a;\n'
                  '    output logic [7:0] q;\nendmodule')
        ed = vedit(source,'top').add_port({'enabled':{'direction':'input','type':'logic'},
                                         'wide':{'direction':'output','type':'logic','width':'[15:0]'}})
        self.assertIn('    input  logic       enabled;\n    output logic [15:0] wide;\n',ed.module_text)
        self.assertEqual(len(ed.ports),4)

    def test_instance_separator_ignores_nested_commas(self):
        source='module top; sub u0(\n .a(f(x,y))\n ,\n .b(b)\n); endmodule'
        ed=vedit(source,'top').add_inst_ports('u0',{'c':'c'})
        self.assertIn(' .b(b)\n ,\n .c(c)\n',ed.module_text)

    def test_crlf_and_long_fields_do_not_reformat_original_lines(self):
        source='module top (\r\n\tinput  logic a,\r\n\toutput logic q\r\n); endmodule'
        text=vedit(source,'top').add_port({'long_signal':{'direction':'input','type':'my_really_long_type'}}).module_text
        self.assertIn('\tinput  logic a,\r\n\toutput logic q,\r\n',text)
        self.assertIn('\tinput  my_really_long_type long_signal\r\n',text)
        self.assertNotIn('\n',text.replace('\r\n',''))


if __name__ == '__main__':
    unittest.main()
