"""AP2/AP6 checks use only frozen aggregate evidence or artificial mutations."""

import csv
import hashlib
import io
import json
from pathlib import Path
from statistics import NormalDist
import tomllib

import pytest

from quantforge.phase3a_power import EVIDENCE_LEVEL, SOURCE_PATH, power_planning, validate_ap2


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config():
    return tomllib.loads((ROOT / "configs/history-extension.toml").read_text(encoding="utf-8"))


@pytest.fixture
def artificial_csv(config, monkeypatch):
    """Intercept only the allowed CSV read; never modify frozen evidence."""
    path = ROOT / SOURCE_PATH
    original = path.read_bytes()
    reader = csv.DictReader(io.StringIO(original.decode("utf-8"), newline=""))
    fields, rows = reader.fieldnames, list(reader)
    original_read = Path.read_bytes

    def install(changed_rows, changed_fields=None):
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=changed_fields or fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(changed_rows)
        payload = stream.getvalue().encode("utf-8")
        config["power"]["source_sha256"] = hashlib.sha256(payload).hexdigest()
        monkeypatch.setattr(Path, "read_bytes", lambda p: payload if p == path else original_read(p))
    return rows, install


def test_all_twelve_anchors_and_twenty_four_inputs_match_independent_arithmetic(config):
    rows = power_planning(config)
    assert len(rows) == 12
    anchors = {anchor["key"]: anchor for anchor in config["power"]["anchors"]}
    assert [row["key"] for row in rows] == list(anchors)
    n = NormalDist()
    for row in rows:
        anchor = anchors[row["key"]]
        for field in ("se_2E", "se_56", "mde_56", "pct_obs", "obs_mean"):
            assert row[field] == pytest.approx(float(anchor[field]), rel=1e-12, abs=0)
            assert float(row[f"display_{field}"]) == pytest.approx(row[field], rel=1e-4, abs=0)
        critical = n.inv_cdf(.975 if row["test"] == "DM" else .95)
        independent_mde = (critical+n.inv_cdf(.8))*row["se_2E"]*(2/56)**.5
        assert row["mde_56"] == pytest.approx(independent_mde, rel=1e-12, abs=0)
        assert row["evidence_level"] == EVIDENCE_LEVEL
        assert row["source_sha256"] == config["power"]["source_sha256"]
    near_zero = next(row for row in rows if row["key"] == "5_development_evaluation_DM")
    assert near_zero["pct_obs"] == pytest.approx(2073.5237704055958, rel=1e-12)
    assert near_zero["obs_mean"] < 0


def test_ap2_checks_csv_statistic_as_well_as_four_mean_and_se_fields(config):
    assert validate_ap2(config) == pytest.approx({
        "cw_mean": 1.0702005158244944e-08, "cw_se": 4.330445321942128e-09,
        "cw_t": 2.4713405579833725, "dm_se": 4.326795335294619e-09,
        "dm_mean": -3.4831310495306305e-09}, rel=1e-12, abs=0)


def test_repeated_planning_is_byte_deterministic(config):
    encode = lambda rows: json.dumps(rows, sort_keys=True, allow_nan=False).encode("utf-8")
    assert encode(power_planning(config)) == encode(power_planning(config))


def test_wrong_source_path_fails_before_any_read(config, monkeypatch):
    config["power"]["source"] = "data/forbidden.csv"
    monkeypatch.setattr(Path, "read_bytes", lambda p: pytest.fail("unexpected read"))
    with pytest.raises(ValueError, match="frozen Phase 2E evidence table"):
        power_planning(config)


def test_source_hash_is_checked_before_csv_parsing(config, monkeypatch):
    config["power"]["source_sha256"] = "0"*64
    monkeypatch.setattr(csv, "DictReader", lambda *a, **kw: pytest.fail("parsed before hash check"))
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        power_planning(config)


@pytest.mark.parametrize("field", ["se_2E", "se_56", "mde_56", "pct_obs", "obs_mean"])
def test_changed_machine_anchor_cannot_pass(config, field):
    config["power"]["anchors"][0][field] = str(float(config["power"]["anchors"][0][field])*1.01)
    with pytest.raises(ValueError, match="Power anchor mismatch"):
        power_planning(config)


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown"])
def test_anchor_family_cannot_shrink_or_duplicate(config, change):
    anchors = config["power"]["anchors"]
    if change == "missing":
        anchors.pop()
    elif change == "duplicate":
        anchors[-1] = anchors[0].copy()
    else:
        anchors[-1]["key"] = "15_unknown_IC"
    with pytest.raises(ValueError, match="anchor"):
        power_planning(config)


@pytest.mark.parametrize("change", ["missing", "duplicate", "wrong_pooling", "wrong_model"])
def test_unique_four_source_rows_are_required(config, artificial_csv, change):
    rows, install = artificial_csv
    if change == "missing":
        rows.pop(0)
    elif change == "duplicate":
        rows.append(rows[0].copy())
    elif change == "wrong_pooling":
        rows[0]["pooling"] = "month_equal"
    else:
        rows[0]["model"] = "new_model"
    install(rows)
    with pytest.raises(ValueError, match="evidence|planning"):
        power_planning(config)


@pytest.mark.parametrize("field", ["cw_mean", "cw_se", "cw_t", "dm_se", "dm_mean"])
def test_ap2_detects_each_corrupted_field_even_with_recomputed_source_hash(config, artificial_csv, field):
    rows, install = artificial_csv
    rows[0][field] = str(float(rows[0][field])*1.01)
    install(rows)
    with pytest.raises(ValueError, match="AP2"):
        power_planning(config)


@pytest.mark.parametrize("field,value", [("ic_se", "nan"), ("dm_mean", "inf"),
                                        ("cw_se", "-1"), ("ic_mean", "0")])
def test_invalid_source_number_is_rejected(config, artificial_csv, field, value):
    rows, install = artificial_csv
    rows[2][field] = value
    install(rows)
    with pytest.raises(ValueError, match="finite|positive|nonzero"):
        power_planning(config)


def test_sidedness_critical_values_must_agree_with_alpha(config):
    config["power"]["z_two_sided"] = config["power"]["z_one_sided"]
    with pytest.raises(ValueError, match="two-sided z"):
        power_planning(config)


def test_development_role_name_is_not_split_into_endpoint(config):
    rows = power_planning(config)
    assert sum(row["role"] == "development_evaluation" for row in rows) == 6
    assert {row["test"] for row in rows} == {"CW", "DM", "IC"}


@pytest.mark.parametrize("index", range(4))
@pytest.mark.parametrize("field", ["cw_se", "cw_mean", "dm_se", "dm_mean", "ic_se", "ic_mean"])
def test_every_one_of_twenty_four_source_inputs_is_anchor_checked(config, artificial_csv, index, field):
    rows, install = artificial_csv
    rows[index][field] = str(float(rows[index][field])*1.01)
    install(rows)
    with pytest.raises(ValueError, match="Power anchor mismatch"):
        power_planning(config)


def test_frozen_markdown_display_table_matches_machine_values(config):
    planned = {row["key"]: row for row in power_planning(config)}
    matched = set()
    text = (ROOT / "docs/phase3a-analysis-plan.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 6 or cells[0] not in {"5", "15"} or cells[2] not in {"CW", "DM", "IC"}:
            continue
        key = "_".join(cells[:3])
        assert key not in matched
        matched.add(key)
        for field, cell in zip(("se_56", "mde_56", "pct_obs"), cells[3:], strict=True):
            assert float(cell.rstrip("%*")) == pytest.approx(planned[key][field], rel=1e-4, abs=0)
    assert matched == set(planned)


def test_duplicate_csv_column_is_rejected(config, artificial_csv):
    rows, install = artificial_csv
    install(rows, list(rows[0]) + ["cw_se"])
    with pytest.raises(ValueError, match="duplicate columns"):
        power_planning(config)
