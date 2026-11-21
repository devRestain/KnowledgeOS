"""C5 document stewardship through the public Owner API and Vault assets."""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from pathlib import Path

from bases_eval import matches
from conftest import conforming_note

from knowledgeos.notes import parse
from knowledgeos.proposals import parse as parse_proposal
from knowledgeos.util import iso


def digest(kos):
    receipt = kos.run("digest.run", {}, actor="schedule/digest")
    assert receipt["status"] == "completed", receipt
    return receipt


def proposal_paths(vault):
    return sorted(p.relative_to(vault.root).as_posix() for p in vault.root.glob("01_AI_Review/Proposals/*.md"))


def test_DOC_1_old_capture_in_inbox_base_and_digest(kos, vault, clock):
    path = "00_Inbox/Four days.md"
    text = conforming_note("capture", "unprocessed").replace("2026-11-01T09:00:00+09:00", iso(clock.now() - timedelta(days=4)))
    vault.write(path, text, age=timedelta(days=4))
    system = os.environ.get("KNOWLEDGEOS_VAULT_SYSTEM")
    assert system, "KNOWLEDGEOS_VAULT_SYSTEM must name the read-only 99_System mount provided by make test"
    assert matches(Path(system) / "Bases/Inbox.base", "Older than 3 days", parse(path, vault.read(path)), mtime=clock.now() - timedelta(days=4), now=clock.now())
    assert digest(kos)["outcome"]["counts"]["inbox_older_3_days"] == 1
    vault.write(path, text.replace("status: unprocessed", "status: processed"))
    assert not matches(Path(system) / "Bases/Inbox.base", "Older than 3 days", parse(path, vault.read(path)), mtime=clock.now(), now=clock.now())
    assert digest(kos)["outcome"]["counts"]["inbox_older_3_days"] == 0


def test_DOC_2_duplicate_ids_stay_searchable_and_ambiguous_targets_refused(kos, vault):
    shared = str(uuid.uuid4())
    paths = ["40_Knowledge/Ideas/Twin A.md", "40_Knowledge/Ideas/Twin B.md"]
    for path in paths:
        vault.write(path, f"---\nid: {shared}\ntype: idea\nai_policy: approve\n---\nShared duplicate token.\n")
    results = kos.search(mode="keyword", query="Shared duplicate token")["results"]
    assert sorted(item["path"] for item in results) == paths
    duplicates = [item for item in kos.get("/api/attention")[1] if item["kind"] == "duplicate_id"]
    assert len(duplicates) == 1 and duplicates[0]["paths"] == paths
    assert all(path in duplicates[0]["detail"] for path in paths)
    refusal = kos.run("standardize.preview", {"paths": [f"id:{shared}"]})
    assert refusal["status"] == "refused" and "ambiguous" in refusal["notice"]
    refusal = kos.run("organize", {"paths": [f"id:{shared}"]})
    assert refusal["status"] == "refused" and "ambiguous" in refusal["notice"]


def test_DOC_3_conflict_is_skipped_and_attention_names_it(kos, vault):
    path = "40_Knowledge/Ideas/Conflict.md"
    raw = "<<<<<<< ours\nOne\n=======\nTwo\n>>>>>>> theirs\n"
    vault.write(path, raw, age=timedelta(hours=1))
    receipt = kos.run("standardize.auto", {}, actor="schedule/standardize")
    assert {"path": path, "reason": "conflict"} in receipt["outcome"]["skipped"]
    assert receipt["outcome"]["applied"] == []
    digest(kos)
    assert vault.text(path) == raw
    assert any(item["kind"] == "conflict" and item["paths"] == [path] for item in kos.get("/api/attention")[1])


def test_DOC_4_done_project_has_exactly_one_archive_proposal(kos, vault):
    target = "20_Projects/Finished/Finished.md"
    original = conforming_note("project", "done", body="User project body.\n")
    vault.write(target, original)
    first = digest(kos)
    [path] = proposal_paths(vault)
    note = parse(path, vault.read(path))
    proposal = parse_proposal(note)
    assert proposal is not None
    assert note.data["kos_capability"] == "C5"
    assert proposal.target_path == target and proposal.target_sha == vault.sha(target)
    assert proposal.checked == {"archive-status", "archive-move"}
    assert [(c["op"], c.get("key"), c.get("value"), c.get("to")) for c in proposal.changes] == [
        ("set", "status", "archived", None), ("move", None, None, "90_Archive")]
    assert "```diff" in note.body and "```json kos-changes" in note.body
    assert "states/" not in note.body.lower() and "blobs/" not in note.body.lower()
    before = vault.snapshot()
    second = digest(kos)
    assert second["outcome"]["proposals"] == [{"path": path, "status": "unchanged"}]
    assert vault.snapshot() == before
    assert vault.text(target) == original
    assert first["outcome"]["counts"]["proposals"] == 1


def test_user_edited_open_proposal_is_never_overwritten_or_duplicated(kos, vault):
    target = "20_Projects/Done/Done.md"
    vault.write(target, conforming_note("project", "done"))
    digest(kos)
    [path] = proposal_paths(vault)
    edited = vault.text(path).replace("- [x] Move", "- [ ] Move") + "\nMy review.\n"
    vault.write(path, edited)
    receipt = digest(kos)
    assert receipt["outcome"]["proposals"] == [{"path": path, "status": "user_edited"}]
    assert proposal_paths(vault) == [path]
    assert vault.text(path) == edited


def test_target_change_marks_stale_then_expires_after_seven_days(kos, vault, clock):
    target = "20_Projects/Done/Done.md"
    original = conforming_note("project", "done")
    vault.write(target, original)
    digest(kos)
    [path] = proposal_paths(vault)
    vault.write(target, original.replace("status: done", "status: active"))
    receipt = digest(kos)
    assert receipt["outcome"]["stale"] == [path]
    assert parse(path, vault.read(path)).status == "stale"
    assert any(item["kind"] == "stale_proposal" for item in kos.get("/api/attention")[1])
    clock.advance(days=8)
    receipt = digest(kos)
    assert receipt["outcome"]["expired"] == [path]
    assert proposal_paths(vault) == []
    assert "status: active" in vault.text(target)


def test_deleted_target_becomes_stale_and_moved_id_target_does_not(kos, vault):
    target = "20_Projects/Done/Done.md"
    vault.write(target, conforming_note("project", "done"))
    digest(kos)
    [path] = proposal_paths(vault)
    moved = "20_Projects/Moved/Moved.md"
    vault.path(moved).parent.mkdir(parents=True)
    vault.path(target).rename(vault.path(moved))
    # Avoid a fresh lifecycle suggestion; moving alone does not change the preimage.
    assert not any(item["kind"] == "stale_proposal" for item in kos.get("/api/attention")[1])
    vault.path(moved).unlink()
    digest(kos)
    assert parse(path, vault.read(path)).status == "stale"


def test_recent_autofix_is_attention_without_confidential_policy_coupling(kos, vault):
    vault.write("40_Knowledge/Ideas/Secret.md", conforming_note("idea", "seed", extra="sensitivity: confidential\n"))
    vault.write("00_Inbox/Fix.md", "Needs standardization.\n", age=timedelta(hours=1))
    kos.run("standardize.auto", {}, actor="schedule/standardize")
    items = kos.get("/api/attention")[1]
    assert not any(item["kind"] == "confidential_policy" for item in items)
    assert any(item["kind"] == "autofix" and item["paths"] == ["00_Inbox/Fix.md"] for item in items)


def test_proposal_state_loss_does_not_authorize_overwriting_existing_output(make_kos, vault, tmp_path):
    kos = make_kos()
    target = "20_Projects/Done/Done.md"
    vault.write(target, conforming_note("project", "done"))
    digest(kos)
    [path] = proposal_paths(vault)
    kos.close()
    kos = make_kos(state=tmp_path / "fresh-state")
    before = vault.read(path)
    receipt = digest(kos)
    assert receipt["outcome"]["proposals"] == [{"status": "user_edited", "path": path}]
    assert proposal_paths(vault) == [path] and vault.read(path) == before


def test_open_lifecycle_proposal_regenerates_in_place_when_target_changes(kos, vault):
    target = "20_Projects/Done/Done.md"
    vault.write(target, conforming_note("project", "done"))
    digest(kos)
    [path] = proposal_paths(vault)
    original_id = parse(path, vault.read(path)).note_id
    vault.write(target, vault.text(target) + "More context.\n")
    receipt = digest(kos)
    assert receipt["outcome"]["proposals"] == [{"status": "regenerated", "path": path}]
    proposal = parse_proposal(parse(path, vault.read(path)))
    assert proposal.proposal_id == original_id
    assert proposal.target_sha == vault.sha(target) and proposal.status == "open"
    assert proposal_paths(vault) == [path]


def test_invalid_yaml_and_rotating_revisit_are_computed_not_notes(kos, vault, clock):
    vault.write("40_Knowledge/Ideas/Broken.md", "---\ntitle: [broken\n---\n")
    for title in ("A", "B", "C", "D"):
        vault.write(f"40_Knowledge/Ideas/{title}.md", conforming_note("idea", "seed"), age=timedelta(days=40))
    first = kos.get("/api/attention")[1]
    assert any(item["kind"] == "invalid_yaml" for item in first)
    first_revisit = [item["paths"] for item in first if item["kind"] == "revisit"]
    assert len(first_revisit) == 3
    assert [item["paths"] for item in kos.get("/api/attention")[1] if item["kind"] == "revisit"] == first_revisit
    clock.advance(days=1)
    assert [item["paths"] for item in kos.get("/api/attention")[1] if item["kind"] == "revisit"] != first_revisit
    assert proposal_paths(vault) == []
