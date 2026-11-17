"""Non-overridable Phase 2B development-data and final-holdout boundaries."""

from __future__ import annotations

import copy
from pathlib import Path
import tomllib

import pandas as pd

from quantforge import data

ALLOWED_MONTHS = (
    "2025-09", "2025-10", "2025-11", "2025-12", "2026-01",
    "2026-02", "2026-03", "2026-04", "2026-05", "2026-06",
)
DEVELOPMENT_START = pd.Timestamp("2025-09-01T00:00:00Z")
DEVELOPMENT_END = pd.Timestamp("2026-07-01T00:00:00Z")
FINAL_HOLDOUT_END = pd.Timestamp("2026-09-01T00:00:00Z")
HOLDOUT_A_START = pd.Timestamp("2026-07-01T00:00:00Z")
HOLDOUT_A_END = FINAL_HOLDOUT_END
HOLDOUT_B_START = pd.Timestamp("2026-09-02T00:00:00Z")
HOLDOUT_B_END = pd.Timestamp("2026-11-01T00:00:00Z")
SMOKE_DAY = pd.Timestamp("2026-09-01T00:00:00Z")
HOLDOUT_A = (HOLDOUT_A_START, HOLDOUT_A_END)
HOLDOUT_B = (HOLDOUT_B_START, HOLDOUT_B_END)
EXPECTED_ROWS = 436_320
DEFAULT_PERIOD_CONFIG = "configs/research-periods.toml"
DEFAULT_DEVELOPMENT_MANIFEST = "data/processed/binance/spot/BTCUSDT/1m/development-manifest.json"
MONTHLY_URL = "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m"
EVALUATION_MONTHS = ("2026-03", "2026-04", "2026-05", "2026-06")

_POLICY = {
    "dataset": {"market": "spot", "symbol": "BTCUSDT", "interval": "1m", "timezone": "UTC"},
    "periods": {
        "development_start": "2025-09-01T00:00:00Z", "development_end": "2026-07-01T00:00:00Z",
        "initial_train_start": "2025-09-01T00:00:00Z", "initial_train_end": "2026-03-01T00:00:00Z",
        "validation_start": "2026-03-01T00:00:00Z", "validation_end": "2026-05-01T00:00:00Z",
        "development_evaluation_start": "2026-05-01T00:00:00Z", "development_evaluation_end": "2026-07-01T00:00:00Z",
        "final_holdout_start": "2026-07-01T00:00:00Z", "final_holdout_end": "2026-09-01T00:00:00Z",
    },
    "access": {"allowed_months": list(ALLOWED_MONTHS), "final_holdout_access": False, "expected_rows": EXPECTED_ROWS},
    "evaluation": {"months": list(EVALUATION_MONTHS), "fit_at": "month_start", "evaluation_cutoff": "month_end", "availability_delay_seconds": 1},
}


class ResearchPolicyError(ValueError):
    """An input attempts to leave the frozen development-data scope."""


def canonical_policy() -> dict:
    return copy.deepcopy(_POLICY)


def ensure_allowed_month(month: str) -> str:
    if not isinstance(month, str) or month not in ALLOWED_MONTHS:
        raise ResearchPolicyError(f"Month {month!r} is outside the frozen Phase 2B development whitelist")
    return month


def month_bounds(month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    ensure_allowed_month(month)
    start = pd.Timestamp(f"{month}-01T00:00:00Z")
    return start, start + pd.offsets.MonthBegin(1)


def fixed_project_path(value: str | Path, expected: str | Path) -> Path:
    """Compare lexical targets before resolution; reject aliases and symlinks.

    A rejected path is never stat'ed or opened. Resolving an allowed canonical
    name may inspect link metadata but never reads a market-data payload.
    """
    root = data.REPOSITORY_ROOT.resolve()
    canonical = root / Path(expected)
    supplied = Path(value)
    lexical = supplied if supplied.is_absolute() else root / supplied
    if ".." in lexical.parts or lexical != canonical:
        raise ResearchPolicyError(f"Path must be the fixed development path {expected}")
    resolved = lexical.resolve()
    if resolved != canonical or not resolved.is_relative_to(root):
        raise ResearchPolicyError("Development paths cannot redirect through symlinks")
    return resolved


def month_paths(month: str) -> dict[str, Path]:
    ensure_allowed_month(month)
    name = f"BTCUSDT-1m-{month}"
    raw = f"data/raw/binance/spot/BTCUSDT/1m/monthly/{name}.zip"
    processed = f"data/processed/binance/spot/BTCUSDT/1m/monthly/{name}"
    relative = {"raw": raw, "checksum": raw + ".CHECKSUM", "parquet": processed + ".parquet", "manifest": processed + ".json"}
    return {key: fixed_project_path(value, value) for key, value in relative.items()}


def load_period_policy(path: str | Path = DEFAULT_PERIOD_CONFIG) -> dict:
    """Validate exact frozen config before any market-data source is opened."""
    config = fixed_project_path(path, DEFAULT_PERIOD_CONFIG)
    raw = tomllib.loads(config.read_text(encoding="utf-8"))
    if not _same_typed_value(raw, _POLICY):
        raise ResearchPolicyError("Research periods and access policy are frozen; overrides are not permitted")
    return canonical_policy()


def _same_typed_value(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(_same_typed_value(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(_same_typed_value(left, right) for left, right in zip(actual, expected, strict=True))
    return actual == expected


def validate_development_bounds(start: pd.Timestamp, end: pd.Timestamp) -> None:
    if start != DEVELOPMENT_START or end != DEVELOPMENT_END:
        raise ResearchPolicyError("The development interval must be exactly [2025-09-01, 2026-07-01) UTC")


def _utc(value: object, name: str) -> pd.Timestamp:
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ResearchPolicyError(f"{name} must be a UTC timestamp") from exc
    if stamp.tzinfo is None:
        raise ResearchPolicyError(f"{name} must be timezone-aware UTC")
    stamp = stamp.tz_convert("UTC")
    if pd.isna(stamp):
        raise ResearchPolicyError(f"{name} must not be NaT")
    return stamp


def ensure_not_holdout(start: object, end_exclusive: object) -> None:
    """Reject any half-open interval intersecting either sealed holdout.

    This guard is intentionally independent of ``_POLICY`` so the historical
    Phase 2B canonical policy hash remains byte-for-byte compatible. Callers
    must invoke it before opening a file, creating a cache directory or making
    a network request.
    """
    start = _utc(start, "start")
    end = _utc(end_exclusive, "end_exclusive")
    if not start < end:
        raise ResearchPolicyError("start must be earlier than end_exclusive")
    for name, (sealed_start, sealed_end) in (("Holdout-A", HOLDOUT_A), ("Holdout-B", HOLDOUT_B)):
        if start < sealed_end and end > sealed_start:
            raise ResearchPolicyError(f"{name} is sealed; interval intersects frozen holdout")


# Phase 3A planning and synthetic-only history boundaries. These do not expand
# any existing development loader or authorize a market-data request.
HISTORICAL_START = pd.Timestamp("2020-01-01T00:00:00Z")
HISTORICAL_END = pd.Timestamp("2025-09-01T00:00:00Z")
HISTORICAL_WARMUP_MONTHS = tuple(f"2020-{month:02d}" for month in range(1, 13))
HISTORICAL_EVALUATION_MONTHS = tuple(
    f"{year}-{month:02d}"
    for year in range(2021, 2026)
    for month in range(1, 13)
    if (year, month) <= (2025, 8)
)
HISTORICAL_MONTHS = HISTORICAL_WARMUP_MONTHS + HISTORICAL_EVALUATION_MONTHS


def ensure_month_in_historical_allowlist(month: str) -> str:
    """Validate a history month without enlarging the development allowlist."""
    if not isinstance(month, str) or month not in HISTORICAL_MONTHS:
        raise ResearchPolicyError(f"Month {month!r} is outside the Phase 3A historical allowlist")
    return month


def ensure_historical_month(month: str) -> str:
    """Validate the separate historical policy used only by new entry points."""
    return ensure_month_in_historical_allowlist(month)


def historical_month_bounds(month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return fixed UTC calendar bounds after the historical allowlist gate."""
    ensure_historical_month(month)
    start = pd.Timestamp(f"{month}-01T00:00:00Z")
    return start, start + pd.offsets.MonthBegin(1)
