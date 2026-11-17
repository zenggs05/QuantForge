"""Run the committed D-033 CLI once and retain independent runtime evidence."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

from gate1_runtime_counter import Counter
from quantforge import data_monthly as monthly

root = Path.cwd()
destination = root / '.tmp/gate1-runtime-d033.json'
event_path = root / '.tmp/gate1-d033-persistence-events.json'
assert not destination.exists() and not event_path.exists(), 'Do not overwrite runtime evidence'
args = ['--config', 'configs/history-extension.toml', '--output-root',
        'data/raw/binance/spot/BTCUSDT/1m/historical', '--resume-from', '2022-06']
spec = importlib.util.spec_from_file_location('gate1_entry', root / 'scripts/download_historical.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
counter = Counter()
started = time.perf_counter()
report = {'status': 'failed', 'argv': args,
          'started_at_utc': datetime.now(timezone.utc).isoformat(),
          'implementation_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
          'carry_in_http_gets': 64, 'carry_in_zip_bytes': 68371336,
          'carry_in_checksum_bytes': 2816}
counter.start()
try:
    assert cli.main(args) == 0
    report['status'] = 'passed'
except BaseException as exc:
    report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    raise
finally:
    sys.setprofile(None)
    report.update(counter.finish())
    report.update(monthly.get_ledger_persistence_diagnostics())
    report['elapsed_seconds'] = time.perf_counter() - started
    report['peak_process_memory_bytes'] = cli._peak_memory_bytes()
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    ledger_path = root / 'results/phase3a-gate1/network-ledger-d033.json'
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_bytes())
        if ledger.get('failure'):
            report['ledger_failure'] = ledger['failure']
        report.update(market_network_requests=ledger['http_gets_count'] - 64,
                      http_gets_count_total=ledger['http_gets_count'],
                      new_zip_bytes=ledger['zip_body_bytes'] - 68371336,
                      new_checksum_bytes=ledger['checksum_body_bytes'] - 2816,
                      market_rows_read_total_in_ledger=ledger.get('market_rows_read', 0),
                      holdout_requests=ledger['holdout_requests'],
                      holdout_access_count=ledger['holdout_access_count'],
                      smoke_day_rows_read=0,
                      row_scope='Historical month timestamps and URLs guarded by the fixed allowlist')
    for path, payload in ((destination, report), (event_path, monthly.get_ledger_persistence_events())):
        with path.open('x', encoding='utf8', newline='\n') as stream:
            json.dump(payload, stream, indent=2)
            stream.write('\n')
    print(json.dumps(report, indent=2), flush=True)
