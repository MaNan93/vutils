#!/usr/bin/env python3
"""Read-only corpus check; write metadata and diagnostics, never input RTL.

uv run python examples/check_src.py ../src --output artifacts/tree-sitter-review.json
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import time

from vutils import TreeSitterParser, vedit
from vutils.vedit import _analyze_modules, gen_inst, gen_sig_decl


def check_file(path: Path, root: Path) -> dict:
    row = {'file': str(path.relative_to(root)), 'status': 'ok', 'stage': 'parse'}
    try:
        source = path.read_bytes().decode('utf-8')
        modules = _analyze_modules(source)
        row.update(modules=len(modules), ports=sum(len(m['ports']) for m in modules),
                   instances=sum(len(m['instances']) for m in modules))
        row['edited_instances'] = []
        for mod in modules:
            row['stage'] = 'template'
            template = 'module vutils_example;\n' + gen_sig_decl(mod) + '\n' + gen_inst(mod) + '\nendmodule\n'
            errors = TreeSitterParser().parse_string(template, {'gen_tree': False}).errors
            if errors:
                raise ValueError('; '.join(e.message for e in errors))
            row['stage'] = 'edit'
            editor = vedit(source, mod['name'])
            ports = {'vutils_review_probe': {'direction': 'input', 'type': 'logic'}}
            wires = {'vutils_review_wire': {'width': '[7:0]'}}
            editor.add_port(ports).add_wire(wires)
            if 'vutils_review_probe' not in {p['name'] for p in editor.ports}:
                raise AssertionError('added port absent from parsed interface')
            first = editor.module_text
            editor.add_port(ports).add_wire(wires)
            if first != editor.module_text:
                raise AssertionError('port or wire insertion is not idempotent')
            if mod['instances']:
                instance = mod['instances'][0]
                # Use lexical scope paths for repeated generate instance names.
                name = instance['path']
                editor.add_inst_ports(name, {'vutils_review_probe': 'vutils_review_wire'})
                first = editor.module_text
                editor.add_inst_ports(name, {'vutils_review_probe': 'vutils_review_wire'})
                if first != editor.module_text:
                    raise AssertionError('instance connection insertion is not idempotent')
                row['edited_instances'].append(name)
        row['stage'] = 'complete'
    except Exception as exc:
        row.update(status='failed', error=str(exc))
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.root.is_dir():
        parser.error('root must be a source directory')
    paths = sorted(args.root.rglob('*.sv'))
    if not paths:
        parser.error('no .sv files found')
    started = time.perf_counter()
    rows = []
    for idx, path in enumerate(paths, 1):
        rows.append(check_file(path, args.root))
        if idx % 100 == 0:
            print(f'{idx}/{len(paths)} files checked', flush=True)
    counts = Counter(row['status'] for row in rows)
    report = {'backend': 'tree-sitter-systemverilog', 'source_root': str(args.root.resolve()),
              'seconds': round(time.perf_counter()-started, 3),
              'summary': dict(counts), 'files': rows,
              'limitations': 'Structural syntax and editing check; no macro expansion, type checking or elaboration.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(json.dumps({**dict(counts), 'seconds': report['seconds'], 'report': str(args.output)}))
    return 1 if counts['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
