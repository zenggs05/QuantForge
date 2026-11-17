"""CLI errors must remain understandable without misreporting download status."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import quantforge.data as data_module


@pytest.fixture
def cli_module():
    script = Path(__file__).resolve().parents[1] / "scripts/download_sample.py"
    spec = importlib.util.spec_from_file_location("download_sample_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_rejects_configuration_escape_cleanly(cli_module, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["download_sample.py", "--config", "../outside.toml"])
    with pytest.raises(SystemExit) as error:
        cli_module.main()
    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert "Invalid repository-local sample configuration" in stderr
    assert "Traceback" not in stderr


def test_cli_failure_does_not_infer_download_failure(cli_module, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(data_module, "REPOSITORY_ROOT", tmp_path)
    config = tmp_path / "sample.toml"
    config.write_text('[sample]\nmarket="spot"\nsymbol="BTCUSDT"\ninterval="1m"\ndate="2026-09-01"\n', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["download_sample.py", "--config", str(config)])

    def fail_after_download(day):
        raise OSError("Simulated Parquet write failure after successful checksum verification")

    monkeypatch.setattr(cli_module, "download_day", fail_after_download)
    assert cli_module.main() == 1
    failure = json.loads(capsys.readouterr().err)
    assert failure["end_to_end_validation_completed"] is False
    assert "real_data_download_verified" not in failure
    persisted = tmp_path / "results/data-validation/BTCUSDT-1m-2026-09-01-failure.json"
    assert json.loads(persisted.read_text(encoding="utf-8")) == failure
