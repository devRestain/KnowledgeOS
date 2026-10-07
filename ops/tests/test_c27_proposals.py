from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from support.control_factory import (
    fixture_path,
    make_portable_fixture_root,
    make_separate_portable_fixture_roots,
)

from vaultops.action_proposals import (
    generate_draft_note_proposal,
    generate_link_suggestions_proposal,
    generate_normalize_proposal,
    proposal_schema,
)
from vaultops.fragments import daily_fragment_bytes
from vaultops.paths import ResolvedPaths
from vaultops.proposals import approve_proposal, review_proposals
from vaultops.triage import deterministic_triage

CONTROL_ROOT = Path(__file__).resolve().parents[2]
SOURCE = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"
DAILY = "10_Journal/Daily/2026/2026-09-09.md"
CANDIDATE = "40_Knowledge/Notes/정보의 빈칸은 공포의 상상을 강화한다.md"


def _fresh_control_copy(tmp_path: Path) -> Path:
    return make_portable_fixture_root(tmp_path, review_queues=True)


def _vault(root: Path | ResolvedPaths) -> Path:
    return root.vault if isinstance(root, ResolvedPaths) else fixture_path(root, "vault")


def _digest(root: Path | ResolvedPaths, relative: str) -> str:
    return hashlib.sha256((_vault(root) / relative).read_bytes()).hexdigest()


def _all_files(root: Path) -> dict[str, bytes]:
    return {
        f"{kind}/{path.relative_to(selected).as_posix()}": path.read_bytes()
        for kind in ("control", "vault", "state", "runtime")
        for selected in [root if kind == "control" else fixture_path(root, kind)]
        for path in selected.rglob("*")
        if path.is_file()
    }


def test_daily_fragment_is_recomputed_and_bound_to_triage(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    daily = fixture_path(root, "vault") / DAILY
    source_hash = hashlib.sha256(daily.read_bytes()).hexdigest()
    fragment_hash = hashlib.sha256(daily_fragment_bytes(daily.read_text(encoding="utf-8"), "#^daily-origin")).hexdigest()

    report, code = deterministic_triage(
        root,
        source_path=DAILY,
        expected_sha256=source_hash,
        locator="#^daily-origin",
        fragment_sha256=fragment_hash,
    )
    assert code == 0, report
    assert report["status"] == "PROPOSED"

    drift, drift_code = deterministic_triage(
        root,
        source_path=DAILY,
        expected_sha256=source_hash,
        locator="#^daily-origin",
        fragment_sha256="0" * 64,
    )
    assert drift_code == 30
    assert drift["errors"][0]["code"] == "TRIAGE_FRAGMENT_DRIFT"


def test_action_schema_is_separate_from_c18_triage_shape(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    source_hash = _digest(root, SOURCE)
    report, code = generate_draft_note_proposal(
        root,
        source_path=SOURCE,
        expected_sha256=source_hash,
        title="C27 Schema Idea",
    )
    assert code == 0, report
    errors = list(Draft202012Validator(proposal_schema()).iter_errors(report["proposal"]))
    assert not errors
    triage, triage_code = deterministic_triage(root, source_path=SOURCE, expected_sha256=source_hash)
    assert triage_code == 0
    assert list(Draft202012Validator(proposal_schema()).iter_errors(triage["proposal"]))


def test_draft_note_generation_is_create_only_and_replay_safe(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    source_hash = _digest(root, SOURCE)
    before = _all_files(root)

    first, first_code = generate_draft_note_proposal(
        root,
        source_path=SOURCE,
        expected_sha256=source_hash,
        title="C27 Generated Idea",
    )
    second, second_code = generate_draft_note_proposal(
        root,
        source_path=SOURCE,
        expected_sha256=source_hash,
        title="C27 Generated Idea",
    )

    assert first_code == second_code == 0
    assert first["status"] == "PASS"
    assert second["status"] == "NO_OP"
    assert second["replayed"] is True
    assert first["proposal"] == second["proposal"]
    assert first["proposal"]["action"] == "draft_note"
    assert first["proposal"]["requested_mutations"][0]["operation"] == "create_note"
    assert first["proposal"]["diff"]["bounded"] is True
    assert (fixture_path(root, "vault") / first["proposal_path"]).is_file()
    assert not (fixture_path(root, "vault") / "40_Knowledge/Ideas/C27 Generated Idea.md").exists()
    after = _all_files(root)
    changed = set(after) - set(before)
    assert changed == {f"vault/{first['proposal_path']}"}
    assert (fixture_path(root, "vault") / first["proposal_path"]).read_text(encoding="utf-8").count(
        "vaultops:proposal-manifest"
    ) == 1


def test_action_proposals_use_selected_control_vault_and_runtime_roots(tmp_path: Path) -> None:
    roots = make_separate_portable_fixture_roots(
        tmp_path,
        review_queues=True,
    )
    source_hash = _digest(roots, SOURCE)
    first, first_code = generate_draft_note_proposal(
        roots,
        source_path=SOURCE,
        expected_sha256=source_hash,
        title="C27 Separate Root Idea",
    )

    assert first_code == 0, first
    assert first["status"] == "PASS"
    assert first["created"] is True
    # A Pending artifact is a deliberate write even though no canonical target changed.
    assert first["mutation_performed"] is False
    assert (roots.vault / first["proposal_path"]).is_file()
    assert not (roots.vault / "40_Knowledge/Ideas/C27 Separate Root Idea.md").exists()

    candidate_set = roots.control / "candidate-set.json"
    candidate_set.write_text(
        json.dumps([{"path": CANDIDATE, "content_hash": _digest(roots, CANDIDATE)}], ensure_ascii=False),
        encoding="utf-8",
    )
    linked, linked_code = generate_link_suggestions_proposal(
        roots,
        source_path=SOURCE,
        expected_sha256=source_hash,
        candidate_set_file=candidate_set,
        retrieval_profile_id="lexical_v1",
    )

    assert linked_code == 0, linked
    assert linked["status"] == "PASS"
    assert linked["proposal"]["bindings"]["candidate_set_sha256"]
    assert (roots.vault / linked["proposal_path"]).is_file()
    assert not (roots.state / "receipts").exists()
    assert not (roots.control / "KnowledgeHub").exists()
    assert not (roots.control / "runtime").exists()


def test_link_suggestions_bind_candidate_set_and_do_not_mutate_source(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    source_hash = _digest(root, SOURCE)
    candidate_hash = _digest(root, CANDIDATE)
    candidate_file = root / "candidate-set.json"
    candidate_file.write_text(
        json.dumps([{"path": CANDIDATE, "content_hash": candidate_hash}], ensure_ascii=False),
        encoding="utf-8",
    )
    source_before = (fixture_path(root, "vault") / SOURCE).read_bytes()

    report, code = generate_link_suggestions_proposal(
        root,
        source_path=SOURCE,
        expected_sha256=source_hash,
        candidate_set_file=candidate_file,
        retrieval_profile_id="lexical_v1",
    )

    assert code == 0, report
    assert report["status"] == "PASS"
    proposal = report["proposal"]
    assert proposal["action"] == "link_suggestions"
    assert proposal["bindings"]["candidate_set_sha256"]
    assert proposal["bindings"]["retrieval_profile_id"] == "lexical_v1"
    assert proposal["suggested_links"][0]["wikilink"] == "[[정보의 빈칸은 공포의 상상을 강화한다]]"
    assert (fixture_path(root, "vault") / SOURCE).read_bytes() == source_before
    assert "related:" in proposal["diff"]["patch"]


def test_normalize_proposal_is_hash_bound_and_does_not_write_target(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    source_file = fixture_path(root, "vault") / SOURCE
    original = source_file.read_text(encoding="utf-8")
    _, remaining = original.split("---\n", 1)
    frontmatter, body = remaining.split("---\n", 1)
    lines = frontmatter.splitlines()
    schema_index = lines.index("schema_version: 1")
    id_index = lines.index("id: 11111111-1111-4111-8111-111111111111")
    lines[schema_index], lines[id_index] = lines[id_index], lines[schema_index]
    reordered = "\n".join(lines) + "\n"
    source_file.write_text(f"---\n{reordered}---\n{body}", encoding="utf-8")
    source_hash = _digest(root, SOURCE)
    before = source_file.read_bytes()

    report, code = generate_normalize_proposal(
        root,
        source_path=SOURCE,
        expected_sha256=source_hash,
    )

    assert code == 0, report
    assert report["status"] == "PASS"
    assert report["proposal"]["action"] == "normalize"
    assert report["proposal"]["requested_mutations"][0]["operation"] == "update_note"
    assert report["proposal"]["target"]["expected_sha256"] == source_hash
    assert source_file.read_bytes() == before


def test_generated_proposal_enters_c19_review_without_applying_target(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    source_hash = _digest(root, SOURCE)
    generated, generated_code = generate_draft_note_proposal(
        root,
        source_path=SOURCE,
        expected_sha256=source_hash,
        title="C27 C19 Compatible Idea",
    )
    assert generated_code == 0, generated

    reviewed, review_code = review_proposals(root, proposal_path=generated["proposal_path"])
    assert review_code == 0, reviewed
    assert reviewed["status"] == "PASS"
    proposal_hash = generated["proposal_sha256"]
    approved, approve_code = approve_proposal(
        root,
        proposal_path=generated["proposal_path"],
        expected_sha256=proposal_hash,
    )
    assert approve_code == 0, approved
    assert approved["status"] == "PASS"
    assert not (fixture_path(root, "vault") / "40_Knowledge/Ideas/C27 C19 Compatible Idea.md").exists()
