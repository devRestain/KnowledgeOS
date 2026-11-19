"""Shared Team flow rules (Blueprint §4.3, §4.4) exercised through the Thinking Team (C4)."""

from __future__ import annotations

import json
import re

import pytest
from conftest import (
    challenger_script,
    conforming_note,
    connector_script,
    enhance_scripts,
    evaluator_script,
    manager_script,
    synthesizer_script,
)

IDEA = "40_Knowledge/Ideas/Tools shape thinking.md"
RELATED = "40_Knowledge/Notes/Related.md"
FULL = ["retriever", "connector", "challenger", "synthesizer"]


@pytest.fixture
def notes(vault):
    vault.write(IDEA, conforming_note("idea", "seed", body="Tools shape how we think.\n\nA notebook changes what I notice.\n"))
    vault.write(RELATED, conforming_note("knowledge", "seed", body="Writing by hand slows me down. [[Tools shape thinking]]\n"))
    return vault


def develop(kos):
    return kos.run("enhance", {"mode": "develop", "path": IDEA})


def work_of(kos, receipt):
    status, work = kos.get(f"/api/works/{receipt['work_id']}")
    assert status == 200, work
    return work


def no_report(vault):
    folder = vault.path("01_AI_Review/Reports")
    return not folder.exists() or not any(folder.iterdir())


def graph_manager(proposal):
    def script(session):
        session.call("team_view")
        session.call("submit_graph", payload=proposal)

    return script


@pytest.mark.parametrize(
    "proposal",
    [
        {"steps": FULL, "evaluation": "after_last", "eval_instructions": "be lenient with tensions"},
        {"steps": FULL, "evaluation": {"position": "after_last", "instructions": "be lenient"}},
        {"steps": FULL, "evaluation": "after_connector"},
        {"steps": ["retriever", "eval_officer", "synthesizer"], "evaluation": "after_last"},
        {"steps": FULL, "evaluation": "after_last", "route": "another/model"},
    ],
)
def test_team_graph_addressing_the_eval_officer_is_rejected_without_a_segment(kos, notes, proposal):
    enhance_scripts(kos.runner)
    kos.runner.script("thinking_manager", graph_manager(proposal))
    before = notes.snapshot()

    receipt = develop(kos)
    assert receipt["status"] == "failed", receipt
    assert "rejected" in receipt["notice"]
    work = work_of(kos, receipt)
    assert work["segments"] == [] and work["status"] == "failed"
    assert [c["status"] for c in work["compositions"]] == ["rejected"]
    assert work["compositions"][0]["violation"] is True
    assert [e["role"] for e in kos.runner.invoked()] == ["thinking_manager"]  # no corrective turn, nothing else ran
    assert notes.snapshot() == before


def test_team_invalid_graph_gets_one_corrective_turn(kos, notes):
    enhance_scripts(kos.runner)
    diagnostics = []

    def manager(session):
        view = session.call("team_view")
        if session.binding.submission_tool == "submit_graph" and not session.corrective:
            session.call("submit_graph", payload={"steps": ["retriever", "summarizer"], "evaluation": "after_last"})
            return
        diagnostics.extend(session.corrective)
        assert view["pool"]
        manager_script()(session)

    kos.runner.script("thinking_manager", manager)
    receipt = develop(kos)
    assert receipt["status"] == "completed", receipt
    graphs = [e for e in kos.runner.invoked("thinking_manager") if e["submission"] == "submit_graph"]
    assert [e["corrective"] for e in graphs] == [False, True]
    assert any("summarizer" in item and "pool" in item for item in diagnostics)
    assert any("synthesizer must be the last producer" in item for item in diagnostics)


def test_team_invalid_graph_twice_fails_without_a_segment(kos, notes):
    enhance_scripts(kos.runner)
    kos.runner.script("thinking_manager", graph_manager({"steps": ["synthesizer", "connector"], "evaluation": "after_last"}))

    receipt = develop(kos)
    assert receipt["status"] == "failed", receipt
    work = work_of(kos, receipt)
    assert work["segments"] == []
    assert [e["corrective"] for e in kos.runner.invoked("thinking_manager")] == [False, True]
    assert kos.runner.invoked("connector") == [] and no_report(notes)


def test_team_handoff_failing_twice_fails_the_step_and_leaves_its_criteria_unmet(kos, notes):
    enhance_scripts(kos.runner)
    judged = []

    def careless_connector(session):
        session.call("work_context")
        claim = {"text": "A note I never read says otherwise.", "citations": ["e999"]}
        session.call("submit_handoff", payload={"related": [], "connections": [], "tensions": [claim]})

    def evaluator(session):
        judged.append([c["id"] for c in session.call("eval_view")["criteria"]])
        evaluator_script()(session)

    kos.runner.script("connector", careless_connector)
    kos.runner.script("eval_officer", evaluator)
    receipt = develop(kos)
    assert receipt["status"] == "unmet", receipt
    work = work_of(kos, receipt)
    first = work["segments"][0]
    step = next(s for s in first["steps"] if s["role"] == "connector")
    assert step["status"] == "failed" and step["attempts"] == 2 and "e999" in step["error"]
    assert [e["corrective"] for e in kos.runner.invoked("connector")[:2]] == [False, True]
    assert first["assessment"]["unmet"] == ["enh.tensions_supported"]
    assert judged[0] == ["enh.addresses_focus"]  # the failed step's criterion is not sent to the EvalOfficer
    owner_judgment = next(j for j in first["evaluation"]["judgments"] if j["criterion"] == "enh.tensions_supported")
    assert owner_judgment["verdict"] == "unmet" and "connector" in owner_judgment["reason"]
    assert no_report(notes)


def test_team_successor_is_limited_to_remaining_work_and_budget(kos, notes):
    enhance_scripts(kos.runner)
    corrective = []

    def manager(session):
        view = session.call("team_view")
        if session.binding.submission_tool == "submit_graph" and view["remaining_work"] and not session.corrective:
            session.call("submit_graph", payload={"steps": FULL, "evaluation": "after_last"})
            return
        corrective.extend(session.corrective)
        manager_script()(session)

    kos.runner.script("thinking_manager", manager)
    kos.runner.script("eval_officer", [evaluator_script(unmet={"enh.tensions_supported": "unsupported"}), evaluator_script()])
    receipt = develop(kos)
    assert receipt["status"] == "completed", receipt
    assert any("remaining producer budget of 2" in item for item in corrective)
    assert any("challenger" in item and "RemainingWork" in item for item in corrective)
    assert work_of(kos, receipt)["segments"][1]["graph"]["steps"] == ["connector", "synthesizer"]


def test_team_budget_exhausted_ends_unmet_and_writes_nothing(kos, notes):
    enhance_scripts(kos.runner)
    kos.runner.script("eval_officer", evaluator_script(unmet={"enh.tensions_supported": "the notes do not disagree"}))
    before = notes.snapshot()

    receipt = develop(kos)
    assert receipt["status"] == "unmet", receipt
    assert "budget" in receipt["notice"] and "enh.tensions_supported" in receipt["notice"]
    assert receipt["outcome"]["unmet"] == ["enh.tensions_supported"]
    work = work_of(kos, receipt)
    assert len(work["segments"]) == 2 and work["team_review"] is None
    assert work["remaining_work"]["criteria"] == ["enh.tensions_supported"]
    graphs = [e for e in kos.runner.invoked("thinking_manager") if e["submission"] == "submit_graph"]
    assert len(graphs) == 2
    assert notes.snapshot() == before


@pytest.mark.parametrize("role", ["connector", "eval_officer"])
def test_team_unavailable_route_fails_visibly_without_retry(kos, notes, role):
    enhance_scripts(kos.runner)
    del kos.runner.scripts[role]  # the scripted runner raises RoleUnavailable for an unscripted role
    before = notes.snapshot()

    receipt = develop(kos)
    assert receipt["status"] == "failed", receipt
    assert role in receipt["notice"] and "unavailable" in receipt["notice"]
    assert len(kos.runner.invoked(role)) == 1
    work = work_of(kos, receipt)
    assert len(work["segments"]) == 1 and work["team_review"] is None
    if role == "connector":
        step = next(s for s in work["segments"][0]["steps"] if s["role"] == "connector")
        assert step["status"] == "failed" and "unavailable" in step["error"]
        assert kos.runner.invoked("challenger") == [] and kos.runner.invoked("synthesizer") == []
    else:
        assert work["segments"][0]["evaluation"]["status"] == "failed"
    assert notes.snapshot() == before


def test_roles_never_receive_paths_ids_or_digests(kos, notes):
    seen: list[str] = []

    def recording(script):
        def wrapped(session):
            original = session.call

            def call(name, **args):
                result = original(name, **args)
                seen.append(json.dumps(result, ensure_ascii=False, default=str))
                return result

            session.call = call
            script(session)

        return wrapped

    def exploring_connector(session):
        context = session.call("work_context")
        handle = context["focus"]["handles"][0]
        session.call("read", handle=handle)
        for result in session.call("search", query="tools")["results"]:
            session.call("read", handle=result["handle"])
        session.call("related", handle=handle)
        session.call("feedback")
        connector_script()(session)

    enhance_scripts(kos.runner)
    kos.runner.script("thinking_manager", recording(manager_script()))
    kos.runner.script("connector", recording(exploring_connector))
    kos.runner.script("challenger", recording(challenger_script))
    kos.runner.script("synthesizer", recording(synthesizer_script()))
    kos.runner.script("eval_officer", recording(evaluator_script()))

    receipt = develop(kos)
    assert receipt["status"] == "completed", receipt
    assert len(seen) > 10
    secrets = [IDEA, RELATED, IDEA.removesuffix(".md"), "40_Knowledge", receipt["work_id"], receipt["request_id"]]
    for path in (IDEA, RELATED):
        secrets.append(notes.sha(path))
        secrets.append(re.search(r"^id: (\S+)$", notes.text(path), re.MULTILINE).group(1))
    for output in seen:
        for secret in secrets:
            assert secret not in output, (secret, output)
        assert not re.search(r"\b[0-9a-f]{64}\b", output), output
