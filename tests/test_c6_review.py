"""REV acceptance: human decisions through durable HTTP request receipts."""

from __future__ import annotations

from datetime import timedelta

from conftest import conforming_note, organize_scripts

from knowledgeos.notes import parse

TARGET = "40_Knowledge/Ideas/Lamp.md"


def make_proposal(kos, vault):
    original = "---\nai_policy: approve\ncustom: 'keep me'\n---\nA lamp changes thinking.\n"
    vault.write(TARGET, original)
    vault.write("40_Knowledge/Notes/Light.md", conforming_note("knowledge", "seed", body="A lamp lights a notebook. [[Lamp]]\n"))
    organize_scripts(kos.runner, title="Light and thought", tags=("lighting",))
    receipt = kos.run("organize", {"paths": [TARGET], "digests": {TARGET: vault.sha(TARGET)}})
    assert receipt["status"] == "completed", receipt
    path = receipt["outcome"]["proposals"][0]["proposal"]
    return original, path, receipt


def test_REV_1_only_checked_changes_apply_and_proposal_is_deleted(kos, vault):
    original, path, _ = make_proposal(kos, vault)
    vault.write(path, vault.text(path).replace("- [x] tags:", "- [ ] tags:"))
    receipt = kos.run("review.apply", {"path": path, "digest": vault.sha(path)})
    assert receipt["status"] == "completed" and receipt["outcome"]["state"] == "applied", receipt
    assert receipt["outcome"]["unchecked"] == ["tag-1"]
    note = parse(TARGET, vault.read(TARGET))
    assert note.type == "idea" and note.status == "seed" and note.title == "Light and thought"
    assert note.list_value("tags") == []
    assert note.list_value("related") == ["[[40_Knowledge/Notes/Light]]"]
    assert note.body == parse(TARGET, original.encode()).body
    assert "ai_policy: approve\ncustom: 'keep me'\n" in vault.text(TARGET)
    assert not vault.path(path).exists()


def test_REV_2_edited_target_stays_unchanged_and_acceptance_is_not_applied(kos, vault):
    original, path, producing = make_proposal(kos, vault)
    edited = original + "User addition.\n"
    vault.write(TARGET, edited)
    receipt = kos.run("review.apply", {"path": path, "digest": vault.sha(path)})
    assert receipt["status"] == "unmet", receipt
    assert receipt["outcome"]["state"] == "not_applied" and receipt["outcome"]["accepted"]
    assert "organize again" in receipt["notice"]
    assert vault.text(TARGET) == edited
    assert parse(path, vault.read(path)).status == "stale"
    status, record = kos.get(f"/api/works/{producing['work_id']}")
    assert status == 200 and receipt["outcome"]["action_id"] in record["actions"]
    assert kos.get(f"/api/requests/{receipt['request_id']}")[1] == receipt
    regenerated = kos.run("organize", {"paths": [TARGET], "digests": {TARGET: vault.sha(TARGET)}})
    assert regenerated["status"] == "completed", regenerated
    assert regenerated["outcome"]["proposals"][0]["proposal"] == path
    assert regenerated["outcome"]["proposals"][0]["status"] == "regenerated"
    assert parse(path, vault.read(path)).status == "open"


def test_REV_3_undo_refuses_after_user_edit_and_points_to_git(kos, vault):
    vault.write(TARGET, "A lamp changes thought.\n", age=timedelta(hours=1))
    fixed = kos.run("standardize.auto", {}, actor="schedule/standardize")
    assert fixed["status"] == "completed", fixed
    assert fixed["outcome"]["applied"] == [TARGET]
    edited = vault.text(TARGET) + "User addition after autofix.\n"
    vault.write(TARGET, edited)
    before = vault.snapshot()
    receipt = kos.run("review.undo", {"path": TARGET, "digest": vault.sha(TARGET)})
    assert receipt["status"] == "refused" and receipt["outcome"]["state"] == "not_applied", receipt
    assert "Git history" in receipt["notice"]
    assert vault.snapshot() == before and vault.text(TARGET) == edited


def test_REV_4_timeout_replay_and_get_receipt_never_apply_twice(kos, vault):
    _, path, producing = make_proposal(kos, vault)
    params = {"path": path, "digest": vault.sha(path)}
    # Treat the first POST response as lost; reconnect using the same stable request identity.
    status, _ = kos.submit("review.apply", params, request_id="lost-apply-response")
    assert status == 202
    first = kos.wait("lost-apply-response")
    assert first["status"] == "completed", first
    after = vault.snapshot()
    status, replay = kos.submit("review.apply", params, request_id="lost-apply-response")
    assert status == 200 and replay == first
    status, recovered = kos.get("/api/requests/lost-apply-response")
    assert status == 200 and recovered == first
    assert recovered["outcome"]["state"] == "applied"
    assert vault.snapshot() == after
    record = kos.get(f"/api/works/{producing['work_id']}")[1]
    assert record["actions"].count(first["outcome"]["action_id"]) == 1
    effects = [effect for effect in record["effects"] if effect["path"] == TARGET and effect["op"] == "replace" and effect["state"] == "applied"]
    assert len(effects) == 1


def test_REV_reject_records_decision_and_never_changes_target(kos, vault):
    original, path, _ = make_proposal(kos, vault)
    receipt = kos.run("review.reject", {"path": path, "digest": vault.sha(path)})
    assert receipt["status"] == "completed" and receipt["outcome"]["state"] == "applied", receipt
    assert receipt["outcome"]["rejected"] and receipt["outcome"]["action_id"]
    assert vault.text(TARGET) == original and not vault.path(path).exists()


def test_REV_undo_restores_last_standardization_once(kos, vault):
    original = "A lamp changes thought.\n"
    vault.write(TARGET, original, age=timedelta(hours=1))
    fixed = kos.run("standardize.auto", {}, actor="schedule/standardize")
    assert fixed["status"] == "completed", fixed
    receipt = kos.run("review.undo", {"path": TARGET, "digest": vault.sha(TARGET)})
    assert receipt["status"] == "completed" and receipt["outcome"]["state"] == "applied", receipt
    assert vault.text(TARGET) == original
    before = vault.snapshot()
    again = kos.run("standardize.auto", {}, actor="schedule/standardize")
    assert again["outcome"]["applied"] == [] and vault.snapshot() == before
    repeated = kos.run("review.undo", {"path": TARGET, "digest": vault.sha(TARGET)})
    assert repeated["status"] == "refused" and "Git history" in repeated["notice"]
    assert vault.snapshot() == before


def test_REV_apply_works_after_originating_state_is_lost(make_kos, vault, tmp_path):
    kos = make_kos()
    _, path, _ = make_proposal(kos, vault)
    kos.close()
    kos = make_kos(state=tmp_path / "fresh-state")
    receipt = kos.run("review.apply", {"path": path, "digest": vault.sha(path)})
    assert receipt["status"] == "completed" and receipt["outcome"]["state"] == "applied", receipt
    assert parse(TARGET, vault.read(TARGET)).title == "Light and thought"
    assert not vault.path(path).exists()
