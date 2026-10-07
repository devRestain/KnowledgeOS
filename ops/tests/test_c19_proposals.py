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

from vaultops.note_engine import render_frontmatter
from vaultops.paths import ResolvedPaths
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
    return make_portable_fixture_root(tmp_path, review_queues=True)


def _vault(root: Path | ResolvedPaths) -> Path:
    return root.vault if isinstance(root, ResolvedPaths) else fixture_path(root, "vault")


def _proposal(
    root: Path | ResolvedPaths,
    *,
    filename: str,
    target_path: str,
    target_markdown: str,
) -> tuple[str, str, Path]:
    vault = _vault(root)
    source = vault / SOURCE_PATH
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
    path = vault / proposal_path
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
    approval_file = (fixture_path(root, "state") / "approved") / f"{proposal_id}.json"
    assert approval_file.is_file()

    applied, apply_code = apply_proposal(root, proposal_path=proposal_path)
    assert apply_code == 0, applied
    assert applied["status"] == "PASS"
    assert (fixture_path(root, "vault") / target_path).is_file()
    assert not source.exists()
    assert (fixture_path(root, "vault") / "01_AI_Review/Resolved/C19 Create Proposal.md").is_file()
    assert ((fixture_path(root, "state") / "receipts") / f"{proposal_id}-proposal-apply.json").is_file()

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
    target = fixture_path(root, "vault") / target_path
    before = target.exists()
    proposal = fixture_path(root, "vault") / proposal_path
    proposal.write_bytes(proposal.read_bytes().replace(b"pending", b"pending\n", 1))
    stale, stale_code = apply_proposal(root, proposal_path=proposal_path)
    assert stale_code == 30
    assert stale["status"] == "FAIL"
    assert stale["errors"][0]["code"] in {"PROPOSAL_INVALID", "APPROVAL_STALE"}
    assert target.exists() is before

    denied_root = _fresh_control_copy(tmp_path / "denied")
    denied_source = fixture_path(denied_root, "vault") / SOURCE_PATH
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
    assert not (fixture_path(denied_root, "state") / "approved").exists()

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


def test_reject_closes_proposal_without_apply(tmp_path: Path) -> None:
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
        fixture_path(root, "vault") / "01_AI_Review/Rejected/C19 Reject Proposal.md"
    ).is_file()
    assert not (fixture_path(root, "vault") / "40_Knowledge/Ideas/C19 Rejected Idea.md").exists()

def test_proposal_approval_apply_and_replay_use_selected_roots(tmp_path: Path, monkeypatch) -> None:
    roots = make_separate_portable_fixture_roots(tmp_path, review_queues=True)
    target_path = "40_Knowledge/Ideas/C19 Separate Root Idea.md"
    target_markdown = _target_markdown("C19 Separate Root Idea")
    proposal_path, proposal_hash, _ = _proposal(
        roots,
        filename="C19 Separate Root Proposal",
        target_path=target_path,
        target_markdown=target_markdown,
    )
    unrelated_cwd = tmp_path / "unrelated cwd Ω"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)

    reviewed, review_code = review_proposals(roots, proposal_path=proposal_path)
    assert review_code == 0, reviewed
    assert reviewed["status"] == "PASS"
    proposal_id = reviewed["proposals"][0]["proposal_id"]

    approved, approve_code = approve_proposal(
        roots,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
    )
    assert approve_code == 0, approved
    assert approved["approval"]["proposal_id"] == proposal_id
    approval_path = roots.state / "approved" / f"{proposal_id}.json"
    assert approval_path.is_file()
    binding = approved["approval"]["approval_binds"]
    assert binding["proposal_sha256"] == proposal_hash
    assert binding["source_baselines"] == [
        {
            "path": SOURCE_PATH,
            "sha256": hashlib.sha256((roots.vault / SOURCE_PATH).read_bytes()).hexdigest(),
        }
    ]
    assert binding["target_baselines"] == [{"path": target_path, "sha256": None}]
    assert binding["diff_sha256"] == hashlib.sha256(target_markdown.encode("utf-8")).hexdigest()
    assert not (roots.control / "runtime").exists()

    applied, apply_code = apply_proposal(roots, proposal_path=proposal_path)
    assert apply_code == 0, applied
    target = roots.vault / target_path
    resolved = roots.vault / "01_AI_Review/Resolved/C19 Separate Root Proposal.md"
    receipt = roots.state / "receipts" / f"{proposal_id}-proposal-apply.json"
    journal = roots.state / "runs" / proposal_id / "journal.jsonl"
    assert applied["status"] == "PASS"
    assert target.is_file()
    assert resolved.is_file()
    assert receipt.is_file()
    assert journal.is_file()
    assert not (roots.control / "KnowledgeHub").exists()
    assert not (roots.control / "runtime").exists()

    replay, replay_code = apply_proposal(roots, proposal_path=proposal_path)
    assert replay_code == 0
    assert replay["status"] == "NO_OP"
    assert replay["replayed"] is True


def test_selected_root_apply_refuses_stale_source_binding(tmp_path: Path) -> None:
    roots = make_separate_portable_fixture_roots(tmp_path, review_queues=True)
    proposal_path, proposal_hash, _ = _proposal(
        roots,
        filename="C19 Stale Separate Source",
        target_path="40_Knowledge/Ideas/C19 Stale Separate Idea.md",
        target_markdown=_target_markdown("C19 Stale Separate Idea"),
    )
    approved, approve_code = approve_proposal(
        roots,
        proposal_path=proposal_path,
        expected_sha256=proposal_hash,
    )
    assert approve_code == 0, approved

    source = roots.vault / SOURCE_PATH
    source.write_bytes(source.read_bytes() + b"\n")
    applied, apply_code = apply_proposal(roots, proposal_path=proposal_path)

    assert apply_code == 30
    assert applied["errors"][0]["code"] == "PROPOSAL_SOURCE_DRIFT"
    assert not (roots.vault / "40_Knowledge/Ideas/C19 Stale Separate Idea.md").exists()
    assert (roots.state / "approved" / f"{approved['approval']['proposal_id']}.json").is_file()
