"""Full synthetic calendar PF audit in an isolated, single serial child."""
import json
import os
from pathlib import Path
import subprocess
import sys

from test_phase3a_gate2_core import no_real_access, _history_config, _execution_contract


def test_all_112_synthetic_calendar_folds_satisfy_d057_and_structural_table(tmp_path):
    root = Path(__file__).resolve().parents[1]
    # These are result-blind timestamps/hashes, never parquet or ZIP content.
    manifest = json.loads((root/"data/processed/binance/spot/BTCUSDT/1m/historical/historical-manifest.json").read_text(encoding="utf-8"))
    compact = {"months":[{k:r[k] for k in ("month","rows","gap_ranges")} for r in manifest["months"]]}
    payload = {"config":_history_config(), "contract":_execution_contract(), "manifest":compact,
               "structural":json.loads((root/"docs/phase3a-gate2-structural-expectations.json").read_text(encoding="utf-8")),
               "conflicts_csv":(root/"docs/phase3a-gate2-sensitivity-conflicts.csv").read_text(encoding="utf-8")}
    input_path = tmp_path/"calendar-metadata.json"
    with input_path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle)
    assert "QF_GATE2_REAL_DATA" not in os.environ
    code = r'''
import builtins, io, json, sys
from pathlib import Path
import pandas as pd
from quantforge import phase3a_gate2 as g2
from quantforge.phase3a_gate2_rehearsal import make_rehearsal_bars
task_path = Path(sys.argv[1])
source_root = g2.REPOSITORY_ROOT
def guarded(original):
    def call(file, *args, **kwargs):
        if isinstance(file, (str,Path)):
            p=Path(file).resolve()
            if p.is_relative_to(source_root/"data") or p.is_relative_to(source_root.parent/"QuantForge-verification"):
                raise AssertionError("synthetic test attempted real data IO: "+str(p))
        return original(file,*args,**kwargs)
    return call
builtins.open=guarded(builtins.open)
io.open=guarded(io.open)
for name in ("REPOSITORY_ROOT","CONTRACT_PATH","MANIFEST_PATH","PARQUET_ROOT","EVIDENCE_DIR","RESULTS_ROOT","LAUNCH_LEDGER"):
    setattr(g2,name,task_path.parent/name)
v=json.loads(task_path.read_text(encoding="utf-8"))
with g2.offline():
    bars=make_rehearsal_bars(v["config"],manifest=v["manifest"],contract=v["contract"])
    close=g2.audit_close_times(bars,v["contract"])
    g2.validate_calendar(bars,v["manifest"]["months"],v["contract"])
    bars=g2.project_keep_columns(bars,v["contract"])
    conflicts=pd.DatetimeIndex(pd.to_datetime(pd.read_csv(io.StringIO(v["conflicts_csv"])).open_time_utc,utc=True))
    context=g2.prepare_fold_audits(bars,v["config"],v["contract"],conflicts,structural=v["structural"],entries=v["manifest"]["months"],close_audit=close)
    print(json.dumps({"gates":context.audit["gates"],"folds":len(context.audit["folds"]),
                      "close_anomalies":len(context.close_audit["anomalies"]),
                      "structural":context.sensitivity["structural_counts"],
                      "removed":[dict(month=r["month"],segments=r["pf6_removed_segments"])
                                 for r in context.audit["folds"] if r["pf6_removed_segments"]]},ensure_ascii=True))
'''
    result = subprocess.run([sys.executable,"-X","utf8","-B","-c",code,str(input_path)],
                            capture_output=True, text=True, encoding="utf-8", cwd=root)
    assert result.returncode == 0, result.stdout + result.stderr
    output = json.loads(result.stdout)
    assert output["folds"] == 112 and output["close_anomalies"] == 8
    assert all(output["gates"].values())
    assert output["structural"] == dict(masked_valid_bars_all_months=2068, masked_eval_bars=1712,
                                       eval_months_affected=10, short_segments_dropped=1)
