from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path

from vaultops.cli import main
from vaultops.note_engine import render_frontmatter
from vaultops.proposals import (
    apply_proposal,
    approve_proposal,
    reject_proposal,
    review_proposals,
)
from vaultops.template_engine import render_note_template

CONTROL_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"


def _fresh_control_copy(tmp_path: Path) -> Path:
    root = tmp_path / "control"
    shutil.copytree(
        CONTROL_ROOT,
        root,
        ignore=shutil.ignore_patterns(
            ".git", ".pytest_cache", ".ruff_cache", "KnowledgeHub", "runtime"
        ),
    )
    fixture = CONTROL_ROOT / "ops/tests/fixtures/c09_portable_vault/guestbook-horror/input"
    shutil.copytree(fixture, root / "KnowledgeHub")
    for relative in (
        "01_AI_Review/Pending",
        "01_AI_Review/Resolved",
        "01_AI_Review/Rejected",
    ):
        (root / "KnowledgeHub" / relative).mkdir(parents=True, exist_ok=True)
    return root


def _proposal(
    root: Path,
    *,
    filename: str,
    target_path: str,
    target_markdown: str,
) -> tuple[str, str, Path]:
    source = root / "KnowledgeHub" / SOURCE_PATH
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    proposal_id = str(uuid.uuid4())
    proposal_path = f"01_AI_Review/Pending/{filename}.md"
    manifest = {
        "action": "create_note",
        "target_path": target_path,
        "target_markdown": target_markdown,
    }
    properties = {
        "schema_version": 1,
        "id": f"proposal-{proposal_id}",
        "type": "proposal",
        "title": filename,
        "status": "pending",
        "created": "2026-09-16T10:00:00+09:00",
        "modified": "2026-09-16T10:00:00+09:00",
        "aliases": [],
        "tags": ["ai/review"],
        "sensitivity": "personal",
        "ai_policy": "deny",
        "ai_status": "proposed",
        "proposal_id": proposal_id,
        "source_hashes": [f"{SOURCE_PATH}|sha256:{source_hash}"],
    }
    body = (
        f"# AI Proposal — {filename}\n\n"
        "<!-- vaultops:proposal-manifest\n"
        f"{json.dumps(manifest, ensure_ascii=False, sort_keys=True)}\n"
        "-->\n"
    )
    path = root / "KnowledgeHub" / proposal_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_frontmatter(properties, body), encoding="utf-8")
    return proposal_path, hashlib.sha256(path.read_bytes()).hexdigest(), path


def _target_markdown(title: str) -> str:
    return render_note_template(
        "T21_Idea.md",
        {
            "title": title,
            "id": str(uuid.uuid4()),
            "created": "2026-09-16T10:00:00+09:00",
            "modified": "2026-09-16T10:00:00+09:00",
        },
    ).markdown


def test_review_approve_apply_and_replay_are_digest_bound(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    target_path = "40_Knowledge/Ideas/C19 Approved Idea.md"
    proposal_path, proposal_hash, source = _proposal(
        root,
        filename="C19 Create Proposal",
        target_path=target_path,
        target_markdown=_target_markdown("C19 Approved Idea"),
    )
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }

    reviewed, review_code = review_proposals(root, proposal_path=proposal_path)
    assert review_code == 0, reviewed
    assert reviewed["status"] == "PASS"
    assert reviewed["mutation_performed"] is False
    assert {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    } == before

    approved, approve_code = approve_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
    )
    assert approve_code == 0, approved
    assert approved["status"] == "PASS"
    proposal_id = approved["approval"]["proposal_id"]
    approval_file = root / "runtime/approved" / f"{proposal_id}.json"
    assert approval_file.is_file()

    applied, apply_code = apply_proposal(root, proposal_path=proposal_path)
    assert apply_code == 0, applied
    assert applied["status"] == "PASS"
    assert (root / "KnowledgeHub" / target_path).is_file()
    assert not source.exists()
    assert (root / "KnowledgeHub" / "01_AI_Review/Resolved/C19 Create Proposal.md").is_file()
    assert (root / "runtime/receipts" / f"{proposal_id}-proposal-apply.json").is_file()

    replay, replay_code = apply_proposal(root, proposal_path=proposal_path)
    assert replay_code == 0
    assert replay["status"] == "NO_OP"
    assert replay["replayed"] is True


def test_stale_approval_and_privacy_denial_fail_without_target_mutation(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    target_path = "40_Knowledge/Ideas/C19 Stale Idea.md"
    proposal_path, proposal_hash, _ = _proposal(
        root,
        filename="C19 Stale Proposal",
        target_path=target_path,
        target_markdown=_target_markdown("C19 Stale Idea"),
    )
    approved, approve_code = approve_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
    )
    assert approve_code == 0, approved
    target = root / "KnowledgeHub" / target_path
    before = target.exists()
    proposal = root / "KnowledgeHub" / proposal_path
    proposal.write_bytes(proposal.read_bytes().replace(b"pending", b"pending\n", 1))
    stale, stale_code = apply_proposal(root, proposal_path=proposal_path)
    assert stale_code == 30
    assert stale["status"] == "FAIL"
    assert stale["errors"][0]["code"] in {"PROPOSAL_INVALID", "APPROVAL_STALE"}
    assert target.exists() is before

    denied_root = _fresh_control_copy(tmp_path / "denied")
    denied_source = denied_root / "KnowledgeHub" / SOURCE_PATH
    denied_source.write_bytes(denied_source.read_bytes().replace(b"ai_policy: ask", b"ai_policy: deny"))
    denied_proposal, denied_hash, _ = _proposal(
        denied_root,
        filename="C19 Privacy Proposal",
        target_path="40_Knowledge/Ideas/C19 Privacy Idea.md",
        target_markdown=_target_markdown("C19 Privacy Idea"),
    )
    denied, denied_code = approve_proposal(
        denied_root,
        proposal_path=denied_proposal,
        expected_sha256=denied_hash,
    )
    assert denied_code == 30
    assert denied["errors"][0]["code"] == "PROPOSAL_PRIVACY_DENIED"
    assert not (denied_root / "runtime/approved").exists()

    malformed_root = _fresh_control_copy(tmp_path / "malformed")
    malformed_proposal, malformed_hash, malformed_file = _proposal(
        malformed_root,
        filename="C19 Malformed Proposal",
        target_path="40_Knowledge/Ideas/C19 Malformed Idea.md",
        target_markdown=_target_markdown("C19 Malformed Idea"),
    )
    malformed_file.write_text(
        malformed_file.read_text(encoding="utf-8").replace(
            "vaultops:proposal-manifest", "vaultops:proposal-manifest-invalid"
        ),
        encoding="utf-8",
    )
    malformed_hash = hashlib.sha256(malformed_file.read_bytes()).hexdigest()
    malformed, malformed_code = approve_proposal(
        malformed_root,
        proposal_path=malformed_proposal,
        expected_sha256=malformed_hash,
    )
    assert malformed_code == 30
    assert malformed["errors"][0]["code"] == "PROPOSAL_MANIFEST_REQUIRED"


def test_reject_closes_proposal_and_cli_review_is_read_only(tmp_path: Path, capsys) -> None:
    root = _fresh_control_copy(tmp_path)
    proposal_path, proposal_hash, _ = _proposal(
        root,
        filename="C19 Reject Proposal",
        target_path="40_Knowledge/Ideas/C19 Rejected Idea.md",
        target_markdown=_target_markdown("C19 Rejected Idea"),
    )
    rejected, reject_code = reject_proposal(
        root,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
        reason="Human review declined this proposal.",
    )
    assert reject_code == 0, rejected
    assert rejected["status"] == "PASS"
    assert (
        root / "KnowledgeHub/01_AI_Review/Rejected/C19 Reject Proposal.md"
    ).is_file()
    assert not (root / "KnowledgeHub/40_Knowledge/Ideas/C19 Rejected Idea.md").exists()

    proposal_path, proposal_hash, _ = _proposal(
        root,
        filename="C19 CLI Review Proposal",
        target_path="40_Knowledge/Ideas/C19 CLI Review Idea.md",
        target_markdown=_target_markdown("C19 CLI Review Idea"),
    )
    assert main(["ai", "review", "--proposal", proposal_path, "--root", str(root)]) == 0
    assert main(
        [
            "ai",
            "approve",
            "--proposal",
            proposal_path,
            "--expected-sha256",
            proposal_hash,
            "--root",
            str(root),
        ]
    ) == 0
    assert main(["ai", "apply", "--proposal", proposal_path, "--root", str(root)]) == 0
    output = capsys.readouterr().out
    assert '"status": "PASS"' in output
    assert (
        root / "KnowledgeHub/01_AI_Review/Resolved/C19 CLI Review Proposal.md"
    ).is_file()
