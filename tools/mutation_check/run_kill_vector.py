"""Run three controls for each fixed mutation using isolated local source copies."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from time import perf_counter
from uuid import uuid4

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.mutation_check.apply_mutant import MUTANTS, apply_mutant


def _run(root: Path, test: str, output: Path, timeout: float) -> dict:
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
               MKL_NUM_THREADS='1', MPLCONFIGDIR=str(root/'.tmp'/'matplotlib'),
               TEMP=str(root/'.tmp'), TMP=str(root/'.tmp'))
    (root/'.tmp').mkdir(exist_ok=True)
    # Explicit sys.path overrides editable-install paths; assert the real import origin.
    bootstrap = (
        'import sys,pathlib; r=pathlib.Path.cwd(); '
        'sys.path[:0]=[str(r/"src"),str(r)]; '
        'import quantforge; '
        'assert pathlib.Path(quantforge.__file__).resolve().is_relative_to((r/"src").resolve()); '
        'import pytest; raise SystemExit(pytest.main(sys.argv[1:]))'
    )
    command = [sys.executable, '-B', '-c', bootstrap, '-q', '-p', 'no:cacheprovider',
        '--basetemp=.tmp/pytest/mutation-control',
        f'tests/test_phase2f_mutation_regressions.py::{test}']
    started = perf_counter()
    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=timeout)
    text = result.stdout + result.stderr
    output.write_text(text, encoding='utf-8')
    return {'returncode': result.returncode, 'seconds': perf_counter()-started,
            'output': text, 'output_sha256': hashlib.sha256(text.encode()).hexdigest()}


def run_kill_vector(output: Path, copy_root: Path | None = None) -> dict:
    root = Path(__file__).resolve().parents[2]
    config = __import__('tomllib').loads((root/'configs/c1-engine-hardening.toml').read_text('utf-8'))
    budget = config['budget']['mutation_max_seconds']
    output = output.resolve()
    if not output.is_relative_to(root/'.tmp'):
        raise ValueError('Mutation evidence must stay in project-local .tmp')
    output.mkdir(parents=True, exist_ok=False)
    allowed = Path(os.environ['LOCALAPPDATA'])/'Temp'/'qf-2f-mutants'
    base = allowed if copy_root is None else copy_root.resolve()
    if base.resolve() != allowed.resolve():
        raise ValueError('Mutation copies must use the authorized temporary subtree')
    session = base/uuid4().hex[:8]
    session.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    report = {'status':'running', 'mutants':[], 'copy_root':str(session)}
    for name, (relative, _, _, test, keyword) in MUTANTS.items():
        copy = session/name
        copy.mkdir()
        for directory in ('src','tests','configs','docs','tools'):
            shutil.copytree(root/directory, copy/directory,
                ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copy2(root/'pyproject.toml', copy/'pyproject.toml')
        target = copy/'src'/'quantforge'/relative
        original = target.read_bytes()
        row = {'mutant':name, 'killer':test, 'expected_keyword':keyword,
               'source_sha256':hashlib.sha256(original).hexdigest()}
        try:
            remaining = lambda: max(.1, budget-(perf_counter()-started))
            before = _run(root,test,output/f'{name}-baseline.txt',remaining())
            target.write_bytes(apply_mutant(name,original))
            mutated = _run(copy,test,output/f'{name}-mutated.txt',remaining())
            target.write_bytes(original)
            restored = _run(copy,test,output/f'{name}-restored.txt',remaining())
            healthy = before['returncode']==0 and restored['returncode']==0
            killed = mutated['returncode']==1 and keyword in mutated['output']
            row.update(baseline=before, mutated=mutated, restored=restored,
                       environment_healthy=healthy, killed=killed,
                       valid_kill=healthy and killed)
        finally:
            target.write_bytes(original)
        report['mutants'].append(row)
        report['seconds']=perf_counter()-started
        report['status']='running' if row['valid_kill'] else 'stopped'
        (output/'mutation-results.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(f'{name}: valid_kill={row["valid_kill"]}',flush=True)
        if not row['valid_kill']:
            raise RuntimeError(f'Mutation {name} failed the three-control acceptance; stop')
    report.update(status='passed', seconds=perf_counter()-started)
    if report['seconds']>budget:
        raise RuntimeError('Mutation budget exceeded')
    (output/'mutation-results.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    run_kill_vector(args.output)
