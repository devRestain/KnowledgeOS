"""C4 Enhance acceptance (docs/CAPABILITIES.md ENH-1..ENH-7) through the ExOps API and scripted roles."""

from __future__ import annotations

import re
from datetime import timedelta

from conftest import (
    conforming_note,
    connector_script,
    enhance_scripts,
    evaluator_script,
    focus_and_candidates,
    manager_script,
    synthesizer_script,
)

from knowledgeos.config import ROOT
from knowledgeos.notes import UUID4_RE, parse
from knowledgeos.util import iso, sha256

IDEA = "40_Knowledge/Ideas/Tools shape thinking.md"
IDEA_BODY = "Tools shape how we think.\n\nA notebook changes what I notice.\n"
REPORTS = "01_AI_Review/Reports/"
CITATION = re.compile(r"\[\[([^|\]]+)\|[^\]]*\]\] \(L(\d+)(?:-(\d+))?, ([0-9a-f]{8})\)")


def write_related(vault, count=3, policy="ask"):
    paths = []
    bodies = [
        "Writing by hand slows me down, which helps. See [[Tools shape thinking]].\n",
        "A whiteboard invites big diagrams. Related: [[Tools shape thinking]].\n",
        "Search tools make me lazy about remembering. [[Tools shape thinking]]\n",
    ]
    for number in range(count):
        path = f"40_Knowledge/Notes/Related {number + 1}.md"
        vault.write(path, conforming_note("knowledge", "seed", body=bodies[number], policy=policy))
        paths.append(path)
    return paths


def work_of(kos, receipt):
    status, work = kos.get(f"/api/works/{receipt['work_id']}")
    assert status == 200, work
    return work


def report_files(vault):
    folder = vault.path(REPORTS)
    return sorted(path.name for path in folder.glob("*.md")) if folder.is_dir() else []


def frontmatter(vault, path):
    return parse(path, vault.read(path)).data


def chunk_hash(text, start, end):
    return sha256("".join(text.splitlines(keepends=True)[start - 1 : end]).encode("utf-8"))


def citations(text):
    return [(target + ".md", int(start), int(end or start), short) for target, start, end, short in CITATION.findall(text)]


def test_ENH_1_develop_report_cites_bytes_read_and_leaves_the_idea_unchanged(kos, vault):
    original = conforming_note("idea", "seed", body=IDEA_BODY)
    vault.write(IDEA, original)
    related = write_related(vault)
    enhance_scripts(kos.runner)

    receipt = kos.run("enhance", {"mode": "develop", "path": IDEA, "digest": vault.sha(IDEA)})
    assert receipt["status"] == "completed", receipt
    report = receipt["outcome"]["report"]
    assert report.startswith(REPORTS) and vault.path(report).is_file()
    assert vault.text(IDEA) == original

    text = vault.text(report)
    found = citations(text)
    assert found, text
    work = work_of(kos, receipt)
    read = [entry for entry in work["evidence"].values() if entry["read"]]
    for path, start, end, short in found:
        matches = [e for e in read if e["path"] == path and e["lines"] == [start, end] and e["chunk_hash"].startswith(short)]
        assert len(matches) == 1, (path, start, end, short)
        assert matches[0]["chunk_hash"] == chunk_hash(vault.text(path), start, end)  # files are unchanged
    by_digest = {f"sha256:{vault.sha(path)}": path for path in [IDEA, *related]}
    for citation in receipt["outcome"]["citations"]:
        cited = by_digest[citation["resource_reference"]["content_digest"]]
        lines = citation["chunk_locator"]
        assert citation["chunk_hash"] == "sha256:" + chunk_hash(vault.text(cited), lines["start_line"], lines["end_line"])

    data = frontmatter(vault, report)
    assert data["type"] == "report" and UUID4_RE.match(data["id"])
    assert data["kos_work"] == receipt["work_id"] and data["kos_mode"] == "develop"
    assert data["created"] == iso(kos.clock.now())
    assert set(data["inputs"]) == {f"[[{path.removesuffix('.md')}]]" for path in [IDEA, *related]}
    for label in re.findall(r"^- \[([^\]]+)\]", text, re.MULTILINE):
        assert label in ("cited", "inference", "not found")
    for heading in ("Core in your own words", "Related things you already wrote", "Connections and tensions",
                    "Gaps and open questions", "Possible next steps"):
        assert f"## {heading}" in text


def test_ENH_2_unlabeled_uncited_claim_fails_twice_and_nothing_is_written(kos, vault):
    vault.write(IDEA, conforming_note("idea", "seed", body=IDEA_BODY))
    enhance_scripts(kos.runner, graph=("retriever", "synthesizer"), successor=("synthesizer",))

    def careless(session):
        context = session.call("work_context")
        sections = {sid: [{"text": "Nothing here.", "label": "not_found", "citations": []}] for sid in context["sections"]}
        sections["core"] = [{"text": "Tools make everyone smarter."}]
        result = session.call("submit_report", payload={"sections": sections})
        assert result["ok"] is False and any("label" in problem for problem in result["diagnostics"])

    kos.runner.script("synthesizer", careless)
    before = vault.snapshot()

    receipt = kos.run("enhance", {"mode": "develop", "path": IDEA})
    assert receipt["status"] == "unmet", receipt
    assert vault.snapshot() == before and report_files(vault) == []
    first = [entry["corrective"] for entry in kos.runner.invoked("synthesizer")[:2]]
    assert first == [False, True]
    work = work_of(kos, receipt)
    step = next(step for step in work["segments"][0]["steps"] if step["role"] == "synthesizer")
    assert step["status"] == "failed" and step["attempts"] == 2 and "label" in step["error"]
    assert "enh.claims_labeled" in work["segments"][0]["assessment"]["unmet"]
    assert work["status"] == "unmet" and work["team_review"] is None
    assert kos.runner.invoked("eval_officer") == []


def test_ENH_3_edit_during_work_report_cites_snapshot_and_states_change(kos, vault):
    original = conforming_note("idea", "seed", body=IDEA_BODY)
    vault.write(IDEA, original)
    write_related(vault, count=1)
    edited = original.replace("Tools shape how we think.", "Tools shape how we think, and we shape them back.")
    enhance_scripts(kos.runner)

    def editing_connector(session):
        vault.write(IDEA, edited)  # the user saves the note while the Work runs
        connector_script()(session)

    kos.runner.script("connector", editing_connector)
    receipt = kos.run("enhance", {"mode": "develop", "path": IDEA})
    assert receipt["status"] == "completed", receipt
    assert vault.text(IDEA) == edited
    assert receipt["outcome"]["inputs_changed"] == [IDEA]

    text = vault.text(receipt["outcome"]["report"])
    line = original.splitlines().index("Tools shape how we think.") + 1
    snapshot_hash = chunk_hash(original, line, line)
    assert (IDEA, line, line, snapshot_hash[:8]) in citations(text)
    assert chunk_hash(edited, line, line) != snapshot_hash
    statement = next(row for row in text.splitlines() if "Input changed after the snapshot" in row)
    assert f"[[{IDEA.removesuffix('.md')}]]" in statement


def test_ENH_4_report_policy_is_the_strictest_input(kos, vault):
    vault.write(IDEA, conforming_note("idea", "seed", body=IDEA_BODY, policy="remote_ok"))
    [asked] = write_related(vault, count=1, policy="ask")
    enhance_scripts(kos.runner)

    receipt = kos.run("enhance", {"mode": "develop", "path": IDEA})
    assert receipt["status"] == "completed", receipt
    data = frontmatter(vault, receipt["outcome"]["report"])
    assert data["ai_policy"] == "ask"
    assert f"[[{asked.removesuffix('.md')}]]" in data["inputs"]


def test_ENH_5_earlier_report_is_never_returned_to_roles(kos, vault):
    vault.write(IDEA, conforming_note("idea", "seed", body=IDEA_BODY, extra="tags: [tools]\n"))
    write_related(vault, count=1)
    earlier = REPORTS + "2026-11-01 Develop — Tools shape thinking.md"
    vault.write(earlier, conforming_note("report", "seed", extra="title: Earlier report\ntags: [tools]\n",
                                         body="Tools shape thinking, an earlier report said. [[Tools shape thinking]]\n"))
    assert any(r["title"] == "Earlier report" for r in kos.search(mode="keyword", query="earlier report said")["results"])
    enhance_scripts(kos.runner)
    titles: list[str] = []

    def searching_connector(session):
        context, handles = focus_and_candidates(session)
        for query in ("tools", "earlier report said", "shape thinking"):
            titles.extend(r["title"] for r in session.call("search", query=query)["results"])
        titles.extend(r["title"] for r in session.call("related", handle=handles[0])["results"])
        for handoff in context["handoffs"]:
            titles.extend(c["title"] for c in handoff["handoff"].get("candidates", []))
        connector_script()(session)

    kos.runner.script("connector", searching_connector)
    receipt = kos.run("enhance", {"mode": "develop", "path": IDEA})
    assert receipt["status"] == "completed", receipt
    assert titles and "Earlier report" not in titles
    work = work_of(kos, receipt)
    assert not any(entry["path"].startswith("01_AI_Review/") for entry in work["evidence"].values())


def test_ENH_6_unsupported_tensions_get_one_connector_synthesizer_successor(kos, vault):
    vault.write(IDEA, conforming_note("idea", "seed", body=IDEA_BODY))
    write_related(vault, count=2)
    enhance_scripts(kos.runner)
    views = []

    def recording_manager(session):
        views.append(session.call("team_view"))
        manager_script()(session)

    kos.runner.script("thinking_manager", recording_manager)
    kos.runner.script("connector", [connector_script(tension="Hand writing and search pull apart."),
                                    connector_script(tension="Hand writing slows; search speeds up recall.")])
    kos.runner.script("synthesizer", [synthesizer_script(tension="FIRST: the notes disagree about speed."),
                                      synthesizer_script(tension="REVISED: one note values slowness, one speed.")])
    kos.runner.script("eval_officer", [
        evaluator_script(unmet={"enh.tensions_supported": "the cited notes do not disagree about speed"}),
        evaluator_script(),
    ])

    receipt = kos.run("enhance", {"mode": "develop", "path": IDEA})
    assert receipt["status"] == "completed", receipt
    work = work_of(kos, receipt)
    first, second = work["segments"]
    assert first["assessment"] == {"status": "unmet", "unmet": ["enh.tensions_supported"]}
    assert second["graph"]["steps"] == ["connector", "synthesizer"]
    assert second["segment"]["trigger_kind"] == "remaining_work"
    assert second["segment"]["target_criterion_ids"] == ["enh.tensions_supported"]
    assert second["assessment"]["status"] == "satisfied"
    successor_view = views[1]
    assert [c["id"] for c in successor_view["remaining_work"]["criteria"]] == ["enh.tensions_supported"]
    text = vault.text(receipt["outcome"]["report"])
    assert "REVISED" in text and "FIRST" not in text
    assert len(report_files(vault)) == 1


def test_ENH_7_teamreview_rates_each_producer_and_changes_nothing_else(kos, vault):
    vault.write(IDEA, conforming_note("idea", "seed", body=IDEA_BODY))
    write_related(vault, count=1)
    enhance_scripts(kos.runner, proposals=("Remove challenger from the pool", "Add a skeptic teammate"))
    config = {path: path.read_bytes() for path in [ROOT / "config/roles.json", *(ROOT / "config/capabilities").glob("*.json")]}
    roles_before = kos.get("/api/roles")[1]
    before_review = {}

    def manager(session):
        if session.binding.submission_tool != "submit_team_review":
            manager_script()(session)
            return
        view = session.call("team_view")
        segments = kos.get(f"/api/works/{session.binding.work_id}")[1]["segments"]
        before_review["segments"] = segments
        producing = {
            step["role"] for segment in segments for step in segment["steps"]
            if step["status"] == "completed" and step.get("artifact")
        }
        assert set(view["producing"]) == producing
        ratings = {
            teammate: {"rating": 4, "note": f"{teammate}: useful handoff; make the next handoff more concise."}
            for teammate in view["producing"]
        }
        session.call("submit_team_review", payload={
            "ratings": ratings,
            "proposals": [{"text": "Remove challenger from the pool"}, {"text": "Add a skeptic teammate"}],
        })

    kos.runner.script("thinking_manager", manager)
    receipt = kos.run("enhance", {"mode": "develop", "path": IDEA})
    assert receipt["status"] == "completed", receipt
    work = work_of(kos, receipt)
    review = work["team_review"]
    assert review["status"] == "recorded"
    producing = {
        step["role"] for segment in work["segments"] for step in segment["steps"]
        if step["status"] == "completed" and step.get("artifact")
    }
    assert "retriever" in producing  # Its evidence handoff is production, despite the static runtime.
    assert set(review["ratings"]) == producing
    for teammate, rating in review["ratings"].items():
        assert 1 <= rating["rating"] <= 5
        assert rating["note"] == f"{teammate}: useful handoff; make the next handoff more concise."
    assert [p["text"] for p in review["proposals"]] == ["Remove challenger from the pool", "Add a skeptic teammate"]
    assert all(p["considered"] is False for p in review["proposals"])
    assert review["invocation"]["profile"] == "kos-thinking-manager"
    assert work["segments"] == before_review["segments"]
    assert work["segments"][-1]["assessment"] == {"status": "satisfied", "unmet": []}
    assert kos.get("/api/roles")[1] == roles_before
    assert {path: path.read_bytes() for path in config} == config
    assert "challenger" in work["team"]["pool"]


def test_C4_report_language_follows_the_focal_note(kos, vault):
    path = "40_Knowledge/Ideas/도구와 생각.md"
    vault.write(path, conforming_note("idea", "seed", body="도구가 생각의 모양을 바꾼다.\n"))
    enhance_scripts(kos.runner)
    receipt = kos.run("enhance", {"mode": "develop", "path": path})
    assert receipt["status"] == "completed", receipt
    text = vault.text(receipt["outcome"]["report"])
    assert "## 내가 쓴 말로 본 핵심" in text and "- [인용] " in text
    assert "Core in your own words" not in text


def test_C4_ask_without_readable_notes_is_not_covered(kos, vault):
    vault.write("40_Knowledge/Notes/Private.md", conforming_note("knowledge", "seed", body="Gardening notes.\n",
                                                                 policy="deny"))
    before = vault.snapshot()
    receipt = kos.run("enhance", {"mode": "ask", "question": "What do I know about gardening?"})
    assert receipt["status"] == "completed", receipt
    assert receipt["outcome"] == {"mode": "ask", "covered": False, "report": None}
    assert "not covered" in receipt["notice"].lower()
    assert vault.snapshot() == before and kos.runner.invoked() == []


def test_C4_ask_answers_from_readable_notes(kos, vault):
    write_related(vault, count=2)
    enhance_scripts(kos.runner)
    receipt = kos.run("enhance", {"mode": "ask", "question": "What does a whiteboard invite?"})
    assert receipt["status"] == "completed", receipt
    text = vault.text(receipt["outcome"]["report"])
    assert "## Answer" in text and "What the Vault does not cover" in text
    assert citations(text)


def test_C4_refuses_deny_missing_and_unreadable_inputs(kos, vault):
    vault.write("40_Knowledge/Ideas/Denied.md", conforming_note("idea", "seed", policy="deny"))
    vault.write("40_Knowledge/Ideas/Local.md", conforming_note("idea", "seed", policy="local_only"))
    vault.write(IDEA, conforming_note("idea", "seed", body=IDEA_BODY))
    cases = [
        ({"mode": "develop", "path": "40_Knowledge/Ideas/Denied.md"}, "deny"),
        ({"mode": "develop", "path": "40_Knowledge/Ideas/Missing.md"}, "not found"),
        ({"mode": "develop", "path": "40_Knowledge/Ideas/Local.md"}, "local_only"),
        ({"mode": "develop", "path": IDEA, "digest": "0" * 64}, "changed"),
        ({"mode": "review", "path": IDEA, "window": {"from": "2026-11-01", "to": "2026-11-07"}}, "project"),
        ({"mode": "develop"}, "path"),
    ]
    for params, reason in cases:
        receipt = kos.run("enhance", params)
        assert receipt["status"] == "refused", (params, receipt)
        assert reason in receipt["notice"], (params, receipt)
        assert receipt["work_id"] is None
    assert report_files(vault) == [] and kos.runner.invoked() == []


def test_C4_review_window_selects_notes_to_interpret(kos, vault, clock):
    project = "20_Projects/Alpha/Alpha.md"
    vault.write(project, conforming_note("project", "active", body="Alpha builds the garden shed.\n"),
                age=timedelta(days=60))
    recent, old = "20_Projects/Alpha/Recent.md", "20_Projects/Alpha/Old.md"
    vault.write(recent, conforming_note("knowledge", "seed", body="Poured the floor for [[Alpha]].\n").replace(
        "2026-11-01T09:00:00+09:00", iso(clock.now() - timedelta(days=2))))
    vault.write(old, conforming_note("knowledge", "seed", body="Picked a site for [[Alpha]].\n").replace(
        "2026-11-01T09:00:00+09:00", iso(clock.now() - timedelta(days=200))), age=timedelta(days=200))
    enhance_scripts(kos.runner)
    window = {"from": (clock.now() - timedelta(days=7)).date().isoformat(), "to": clock.now().date().isoformat()}

    receipt = kos.run("enhance", {"mode": "review", "path": project, "window": window})
    assert receipt["status"] == "completed", receipt
    read = {entry["path"] for entry in work_of(kos, receipt)["evidence"].values() if entry["read"]}
    assert project in read and recent in read and old not in read
    assert "## What moved" in vault.text(receipt["outcome"]["report"])
