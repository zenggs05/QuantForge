"""Synthetic tests for the D-063 Holdout-B runner. No real data, no network."""
import hashlib
import io
import zipfile

import numpy as np
import pandas as pd
import pytest

from quantforge import holdout_b as hb
from quantforge import phase3a_runner as runner
from quantforge.phase3a_config import load_history_config


def _csv_rows(start, minutes):
    rows = []
    for i in range(minutes):
        t = pd.Timestamp(start) + pd.Timedelta(minutes=i)
        us = t.value // 1000
        rows.append(f"{us},100,101,99,100.5,2,{us + 59_999_999},201,10,1,100.5,0")
    return ("\n".join(rows) + "\n").encode()


def _zip(month, payload, name=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name or f"BTCUSDT-1m-{month}.csv", payload)
    return buffer.getvalue()


def test_real_access_refused_under_pytest(monkeypatch):
    monkeypatch.setenv("QF_HOLDOUT_B_AUTH", hb.AUTH)
    with pytest.raises(hb.HoldoutError):
        hb.require_auth()
    monkeypatch.delenv("QF_HOLDOUT_B_AUTH")
    with pytest.raises(hb.HoldoutError):
        hb.require_auth()


def test_guard_times_never_admits_smoke_day_or_later_periods():
    hb.guard_times(pd.DatetimeIndex(["2025-09-01T00:00Z", "2026-08-31T23:59Z", "2026-09-02T00:00Z", "2026-10-31T23:59Z"]))
    for bad in ("2026-09-01T00:00Z", "2026-09-01T23:59Z", "2026-11-01T00:00Z", "2025-08-31T23:59Z"):
        with pytest.raises(hb.HoldoutError):
            hb.guard_times(pd.DatetimeIndex([bad]))


def test_smoke_rows_are_dropped_before_audit():
    frame = hb.read_zip_csv(_zip("2026-09", _csv_rows("2026-09-01T23:58Z", 4)), "2026-09")
    kept, dropped = hb.drop_smoke_rows(frame)
    assert dropped == 2 and len(kept) == 2
    assert kept.open_time.min() == pd.Timestamp("2026-09-02T00:00Z")
    audit = hb.audit_frame(kept, "2026-09", start=hb.EVAL_START)
    assert audit["window_start"].startswith("2026-09-02")
    assert audit["expected_rows"] == 29 * 1440 and audit["rows"] == 2


def test_zip_member_name_is_enforced():
    with pytest.raises(hb.HoldoutError):
        hb.read_zip_csv(_zip("2026-10", _csv_rows("2026-10-01T00:00Z", 2), name="other.csv"), "2026-10")


def test_url_allowlist_is_exactly_four_strings():
    assert len(hb.URLS) == 4 and len(set(hb.URLS)) == 4
    for url in hb.URLS:
        month, kind = hb.url_info(url)
        assert month in hb.MONTHS and kind in ("zip", "checksum")
    for bad in (hb.URL_PREFIX + "2026-11.zip", hb.URL_PREFIX + "2026-09-01.zip",
                "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-09-01.zip"):
        with pytest.raises(hb.HoldoutError):
            hb.url_info(bad)


def test_checksum_validation():
    payload = _zip("2026-09", _csv_rows("2026-09-02T00:00Z", 2))
    text = f"{hashlib.sha256(payload).hexdigest()}  BTCUSDT-1m-2026-09.zip".encode()
    assert hb.validate_checksum(text, "2026-09") == hashlib.sha256(payload).hexdigest()
    with pytest.raises(Exception):
        hb.validate_checksum(text, "2026-10")


def _pooled(rng):
    config = hb.execution_config(load_history_config())
    rows = []
    for month, start in (("2026-09", "2026-09-02T01:01:01Z"), ("2026-10", "2026-10-01T00:01:01Z")):
        n = 6000
        times = pd.date_range(start, periods=n, freq="min")
        for h in (15, 5):
            y = rng.normal(0, 1e-3, n)
            f = 0.05 * y + rng.normal(0, 1e-4, n)
            for spec in (("v1", "v2") if h == 15 else ("v1",)):
                for baseline in ("zero", "historical_mean"):
                    b = np.zeros(n) if baseline == "zero" else np.full(n, 1e-6)
                    key = dict(month=month, specification=spec, horizon_minutes=h, baseline=baseline, specification_evidence_level="t")
                    rows.extend(runner._monthly(y, f, b, times, key, config, mode="real", evidence=hb.LEVEL))
                if h == 15 and spec == "v1":
                    key = dict(month=month, specification=spec, horizon_minutes=h, baseline="demeaned_zero", specification_evidence_level="t")
                    rows.extend(runner._monthly(y - y.mean(), f - f.mean(), np.zeros(n), times, key, config, mode="real", evidence=hb.LEVEL))
    return runner._pool(rows, config, evidence=hb.LEVEL), rows


def test_primary_is_ic_and_secondary_family_is_holm_adjusted():
    pooled, rows = _pooled(np.random.default_rng(7))
    primary, secondary = hb.holm_family(pooled)
    assert primary["metric"] == "IC" and primary["horizon_minutes"] == 15 and primary["specification"] == "v1"
    assert primary["reject"] is True and 0 < primary["mean"] < 1
    assert [r["test_id"] for r in secondary] == ["S1", "S2", "S3", "S4", "S5"]
    raw = sorted(r["p_value"] for r in secondary)
    assert min(r["holm_p"] for r in secondary) == pytest.approx(min(1.0, 5 * raw[0]))
    s2 = next(r for r in secondary if r["test_id"] == "S2")
    assert s2["baseline"] == "demeaned_zero" and s2["metric"] == "CW"


def test_demeaned_cw_equals_twice_within_month_covariance():
    rng = np.random.default_rng(3)
    y, f = rng.normal(0.001, 1e-3, 500), rng.normal(0.0005, 1e-4, 500)
    from quantforge.diagnostics import forecast_tests as ft
    c = ft.clark_west_differential(y - y.mean(), f - f.mean(), np.zeros(500))
    assert np.mean(c) == pytest.approx(2 * np.mean((f - f.mean()) * (y - y.mean())), rel=1e-12)


def test_breakeven_accounting():
    times = pd.date_range("2026-10-01T00:01:01Z", periods=15 * 40, freq="min")
    y = np.tile([0.001, -0.001], 15 * 20)
    f = y.copy()
    result = hb.breakeven(times, f, y, 15, 0.0)
    assert result["periods"] == 40 and result["traded_share"] == 1.0
    assert result["breakeven_fee_bp_per_side"] > 0
    assert hb.breakeven(times, f, y, 15, 50.0)["breakeven_fee_bp_per_side"] is None


def test_combined_ic_arithmetic():
    out = hb.combine_ic(dict(n=100, mean=0.03, se=0.01), dict(n=300, mean=0.01, se=0.006))
    assert out["mean"] == pytest.approx(0.015)
    assert out["se"] == pytest.approx(np.sqrt((0.25 * 0.01) ** 2 + (0.75 * 0.006) ** 2))


def test_protocol_and_frozen_hashes_match_repository():
    for name, expected in hb.FROZEN.items():
        assert hb.digest(hb.ROOT / name) == expected, name
    assert hb.digest(hb.PROTOCOL) == hb.PROTOCOL_SHA


def test_fold_month_uses_fit_time_not_first_evaluation_bar():
    """D-064 regression: October's first evaluation bar opens at 2026-09-30T23:59Z."""
    from types import SimpleNamespace
    fold = SimpleNamespace(fit_time=pd.Timestamp("2026-10-01T00:00Z"),
                           eval_indices=pd.DatetimeIndex(["2026-09-30T23:59Z", "2026-10-01T00:00Z"]))
    assert fold.eval_indices[0].strftime("%Y-%m") == "2026-09"
    assert hb.fold_month(fold) == "2026-10"
    september = SimpleNamespace(fit_time=hb.FIT_TIMES["2026-09"], eval_indices=pd.DatetimeIndex(["2026-09-02T01:00Z"]))
    assert hb.fold_month(september) == "2026-09"


def test_reaudit_comparison_ignores_only_code_hashes():
    base = dict(rows=10, folds=[1, 2], execution_code_sha256={"a": "1"})
    assert hb.audit_equal_except_code(base, {**base, "execution_code_sha256": {"a": "2"}})
    assert not hb.audit_equal_except_code(base, {**base, "rows": 11})
