"""ORG-7: System One gives the classifier advisory type/status hints; the classifier still decides."""

from __future__ import annotations

from conftest import conforming_note, organize_scripts, system_one_settings

from knowledgeos.notes import parse
from knowledgeos.proposals import parse as parse_proposal

TARGET = "00_Inbox/Tools.md"
UNTYPED = "---\nai_policy: approve\n---\nTools change how I think.\n"


def spy(runner, seen, *, before_call=None):
    """Exercise the classifier tool through its bound ScriptedRunner session."""
    original = runner.scripts["classifier"]

    def script(session):
        context = session.call("work_context")
        assert "system_one" not in context
        for target in context["targets"]:
            assert "system_one" not in target
            restore = before_call() if before_call else lambda: None
            try:
                answer = session.call("system_one", handle=target["target"])
                repeated = session.call("system_one", handle=target["target"])
                assert repeated == answer
                seen.append(answer)
            finally:
                restore()
        original(session)

    runner.script("classifier", script)


def proposals(vault):
    return sorted(path.relative_to(vault.root).as_posix() for path in vault.root.glob("01_AI_Review/Proposals/*.md"))


def start(make_kos, decisions, tmp_path):
    return make_kos(settings={"system_one": system_one_settings(decisions, tmp_path)})


def organize(kos, vault, paths=(TARGET,)):
    return kos.run("organize", {"paths": list(paths), "digests": {path: vault.sha(path) for path in paths}})


def test_ORG_7_tool_is_advisory_and_reuses_recorded_answer_and_classifier_still_decides(make_kos, vault, decisions, tmp_path):
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
    answer = seen[0]
    assert answer["status"] == "answered" and answer["advisory"]
    hint = answer["suggestion"]
    assert hint["type"]["choice"] == "idea" and hint["type"]["confidence"] == 0.9
    assert hint["type"]["top"][0] == {"value": "idea", "probability": 0.9}
    assert "status" not in hint
    work = kos.get(f"/api/works/{receipt['work_id']}")[1]
    assert work["system_one"][TARGET]["suggestion"] == hint
    [path] = proposals(vault)
    proposal = parse_proposal(parse(path, vault.read(path)))
    assert any(c.get("values", {}).get("type") == "question" for c in proposal.changes)
    assert not any(c.get("values", {}).get("type") == "idea" for c in proposal.changes)
    assert vault.text(TARGET) == UNTYPED


def test_ORG_7_known_type_also_asks_for_status(make_kos, vault, decisions, tmp_path):
    kos = start(make_kos, decisions, tmp_path)
    target = "40_Knowledge/Ideas/Tools.md"
    vault.write(target, conforming_note("idea", "seed", body="Tools change how I think.\n", policy="approve"))
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
    hint = seen[0]["suggestion"]
    assert hint["status"]["choice"] == "developing" and hint["type"]["choice"] == "idea"


def test_ORG_7_target_turning_deny_is_not_sent(make_kos, vault, decisions, tmp_path):
    kos = start(make_kos, decisions, tmp_path)
    vault.write(TARGET, UNTYPED)
    organize_scripts(kos.runner)
    seen = []

    def restrict():
        vault.write(TARGET, UNTYPED.replace("approve", "deny"))
        return lambda: vault.write(TARGET, UNTYPED)

    spy(kos.runner, seen, before_call=restrict)
    receipt = organize(kos, vault)
    assert receipt["status"] == "completed", receipt
    assert seen[0]["status"] == "denied" and decisions.calls == []


def test_ORG_7_unavailable_system_one_answers_unavailable_and_work_completes(make_kos, vault, decisions, tmp_path):
    kos = start(make_kos, decisions, tmp_path)
    vault.write(TARGET, UNTYPED)
    decisions.down = True
    organize_scripts(kos.runner, note_type="question", status="open")
    seen = []
    spy(kos.runner, seen)
    receipt = organize(kos, vault)
    assert receipt["status"] == "completed", receipt
    assert decisions.calls == [] and seen[0]["status"] == "unavailable"
    assert vault.text(TARGET) == UNTYPED
    assert len(proposals(vault)) == 1


def test_ORG_7_unconfigured_system_one_tool_answers_unconfigured(kos, vault):
    vault.write(TARGET, UNTYPED)
    organize_scripts(kos.runner, note_type="question", status="open")
    seen = []
    spy(kos.runner, seen)
    receipt = organize(kos, vault)
    assert receipt["status"] == "completed", receipt
    assert seen[0]["status"] == "unconfigured"


def test_linker_can_ask_system_one_about_a_read_note(make_kos, vault, decisions, tmp_path):
    kos = start(make_kos, decisions, tmp_path)
    vault.write(TARGET, UNTYPED)
    decisions.predict("answer", 0.8)
    organize_scripts(kos.runner)
    original = kos.runner.scripts["linker"]
    seen = []

    def script(session):
        target = session.call("work_context")["targets"][0]["target"]
        session.call("read", handle=target)
        seen.append(session.call("system_one", handle=target, question="Does this note discuss tools?"))
        original(session)

    kos.runner.script("linker", script)
    receipt = organize(kos, vault)
    assert receipt["status"] == "completed", receipt
    assert seen[0]["holds"] is True and seen[0]["probability"] == 0.8
    assert [q["name"] for call in decisions.calls for q in call["questions"]] == ["answer"]


def test_ORG_7_cached_suggestion_is_withheld_after_policy_changes(make_kos, vault, decisions, tmp_path):
    kos = start(make_kos, decisions, tmp_path)
    vault.write(TARGET, UNTYPED)
    decisions.choose("type", "idea", 0.9)
    organize_scripts(kos.runner)
    original = kos.runner.scripts["classifier"]
    seen = []

    def script(session):
        target = session.call("work_context")["targets"][0]["target"]
        seen.append(session.call("system_one", handle=target)["status"])
        vault.write(TARGET, UNTYPED.replace("approve", "deny"))
        try:
            seen.append(session.call("system_one", handle=target)["status"])
        finally:
            vault.write(TARGET, UNTYPED)
        original(session)

    kos.runner.script("classifier", script)
    receipt = organize(kos, vault)
    assert receipt["status"] == "completed", receipt
    assert seen == ["answered", "denied"]
    assert len(decisions.calls) == 1
