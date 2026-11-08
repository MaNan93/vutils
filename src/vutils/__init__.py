"""vutils - Verilog/SystemVerilog module analysis and editing tool.

Uses Tree-sitter SystemVerilog CST for precise source code navigation and editing.
CLI interface compatible with modeditor.
"""

from .vcli import main
from .vedit import read_interface, read_interface_from_file, vedit
from .vparser import TreeSitterParser, VeribleParser

__all__ = ["main", "vedit", "TreeSitterParser", "VeribleParser", "read_interface", "read_interface_from_file"]
