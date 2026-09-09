from __future__ import annotations

import unicodedata
import uuid
from pathlib import Path

import pytest

from vaultops.note_engine import (
    FrontmatterError,
    NoteEngine,
    UnsafePathError,
    parse_frontmatter,
    render_frontmatter,
    validate_path_collisions,
    validate_relation_candidate,
)
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def _engine() -> NoteEngine:
    return NoteEngine(load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml"))


def _common(note_type: str, title: str, status: str, *, deterministic_id: str | None = None) -> dict:
    identifier = deterministic_id or str(uuid.uuid4())
    return {
        "schema_version": 1,
        "id": identifier,
        "type": note_type,
        "title": title,
        "status": status,
        "created": "2026-09-09T09:00:00+09:00",
        "modified": "2026-09-09T09:01:00+09:00",
        "aliases": [],
        "tags": [],
        "sensitivity": "personal",
        "ai_policy": "ask",
        "ai_status": "idle",
    }


def _positive_notes() -> list[tuple[str, dict, dict[str, str]]]:
    notes: list[tuple[str, dict, dict[str, str]]] = []
    notes.append(("00_Inbox/Captures/Thought.md", {**_common("capture", "Thought", "unprocessed"), "captured_from": "mac_cli", "capture_device": "mac", "capture_kind": "thought", "triage_hint": "none", "needs_desktop_review": False}, {}))
    notes.append(("01_AI_Review/Pending/Proposal.md", {**_common("proposal", "Proposal", "pending"), "proposal_id": str(uuid.uuid4()), "source_hashes": ["00_Inbox/Captures/Thought.md|sha256:" + "a" * 64]}, {}))
    notes.append(("10_Journal/Daily/2026-09-09.md", {**_common("daily", "2026-09-09", "open", deterministic_id="daily-2026-09-09"), "period_start": "2026-09-09", "period_end": "2026-09-09"}, {}))
    notes.append(("10_Journal/Weekly/2026-W37.md", {**_common("weekly", "2026-W37", "open", deterministic_id="weekly-2026-w37"), "period_start": "2026-09-07", "period_end": "2026-09-13"}, {}))
    notes.append(("10_Journal/Monthly/2026-09.md", {**_common("monthly", "2026-09", "open", deterministic_id="monthly-2026-09"), "period_start": "2026-09-01", "period_end": "2026-09-30"}, {}))
    notes.append(("20_Projects/Alpha/Alpha.md", {**_common("project", "Alpha", "active"), "outcome": "Ship the first slice", "priority": "high", "focus_rank": 1, "next_action": "Write the validator"}, {}))
    notes.append(("20_Projects/Alpha/Working/Design.md", {**_common("project_note", "Design", "draft"), "projects": ["[[Alpha]]"], "note_kind": "plan"}, {"Alpha": "project"}))
    notes.append(("40_Knowledge/Ideas/Small Idea.md", {**_common("idea", "Small Idea", "seed"), "possibility": "A testable possibility"}, {}))
    notes.append(("40_Knowledge/Questions/Decision.md", {**_common("question", "Decision", "deciding"), "question_kind": "decision", "decision": "Choose the safe path"}, {}))
    notes.append(("20_Projects/Alpha/Artifacts/Report.md", {**_common("artifact", "Report", "final"), "artifact_kind": "report", "projects": ["[[Alpha]]"], "artifact_uri": "https://example.com/report"}, {"Alpha": "project"}))
    notes.append(("30_Areas/Health.md", {**_common("area", "Health", "active"), "standard": "Keep a weekly review", "review_cadence": "weekly", "next_review": "2026-09-16"}, {}))
    notes.append(("40_Knowledge/Notes/Claim.md", {**_common("knowledge", "Claim", "developing"), "claim": "A concise claim", "confidence": "medium"}, {}))
    notes.append(("40_Knowledge/Sources/Source.md", {**_common("source", "Source", "processed"), "source_kind": "article", "source_url": "https://example.com/source"}, {}))
    notes.append(("40_Knowledge/People/Ada.md", {**_common("person", "Ada", "active",), "organization": "Example"}, {}))
    notes.append(("50_Maps/Topics MOC.md", {**_common("moc", "Topics MOC", "active"), "scope": "A topic map"}, {}))
    notes.append(("60_Meetings/2026-09-09 — Review.md", {**_common("meeting", "2026-09-09 — Review", "scheduled"), "meeting_at": "2026-09-09T10:00:00+09:00", "attendees": ["[[Ada]]"]}, {"Ada": "person"}))
    notes.append(("Home.md", {**_common("home", "Home", "active", deterministic_id="home"), "purpose": "Desktop start", "audience": "desktop"}, {}))
    notes.append(("99_System/Readme.md", {**_common("system", "Readme", "active", deterministic_id="system-readme"), "purpose": "System contract"}, {}))
    return notes


@pytest.mark.parametrize("path, properties, target_types", _positive_notes())
def test_all_eighteen_note_types_have_valid_positive_fixtures(path: str, properties: dict, target_types: dict[str, str]) -> None:
    result = _engine().validate_text(path, render_frontmatter(properties, "# body\n"), target_types=target_types)
    assert result.passed, result.as_dict()


def test_duplicate_frontmatter_key_is_rejected() -> None:
    with pytest.raises(FrontmatterError, match="duplicate YAML key"):
        parse_frontmatter("---\ntitle: first\ntitle: second\n---\nbody\n")


def test_writer_quotes_wikilinks_and_round_trips_body() -> None:
    text = render_frontmatter({"projects": ["[[Alpha]]"], "title": "Note"}, "본문\n")
    assert "'[[Alpha]]'" in text
    parsed = parse_frontmatter(text)
    assert parsed.properties["projects"] == ["[[Alpha]]"]
    assert parsed.body == "본문\n"


def test_title_basename_timezone_uuid_and_conditional_errors_are_explicit() -> None:
    properties = _positive_notes()[5][1].copy()
    properties.update({"title": "Wrong", "id": "not-a-uuid", "modified": "2026-09-09T09:01:00"})
    properties.pop("focus_rank")
    properties.pop("next_action")
    result = _engine().validate_properties(properties, "20_Projects/Alpha/Alpha.md")
    codes = {error.code for error in result.errors}
    assert {"NOTE_TITLE_BASENAME_MISMATCH", "NOTE_ID_INVALID", "NOTE_DATETIME_TIMEZONE", "NOTE_CONDITIONAL_PROPERTY"} <= codes


def test_unicode_casefold_and_nfc_collisions_are_rejected() -> None:
    composed = "40_Knowledge/Notes/Café.md"
    decomposed = "40_Knowledge/Notes/Cafe\u0301.md"
    issues = validate_path_collisions([composed, decomposed, "40_Knowledge/Notes/Claim.md", "40_Knowledge/Notes/claim.md"])
    assert {issue.code for issue in issues} == {"PATH_UNICODE_CASE_COLLISION"}
    assert unicodedata.normalize("NFC", decomposed) != decomposed


@pytest.mark.parametrize("path", ["/absolute.md", "../escape.md", "a/../../escape.md", "a\x00.md", ".hidden.md", ".vault-bridge/request.json"])
def test_unsafe_paths_are_rejected(path: str) -> None:
    with pytest.raises(UnsafePathError):
        from vaultops.note_engine import normalize_vault_relative_path

        normalize_vault_relative_path(path)


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafePathError):
        from vaultops.note_engine import resolve_vault_relative_path

        resolve_vault_relative_path(vault, "link/Note.md")


def test_relation_direction_and_unapproved_proposal_are_rejected() -> None:
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")
    direction_errors = validate_relation_candidate(
        "supports", "source", "project", relation_registry=blueprint["relation_registry"]
    )
    proposal_errors = validate_relation_candidate(
        "supports", "source", "knowledge", provenance="ai_proposed", approved=False, relation_registry=blueprint["relation_registry"]
    )
    assert {error.code for error in direction_errors} == {"RELATION_OBJECT_TYPE"}
    assert {error.code for error in proposal_errors} == {"RELATION_PROPOSAL_NOT_APPROVED"}


def test_canonical_relation_requires_resolved_target_and_correct_direction() -> None:
    properties = _positive_notes()[11][1].copy()
    properties["supports"] = ["[[Missing]]"]
    result = _engine().validate_properties(properties, "40_Knowledge/Notes/Claim.md", target_types={})
    assert any(error.code == "RELATION_TARGET_UNRESOLVED" for error in result.errors)

