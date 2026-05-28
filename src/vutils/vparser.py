"""Official-style wrapper for verible-verilog-syntax JSON output.

This module focuses on parser data access patterns similar to Verible's
Python examples:
- parse_string / parse_file / parse_files
- syntax tree navigation via Node APIs
- optional token and raw token export
"""

from __future__ import annotations

import collections
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, TypedDict, Union


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


class InstanceInfo(TypedDict, total=False):
    """Instance declaration information."""
    name: str
    type: str
    offset: int
    port_close_idx: int


class ModuleInfo(TypedDict, total=False):
    """Module analysis information."""
    name: str
    is_ansi: bool
    cst: Any
    ports: list[PortInfo]
    param_names: list[str]
    instances: list[InstanceInfo]


def _find_verible_binary() -> str:
    """Locate verible-verilog-syntax binary.

    Search order:
    1) VERIBLE_BIN (path to file or containing directory)
    2) PATH
    3) nearby 'verible' directory (walk up from this file)
    """
    name = "verible-verilog-syntax"
    if env := os.environ.get("VERIBLE_BIN", ""):
        p = Path(env)
        if p.is_file():
            return str(p)
        for ext in ("", ".exe"):
            candidate = p / (name + ext)
            if candidate.is_file():
                return str(candidate)

    if found := shutil.which(name):
        return found

    here = Path(__file__).resolve().parent
    for _ in range(6):
        for ext in ("", ".exe"):
            candidate = here / "verible" / (name + ext)
            if candidate.is_file():
                return str(candidate)
        here = here.parent

    return name


VERIBLE = _find_verible_binary()


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
        children = list(getattr(node, "children", []))
        if self.reverse_children:
            children.reverse()
        return children

    def _iter_tree(self, node: Optional["Node"]) -> Iterable["Node"]:
        raise NotImplementedError


class PreOrderTreeIterator(_TreeIteratorBase):
    def _iter_tree(self, node: Optional["Node"]) -> Iterable["Node"]:
        if node is None:
            return
        if self.filter_(node):
            yield node
        for child in self._children(node):
            yield from self._iter_tree(child)


class PostOrderTreeIterator(_TreeIteratorBase):
    def _iter_tree(self, node: Optional["Node"]) -> Iterable["Node"]:
        if node is None:
            return
        for child in self._children(node):
            yield from self._iter_tree(child)
        if self.filter_(node):
            yield node


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
        token = self.find(lambda n: isinstance(n, TokenNode), iter_=PostOrderTreeIterator)
        return token.start if token is not None else None

    @property
    def end(self) -> Optional[int]:
        token = self.find(
            lambda n: isinstance(n, TokenNode),
            iter_=PostOrderTreeIterator,
            reverse_children=True,
        )
        return token.end if token is not None else None

    def _normalize_filter(
        self,
        filter_: Union[CallableFilter, KeyValueFilter, None],
    ) -> CallableFilter:
        if filter_ is None:
            return lambda _: True
        if callable(filter_):
            return filter_

        def as_list(value: Union[str, list[str]]) -> list[str]:
            return value if isinstance(value, list) else [value]

        def _f(node: Node) -> bool:
            for attr, value in filter_.items():
                if not hasattr(node, attr):
                    return False
                if getattr(node, attr) not in as_list(value):
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
    tree: Optional[RootNode] = None
    tokens: Optional[list[Token]] = None
    rawtokens: Optional[list[Token]] = None
    errors: Optional[list[ParseError]] = None


def _remove_null_children(tree: Any) -> Any:
    if isinstance(tree, list):
        return [_remove_null_children(t) for t in tree if t is not None]
    if isinstance(tree, dict):
        out: dict[str, Any] = {}
        for k, v in tree.items():
            if k == "children" and isinstance(v, list):
                out[k] = [_remove_null_children(t) for t in v if t is not None]
            else:
                out[k] = _remove_null_children(v)
        return out
    return tree


class VeribleVerilogSyntax:
    """Wrapper around verible-verilog-syntax --export_json."""

    def __init__(self, executable: str = VERIBLE):
        self.executable = executable

    @staticmethod
    def _options(user_options: Optional[dict[str, Any]]) -> dict[str, Any]:
        return {
            "gen_tree": True,
            "skip_null": False,
            "gen_tokens": False,
            "gen_rawtokens": False,
            **(user_options or {}),
        }

    def _build_args(self, options: dict[str, Any], paths: list[str]) -> list[str]:
        args = [self.executable, "--export_json"]
        if options["gen_tree"]:
            args.append("--printtree")
        if options["gen_tokens"]:
            args.append("--printtokens")
        if options["gen_rawtokens"]:
            args.append("--printrawtokens")
        args.extend(paths)
        return args

    @staticmethod
    def _transform_tree(tree_json: Any, data: SyntaxData, skip_null: bool) -> Optional[RootNode]:
        if not isinstance(tree_json, dict) or "children" not in tree_json:
            return None

        tree = _remove_null_children(tree_json) if skip_null else tree_json

        def make_node(node_json: Any, parent: Optional[Node] = None) -> Optional[Node]:
            if node_json is None:
                return None
            if not isinstance(node_json, dict):
                return None

            if "children" in node_json:
                branch = BranchNode(tag=str(node_json.get("tag", "")), parent=parent, children=[])
                for child_json in node_json.get("children", []):
                    child = make_node(child_json, parent=branch)
                    if child is not None:
                        branch.children.append(child)
                return branch

            if "start" in node_json and "end" in node_json:
                return TokenNode(
                    tag=str(node_json.get("tag", "")),
                    start=int(node_json["start"]),
                    end=int(node_json["end"]),
                    parent=parent,
                )

            return None

        root = RootNode(tag=str(tree.get("tag", "")), syntax_data=data, children=[])
        for child_json in tree.get("children", []):
            child = make_node(child_json, parent=root)
            if child is not None:
                root.children.append(child)
        return root

    @staticmethod
    def _transform_tokens(tokens_json: Any, data: SyntaxData) -> list[Token]:
        if not isinstance(tokens_json, list):
            return []
        out: list[Token] = []
        for t in tokens_json:
            if not isinstance(t, dict):
                continue
            if "tag" not in t or "start" not in t or "end" not in t:
                continue
            out.append(Token(str(t["tag"]), int(t["start"]), int(t["end"]), data))
        return out

    @staticmethod
    def _transform_errors(errors_json: Any) -> list[ParseError]:
        if not isinstance(errors_json, list):
            return []
        out: list[ParseError] = []
        for e in errors_json:
            if not isinstance(e, dict):
                continue
            out.append(
                ParseError(
                    line=int(e.get("line", 0) or 0),
                    column=int(e.get("column", 0) or 0),
                    phase=str(e.get("phase", "") or ""),
                    message=str(e.get("message", "") or ""),
                )
            )
        return out

    def _load_source_bytes(self, file_key: str, stdin_input: Optional[str]) -> Optional[bytes]:
        if file_key == "-":
            return stdin_input.encode("utf-8") if stdin_input is not None else None
        try:
            return Path(file_key).read_bytes()
        except OSError:
            return None

    def _parse(
        self,
        paths: list[str],
        *,
        input_: Optional[str] = None,
        options: Optional[dict[str, Any]] = None,
    ) -> dict[str, SyntaxData]:
        opts = self._options(options)
        args = self._build_args(opts, paths)
        input_bytes = input_.encode("utf-8") if input_ is not None else None
        proc = subprocess.run(
            args,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            check=False,
        )

        # Decode output with proper error handling for diagnostics
        try:
            stdout_text = (proc.stdout or b"").decode("utf-8")
        except UnicodeDecodeError as e:
            stderr_text = (proc.stderr or b"").decode("utf-8", errors="replace")
            raise ValueError(
                f"Verible output contains invalid UTF-8 at byte {e.start}: {e.reason}\n"
                f"stderr: {stderr_text}\n"
                f"binary: {self.executable}"
            )

        try:
            stderr_text = (proc.stderr or b"").decode("utf-8")
        except UnicodeDecodeError:
            stderr_text = (proc.stderr or b"").decode("utf-8", errors="replace")

        if not stdout_text.strip():
            hint = stderr_text.strip() or "(no stderr)"
            raise ValueError(
                f"Verible produced no output (exit code {proc.returncode}).\n"
                f"stderr: {hint}\n"
                f"binary: {self.executable}"
            )

        try:
            json_data = json.loads(stdout_text)
        except json.JSONDecodeError as e:
            msg = stderr_text.strip() or stdout_text[:200]
            raise ValueError(
                f"Verible produced invalid JSON: {e.msg} at line {e.lineno}, column {e.colno}\n"
                f"diagnostic: {msg}"
            )

        if not isinstance(json_data, dict):
            raise ValueError(
                f"Verible produced unexpected JSON structure: expected dict, got {type(json_data).__name__}"
            )

        results: dict[str, SyntaxData] = {}
        for file_key, payload in json_data.items():
            data = SyntaxData(source_code=self._load_source_bytes(file_key, input_))
            if isinstance(payload, dict):
                if opts["gen_tree"]:
                    data.tree = self._transform_tree(payload.get("tree"), data, opts["skip_null"])
                if opts["gen_tokens"]:
                    data.tokens = self._transform_tokens(payload.get("tokens"), data)
                if opts["gen_rawtokens"]:
                    data.rawtokens = self._transform_tokens(payload.get("rawtokens"), data)
                data.errors = self._transform_errors(payload.get("errors", []))
            else:
                data.errors = [ParseError(line=0, column=0, phase="parse", message="null result")]
            results[str(file_key)] = data

        return results

    def parse_files(
        self,
        paths: list[str],
        options: Optional[dict[str, Any]] = None,
    ) -> dict[str, SyntaxData]:
        return self._parse(paths, options=options)

    def parse_file(
        self,
        path: str,
        options: Optional[dict[str, Any]] = None,
    ) -> Optional[SyntaxData]:
        return self._parse([path], options=options).get(path)

    def parse_string(
        self,
        string: str,
        options: Optional[dict[str, Any]] = None,
    ) -> Optional[SyntaxData]:
        return self._parse(["-"], input_=string, options=options).get("-")


class VeribleParser(VeribleVerilogSyntax):
    """Alias kept for local naming convenience in this repository."""


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
    if isinstance(node, TokenNode):
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
