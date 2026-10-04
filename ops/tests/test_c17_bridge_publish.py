from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from support.control_factory import (
    APPLICATION_CONTROL_INPUTS,
    fixture_path,
    make_control_root,
    make_separate_portable_fixture_roots,
)

from vaultops.bridge_contract import canonical_json_bytes, remote_identity_sha256
from vaultops.bridge_publish import bridge_status, ingest_bridge_request, publish_bridge_response
from vaultops.cli import main
from vaultops.paths import ResolvedPaths, resolve_api_paths
from vaultops.recovery import RecoveryJournal
from vaultops.runtime import RUNTIME_DIRECTORIES, STATE_DIRECTORIES

CONTROL_ROOT = Path(__file__).resolve().parents[2]
JOB_ID = "550e8400-e29b-41d4-a716-446655440000"
SOURCE_PATH = "00_Inbox/Captures/2026/09/bridge-source.md"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _fresh_control_copy(tmp_path: Path) -> Path:
    inputs = tuple(item for item in APPLICATION_CONTROL_INPUTS if item != "ops/vaultops.toml")
    root = make_control_root(tmp_path, inputs)
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
    (fixture_path(root, "state")).mkdir(mode=0o700, exist_ok=True)
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
        (fixture_path(root, "state") / relative).mkdir()
    for path in [fixture_path(root, "state"), *(fixture_path(root, "state") / name for name in (
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
    ))]:
        path.chmod(0o700)
    sentinel = {
        "schema_version": 1,
        "contract_id": "knowledgeos-vault-root-v1",
        "vault_uuid": "411602c1-5278-4a8b-8b96-9183fb6ef8c2",
        "canonical_vault_name": "KnowledgeHub",
        "remote_identity_sha256": remote_identity_sha256("https://github.com/example/knowledgehub"),
        "expected_branch": "main",
    }
    (vault / ".knowledgeos-root.json").write_bytes(canonical_json_bytes(sentinel))
    (vault / SOURCE_PATH).write_text("---\ntype: capture\n---\nbridge source\n", encoding="utf-8")
    _git(vault, "init", "--initial-branch", "main")
    _git(vault, "config", "user.email", "test@example.invalid")
    _git(vault, "config", "user.name", "KnowledgeOS Test")
    _git(vault, "add", "--", ".knowledgeos-root.json", SOURCE_PATH)
    _git(vault, "commit", "-m", "seed bridge vault")
    return root


def _commit_request(root: Path | ResolvedPaths) -> tuple[dict[str, object], Path, str]:
    roots = resolve_api_paths(root)
    vault = roots.vault
    source = vault / SOURCE_PATH
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    request = {
        "schema_version": 1,
        "job_id": JOB_ID,
        "created_at": "2026-09-14T12:00:00+09:00",
        "device_id": "test-phone-01",
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
    _git(vault, "commit", "-m", "add bridge request")
    return request, request_path, _git(vault, "rev-parse", "HEAD")


def _fresh_separate_control_copy(tmp_path: Path) -> ResolvedPaths:
    roots = make_separate_portable_fixture_roots(tmp_path)
    _git(roots.control, "init", "--initial-branch", "main")
    for relative in STATE_DIRECTORIES:
        directory = roots.state / relative
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
    for relative in RUNTIME_DIRECTORIES:
        directory = roots.runtime / relative
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
    sentinel = {
        "schema_version": 1,
        "contract_id": "knowledgeos-vault-root-v1",
        "vault_uuid": "411602c1-5278-4a8b-8b96-9183fb6ef8c2",
        "canonical_vault_name": "KnowledgeHub",
        "remote_identity_sha256": remote_identity_sha256("https://github.com/example/knowledgehub"),
        "expected_branch": "main",
    }
    (roots.vault / ".knowledgeos-root.json").write_bytes(canonical_json_bytes(sentinel))
    (roots.vault / SOURCE_PATH).parent.mkdir(parents=True, exist_ok=True)
    (roots.vault / SOURCE_PATH).write_text(
        "---\ntype: capture\n---\nbridge source\n",
        encoding="utf-8",
    )
    for relative in (
        ".vault-bridge/requests/2026/09",
        ".vault-bridge/responses/2026/09",
        "01_AI_Review/Pending",
    ):
        (roots.vault / relative).mkdir(parents=True, exist_ok=True)
    _git(roots.vault, "init", "--initial-branch", "main")
    _git(roots.vault, "config", "user.email", "test@example.invalid")
    _git(roots.vault, "config", "user.name", "KnowledgeOS Test")
    _git(roots.vault, "add", "--", ".")
    _git(roots.vault, "commit", "-m", "seed separate bridge Vault")
    return roots


def _response(request: dict[str, object], request_path: Path, request_commit: str, status: str = "no_change") -> dict[str, object]:
    request_bytes = request_path.read_bytes()
    return {
        "schema_version": 1,
        "job_id": request["job_id"],
        "sequence": 1,
        "status": status,
        "event_at": "2026-09-14T12:05:00+09:00",
        "completed_at": "2026-09-14T12:05:00+09:00",
        "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
        "request_commit": request_commit,
        "job_receipt_sha256": "a" * 64,
        "output_schema_sha256": "b" * 64,
        "warnings": [],
    }


def test_ingest_requires_committed_request_and_binds_source_tree(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    request, request_path, request_commit = _commit_request(root)

    report, exit_code = ingest_bridge_request(root, request_path=request_path)

    assert exit_code == 0, report["errors"][0]["message"]
    assert report["status"] == "PASS"
    assert report["state"] == "ingested"
    assert report["job_id"] == JOB_ID
    assert report["request_commit"] == request_commit
    assert report["source_blob_sha256"] == request["source"]["blob_sha256"]


def test_ingest_queue_is_idempotent_and_quarantines_changed_digest(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _request, request_path, _request_commit_id = _commit_request(root)

    first, first_code = ingest_bridge_request(root, request_path=request_path)
    assert first_code == 0
    assert first["queue_write"] == "CREATED"

    replay, replay_code = ingest_bridge_request(root, request_path=request_path)
    assert replay_code == 0
    assert replay["status"] == "NO_OP"
    assert replay["queue_write"] == "EXISTING"

    queue_path = (fixture_path(root, "state") / "queue") / f"{JOB_ID}.json"
    queue_path.write_bytes(queue_path.read_bytes() + b"tampered")
    conflict, conflict_code = ingest_bridge_request(root, request_path=request_path)
    assert conflict_code == 30
    assert conflict["status"] == "FAIL"
    assert "quarantined" in conflict["errors"][0]["message"]
    assert list(((fixture_path(root, "state") / "quarantine/bridge")).glob(f"{JOB_ID}-*.json"))


def test_ingest_uses_selected_vault_and_runtime_for_queue_replay_and_quarantine(
    tmp_path: Path,
) -> None:
    roots = _fresh_separate_control_copy(tmp_path)
    _request, request_path, _request_commit_id = _commit_request(roots)

    first, first_code = ingest_bridge_request(roots.control, request_path=request_path)

    assert first_code == 0
    assert first["status"] == "PASS"
    assert first["queue_write"] == "CREATED"
    queue_path = roots.state / "queue" / f"{JOB_ID}.json"
    assert queue_path.is_file()
    assert not ((roots.control / "runtime/queue") / f"{JOB_ID}.json").exists()
    assert not (roots.control / "KnowledgeHub").exists()

    replay, replay_code = ingest_bridge_request(roots.control, request_path=request_path)

    assert replay_code == 0
    assert replay["status"] == "NO_OP"
    assert replay["queue_write"] == "EXISTING"
    queue_path.write_bytes(queue_path.read_bytes() + b"tampered")

    conflict, conflict_code = ingest_bridge_request(roots.control, request_path=request_path)

    assert conflict_code == 30
    assert conflict["status"] == "FAIL"
    assert "quarantined" in conflict["errors"][0]["message"]
    quarantine = roots.state / "quarantine/bridge"
    assert list(quarantine.glob(f"{JOB_ID}-*.json"))
    assert not queue_path.exists()


def test_ingest_rejects_selected_vault_sentinel_branch_identity_mismatch(tmp_path: Path) -> None:
    roots = _fresh_separate_control_copy(tmp_path)
    _request, request_path, _request_commit_id = _commit_request(roots)
    sentinel_path = roots.vault / ".knowledgeos-root.json"
    sentinel = json.loads(sentinel_path.read_text(encoding="utf-8"))
    sentinel["expected_branch"] = "release"
    sentinel_path.write_bytes(canonical_json_bytes(sentinel))

    report, exit_code = ingest_bridge_request(roots.control, request_path=request_path)

    assert exit_code == 30
    assert report["status"] == "FAIL"
    assert report["errors"][0]["code"] == "BRIDGE_CONFLICT"
    assert not (roots.state / "queue" / f"{JOB_ID}.json").exists()


def test_ingest_requires_selected_runtime_readiness(tmp_path: Path) -> None:
    roots = _fresh_separate_control_copy(tmp_path)
    _request, request_path, _request_commit_id = _commit_request(roots)
    roots.runtime.chmod(0o755)

    report, exit_code = ingest_bridge_request(roots.control, request_path=request_path)

    assert exit_code == 10
    assert report["status"] == "FAIL"
    assert "mode must be 0700" in report["errors"][0]["message"]
    assert not (roots.state / "queue" / f"{JOB_ID}.json").exists()
    assert not ((roots.control / "runtime/queue") / f"{JOB_ID}.json").exists()


def test_bridge_status_reads_requests_from_selected_vault(tmp_path: Path) -> None:
    roots = _fresh_separate_control_copy(tmp_path)
    _request, request_path, _request_commit_id = _commit_request(roots)

    report, exit_code = bridge_status(roots.control, job_id=JOB_ID)

    assert exit_code == 0
    assert report["requests"] == [request_path.relative_to(roots.vault).as_posix()]
    assert report["control_root"] == roots.control.as_posix()
    assert report["vault_root"] == roots.vault.as_posix()


def test_ingest_rejects_worktree_drift_and_uncommitted_request(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _request, request_path, _request_commit_id = _commit_request(root)
    source = fixture_path(root, "vault") / SOURCE_PATH
    source.write_text(source.read_text(encoding="utf-8") + "drift\n", encoding="utf-8")

    drift, drift_code = ingest_bridge_request(root, request_path=request_path)

    assert drift_code == 30
    assert drift["status"] == "FAIL"
    assert drift["errors"][0]["code"] == "BRIDGE_CONFLICT"

    source.write_text("---\ntype: capture\n---\nbridge source\n", encoding="utf-8")
    request_path.unlink()
    request_path.write_bytes(canonical_json_bytes({"broken": True}))
    uncommitted, uncommitted_code = ingest_bridge_request(root, request_path=request_path)
    assert uncommitted_code == 30
    assert uncommitted["status"] == "FAIL"


def test_publish_commits_exact_response_and_proposal_paths_and_replays_noop(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    request, request_path, request_commit = _commit_request(root)
    response = _response(request, request_path, request_commit, status="needs_review")
    proposal = tmp_path / "proposal.md"
    proposal.write_text("---\ntype: idea\n---\nproposal\n", encoding="utf-8")
    response["proposal_id"] = "650e8400-e29b-41d4-a716-446655440000"
    response["proposal_path"] = "01_AI_Review/Pending/2026/09/Bridge Proposal.md"
    response["proposal_sha256"] = hashlib.sha256(proposal.read_bytes()).hexdigest()
    response_file = tmp_path / "response.json"
    response_file.write_bytes(canonical_json_bytes(response))

    report, exit_code = publish_bridge_response(root, response_file=response_file, proposal_file=proposal)

    assert exit_code == 0, report["errors"][0]["message"]
    assert report["status"] == "PASS"
    assert len(report["committed_paths"]) == 2
    vault = fixture_path(root, "vault")
    event = vault / ".vault-bridge/responses/2026/09/550e8400-e29b-41d4-a716-446655440000/0001-needs_review.json"
    proposal_target = vault / "01_AI_Review/Pending/2026/09/Bridge Proposal.md"
    assert event.is_file()
    assert proposal_target.read_bytes() == proposal.read_bytes()
    assert _git(vault, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines() == sorted(
        [event.relative_to(vault).as_posix(), proposal_target.relative_to(vault).as_posix()]
    )
    assert ((fixture_path(root, "state") / "receipts") / f"{JOB_ID}-bridge_publish.json").is_file()

    replay, replay_code = publish_bridge_response(root, response_file=response_file, proposal_file=proposal)
    assert replay_code == 0
    assert replay["status"] == "NO_OP"
    assert replay["replayed"] is True


def test_publish_quarantines_changed_retry_before_vault_mutation(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    request, request_path, request_commit = _commit_request(root)
    response = _response(request, request_path, request_commit)
    response_file = tmp_path / "response.json"
    response_file.write_bytes(canonical_json_bytes(response))
    first, first_code = publish_bridge_response(root, response_file=response_file)
    assert first_code == 0, first
    assert first["status"] == "PASS"

    changed = dict(response)
    changed["warnings"] = ["changed retry"]
    changed_file = tmp_path / "changed-response.json"
    changed_file.write_bytes(canonical_json_bytes(changed))
    before = _git(fixture_path(root, "vault"), "rev-parse", "HEAD")
    conflict, conflict_code = publish_bridge_response(root, response_file=changed_file)

    assert conflict_code == 30
    assert conflict["status"] == "FAIL"
    assert conflict["errors"][0]["code"] in {"BRIDGE_CONFLICT", "BRIDGE_INTENT_MISMATCH"}
    assert _git(fixture_path(root, "vault"), "rev-parse", "HEAD") == before


def test_publish_resumes_after_files_are_created_but_before_commit(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    request, request_path, request_commit = _commit_request(root)
    response = _response(request, request_path, request_commit)
    response_file = tmp_path / "response.json"
    response_file.write_bytes(canonical_json_bytes(response))
    response_sha = hashlib.sha256(
        canonical_json_bytes(response) + b"\n"
    ).hexdigest()
    event_relative = ".vault-bridge/responses/2026/09/550e8400-e29b-41d4-a716-446655440000/0001-no_change.json"
    journal = RecoveryJournal(root, job_id=JOB_ID, operation="bridge_publish")
    journal.start(
        {
            "schema_version": 1,
            "job_id": JOB_ID,
            "status": "no_change",
            "request_path": request_path.relative_to(fixture_path(root, "vault")).as_posix(),
            "request_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
            "request_commit": request_commit,
            "response_path": event_relative,
            "response_sha256": response_sha,
            "proposal_path": None,
            "proposal_sha256": None,
            "exact_paths": [event_relative],
            "blob_hashes": {event_relative: response_sha},
            "vault_git_head_before": _git(fixture_path(root, "vault"), "rev-parse", "HEAD"),
        }
    )
    event = fixture_path(root, "vault") / event_relative
    event.parent.mkdir(parents=True)
    event.write_bytes(canonical_json_bytes(response) + b"\n")

    resumed, resumed_code = publish_bridge_response(root, response_file=response_file)

    assert resumed_code == 0, resumed
    assert resumed["status"] == "PASS"
    assert resumed["replayed"] is True
    assert _git(fixture_path(root, "vault"), "status", "--porcelain") == ""


def test_publish_and_replay_use_selected_vault_and_runtime_receipts(tmp_path: Path) -> None:
    roots = _fresh_separate_control_copy(tmp_path)
    request, request_path, request_commit = _commit_request(roots)
    response = _response(request, request_path, request_commit, status="needs_review")
    proposal = tmp_path / "proposal.md"
    proposal.write_text("---\ntype: idea\n---\nseparate-root proposal\n", encoding="utf-8")
    response["proposal_id"] = "650e8400-e29b-41d4-a716-446655440001"
    response["proposal_path"] = "01_AI_Review/Pending/2026/09/Separate Root Proposal.md"
    response["proposal_sha256"] = hashlib.sha256(proposal.read_bytes()).hexdigest()
    response_file = tmp_path / "response.json"
    response_file.write_bytes(canonical_json_bytes(response))

    report, exit_code = publish_bridge_response(
        roots.control,
        response_file=response_file,
        proposal_file=proposal,
    )

    event_relative = (
        f".vault-bridge/responses/2026/09/{JOB_ID}/0001-needs_review.json"
    )
    proposal_relative = str(response["proposal_path"])
    assert exit_code == 0, report["errors"][0]["message"]
    assert report["status"] == "PASS"
    assert _git(roots.vault, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines() == sorted(
        [event_relative, proposal_relative]
    )
    assert (roots.vault / event_relative).is_file()
    assert (roots.vault / proposal_relative).read_bytes() == proposal.read_bytes()
    assert (roots.state / "receipts" / f"{JOB_ID}-bridge_publish.json").is_file()
    assert (roots.state / "runs" / JOB_ID / "journal.jsonl").is_file()
    assert not ((roots.control / "runtime/receipts") / f"{JOB_ID}-bridge_publish.json").exists()
    assert not (roots.control / "KnowledgeHub").exists()

    replay, replay_code = publish_bridge_response(
        roots.control,
        response_file=response_file,
        proposal_file=proposal,
    )

    assert replay_code == 0
    assert replay["status"] == "NO_OP"
    assert replay["replayed"] is True
    head_before_conflict = _git(roots.vault, "rev-parse", "HEAD")
    changed_response = dict(response)
    changed_response["warnings"] = ["changed separate-root retry"]
    changed_file = tmp_path / "changed-response.json"
    changed_file.write_bytes(canonical_json_bytes(changed_response))

    conflict, conflict_code = publish_bridge_response(
        roots.control,
        response_file=changed_file,
        proposal_file=proposal,
    )

    assert conflict_code == 30
    assert conflict["status"] == "FAIL"
    assert conflict["errors"][0]["code"] == "BRIDGE_INTENT_MISMATCH"
    assert _git(roots.vault, "rev-parse", "HEAD") == head_before_conflict
    assert list((roots.state / "quarantine/transactions").glob(f"{JOB_ID}-bridge_publish-*"))


def test_publish_recovers_selected_vault_files_with_selected_runtime_journal(
    tmp_path: Path,
) -> None:
    roots = _fresh_separate_control_copy(tmp_path)
    request, request_path, request_commit = _commit_request(roots)
    response = _response(request, request_path, request_commit)
    response_file = tmp_path / "response.json"
    response_file.write_bytes(canonical_json_bytes(response))
    response_bytes = canonical_json_bytes(response) + b"\n"
    response_sha = hashlib.sha256(response_bytes).hexdigest()
    event_relative = (
        f".vault-bridge/responses/2026/09/{JOB_ID}/0001-no_change.json"
    )
    journal = RecoveryJournal(roots, job_id=JOB_ID, operation="bridge_publish")
    journal.start(
        {
            "schema_version": 1,
            "job_id": JOB_ID,
            "status": "no_change",
            "request_path": request_path.relative_to(roots.vault).as_posix(),
            "request_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
            "request_commit": request_commit,
            "response_path": event_relative,
            "response_sha256": response_sha,
            "proposal_path": None,
            "proposal_sha256": None,
            "exact_paths": [event_relative],
            "blob_hashes": {event_relative: response_sha},
            "vault_git_head_before": _git(roots.vault, "rev-parse", "HEAD"),
        }
    )
    event = roots.vault / event_relative
    event.parent.mkdir(parents=True)
    event.write_bytes(response_bytes)

    resumed, resumed_code = publish_bridge_response(
        roots.control,
        response_file=response_file,
    )

    assert resumed_code == 0, resumed
    assert resumed["status"] == "PASS"
    assert resumed["replayed"] is True
    assert _git(roots.vault, "status", "--porcelain") == ""
    assert journal.latest()["state"] == "completed"
    assert journal.receipt_path.is_file()
    assert journal.path.is_relative_to(roots.state)
    assert not ((roots.control / "runtime/runs") / JOB_ID).exists()


def test_bridge_cli_routes_ingest_and_status_as_json(tmp_path: Path, capsys) -> None:
    root = _fresh_control_copy(tmp_path)
    _request, request_path, _request_commit_id = _commit_request(root)
    root_arg = str(root)

    assert main(["bridge", "ingest", "--path", str(request_path), "--root", root_arg]) == 0
    ingest_report = json.loads(capsys.readouterr().out)
    assert ingest_report["state"] == "ingested"

    assert main(["bridge", "status", "--job-id", JOB_ID, "--root", root_arg]) == 0
    status_report = json.loads(capsys.readouterr().out)
    assert status_report["status"] == "PASS"
    assert status_report["requests"] == [request_path.relative_to(fixture_path(root, "vault")).as_posix()]


@pytest.mark.parametrize("bad_status", ["<bad>", "../bad"])
def test_publish_rejects_unsafe_status_before_writing(tmp_path: Path, bad_status: str) -> None:
    root = _fresh_control_copy(tmp_path)
    request, request_path, request_commit = _commit_request(root)
    response = _response(request, request_path, request_commit)
    response["status"] = bad_status
    response_file = tmp_path / "response.json"
    response_file.write_bytes(json.dumps(response).encode("utf-8"))

    report, exit_code = publish_bridge_response(root, response_file=response_file)

    assert exit_code in {13, 30}
    assert report["status"] == "FAIL"
