"""Six synthetic reproductions, with original source and opaque macros intact."""
from pathlib import Path
import unittest
from vutils import TreeSitterParser, vedit
from vutils.vparser import PreOrderTreeIterator

ROOT = Path(__file__).resolve().parents[1]/'examples'/'parser_repros'


class ParserReproductionTests(unittest.TestCase):
    def test_all_six_reproductions_parse(self):
        files = sorted(ROOT.glob('*.sv'))
        self.assertEqual(len(files),6)
        for path in files:
            with self.subTest(path=path.name):
                source = path.read_text()
                result = TreeSitterParser().parse_string(source)
                self.assertFalse(result.errors)
                for node in PreOrderTreeIterator(result.tree):
                    self.assertEqual(node.text,source.encode()[node.start:node.end].decode())

    def test_opaque_range_preserves_metadata_and_edit_offsets(self):
        source = ("// 中文\nmodule repro(input [`WIDTH_RANGE] payload); "
                  "wire [7:0] q=$bits(payload)'(1); sub u0(.a(payload)); endmodule")
        ed = vedit(source,'repro')
        self.assertEqual(ed.ports[0]['width'],'[`WIDTH_RANGE]')
        self.assertEqual(ed.instances[0]['offset'],source.encode().index(b'u0'))
        ed.add_ports_and_inst_ports({'enabled':{'direction':'input'}}, {'u0':{'enabled':{'wire':'enabled'}}})
        self.assertIn('[`WIDTH_RANGE] payload',ed.module_text)
        self.assertNotIn('WIDTH_RANGE:0',ed.module_text)
        self.assertIn('.enabled(enabled)',ed.module_text)

    def test_recovery_never_accepts_invalid_plain_ranges_or_indices(self):
        for declaration in ['input [WIDTH] a','input [7:] a','input [`RANGE +] a']:
            with self.subTest(declaration=declaration):
                self.assertTrue(TreeSitterParser().parse_string('module top('+declaration+'); endmodule').errors)
        for expr in ['a[b.c +]','a[b::]','a[b.c[N-1:]]']:
            with self.subTest(expr=expr):
                self.assertTrue(TreeSitterParser().parse_string('module top; assign q='+expr+'; endmodule').errors)

    def test_macro_type_and_macro_range_are_distinct(self):
        for source in ['module top(input [`RANGE] a); endmodule',
                       'module top(input wire [`RANGE] a); endmodule',
                       'module top(input pkg::T [`RANGE] a); endmodule']:
            with self.subTest(source=source):
                ed=vedit(source,'top')
                self.assertEqual(ed.ports[0]['width'],'[`RANGE]')
                self.assertEqual(ed.ports[0]['name'],'a')


if __name__=='__main__':
    unittest.main()
