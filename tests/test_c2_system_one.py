"""C2 rule 9: System One decides closed-set frontmatter values (docs/CAPABILITIES.md STD-9..STD-11),
through the ExOps API against a fake Decisions API on a real loopback socket."""

from __future__ import annotations

import json
from datetime import timedelta

from conftest import conforming_note, system_one_settings

from knowledgeos.notes import parse

NOTES = "40_Knowledge/Notes/"
OLD = timedelta(hours=1)


def preview(kos, *paths):
    receipt = kos.run("standardize.preview", {"paths": list(paths),
                                              "digests": {path: kos.vault.sha(path) for path in paths}})
    assert receipt["status"] == "completed", receipt
    return receipt


def automatic(kos):
    receipt = kos.run("standardize.auto", {}, actor="schedule/standardize")
    assert receipt["status"] == "completed", receipt
    return receipt


def test_STD_9_manual_preview_maps_type_then_status_and_applies_after_confirmation(make_kos, vault, decisions,
                                                                                   tmp_path):
    kos = make_kos(settings={"system_one": system_one_settings(decisions, tmp_path)})
    path = "생각 메모.md"
    original = "---\nstatus: 초안\nsensitivity: secret-ish\n---\n도구가 생각의 모양을 바꾼다는 주장.\n"
    vault.write(path, original, age=OLD)
    decisions.choose("type", "knowledge", 0.92)
    decisions.choose("status", "developing", 0.81)

    receipt = preview(kos, path)
    [planned] = receipt["outcome"]["notes"]
    judged = [(change["key"], change["after"]) for change in planned["changes"] if change["rule"] == "system_one"]
    assert judged == [("type", "knowledge"), ("status", "developing")]
    assert [(record["key"], record["value"], record["confidence"]) for record in planned["decisions"]] == [
        ("type", "knowledge", 0.92), ("status", "developing", 0.81)]
    assert vault.text(path) == original  # nothing written before confirmation
    # One request per question round: type first, then the status that type makes invalid.
    assert [[question["name"] for question in call["questions"]] for call in decisions.calls] == [["type"], ["status"]]
    assert "도구가 생각의 모양을" in decisions.sent_text()
    assert "sensitivity" not in decisions.questions() and "ai_policy" not in decisions.questions()
    assert decisions.keys == ["Bearer sk-test-key"] * 2
    status_question = decisions.calls[1]["questions"][0]
    assert {choice["value"] for choice in status_question["choices"]} == {
        "seed", "developing", "evergreen", "archived", "none_of_these"}

    done = kos.run("standardize.confirm", {"preview_id": receipt["outcome"]["preview_id"]})
    assert done["status"] == "completed", done
    note = parse(path, vault.read(path))
    assert (note.data["type"], note.data["status"], note.data["sensitivity"]) == (
        "knowledge", "developing", "secret-ish")
    [change] = kos.changes()
    assert change["kind"] == "manual"
    recorded = kos.owner.store.read_stream("system_one")
    assert [(entry["purpose"], entry["ref"], entry["outcome"]) for entry in recorded] == [
        ("standardize", receipt["work_id"], "answered")] * 2
    assert all("생각" not in str(entry) for entry in recorded)  # references and digests, never content


def test_STD_10_automatic_path_applies_confident_answers_only_for_approve_notes(make_kos, vault, decisions,
                                                                                tmp_path):
    kos = make_kos(settings={"system_one": system_one_settings(decisions, tmp_path)})
    confident = NOTES + "Confident.md"
    unsure = NOTES + "Unsure.md"
    private = NOTES + "Private.md"
    capture = "00_Inbox/Link.md"
    vault.write(confident, conforming_note("knowledge", "draft", body="CONFIDENT body\n", policy="approve"), age=OLD)
    vault.write(unsure, conforming_note("knowledge", "wip", body="UNSURE body\n", policy="approve"), age=OLD)
    vault.write(private, conforming_note("knowledge", "draft", body="PRIVATE body\n", policy="ask"), age=OLD)
    vault.write(capture, conforming_note("capture", "unprocessed", body="https://example.org/x\n",
                                         policy="approve"), age=OLD)
    decisions.rules["status"] = lambda question, body: {
        "choice": "developing", "confidence": 0.6 if "UNSURE" in json.dumps(body["input"]) else 0.9}
    decisions.choose("capture_kind", "link", 0.95)

    run = automatic(kos)
    assert sorted(run["outcome"]["applied"]) == sorted([confident, capture])
    assert parse(confident, vault.read(confident)).data["status"] == "developing"
    assert parse(capture, vault.read(capture)).data["capture_kind"] == "link"
    assert parse(unsure, vault.read(unsure)).data["status"] == "wip"  # below min_confidence 0.7: untouched
    assert parse(private, vault.read(private)).data["status"] == "draft"
    assert "PRIVATE" not in decisions.sent_text()  # retired policies fail closed for every origin
    work = kos.get(f"/api/works/{run['work_id']}")[1]
    assert private not in {item["path"] for item in work["workspec"]["inputs"]}
    assert {"path": unsure, "key": "status", "reason": "low_confidence"} in run["outcome"]["undecided"]
    autofix = {change["path"]: change for change in kos.changes()}
    assert autofix[confident]["kind"] == "autofix"

    sent = len(decisions.calls)
    again = automatic(kos)
    assert again["outcome"]["applied"] == [] and len(decisions.calls) == sent  # settled: nothing re-asked


def test_STD_11_unavailable_system_one_keeps_deterministic_rules_and_asks_again_next_run(make_kos, vault, decisions,
                                                                                       tmp_path):
    kos = make_kos(settings={"system_one": system_one_settings(decisions, tmp_path)})
    path = NOTES + "Later.md"
    vault.write(path, "---\nstatus: draft\nai_policy: approve\n---\nA claim.\n", age=OLD)
    decisions.down = True

    run = automatic(kos)
    assert run["outcome"]["applied"] == [path]  # rules 1-8 still apply
    note = parse(path, vault.read(path))
    assert note.data["type"] == "knowledge" and note.data["status"] == "draft" and "id" in note.data
    assert run["outcome"]["system_one"].startswith("unavailable")
    assert {"path": path, "key": "status", "reason": "unavailable"} in run["outcome"]["undecided"]
    [failed] = kos.owner.store.read_stream("system_one")
    assert failed["outcome"] == "failed"

    decisions.down = False
    decisions.choose("status", "seed", 0.88)
    kos.clock.advance(minutes=15)  # past the quiet period after the autofix write
    later = automatic(kos)
    assert later["outcome"]["applied"] == [path]
    assert parse(path, vault.read(path)).data["status"] == "seed"


def test_system_one_unconfigured_leaves_judgment_fields_and_reports_health(kos, vault):
    path = NOTES + "Plain.md"
    vault.write(path, conforming_note("knowledge", "draft"), age=OLD)
    receipt = preview(kos, path)
    assert receipt["outcome"]["notes"] == [] and receipt["outcome"]["system_one"] == "unconfigured"
    assert {"path": path, "key": "status", "reason": "unconfigured"} in receipt["outcome"]["undecided"]
    status, health = kos.get("/api/health")
    assert status == 200 and health["dependencies"]["system_one"] == "unconfigured"


def test_system_one_key_file_must_be_private(make_kos, decisions, tmp_path):
    settings = system_one_settings(decisions, tmp_path)
    (tmp_path / "decisions.key").chmod(0o644)
    kos = make_kos(settings={"system_one": settings})
    status, health = kos.get("/api/health")
    assert status == 200 and health["dependencies"]["system_one"] == "unavailable"
