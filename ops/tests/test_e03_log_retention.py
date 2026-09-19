from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from test_c24_background import _fresh_control_copy

from vaultops.background import (
    WORKER_LOG_MAX_AGE_SECONDS,
    WORKER_LOG_MAX_BYTES,
    WORKER_LOG_RETAIN_BYTES,
    worker_log_record,
)


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
    logs = root / "runtime" / "logs"
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
    environment["PYTHONPATH"] = str(root / "ops" / "src")
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
    logs = root / "runtime" / "logs"
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
    environment["PYTHONPATH"] = str(root / "ops" / "src")
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
