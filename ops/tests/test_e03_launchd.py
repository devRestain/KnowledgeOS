from __future__ import annotations

import os
import plistlib
import stat
import subprocess
import sys
import time
from pathlib import Path

from support.control_factory import fixture_path, make_separate_portable_fixture_roots
from test_c24_background import _fresh_control_copy

from vaultops import launchd
from vaultops.background import (
    WORKER_LOG_MAX_AGE_SECONDS,
    WORKER_LOG_MAX_BYTES,
    WORKER_LOG_RETAIN_BYTES,
    worker_log_record,
)
from vaultops.launchd import EXIT_CONFLICT, EXIT_OK
from vaultops.runtime import RUNTIME_DIRECTORIES, STATE_DIRECTORIES


def _host_executable(tmp_path: Path) -> Path:
    executable = tmp_path / "vaultctl"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    return executable


def _install_path(tmp_path: Path) -> Path:
    directory = tmp_path / "LaunchAgents"
    directory.mkdir()
    return directory / f"{launchd.LAUNCHD_LABEL}.plist"


def test_e03_dry_run_binds_exact_root_and_requires_explicit_activation(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    executable = _host_executable(tmp_path)
    install_path = _install_path(tmp_path)

    preview, preview_code = launchd.install(
        root,
        dry_run=True,
        executable=executable,
        install_path=install_path,
    )

    assert preview_code == EXIT_OK
    assert preview["status"] == "PASS"
    assert preview["existing_plist"] == "absent"
    assert preview["would_write_plist"] is True
    assert preview["bound_control_root"] == str(root.resolve())
    assert preview["bound_executable"] == str(executable.resolve())
    assert preview["c36_gate"] == {
        "c36_complete": True,
        "c36_gates_pass": True,
        "c36_background_inactive": True,
    }
    assert not install_path.exists()
    deferred, deferred_code = launchd.install(
        root,
        executable=executable,
        install_path=install_path,
    )

    assert deferred_code == EXIT_CONFLICT
    assert deferred["errors"][0]["code"] == "LAUNCHD_ACTIVATION_REQUIRED"
    assert not install_path.exists()


def test_e03_dry_run_names_each_selected_root_and_runtime_log_path(tmp_path: Path) -> None:
    roots = make_separate_portable_fixture_roots(
        tmp_path,
        extra_inputs=(
            "ops/config/background.yaml",
            "ops/launchd/com.knowledgeos.vaultops.plist",
            "ops/schemas/worker-report.schema.json",
        ),
    )
    (roots.control / "PROJECT_STATE.md").write_text(
        "/goal phase=history id=C36 state=complete\n"
        "/evidence id=E_C36_GATES class=runtime result=pass\n"
        "background activation stays disabled\n",
        encoding="utf-8",
    )
    for relative in STATE_DIRECTORIES:
        directory = roots.state / relative
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
    for relative in RUNTIME_DIRECTORIES:
        directory = roots.runtime / relative
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
    executable = _host_executable(tmp_path)
    install_path = _install_path(tmp_path)

    preview, preview_code = launchd.install(
        roots.control,
        dry_run=True,
        executable=executable,
        install_path=install_path,
    )

    assert preview_code == EXIT_OK
    assert preview["status"] == "PASS"
    assert preview["resolved_roots"] == {
        "core": str(roots.core),
        "state": str(roots.state),
        "control": str(roots.control),
        "vault": str(roots.vault),
        "runtime": str(roots.runtime),
    }
    configuration = preview["configuration_preview"]
    assert configuration["working_directory"] == str(roots.control)
    assert configuration["program_arguments"][-2:] == ["--root", str(roots.control)]
    assert configuration["environment_roots"] == {
        "KNOWLEDGEOS_CORE_ROOT": str(roots.core),
        "KNOWLEDGEOS_STATE_ROOT": str(roots.state),
        "KNOWLEDGEOS_CONTROL_ROOT": str(roots.control),
        "KNOWLEDGEOS_VAULT_ROOT": str(roots.vault),
        "KNOWLEDGEOS_RUNTIME_ROOT": str(roots.runtime),
    }
    assert configuration["stdout_path"] == str(roots.runtime / "logs/worker.stdout.log")
    assert configuration["stderr_path"] == str(roots.runtime / "logs/worker.stderr.log")
    assert not install_path.exists()

def test_e03_activation_is_idempotent_and_rollback_is_exact(tmp_path: Path, monkeypatch) -> None:
    root = _fresh_control_copy(tmp_path)
    executable = _host_executable(tmp_path)
    install_path = _install_path(tmp_path)
    active = False
    calls: list[tuple[str, ...]] = []

    def fake_launchctl(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        nonlocal active
        calls.append(arguments)
        if arguments[0] == "print":
            return subprocess.CompletedProcess(
                ["launchctl", *arguments],
                0 if active else 113,
                stdout="active" if active else "",
                stderr="" if active else "Could not find service",
            )
        if arguments[0] == "bootstrap":
            active = True
            return subprocess.CompletedProcess(["launchctl", *arguments], 0, stdout="", stderr="")
        if arguments[0] == "bootout":
            active = False
            return subprocess.CompletedProcess(["launchctl", *arguments], 0, stdout="", stderr="")
        raise AssertionError(arguments)

    monkeypatch.setattr(launchd, "_require_host_launchd", lambda: None)
    monkeypatch.setattr(launchd, "_run_launchctl", fake_launchctl)

    installed, installed_code = launchd.install(
        root,
        activate=True,
        executable=executable,
        install_path=install_path,
    )

    assert installed_code == EXIT_OK
    assert installed["status"] == "PASS"
    assert installed["activation"] == "bootstrapped"
    assert installed["launchd_active"] is True
    assert installed["mutation_performed"] is True
    assert install_path.is_file()
    assert stat.S_IMODE(install_path.stat().st_mode) == 0o600
    document = plistlib.loads(install_path.read_bytes())
    assert document["ProgramArguments"] == [
        str(executable.resolve()),
        "ai",
        "worker",
        "--once",
        "--root",
        str(root.resolve()),
    ]
    assert document["KnowledgeOS"]["installation"] == "E03"
    assert document["KnowledgeOS"]["bound_control_root"] == str(root.resolve())
    assert document["KnowledgeOS"]["provider_called"] is False

    replay, replay_code = launchd.install(
        root,
        activate=True,
        executable=executable,
        install_path=install_path,
    )
    assert replay_code == EXIT_OK
    assert replay["activation"] == "already_active"
    assert replay["mutation_performed"] is False

    preview, preview_code = launchd.rollback(root, install_path=install_path)
    assert preview_code == EXIT_OK
    assert preview["would_bootout"] is True
    assert install_path.is_file()

    removed, removed_code = launchd.rollback(root, apply=True, install_path=install_path)
    assert removed_code == EXIT_OK
    assert removed["status"] == "PASS"
    assert removed["installed"] is False
    assert removed["launchd_active"] is False
    assert removed["mutation_performed"] is True
    assert not install_path.exists()
    assert any(call[0] == "bootstrap" for call in calls)
    assert any(call[0] == "bootout" for call in calls)


def test_e03_rejects_foreign_bytes_and_cleans_up_failed_bootstrap(tmp_path: Path, monkeypatch) -> None:
    root = _fresh_control_copy(tmp_path)
    executable = _host_executable(tmp_path)
    install_path = _install_path(tmp_path)
    install_path.write_bytes(b"foreign plist bytes\n")

    conflict, conflict_code = launchd.install(
        root,
        dry_run=True,
        executable=executable,
        install_path=install_path,
    )
    assert conflict_code == EXIT_CONFLICT
    assert conflict["errors"][0]["code"] == "LAUNCHD_PLIST_CONFLICT"
    assert install_path.read_bytes() == b"foreign plist bytes\n"

    install_path.unlink()
    calls: list[tuple[str, ...]] = []

    def failed_launchctl(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if arguments[0] == "print":
            return subprocess.CompletedProcess(
                ["launchctl", *arguments],
                113,
                stdout="",
                stderr="Could not find service",
            )
        if arguments[0] == "bootstrap":
            return subprocess.CompletedProcess(
                ["launchctl", *arguments],
                1,
                stdout="",
                stderr="synthetic bootstrap failure",
            )
        raise AssertionError(arguments)

    monkeypatch.setattr(launchd, "_require_host_launchd", lambda: None)
    monkeypatch.setattr(launchd, "_run_launchctl", failed_launchctl)
    failed, failed_code = launchd.install(
        root,
        activate=True,
        executable=executable,
        install_path=install_path,
    )

    assert failed_code == EXIT_CONFLICT
    assert failed["errors"][0]["code"] == "LAUNCHD_ACTIVATION_FAILED"
    assert not install_path.exists()
    assert any(call[0] == "bootstrap" for call in calls)


def test_e03_rollback_refuses_tampered_owned_plist(tmp_path: Path, monkeypatch) -> None:
    root = _fresh_control_copy(tmp_path)
    executable = _host_executable(tmp_path)
    install_path = _install_path(tmp_path)
    active = False

    def fake_launchctl(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        nonlocal active
        if arguments[0] == "print":
            return subprocess.CompletedProcess(
                ["launchctl", *arguments],
                0 if active else 113,
                stdout="" if not active else "active",
                stderr="Could not find service" if not active else "",
            )
        if arguments[0] == "bootstrap":
            active = True
            return subprocess.CompletedProcess(["launchctl", *arguments], 0, stdout="", stderr="")
        if arguments[0] == "bootout":
            active = False
            return subprocess.CompletedProcess(["launchctl", *arguments], 0, stdout="", stderr="")
        raise AssertionError(arguments)

    monkeypatch.setattr(launchd, "_require_host_launchd", lambda: None)
    monkeypatch.setattr(launchd, "_run_launchctl", fake_launchctl)
    installed, installed_code = launchd.install(
        root,
        activate=True,
        executable=executable,
        install_path=install_path,
    )
    assert installed_code == EXIT_OK, installed

    install_path.write_bytes(install_path.read_bytes() + b"tampered")
    refused, refused_code = launchd.rollback(root, apply=True, install_path=install_path)
    assert refused_code == EXIT_CONFLICT
    assert refused["errors"][0]["code"] == "E03_ROLLBACK_CONFLICT"
    assert install_path.is_file()


def test_worker_log_record_is_timestamped_and_does_not_copy_request_content() -> None:
    record = worker_log_record(
        {
            "status": "PASS",
            "operation": "ai worker",
            "wake": {"id": "wake-1", "kind": "invocation", "synthetic": False},
            "requests": [{"path": "private/source.md", "status": "PASS", "state": "queued"}],
            "recovery": {"status": "PASS", "summary": {"jobs": 0}},
            "provider_called": False,
            "vault_mutated": False,
            "git_network_called": False,
        },
        0,
        logged_at="2026-09-19T17:00:00+09:00",
    )

    assert record["logged_at"] == "2026-09-19T17:00:00+09:00"
    assert record["request_count"] == 1
    assert "requests" not in record
    assert "private/source.md" not in str(record)


def test_launchd_bound_worker_logs_discard_legacy_bytes_and_stay_bounded(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    logs = fixture_path(root, "runtime") / "logs"
    stdout_path = logs / "worker.stdout.log"
    stderr_path = logs / "worker.stderr.log"
    stdout_path.write_bytes(b"legacy unbounded report\n")
    stderr_path.write_bytes(b"legacy stderr\n")

    script = """
import sys
from pathlib import Path
from vaultops.background import configure_worker_log_streams

root = Path(sys.argv[1])
if not configure_worker_log_streams(root):
    raise SystemExit(3)
print('new-record\\n' * 10000, end='')
print('new-error\\n' * 10000, file=sys.stderr, end='')
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    with stdout_path.open("r+b") as stdout, stderr_path.open("r+b") as stderr:
        stdout.seek(0, 2)
        stderr.seek(0, 2)
        result = subprocess.run(
            [sys.executable, "-c", script, str(root)],
            cwd=root,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            check=False,
        )

    assert result.returncode == 0
    stdout_bytes = stdout_path.read_bytes()
    stderr_bytes = stderr_path.read_bytes()
    assert len(stdout_bytes) <= WORKER_LOG_MAX_BYTES
    assert len(stderr_bytes) <= WORKER_LOG_MAX_BYTES
    assert len(stdout_bytes) >= WORKER_LOG_RETAIN_BYTES
    assert len(stderr_bytes) >= WORKER_LOG_RETAIN_BYTES
    assert stdout_bytes.startswith(b"# KnowledgeOS worker log v1\n")
    assert stderr_bytes.startswith(b"# KnowledgeOS worker log v1\n")
    assert b"legacy" not in stdout_bytes
    assert b"legacy" not in stderr_bytes
    assert b"new-record" in stdout_bytes
    assert b"new-error" in stderr_bytes


def test_launchd_bound_worker_logs_discard_stale_tail(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    logs = fixture_path(root, "runtime") / "logs"
    stdout_path = logs / "worker.stdout.log"
    stderr_path = logs / "worker.stderr.log"
    for path in (stdout_path, stderr_path):
        path.write_bytes(b"# KnowledgeOS worker log v1\nold-summary\n")
        stale_at = time.time() - WORKER_LOG_MAX_AGE_SECONDS - 1
        os.utime(path, (stale_at, stale_at))

    script = """
import sys
from pathlib import Path
from vaultops.background import configure_worker_log_streams

root = Path(sys.argv[1])
if not configure_worker_log_streams(root):
    raise SystemExit(3)
print('fresh-summary')
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    with stdout_path.open("r+b") as stdout, stderr_path.open("r+b") as stderr:
        stdout.seek(0, 2)
        stderr.seek(0, 2)
        result = subprocess.run(
            [sys.executable, "-c", script, str(root)],
            cwd=root,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            check=False,
        )

    assert result.returncode == 0
    assert b"old-summary" not in stdout_path.read_bytes()
    assert b"old-summary" not in stderr_path.read_bytes()
    assert b"fresh-summary" in stdout_path.read_bytes()
