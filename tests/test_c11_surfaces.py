"""C11 surfaces: free text through the model Director (SUR-3) and the console's cancellation
of a running Team Work through ExOps admission (SUR-4)."""

from __future__ import annotations

import json
import threading
import time

from conftest import conforming_note, connector_script, enhance_scripts

from knowledgeos.config import ROOT
from knowledgeos.owner import TERMINAL

IDEA = "40_Knowledge/Ideas/Tools shape thinking.md"
IDEA_BODY = "Tools shape how we think.\n\nA notebook changes what I notice.\n"
REPORTS = "01_AI_Review/Reports/"
GRAPH = ("retriever", "connector", "synthesizer")


def report_files(vault) -> list[str]:
    folder = vault.path(REPORTS)
    return sorted(path.name for path in folder.glob("*.md")) if folder.is_dir() else []


def wait_until(predicate, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while not (result := predicate()):
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.02)
    return result


def director(*payloads, seen: list | None = None):
    """Director turns, one per invocation (the second is the corrective turn). A payload may be
    a function of ``catalog_view``. ``seen`` collects each turn's view, feedback and result."""

    def turn_for(payload):
        def turn(session) -> None:
            view = session.call("catalog_view")
            entry = {"view": view, "feedback": session.call("feedback"), "corrective": list(session.corrective)}
            entry["result"] = session.call("submit_workspec", payload=payload(view) if callable(payload) else payload)
            if seen is not None:
                seen.append(entry)

        return turn

    return [turn_for(payload) for payload in payloads]


def develop_named_target(view):
    [target] = view["targets"]
    return {"command": "enhance", "params": {"mode": "develop", "path": target["ref"]}}


# ---------------------------------------------------------------- SUR-3 free text


def test_SUR_3_unmappable_free_text_admits_no_work_and_notice_says_why(kos, vault):
    vault.write(IDEA, conforming_note("idea", "seed", body=IDEA_BODY))
    before = vault.snapshot()
    seen: list = []
    reason = "Sending email is outside KnowledgeOS; ask to develop or organize a note instead"
    kos.runner.script("director", director({"refuse": reason}, seen=seen))

    status, received = kos.submit("free_text", {"text": "Email my notes to Alice"})
    assert status == 202 and received["status"] == "received"
    receipt = kos.wait(received["request_id"])
    assert receipt["status"] == "refused" and receipt["work_id"] is None
    assert reason in receipt["notice"]
    assert receipt["outcome"]["derived_request_id"] is None
    assert kos.get("/api/works")[1] == []
    assert vault.snapshot() == before
    assert [entry["role"] for entry in kos.runner.invoked()] == ["director"]

    view = seen[0]["view"]
    offered = {item["command"]: item for item in view["commands"]}
    assert {"enhance", "standardize.preview"} <= set(offered)
    assert not {"free_text", "work.cancel", "review.apply", "job.run"} & set(offered)
    assert "digest" not in offered["enhance"]["params"] and "digests" not in offered["standardize.preview"]["params"]
    assert view["targets"] == []
    assert "Tools shape how we think" not in json.dumps(view)

    # The Director's own feedback reports the clarification failure, without the request text.
    later: list = []
    kos.runner.script("director", director({"refuse": "Still not a KnowledgeOS command"}, seen=later))
    assert kos.run("free_text", {"text": "Email my notes to Bob"})["status"] == "refused"
    items = later[0]["feedback"]["items"]
    assert any(item["kind"] == "admission" and item["outcome"] == "refused" for item in items)
    assert "Alice" not in json.dumps(items)


def test_SUR_3_draft_still_invalid_after_corrective_turn_admits_no_work(kos, vault):
    vault.write(IDEA, conforming_note("idea", "seed", body=IDEA_BODY))
    before = vault.snapshot()
    seen: list = []
    kos.runner.script(
        "director",
        director({"command": "notes.delete", "params": {}}, {"command": "enhance", "params": {"mode": "develop"}}, seen=seen),
    )

    receipt = kos.run("free_text", {"text": "Make my thinking sharper"})
    assert receipt["status"] == "refused" and receipt["work_id"] is None
    assert "enhance develop needs path" in receipt["notice"]
    assert [entry["corrective"] for entry in kos.runner.invoked("director")] == [False, True]
    first, second = seen
    assert first["result"]["ok"] is False and "not offered" in first["result"]["diagnostics"][0]
    assert second["corrective"] == first["result"]["diagnostics"]
    assert second["result"] == {"ok": False, "diagnostics": second["result"]["diagnostics"], "final": True}
    assert kos.get("/api/works")[1] == [] and vault.snapshot() == before


def test_SUR_3_director_authoring_criteria_is_rejected_without_corrective_turn(kos, vault):
    vault.write(IDEA, conforming_note("idea", "seed", body=IDEA_BODY))

    def with_criteria(view):
        return {**develop_named_target(view), "criteria": ["the report is good"]}

    kos.runner.script("director", director(with_criteria, with_criteria))
    receipt = kos.run("free_text", {"text": "Develop Tools shape thinking"})
    assert receipt["status"] == "refused" and receipt["work_id"] is None
    assert "authority violation" in receipt["notice"] and "criteria" in receipt["notice"]
    assert len(kos.runner.invoked("director")) == 1
    assert kos.get("/api/works")[1] == []


def test_SUR_3_unavailable_director_route_admits_no_work(kos, vault):
    vault.write(IDEA, conforming_note("idea", "seed", body=IDEA_BODY))
    before = vault.snapshot()
    receipt = kos.run("free_text", {"text": "Develop Tools shape thinking"})
    assert receipt["status"] == "failed" and receipt["work_id"] is None
    assert "unavailable" in receipt["notice"] and "not retried or rerouted" in receipt["notice"]
    assert kos.get("/api/works")[1] == [] and vault.snapshot() == before


def test_SUR_3_mapped_free_text_admits_the_derived_request_with_catalog_criteria(kos, vault):
    original = conforming_note("idea", "seed", body=IDEA_BODY)
    vault.write(IDEA, original)
    enhance_scripts(kos.runner, graph=GRAPH)
    seen: list = []
    literal_path = {"command": "enhance", "params": {"mode": "develop", "path": IDEA}}
    kos.runner.script("director", director(literal_path, develop_named_target, seen=seen))

    params = {"text": "Please develop my idea Tools shape thinking"}
    status, received = kos.submit("free_text", params, request_id="free-text-develop")
    assert status == 202 and received["status"] == "received"
    receipt = kos.wait("free-text-develop")
    assert receipt["status"] == "completed", receipt
    assert receipt["outcome"]["command"] == "enhance"

    first, second = seen
    assert "target refs" in first["result"]["diagnostics"][0] and second["result"] == {"ok": True}
    view = second["view"]
    assert view["targets"] == [{"ref": "t1", "title": "Tools shape thinking", "type": "idea", "status": "seed"}]
    dumped = json.dumps(view)
    assert IDEA not in dumped and vault.sha(IDEA) not in dumped and "notebook" not in dumped

    derived_id = receipt["outcome"]["derived_request_id"]
    status, derived = kos.get(f"/api/requests/{derived_id}")
    assert status == 200 and derived["command"] == "enhance" and derived["actor"] == "user/local"
    assert derived["status"] == "completed" and derived["work_id"] == receipt["work_id"]
    assert derived["outcome"] == receipt["outcome"]["outcome"]

    work = kos.get(f"/api/works/{receipt['work_id']}")[1]
    assert work["request_id"] == derived_id
    assert [item["path"] for item in work["workspec"]["inputs"]] == [IDEA]
    catalog = json.loads((ROOT / "config/capabilities/C4.json").read_text())
    assert [c["id"] for c in work["workspec"]["criteria"]] == catalog["modes"]["develop"]["criteria"]
    report = receipt["outcome"]["outcome"]["report"]
    assert report.startswith(REPORTS) and vault.path(report).is_file()
    assert vault.text(IDEA) == original

    replay = kos.post("/api/requests", {"request_id": "free-text-develop", "command": "free_text", "params": params})
    assert replay == (200, receipt)
    assert len(kos.runner.invoked("director")) == 2 and len(kos.get("/api/works")[1]) == 1


# ---------------------------------------------------------------- SUR-4 console cancellation


def start_blocked_enhance(kos, vault):
    """An enhance Work whose connector step blocks until ``release`` is set."""
    vault.write(IDEA, conforming_note("idea", "seed", body=IDEA_BODY))
    enhance_scripts(kos.runner, graph=GRAPH)
    started, release = threading.Event(), threading.Event()
    proceed = connector_script()

    def blocking_connector(session) -> None:
        started.set()
        assert release.wait(10)
        proceed(session)

    kos.runner.script("connector", blocking_connector)
    status, enhance = kos.submit("enhance", {"mode": "develop", "path": IDEA, "digest": vault.sha(IDEA)})
    assert status == 202
    assert started.wait(10)
    running = kos.get(f"/api/requests/{enhance['request_id']}")[1]
    assert running["status"] == "running" and running["work_id"]
    return enhance["request_id"], running["work_id"], release


def test_SUR_4_console_cancel_of_running_team_work_is_received_then_applied(kos, vault, tmp_path):
    enhance_id, work_id, release = start_blocked_enhance(kos, vault)
    try:
        before = vault.snapshot()
        status, received = kos.submit("work.cancel", {"work_id": work_id})
        assert status == 202 and received["status"] == "received" and received["command"] == "work.cancel"
        [action_id] = wait_until(lambda: kos.get(f"/api/works/{work_id}")[1]["actions"])
        action = json.loads((tmp_path / "state/actions" / f"{action_id}.json").read_text())
        assert action["action"] == "work.cancel" and action["state"] == "received"
        observed = kos.get(f"/api/requests/{received['request_id']}")[1]
        assert observed["status"] == "received" and observed["status"] not in TERMINAL
        time.sleep(0.05)
    finally:
        release.set()

    cancelled = kos.wait(received["request_id"])
    assert cancelled["status"] == "completed", cancelled
    assert cancelled["outcome"]["state"] == "applied" and cancelled["outcome"]["status"] == "cancelled"
    assert cancelled["outcome"]["action_id"] == action_id
    action = json.loads((tmp_path / "state/actions" / f"{action_id}.json").read_text())
    assert action["state"] == "applied" and action["request_id"] == received["request_id"]

    assert kos.wait(enhance_id)["status"] == "cancelled"
    assert kos.get(f"/api/works/{work_id}")[1]["status"] == "cancelled"
    assert kos.runner.invoked("synthesizer") == [] and kos.runner.invoked("eval_officer") == []
    assert report_files(vault) == [] and vault.snapshot() == before


def test_SUR_4_unobserved_cancellation_is_unknown_not_fabricated(make_kos, vault):
    kos = make_kos(settings={"C11": {"cancel_observation_seconds": 0.2}})
    enhance_id, work_id, release = start_blocked_enhance(kos, vault)
    try:
        before = vault.snapshot()
        status, received = kos.submit("work.cancel", {"work_id": work_id})
        assert status == 202 and received["status"] == "received"
        unknown = kos.wait(received["request_id"])
        assert unknown["status"] == "unknown", unknown
        assert unknown["outcome"]["state"] == "unknown" and unknown["outcome"]["status"] == "cancelling"
        assert kos.get(f"/api/works/{work_id}")[1]["status"] == "running"
        [record] = [u for u in kos.get("/api/unknowns")[1] if u["unknown_id"] == unknown["outcome"]["unknown_id"]]
        assert record["kind"] == "cancellation" and record["resolution"] is None
    finally:
        release.set()

    assert kos.wait(enhance_id)["status"] == "cancelled"
    assert kos.get(f"/api/requests/{received['request_id']}")[1] == unknown
    assert report_files(vault) == [] and vault.snapshot() == before
