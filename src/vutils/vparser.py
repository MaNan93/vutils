"""In-process Tree-sitter SystemVerilog parsing and CST navigation."""

from __future__ import annotations

import collections
from bisect import bisect_left
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, TypedDict, Union

from tree_sitter import Language, Parser, Query, QueryCursor
import tree_sitter_systemverilog


COMMON_TYPES = frozenset({
    "wire", "reg", "logic", "bit", "tri", "supply0", "supply1",
    "triand", "trior", "trireg", "uwire", "real", "realtime",
    "integer", "time", "signed", "unsigned",
})


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

INVALID_POSITION = 999999
INVALID_RANGE = (999999, -1)


# ─────────────────────────────────────────────────────────────────────────────
# Type Definitions
# ─────────────────────────────────────────────────────────────────────────────


class PortInfo(TypedDict, total=False):
    """Port declaration information."""
    name: str
    direction: str
    type: str
    width: str
    dimension: str
    interface: bool
    interface_type: str
    modport: str


class InstanceInfo(TypedDict, total=False):
    """Instance declaration information."""
    name: str
    type: str
    offset: int
    port_close_idx: int
    path: str


class ModuleInfo(TypedDict, total=False):
    """Module analysis information."""
    name: str
    is_ansi: bool
    cst: Any
    ports: list[PortInfo]
    param_names: list[str]
    instances: list[InstanceInfo]
    imports: list[str]


CallableFilter = Callable[["Node"], bool]
KeyValueFilter = dict[str, Union[str, list[str]]]


class _TreeIteratorBase:
    def __init__(
        self,
        tree: "Node",
        filter_: Optional[CallableFilter] = None,
        reverse_children: bool = False,
    ) -> None:
        self.tree = tree
        self.filter_ = filter_ if filter_ is not None else (lambda _: True)
        self.reverse_children = reverse_children

    def __iter__(self) -> Iterable["Node"]:
        yield from self._iter_tree(self.tree)

    def _children(self, node: Optional["Node"]) -> Iterable["Node"]:
        if node is None:
            return []
        children = getattr(node, "children", ())
        return reversed(children) if self.reverse_children else children

    def _iter_tree(self, node: Optional["Node"]) -> Iterable["Node"]:
        raise NotImplementedError


class PreOrderTreeIterator(_TreeIteratorBase):
    def _iter_tree(self, node: Optional["Node"]) -> Iterable["Node"]:
        if node is None:
            return
        stack = [node]
        while stack:
            current = stack.pop()
            if self.filter_(current):
                yield current
            children = getattr(current, "children", ())
            stack.extend(children if self.reverse_children else reversed(children))


class PostOrderTreeIterator(_TreeIteratorBase):
    def _iter_tree(self, node: Optional["Node"]) -> Iterable["Node"]:
        if node is None:
            return
        stack = [(node, False)]
        while stack:
            current, visited = stack.pop()
            if visited:
                if self.filter_(current):
                    yield current
            else:
                stack.append((current, True))
                children = getattr(current, "children", ())
                stack.extend((child, False) for child in
                             (children if self.reverse_children else reversed(children)))


class LevelOrderTreeIterator(_TreeIteratorBase):
    def _iter_tree(self, node: Optional["Node"]) -> Iterable["Node"]:
        if node is None:
            return
        queue: collections.deque[Node] = collections.deque([node])
        while queue:
            current = queue.popleft()
            if self.filter_(current):
                yield current
            queue.extend(self._children(current))


class Node:
    """Base syntax tree node."""

    def __init__(self, parent: Optional["Node"] = None) -> None:
        self.parent = parent

    @property
    def syntax_data(self) -> Optional["SyntaxData"]:
        p = self.parent
        while p is not None and not isinstance(p, RootNode):
            if hasattr(p, "_data"):
                return p._data
            p = p.parent
        return p.syntax_data if isinstance(p, RootNode) else None

    @property
    def start(self) -> Optional[int]:
        raise NotImplementedError

    @property
    def end(self) -> Optional[int]:
        raise NotImplementedError

    @property
    def text(self) -> str:
        sd = self.syntax_data
        if sd is None or sd.source_code is None:
            return ""
        s = self.start
        e = self.end
        if s is None or e is None or e > len(sd.source_code) or s < 0 or e < s:
            return ""
        return sd.source_code[s:e].decode("utf-8", errors="ignore")


class LeafNode(Node):
    @property
    def start(self) -> None:
        return None

    @property
    def end(self) -> None:
        return None


class TokenNode(LeafNode):
    def __init__(
        self,
        tag: str,
        start: int,
        end: int,
        parent: Optional[Node] = None,
    ) -> None:
        super().__init__(parent)
        self.tag = tag
        self._start = start
        self._end = end

    @property
    def start(self) -> int:
        return self._start

    @property
    def end(self) -> int:
        return self._end


class BranchNode(Node):
    def __init__(
        self,
        tag: str,
        parent: Optional[Node] = None,
        children: Optional[list[Node]] = None,
    ) -> None:
        super().__init__(parent)
        self.tag = tag
        self.children: list[Node] = children if children is not None else []
        for child in self.children:
            child.parent = self

    @property
    def start(self) -> Optional[int]:
        token = self._boundary_token(reverse=False)
        return token.start if token is not None else None

    @property
    def end(self) -> Optional[int]:
        token = self._boundary_token(reverse=True)
        return token.end if token is not None else None

    def _boundary_token(self, reverse: bool) -> Optional[TokenNode]:
        """Find an edge token lazily, without general search/filter machinery.

        Descend in child order and skip empty branches. Iterator stacks avoid
        recursion and allocating a stack entry for every sibling in wide nodes.
        Recompute each time because callers can modify the tree's children.
        """
        stack = [iter(reversed(self.children) if reverse else self.children)]
        exhausted = object()
        while stack:
            child = next(stack[-1], exhausted)
            if child is exhausted:
                stack.pop()
            elif isinstance(child, TokenNode):
                return child
            else:
                children = getattr(child, "children", ())
                if children:
                    stack.append(iter(reversed(children) if reverse else children))
        return None

    def _normalize_filter(
        self,
        filter_: Union[CallableFilter, KeyValueFilter, None],
    ) -> CallableFilter:
        if filter_ is None:
            return lambda _: True
        if callable(filter_):
            return filter_

        missing = object()
        checks = tuple((attr, tuple(value) if isinstance(value, list) else (value,))
                       for attr, value in filter_.items())

        def _f(node: Node) -> bool:
            for attr, values in checks:
                if getattr(node, attr, missing) not in values:
                    return False
            return True

        return _f

    def iter_find_all(
        self,
        filter_: Union[CallableFilter, KeyValueFilter, None],
        max_count: int = 0,
        iter_: type[_TreeIteratorBase] = LevelOrderTreeIterator,
        **kwargs: Any,
    ) -> Iterable[Node]:
        predicate = self._normalize_filter(filter_)
        count = max_count
        for node in iter_(self, predicate, **kwargs):
            yield node
            if count > 0:
                count -= 1
                if count == 0:
                    break

    def find(
        self,
        filter_: Union[CallableFilter, KeyValueFilter, None],
        iter_: type[_TreeIteratorBase] = LevelOrderTreeIterator,
        **kwargs: Any,
    ) -> Optional[Node]:
        return next(self.iter_find_all(filter_, max_count=1, iter_=iter_, **kwargs), None)

    def find_all(
        self,
        filter_: Union[CallableFilter, KeyValueFilter, None],
        max_count: int = 0,
        iter_: type[_TreeIteratorBase] = LevelOrderTreeIterator,
        **kwargs: Any,
    ) -> list[Node]:
        return list(self.iter_find_all(filter_, max_count=max_count, iter_=iter_, **kwargs))


class RootNode(BranchNode):
    def __init__(
        self,
        tag: str,
        syntax_data: Optional["SyntaxData"] = None,
        children: Optional[list[Node]] = None,
    ) -> None:
        super().__init__(tag=tag, parent=None, children=children)
        self._syntax_data = syntax_data

    @property
    def syntax_data(self) -> Optional["SyntaxData"]:
        return self._syntax_data


@dataclass
class Token:
    tag: str
    start: int
    end: int
    syntax_data: Optional["SyntaxData"] = None

    @property
    def text(self) -> str:
        sd = self.syntax_data
        if sd is None or sd.source_code is None or self.end > len(sd.source_code):
            return ""
        return sd.source_code[self.start:self.end].decode("utf-8", errors="ignore")


@dataclass
class ParseError:
    line: int
    column: int
    phase: str
    message: str = ""


@dataclass
class SyntaxData:
    source_code: Optional[bytes] = None
    tree: Optional[BranchNode] = None
    tokens: Optional[list[Token]] = None
    rawtokens: Optional[list[Token]] = None
    errors: Optional[list[ParseError]] = None
    inserted_bytes: tuple[int, ...] = ()


_LANGUAGE = Language(tree_sitter_systemverilog.language())
_MODULE_QUERY = Query(_LANGUAGE, "(module_declaration) @module")


def module_nodes(tree):
    """Capture module definitions without materializing their body wrappers."""
    nodes = QueryCursor(_MODULE_QUERY).captures(tree.native).get("module", [])
    return [SyntaxNode(node, tree.syntax_data, tree) for node in sorted(nodes, key=lambda n: n.start_byte)]

# A parsing view, never the text written back to disk. Replace the backtick of
# opaque macro identifiers with an identifier character; hide conditional
# directive lines so list entries remain visible in the concrete syntax tree.
# Every replacement preserves byte length and newlines. This does not expand
# macros, choose branches, resolve includes, or perform elaboration.
_SOURCE_PART = re.compile(
    rb'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\[\s\S]|[^"\\])*"|\\[^\s]+'
    rb'|`(?:define|include|undef|timescale|default_nettype|resetall|celldefine|endcelldefine|'
    rb'begin_keywords|end_keywords|unconnected_drive|nounconnected_drive|line|pragma)\b'
    rb'(?:[^\n]*\\\r?\n)*[^\n]*'
    rb'|`(?:ifdef|ifndef|elsif|else|endif)\b[^\r\n]*'
    rb'|`[A-Za-z_][A-Za-z0-9_$]*')
_CONDITIONAL = re.compile(rb'`(?:ifdef|ifndef|elsif|else|endif)\b')
_MACRO_APOSTROPHE = re.compile(rb"\s*'")
_MACRO_TYPE = re.compile(rb"\s*#\s*\(|\s+[A-Za-z_][A-Za-z0-9_$]*\s*(?:[(),;\[])")


def _parsing_view(source: bytes) -> bytes:
    def replace(match):
        token = match.group()
        if _CONDITIONAL.match(token):
            return bytes(c if c in (10, 13) else 32 for c in token)
        if token.startswith(b'`') and not re.match(
            rb'`(?:define|include|undef|timescale|default_nettype|resetall|celldefine|endcelldefine|'
            rb'begin_keywords|end_keywords|unconnected_drive|nounconnected_drive|line|pragma)\b', token
        ):
            # The grammar accepts standalone macro statements and expressions.
            # Only identifier-shaped type / module references need adaptation.
            preceding = match.start() - 1
            while preceding >= 0 and source[preceding] in b" \t\r\n":
                preceding -= 1
            if (preceding >= 0 and source[preceding] in b"#|&~^[") or _MACRO_APOSTROPHE.match(source, match.end()):
                return b'_' + token[1:]
            if _MACRO_TYPE.match(source, match.end()):
                return b'_' + token[1:]
            return token
        return token
    return _SOURCE_PART.sub(replace, source)


_CAST_TOKEN = re.compile(
    rb'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\[\s\S]|[^"\\])*"|\\[^\s]+'
    rb'|\$[A-Za-z_][A-Za-z0-9_$]*|[()]|[^\s]')


_CAST_FOLLOW = re.compile(rb"\s*'\s*\(")


def _parenthesize_system_casts(source):
    """Work around the grammar's rejection of $bits(x)'(...) sized casts.

    Parenthesizing the size expression is semantically neutral. Keep a sparse
    map for the added bytes, so edits and diagnostics still use original offsets.
    No expression is replaced or discarded, including the argument expressions.
    """
    stack, insertions = [], []
    previous = None
    for token in _CAST_TOKEN.finditer(source):
        text = token.group()
        if text.startswith((b"//", b"/*")):
            continue
        if text == b"(":
            stack.append(previous if previous and previous.group().startswith(b"$") else None)
        elif text == b")" and stack:
            function = stack.pop()
            if function is not None and _CAST_FOLLOW.match(source, token.end()):
                insertions.extend(((function.start(), b"("), (token.end(), b")")))
        previous = token
    if not insertions:
        return source, ()
    parts, mapped, cursor = [], [], 0
    for idx, (offset, char) in enumerate(sorted(insertions)):
        parts.extend((source[cursor:offset], char))
        mapped.append(offset + idx)
        cursor = offset
    parts.append(source[cursor:])
    return b"".join(parts), tuple(mapped)


def _original_offset(data, position):
    return position - bisect_left(data.inserted_bytes, position)


def _parse_source(source: bytes):
    """One in-process native parse (also the instrumentation point for tests)."""
    view, inserted = _parenthesize_system_casts(_parsing_view(source))
    return Parser(_LANGUAGE).parse(view), inserted


class SyntaxNode(BranchNode):
    """Lazy Tree-sitter node adapter with original-text byte ranges.

    ``tag`` is the native grammar node type. Child wrappers are built only on
    access, so interface reads do not allocate Python objects for module bodies.
    ``native`` exposes the underlying tree-sitter Node, including field access.
    """

    def __init__(self, native, data, parent=None):
        Node.__init__(self, parent)
        self.native = native
        self.tag = native.type
        self._data = data
        self._children = None

    @property
    def syntax_data(self):
        return self._data

    @property
    def start(self):
        return _original_offset(self._data, self.native.start_byte)

    @property
    def end(self):
        return _original_offset(self._data, self.native.end_byte)

    @property
    def children(self):
        if self._children is None:
            self._children = [_wrap_node(child, self._data, self) for child in self.native.children]
        return self._children

    def field(self, name):
        native = self.native.child_by_field_name(name)
        if native is None:
            return None
        return next((child for child in self.children if child.native == native), None)


def _wrap_node(native, data, parent=None):
    if native.child_count:
        return SyntaxNode(native, data, parent)
    token = TokenNode(native.type, _original_offset(data, native.start_byte),
                      _original_offset(data, native.end_byte), parent)
    token.native = native
    return token


class TreeSitterParser:
    """SystemVerilog CST parser; no external executable is needed.

    Errors and missing tokens are always collected, including diagnostics-only
    calls. Recovery trees are available to callers but editors reject errors.
    Offsets are UTF-8 byte offsets into the original, unmodified source.
    """

    def __init__(self, executable=None):
        # Accepted only for compatibility with the former Verible wrapper.
        # No executable is located or launched.
        pass

    def parse_string(self, string, options=None):
        return self._parse_bytes(string.encode("utf-8"), options)

    def _parse_bytes(self, source, options=None):
        opts = {"gen_tree": True, "gen_tokens": False, "gen_rawtokens": False, **(options or {})}
        tree, inserted = _parse_source(source)
        data = SyntaxData(source_code=source, errors=[], inserted_bytes=inserted)
        stack = [tree.root_node]
        tokens = []
        while stack:
            node = stack.pop()
            if node.is_error or node.is_missing:
                start, end = _original_offset(data, node.start_byte), _original_offset(data, node.end_byte)
                text = source[start:end].decode("utf-8", errors="replace")
                message = f"missing {node.type}" if node.is_missing else f"unexpected syntax {text[:100]!r}"
                line = source.count(b"\n", 0, start)
                column = start - source.rfind(b"\n", 0, start) - 1
                data.errors.append(ParseError(line, column, "parse", message))
                # Report the recovery span once; do not duplicate nested errors.
                if node.is_error and not (opts["gen_tokens"] or opts["gen_rawtokens"]):
                    continue
            if node.child_count:
                if node.has_error or opts["gen_tokens"] or opts["gen_rawtokens"]:
                    stack.extend(reversed(node.children))
            elif opts["gen_tokens"] or opts["gen_rawtokens"]:
                start, end = _original_offset(data, node.start_byte), _original_offset(data, node.end_byte)
                if end > start:
                    tokens.append(Token(node.type, start, end, data))
        if opts["gen_tree"]:
            data.tree = SyntaxNode(tree.root_node, data)
        if opts["gen_tokens"]:
            data.tokens = [t for t in tokens if t.tag not in {"one_line_comment", "block_comment"}]
        if opts["gen_rawtokens"]:
            data.rawtokens = tokens
        return data

    def parse_file(self, path, options=None):
        return self._parse_bytes(Path(path).read_bytes(), options)

    def parse_files(self, paths, options=None):
        return {str(path): self.parse_file(path, options) for path in paths}


# Import compatibility only: CST tags now follow tree-sitter-systemverilog.
VeribleParser = TreeSitterParser
VeribleVerilogSyntax = TreeSitterParser


def _iter_children(node: Any) -> Iterable[Any]:
    if isinstance(node, dict):
        return node.get("children", [])
    if isinstance(node, BranchNode):
        return node.children
    if isinstance(node, RootNode):
        return node.children
    return []


def _node_tag(node: Any) -> str:
    if isinstance(node, dict):
        return str(node.get("tag", ""))
    if isinstance(node, (BranchNode, TokenNode, RootNode)):
        return str(getattr(node, "tag", ""))
    return ""


def find_all(node: Any, tag: str) -> list[Any]:
    """Find all descendant nodes with a specific tag."""
    out: list[Any] = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if _node_tag(cur) == tag:
            out.append(cur)
        children = list(_iter_children(cur))
        children.reverse()
        stack.extend(children)
    return out


def find_child(node: Any, tag: str) -> Optional[Any]:
    """Find first direct child with a specific tag."""
    for child in _iter_children(node):
        if _node_tag(child) == tag:
            return child
    return None


def find_children(node: Any, tag: str) -> list[Any]:
    """Find all direct children with a specific tag."""
    return [c for c in _iter_children(node) if _node_tag(c) == tag]


def find_descendant(node: Any, tag: str) -> Optional[Any]:
    """Find first descendant with a specific tag."""
    stack = [node]
    while stack:
        cur = stack.pop()
        if _node_tag(cur) == tag:
            return cur
        children = list(_iter_children(cur))
        children.reverse()
        stack.extend(children)
    return None


def _node_span(node: Any) -> Optional[tuple[int, int]]:
    if isinstance(node, dict) and "start" in node and "end" in node:
        return (int(node["start"]), int(node["end"]))
    if isinstance(node, (TokenNode, SyntaxNode)):
        return (node.start, node.end)
    return None


def get_node_range(node: Any) -> tuple[int, int]:
    """Get (min_start, max_end) from all token leaves in subtree."""
    starts: list[int] = []
    ends: list[int] = []
    stack = [node]
    while stack:
        cur = stack.pop()
        span = _node_span(cur)
        if span is not None:
            starts.append(span[0])
            ends.append(span[1])
            continue
        children = list(_iter_children(cur))
        children.reverse()
        stack.extend(children)
    if not starts:
        return INVALID_RANGE
    return (min(starts), max(ends))


def get_text(node: Any, source: str) -> str:
    """Get source text for a CST node.

    For dict leaves with explicit 'text', return it.
    Otherwise use node span against source.
    """
    if isinstance(node, dict) and "text" in node:
        return str(node["text"])
    if isinstance(node, (Node, Token)):
        text = getattr(node, "text", "")
        if text:
            return text
    s, e = get_node_range(node)
    if s < INVALID_POSITION and e >= s and e <= len(source):
        return source[s:e]
    return ""
