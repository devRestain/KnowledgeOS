"""ENH-8: model teammates ask System One closed questions about notes read in the Work."""

from __future__ import annotations

import pytest
from conftest import (
    conforming_note,
    enhance_scripts,
    focus_and_candidates,
    system_one_settings,
)

IDEA = "40_Knowledge/Ideas/Tools shape thinking.md"
RELATED = "40_Knowledge/Notes/Handwriting.md"


def seed(vault):
    vault.write(IDEA, conforming_note("idea", "seed", body="Tools shape how we think.\n"))
    vault.write(RELATED, conforming_note("knowledge", "seed", body="Writing by hand slows me down. [[Tools shape thinking]]\n"))


def asked(decisions):
    return [call for call in decisions.calls if any(q["name"] == "answer" for q in call["questions"])]


def run(kos, vault, script, role="connector"):
    enhance_scripts(kos.runner)
    final = kos.runner.scripts[role]
    kos.runner.script(role, lambda session: (script(session), final(session)))
    return kos.run("enhance", {"mode": "develop", "path": IDEA, "digest": vault.sha(IDEA)})


@pytest.mark.parametrize("role", ["connector", "challenger", "synthesizer"])
def test_ENH_8_teammate_asks_choice_and_predicate_about_read_notes(make_kos, vault, decisions, tmp_path, role):
    kos = make_kos(settings={"system_one": system_one_settings(decisions, tmp_path)})
    seed(vault)
    decisions.choose("answer", "same subject", 0.9)
    seen = {}

    def script(session):
        _, handles = focus_and_candidates(session)
        seen["choice"] = session.call("system_one", handle=handles[-1], question="How does it relate to tools?",
                                      choices=["same subject", "unrelated"])
        decisions.predict("answer", 0.2)
        seen["predicate"] = session.call("system_one", handle=handles[-1], question="Does it disagree?")
        seen["unread"] = session.call("system_one", handle="e999", question="Anything?")

    receipt = run(kos, vault, script, role)
    assert receipt["status"] == "completed", receipt
    assert seen["choice"]["status"] == "answered" and seen["choice"]["choice"] == "same subject"
    assert seen["predicate"]["status"] == "answered" and seen["predicate"]["holds"] is False
    assert seen["unread"]["status"] == "not_read"
    calls = asked(decisions)
    assert len(calls) == 2
    assert [option["value"] for option in calls[0]["questions"][0]["choices"]] == ["same subject", "unrelated", "none_of_these"]
    assert "Writing by hand" in str(calls[0]["input"])
    report = receipt["outcome"]["report"]
    assert "same subject" not in vault.text(report)


def test_ENH_8_denied_note_and_step_limit_send_nothing_more(make_kos, vault, decisions, tmp_path):
    kos = make_kos(settings={"system_one": system_one_settings(decisions, tmp_path)})
    seed(vault)
    decisions.predict("answer", 0.9)
    seen = []

    def script(session):
        _, handles = focus_and_candidates(session)
        for _ in range(11):
            seen.append(session.call("system_one", handle=handles[0], question="Is it about tools?")["status"])
        vault.write(RELATED, conforming_note("knowledge", "seed", body="Now private.\n", policy="deny"))
        seen.append(session.call("system_one", handle=handles[-1], question="Is it about tools?")["status"])

    run(kos, vault, script)
    assert seen[:10] == ["answered"] * 10
    assert seen[10] == "limit_reached"
    assert seen[11] == "denied"
    assert len(asked(decisions)) == 10
    assert "Now private" not in decisions.sent_text()
