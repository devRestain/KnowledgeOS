from __future__ import annotations

from pathlib import Path

import pytest

from vaultops.obsidian_status import (
    EXIT_DEGRADED,
    FIXED_ARGV,
    _collect_status,
    _ProbeResult,
)

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def _run_probe(result: _ProbeResult):
    return lambda _path: result


def test_status_adapter_uses_one_fixed_version_probe_and_does_not_infer_device_connection() -> None:
    report, exit_code = _collect_status(
        CONTROL_ROOT,
        executable_resolver=lambda _name: "/private/fake/obsidian",
        probe=_run_probe(_ProbeResult(0, b"Obsidian 1.13.7\n", b"")),
    )

    assert exit_code == 0
    assert report["status"] == "PASS"
    assert report["adapter"]["fixed_argv"] == list(FIXED_ARGV) == ["version"]
    assert report["adapter"]["shell"] is False
    assert report["adapter"]["state"] == "version_verified"
    assert report["adapter"]["version"] == "1.13.7"
    assert report["adapter"]["app_connection"]["state"] == "not_run"
    assert report["adapter"]["capabilities"]["raw_passthrough"] is False
    assert report["adapter"]["capabilities"]["document_operations"] is False


@pytest.mark.parametrize(
    ("probe", "state", "code"),
    (
        (
            _ProbeResult(1, b"", b"Obsidian app is not running\n"),
            "app_not_running",
            "OBSIDIAN_APP_NOT_RUNNING",
        ),
        (_ProbeResult(0, b"not a version\n", b""), "malformed_output", "OBSIDIAN_CLI_OUTPUT_INVALID"),
        (
            _ProbeResult(0, b"Obsidian 1.13.7\nCapabilities: read,version\n", b""),
            "unexpected_capability",
            "OBSIDIAN_CLI_CAPABILITY_UNEXPECTED",
        ),
        (
            _ProbeResult(0, b"Obsidian 1.13.7\nVault: Other\n", b""),
            "vault_mismatch",
            "OBSIDIAN_VAULT_MISMATCH",
        ),
        (_ProbeResult(0, b"", b"", timed_out=True), "timeout", "OBSIDIAN_CLI_TIMEOUT"),
        (_ProbeResult(0, b"x" * 4097, b"", output_limited=True), "output_limit", "OBSIDIAN_CLI_OUTPUT_LIMIT"),
    ),
)
def test_status_adapter_fails_closed_for_untrusted_probe_states(
    probe: _ProbeResult, state: str, code: str
) -> None:
    report, exit_code = _collect_status(
        CONTROL_ROOT,
        executable_resolver=lambda _name: "/private/fake/obsidian",
        probe=_run_probe(probe),
    )

    assert exit_code == EXIT_DEGRADED
    assert report["status"] == "DEGRADED"
    assert report["adapter"]["state"] == state
    assert report["errors"][0]["code"] == code


def test_status_adapter_reports_missing_path_without_attempting_a_command() -> None:
    called = False

    def fail_if_called(_path: str) -> _ProbeResult:
        nonlocal called
        called = True
        raise AssertionError("missing PATH must not spawn a process")

    report, exit_code = _collect_status(
        CONTROL_ROOT,
        executable_resolver=lambda _name: None,
        probe=fail_if_called,
    )

    assert exit_code == EXIT_DEGRADED
    assert report["adapter"]["state"] == "missing_path"
    assert report["errors"][0]["code"] == "OBSIDIAN_CLI_NOT_FOUND"
    assert called is False
