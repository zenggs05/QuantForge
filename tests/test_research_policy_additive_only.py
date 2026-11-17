"""The original development policy remains frozen when history helpers are added."""

import ast
import hashlib
import json
from pathlib import Path
import tomllib

import pytest

from quantforge import research_policy as policy


def test_original_policy_object_and_ast_are_unchanged():
    config = tomllib.loads(Path("configs/history-extension.toml").read_text(encoding="utf-8"))
    assert policy.canonical_policy() == json.loads(config["legacy_policy"]["canonical_json"])
    tree = ast.parse(Path(policy.__file__).read_text(encoding="utf-8"))
    policy_node = next(node for node in tree.body
                       if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "_POLICY" for t in node.targets))
    digest = hashlib.sha256(ast.dump(policy_node, include_attributes=False).encode()).hexdigest()
    assert digest == config["legacy_policy"]["policy_ast_sha256"]
    copied = policy.canonical_policy()
    copied["access"]["allowed_months"].append("2020-01")
    assert policy.canonical_policy() != copied


def test_separate_month_policies_and_calendar_boundaries():
    assert len(policy.HISTORICAL_WARMUP_MONTHS) == 12
    assert len(policy.HISTORICAL_EVALUATION_MONTHS) == 56
    assert len(policy.HISTORICAL_MONTHS) == 68
    assert policy.HISTORICAL_MONTHS[0] == "2020-01"
    assert policy.HISTORICAL_MONTHS[-1] == "2025-08"
    assert policy.ensure_allowed_month("2026-03") == "2026-03"
    start, end = policy.historical_month_bounds("2020-02")
    assert (end - start).days == 29
    assert set(policy.ALLOWED_MONTHS).isdisjoint(policy.HISTORICAL_MONTHS)


@pytest.mark.parametrize("month", ["2019-12", "2025-09", "2026-07", "2026-08", "2026-09", "2026-10", "2020-1", None])
def test_history_allowlist_is_closed(month):
    with pytest.raises(policy.ResearchPolicyError, match="historical allowlist"):
        policy.ensure_month_in_historical_allowlist(month)
