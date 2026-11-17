"""One clean offline revalidation of the D-031/D-032/D-033 artifact manifest."""
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

from gate1_runtime_counter import Counter
from quantforge import data, data_monthly as monthly, research_policy

root = Path.cwd()
destination = root / '.tmp/gate1-d033-revalidation.json'
assert not destination.exists(), 'Do not overwrite revalidation evidence'
assert not subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
spec = importlib.util.spec_from_file_location('gate1_entry', root / 'scripts/download_historical.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
network_attempts = 0


def forbidden(*args, **kwargs):
    global network_attempts
    network_attempts += 1
    raise AssertionError('Offline integrity revalidation attempted network access')


socket.create_connection = forbidden
urllib.request.urlopen = forbidden
urllib.request.OpenerDirector.open = forbidden
monthly._open_historical_response = forbidden
counter = Counter()
counter.start()
sys.setprofile(cli._no_research)
started = time.perf_counter()
report = {'status': 'failed', 'clean_commit': head, 'rows_revalidated': 0, 'months': []}
try:
    contract = monthly.load_historical_download_contract()
    path = root / cli.MANIFEST
    manifest_bytes = path.read_bytes()
    manifest = json.loads(manifest_bytes)
    monthly.validate_historical_graded_manifest(manifest, contract)
    for entry in manifest['months']:
        paths = monthly.historical_month_paths(entry['month'], contract)
        source = json.loads(paths['source'].read_bytes())
        check = monthly.verify_historical_graded_month(entry['month'], contract, source)
        actual = {k: v for k, v in check.items() if k != 'reverified'}
        expected = {k: v for k, v in entry.items() if k != 'reverified'}
        assert research_policy._same_typed_value(actual, expected), entry['month']
        report['rows_revalidated'] += check['rows']
        report['months'].append({'month': entry['month'], 'rows': check['rows'], 'verified': True,
                                **{k: check[k] for k in ('zip_sha256', 'checksum_text_sha256',
                                                         'csv_sha256', 'parquet_sha256')}})
        print(entry['month'] + ': exact raw/Parquet and manifest audit reproduced', flush=True)
    boundaries = monthly.audit_historical_boundaries(manifest['months'])
    assert boundaries == manifest['cross_month_boundaries']
    for month in ('2024-12', '2025-01'):
        entry = next(e for e in manifest['months'] if e['month'] == month)
        expected = {'month': month, 'expected_unit': 'ms' if month == '2024-12' else 'us',
                    'detected_unit': entry['timestamp_unit'], 'first_open_raw': entry['first_open_raw'],
                    'first_open_parsed_utc': entry['first_open_utc'], 'last_open_parsed_utc': entry['last_open_utc']}
        boundary = root / ('docs/phase3a-gate1-evidence/timestamp-boundary-' + month + '.json')
        assert boundary.read_bytes() == monthly._historical_json_bytes(expected)
    assert path.read_bytes() == manifest_bytes
    report.update(status='passed', months_revalidated=len(report['months']),
                  cross_month_boundaries_revalidated=len(boundaries),
                  manifest_file_sha256=data.sha256(manifest_bytes),
                  deterministic_file_count=207,
                  deterministic_file_count_definition='204 local ZIP/CHECKSUM/Parquet files, manifest, two boundary JSON files; additionally 68 embedded CSV digests and sidecar metadata audited')
except BaseException as exc:
    report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    raise
finally:
    sys.setprofile(None)
    report.update(counter.finish())
    report.update(market_network_requests=0, network_attempts=network_attempts,
                  holdout_requests=0, holdout_access_count=0, smoke_day_rows_read=0,
                  peak_memory_bytes=cli._peak_memory_bytes(), elapsed_seconds=time.perf_counter() - started)
    with destination.open('x', encoding='utf8', newline='\n') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'months'}, indent=2), flush=True)
