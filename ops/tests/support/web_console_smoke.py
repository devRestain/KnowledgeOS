"""Disposable real Owner console; no live roots, fabricated HTTP or implicit execution.

Run in the project image with read-only control/Core and a tmpfs /tmp. Without
--temporal-target, Work is admitted but remains undispatched; one real Pending
normalization is prepared.
An explicit disposable Temporal target enables one actual independently evaluated
native Officer seed. No model/provider, control binding or daily producer is faked.
The opt-in --inspection-delay-seconds bound holds the actual inspection handler
only in this disposable fixture for real-browser in-flight disconnect evidence.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import signal
import tempfile
import threading
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

SOURCE = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"
SEARCH_TITLE = "웹 작업실 검증 근거"
SEARCH_SOURCE = f"40_Knowledge/Notes/{SEARCH_TITLE}.md"
QUERY = "webconsolefixtureevidence"
ACTOR = "owner.web_fixture"


def compose(root: Path):
    from support.control_factory import make_separate_portable_fixture_roots
    from support.work_factory import WorkFixture
    from vaultops.adapters.core import canonical, digest
    from vaultops.application.exops import KnowledgeExOps, TeamBinding
    from vaultops.application.intake_templates import build_template_admissions
    from vaultops.application.knowledge import KnowledgeApplication
    from vaultops.application.normalization_work import normalization_native_digest
    from vaultops.application.team_catalog import OfficerBinding, SpecialistBinding, TeamCatalog
    from vaultops.application.work import work_spec_digest
    from vaultops.note_engine import NoteEngine
    from vaultops.projection import generate_projection
    from vaultops.template_engine import render_note_template

    roots = make_separate_portable_fixture_roots(root, extra_inputs=(
        "ops/config/team-catalog.json", "ops/config/work-methods.json"), review_queues=True)
    rendered = render_note_template("T40_Knowledge.md", {
        "title": SEARCH_TITLE, "id": "be4b0d59-0fe5-4b0e-a984-52cb824c49fa",
        "created": "2026-10-31T09:00:00+09:00", "modified": "2026-10-31T09:00:00+09:00",
        "status": "seed", "sensitivity": "personal", "ai_policy": "ask",
        "claim": "검색으로 선택한 정확한 원문 범위를 Owner 작업에 전달한다.",
        "confidence": "unknown", "tags": [],
        "body": f"# {SEARCH_TITLE}\n\n## 핵심 주장\n\n"
                f"{QUERY}는 폐기 가능한 합성 지식 노트의 검색 근거이다.\n\n"
                "## 근거\n\n정확한 경로와 SHA-256을 선택하고 접수와 실행 완료를 구분한다.\n",
    })
    markdown = NoteEngine.from_root(roots).write_text(
        SEARCH_SOURCE, rendered.properties, rendered.body)
    (roots.vault / SEARCH_SOURCE).write_text(markdown, encoding="utf-8")
    app = KnowledgeApplication.from_roots(roots)
    catalog = TeamCatalog.load(roots.control)
    teams = {}

    def reference(candidate, name):
        content = canonical(candidate.content())
        return {"owner_operation_id": "knowledgeos", "artifact_id": "artifact.web." + name,
                "source_revision": digest(content)[7:], "content_digest": digest(content),
                "media_type": "application/json", "size_bytes": len(content),
                "classification": "internal", "availability": "available_by_owner_export",
                "export_capability_id": "knowledgeos.binding_read"}

    for team, manager, roles in (
        ("knowledge-curation", "curation_manager", ("normalizer",)),
        ("corpus-stewardship", "stewardship_manager", ("coverage_reader", "enrichment_proposer")),
        ("relation-ontology", "ontology_manager", ("relation_analyst", "ontology_curator")),
        ("evidence-research", "research_manager", ("retriever", "synthesizer")),
    ):
        candidate = TeamBinding(team_id=team,
            manager_executor_id="knowledgeos." + manager + ".web_fixture",
            manager_profile=catalog.profile("knowledgeos." + manager),
            eval_officer_executor_id="knowledgeos.eval_officer.web_fixture",
            eval_officer_profile=catalog.profile("knowledgeos.eval_officer"),
            specialists=tuple(SpecialistBinding("knowledgeos." + role,
                "knowledgeos." + role + ".web_fixture", catalog.profile("knowledgeos." + role))
                for role in roles), catalog_digest=catalog.digest,
            native_binding_digest=digest(canonical({"fixture_registration": team,
                                                    "worker": "unavailable"})),
            binding_reference={})
        teams[team] = replace(candidate, binding_reference=reference(candidate, team))
    candidate = OfficerBinding(
        officer_executor_id="knowledgeos.normalization_officer.web_fixture",
        officer_profile=catalog.profile("knowledgeos.normalization_officer"),
        eval_officer_executor_id="knowledgeos.eval_officer.web_fixture",
        eval_officer_profile=catalog.profile("knowledgeos.eval_officer"),
        catalog_digest=catalog.digest, native_binding_digest=normalization_native_digest(),
        binding_reference={})
    officer = replace(candidate, binding_reference=reference(candidate, "officer"))
    admitted = {}
    allowed = {"submit_work", "read_work", "search_query", "proposal_list", "proposal_read",
               "proposal_decide", "proposal_apply", "daily_list", "daily_read", "daily_decide"}
    exops = KnowledgeExOps(app,
        authorize_actor=lambda actor, action: actor.actor_id == ACTOR and action in allowed,
        director_admitted=lambda spec: admitted.get(spec["admission_receipt_id"]) == dict(spec),
        director_method_source_admitted=lambda spec, source:
            admitted.get(spec["admission_receipt_id"]) == dict(spec)
            and app.reference(app.resolve_reference(dict(source))) == dict(source),
        director_officer_source_admitted=lambda spec, source:
            admitted.get(spec["admission_receipt_id"]) == dict(spec)
            and app.reference(SOURCE) == dict(source),
        teams=teams, current_team_binding=teams.get,
        worker_identity="knowledgeos.worker.web_fixture",
        officers={officer.officer_executor_id: officer},
        current_officer_binding=lambda executor: officer
            if executor == officer.officer_executor_id else None)

    def intake(_actor, payload):
        fixture = WorkFixture()
        team = teams["knowledge-curation"]
        suffix = uuid4().hex
        spec = copy.deepcopy(fixture.spec)
        spec.update(work_id="work.web." + suffix, objective=payload["question"],
                    constraints="Disposable scope only; no Team Worker or provider is bound",
                    team_id=team.team_id, manager_executor_id=team.manager_executor_id,
                    eval_officer_executor_id=team.eval_officer_executor_id,
                    allowed_executor_ids=[item.executor_id for item in team.specialists],
                    allowed_profiles=[dict(item.profile) for item in team.specialists],
                    admission_receipt_id="admission.web." + suffix)
        spec["spec_digest"] = work_spec_digest(spec)
        admitted[spec["admission_receipt_id"]] = copy.deepcopy(spec)
        run = {**copy.deepcopy(fixture.run), "work_id": spec["work_id"],
               "work_run_id": "workrun.web." + suffix, "work_spec_digest": spec["spec_digest"],
               "binding_reference": dict(team.binding_reference)}
        return spec, run, "action.web." + suffix, app.clock() + timedelta(hours=1)

    def templates(_actor, template, scope, request_id):
        admissions = build_template_admissions(exops, template, scope, request_id,
            issue_director_receipt=lambda _template, team, _scope, request:
                "admission.web." + request.replace("-", "") + "." + team)
        for spec, *_rest in admissions:
            admitted[spec["admission_receipt_id"]] = copy.deepcopy(spec)
        return admissions

    projection, code = generate_projection(roots)
    if code:
        raise RuntimeError(json.dumps(projection))
    return exops, officer, admitted, intake, templates


async def native_seed(exops, officer, admitted, target):
    from clients.temporal import TemporalBinding
    from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
    from temporalio.client import Client
    from temporalio.service import RPCError, RPCStatusCode
    from temporalio.worker import Worker

    from vaultops.application.exops import OwnerActor
    from vaultops.application.normalization_work import (
        NormalizationOfficerExecutor,
        normalization_admission,
    )
    from vaultops.application.temporal_work import (
        WORKFLOW_TYPE,
        connect_owner_temporal,
        dispatch_admitted_work,
        worker_components,
    )

    if target != "server:7233":
        raise ValueError("Native seed requires the exact disposable pinned server:7233 route")
    suffix = uuid4().hex
    spec, run = normalization_admission(exops.application, officer,
        exops.application.reference(SOURCE), work_id="work.web_native." + suffix,
        work_run_id="workrun.web_native." + suffix,
        admission_receipt_id="admission.web_native." + suffix)
    admitted[spec["admission_receipt_id"]] = copy.deepcopy(spec)
    exops.submit_work(spec, run, actor=OwnerActor(ACTOR, "web"),
        action_id="action.web_native." + suffix,
        expires_at=exops.application.clock() + timedelta(minutes=10),
        selected_scope={"path": SOURCE,
                        "content_sha256": exops.application.reference(SOURCE)["source_revision"]},
        officer_source_reference=exops.application.reference(SOURCE))
    namespace = "knowledgeos"
    transport = TemporalBinding(owner_operation_id="knowledgeos", target_host=target,
        namespace=namespace, task_queue="knowledgeos.work.v1",
        workflow_type=WORKFLOW_TYPE, workflow_id_prefix="knowledgeos/",
        update_name="web.update.unbound", signal_name="web.signal.unbound",
        query_name="web.query.unbound", tls=False)
    sdk = await Client.connect(target, namespace=namespace, tls=False)
    try:
        await sdk.workflow_service.register_namespace(RegisterNamespaceRequest(namespace=namespace,
            workflow_execution_retention_period={"seconds": 3600}))
    except RPCError as error:
        if error.status != RPCStatusCode.ALREADY_EXISTS:
            raise
    scoped = await connect_owner_temporal(exops, transport)
    workflow, activity = worker_components(exops, NormalizationOfficerExecutor(exops))
    async with Worker(sdk, task_queue=transport.task_queue, workflows=[workflow],
                      activities=[activity], identity=exops.worker_identity):
        await dispatch_admitted_work(exops, scoped, run["work_run_id"])
        await asyncio.wait_for(sdk.get_workflow_handle("knowledgeos/" + run["work_run_id"]).result(),
                               timeout=120)
    return {"work_run_id": run["work_run_id"], "namespace": namespace,
            "scope": "actual deterministic native Officer; no model or Team control"}


async def serve(arguments):
    from vaultops.interfaces.loopback_owner import create_loopback_owner

    with tempfile.TemporaryDirectory(prefix="knowledgeos-web-console-") as directory:
        exops, officer, admitted, intake, templates = compose(Path(directory))
        if arguments.temporal_target:
            seed = await native_seed(exops, officer, admitted, arguments.temporal_target)
        else:
            report, code = exops.application.create_normalize_proposal(
                source_path=SOURCE, expected_sha256=exops.application.reference(SOURCE)["source_revision"])
            if code:
                raise RuntimeError(json.dumps(report))
            seed = {"scope": "real Pending proposal; Work remains undispatched; no native result"}
        server = create_loopback_owner(exops, actor_id=ACTOR,
            port=arguments.port, bind_host=arguments.bind_host,
            admit_request=intake, admit_template=templates)
        if arguments.inspection_delay_seconds:
            actual_handler = server.RequestHandlerClass
            gate_lock = threading.Lock()
            gate_counts = {"inspection": 0, "result": 0}

            class DelayedInspection(actual_handler):
                def do_POST(self):
                    if self.path == "/owner/work/result":
                        with gate_lock:
                            gate_counts["result"] += 1
                            print("WEB_CONSOLE_RESULT_REQUEST " + json.dumps({
                                "result_requests": gate_counts["result"]}), flush=True)
                    if self.path == "/owner/work/inspection":
                        with gate_lock:
                            gate_counts["inspection"] += 1
                            self.inspection_id = gate_counts["inspection"]
                            print("WEB_CONSOLE_INSPECTION_HELD " + json.dumps({
                                "inspection_id": self.inspection_id,
                                "result_requests": gate_counts["result"]}), flush=True)
                        time.sleep(arguments.inspection_delay_seconds)
                        print("WEB_CONSOLE_INSPECTION_RELEASED " + json.dumps({
                            "inspection_id": self.inspection_id}), flush=True)
                    return super().do_POST()

                def _reply(self, status, value):
                    super()._reply(status, value)
                    if self.command == "POST" and self.path == "/owner/work/inspection":
                        with gate_lock:
                            print("WEB_CONSOLE_INSPECTION_COMPLETED " + json.dumps({
                                "inspection_id": self.inspection_id,
                                "http_status": status,
                                "result_requests": gate_counts["result"]}), flush=True)

            server.RequestHandlerClass = DelayedInspection
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for selected in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(selected, stop.set)
        print(json.dumps({"console_url": f"http://127.0.0.1:{server.server_port}/console/",
                          "source": SEARCH_SOURCE, "normalization_source": SOURCE,
                          "query": QUERY, "seed": seed,
                          "inspection_delay_seconds": arguments.inspection_delay_seconds,
                          "unavailable": ["native Team control", "daily producer", "model Runner"]},
                         ensure_ascii=False), flush=True)
        print("WEB_CONSOLE_READY", flush=True)
        try:
            await stop.wait()
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=17902)
    parser.add_argument("--bind-host", choices=("127.0.0.1", "owner"), default="127.0.0.1")
    parser.add_argument("--temporal-target", choices=("server:7233",),
                        help="Exact pinned route in an exclusive disposable Temporal service only")
    parser.add_argument("--inspection-delay-seconds", type=int, choices=range(21), default=0,
                        help="Fixture-only bounded delay before the actual inspection handler")
    arguments = parser.parse_args()
    os.umask(0o077)
    asyncio.run(serve(arguments))


if __name__ == "__main__":
    main()
