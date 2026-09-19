from __future__ import annotations

import plistlib
import stat
import subprocess
from pathlib import Path

from test_c24_background import _fresh_control_copy

from vaultops import launchd
from vaultops.launchd import EXIT_CONFLICT, EXIT_OK


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
