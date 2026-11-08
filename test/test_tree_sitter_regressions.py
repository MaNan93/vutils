"""Behavioral regressions for the Tree-sitter backend migration."""
import unittest

from vutils import TreeSitterParser, read_interface, vedit
from vutils.vedit import add_inst_port, gen_sig_decl
from vutils.vparser import PreOrderTreeIterator


class TreeSitterMigrationTests(unittest.TestCase):
    def test_instance_array_uses_connection_parentheses(self):
        source = 'module top; sub u0 [((N)-1):0](.a(f(x))); endmodule'
        for edit in [lambda: vedit(source, 'top').add_inst_ports('u0', {'b': 'b'}).module_text,
                     lambda: add_inst_port(source, None, 'top', {'instname':'u0', 'port':'b', 'wire':'b'})]:
            result = edit()
            self.assertIn('u0 [((N)-1):0](.a(f(x)),', result)
            self.assertIn('.b(b)', result)
            self.assertFalse(TreeSitterParser().parse_string(result).errors)

    def test_ambiguous_instance_requires_scope_and_rolls_back(self):
        source = ('module top; generate if (N) begin:left sub u0(.a(a)); end '
                  'else begin:right sub u0(.a(a)); end endgenerate endmodule')
        ed = vedit(source, 'top')
        self.assertEqual([i['path'] for i in ed.instances], ['left.u0', 'right.u0'])
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            ed.add_inst_ports('u0', {'b':'b'})
        self.assertEqual(ed.module_text, source)
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            add_inst_port(source, None, 'top', {'instname':'u0', 'port':'b', 'wire':'b'})
        ed.add_inst_ports('right.u0', {'b':'b'})
        self.assertNotIn('.b(b)', ed.module_text.split('else')[0])
        self.assertIn('.b(b)', ed.module_text.split('else')[1])
        first = ed.module_text
        ed.add_inst_ports('right.u0', {'b':'b'})
        self.assertEqual(ed.module_text, first)

    def test_signal_declarations_preserve_types_signedness_and_imports(self):
        source = ('module top import pkg::packet_t; (input packet_t p, input integer count, '
                  'output logic signed [7:0] q [2], input signed [3:0] a); endmodule')
        decl = gen_sig_decl(read_interface(source))
        self.assertIn('import pkg::packet_t;', decl)
        self.assertRegex(decl, r'packet_t\s+p;')
        self.assertRegex(decl, r'integer\s+count;')
        self.assertRegex(decl, r'logic signed\s+\[7:0\]\s+q \[2\];')
        self.assertRegex(decl, r'logic signed\s+\[3:0\]\s+a;')

    def test_cast_adaptation_preserves_offsets_and_original_source(self):
        source = ('// 中文\nmodule top(input a);\n'
                  "wire [7:0] x = $bits(a)'(a); wire [7:0] y = $clog2($bits(x))'(1);\n"
                  'sub u0(.a(x)); endmodule')
        data = TreeSitterParser().parse_string(source)
        self.assertFalse(data.errors)
        for node in PreOrderTreeIterator(data.tree):
            self.assertEqual(node.text, source.encode()[node.start:node.end].decode())
        ed = vedit(source, 'top')
        self.assertEqual(source.encode().index(b'u0'), ed.instances[0]['offset'])
        ed.add_ports_and_inst_ports({'b':{'direction':'input'}}, {'u0':{'b':{'wire':'b'}}})
        self.assertIn("$bits(a)'(a)", ed.module_text)
        self.assertIn("$clog2($bits(x))'(1)", ed.module_text)
        self.assertIn('.b(b)', ed.module_text)

    def test_diagnostics_after_cast_use_original_positions(self):
        source = "module top; wire x=$bits(a)'(1); assign = ; endmodule"
        data = TreeSitterParser().parse_string(source, {'gen_tree':False})
        self.assertTrue(data.errors)
        self.assertEqual(data.errors[0].line, 0)
        self.assertGreaterEqual(data.errors[0].column, source.index('assign'))
        self.assertLessEqual(data.errors[0].column, source.index('; endmodule'))

    def test_macros_remain_opaque_and_source_is_preserved(self):
        source = ('`define BODY(x) assign q=x;\n'
                  'module top(input a, output q);\n`BODY(a)\n'
                  "always @(a) x <= #`TP '0;\n"
                  '`SUB #(.W(`W)) u0(.a(a));\nendmodule')
        ed = vedit(source, 'top')
        self.assertEqual(ed.instances[0]['type'], '`SUB')
        ed.add_inst_ports('u0', {'q':'q'})
        self.assertIn('`BODY(a)', ed.module_text)
        self.assertIn("#`TP '0", ed.module_text)
        self.assertIn('`SUB #(.W(`W))', ed.module_text)

    def test_macro_directives_inside_comments_strings_are_not_rewritten(self):
        source = ('// `ifdef FAKE\nmodule top(input a);\n'
                  'string s="`ifdef STILL_TEXT"; /* `endif */\n'
                  'sub u0(.a(a)); endmodule')
        ed = vedit(source, 'top').add_inst_ports('u0', {'b':'b'})
        self.assertIn('"`ifdef STILL_TEXT"', ed.module_text)
        self.assertIn('/* `endif */', ed.module_text)

    def test_leading_localparam_is_not_an_override_parameter(self):
        ed = vedit('module top #(parameter W=8,D=4, localparam N=2, parameter type T=logic,U=bit)(); endmodule', 'top')
        self.assertEqual(ed.param_names, ['W','D','T','U'])

    def test_interface_modports_generate_interface_instances(self):
        info = read_interface('module top(bus.master a [2], b, interface.slave c); endmodule')
        self.assertTrue(all(p.get('interface') for p in info['ports']))
        self.assertEqual([p['direction'] for p in info['ports']], ['', '', ''])
        self.assertEqual(info['ports'][0]['modport'], 'master')
        decl = gen_sig_decl(info)
        self.assertIn('bus a [2] ();', decl)
        self.assertIn('bus b ();', decl)
        self.assertIn('Connect c to an existing interface instance', decl)
        self.assertFalse(TreeSitterParser().parse_string('module example; ' + decl + '\nendmodule').errors)


if __name__ == '__main__':
    unittest.main()
