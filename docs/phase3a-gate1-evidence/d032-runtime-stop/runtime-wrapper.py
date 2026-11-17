"""Run only the committed historical CLI, preserving actual runtime counters."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

from gate1_runtime_counter import Counter

root = Path.cwd()
mode = sys.argv[1]
assert mode in ('refetch', 'resume')
destination = root / ('.tmp/gate1-runtime-' + mode + '.json')
assert not destination.exists(), 'Do not overwrite runtime evidence'
args = ['--config', 'configs/history-extension.toml', '--output-root',
        'data/raw/binance/spot/BTCUSDT/1m/historical']
args += ['--only-2020-02-refetch'] if mode == 'refetch' else ['--resume-from', '2020-03']
spec = importlib.util.spec_from_file_location('gate1_entry', root / 'scripts/download_historical.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
counter = Counter()
started = time.perf_counter()
report = {'status': 'failed', 'argv': args,
          'started_at_utc': datetime.now(timezone.utc).isoformat(),
          'implementation_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}
counter.start()
try:
    result = cli.main(args)
    assert result == 0
    report['status'] = 'passed'
except BaseException as exc:
    report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    raise
finally:
    sys.setprofile(None)
    report.update(counter.finish())
    report['elapsed_seconds'] = time.perf_counter() - started
    report['peak_process_memory_bytes'] = cli._peak_memory_bytes()
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    ledger_path = root / ('results/phase3a-gate1/continuations/2020-02-refetch.json' if mode == 'refetch'
                          else 'results/phase3a-gate1/network-ledger-continuation.json')
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_bytes())
        report['market_network_requests'] = ledger['http_gets_count'] - (4 if mode == 'refetch' else 6)
        report['holdout_requests'] = ledger['holdout_requests']
        report['holdout_access_count'] = ledger['holdout_access_count']
        report['smoke_day_rows_read'] = 0
        report['row_scope'] = 'Only historical allowlist URLs and parsed historical month timestamps; smoke is outside both guards'
    with destination.open('x', encoding='utf8', newline='\n') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps(report, indent=2), flush=True)
