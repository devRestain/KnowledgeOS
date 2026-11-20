"""ORG-7: System One gives the classifier advisory type/status hints; the classifier still decides."""

from __future__ import annotations

from conftest import conforming_note, organize_scripts, system_one_settings

from knowledgeos.notes import parse
from knowledgeos.proposals import parse as parse_proposal

TARGET = "00_Inbox/Tools.md"
UNTYPED = "---\nai_policy: remote_ok\n---\nTools change how I think.\n"


def spy(runner, seen):
    """Record every `work_context` the classifier receives, then run its scripted behavior."""
    original = runner.scripts["classifier"]

    def script(session):
        call = session.call

        def capture(name, **args):
            value = call(name, **args)
            if name == "work_context":
                seen.append(value)
            return value

        session.call = capture
        original(session)

    runner.script("classifier", script)


def proposals(vault):
    return sorted(path.relative_to(vault.root).as_posix() for path in vault.root.glob("01_AI_Review/Proposals/*.md"))


def start(make_kos, decisions, tmp_path):
    return make_kos(settings={"system_one": system_one_settings(decisions, tmp_path)})


def organize(kos, vault, paths=(TARGET,)):
    return kos.run("organize", {"paths": list(paths), "digests": {path: vault.sha(path) for path in paths}})


def test_ORG_7_hint_is_advisory_and_classifier_still_decides(make_kos, vault, decisions, tmp_path):
    kos = start(make_kos, decisions, tmp_path)
    vault.write(TARGET, UNTYPED)
    decisions.choose("type", "idea", 0.9)
    organize_scripts(kos.runner, note_type="question", status="open")
    seen = []
    spy(kos.runner, seen)
    receipt = organize(kos, vault)
    assert receipt["status"] == "completed", receipt
    [call] = decisions.calls
    assert [q["name"] for q in call["questions"]] == ["type"]
    assert {o["value"] for o in call["questions"][0]["choices"]} == {
        "capture", "idea", "knowledge", "question", "project", "source", "none_of_these"}
    [target] = seen[0]["targets"]
    hint = target["system_one"]
    assert "advisory" in hint["advisory"] and hint["type"]["choice"] == "idea"
    assert hint["type"]["confidence"] == 0.9 and hint["type"]["top"][0] == {"value": "idea", "probability": 0.9}
    assert "status" not in hint
    [path] = proposals(vault)
    proposal = parse_proposal(parse(path, vault.read(path)))
    assert any(c.get("values", {}).get("type") == "question" for c in proposal.changes)
    assert not any(c.get("values", {}).get("type") == "idea" for c in proposal.changes)
    assert vault.text(TARGET) == UNTYPED


def test_ORG_7_known_type_also_asks_for_status(make_kos, vault, decisions, tmp_path):
    kos = start(make_kos, decisions, tmp_path)
    target = "40_Knowledge/Ideas/Tools.md"
    vault.write(target, conforming_note("idea", "seed", body="Tools change how I think.\n", policy="remote_ok"))
    decisions.choose("type", "idea", 0.9)
    decisions.choose("status", "developing", 0.8)
    organize_scripts(kos.runner, note_type="idea", status="mature")
    seen = []
    spy(kos.runner, seen)
    receipt = organize(kos, vault, (target,))
    assert receipt["status"] == "completed", receipt
    [call] = decisions.calls
    assert [q["name"] for q in call["questions"]] == ["type", "status"]
    assert {o["value"] for o in call["questions"][1]["choices"]} == {
        "seed", "developing", "mature", "dropped", "archived", "none_of_these"}
    hint = seen[0]["targets"][0]["system_one"]
    assert hint["status"]["choice"] == "developing" and hint["type"]["choice"] == "idea"


def test_ORG_7_scheduled_run_never_sends_ask_note(make_kos, vault, decisions, tmp_path):
    kos = start(make_kos, decisions, tmp_path)
    secret = "ASK-ONLY-PLANS-796183"
    vault.write(TARGET, conforming_note("capture", "unprocessed", body="Tools for thinking.\n", policy="remote_ok"))
    vault.write("00_Inbox/Private plans.md", conforming_note("capture", "unprocessed", body=f"Tools {secret}\n"))
    decisions.choose("type", "idea", 0.9)
    organize_scripts(kos.runner)
    seen = []
    spy(kos.runner, seen)
    trigger = kos.run("job.run", {"job": "curate"}, actor="schedule/curate")
    receipt = kos.wait(trigger["outcome"]["request_id"])
    assert receipt["status"] == "completed", receipt
    assert len(decisions.calls) == 1
    assert "Tools for thinking." in decisions.sent_text() and secret not in decisions.sent_text()
    assert "Private plans" not in decisions.sent_text()
    assert [t["system_one"]["type"]["choice"] for t in seen[0]["targets"]] == ["idea"]


def test_ORG_7_unavailable_system_one_leaves_work_unchanged(make_kos, vault, decisions, tmp_path):
    kos = start(make_kos, decisions, tmp_path)
    vault.write(TARGET, UNTYPED)
    decisions.down = True
    organize_scripts(kos.runner, note_type="question", status="open")
    seen = []
    spy(kos.runner, seen)
    receipt = organize(kos, vault)
    assert receipt["status"] == "completed", receipt
    assert decisions.calls == [] and "system_one" not in seen[0]["targets"][0]
    assert len(proposals(vault)) == 1


def test_ORG_7_unconfigured_system_one_adds_no_hints(kos, vault):
    vault.write(TARGET, UNTYPED)
    organize_scripts(kos.runner, note_type="question", status="open")
    seen = []
    spy(kos.runner, seen)
    receipt = organize(kos, vault)
    assert receipt["status"] == "completed", receipt
    assert "system_one" not in seen[0]["targets"][0]
