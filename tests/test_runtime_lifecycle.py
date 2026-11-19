"""Owner lifecycle against the real worker-local Temporal server and LangGraph runtime."""

from __future__ import annotations

import asyncio
import threading
import time

from conftest import conforming_note, enhance_scripts, evaluator_script, manager_script
from temporalio.client import Client

from knowledgeos.roles import RoleOutcomeUnknown

IDEA = "40_Knowledge/Ideas/Restartable idea.md"
REPORTS = "01_AI_Review/Reports"


def temporal_description(kos, work_id, settings):
    status, work = kos.get(f"/api/works/{work_id}")
    assert status == 200

    async def describe():
        client = await Client.connect(settings["address"], namespace=settings["namespace"])
        return await client.get_workflow_handle(work["runtime"]["workflow_id"]).describe()

    return asyncio.run(describe())


def test_runtime_receipt_precedes_evaluation_and_report_application(make_kos, vault, runner, temporal_settings):
    vault.write(IDEA, conforming_note("idea", "seed", body="Tools shape how we think.\n"))
    enhance_scripts(runner, graph=("retriever", "synthesizer"))
    evaluating, release = threading.Event(), threading.Event()

    def evaluate(session):
        evaluating.set()
        assert release.wait(10), "test did not release independent evaluation"
        evaluator_script()(session)

    runner.script("eval_officer", evaluate)
    kos = make_kos()
    try:
        status, receipt = kos.submit("enhance", {"mode": "develop", "path": IDEA})
        assert status == 202 and receipt["status"] == "received"
        assert evaluating.wait(10), "Work never reached independent evaluation"
        status, admitted = kos.get(f"/api/requests/{receipt['request_id']}")
        assert status == 200 and admitted["status"] == "running"
        assert admitted["work_id"] and admitted["outcome"] is None
        status, work = kos.get(f"/api/works/{admitted['work_id']}")
        assert status == 200 and work["status"] == "running"
        observation = temporal_description(kos, work["work_id"], temporal_settings)
        assert observation.status.name == "RUNNING" and observation.run_id
        assert list(vault.path(REPORTS).glob("*.md")) == []
    finally:
        release.set()
    completed = kos.wait(receipt["request_id"])
    assert completed["status"] == "completed", completed
    assert vault.path(completed["outcome"]["report"]).is_file()
    assert len(list(vault.path(REPORTS).glob("*.md"))) == 1


def test_runtime_different_state_roots_use_isolated_stable_queues(make_kos, vault, tmp_path, temporal_settings):
    vault.write("00_Inbox/Queue isolation.md", "A capture without frontmatter.\n")
    owners = [
        make_kos(),
        make_kos(state=tmp_path / "other-state", runtime=tmp_path / "other-runtime"),
    ]
    queues = []
    for kos in owners:
        receipt = kos.run("standardize.preview", {"paths": ["00_Inbox/Queue isolation.md"]})
        assert receipt["status"] == "completed", receipt
        observation = temporal_description(kos, receipt["work_id"], temporal_settings)
        queues.append(observation.task_queue)
    assert queues[0] != queues[1]


def test_runtime_running_work_reattaches_without_duplicate_report_or_review(make_kos, vault, runner, temporal_settings):
    vault.write(IDEA, conforming_note("idea", "seed", body="Tools shape how we think.\n"))
    graph = ("retriever", "synthesizer")
    enhance_scripts(runner, graph=graph)
    reviewing, release = threading.Event(), threading.Event()
    complete_manager = manager_script(graph=graph, proposals=("Consider another source",))

    def manager(session):
        if session.binding.submission_tool == "submit_team_review":
            reviewing.set()
            assert release.wait(10), "test did not release TeamReview after handoff"
        complete_manager(session)

    runner.script("thinking_manager", manager)
    kos = make_kos()
    shutdown_errors = []

    def shutdown():
        try:
            kos.owner.close()
        except Exception as error:  # noqa: BLE001 - re-raised to the test thread via the assertion below
            shutdown_errors.append(error)

    closing = threading.Thread(target=shutdown, name="test-owner-shutdown")
    try:
        status, receipt = kos.submit("enhance", {"mode": "develop", "path": IDEA},
                                     request_id="reattach-running-work")
        assert status == 202 and reviewing.wait(10), receipt
        status, running = kos.get(f"/api/requests/{receipt['request_id']}")
        assert status == 200 and running["status"] == "running"
        work_id = running["work_id"]
        before = temporal_description(kos, work_id, temporal_settings)
        assert before.status.name == "RUNNING"
        reports = {path.name: path.read_bytes() for path in vault.path(REPORTS).glob("*.md")}
        assert len(reports) == 1

        closing.start()
        deadline = time.monotonic() + 10
        while True:
            status, draining = kos.get(f"/api/works/{work_id}")
            assert status == 200
            if draining["runtime"].get("graceful_handoff"):
                break
            assert closing.is_alive(), shutdown_errors
            assert time.monotonic() < deadline, "Owner did not begin graceful shutdown"
            time.sleep(0.02)
        assert closing.is_alive(), "Owner closed before its in-flight Activity drained"
        status, _ = kos.submit("standardize.preview", {"paths": [IDEA]}, request_id="after-shutdown-started")
        assert status == 400
        assert kos.get("/api/requests/after-shutdown-started")[0] == 404
        status, duplicate = kos.submit("enhance", {"mode": "develop", "path": IDEA},
                                       request_id=receipt["request_id"])
        assert status == 200 and duplicate["work_id"] == work_id
        restarted = make_kos(instance_id="knowledgeos-restarted")
        status, attached = restarted.get(f"/api/requests/{receipt['request_id']}")
        assert status == 200 and attached["work_id"] == work_id and attached["status"] == "running"
        reattached = temporal_description(restarted, work_id, temporal_settings)
        assert reattached.id == before.id
        assert reattached.run_id == before.run_id
        assert reattached.task_queue == before.task_queue
    finally:
        release.set()
        if closing.ident is not None:
            closing.join(timeout=10)
            assert not closing.is_alive(), "Owner shutdown did not drain the released Activity"
            assert shutdown_errors == []
    kos.close()
    completed = restarted.wait(receipt["request_id"])
    assert completed["status"] == "completed" and completed["work_id"] == work_id, completed
    assert {path.name: path.read_bytes() for path in vault.path(REPORTS).glob("*.md")} == reports
    assert len(restarted.get("/api/works")[1]) == 1
    work = restarted.get(f"/api/works/{work_id}")[1]
    assert [proposal["text"] for proposal in work["team_review"]["proposals"]] == ["Consider another source"]
    assert len(runner.invoked("synthesizer")) == 1
    assert len(runner.invoked("thinking_manager")) == 2
    assert restarted.get("/api/unknowns")[1] == []


def test_runtime_unknown_model_result_is_retained_without_reinvocation(make_kos, vault, runner):
    vault.write(IDEA, conforming_note("idea", "seed", body="Tools shape how we think.\n"))
    enhance_scripts(runner, graph=("retriever", "synthesizer"))

    def lost_response(session):
        raise RoleOutcomeUnknown("provider response was lost after dispatch")

    runner.script("synthesizer", lost_response)
    kos = make_kos()
    request_id = "lost-model-response"
    params = {"mode": "develop", "path": IDEA}
    status, receipt = kos.submit("enhance", params, request_id=request_id)
    assert status == 202
    unknown = kos.wait(receipt["request_id"])
    assert unknown["status"] == "unknown", unknown
    assert len(runner.invoked("synthesizer")) == 1
    status, original_work = kos.get(f"/api/works/{unknown['work_id']}")
    assert status == 200
    kos.close()

    restarted = make_kos()
    status, duplicate = restarted.submit("enhance", params, request_id=request_id)
    assert status == 200 and duplicate == unknown
    status, retained_work = restarted.get(f"/api/works/{unknown['work_id']}")
    assert status == 200 and retained_work == original_work
    assert len(restarted.get("/api/works")[1]) == 1
    assert len(runner.invoked("synthesizer")) == 1
    assert list(vault.path(REPORTS).glob("*.md")) == []
    unknowns = restarted.get("/api/unknowns")[1]
    assert any(row["ref"].get("work_id") == unknown["work_id"] and row["resolution"] is None
               for row in unknowns)


def test_runtime_unknown_external_delivery_is_not_replayed_on_restart(make_kos):
    deliveries = []

    def lost_response(notice):
        deliveries.append(notice["notice_id"])
        raise OSError("delivery response lost after dispatch")

    kos = make_kos(notifier=lost_response)
    request_id = "lost-delivery-response"
    status, receipt = kos.submit("digest.run", {}, actor="schedule/digest", request_id=request_id)
    assert status == 202
    completed = kos.wait(receipt["request_id"])
    assert completed["status"] == "completed", completed
    notices = [notice for notice in kos.get("/api/notices")[1] if notice["kind"] == "digest"]
    assert len(notices) == 1 and notices[0]["delivery"] == "unknown"
    assert deliveries.count(notices[0]["notice_id"]) == 1
    kos.close()

    restarted = make_kos(notifier=lost_response)
    status, duplicate = restarted.submit("digest.run", {}, actor="schedule/digest", request_id=request_id)
    assert status == 200 and duplicate == completed
    retained = {notice["notice_id"]: notice for notice in restarted.get("/api/notices")[1]}
    assert retained[notices[0]["notice_id"]] == notices[0]
    assert deliveries.count(notices[0]["notice_id"]) == 1
    assert any(row["kind"] == "notice" and row["ref"]["notice_id"] == notices[0]["notice_id"]
               for row in restarted.get("/api/unknowns")[1])
