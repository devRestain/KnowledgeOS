"""C11 System One touchpoints: the free-text prefilter before the Director (SUR-10)."""

from __future__ import annotations

from conftest import conforming_note, system_one_settings
from test_c11_surfaces import IDEA, IDEA_BODY, director

QUESTION = "free_text_command"
WEATHER = "오늘 날씨 알려줘"
OFFERED = ["standardize.preview", "organize", "enhance"]


def prefiltered(make_kos, decisions, tmp_path, vault):
    vault.write(IDEA, conforming_note("idea", "seed", body=IDEA_BODY))
    return make_kos(settings={"system_one": system_one_settings(decisions, tmp_path)})


def test_SUR_10_confident_none_of_these_refuses_free_text_without_the_director(make_kos, decisions, tmp_path, vault):
    kos = prefiltered(make_kos, decisions, tmp_path, vault)
    before = vault.snapshot()
    decisions.choose(QUESTION, "none_of_these", 0.9)

    receipt = kos.run("free_text", {"text": WEATHER})
    assert receipt["status"] == "refused" and receipt["work_id"] is None
    assert "System One found no KnowledgeOS command" in receipt["notice"]
    assert receipt["outcome"] == {"derived_request_id": None, "command": None, "invocation": None,
                                  "system_one": {"choice": "none_of_these", "confidence": 0.9}}
    assert kos.runner.invoked() == [] and kos.get("/api/works")[1] == [] and vault.snapshot() == before

    # Only the request text left the Owner, as one input_text; the question offers the free-text commands.
    [call] = decisions.calls
    assert call["input"] == [{"role": "user", "content": [{"type": "input_text", "text": WEATHER}]}]
    [question] = call["questions"]
    assert question["name"] == QUESTION
    assert [option["value"] for option in question["choices"]] == [*OFFERED, "none_of_these"]
    assert "Tools shape" not in decisions.sent_text() and IDEA not in decisions.sent_text()

    # The call is recorded against the request, and the Director's feedback says it refused.
    [entry] = kos.owner.store.read_stream("system_one")
    assert entry["purpose"] == "free_text" and entry["ref"] == receipt["request_id"]
    items = kos.owner.engine._feedback("director")["items"]
    assert any(item["kind"] == "admission" and item["outcome"] == "refused" for item in items)


def test_SUR_10_other_answers_continue_to_the_director_unchanged(make_kos, decisions, tmp_path, vault):
    kos = prefiltered(make_kos, decisions, tmp_path, vault)
    reason = "Weather is outside KnowledgeOS"

    # A command was chosen.
    decisions.choose(QUESTION, "organize", 0.95)
    kos.runner.script("director", director({"refuse": reason}))
    receipt = kos.run("free_text", {"text": WEATHER})
    assert receipt["status"] == "refused" and reason in receipt["notice"]
    assert len(kos.runner.invoked("director")) == 1
    assert receipt["outcome"]["system_one"] == {"choice": "organize", "confidence": 0.95}

    # none_of_these below the confidence threshold does not settle the request.
    decisions.choose(QUESTION, "none_of_these", 0.4)
    kos.runner.script("director", director({"refuse": reason}))
    receipt = kos.run("free_text", {"text": WEATHER + " 지금"})
    assert reason in receipt["notice"] and len(kos.runner.invoked("director")) == 2
    assert receipt["outcome"]["system_one"] == {"choice": "none_of_these", "confidence": 0.4}

    # A refusal by System One does not settle it either.
    decisions.rules.clear()
    kos.runner.script("director", director({"refuse": reason}))
    receipt = kos.run("free_text", {"text": WEATHER + " 내일"})
    assert reason in receipt["notice"] and len(kos.runner.invoked("director")) == 3
    assert receipt["outcome"]["system_one"] == {"choice": None, "confidence": None}


def test_SUR_10_unavailable_system_one_leaves_free_text_unchanged(make_kos, decisions, tmp_path, vault):
    kos = prefiltered(make_kos, decisions, tmp_path, vault)
    decisions.down = True
    reason = "Weather is outside KnowledgeOS"
    kos.runner.script("director", director({"refuse": reason}))

    receipt = kos.run("free_text", {"text": WEATHER})
    assert receipt["status"] == "refused" and reason in receipt["notice"]
    assert len(kos.runner.invoked("director")) == 1 and receipt["outcome"]["system_one"] == "unavailable"
    assert decisions.calls == []


def test_SUR_10_unconfigured_system_one_adds_nothing_to_the_outcome(kos, vault):
    kos.runner.script("director", director({"refuse": "Weather is outside KnowledgeOS"}))
    receipt = kos.run("free_text", {"text": WEATHER})
    assert receipt["status"] == "refused" and "system_one" not in receipt["outcome"]
    assert len(kos.runner.invoked("director")) == 1
