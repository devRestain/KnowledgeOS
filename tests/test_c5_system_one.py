"""C5 System One lifecycle proposals: status transitions that need reading the note."""

from __future__ import annotations

import json

from conftest import conforming_note, system_one_settings

from knowledgeos.notes import parse
from knowledgeos.proposals import parse as parse_proposal

ANSWERED = "40_Knowledge/Questions/Sky.md"
PRIVATE = "40_Knowledge/Questions/Private.md"
ALIVE = "40_Knowledge/Ideas/Alive.md"
DONE = "20_Projects/Done/Done.md"
SECRET = "ASK-ONLY-SECRET-TEXT"


def digest(kos):
    receipt = kos.run("digest.run", {}, actor="schedule/digest")
    assert receipt["status"] == "completed", receipt
    return receipt


def proposal_paths(vault):
    return sorted(p.relative_to(vault.root).as_posix() for p in vault.root.glob("01_AI_Review/Proposals/*.md"))


def kos_with_system_one(make_kos, decisions, tmp_path):
    return make_kos(settings={"system_one": system_one_settings(decisions, tmp_path)})


def judge(decisions):
    """The question is answered by a note carrying the marker; no idea is abandoned."""
    decisions.predict("answered", lambda question, body: 0.95 if "RAYLEIGH-ANSWER" in json.dumps(body) else 0.05)
    decisions.predict("abandoned", 0.05)
    decisions.predict("finished", 0.05)


def test_DOC_5_system_one_proposes_status_changes_for_sendable_notes_only(make_kos, vault, decisions, tmp_path):
    kos = kos_with_system_one(make_kos, decisions, tmp_path)
    judge(decisions)
    question = conforming_note("question", "open", body="Why is the sky blue?\n\nRAYLEIGH-ANSWER: scattering.\n",
                               policy="approve")
    vault.write(ANSWERED, question)
    vault.write(PRIVATE, conforming_note("question", "open", body=f"Why? {SECRET}. RAYLEIGH-ANSWER\n", policy="ask"))
    vault.write(ALIVE, conforming_note("idea", "seed", body="Still exploring this.\n", policy="approve"))
    before = {path: vault.read(path) for path in (ANSWERED, PRIVATE, ALIVE)}

    first = digest(kos)
    [path] = proposal_paths(vault)
    note = parse(path, vault.read(path))
    proposal = parse_proposal(note)
    assert proposal is not None and note.data["kos_capability"] == "C5"
    assert proposal.target_path == ANSWERED and proposal.target_sha == vault.sha(ANSWERED)
    assert [(c["op"], c.get("key"), c.get("value")) for c in proposal.changes] == [("set", "status", "answered")]
    assert "System One" in note.body and "0.95" in note.body
    assert first["outcome"]["proposals"] == [{"path": path, "status": "created"}]
    assert SECRET not in decisions.sent_text() and PRIVATE not in decisions.sent_text()
    assert {call["input"][0]["content"][1]["data"]["path"] for call in decisions.calls} == {ANSWERED, ALIVE}
    assert all(vault.read(p) == raw for p, raw in before.items())
    streamed = kos.owner.store.read_stream("system_one")
    assert len(streamed) == 2 and {entry["purpose"] for entry in streamed} == {"stewardship lifecycle"}
    [ref] = {entry["ref"] for entry in streamed}
    assert kos.owner.get_work(ref) is not None

    asked = len(decisions.calls)
    snapshot = vault.snapshot()
    second = digest(kos)
    assert len(decisions.calls) == asked
    assert second["outcome"]["proposals"] == []
    assert vault.snapshot() == snapshot

    vault.write(ALIVE, conforming_note("idea", "seed", body="Edited since.\n", policy="approve"))
    digest(kos)
    assert len(decisions.calls) == asked + 1
    assert proposal_paths(vault) == [path]


def test_DOC_5_project_and_idea_judgments_become_proposals_and_open_proposals_reserve_targets(
        make_kos, vault, decisions, tmp_path):
    kos = kos_with_system_one(make_kos, decisions, tmp_path)
    decisions.predict("finished", 0.9)
    decisions.predict("abandoned", 0.4)
    decisions.predict("answered", 0.05)
    vault.write("20_Projects/Shipped/Shipped.md", conforming_note("project", "active", policy="approve"))
    vault.write(ALIVE, conforming_note("idea", "mature", policy="approve"))
    digest(kos)
    [path] = proposal_paths(vault)
    proposal = parse_proposal(parse(path, vault.read(path)))
    assert proposal.target_path == "20_Projects/Shipped/Shipped.md"
    assert [(c.get("key"), c.get("value")) for c in proposal.changes] == [("status", "done")]

    # The open proposal reserves its target: a changed note is not asked about or proposed again.
    edited = conforming_note("project", "active", body="Changed.\n", policy="approve")
    vault.write("20_Projects/Shipped/Shipped.md", edited)
    asked = len(decisions.calls)
    digest(kos)
    assert len(decisions.calls) == asked


def test_DOC_5_unavailable_system_one_leaves_static_proposals_and_asks_again_later(
        make_kos, vault, decisions, tmp_path):
    kos = kos_with_system_one(make_kos, decisions, tmp_path)
    judge(decisions)
    vault.write(DONE, conforming_note("project", "done", policy="approve"))
    vault.write(ANSWERED, conforming_note("question", "open", body="RAYLEIGH-ANSWER\n", policy="approve"))
    decisions.down = True
    receipt = digest(kos)
    [path] = proposal_paths(vault)
    proposal = parse_proposal(parse(path, vault.read(path)))
    assert proposal.target_path == DONE and "archive-move" in proposal.checked
    assert [entry["status"] for entry in receipt["outcome"]["proposals"]] == ["created"]
    assert decisions.calls == []
    failed = [e for e in kos.owner.store.read_stream("system_one") if e["outcome"] == "failed"]
    assert len(failed) == 1

    decisions.down = False
    digest(kos)
    assert len(decisions.calls) == 1
    assert {parse_proposal(parse(p, vault.read(p))).target_path for p in proposal_paths(vault)} == {DONE, ANSWERED}


def test_DOC_5_unconfigured_system_one_changes_nothing(make_kos, vault):
    kos = make_kos()
    vault.write(ANSWERED, conforming_note("question", "open", body="RAYLEIGH-ANSWER\n", policy="approve"))
    digest(kos)
    assert proposal_paths(vault) == []
