"""vutils - Verilog/SystemVerilog module analysis and editing tool.

Uses verible-verilog-syntax CST for precise source code navigation and editing.
CLI interface compatible with modeditor.
"""

from .vcli import main
from .vedit import vedit
from .vparser import VeribleParser

__all__ = ["main", "vedit", "VeribleParser"]
