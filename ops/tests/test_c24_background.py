from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path

from jsonschema import Draft202012Validator
from support.control_factory import (
    APPLICATION_CONTROL_INPUTS,
    fixture_path,
    make_control_root,
    make_separate_portable_fixture_roots,
)

from vaultops.background import (
    EXIT_CONFLICT,
    EXIT_INPUT_INVALID,
    EXIT_OK,
    configure_worker_log_streams,
    evaluate_frozen_baseline,
    evaluate_synthetic_wake,
    render_background_artifacts,
    run_worker,
    scheduled_worker_record,
    worker_once,
    worker_report_schema,
)
from vaultops.bridge_contract import canonical_json_bytes, remote_identity_sha256
from vaultops.cli import main
from vaultops.paths import resolve_api_paths
from vaultops.recovery import RecoveryJournal
from vaultops.runtime import RUNTIME_DIRECTORIES, STATE_DIRECTORIES

CONTROL_ROOT = Path(__file__).resolve().parents[2]
JOB_ID = "550e8400-e29b-41d4-a716-446655440000"
SOURCE_PATH = "00_Inbox/Captures/2026/09/c24-source.md"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repo}", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _fresh_control_copy(tmp_path: Path) -> Path:
    root = make_control_root(
        tmp_path,
        tuple(
            item
            for item in (*APPLICATION_CONTROL_INPUTS, "ops/tests/fixtures/c24_background")
            if item != "ops/vaultops.toml"
        ),
    )
    (root / "PROJECT_STATE.md").write_text(
        "/goal phase=history id=C36 state=complete\n"
        "/evidence id=E_C36_GATES class=runtime result=pass\n"
        "background activation stays disabled\n",
        encoding="utf-8",
    )
    _git(root, "init", "--initial-branch", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "KnowledgeOS Test")

    vault = fixture_path(root, "vault")
    for relative in (
        ".vault-bridge/requests/2026/09",
        ".vault-bridge/responses/2026/09",
        "00_Inbox/Captures/2026/09",
        "01_AI_Review/Pending",
    ):
        (vault / relative).mkdir(parents=True)
    runtime = fixture_path(root, "state")
    runtime.mkdir(mode=0o700, exist_ok=True)
    for relative in (
        "staging",
        "queue",
        "quarantine",
        "awaiting_remote_authorization",
        "running",
        "review",
        "approved",
        "applying",
        "done",
        "rejected",
        "expired",
        "failed",
        "conflict",
        "runs",
        "receipts",
        "locks",
        "index",
        "cache",
        "logs",
    ):
        (runtime / relative).mkdir(mode=0o700)

    for relative in RUNTIME_DIRECTORIES:
        (fixture_path(root, "runtime") / relative).mkdir(mode=0o700, exist_ok=True)

    sentinel = {
        "schema_version": 1,
        "contract_id": "knowledgeos-vault-root-v1",
        "vault_uuid": "411602c1-5278-4a8b-8b96-9183fb6ef8c2",
        "canonical_vault_name": "KnowledgeHub",
        "remote_identity_sha256": remote_identity_sha256(
            "https://github.com/example/knowledgehub"
        ),
        "expected_branch": "main",
    }
    (vault / ".knowledgeos-root.json").write_bytes(canonical_json_bytes(sentinel))
    (vault / SOURCE_PATH).write_text(
        "---\ntype: capture\n---\nc24 source\n", encoding="utf-8"
    )
    _git(vault, "init", "--initial-branch", "main")
    _git(vault, "config", "user.email", "test@example.invalid")
    _git(vault, "config", "user.name", "KnowledgeOS Test")
    _git(vault, "add", "--", ".knowledgeos-root.json", SOURCE_PATH)
    _git(vault, "commit", "-m", "seed c24 bridge vault")
    return root


def _commit_request(root: Path) -> Path:
    vault = fixture_path(root, "vault")
    source = vault / SOURCE_PATH
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    request = {
        "schema_version": 1,
        "job_id": JOB_ID,
        "created_at": "2026-09-17T12:00:00+09:00",
        "device_id": "c24-test-phone",
        "pipeline_kind": "triage",
        "source": {"path": SOURCE_PATH, "blob_sha256": source_digest},
        "target": None,
        "parameters": {},
        "route_id": "codex_chatgpt_login",
        "mode": "proposal",
    }
    request_path = vault / f".vault-bridge/requests/2026/09/{JOB_ID}.json"
    request_path.write_bytes(canonical_json_bytes(request))
    _git(vault, "add", "--", request_path.relative_to(vault).as_posix())
    _git(vault, "commit", "-m", "add c24 bridge request")
    return request_path


def test_worker_replays_committed_request_without_vault_or_network_mutation(
    tmp_path: Path,
) -> None:
    root = _fresh_control_copy(tmp_path)
    request_path = _commit_request(root)
    vault = fixture_path(root, "vault")
    head_before = _git(vault, "rev-parse", "HEAD")
    status_before = _git(vault, "status", "--porcelain")
    vault_before = {
        path.relative_to(vault).as_posix(): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file()
    }

    first, first_code = worker_once(root, wake_id="synthetic-wake", synthetic=True)
    second, second_code = worker_once(root, wake_id="synthetic-wake-replay", synthetic=True)

    assert first_code == EXIT_OK
    assert first["status"] == "PASS"
    assert first["requests"][0]["status"] == "PASS"
    assert first["requests"][0]["queue_write"] == "CREATED"
    assert second_code == EXIT_OK
    assert second["status"] == "PASS"
    assert second["requests"][0]["status"] == "NO_OP"
    assert second["requests"][0]["queue_write"] == "EXISTING"
    assert second["requests"][0]["path"] == request_path.relative_to(vault).as_posix()
    assert first["runtime_mutation_performed"] is True
    assert second["runtime_mutation_performed"] is False
    for report in (first, second):
        assert report["provider_called"] is False
        assert report["vault_mutated"] is False
        assert report["git_network_called"] is False
        assert report["mutation_performed"] is False
        assert list(Draft202012Validator(worker_report_schema()).iter_errors(report)) == []

    attempt_marker = (fixture_path(root, "state") / "worker/attempt.json")
    assert json.loads(attempt_marker.read_text(encoding="utf-8"))["status"] == "completed"

    assert _git(vault, "rev-parse", "HEAD") == head_before
    assert _git(vault, "status", "--porcelain") == status_before
    assert {
        path.relative_to(vault).as_posix(): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file()
    } == vault_before


def test_worker_refuses_request_count_over_configured_limit_before_ingestion(
    tmp_path: Path,
) -> None:
    root = _fresh_control_copy(tmp_path)
    vault = fixture_path(root, "vault")
    request_directory = vault / ".vault-bridge/requests/2026/09"
    for _ in range(101):
        request_id = str(uuid.uuid4())
        (request_directory / f"{request_id}.json").write_text("{}\n", encoding="utf-8")
    vault_before = {
        path.relative_to(vault).as_posix(): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file()
    }

    report, code = worker_once(
        root,
        wake_id="request-limit",
        synthetic=True,
    )

    assert code == EXIT_INPUT_INVALID
    assert report["status"] == "FAIL"
    assert report["requests"] == []
    assert "worker request limit exceeded: 101 > 100" in report["errors"][0]["message"]
    assert not any(((fixture_path(root, "state") / "queue")).iterdir())
    assert {
        path.relative_to(vault).as_posix(): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file()
    } == vault_before


def test_worker_and_bounded_logs_use_the_selected_vault_and_runtime_roots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    roots = make_separate_portable_fixture_roots(tmp_path)
    request_relative = ".vault-bridge/requests/2026/09/550e8400-e29b-41d4-a716-446655440001.json"
    request_path = roots.vault / request_relative
    request_path.parent.mkdir(parents=True)
    request_path.write_text("{}\n", encoding="utf-8")
    for relative in STATE_DIRECTORIES:
        directory = roots.state / relative
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
    for relative in RUNTIME_DIRECTORIES:
        directory = roots.runtime / relative
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)

    report, code = worker_once(
        roots,
        wake_id="separate-roots",
        synthetic=True,
        dry_run=True,
    )

    assert code == EXIT_OK
    assert report["status"] == "PASS"
    assert [item["path"] for item in report["requests"]] == [request_relative]
    assert report["requests"][0]["status"] == "NOT_RUN"
    assert report["runtime_mutation_performed"] is False
    assert (roots.control / "KnowledgeHub").exists() is False

    observed_logs: list[Path] = []

    def skip_host_stream_binding(_file_descriptor: int, path: Path) -> None:
        observed_logs.append(path)

    monkeypatch.setattr(
        "vaultops.background._prepare_bounded_log_fd",
        skip_host_stream_binding,
    )
    configure_worker_log_streams(roots)

    assert observed_logs == [
        roots.runtime / "logs/worker.stdout.log",
        roots.runtime / "logs/worker.stderr.log",
    ]


def test_synthetic_wake_evaluation_requires_replay_and_recovery_stability(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _commit_request(root)

    report, code = evaluate_synthetic_wake(root, wake_id="c24-evaluation")

    assert code == EXIT_OK
    assert report["status"] == "PASS"
    assert report["checks"] == {
        "replay_no_op": True,
        "recovery_stable": True,
        "provider_called": False,
        "vault_mutated": False,
        "git_network_called": False,
    }


def test_worker_reports_repairable_recovery_without_applying_it(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    vault = fixture_path(root, "vault")
    source_relative = "00_Inbox/Captures/2026/09/recovery-source.md"
    destination_relative = "90_Archive/Captures/2026/recovery-source.md"
    source = vault / source_relative
    destination = vault / destination_relative
    source.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = b"recovery source\n"
    destination_bytes = b"published destination\n"
    source.write_bytes(source_bytes)
    destination.write_bytes(destination_bytes)
    job_id = str(uuid.uuid4())
    RecoveryJournal(root, job_id=job_id, operation="capture_finalize").start(
        {
            "schema_version": 1,
            "source": source_relative,
            "destination": destination_relative,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "destination_sha256": hashlib.sha256(destination_bytes).hexdigest(),
        }
    )

    before = (source.read_bytes(), destination.read_bytes())
    report, code = worker_once(root, wake_id="recovery-wake", synthetic=True)

    assert code == EXIT_OK, report
    assert report["status"] == "REPAIR_REQUIRED"
    assert report["recovery_status"] == "REPAIR_REQUIRED"
    assert report["recovery"]["summary"] == {
        "jobs": 1,
        "complete": 0,
        "repairable": 1,
        "conflict": 0,
    }
    assert report["recovery"]["jobs"][0]["actions"] == ["remove_source_and_complete"]
    assert (source.read_bytes(), destination.read_bytes()) == before
    assert report["vault_mutated"] is False
    assert report["mutation_performed"] is False


def test_worker_fails_closed_on_recovery_conflict(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    vault = fixture_path(root, "vault")
    source_relative = "00_Inbox/Captures/2026/09/conflict-source.md"
    destination_relative = "90_Archive/Captures/2026/conflict-source.md"
    source = vault / source_relative
    destination = vault / destination_relative
    source.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = b"conflict source\n"
    destination_bytes = b"published destination\n"
    source.write_bytes(source_bytes)
    destination.write_bytes(destination_bytes)
    job_id = str(uuid.uuid4())
    RecoveryJournal(root, job_id=job_id, operation="capture_finalize").start(
        {
            "schema_version": 1,
            "source": source_relative,
            "destination": destination_relative,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "destination_sha256": hashlib.sha256(destination_bytes).hexdigest(),
        }
    )
    destination.write_bytes(b"tampered destination\n")
    before = (source.read_bytes(), destination.read_bytes())

    report, code = worker_once(root, wake_id="conflict-wake", synthetic=True)

    assert code == EXIT_CONFLICT
    assert report["status"] == "CONFLICT"
    assert report["recovery"]["summary"]["conflict"] == 1
    assert (source.read_bytes(), destination.read_bytes()) == before
    assert report["provider_called"] is False
    assert report["vault_mutated"] is False


def test_stale_worker_attempt_reconciles_before_ingesting_another_request(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    request_path = _commit_request(root)
    worker_directory = (fixture_path(root, "state") / "worker")
    worker_directory.mkdir(mode=0o700)
    marker = {
        "schema_version": 1,
        "attempt_id": "a" * 32,
        "owner": "manual",
        "status": "running",
        "started_at": "2026-09-28T10:00:00+00:00",
        "finished_at": None,
        "result_status": None,
    }
    marker_path = worker_directory / "attempt.json"
    marker_path.write_text(
        json.dumps(marker, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    marker_path.chmod(0o600)
    source = (fixture_path(root, "vault") / "00_Inbox/Captures/2026/09/stale-source.md")
    destination = (fixture_path(root, "vault") / "90_Archive/Captures/2026/stale-source.md")
    source.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = b"stale source\n"
    destination_bytes = b"published destination\n"
    source.write_bytes(source_bytes)
    destination.write_bytes(destination_bytes)
    RecoveryJournal(root, job_id=str(uuid.uuid4()), operation="capture_finalize").start(
        {
            "schema_version": 1,
            "source": "00_Inbox/Captures/2026/09/stale-source.md",
            "destination": "90_Archive/Captures/2026/stale-source.md",
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "destination_sha256": hashlib.sha256(destination_bytes).hexdigest(),
        }
    )
    vault_before = {
        path.relative_to(fixture_path(root, "vault")).as_posix(): path.read_bytes()
        for path in (fixture_path(root, "vault")).rglob("*")
        if path.is_file()
    }

    report, code = worker_once(root, wake_id="must-not-ingest", synthetic=True)

    assert code == EXIT_OK, report
    assert report["status"] == "REPAIR_REQUIRED"
    assert report["requests"] == []
    assert not list(((fixture_path(root, "state") / "queue")).glob("*.json"))
    assert json.loads((worker_directory / "attempt.json").read_text(encoding="utf-8"))["status"] == "needs_attention"
    assert (fixture_path(root, "vault")).is_dir()
    assert request_path.is_file()
    assert {
        path.relative_to(fixture_path(root, "vault")).as_posix(): path.read_bytes()
        for path in (fixture_path(root, "vault")).rglob("*")
        if path.is_file()
    } == vault_before


def test_worker_execution_lock_is_nonblocking(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    worker_directory = (fixture_path(root, "state") / "worker")
    worker_directory.mkdir(mode=0o700)
    lock_path = worker_directory / "worker.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        report, code = worker_once(root, wake_id="overlap", synthetic=True)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    assert code == EXIT_CONFLICT
    assert report["status"] == "CONFLICT"
    assert report["errors"][0]["code"] == "WORKER_ALREADY_RUNNING"
    assert not (worker_directory / "attempt.json").exists()


def test_manual_worker_blocks_an_unresolved_scheduled_attempt(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    request_path = _commit_request(root)
    worker_directory = (fixture_path(root, "state") / "worker")
    worker_directory.mkdir(mode=0o700)
    marker = {
        "schema_version": 1,
        "attempt_id": "c" * 32,
        "owner": "scheduler",
        "status": "running",
        "started_at": "2026-09-28T10:00:00+00:00",
        "finished_at": None,
        "result_status": None,
    }
    marker_path = worker_directory / "attempt.json"
    marker_path.write_text(
        json.dumps(marker, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    marker_path.chmod(0o600)

    report, code = worker_once(root, wake_id="manual-overlap", synthetic=True)

    assert code == EXIT_CONFLICT
    assert report["status"] == "CONFLICT"
    assert report["errors"][0]["code"] == "SCHEDULED_ATTEMPT_PENDING"
    assert request_path.is_file()
    assert not list(((fixture_path(root, "state") / "queue")).glob("*.json"))


def test_scheduled_worker_result_is_compact_and_maps_repair_to_attention() -> None:
    record = scheduled_worker_record(
        {
            "status": "REPAIR_REQUIRED",
            "operation": "ai worker",
            "wake": {"id": "private-wake-marker"},
            "requests": [{"path": ".vault-bridge/private.json"}],
            "request_count": 1,
            "recovery_status": "REPAIR_REQUIRED",
            "recovery": {"summary": {"jobs": 1, "repairable": 1, "private": "secret"}},
            "errors": [{"message": "private report text"}],
        },
        EXIT_OK,
    )
    encoded = json.dumps(record, separators=(",", ":"))
    assert record["outcome"] == "needs_attention"
    assert record["exit_code"] == EXIT_OK
    assert record["delivery"] == "local"
    assert record["agent_reasoning"] is False
    assert record["recovery_summary"] == {"jobs": 1, "repairable": 1}
    assert "private" not in encoded
    assert "secret" not in encoded


def test_scheduled_cli_uses_sanitized_stdout_without_launchd_streams(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    root = _fresh_control_copy(tmp_path)
    monkeypatch.setattr("vaultops.cli.resolve_paths", lambda _root=None: resolve_api_paths(root))
    scheduler_attempt_id = "b" * 32
    worker_directory = (fixture_path(root, "state") / "worker")
    worker_directory.mkdir(mode=0o700)
    marker = {
        "schema_version": 1,
        "attempt_id": scheduler_attempt_id,
        "owner": "scheduler",
        "status": "running",
        "started_at": "2026-09-28T10:00:00+00:00",
        "finished_at": None,
        "result_status": None,
    }
    marker_path = worker_directory / "attempt.json"
    marker_path.write_text(
        json.dumps(marker, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    marker_path.chmod(0o600)
    monkeypatch.setenv("KNOWLEDGEOS_SCHEDULED_ATTEMPT_ID", scheduler_attempt_id)
    monkeypatch.setattr(
        "vaultops.cli.configure_worker_log_streams",
        lambda _root: (_ for _ in ()).throw(AssertionError("scheduled output must not use launchd fds")),
    )

    code = main(["ai", "worker", "--scheduled-report"])

    output = capsys.readouterr().out
    record = json.loads(output)
    assert code == EXIT_OK
    assert record["status"] == "PASS"
    assert record["outcome"] == "completed"
    assert record["delivery"] == "local"
    assert record["provider_called"] is False
    assert "requests" not in record


def test_scheduled_cli_rejects_consumer_selected_roots_and_modes(capsys) -> None:
    assert main(["ai", "worker", "--scheduled-report", "--wake-id", "caller-controlled"]) == 10
    record = json.loads(capsys.readouterr().out)
    assert record["status"] == "FAIL"
    assert record["mutation_performed"] is False
    assert record["canonical_target_changed"] is False


def test_c24_artifacts_and_cli_keep_launchd_inactive(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    root = _fresh_control_copy(tmp_path)
    executable = tmp_path / "vaultctl"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(
        "vaultops.launchd.os.access",
        lambda path, mode: Path(path) == executable and mode == 1,
    )
    launchd_path = tmp_path / "LaunchAgents" / "com.knowledgeos.vaultops.plist"
    launchd_path.parent.mkdir()

    artifacts, artifact_code = render_background_artifacts(root)
    assert artifact_code == EXIT_OK
    assert artifacts["launchd_active"] is False

    baseline, baseline_code = evaluate_frozen_baseline(
        root,
        baseline_path=root / "ops/tests/fixtures/c24_background/evaluation.yaml",
    )
    assert baseline_code == EXIT_OK
    assert baseline["status"] == "PASS"
    assert baseline["metrics"] == {"cases": 1, "all_cases_passed": True}

    assert main(["ai", "worker", "--evaluation-file", str(root / "ops/tests/fixtures/c24_background/evaluation.yaml"), "--root", str(root)]) == EXIT_OK
    cli_evaluation = json.loads(capsys.readouterr().out)
    assert cli_evaluation["status"] == "PASS"

    assert (
        main(
            [
                "launchd",
                "install",
                "--dry-run",
                "--executable",
                str(executable),
                "--install-path",
                str(launchd_path),
                "--root",
                str(root),
            ]
        )
        == EXIT_OK
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["installed"] is False
    assert preview["launchd_active"] is False

    assert (
        main(
            [
                "launchd",
                "install",
                "--executable",
                str(executable),
                "--install-path",
                str(launchd_path),
                "--root",
                str(root),
            ]
        )
        == EXIT_CONFLICT
    )
    deferred = json.loads(capsys.readouterr().out)
    assert deferred["status"] == "CONFLICT"
    assert deferred["errors"][0]["code"] == "LAUNCHD_ACTIVATION_REQUIRED"
    assert deferred["launchd_active"] is False


def test_bounded_watch_returns_after_one_initial_cycle(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)

    report, code = run_worker(root, once=False, max_cycles=1, wake_id="bounded-watch")

    assert code == EXIT_OK
    assert report["status"] == "PASS"
    assert report["mode"] == "watch"
    assert report["wake_count"] == 1
