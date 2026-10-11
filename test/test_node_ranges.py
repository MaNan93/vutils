"""CST boundary queries preserve traversal semantics and mutable tree behavior."""

import unittest

from vutils.vparser import (
    BranchNode, LeafNode, PostOrderTreeIterator, PreOrderTreeIterator,
    RootNode, SyntaxData, TokenNode, VeribleParser,
)


class NodeRangeTests(unittest.TestCase):
    def test_empty_branches_and_non_token_leaves(self):
        for children in ([], [LeafNode()], [BranchNode("empty")],
                         [BranchNode("outer", children=[BranchNode("inner"), LeafNode()])]):
            with self.subTest(children=children):
                branch = BranchNode("test", children=children)
                self.assertIsNone(branch.start)
                self.assertIsNone(branch.end)

    def test_empty_edges_and_nested_tokens(self):
        branch = BranchNode("test", children=[
            BranchNode("empty_left"), LeafNode(),
            BranchNode("nested", children=[BranchNode("empty"), TokenNode("a", 4, 5),
                                           TokenNode("b", 7, 9), BranchNode("empty_right")]),
            BranchNode("empty_right"),
        ])
        self.assertEqual((branch.start, branch.end), (4, 9))

    def test_boundaries_follow_child_order_not_numeric_extrema(self):
        branch = BranchNode("test", children=[TokenNode("first", 20, 30), TokenNode("last", 4, 7)])
        self.assertEqual((branch.start, branch.end), (20, 7))

    def test_mutating_children_updates_boundaries(self):
        branch = BranchNode("test", children=[TokenNode("a", 4, 5)])
        self.assertEqual((branch.start, branch.end), (4, 5))
        branch.children.insert(0, TokenNode("first", 1, 3, parent=branch))
        branch.children.append(TokenNode("last", 8, 10, parent=branch))
        self.assertEqual((branch.start, branch.end), (1, 10))
        branch.children.clear()
        self.assertIsNone(branch.start)
        self.assertIsNone(branch.end)

    def test_deep_and_wide_trees(self):
        nested = TokenNode("a", 2, 4)
        for _ in range(1500):
            nested = BranchNode("nested", children=[nested])
        self.assertEqual((nested.start, nested.end), (2, 4))
        wide = BranchNode("wide", children=[BranchNode("empty") for _ in range(3000)]
                          + [TokenNode("a", 2, 4)]
                          + [BranchNode("empty") for _ in range(3000)])
        self.assertEqual((wide.start, wide.end), (2, 4))

    def test_unicode_text_uses_byte_offsets(self):
        data = SyntaxData(source_code="中文 abc".encode("utf-8"))
        branch = BranchNode("text", children=[TokenNode("text", 0, 6)])
        root = RootNode("source", syntax_data=data, children=[branch])
        data.tree = root
        self.assertEqual(branch.text, "中文")
        self.assertEqual((branch.start, branch.end), (0, 6))

    def test_real_cst_matches_original_search_semantics(self):
        source = ("module top #(parameter W=8)(input logic signed [W-1:0] a [2], output logic b);\n"
                  "sub #(.W(W)) u0(.a(a[0]), .b(b));\n"
                  "generate if (W>1) begin: g wire [W-1:0] x=a[1]; end endgenerate\nendmodule")
        data = VeribleParser().parse_string(source)
        self.assertFalse(data.errors)
        for branch in PreOrderTreeIterator(data.tree):
            if not isinstance(branch, BranchNode):
                continue
            first = branch.find(lambda n: isinstance(n, TokenNode), iter_=PostOrderTreeIterator)
            last = branch.find(lambda n: isinstance(n, TokenNode), iter_=PostOrderTreeIterator,
                               reverse_children=True)
            self.assertEqual(branch.start, first.start if first else None)
            self.assertEqual(branch.end, last.end if last else None)


if __name__ == "__main__":
    unittest.main()
