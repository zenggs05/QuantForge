"""D-033 incident recovery with immutable carry-in and exact byte accounting."""

from __future__ import annotations

import copy
import json
import re

from quantforge import data, data_monthly as monthly, research_policy


D033_LEDGER_PATH = "results/phase3a-gate1/network-ledger-d033.json"
D033_INCIDENT_PATH = "results/phase3a-gate1/network-ledger-continuation.json"
D033_ARCHIVED_INCIDENT_PATH = "docs/phase3a-gate1-evidence/d032-runtime-stop/network-ledger-at-stop.json"
D033_INCIDENT_SHA256 = "a68517f7dda44775ef6c20dfb1c88a144cebe1b05c1583662101318a533d9379"
D033_JUNE_CHECKSUM_SHA256 = "30ed4c4ce515aceb2df64d36fd1c87e720f7747c0d3f26c3579b7d718d05905d"
D033_SCOPE = "D-033"


def _fixed(relative: str):
    return monthly._historical_contract_path(relative, relative)


def _read_incident(contract: dict, *, compare_local: bool) -> tuple[dict, bytes]:
    """Load only the pinned June failure metadata, never market rows."""
    archived = monthly._limited_read(_fixed(D033_ARCHIVED_INCIDENT_PATH), 16 * 1024 * 1024)
    if data.sha256(archived) != D033_INCIDENT_SHA256:
        raise data.DataValidationError("D-033 archived incident hash differs from its pinned bytes")
    if compare_local:
        local = monthly._limited_read(_fixed(D033_INCIDENT_PATH), 16 * 1024 * 1024)
        if local != archived:
            raise data.DataValidationError("D-033 local incident ledger differs from archived evidence")
    incident = json.loads(archived)
    validator = monthly.HistoricalNetworkLedger(contract)
    validator.state = incident
    validator._validate_state()
    expected = {
        "status": "failed", "failed_month": "2022-06", "http_gets_count": 64,
        "zip_body_bytes": 68371336, "checksum_body_bytes": 2816,
        "zip_reserved_bytes": 0, "checksum_reserved_bytes": 0,
    }
    if (any(not research_policy._same_typed_value(incident.get(k), v) for k, v in expected.items())
            or tuple(e.get("month") for e in incident["months"]) != contract["months"][:29]
            or len(incident["months"]) != 29
            or incident.get("stop_error", {}).get("type") != "PermissionError"
            or incident.get("failure", {}).get("error_type") != "PermissionError"):
        raise data.DataValidationError("D-033 carry-in does not match the June incident checkpoint")
    checksum, partial = incident["attempts"][62:64]
    if (checksum.get("id") != 62 or checksum.get("month") != "2022-06"
            or checksum.get("kind") != "checksum" or checksum.get("status") != "received"
            or checksum.get("http_status") != 200 or checksum.get("body_bytes") != 88
            or checksum.get("sha256") != D033_JUNE_CHECKSUM_SHA256
            or partial.get("id") != 63 or partial.get("month") != "2022-06"
            or partial.get("kind") != "zip" or partial.get("status") != "failed"
            or partial.get("error_type") != "PermissionError"
            or partial.get("body_bytes") != 1601170 or partial.get("reserved_bytes") != 0):
        raise data.DataValidationError("D-033 June CHECKSUM/partial ZIP incident metadata changed")
    return incident, archived


def _verify_cached_prefix_bytes(contract: dict, incident: dict) -> None:
    """Check retained raw/Parquet digests and sidecar metadata without parsing bars."""
    stable = (
        "month", "status", "url", "checksum_url", "zip_sha256", "checksum_text_sha256",
        "parquet_sha256", "csv_sha256", "rows", "timestamp_unit", "first_open_utc",
        "last_open_utc", "bytes_downloaded", "checksum_bytes_downloaded", "zip_bytes",
        "parquet_bytes", "attempts", "http_gets",
    )
    for entry in incident["months"]:
        month = entry["month"]
        monthly._validate_historical_graded_entry(entry, month, contract)
        paths = monthly._historical_paths(month, contract)
        source = json.loads(monthly._limited_read(paths["source"], 2 * 1024 * 1024))
        if any(not research_policy._same_typed_value(source.get(k), entry.get(k)) for k in stable):
            raise data.DataValidationError(f"D-033 retained sidecar metadata changed for {month}")
        for key, digest_key, limit in (
            ("raw", "zip_sha256", contract["per_zip_limit_bytes"]),
            ("checksum", "checksum_text_sha256", contract["per_checksum_limit_bytes"]),
            ("parquet", "parquet_sha256", contract["per_csv_limit_bytes"] * 2),
        ):
            payload = monthly._limited_read(paths[key], limit)
            if data.sha256(payload) != entry[digest_key]:
                raise data.DataValidationError(f"D-033 retained {key} bytes changed for {month}")
    monthly.audit_historical_boundaries(incident["months"])


class HistoricalD033Ledger(monthly.HistoricalNetworkLedger):
    """Start the separately authorized June recovery in a new fixed ledger."""

    def __init__(self, contract: dict, implementation_commit: str):
        super().__init__(contract)
        if not isinstance(implementation_commit, str) or re.fullmatch(r"[0-9a-f]{40}", implementation_commit) is None:
            raise data.DataValidationError("D-033 requires a full implementation commit SHA")
        self.path = _fixed(D033_LEDGER_PATH)
        self.implementation_commit = implementation_commit

    def __enter__(self):
        if self._active:
            raise monthly.DownloadBudgetError("D-033 ledger context cannot be nested")
        self._lock = monthly._HistoricalDownloadLock()
        self._lock.__enter__()
        try:
            if self.path.exists():
                raise monthly.DownloadBudgetError("D-033 ledger already exists; automatic retry/reset is forbidden")
            incident, _ = _read_incident(self.contract, compare_local=True)
            paths = monthly._historical_paths("2022-06", self.contract)
            if any(path.exists() for path in paths.values()):
                raise data.DataValidationError("D-033 June output exists; replacement is forbidden")
            _verify_cached_prefix_bytes(self.contract, incident)
            state = copy.deepcopy(incident)
            incident_details = {
                key: state.pop(key) for key in (
                    "failure", "failed_month", "stop_error", "stopped_at_utc", "last_month_diagnostics",
                    "error", "error_type",
                ) if key in state
            }
            state.update(
                status="in_progress", active_attempt_scope=D033_SCOPE,
                implementation_commit=self.implementation_commit, final_commit_sha=self.implementation_commit,
                d033_started_at_utc=monthly._now(),
                d033_recovery={
                    "incident_commit": "e0caaaf", "incident_ledger_sha256": D033_INCIDENT_SHA256,
                    "incident_archive_path": D033_ARCHIVED_INCIDENT_PATH,
                    "incident_cumulative_gets_before_resume": 64,
                    "incident_partial_bytes_charged": 1601170, "incident_checksum_bytes_charged": 88,
                    "incident_zip_body_bytes": 68371336, "incident_checksum_body_bytes": 2816,
                    "resume_implementation_commit": self.implementation_commit,
                    "resume_checksum_sha256_expected": D033_JUNE_CHECKSUM_SHA256,
                    "resume_checksum_sha_matches_archived_incident": False,
                    "incident": incident_details,
                    "diagnostics_note": "Original June diagnostics are stale May metadata and make no June quality claim.",
                },
            )
            self.state = state
            self._active = True
            self.save()
            return self
        except BaseException:
            self._active = False
            self._lock.__exit__(None, None, None)
            raise

    def validate_received_checksum(self, month: str, checksum: bytes) -> None:
        """Reject a changed June CHECKSUM before sending the ZIP request."""
        research_policy.ensure_month_in_historical_allowlist(month)
        if month == "2022-06":
            if data.sha256(checksum) != D033_JUNE_CHECKSUM_SHA256:
                raise data.DataValidationError("D-033 June CHECKSUM SHA differs from archived incident")
            self.state["d033_recovery"]["resume_checksum_sha_matches_archived_incident"] = True
            self.save()

    def add_month(self, entry: dict) -> None:
        """Record the new June quality tier separately from stale incident metadata."""
        if entry.get("month") == "2022-06":
            if self.state["d033_recovery"].get("resume_checksum_sha_matches_archived_incident") is not True:
                raise data.DataValidationError("D-033 June cannot publish before CHECKSUM identity verification")
            self.state["d033_recovery"].update(
                resume_tier=entry["status_tier"],
                resume_missing_minutes=entry["missing_minutes"],
                resume_anomalous_close_time_rows=entry["anomalous_close_time_rows"],
                resume_zip_sha256=entry["zip_sha256"],
                resume_checksum_sha256=entry["checksum_text_sha256"],
            )
        super().add_month(entry)


def validate_d033_manifest_ledger(ledger: dict, entries: list[dict], contract: dict) -> dict:
    """Validate the recovery ledger without dropping either failed payload cost."""
    contract = monthly._validate_historical_contract(contract)
    incident, _ = _read_incident(contract, compare_local=False)
    validator = monthly.HistoricalNetworkLedger(contract)
    validator.state = ledger
    validator._validate_state()
    if (ledger.get("status") != "passed" or ledger.get("active_attempt_scope") != D033_SCOPE
            or not isinstance(ledger.get("d033_recovery"), dict)):
        raise data.DataValidationError("D-033 manifest requires a passed recovery ledger")
    recovery = ledger["d033_recovery"]
    expected = {
        "incident_commit": "e0caaaf", "incident_ledger_sha256": D033_INCIDENT_SHA256,
        "incident_archive_path": D033_ARCHIVED_INCIDENT_PATH,
        "incident_cumulative_gets_before_resume": 64,
        "incident_partial_bytes_charged": 1601170, "incident_checksum_bytes_charged": 88,
        "incident_zip_body_bytes": 68371336, "incident_checksum_body_bytes": 2816,
        "resume_checksum_sha256_expected": D033_JUNE_CHECKSUM_SHA256,
        "resume_checksum_sha_matches_archived_incident": True,
    }
    if any(not research_policy._same_typed_value(recovery.get(k), v) for k, v in expected.items()):
        raise data.DataValidationError("D-033 recovery evidence does not match the pinned incident")
    if (not isinstance(recovery.get("resume_implementation_commit"), str)
            or re.fullmatch(r"[0-9a-f]{40}", recovery["resume_implementation_commit"]) is None):
        raise data.DataValidationError("D-033 recovery implementation SHA is invalid")
    if not isinstance(entries, list) or len(entries) != 68:
        raise data.DataValidationError("D-033 manifest requires all 68 month entries")
    june = entries[29]
    for recovery_key, entry_key in (
        ("resume_tier", "status_tier"), ("resume_missing_minutes", "missing_minutes"),
        ("resume_anomalous_close_time_rows", "anomalous_close_time_rows"),
        ("resume_zip_sha256", "zip_sha256"), ("resume_checksum_sha256", "checksum_text_sha256"),
    ):
        if not research_policy._same_typed_value(recovery.get(recovery_key), june.get(entry_key)):
            raise data.DataValidationError("D-033 June recovery quality metadata does not reconcile")
    attempts = ledger["attempts"]
    if (not research_policy._same_typed_value(attempts[:64], incident["attempts"])
            or len(attempts) < 64 + 2 * 39
            or not research_policy._same_typed_value(entries[:29], incident["months"])):
        raise data.DataValidationError("D-033 recovery changed the original request or month prefix")
    for attempt in attempts[64:]:
        if attempt.get("scope") != D033_SCOPE or attempt.get("month") not in contract["months"][29:]:
            raise data.DataValidationError("D-033 new requests are outside the recovery scope")
        if (attempt.get("month") == "2022-06" and attempt.get("kind") == "checksum"
                and attempt.get("status") == "received" and attempt.get("sha256") != D033_JUNE_CHECKSUM_SHA256):
            raise data.DataValidationError("D-033 received June CHECKSUM differs from the incident")
    normalized = copy.deepcopy(ledger)
    normalized.pop("d033_recovery")
    normalized["attempts"] = [copy.deepcopy(a) for a in attempts if a["id"] not in {62, 63}]
    for identifier, attempt in enumerate(normalized["attempts"]):
        attempt["id"] = identifier
    normalized["http_gets_count"] -= 2
    for kind, cost in (("zip", 1601170), ("checksum", 88)):
        for suffix in ("body_bytes", "charged_bytes"):
            normalized[f"{kind}_{suffix}"] -= cost
    normalized["total_bytes_downloaded"] -= 1601170
    normalized["checksum_bytes_downloaded"] -= 88
    result = monthly._validate_historical_manifest_ledger(normalized, entries, contract)
    for kind, cost in (("zip", 1601170), ("checksum", 88)):
        result["prior_failed_payload_bytes"][kind] += cost
        result["cumulative_body_bytes"][kind] += cost
        result["cumulative_charged_bytes"][kind] += cost
        if result["cumulative_charged_bytes"][kind] > contract[f"{kind}_budget_bytes"]:
            raise monthly.DownloadBudgetError("D-033 cumulative bytes exceed frozen budget")
        if (result["cumulative_body_bytes"][kind] != ledger[f"{kind}_body_bytes"]
                or result["cumulative_charged_bytes"][kind] != ledger[f"{kind}_charged_bytes"]):
            raise data.DataValidationError("D-033 cumulative transfer reconciliation failed")
    result["cumulative_http_gets"] += 2
    if result["cumulative_http_gets"] != ledger["http_gets_count"]:
        raise data.DataValidationError("D-033 cumulative GET reconciliation failed")
    result["prior_failed_events"] = {
        "2020-02": {"zip_body_bytes": 2077466, "checksum_body_bytes": 88},
        "2022-06": {"zip_body_bytes": 1601170, "checksum_body_bytes": 88},
    }
    return result
