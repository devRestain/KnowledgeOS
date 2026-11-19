"""Maintenance CLI: child job receipts, explicit reconciliation and source installs."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from test_hermes_adapter import cli_call, fake_calls, fake_config, make_fake

from knowledgeos.config import ROOT, load


def test_JOB_1_cli_job_run_reports_trigger_and_actual_child_execution(kos, vault):
    path = "40_Knowledge/Ideas/Scheduled.md"
    vault.write(path, "A scheduled idea.\n", age=timedelta(hours=1))
    code, output, error = kos.cli("job", "run", "standardize")
    assert code == 0, (output, error)
    report = json.loads(output)
    assert report["trigger"]["status"] == "completed"
    assert report["execution"]["status"] == "completed"
    assert report["execution"]["request_id"] == report["trigger"]["outcome"]["request_id"]
    assert report["execution"]["outcome"]["applied"] == [path]
    assert report["trigger"]["actor"] == "schedule/standardize"
    kos.run("job.pause", {"job": "standardize"})
    code, output, _ = kos.cli("job", "run", "standardize")
    assert code == 1
    report = json.loads(output)
    assert report["trigger"]["status"] == "refused" and report["execution"] is None


@pytest.mark.parametrize("resolution", ["applied", "not_applied"])
def test_SUR_4_cli_reconcile_requires_yes_and_preserves_original_unknown(tmp_path, vault, make_kos, resolution):
    original = {"unknown_id": "unknown-cli", "kind": "notice", "ref": {"notice_id": "old-notice"},
                "observed": "unknown", "recorded_at": "2026-11-01T09:00:00+09:00", "resolution": None}
    directory = tmp_path / "state/unknowns"
    directory.mkdir(parents=True)
    (directory / "unknown-cli.json").write_text(json.dumps(original))
    kos = make_kos()
    before = vault.snapshot()
    args = ("reconcile", "unknown-cli", "--resolution", resolution)
    code, _, error = kos.cli(*args)
    assert code == 2 and "--yes" in error
    assert kos.get("/api/unknowns")[1] == [original]
    code, output, error = kos.cli(*args, "--yes")
    assert code == 0, (output, error)
    receipt = json.loads(output)
    assert receipt["command"] == "outcome.reconcile" and receipt["status"] == "completed"
    [record] = kos.get("/api/unknowns")[1]
    assert record["resolution"] == resolution and record["resolved_by"] == "user/local"
    assert record["ref"] == original["ref"] and record["observed"] == "unknown"
    assert vault.snapshot() == before
    assert kos.cli(*args, "--yes")[0] == 1  # no second resolution or producer replay


def outside_runtime(tmp_path: Path):
    return {path.relative_to(tmp_path).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in tmp_path.rglob("*") if path.is_file() and not path.is_relative_to(tmp_path / "runtime")}


def test_SUR_5_install_dry_run_only_generates_inside_runtime(tmp_path, vault):
    binary = make_fake(tmp_path)
    config_file = fake_config(tmp_path, binary, vault.root, port=8765)
    before = outside_runtime(tmp_path)
    code, output, error = cli_call("install", "profiles", "--dry-run", "--config", str(config_file))
    assert code == 0, (output, error)
    report = json.loads(output)
    assert report["target"] == "profiles" and report["dry_run"] is True
    assert outside_runtime(tmp_path) == before
    assert not (tmp_path / "hermes-home").exists() and fake_calls(binary) == []
    distributions = tmp_path / "runtime/hermes/distributions"
    assert list(distributions.glob("*/distribution.yaml"))
    assert "needs credential" in output
    roles = json.loads((ROOT / "config/roles.json").read_text())["roles"]
    assert len(list(distributions.iterdir())) == len(roles) + 1
    assert (distributions / "kos-ops/gateway.parked").is_file()


def test_SUR_5_install_target_jobs_no_longer_exists(tmp_path, vault):
    binary = make_fake(tmp_path)
    config_file = fake_config(tmp_path, binary, vault.root)
    with pytest.raises(SystemExit) as refused:
        cli_call("install", "jobs", "--config", str(config_file))
    assert refused.value.code == 2
    assert fake_calls(binary) == [] and not (tmp_path / "runtime/hermes").exists()


def test_SUR_5_install_profiles_provisions_the_outbound_only_ops_profile(tmp_path, vault):
    binary = make_fake(tmp_path)
    config_file = fake_config(tmp_path, binary, vault.root)
    code, output, error = cli_call("install", "profiles", "--config", str(config_file))
    assert code == 0, (output, error)
    report = json.loads(output)
    assert report["operations"]["profile"] == "kos-ops" and report["operations"]["action"] == "install"
    assert report["operations"]["status"] == "done" and report["operations"]["credential"] == "needs credential"
    assert all(entry["profile"] != "kos-ops" for entry in report["profiles"])
    assert "scheduler" not in report and "jobs" not in report
    home = Path(load(config_file).settings["hermes"]["home"]) / "profiles/kos-ops"
    assert (home / "gateway.parked").is_file()
    settings = yaml.safe_load((home / "config.yaml").read_text())
    assert settings["platform_toolsets"] == {"cli": [], "acp": []} and "model" not in settings
    assert settings["skills"]["ledger"] is False and settings["curator"]["enabled"] is False
    assert not (home / "cron").exists() and not (home / "scripts").exists()
    assert not (home / "auth.json").exists() and not (home / ".env").exists()
    assert not [row for row in fake_calls(binary, "start") if "cron" in row["argv"]]
    code, output, error = cli_call("install", "profiles", "--config", str(config_file))
    assert code == 0, (output, error)
    assert json.loads(output)["operations"]["action"] == "update"
