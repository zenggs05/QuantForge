"""Offline historical URL planning cannot invoke existing network download paths."""

import json
from pathlib import Path

import pytest

from quantforge import data, data_monthly as monthly, research_policy as policy


def test_plan_matches_frozen_draft_and_every_guard_is_called(monkeypatch):
    allowed, guarded = [], []
    original_allowed = policy.ensure_month_in_historical_allowlist
    original_guard = policy.ensure_not_holdout

    def allow(month):
        allowed.append(month)
        return original_allowed(month)

    def guard(start, end):
        guarded.append((start, end))
        return original_guard(start, end)

    def forbidden(*args, **kwargs):
        pytest.fail("Offline plan attempted network access")

    monkeypatch.setattr(policy, "ensure_month_in_historical_allowlist", allow)
    monkeypatch.setattr(policy, "ensure_not_holdout", guard)
    monkeypatch.setattr(monthly, "_open_response", forbidden)
    monkeypatch.setattr(data, "fetch", forbidden)
    plan = monthly.plan_historical_download()
    assert plan == json.loads(Path("docs/phase3a-download-manifest-draft.json").read_text(encoding="utf-8"))
    assert set(allowed) == set(policy.HISTORICAL_MONTHS)
    assert len(guarded) == 68
    assert plan["network_requests"] == 0
    for row, (start, end) in zip(plan["months"], guarded, strict=True):
        assert row["month"] == start.strftime("%Y-%m")
        assert end <= policy.HISTORICAL_END
        assert row["expected_checksum_url"] == row["url"] + ".CHECKSUM"


def test_old_downloader_still_rejects_historical_month_before_network(monkeypatch):
    monkeypatch.setattr(monthly, "_open_response", lambda *args: pytest.fail("Network attempted"))
    with pytest.raises(data.DataValidationError, match="authorized development"):
        monthly.download_month("2020-01")
