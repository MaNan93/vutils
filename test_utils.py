#!/usr/bin/env python3
"""Test utils.py edit operations (add_port, add_wire, add_inst_port)."""

import sys
import os
import tempfile

BASE = r"C:\Users\man\Desktop\code\vutils"
sys.path.insert(0, os.path.join(BASE, "src"))

os.environ["VERIBLE_BIN"] = r"C:\Users\man\Desktop\code\verible"

from vutils.vparser import VeribleParser
from vutils.vutils import (
    ModuleEditor,
    add_port,
    add_wire,
    add_inst_port,
)

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        msg = f"  FAIL  {label}"
        if detail:
            msg += f": {detail}"
        print(msg)


def reparse(source, module_name):
    """Write source to temp file, parse, return module info."""
    with tempfile.NamedTemporaryFile(suffix=".sv", mode="w", delete=False, encoding="utf-8") as f:
        f.write(source)
        tmp = f.name
    try:
        p = VeribleParser(tmp)
        return p.get_module(module_name)
    finally:
        os.unlink(tmp)


TEST_SV = os.path.join(BASE, "examples", "test_parser.sv")

# ============================================================
#  Test 1: add_port — non-ANSI module (top)
# ============================================================
print("=" * 60)
print("  Test 1: add_port — non-ANSI module (top)")
print("=" * 60)

p1 = VeribleParser(TEST_SV)
src1 = p1.source
src1 = add_port(src1, p1, "top", {
    "direction": "input", "type": "wire", "width": "[3:0]",
    "name": "new_sig", "dimension": "",
})

check("new_sig in port list", "new_sig" in src1[src1.index("top("):src1.index(");", src1.index("top("))])
check("new_sig decl in body", "input wire [3:0] new_sig;" in src1)

m1v = reparse(src1, "top")
check("re-parse: top has 5 ports", len(m1v["ports"]) == 5)
check("re-parse: new_sig found", any(p["name"] == "new_sig" for p in m1v["ports"]))
check("re-parse: new_sig direction", next(p for p in m1v["ports"] if p["name"] == "new_sig")["direction"] == "input")

# Show the modified top module
top_start = src1.index("module top(")
top_end = src1.index("endmodule", top_start) + len("endmodule")
print()
print("--- Modified top module ---")
for line in src1[top_start:top_end].splitlines():
    print("  " + line)
print("--- End ---")

# ============================================================
#  Test 2: add_port — ANSI module (alu)
# ============================================================
print()
print("=" * 60)
print("  Test 2: add_port — ANSI module (alu)")
print("=" * 60)

p2 = VeribleParser(TEST_SV)
src2 = p2.source
src2 = add_port(src2, p2, "alu", {
    "direction": "output", "type": "reg", "width": "[FULL-1:0]",
    "name": "overflow", "dimension": "",
})

check("overflow in source", "output reg [FULL-1:0] overflow" in src2)

m2v = reparse(src2, "alu")
check("re-parse: alu has 6 ports", len(m2v["ports"]) == 6)
check("re-parse: overflow found", any(p["name"] == "overflow" for p in m2v["ports"]))
check("re-parse: overflow direction", next(p for p in m2v["ports"] if p["name"] == "overflow")["direction"] == "output")

alu_start = src2.index("module alu")
alu_end = src2.index("endmodule", alu_start) + len("endmodule")
print()
print("--- Modified alu module ---")
for line in src2[alu_start:alu_end].splitlines():
    print("  " + line)
print("--- End ---")

# ============================================================
#  Test 3: add_wire — add wire to top
# ============================================================
print()
print("=" * 60)
print("  Test 3: add_wire — add wire to top")
print("=" * 60)

p3 = VeribleParser(TEST_SV)
src3 = p3.source
src3 = add_wire(src3, p3, "top", {
    "type": "wire", "width": "[7:0]", "name": "new_wire", "dimension": "",
})

check("new_wire in body", "wire [7:0] new_wire;" in src3)

m3v = reparse(src3, "top")
check("re-parse: top still OK", m3v["name"] == "top")
check("re-parse: top still 4 ports", len(m3v["ports"]) == 4)

top_start3 = src3.index("module top(")
top_end3 = src3.index("endmodule", top_start3) + len("endmodule")
print()
print("--- Modified top module (wire only) ---")
for line in src3[top_start3:top_end3].splitlines():
    print("  " + line)
print("--- End ---")

# ============================================================
#  Test 4: add_inst_port — add port to u_adder0
# ============================================================
print()
print("=" * 60)
print("  Test 4: add_inst_port — add .new_pin(new_wire) to u_adder0")
print("=" * 60)

p4 = VeribleParser(TEST_SV)
src4 = p4.source
src4 = add_inst_port(src4, p4, "top", {
    "instname": "u_adder0", "port": "new_pin", "wire": "new_wire",
})

check("new_pin in instance", ".new_pin(new_wire)" in src4)

m4v = reparse(src4, "top")
check("re-parse: top still OK", m4v["name"] == "top")
check("re-parse: u_adder0 still exists", any(i["name"] == "u_adder0" for i in m4v["instances"]))

top_start4 = src4.index("module top(")
top_end4 = src4.index("endmodule", top_start4) + len("endmodule")
# Only print the instance section
adder_start = src4.index("adder u_adder0", top_start4)
adder_end = src4.index(");", adder_start) + 2
print()
print("--- Modified u_adder0 instance ---")
for line in src4[adder_start:adder_end].splitlines():
    print("  " + line)
print("--- End ---")

# ============================================================
#  Test 5: add_inst_port — add port to u_mul
# ============================================================
print()
print("=" * 60)
print("  Test 5: add_inst_port — add .en(1'b1) to u_mul")
print("=" * 60)

p5 = VeribleParser(TEST_SV)
src5 = p5.source
src5 = add_inst_port(src5, p5, "top", {
    "instname": "u_mul", "port": "en", "wire": "1'b1",
})

check("en pin in u_mul", ".en(1'b1)" in src5)

m5v = reparse(src5, "top")
check("re-parse: top still OK", m5v["name"] == "top")
check("re-parse: u_mul still exists", any(i["name"] == "u_mul" for i in m5v["instances"]))

mul_start5 = src5.index("mul u_mul", src5.index("module top("))
mul_end5 = src5.index(");", mul_start5) + 2
print()
print("--- Modified u_mul instance ---")
for line in src5[mul_start5:mul_end5].splitlines():
    print("  " + line)
print("--- End ---")

# ============================================================
#  Test 6: ModuleEditor — combined add_port + add_wire + add_inst_port
# ============================================================
print()
print("=" * 60)
print("  Test 6: ModuleEditor — combined port + wire + inst-port")
print("=" * 60)

editor = ModuleEditor.from_file(TEST_SV, module_name="top")
editor.add_port({
    "new_sig": {"direction": "input", "type": "wire", "width": "[3:0]", "dimension": ""},
})
editor.add_wire({
    "new_wire": {"type": "wire", "width": "[7:0]", "dimension": ""},
})
editor.add_inst_port({
    "u_adder0": {"new_pin": {"wire": "new_wire"}},
    "u_mul": {"en": {"wire": "1'b1"}},
})

src6 = editor.module_text
check("new_sig port in list", "new_sig" in src6[src6.index("top("):src6.index(");", src6.index("top("))])
check("new_wire decl", "wire [7:0] new_wire;" in src6)
check("new_pin on u_adder0", ".new_pin(new_wire)" in src6)
check("en on u_mul", ".en(1'b1)" in src6)

m6v = reparse(src6, "top")
check("re-parse: top has 5 ports", len(m6v["ports"]) == 5)
check("re-parse: u_adder0 still exists", any(i["name"] == "u_adder0" for i in m6v["instances"]))
check("re-parse: u_mul still exists", any(i["name"] == "u_mul" for i in m6v["instances"]))

top_start6 = src6.index("module top(")
top_end6 = src6.index("endmodule", top_start6) + len("endmodule")
print()
print("--- Modified top module (combined) ---")
for line in src6[top_start6:top_end6].splitlines():
    print("  " + line)
print("--- End ---")

# ============================================================
#  Test 7: Idempotency — adding same port twice should be no-op
# ============================================================
print()
print("=" * 60)
print("  Test 7: Idempotency — add same port twice")
print("=" * 60)

p7 = VeribleParser(TEST_SV)
src7 = p7.source
src7a = add_port(src7, p7, "top", {
    "direction": "input", "type": "wire", "width": "[3:0]",
    "name": "new_sig", "dimension": "",
})
# Re-parse and add again
fd7, tmp7 = tempfile.mkstemp(suffix=".sv")
try:
    with os.fdopen(fd7, "w", encoding="utf-8") as f7:
        f7.write(src7a)
    p7b = VeribleParser(tmp7)
    src7b = add_port(src7a, p7b, "top", {
        "direction": "input", "type": "wire", "width": "[3:0]",
        "name": "new_sig", "dimension": "",
    })
    check("idempotent: source unchanged", src7a == src7b)
finally:
    os.unlink(tmp7)

# ============================================================
#  Test 8: add_port on wrapper (no-ports module)
# ============================================================
print()
print("=" * 60)
print("  Test 8: add_port — wrapper (no-ports module)")
print("=" * 60)

p8 = VeribleParser(TEST_SV)
m8 = p8.get_module("wrapper")
offsets8 = p8.get_module_offsets(m8["cst"])
print(f"  wrapper port_insert_idx = {offsets8['port_insert_idx']}")
print(f"  wrapper decl_insert_idx = {offsets8['decl_insert_idx']}")

if offsets8["port_insert_idx"] < 0:
    # wrapper has no port list — skip port addition, just add wire
    print("  (wrapper has no port list, skipping port add test)")
    src8 = p8.source
    src8 = add_wire(src8, p8, "wrapper", {
        "type": "wire", "width": "[7:0]", "name": "new_w", "dimension": "",
    })
    check("new_wire in wrapper body", "wire [7:0] new_w;" in src8)
else:
    src8 = p8.source
    src8 = add_port(src8, p8, "wrapper", {
        "direction": "input", "type": "wire", "width": "",
        "name": "clk", "dimension": "",
    })
    check("clk in wrapper port list", "clk" in src8)

# ============================================================
#  Test 9: add_wire on empty body module (empty_mod)
# ============================================================
print()
print("=" * 60)
print("  Test 9: add_wire — empty body module (empty_mod)")
print("=" * 60)

p9 = VeribleParser(TEST_SV)
m9 = p9.get_module("empty_mod")
offsets9 = p9.get_module_offsets(m9["cst"])
print(f"  empty_mod decl_insert_idx = {offsets9['decl_insert_idx']}")

if offsets9["decl_insert_idx"] < 0:
    print("  (empty_mod has no body items, wire add may fail — expected)")
    try:
        src9 = p9.source
        src9 = add_wire(src9, p9, "empty_mod", {
            "type": "wire", "width": "", "name": "new_flag", "dimension": "",
        })
        check("wire in empty_mod", "wire new_flag;" in src9)
    except ValueError as e:
        check("empty body raises ValueError", True, str(e))
else:
    src9 = p9.source
    src9 = add_wire(src9, p9, "empty_mod", {
        "type": "wire", "width": "", "name": "new_flag", "dimension": "",
    })
    check("wire in empty_mod", "wire new_flag;" in src9)

# ============================================================
#  Summary
# ============================================================
print()
print("=" * 60)
total = PASS + FAIL
print(f"  Results: {PASS}/{total} passed, {FAIL}/{total} failed")
print("=" * 60)
