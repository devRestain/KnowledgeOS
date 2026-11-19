"""C11 Owner lifecycle: the ``install service`` LaunchAgent and the Thin Client start/stop surface.

``launchctl`` is a fake executable on PATH that records its argv. No real launchd, Hermes,
Obsidian, model or credential is used.
"""
from __future__ import annotations

import inspect
import json
import os
import plistlib
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from test_c11_thin_client import assert_passed, run_node
from test_hermes_adapter import cli_call, fake_config, make_fake

from knowledgeos import cli
from knowledgeos.config import ROOT

LABEL = "com.agentfabric.knowledgeos.owner"


# Written into tmp_path as a Python executable named ``launchctl``.
def launchctl_main():
    import json
    import sys
    from pathlib import Path

    root = Path(__file__).parent
    with (root / "calls.jsonl").open("a") as stream:
        stream.write(json.dumps(sys.argv[1:]) + "\n")
    codes = json.loads((root / "codes.json").read_text()) if (root / "codes.json").exists() else {}
    return codes.get(sys.argv[1], 0 if sys.argv[1] == "bootstrap" else 113)


@pytest.fixture
def launchctl(tmp_path, monkeypatch):
    """A recording ``launchctl`` first on PATH and an isolated HOME; returns ``(calls, set_codes)``."""
    directory = tmp_path / "fake-launchctl"
    directory.mkdir()
    binary = directory / "launchctl"
    binary.write_text(f"#!{sys.executable}\n" + inspect.getsource(launchctl_main) + "\nraise SystemExit(launchctl_main())\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HERMES_HOME", raising=False)

    def calls() -> list[list[str]]:
        path = directory / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def set_codes(**codes: int) -> None:
        (directory / "codes.json").write_text(json.dumps(codes))

    return calls, set_codes


def snapshot(root: Path) -> dict[str, bytes | None]:
    return {path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None for path in root.rglob("*")}


def source_tree() -> str:
    return str(Path(cli.__file__).resolve().parents[1])


def test_SUR_7_install_service_dry_run_prints_plist_and_argv_and_writes_nothing(tmp_path, vault, launchctl):
    calls, _ = launchctl
    binary = make_fake(tmp_path)
    config_file = fake_config(tmp_path, binary, vault.root)
    before = snapshot(tmp_path)
    code, output, error = cli_call("install", "service", "--dry-run", "--config", str(config_file))
    assert code == 0, (output, error)
    report = json.loads(output)
    plist_path = tmp_path / "home/Library/LaunchAgents" / f"{LABEL}.plist"
    assert report["target"] == "service" and report["dry_run"] is True and report["ok"] is True
    assert report["label"] == LABEL and report["plist_path"] == str(plist_path)
    plist = report["plist"]
    assert plist["Label"] == LABEL
    assert plist["ProgramArguments"] == [
        os.path.abspath(sys.executable), "-m", "knowledgeos.cli", "serve", "--config", str(config_file.resolve()),
    ]
    assert plist["RunAtLoad"] is False and plist["KeepAlive"] is False and plist["ProcessType"] == "Interactive"
    runtime = (tmp_path / "runtime").resolve()
    assert plist["StandardOutPath"] == str(runtime / "logs/owner.out.log")
    assert plist["StandardErrorPath"] == str(runtime / "logs/owner.err.log")
    environment = plist["EnvironmentVariables"]
    assert environment["HERMES_HOME"] == str(tmp_path / "hermes-home")
    entries = environment["PATH"].split(os.pathsep)
    assert entries[0] == str(binary.parent) and "/usr/bin" in entries and len(entries) == len(set(entries))
    assert environment["PYTHONPATH"] == source_tree()
    uid = os.getuid()
    assert [step["argv"] for step in report["launchctl"]] == [
        ["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
    ]
    assert snapshot(tmp_path) == before, "dry run changed the filesystem"
    assert not plist_path.exists() and not (runtime / "logs").exists() and calls() == []


def test_SUR_7_install_service_writes_plist_and_loads_it_without_starting(tmp_path, vault, launchctl):
    calls, set_codes = launchctl
    config_file = fake_config(tmp_path, make_fake(tmp_path), vault.root)
    plist_path = tmp_path / "agents/test.owner.plist"
    args = ("install", "service", "--config", str(config_file), "--plist", str(plist_path), "--label", "test.owner")
    uid = os.getuid()

    code, output, error = cli_call(*args)
    assert code == 0, (output, error)
    report = json.loads(output)
    assert report["ok"] is True and report["loaded"] is True and report["was_loaded"] is False
    written = plistlib.loads(plist_path.read_bytes())
    assert written == report["plist"] and written["Label"] == "test.owner"
    assert written["RunAtLoad"] is False and written["KeepAlive"] is False
    assert plist_path.stat().st_mode & 0o777 == 0o644
    assert (tmp_path / "runtime/logs").is_dir()
    domain = f"gui/{uid}"
    assert calls() == [["bootout", f"{domain}/test.owner"], ["bootstrap", domain, str(plist_path.resolve())]]
    assert [step["returncode"] for step in report["launchctl"]] == [113, 0]

    # Reinstall replaces the loaded service; nothing is ever kickstarted or started.
    set_codes(bootout=0)
    code, output, error = cli_call(*args)
    assert code == 0, (output, error)
    assert json.loads(output)["was_loaded"] is True
    assert plistlib.loads(plist_path.read_bytes()) == written
    assert {call[0] for call in calls()} == {"bootout", "bootstrap"} and len(calls()) == 4


def test_SUR_7_install_service_failures_are_reported_not_hidden(tmp_path, vault, launchctl, monkeypatch):
    calls, set_codes = launchctl
    config_file = fake_config(tmp_path, make_fake(tmp_path), vault.root)
    plist_path = tmp_path / "agents/owner.plist"
    args = ("install", "service", "--config", str(config_file), "--plist", str(plist_path))

    set_codes(bootout=5)
    code, output, _ = cli_call(*args)
    report = json.loads(output)
    assert code == 1 and report["ok"] is False
    assert [call[0] for call in calls()] == ["bootout"], "an unexplained bootout failure must not continue to bootstrap"

    set_codes(bootout=113, bootstrap=5)
    code, output, _ = cli_call(*args)
    report = json.loads(output)
    assert code == 1 and report["ok"] is False and report["loaded"] is False

    before_calls = len(calls())
    plist_path.unlink()
    code, _, error = cli_call("install", "service", "--config", str(config_file), "--plist", str(plist_path), "--label", "bad/label")
    assert code == 2 and "label" in error
    code, _, error = cli_call("install", "profiles", "--config", str(config_file), "--label", "x")
    assert code == 2 and "install service" in error
    assert not plist_path.exists() and len(calls()) == before_calls

    empty = tmp_path / "no-launchctl"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    code, _, error = cli_call(*args)
    assert code == 2 and "launchctl" in error
    assert not plist_path.exists()


def test_SUR_7_install_service_without_hermes_takes_home_from_the_environment(tmp_path, vault, launchctl, monkeypatch):
    config_file = tmp_path / "owner.json"
    config_file.write_text(json.dumps({
        "vault": str(vault.root), "state": str(tmp_path / "state"), "runtime": str(tmp_path / "runtime"), "root": str(ROOT),
    }))
    args = ("install", "service", "--dry-run", "--config", str(config_file))

    code, output, error = cli_call(*args)
    assert code == 0, (output, error)
    assert "HERMES_HOME" not in json.loads(output)["plist"]["EnvironmentVariables"]

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env-home"))
    code, output, error = cli_call(*args)
    assert code == 0, (output, error)
    assert json.loads(output)["plist"]["EnvironmentVariables"]["HERMES_HOME"] == str(tmp_path / "env-home")


def test_SUR_6_owner_serve_stops_cleanly_on_sigterm(tmp_path, vault, temporal_settings):
    config_file = tmp_path / "owner.json"
    config_file.write_text(json.dumps({
        "vault": str(vault.root), "state": str(tmp_path / "state"), "runtime": str(tmp_path / "runtime"),
        "root": str(ROOT), "port": 0, "settings": {"runtime": dict(temporal_settings)},
    }))
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    process = subprocess.Popen([sys.executable, "-m", "knowledgeos.cli", "serve", "--config", str(config_file)],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = process.stdout.readline()
        assert line.startswith("KnowledgeOS ExOps listening on "), line
        client = cli.Client(line.strip().split()[-1], timeout=5)
        status, health = client.call("GET", "/api/health")
        assert status == 200 and health["healthy"] is True
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=30) == 0
        with pytest.raises(cli.CliError):
            client.call("GET", "/api/health")
    finally:
        if process.poll() is None:
            process.kill()
        _, stderr = process.communicate()
    assert "Traceback" not in stderr, stderr


def test_SUR_6_thin_client_owner_start_stop_follow_launchd_and_report_observed_health():
    assert_passed(
        run_node("test_SUR_6_thin_client_owner_start_stop_follow_launchd_and_report_observed_health"), expected=1
    )
