#!/usr/bin/env python3
"""Comprehensive functional test for vutils CLI.

Covers every CLI argument/option with concrete examples.
Uses test.sv (memory + adder modules) as the multi-module fixture,
and generates a single-module fixture for tests that need it.

Usage:
    python test_cli_all.py          # run all tests
    python test_cli_all.py --verbose # show command output
"""

import os
import subprocess
import sys
import tempfile
import shutil

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PYTHON = r"C:\Users\man\.local\bin\python3.12.exe"
VMOD = r"C:\Users\man\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none\Scripts\vutils.exe"
TEST_SV = os.path.join(os.path.dirname(__file__), "examples", "test_parser.sv")
VERIBLE_BIN = r"C:\Users\man\Desktop\code\verible"

VERBOSE = "--verbose" in sys.argv

PASS = 0
FAIL = 0


def env():
    """Return environment with VERIBLE_BIN set."""
    e = os.environ.copy()
    e["VERIBLE_BIN"] = VERIBLE_BIN
    return e


# ---------------------------------------------------------------------------
# Single-module fixture (memory only, for ops that require single module)
# ---------------------------------------------------------------------------

SINGLE_MOD_CONTENT = """module single_dut (
    input  wire clk,
    input  wire rst_n,
    input  wire [7:0] data_in,
    output reg  [7:0] data_out,
    output wire valid
);
    wire [7:0] tmp;

    always @(posedge clk) begin
        if (!rst_n)
            data_out <= 8'h0;
        else
            data_out <= data_in;
    end

    assign valid = (data_out != 8'h0);

endmodule
"""


def make_single_mod_fixture() -> str:
    """Create a temp file with a single module, return its path."""
    fd, path = tempfile.mkstemp(suffix=".sv", prefix="single_mod_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(SINGLE_MOD_CONTENT)
        return path
    except Exception:
        os.close(fd)
        raise


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def run_test(name: str, args: list[str], expect_rc: int = 0,
             expect_in_output: str | None = None,
             expect_not_in_output: str | None = None,
             fixture: str | None = None) -> bool:
    """Run a single CLI test case and report PASS/FAIL."""
    global PASS, FAIL
    sv_file = fixture or TEST_SV
    cmd = [VMOD, *args, sv_file]
    label = f"  {name}"
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=10, env=env())
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  FAIL {label}  -> {e}")
        FAIL += 1
        return False

    ok = True
    if result.returncode != expect_rc:
        print(f"  FAIL {label}  -> rc={result.returncode}, expected {expect_rc}")
        if VERBOSE:
            print(f"       stdout: {result.stdout[:300]}")
            print(f"       stderr: {result.stderr[:200]}")
        ok = False
    elif expect_in_output and expect_in_output not in result.stdout:
        print(f"  FAIL {label}  -> '{expect_in_output}' not in stdout")
        if VERBOSE:
            print(f"       stdout: {result.stdout[:300]}")
        ok = False
    elif expect_not_in_output and expect_not_in_output in result.stdout:
        print(f"  FAIL {label}  -> '{expect_not_in_output}' unexpectedly in stdout")
        if VERBOSE:
            print(f"       stdout: {result.stdout[:300]}")
        ok = False

    if ok:
        print(f"  PASS  {label}")
        PASS += 1
        if VERBOSE and result.stdout.strip():
            for line in result.stdout.strip().split("\n")[:12]:
                print(f"       {line}")
    else:
        FAIL += 1

    return ok


def run_edit_test(name: str, args: list[str], expect_rc: int = 0,
                  expect_in_output: str | None = None,
                  fixture: str | None = None) -> bool:
    """Run an edit test on a temporary copy of fixture (non-destructive)."""
    global PASS, FAIL
    src = fixture or TEST_SV
    with tempfile.NamedTemporaryFile(suffix=".sv", mode="w", delete=False,
                                      encoding="utf-8", prefix="vmedit_") as tmp:
        shutil.copy2(src, tmp.name)
        tmp_path = tmp.name

    try:
        cmd = [VMOD, *args, tmp_path]
        label = f"  {name}"
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=10, env=env())

        ok = True
        if result.returncode != expect_rc:
            print(f"  FAIL {label}  -> rc={result.returncode}, expected {expect_rc}")
            if VERBOSE:
                print(f"       stderr: {result.stderr[:200]}")
            ok = False
        elif expect_in_output:
            if "--inplace" in args:
                with open(tmp_path, "r", encoding="utf-8") as f:
                    content = f.read()
                if expect_in_output not in content:
                    print(f"  FAIL {label}  -> '{expect_in_output}' not in file")
                    if VERBOSE:
                        print(f"       file tail: ...{content[-400:]}")
                    ok = False
            else:
                if expect_in_output not in result.stdout:
                    print(f"  FAIL {label}  -> '{expect_in_output}' not in stdout")
                    if VERBOSE:
                        print(f"       stdout: {result.stdout[:400]}")
                    ok = False

        if ok:
            print(f"  PASS  {label}")
            PASS += 1
            if VERBOSE and result.stdout.strip() and "--inplace" not in args:
                for line in result.stdout.strip().split("\n")[:12]:
                    print(f"       {line}")
            elif ok and VERBOSE and "--inplace" in args:
                with open(tmp_path, "r", encoding="utf-8") as f:
                    content = f.read()
                for line in content.strip().split("\n")[-6:]:
                    print(f"       {line}")
        else:
            FAIL += 1

        return ok
    finally:
        os.unlink(tmp_path)


# ===========================================================================
# Test Suite
# ===========================================================================

def main():
    global PASS, FAIL

    # Create single-module fixture (cleanup at end)
    singleFixture = make_single_mod_fixture()

    print("=" * 72)
    print("  vutils CLI - Comprehensive Functional Test Suite")
    print("=" * 72)
    print(f"\n  Python:            {sys.executable}")
    print(f"  VMOD binary:       {VMOD}  (exists: {os.path.exists(VMOD)})")
    print(f"  VERIBLE_BIN:       {VERIBLE_BIN}  (exists: {os.path.isdir(VERIBLE_BIN)})")
    print(f"  Multi-mod fixture: {TEST_SV}  (exists: {os.path.exists(TEST_SV)})")
    print(f"  Single-mod fixture: {os.path.basename(singleFixture)}")

    # Quick smoke test: run --help and show rc + stderr if fail
    smoke = subprocess.run([VMOD, "--help"], capture_output=True, text=True,
                           timeout=5, env=env())
    if smoke.returncode != 0:
        print(f"\n  *** SMOKE TEST FAILED (rc={smoke.returncode}) ***")
        print(f"      stdout: [{smoke.stdout[:200]}]")
        print(f"      stderr: [{smoke.stderr[:400]}]")
        print(f"  Check: Is VMOD path correct? Does vutils.exe work from shell?")
        print()
    else:
        print("  Smoke test (--help): OK\n")

    # ==================================================================
    # Group 1: Help & basic flags
    # ==================================================================
    print("--- Group 1: Help & Basic Flags ---")

    run_test("1.1  --help",
             ["--help"], expect_rc=0, expect_in_output="usage:")

    run_test("1.2  no action flags (shows help)",
             [],
             expect_rc=0, expect_in_output="usage:")

    # ==================================================================
    # Group 2: --list-port (5 filter modes + direction filtering)
    # ==================================================================
    print("\n--- Group 2: --list-port (direction filters) ---")

    # Multi-module file requires --module
    run_test("2.1  --list-port all (multi-mod, no --module => error)",
             ["--list-port"],
             expect_rc=2, expect_in_output="multiple modules")

    run_test("2.2  --list-port all on memory",
             ["--module", "memory", "--list-port"],
             expect_rc=0, expect_in_output="address")

    run_test("2.3  --list-port input on memory",
             ["--module", "memory", "--list-port", "input"],
             expect_rc=0,
             expect_in_output="address",
             expect_not_in_output="data_out")

    run_test("2.4  --list-port output on memory",
             ["--module", "memory", "--list-port", "output"],
             expect_rc=0,
             expect_in_output="data_out",
             expect_not_in_output="address")

    run_test("2.5  --list-port all on adder (has param ports)",
             ["--module", "adder", "--list-port"],
             expect_rc=0, expect_in_output="s")

    # Single-module file: no --module needed
    run_test("2.6  --list-port all (single-mod, no --module)",
             ["--list-port"], expect_rc=0, expect_in_output="data_in",
             fixture=singleFixture)

    run_test("2.7  --list-port inout (single-mod, no match => empty)",
             ["--list-port", "inout"], expect_rc=0,
             fixture=singleFixture)

    # ==================================================================
    # Group 4: --hier / --hierarchy
    # ==================================================================
    print("\n--- Group 4: --hierarchy tree ---")

    run_test("4.1  --hier (multi-module file)",
             ["--hier"],
             expect_rc=0, expect_in_output=". memory")

    run_test("4.2  --hier shows adder sub-tree too",
             ["--hier"],
             expect_rc=0, expect_in_output=". adder")

    run_test("4.3  --hier (single-module)",
             ["--hier"], expect_rc=0, expect_in_output=". single_dut",
             fixture=singleFixture)

    # ==================================================================
    # Group 5: --inst-module (instantiation template generation)
    # ==================================================================
    print("\n--- Group 5: --inst-module (template gen) ---")

    # Multi-module needs --module
    run_test("5.1  --inst-module without --module (error)",
             ["--inst-module"],
             expect_rc=4, expect_in_output="multiple modules")

    run_test("5.2  --inst-module memory",
             ["--module", "memory", "--inst-module"],
             expect_rc=0, expect_in_output="memory")

    run_test("5.3  --inst-module adder (with param WIDTH=...)",
             ["--module", "adder", "--inst-module"],
             expect_rc=0, expect_in_output="WIDTH")

    run_test("5.4  --inst-module + --inst-name u_mem",
             ["--module", "memory", "--inst-module", "--inst-name", "u_mem"],
             expect_rc=0, expect_in_output="u_mem")

    run_test("5.5  --inst-module + --inst-no-param (ports only)",
             ["--module", "adder", "--inst-module", "--inst-no-param"],
             expect_rc=0, expect_in_output=".a(")

    run_test("5.6  --inst-module (single-mod, no --module needed)",
             ["--inst-module"],
             expect_rc=0, expect_in_output="single_dut",
             fixture=singleFixture)

    # ==================================================================
    # Group 6: --add-port
    # ==================================================================
    print("\n--- Group 6: --add-port ---")

    run_edit_test("6.1  add input port 'logic [7:0] new_addr'",
                  ["--module", "memory",
                   "--add-port", "input, logic [7:0], new_addr"],
                  expect_rc=0, expect_in_output="new_addr")

    run_edit_test("6.2  add output port 'reg [15:0] data_wide'",
                  ["--module", "memory",
                   "--add-port", "output, reg [15:0], data_wide"],
                  expect_rc=0, expect_in_output="data_wide")

    run_edit_test("6.3  add port on single-mod (no --module needed)",
                  ["--add-port", "input, logic, enable"],
                  expect_rc=0, expect_in_output="enable",
                  fixture=singleFixture)

    run_edit_test("6.4  add ref port (SystemVerilog style)",
                  ["--add-port", "ref, logic [31:0], bus_data"],
                  expect_rc=0, expect_in_output="bus_data",
                  fixture=singleFixture)

    # ==================================================================
    # Group 7: --add-wire
    # ==================================================================
    print("\n--- Group 7: --add-wire ---")

    run_edit_test("7.1  add wire 'wire [31:0] internal_bus'",
                  ["--module", "memory",
                   "--add-wire", "wire [31:0], internal_bus"],
                  expect_rc=0, expect_in_output="internal_bus")

    run_edit_test("7.2  add wire 'logic simple_flag'",
                  ["--module", "memory",
                   "--add-wire", "logic, simple_flag"],
                  expect_rc=0, expect_in_output="simple_flag")

    run_edit_test("7.3  add wire on single-mod (no --module needed)",
                  ["--add-wire", "wire [15:0] wide_tmp"],
                  expect_rc=0, expect_in_output="wide_tmp",
                  fixture=singleFixture)

    # ==================================================================
    # Group 8: --add-inst-port
    # ==================================================================
    print("\n--- Group 8: --add-inst-port ---")

    run_edit_test("8.1  add inst port '.new_sig()' to a0",
                  ["--module", "memory",
                   "--add-inst-port", "a0, new_sig"],
                  expect_rc=0, expect_in_output=".new_sig(")

    run_edit_test("8.2  add inst port '.extra_pin(extra_wire)' to a0",
                  ["--module", "memory",
                   "--add-inst-port", "a0, extra_pin, extra_wire"],
                  expect_rc=0, expect_in_output=".extra_pin(extra_wire)")

    # ==================================================================
    # Group 9: Chained edit operations
    # ==================================================================
    print("\n--- Group 10: Combined Edit Operations ---")

    run_edit_test(
        "10.1 add port + add wire + add inst-port (chained)",
        [
            "--module", "memory",
            "--add-port", "input, logic, valid",
            "--add-wire", "wire [7:0], tmp_data",
            "--add-inst-port", "a0, a, addr_a",
        ],
        expect_rc=0, expect_in_output="valid")

    # ==================================================================
    # Group 10: Output modes (--output, --inplace)
    # ==================================================================
    print("\n--- Group 10: Output Modes ---")

    # --output writes to another file
    out_path = None
    try:
        fd, out_path = tempfile.mkstemp(suffix="_out.sv", prefix="vmout_")
        os.close(fd)

        cmd = [VMOD,
               "--module", "memory",
               "--add-port", "input, logic, test_out_pin",
               "--output", out_path, TEST_SV]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=10, env=env())
        label = "10.1  --output <file>"
        if result.returncode == 0:
            with open(out_path, "r", encoding="utf-8") as f:
                content = f.read()
            if "test_out_pin" in content:
                print(f"  PASS  {label}")
                PASS += 1
            else:
                print(f"  FAIL {label}  -> string not in output file")
                FAIL += 1
            if VERBOSE:
                for line in content.split("\n")[-6:]:
                    print(f"       {line}")
        else:
            print(f"  FAIL {label}  -> rc={result.returncode}")
            FAIL += 1
    finally:
        if out_path and os.path.exists(out_path):
            os.unlink(out_path)

    # --inplace edit
    run_edit_test("10.2  --inplace write-back",
                  ["--module", "memory",
                   "--add-port", "input, logic, inplace_pin", "--inplace"],
                  expect_rc=0, expect_in_output="inplace_pin")

    # --output original file should NOT be modified
    try:
        fd, out_path2 = tempfile.mkstemp(suffix="_orig.sv", prefix="vmchk_")
        os.close(fd)
        shutil.copy2(TEST_SV, out_path2)
        cmd = [VMOD,
               "--module", "memory",
               "--add-port", "input, logic, should_not_appear",
               "--output", out_path2, TEST_SV]
        subprocess.run(cmd, capture_output=True, timeout=10, env=env())
        label = "10.3  original unchanged after --output"
        with open(TEST_SV, "r", encoding="utf-8") as f:
            orig_content = f.read()
        if "should_not_appear" not in orig_content:
            print(f"  PASS  {label}")
            PASS += 1
        else:
            print(f"  FAIL {label}  -> original file was modified!")
            FAIL += 1
    finally:
        if out_path2 and os.path.exists(out_path2):
            os.unlink(out_path2)

    # ==================================================================
    # Group 12: Error handling & edge cases
    # ==================================================================
    print("\n--- Group 11: Edge Cases & Error Handling ---")

    # Non-existent file: argparse error (rc=2, message on stderr)
    fake = os.path.join(tempfile.gettempdir(), "nonexistent_vm_test.sv")
    # Ensure it doesn't exist
    if os.path.exists(fake):
        os.unlink(fake)
    run_test("11.1  non-existent file (rc=2, argparse error)",
             [fake, "--list-port"],
             expect_rc=2)  # stderr has 'usage:' but stdout is empty

    # Invalid --add-port spec (edit-stage validation error)
    run_test("11.2  invalid --add-port spec (missing name, rc=3)",
             ["--module", "memory",
              "--add-port", "input,"],
             expect_rc=3)  # Edit failed: validation in edit stage

    # Invalid direction (edit-stage validation error)
    run_test("11.3  invalid direction in --add-port (rc=3)",
             ["--module", "memory",
              "--add-port", "invalid_direction, x"],
             expect_rc=3)  # Edit failed: direction validation

    # --module non-existent module name
    run_test("11.4  --module with wrong name",
             ["--module", "nonexistent_module_xxx", "--list-port"],
             expect_rc=2)

    # ==================================================================
    # Summary
    # ==================================================================
    total = PASS + FAIL
    print("\n" + "=" * 72)
    print(f"  Results: {PASS}/{total} passed, {FAIL}/{total} failed")
    if FAIL == 0:
        print("  ALL TESTS PASSED!")
    print("=" * 72)

    # Cleanup
    if os.path.exists(singleFixture):
        os.unlink(singleFixture)

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
