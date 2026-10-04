from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from support.control_factory import (
    fixture_path,
    make_portable_fixture_root,
    make_separate_portable_fixture_roots,
)

import vaultops.proposals as proposals_module
from vaultops.note_engine import render_frontmatter
from vaultops.paths import ResolvedPaths
from vaultops.proposals import approve_proposal, reject_proposal
from vaultops.template_engine import render_note_template

CONTROL_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"


def _fresh_control_copy(tmp_path: Path) -> Path:
    return make_portable_fixture_root(tmp_path, review_queues=True)


def _vault(root: Path | ResolvedPaths) -> Path:
    return root.vault if isinstance(root, ResolvedPaths) else fixture_path(root, "vault")


def _proposal(root: Path | ResolvedPaths, title: str) -> tuple[str, str, Path]:
    vault = _vault(root)
    source = vault / SOURCE_PATH
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    proposal_id = str(uuid.uuid4())
    proposal_path = f"01_AI_Review/Pending/{title}.md"
    target_path = f"40_Knowledge/Ideas/{title}.md"
    target_markdown = render_note_template(
        "T21_Idea.md",
        {
            "title": title,
            "id": str(uuid.uuid4()),
            "created": "2026-09-17T10:00:00+09:00",
            "modified": "2026-09-17T10:00:00+09:00",
        },
    ).markdown
    manifest = {
        "action": "create_note",
        "target_path": target_path,
        "target_markdown": target_markdown,
    }
    properties = {
        "schema_version": 1,
        "id": f"proposal-{proposal_id}",
        "type": "proposal",
        "title": title,
        "status": "pending",
        "created": "2026-09-17T10:00:00+09:00",
        "modified": "2026-09-17T10:00:00+09:00",
        "aliases": [],
        "tags": ["ai/review"],
        "sensitivity": "personal",
        "ai_policy": "deny",
        "ai_status": "proposed",
        "proposal_id": proposal_id,
        "source_hashes": [f"{SOURCE_PATH}|sha256:{source_hash}"],
    }
    body = (
        f"# AI Proposal — {title}\n\n"
        "<!-- vaultops:proposal-manifest\n"
        f"{json.dumps(manifest, ensure_ascii=False, sort_keys=True)}\n"
        "-->\n"
    )
    path = vault / proposal_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_frontmatter(properties, body), encoding="utf-8")
    return proposal_path, hashlib.sha256(path.read_bytes()).hexdigest(), path


def test_approval_replay_ignores_timestamp_but_quarantines_changed_bytes(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    proposal_path, proposal_hash, proposal_file = _proposal(root, "C28 Approval Replay")

    first, first_code = approve_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
    )
    assert first_code == 0, first
    replay, replay_code = approve_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
    )
    assert replay_code == 0
    assert replay["status"] == "NO_OP"
    assert replay["replayed"] is True

    proposal_file.write_bytes(proposal_file.read_bytes().replace(b"ai/review", b"ai/changed", 1))
    changed_hash = hashlib.sha256(proposal_file.read_bytes()).hexdigest()
    conflict, conflict_code = approve_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=changed_hash,
    )
    assert conflict_code == 30
    assert conflict["status"] == "CONFLICT"
    assert conflict["errors"][0]["code"] == "APPROVAL_REPLAY_CONFLICT"
    assert conflict["quarantined"].startswith("state/quarantine/proposals/")


def test_rejection_recovers_after_pending_rewrite_fault_and_replays_noop(
    tmp_path: Path, monkeypatch
) -> None:
    root = _fresh_control_copy(tmp_path)
    proposal_path, proposal_hash, _proposal_file = _proposal(root, "C28 Rewrite Fault")
    original_write = proposals_module.write_note_file

    def fail_after_write(*args, **kwargs):
        original_write(*args, **kwargs)
        raise OSError("simulated crash after rejection rewrite")

    monkeypatch.setattr(proposals_module, "write_note_file", fail_after_write)
    first, first_code = reject_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
        reason="Human review declined this proposal.",
    )
    assert first_code == 10
    assert first["status"] == "FAIL"
    monkeypatch.setattr(proposals_module, "write_note_file", original_write)

    resumed, resumed_code = reject_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
        reason="Human review declined this proposal.",
    )
    assert resumed_code == 0, resumed
    assert resumed["status"] == "PASS"
    assert resumed["replayed"] is True
    assert (fixture_path(root, "vault") / resumed["closed_path"]).is_file()
    assert ((fixture_path(root, "state") / "receipts") / f"{resumed['receipt']['proposal_id']}-proposal-rejection.json").is_file()

    replay, replay_code = reject_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
        reason="Human review declined this proposal.",
    )
    assert replay_code == 0
    assert replay["status"] == "NO_OP"
    assert replay["replayed"] is True


def test_rejection_recovery_uses_selected_runtime_and_vault(tmp_path: Path, monkeypatch) -> None:
    roots = make_separate_portable_fixture_roots(tmp_path, review_queues=True)
    proposal_path, proposal_hash, _ = _proposal(roots, "C28 Separate Runtime Recovery")
    original_write = proposals_module.write_note_file

    def fail_after_write(*args, **kwargs):
        original_write(*args, **kwargs)
        raise OSError("simulated crash after rejection rewrite")

    monkeypatch.setattr(proposals_module, "write_note_file", fail_after_write)
    interrupted, interrupted_code = reject_proposal(
        roots,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
        reason="Human review declined this proposal.",
    )
    assert interrupted_code == 10
    assert interrupted["status"] == "FAIL"
    journal = next((roots.state / "runs").glob("*/journal.jsonl"))
    assert journal.is_file()
    assert not (roots.control / "runtime").exists()
    monkeypatch.setattr(proposals_module, "write_note_file", original_write)

    recovered, recovered_code = reject_proposal(
        roots,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
        reason="Human review declined this proposal.",
    )

    assert recovered_code == 0, recovered
    assert recovered["status"] == "PASS"
    assert recovered["replayed"] is True
    assert (roots.vault / recovered["closed_path"]).is_file()
    assert (
        roots.state
        / "receipts"
        / f"{recovered['receipt']['proposal_id']}-proposal-rejection.json"
    ).is_file()
    assert not (roots.control / "KnowledgeHub").exists()


def test_rejection_recovers_same_request_after_initial_pending_write_fault(
    tmp_path: Path, monkeypatch
) -> None:
    root = _fresh_control_copy(tmp_path)
    proposal_path, proposal_hash, proposal_file = _proposal(root, "C28 Initial Write Fault")
    original_bytes = proposal_file.read_bytes()
    original_write = proposals_module.write_note_file

    def fail_before_write(*_args, **_kwargs):
        raise OSError("simulated interruption before the pending rewrite")

    monkeypatch.setattr(proposals_module, "write_note_file", fail_before_write)
    interrupted, interrupted_code = reject_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
        reason="Human review declined this proposal.",
    )
    assert interrupted_code == 10
    assert interrupted["status"] == "FAIL"
    assert proposal_file.read_bytes() == original_bytes
    monkeypatch.setattr(proposals_module, "write_note_file", original_write)

    resumed, resumed_code = reject_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
        reason="Human review declined this proposal.",
    )

    assert resumed_code == 0, resumed
    assert resumed["status"] == "PASS"
    assert resumed["replayed"] is True
    assert not proposal_file.exists()
    assert (fixture_path(root, "vault") / resumed["closed_path"]).is_file()
    assert (
        (fixture_path(root, "state") / "receipts")
        / f"{resumed['receipt']['proposal_id']}-proposal-rejection.json"
    ).is_file()


def test_rejection_recovers_completed_journal_when_receipt_was_not_published(
    tmp_path: Path, monkeypatch
) -> None:
    root = _fresh_control_copy(tmp_path)
    proposal_path, proposal_hash, _proposal_file = _proposal(root, "C28 Missing Receipt")
    original_create_only_json = proposals_module._create_only_json

    def fail_rejection_receipt(path: Path, payload):
        if path.name.endswith("-proposal-rejection.json"):
            raise OSError("simulated interruption before receipt publication")
        original_create_only_json(path, payload)

    monkeypatch.setattr(proposals_module, "_create_only_json", fail_rejection_receipt)
    interrupted, interrupted_code = reject_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
        reason="Human review declined this proposal.",
    )
    assert interrupted_code == 10
    assert interrupted["status"] == "FAIL"

    journal_path = next(((fixture_path(root, "state") / "runs")).glob("*/journal.jsonl"))
    records = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["state"] == "completed"
    receipt_path = (
        (fixture_path(root, "state") / "receipts")
        / f"{records[0]['job_id']}-proposal-rejection.json"
    )
    assert not receipt_path.exists()
    monkeypatch.setattr(proposals_module, "_create_only_json", original_create_only_json)

    recovered, recovered_code = reject_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
        reason="Human review declined this proposal.",
    )
    assert recovered_code == 0, recovered
    assert recovered["status"] == "PASS"
    assert recovered["replayed"] is True
    assert receipt_path.is_file()

    replay, replay_code = reject_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
        reason="Human review declined this proposal.",
    )
    assert replay_code == 0
    assert replay["status"] == "NO_OP"
    assert replay["replayed"] is True


def test_rejection_recovers_after_move_fault_and_ignores_unrelated_vault_drift(
    tmp_path: Path, monkeypatch
) -> None:
    root = _fresh_control_copy(tmp_path)
    proposal_path, proposal_hash, _proposal_file = _proposal(root, "C28 Move Fault")
    original_replace = proposals_module.os.replace

    def fail_after_replace(source: Path, destination: Path):
        original_replace(source, destination)
        raise OSError("simulated crash after rejection move")

    monkeypatch.setattr(proposals_module.os, "replace", fail_after_replace)
    first, first_code = reject_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
        reason="The proposal is out of scope.",
    )
    assert first_code == 10
    assert first["status"] == "FAIL"
    monkeypatch.setattr(proposals_module.os, "replace", original_replace)

    unrelated = fixture_path(root, "vault") / "80_Assets/Documents/unrelated-drift.txt"
    unrelated.write_text("unrelated Vault drift\n", encoding="utf-8")
    resumed, resumed_code = reject_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
        reason="The proposal is out of scope.",
    )
    assert resumed_code == 0, resumed
    assert resumed["status"] == "PASS"
    assert resumed["replayed"] is True
    assert (fixture_path(root, "vault") / resumed["closed_path"]).is_file()


def test_rejection_request_conflict_quarantines_active_journal(tmp_path: Path, monkeypatch) -> None:
    root = _fresh_control_copy(tmp_path)
    proposal_path, proposal_hash, _proposal_file = _proposal(root, "C28 Journal Conflict")
    original_write = proposals_module.write_note_file

    def fail_before_write(*_args, **_kwargs):
        raise OSError("simulated crash before rejection rewrite")

    monkeypatch.setattr(proposals_module, "write_note_file", fail_before_write)
    first, first_code = reject_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
        reason="First rejection reason.",
    )
    assert first_code == 10
    assert first["status"] == "FAIL"
    monkeypatch.setattr(proposals_module, "write_note_file", original_write)

    conflict, conflict_code = reject_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
        reason="Different rejection reason.",
    )
    assert conflict_code == 30
    assert conflict["status"] == "CONFLICT"
    assert conflict["errors"][0]["code"] == "DECISION_REPLAY_CONFLICT"
    assert conflict["quarantined"].startswith("state/quarantine/transactions/")
