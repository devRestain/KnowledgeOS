"""Disposable console support; no live roots or implicit execution.

--headless executes maintained HTML/JS with explicit synthetic reads in a bounded
Node DOM, never a browser or native client. Other modes run a real disposable
Owner in the project image with read-only control/Core and a tmpfs /tmp.
Without --temporal-target, Work is admitted but remains undispatched.
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
import subprocess
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
        "ops/config/team-catalog.json", "ops/config/work-methods.json",
        "ops/config/agent-profiles.json", "ops/clients/hermes/role_policy.py"), review_queues=True)
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


def administrative_seed(exops, intake):
    """Pre-admit one observable Work; the browser never performs document intake."""
    from vaultops.application.exops import OwnerActor

    actor = OwnerActor(ACTOR, "web")
    spec, run, action_id, expires_at = intake(actor, {
        "question": "Observe this disposable admitted Work without executing a producer."})
    exops.submit_work(spec, run, actor=actor, action_id=action_id, expires_at=expires_at,
        selected_scope={"path": SEARCH_SOURCE,
                        "content_sha256": exops.application.reference(SEARCH_SOURCE)["source_revision"]})
    return {"work_run_id": run["work_run_id"],
            "scope": "real admitted Work remains undispatched; no native result"}


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
            seed = administrative_seed(exops, intake)
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


def headless_console():
    """Execute the maintained HTML/JS with a bounded DOM, not a GUI/browser proof."""
    from html.parser import HTMLParser

    class Shell(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.nodes, self.stack = [], []

        def handle_starttag(self, tag, attrs):
            index = len(self.nodes)
            self.nodes.append({"tag": tag, "attrs": attrs, "parent": self.stack[-1] if self.stack else None})
            if tag not in {"meta", "link", "input", "br", "hr", "img", "source"}:
                self.stack.append(index)

        def handle_endtag(self, tag):
            for position in range(len(self.stack) - 1, -1, -1):
                if self.nodes[self.stack[position]]["tag"] == tag:
                    self.stack = self.stack[:position]
                    break

        def handle_data(self, data):
            if self.stack:
                self.nodes.append({"text": data, "parent": self.stack[-1]})

    control = Path(__file__).resolve().parents[3]
    shell = Shell()
    shell.feed((control / "frontend/index.html").read_text(encoding="utf-8"))
    script = r"""
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const {Element, deferred} = require(process.argv[1]);
const shell = JSON.parse(fs.readFileSync(0, "utf8")), elements = new Map(), all = [];
const root = new Element("document"), windowEvents = new Map(), calls = [];
function create(tag) { const el = new Element(tag); el.click = () => el.fire("click"); return el; }
for (const item of shell) {
  const parent = item.parent === null ? root : all[item.parent];
  if (item.text !== undefined) { parent.appendText(item.text); all.push(null); continue; }
  const el = create(item.tag); all.push(el); parent.append(el);
  for (const [name, value] of item.attrs) {
    el.setAttribute(name, value ?? "");
    if (name === "id") elements.set(value, el);
    if (name === "hidden") el.hidden = true;
    if (name.startsWith("data-")) el.dataset[name.slice(5)] = value ?? "";
  }
}
const get = id => { assert.ok(elements.has(id), `HTML has no element ${id}`); return elements.get(id); };
const workId = "workrun.headless.retained", graphId = "graphrun.headless.retained";
const assessment = {completion_state:"incomplete", criterion_outcomes:[{criterion_id:"meaning",outcome:"unmet"}]};
const remaining = {gaps:[{criterion_id:"meaning",summary:"retained independent semantic gap"}]};
const work = {spec:{spec_digest:"retained-spec-digest",objective:"Retained objective",acceptance_criteria:[{criterion_id:"meaning",description:"Evidence"}],
  manager_executor_id:"manager.retained",eval_officer_executor_id:"eval.retained"},
  run:{work_run_id:workId,state:"awaiting_human",pending_action_ids:[],graph_run_ids:[graphId],segments_started:1,revision:2},
  receipt:{receipt_state:"accepted"},dispatch_state:"accepted",application_state:"unknown"};
const inspection = {run_revision:2,work_spec_digest:"retained-spec-digest",segments:[{segment:{segment_index:1,graph_run_id:graphId},graph_availability:"retained",
  graph_spec:{graph_id:"graph.retained",graph_digest:"retained-graph-digest"},nodes:[{node_id:"node.retained"}],
  edges:[{source_node_id:"node.start",target_node_id:"node.retained"}],assignments:[{executor_id:"specialist.retained"}],
  plan:{rationale:"Retained Manager reasoning",steps:[{node_id:"node.retained",role_id:"role.retained",route:"historical",
    stage_id:"retained_stage",criterion_ids:["meaning"]}]}}],
  invocations:[{role_id:"role.retained",responsibility:"specialist",graph_run_id:graphId,stage_id:"retained_stage",
    observation_state:"observed",historical_prompt_text:"not_retained",integrity:"matches",instructions:"Original retained instruction",
    observed:{provider:"observed-provider",model:"observed-model",reasoning_effort:"observed-effort",
      prompt_digest:"historical-soul-digest",instructions_digest:"historical-composed-digest",request_digest:"historical-request-digest"},
    transport_requests:[{wire_instructions_digest:"historical-wire-digest"}]}],
  assessment,remaining_work:remaining,prior_assessments:[{source:"initial",assessment,remaining_work:remaining}]};
const policy = {availability:"available",kind:"current_configured_policy",policy_digest:"current-policy",
  routes:{current:{provider:"configured-provider",model:"configured-model",reasoning_effort:"configured-effort"}},
  roles:[{role_id:"role.retained",default_route:"current",allowed_routes:["current"],manual_homes:["manual-home"],
    purpose:"Current role purpose",prompt_digest:"current-soul-digest"}]};
function data(route, payload) {
  if (route === "owner/status") return {read_only:false,generation:1,revision:1,bindings:{},observed_at:"retained-time"};
  if (route === "work/history") return {works:[{work_run_id:workId,objective:"Discover without path or memorized ID",
    run_state:"unknown",assessment_state:"incomplete",segments_started:1,application_state:"unknown"}],next_before:null};
  if (route === "agents/policy") return policy;
  if (route === "agents/prompt") return {availability:"available",role_id:"role.retained",policy_digest:"current-policy",
    prompt_digest:"current-soul-digest",composition:"COMMON + LF + SOUL",
    common:{path:"COMMON.md",text:"Exact current COMMON source",content_digest:"common-digest"},
    soul:{path:"SOUL.md",text:"Exact current SOUL source",content_digest:"soul-digest"}};
  if (route === "work/methods") return {methods:[]};
  if (route === "work/read") return payload.work_run_id === "workrun.failed" ? {status:"FAIL",message:"unavailable"} : work;
  if (route === "work/inspection") return staleInspection ? {...inspection,run_revision:3} : inspection;
  if (route === "work/result") return {run_revision:2,work_spec_digest:"retained-spec-digest",state:"unknown",
    assessment,text:null,truth_evaluated:false,remaining_work:remaining};
  throw new Error("Effect or unexpected route attempted: " + route);
}
let hold = null, replaced = "", staleInspection = false;
const context = {document:{getElementById:get,createElement:create,querySelectorAll:selector=>root.querySelectorAll(selector)},
  window:{addEventListener:(name,callback)=>windowEvents.set(name,callback)},
  location:{hash:"#work_run_id="+workId,pathname:"/console/"},history:{replaceState:(_a,_b,url)=>{replaced=url;}},
  URLSearchParams,AbortController,setTimeout,clearTimeout,setInterval(){},console,
  fetch:async(url,request)=>{
    const route=url.replace("/owner/",""),payload=JSON.parse(request.body); calls.push({route,payload});
    assert.equal(request.method,"POST"); assert.equal(request.credentials,"omit");
    if (hold?.route === route) { const pending=hold; pending.started.resolve(); await pending.release.promise; }
    const body=data(route,payload); return {ok:body.status!=="FAIL",status:body.status==="FAIL"?403:200,json:async()=>body};
  }};
const flush = async()=>{for(let index=0;index<8;index++)await new Promise(resolve=>setImmediate(resolve));};
const privateIds=["history-list","role-policy","method-record","work-summary","criteria","segments","invocations",
  "prior-assessments","gaps","profiles","result-text","result-record","result-evidence","command-state","target","owner-record"];
const clear = ()=>{for(const id of privateIds)assert.equal(get(id).textContent,"",id);assert.equal(get("work-detail").hidden,true);};
async function heldRead(route, trigger) {
  hold={route,started:deferred(),release:deferred()};
  const pending=trigger(); await hold.started.promise;
  const resultCount=calls.filter(call=>call.route==="work/result").length;
  await get("disconnect").fire("click"); clear();
  hold.release.resolve(); await pending; await flush(); clear();
  assert.equal(calls.filter(call=>call.route==="work/result").length,resultCount);
  hold=null;
}
(async()=>{
  vm.runInNewContext(fs.readFileSync(process.argv[2],"utf8"),context); await flush();
  assert.equal(get("work-detail").hidden,false);
  assert.ok(get("history-list").textContent.includes("Discover without path"));
  assert.ok(get("segments").textContent.includes("retained-graph-digest"));
  assert.ok(get("segments").textContent.includes("specialist.retained"));
  assert.ok(get("invocations").textContent.includes("observed-model"));
  assert.ok(get("invocations").textContent.includes("historical-soul-digest"));
  assert.ok(!get("invocations").textContent.includes("configured-model"));
  assert.ok(get("role-policy").textContent.includes("configured-model"));
  assert.ok(get("gaps").textContent.includes("retained independent semantic gap"));
  assert.ok(replaced.endsWith("#work_run_id="+workId));
  assert.equal(calls.filter(call=>call.route==="agents/prompt").length,0);
  await get("role-policy").querySelector("button").fire("click");
  assert.deepEqual(calls.at(-1).payload,{role_id:"role.retained"});
  assert.ok(get("role-policy").textContent.includes("Exact current COMMON source"));
  assert.ok(get("role-policy").textContent.includes("Exact current SOUL source"));
  assert.equal(get("notice").textContent,"Owner 요청을 완료했습니다.");
  await get("refresh-history").fire("click");
  assert.equal(get("notice").textContent,"Owner 요청을 완료했습니다.");
  const link=get("history-list").querySelector("a");
  assert.equal(link.href,"/console/#work_run_id="+workId);
  await link.fire("click"); assert.ok(get("work-summary").textContent.includes(workId));
  get("work-id").value="workrun.failed"; await get("open-work-form").fire("submit");
  assert.equal(get("work-detail").hidden,true); assert.equal(get("invocations").textContent,"");
  get("work-id").value=workId;
  staleInspection=true;
  const priorResultCalls=calls.filter(call=>call.route==="work/result").length;
  await get("open-work-form").fire("submit");
  assert.equal(get("work-detail").hidden,true); assert.equal(get("criteria").textContent,"");
  assert.equal(calls.filter(call=>call.route==="work/result").length,priorResultCalls);
  staleInspection=false;
  await heldRead("work/inspection",()=>get("open-work-form").fire("submit"));
  await get("refresh-owner").fire("click");
  await heldRead("work/history",()=>get("refresh-history").fire("click"));
  await get("refresh-owner").fire("click");
  await heldRead("agents/prompt",()=>get("role-policy").querySelector("button").fire("click"));
  await get("refresh-owner").fire("click"); windowEvents.get("offline")(); clear();
  assert.ok(calls.every(call=>["owner/status","work/history","agents/policy","agents/prompt","work/methods",
    "work/read","work/inspection","work/result"].includes(call.route)));
  console.log(JSON.stringify({headless_console:"pass",actual_html_js:true,synthetic_owner:true,
    browser:false,model_calls:0,cases:["discovery","deep_link","retained_graph","observed_vs_current_policy",
    "lazy_prompt","independent_gaps","failed_switch_clear","revision_switch_clear","work_disconnect","history_disconnect","prompt_disconnect","offline_clear"]}));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    completed = subprocess.run(
        ["node", "-e", script, str(Path(__file__).with_name("thin_client_host.js")), str(control / "frontend/app.js")],
        input=json.dumps(shell.nodes), text=True, capture_output=True, timeout=20, check=True)
    return json.loads(completed.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true",
                        help="Run actual HTML/JS in a bounded Node DOM with synthetic reads, no browser or service")
    parser.add_argument("--port", type=int, default=17902)
    parser.add_argument("--bind-host", choices=("127.0.0.1", "owner"), default="127.0.0.1")
    parser.add_argument("--temporal-target", choices=("server:7233",),
                        help="Exact pinned route in an exclusive disposable Temporal service only")
    parser.add_argument("--inspection-delay-seconds", type=int, choices=range(21), default=0,
                        help="Fixture-only bounded delay before the actual inspection handler")
    arguments = parser.parse_args()
    if arguments.headless:
        print(json.dumps(headless_console(), sort_keys=True))
        return
    os.umask(0o077)
    asyncio.run(serve(arguments))


if __name__ == "__main__":
    main()
