"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let linkedWork = new URLSearchParams(location.hash.slice(1)).get("work_run_id");
  if (linkedWork && !/^[a-z][a-z0-9_.-]{0,159}$/.test(linkedWork)) linkedWork = null;
  if (location.hash) history.replaceState(null, "", location.pathname);
  const state = {epoch: 0, busy: false, owner: null, scope: null, selectedAt: 0,
    work: null, inspection: null, target: null, targetAt: 0, proposal: null, proposalAt: 0,
    daily: null, dailyAt: 0, uncertain: false, search: 0, steering: null, steeringAt: 0, steeringArtifact: null};
  const MAX_AGE = 60000;
  const json = value => JSON.stringify(value, null, 2);
  const node = (tag, text, cls) => { const item = document.createElement(tag); if (text !== undefined) item.textContent = text; if (cls) item.className = cls; return item; };
  function notice(text, kind = "") { $("notice").textContent = text; $("notice").className = "notice " + kind; }
  function fresh(at) { return at > 0 && Date.now() - at < MAX_AGE; }
  function observed(element) { element.classList.remove("stale"); element.dataset.observed = String(Date.now()); element.dataset.label = "조회 " + new Date().toLocaleTimeString("ko-KR"); element.textContent = element.dataset.label + " · 수동 관측값"; }
  function sync() {
    const connected = !!state.owner;
    document.querySelectorAll("[data-owner]").forEach(b => b.disabled = state.busy || !connected);
    document.querySelectorAll("[data-write]").forEach(b => b.disabled = state.busy || !connected || state.owner.read_only || state.uncertain);
    $("refresh-owner").disabled = state.busy;
    const bindings = state.owner?.bindings || {};
    $("intake").disabled ||= !bindings.director_intake || !state.scope;
    $("template-metadata").disabled ||= !bindings.structured_templates || !state.scope;
    $("template-research").disabled ||= !bindings.structured_templates || !state.scope;
    $("intake-availability").textContent = !connected ? "연결 후 바인딩 확인" : bindings.director_intake ? "Director 바인딩 있음 · 실행 Worker 상태는 미관측" : "사용 불가: Director intake 바인딩 없음";
    $("control-target").disabled ||= !bindings.run_control || !state.work;
    $("refresh-steering").disabled ||= !bindings.safe_steering || !state.work;
    $("steering-export").disabled ||= !bindings.safe_steering || !state.work;
    $("steering-availability").textContent = bindings.safe_steering ? "Owner의 실제 안전 경계에서 허용한 조정만 표시합니다." : "사용 불가: 안전한 steering 바인딩 없음. CUTOVER를 RESUME으로 대체하지 않습니다.";
    $("register-steering-artifact").disabled ||= !bindings.control_write || !state.steering?.supported_kinds?.includes($("steering-kind").value) || !fresh(state.steeringAt);
    $("submit-steering").disabled ||= !bindings.control_write || !state.steeringArtifact || !fresh(state.steeringAt);
    const currentTarget = bindings.control_write && state.target && fresh(state.targetAt) && !state.work?.run.pending_action_ids?.length;
    $("control-cancel").disabled ||= !currentTarget;
    $("control-pause").disabled ||= !currentTarget || state.work?.run.state !== "awaiting_human";
    $("control-resume").disabled ||= !currentTarget || state.work?.run.state !== "paused";
    $("proposal-approve").disabled ||= !bindings.proposal_decide || !state.proposal || !fresh(state.proposalAt) || !!state.proposal.decision || state.proposal.source_stale;
    $("proposal-reject").disabled ||= !bindings.proposal_decide || !state.proposal || !fresh(state.proposalAt) || !!state.proposal.decision;
    $("proposal-apply").disabled ||= !bindings.proposal_apply || !state.proposal?.apply_allowed || !fresh(state.proposalAt) || state.proposal.source_stale || decisionValue(state.proposal.decision) !== "approve";
    $("daily-confirm").disabled ||= !bindings.daily_decide || !state.daily || !fresh(state.dailyAt);
    $("daily-reject").disabled ||= !bindings.daily_decide || !state.daily || !fresh(state.dailyAt);
    $("refresh-works").disabled ||= !state.scope;
    $("refresh-work").disabled ||= !state.work;
    $("refresh-proposals").disabled ||= !bindings.proposal_review;
    $("refresh-daily").disabled ||= !bindings.daily_review;
    if (state.owner?.read_only) $("search-mode").value = "lexical";
    $("search-mode").disabled = !!state.owner?.read_only;
  }
  function decisionValue(decision) { return decision?.decision || decision?.response?.chosen_action || decision?.receipt?.chosen_action; }
  async function api(route, payload = {}, mutation = false) {
    const epoch = state.epoch;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    let response;
    try {
      response = await fetch("/owner/" + route, {method: "POST", cache: "no-store", credentials: "omit", redirect: "error", signal: controller.signal,
        headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
      const data = await response.json();
      if (epoch !== state.epoch) throw new Error("연결이 바뀌어 이전 응답을 폐기했습니다.");
      if (!response.ok || data.status === "FAIL") {
        const error = data.errors?.[0];
        throw new Error((error?.code || response.status) + ": " + (error?.message || data.message || "Owner 요청 실패"));
      }
      return data;
    } catch (error) {
      if (epoch !== state.epoch) throw error;
      state.target = null; state.proposalAt = 0; state.dailyAt = 0; state.steeringAt = 0;
      if (mutation && (!response || response.ok || response.status >= 500)) { state.uncertain = true; throw new Error("응답 불명: 요청이 접수되었을 수 있습니다. 자동 재시도하지 않습니다. 같은 명령 ID 또는 작업 목록을 조회하고 Owner에서 대조하세요. " + error.message); }
      throw error;
    } finally { clearTimeout(timer); }
  }
  function bind(id, event, fn) { $(id).addEventListener(event, async e => {
    e.preventDefault(); if (state.busy) return;
    state.busy = true; sync(); notice("Owner에 요청 중…");
    try { await fn(); } catch (error) { notice(error.message, "error"); document.querySelectorAll("[data-observed]").forEach(el => { el.classList.add("stale"); el.textContent = el.dataset.label + " · 이전 관측값 (최근 요청 실패)"; }); }
    finally { state.busy = false; sync(); }
  }); }
  function clearPrivate() {
    state.epoch++; state.owner = null; state.scope = null; state.work = null;
    state.inspection = null;
    state.steering = null; state.steeringArtifact = null;
    ["steering-view", "steering-artifact", "steering-receipt", "steering-observed", "steering-kind"].forEach(id => $(id).replaceChildren());
    ["steering-export-text", "steering-export-record", "steering-export-observed"].forEach(id => $(id).replaceChildren());
    ["steering-answer", "steering-reason", "steering-parameter-id", "steering-successor-spec"].forEach(id => $(id).value = "");
    document.querySelectorAll("[data-observed]").forEach(el => { delete el.dataset.observed; delete el.dataset.label; el.classList.remove("stale"); });
    state.target = null; state.proposal = null; state.daily = null; state.uncertain = false; state.search++;
    $("question").value = ""; $("query").value = ""; $("decision-reason").value = "";
    $("work-id").value = ""; $("command-id").value = ""; $("scope-digest").value = ""; $("scope-path").textContent = "선택한 노트 없음";
    ["search-results", "works", "proposals", "daily-list", "work-summary", "criteria", "segments", "profiles", "gaps", "target", "command-state", "result-state", "result-evidence", "result-text", "result-record", "proposal-source", "proposal-diff", "proposal-text", "proposal-title", "proposal-state", "proposal-action-state", "daily-record", "daily-action-state", "intake-receipt"].forEach(id => $(id).replaceChildren());
    ["work-detail", "proposal-detail", "daily-detail"].forEach(id => $(id).hidden = true);
    $("connection-state").textContent = "연결 안 됨"; $("owner-status").textContent = "화면 데이터를 지웠습니다. Owner 새로 조회로 다시 시작하세요.";
    sync();
  }
  $("disconnect").addEventListener("click", () => { clearPrivate(); notice("연결 해제됨. 비공개 화면 데이터를 지웠습니다. 로컬 Owner는 계속 실행됩니다."); });
  bind("refresh-owner", "click", async () => {
    clearPrivate();
    try { state.owner = await api("owner/status"); } catch (error) { clearPrivate(); throw error; }
    $("connection-state").textContent = state.owner.read_only ? "읽기 전용 연결" : "Owner 연결됨";
    $("owner-status").textContent = `knowledgeos · generation ${state.owner.generation} · revision ${state.owner.revision} · 조회 ${state.owner.observed_at}. Worker/Workflow 실시간 상태는 미관측입니다.`;
    $("owner-status").dataset.observed = String(Date.now()); $("owner-status").dataset.label = $("owner-status").textContent;
    notice("연결되었습니다. 검색으로 정확한 노트 범위를 선택하세요.", "success");
    if (linkedWork) { const id = linkedWork; linkedWork = null; await loadWork(id); }
  });
  document.querySelectorAll("[data-tab]").forEach(button => button.addEventListener("click", () => {
    document.querySelectorAll("[data-tab]").forEach(b => { if (b === button) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
    document.querySelectorAll(".tab-panel").forEach(panel => panel.hidden = panel.id !== "tab-" + button.dataset.tab);
  }));
  bind("search-form", "submit", async () => {
    const serial = ++state.search;
    const result = await api("search/query", {query: $("query").value.trim(), mode: $("search-mode").value});
    if (serial !== state.search) return;
    const root = $("search-results"); root.replaceChildren();
    root.append(node("p", `generation ${result.index_generation_id} · ${result.results.length}개 노트`, "muted"));
    const observation = node("p", "", "observation"); observed(observation); root.append(observation);
    result.results.forEach(item => {
      const article = node("article", undefined, "search-item");
      article.append(node("h3", item.title), node("p", item.excerpt));
      const choose = node("button", item.path); choose.type = "button"; choose.dataset.scopePath = item.path;
      choose.addEventListener("click", async () => {
        if (state.busy) return; state.busy = true; sync();
        state.scope = {path: item.path, content_sha256: item.resource_reference.source_revision}; state.selectedAt = Date.now();
        state.work = null; state.target = null; $("work-detail").hidden = true;
        $("scope-path").textContent = item.path; $("scope-digest").value = state.scope.content_sha256;
        root.querySelectorAll("article").forEach(a => a.classList.toggle("selected", a === article));
        try { await loadWorks(); notice("정확한 원문 범위를 선택했습니다.", "success"); } catch (error) { notice(error.message, "error"); }
        finally { state.busy = false; sync(); }
      });
      article.append(choose, node("small", `${item.chunk_locator} · ${item.chunk_hash}`)); root.append(article);
    });
    if (!result.results.length) root.append(node("p", "검색 결과가 없습니다. 다른 단어나 검색 방식을 선택하세요.", "empty"));
    notice("Owner 검색 결과를 조회했습니다.", "success");
  });
  async function loadWorks() {
    if (!state.scope) throw new Error("먼저 노트를 선택하세요.");
    const result = await api("work/list", {path: state.scope.path});
    $("works").replaceChildren();
    const observation = node("p", "", "observation"); observed(observation); $("works").append(observation);
    result.works.forEach(item => {
      const b = node("button", undefined, "list-item"); b.type = "button"; b.dataset.workRunId = item.work_run_id;
      b.append(node("strong", item.work_run_id), node("span", `${item.team_id || "Officer"} · ${item.run_state} · 접수 ${item.receipt_state} / 전달 ${item.dispatch_state} / 적용 ${item.application_state} · 평가 ${item.evaluation_state}${item.source_stale ? " · 원문 변경됨" : ""}`));
      b.addEventListener("click", () => { if (!state.busy) { $("work-id").value = item.work_run_id; $("open-work-form").requestSubmit(); } }); $("works").append(b);
    });
    if (!result.works.length) $("works").append(node("p", "이 노트로 접수된 작업이 없습니다.", "empty"));
  }
  bind("refresh-works", "click", async () => { await loadWorks(); notice("작업 목록을 새로 조회했습니다.", "success"); });
  bind("intake-form", "submit", async () => {
    if (!state.scope) throw new Error("먼저 정확한 노트를 선택하세요.");
    const receipt = await api("work/intake", {question: $("question").value, scope: state.scope}, true);
    $("intake-receipt").textContent = json(receipt); await loadWorks(); notice("작업 접수 영수증을 받았습니다. 실행과 독립 평가 완료는 별도입니다.", "success");
  });
  async function template(template_id) {
    const request_id = crypto.randomUUID(); $("intake-receipt").textContent = json({request_id, template_id, state: "전송 전"});
    const receipt = await api("work/intake-template", {template_id, scope: state.scope, request_id}, true);
    $("intake-receipt").textContent = json(receipt); await loadWorks(); notice("구조화된 작업이 접수되었습니다. Pending 승인과 원본 적용은 별도입니다.", "success");
  }
  bind("template-metadata", "click", () => template("note_metadata"));
  bind("template-research", "click", () => template("document_enhancement"));
  function facts(root, values) {
    const dl = node("dl", undefined, "facts"); values.forEach(([label, value]) => dl.append(node("dt", label), node("dd", value ?? "미제공"))); root.append(dl);
  }
  function renderResultEvidence(value) {
    const root = $("result-evidence");
    if (value === undefined || value === null) return;
    if (Array.isArray(value)) {
      if (!value.every(item => item && typeof item.path === "string" && typeof item.chunk_locator === "string" && typeof item.excerpt === "string")) {
        throw new Error("Owner 인용 근거의 경로·위치·발췌 형식이 올바르지 않습니다.");
      }
      value.forEach(item => {
        const block = node("article", undefined, "evidence");
        block.dataset.evidenceKind = "citation";
        block.append(node("strong", item.path), node("small", item.chunk_locator), node("p", item.excerpt));
        root.append(block);
      });
      return;
    }
    if (typeof value !== "object") throw new Error("Owner 근거는 인용 목록 또는 구조화된 객체여야 합니다.");
    const block = node("section", undefined, "evidence");
    block.dataset.evidenceKind = "structured";
    block.append(node("h4", "구조화된 Owner 근거"), node("p", "필드·값의 인용이나 교환·진단 근거를 원래 구조 그대로 표시합니다."), node("pre", json(value)));
    root.append(block);
  }
  async function loadWork(id) {
    const epoch = state.epoch;
    function requireCurrentView() {
      if (epoch !== state.epoch || !state.owner) throw new Error("연결이 바뀌어 이전 작업 조회를 폐기했습니다.");
    }
    state.target = null; $("target").textContent = "상세 조회 후 제어 대상을 다시 확인하세요.";
    state.steering = null; state.steeringArtifact = null; $("steering-kind").replaceChildren();
    ["steering-view", "steering-artifact", "steering-receipt"].forEach(id => $(id).textContent = "작업 조회 후 새로 확인하세요.");
    ["steering-answer", "steering-reason", "steering-parameter-id", "steering-successor-spec"].forEach(id => $(id).value = "");
    ["steering-export-text", "steering-export-record", "steering-export-observed"].forEach(id => $(id).replaceChildren());
    const work = await api("work/read", {work_run_id: id});
    requireCurrentView(); state.work = work; $("work-detail").hidden = false;
    $("work-id").value = id; observed($("work-observed")); $("work-summary").replaceChildren();
    facts($("work-summary"), [["작업", work.spec.objective], ["WorkRun", id], ["실행 상태", work.run.state], ["접수 / 전달 / 적용", `${work.receipt.receipt_state} / ${work.dispatch_state} / ${work.application_state}`], ["배정", work.spec.manager_executor_id || work.spec.officer_executor_id], ["EvalOfficer", work.spec.eval_officer_executor_id], ["현재 GraphRun", work.run.active_graph_run_id || "활성 GraphRun 없음"], ["Revision / control", `${work.run.revision} / ${work.run.control_revision}`], ["누적 사용량", `세그먼트 ${work.run.segments_started} / Profile ${work.run.profile_invocations} / 반복 gap ${work.run.repeated_gap_count}`], ["대기 명령", (work.run.pending_action_ids || []).join(", ") || "없음"], ["미해결 효과", (work.run.unresolved_effect_ids || []).join(", ") || "없음"], ["Runtime", "실시간 Worker/Workflow Query 바인딩 없음 · 상태 미관측"]]);
    $("profiles").textContent = json({allowed_profiles: work.spec.allowed_profiles, explicit_assignments: work.spec.explicit_assignments, continuation_policy: work.spec.continuation_policy});
    $("segments").replaceChildren(); $("criteria").replaceChildren(); $("gaps").replaceChildren();
    $("result-text").textContent = ""; $("result-record").textContent = ""; $("result-evidence").replaceChildren();
    let inspection = null;
    try { inspection = await api("work/inspection", {work_run_id: id}); }
    catch (error) {
      requireCurrentView();
      $("segments").append(node("p", "계보 조회 사용 불가: " + error.message, "muted"));
    }
    requireCurrentView();
    state.inspection = inspection;
    work.spec.acceptance_criteria.forEach(criterion => {
      const outcome = inspection?.assessment?.criterion_outcomes?.find(o => o.criterion_id === criterion.criterion_id);
      const block = node("article", undefined, "criterion " + (outcome?.outcome || ""));
      block.append(node("strong", criterion.description), node("p", `${criterion.criterion_id} · ${outcome?.outcome || "독립 평가 미제공"}`));
      if (outcome?.evidence_references?.length) block.append(node("pre", json(outcome.evidence_references))); $("criteria").append(block);
    });
    if (inspection) {
      if (!inspection.segments.length) $("segments").append(node("p", "Owner에 export 가능한 승인 세그먼트가 없습니다. 아래 WorkRun 계보를 확인하세요.", "muted"));
      inspection.segments.forEach(item => { const detail = node("details"); detail.append(node("summary", `세그먼트 ${item.segment.segment_index} · ${item.segment.graph_run_id}`), node("pre", json(item))); $("segments").append(detail); });
    }
    $("segments").append(node("pre", json({graph_run_ids: work.run.graph_run_ids, active_graph_run_id: work.run.active_graph_run_id})));
    if (work.remaining_work) { $("gaps").append(node("h3", "남은 기준과 평가 gap"), node("pre", json(work.remaining_work))); }
    try {
      requireSession();
      const result = await api("work/result", {work_run_id: id});
      requireSession();
      $("result-state").textContent = result.reason || `Owner 결과 · ${result.state} · 독립 평가 ${result.assessment?.completion_state || "없음"}`;
      if (result.truth_evaluated === false) $("result-state").textContent += " · 내용 진위 평가 아님";
      $("result-text").textContent = result.text || "아직 독립 평가된 결과 본문이 없습니다.";
      $("result-record").textContent = json({assessment: result.assessment, report: result.report, evidence: result.evidence, evaluations: inspection?.evaluations, result_reference: work.run.result_reference, truncated: result.truncated, truth_evaluated: result.truth_evaluated, apply_allowed: result.apply_allowed});
      renderResultEvidence(result.evidence);
    } catch (error) {
      requireSession();
      $("result-state").textContent = "결과 사용 불가: " + error.message;
    }
    requireSession();
    notice("Owner의 작업 상태와 근거를 조회했습니다. 실행 완료와 Pending 적용은 다릅니다.", "success");
  }
  bind("open-work-form", "submit", () => loadWork($("work-id").value.trim()));
  bind("refresh-work", "click", () => loadWork(state.work.run.work_run_id));
  bind("control-target", "click", async () => {
    state.target = null;
    const target = await api("work/control/target", {work_run_id: state.work.run.work_run_id});
    state.target = target; state.targetAt = Date.now(); $("target").textContent = json(target);
    notice("현재 revision의 제어 대상을 조회했습니다. 60초 후에는 다시 확인해야 합니다.", "success");
  });
  async function control(kind) {
    if (!state.target || !fresh(state.targetAt)) throw new Error("제어 대상을 새로 확인하세요.");
    if (!confirm(`이 정확한 WorkRun에 ${kind.toUpperCase()} 명령을 접수할까요? 접수는 적용 완료가 아닙니다.`)) return;
    const command_id = "web." + crypto.randomUUID(); $("command-id").value = command_id;
    const payload = {work_run_id: state.work.run.work_run_id, command_id, target: state.target, expires_at: new Date(Date.now() + 300000).toISOString()};
    $("command-state").textContent = json({command_id, application_state: "응답 대기"});
    state.target = null;
    const receipt = await api("work/control/" + kind, payload, true); $("command-state").textContent = json(receipt);
    notice(`명령 접수 응답: ${receipt.application_state}. 적용 여부는 같은 명령 ID로 조회하세요.`, "success");
  }
  ["pause", "resume", "cancel"].forEach(kind => bind("control-" + kind, "click", () => control(kind)));
  bind("command-form", "submit", async () => {
    if (!state.work) throw new Error("먼저 작업을 여세요.");
    const receipt = await api("work/control/command", {work_run_id: state.work.run.work_run_id, command_id: $("command-id").value.trim()});
    $("command-state").textContent = json(receipt); notice(`명령 접수 ${receipt.receipt_state} / 적용 ${receipt.application_state}`, "success");
  });
  async function loadProposal(reference) {
    state.proposal = null;
    const item = await api("proposal/read", {proposal_reference: reference}); state.proposal = item; state.proposalAt = Date.now();
    $("proposal-detail").hidden = false; $("proposal-title").textContent = item.target_path || "Pending 제안"; observed($("proposal-state"));
    $("proposal-source").textContent = json({source_path: item.source_path, admitted_source_digest: item.source_digest, current_source_reference: item.source_reference, source_stale: item.source_stale, field_evidence: item.field_evidence});
    $("proposal-diff").textContent = item.diff ?? "원본 적용 diff가 제공되지 않습니다. 이 제안의 허용 행동을 확인하세요.";
    $("proposal-text").textContent = item.text;
    $("proposal-source").textContent += "\n\n원문" + (item.source_truncated ? " (앞 16384자, 잘림)" : "") + ":\n" + (item.source_text ?? "사용 불가");
    $("proposal-action-state").textContent = `결정 ${decisionValue(item.decision) || "미결정"} · ${item.apply_allowed ? "승인 후 별도 적용 필요" : "이 제안은 원본 적용을 지원하지 않습니다"}${item.source_stale ? " · 원문 변경됨: 승인·적용 차단" : ""}`;
  }
  bind("refresh-proposals", "click", async () => {
    const result = await api("proposal/list"); $("proposals").replaceChildren();
    const observation = node("p", "", "observation"); observed(observation); $("proposals").append(observation);
    result.proposals.forEach(item => { const b = node("button", (item.proposal_reference.logical_id || item.proposal_reference.artifact_id) + (item.apply_allowed ? " · 별도 적용 가능" : " · 결정만 가능"), "list-item"); b.type = "button"; b.addEventListener("click", async () => { if (state.busy) return; state.busy = true; sync(); try { await loadProposal(item.proposal_reference); notice("Pending 원문과 변경 diff를 조회했습니다.", "success"); } catch (error) { notice(error.message, "error"); } finally { state.busy = false; sync(); } }); $("proposals").append(b); });
    if (!result.proposals.length) $("proposals").append(node("p", "Owner가 export한 Pending 제안이 없습니다.", "empty"));
    notice("Pending 목록을 조회했습니다.", "success");
  });
  async function decideProposal(decision) {
    const reference = state.proposal.proposal_reference;
    const receipt = await api("proposal/decide", {proposal_reference: reference, decision, reason: $("decision-reason").value}, true);
    $("proposal-action-state").textContent = json(receipt); await loadProposal(reference);
    notice(decision === "approve" ? "승인을 기록했습니다. 원본은 아직 적용하지 않았습니다." : "거절을 기록했습니다. 원본 적용은 수행하지 않았습니다.", "success");
  }
  bind("proposal-approve", "click", () => decideProposal("approve")); bind("proposal-reject", "click", () => decideProposal("reject"));
  bind("proposal-apply", "click", async () => {
    if (!confirm("승인한 제안을 원본에 적용할까요? 이 작업은 결정 기록과 별개의 Vault 변경입니다.")) return;
    const receipt = await api("proposal/apply", {proposal_reference: state.proposal.proposal_reference}, true);
    state.proposal = null; $("proposal-action-state").textContent = "Owner 원본 적용 응답:\n" + json(receipt);
    notice("Owner의 원본 적용 응답을 받았습니다. 원문이 변경되어 이전 검색·범위는 오래된 값입니다. 새로 검색하세요.", "success");
    state.scope = null; $("scope-path").textContent = "적용 후 새로 검색 필요"; $("scope-digest").value = "";
  });
  async function loadDaily(effect_id) {
    state.daily = null; const item = await api("daily/read", {effect_id}); state.daily = item; state.dailyAt = Date.now();
    $("daily-detail").hidden = false; $("daily-record").textContent = json(item); observed($("daily-action-state"));
  }
  bind("refresh-daily", "click", async () => {
    const result = await api("daily/list"); $("daily-list").replaceChildren();
    const observation = node("p", "", "observation"); observed(observation); $("daily-list").append(observation);
    result.pending.forEach(item => { const b = node("button", `${item.path} · ${item.review_status}`, "list-item"); b.type = "button"; b.addEventListener("click", async () => { if (state.busy) return; state.busy = true; sync(); try { await loadDaily(item.effect_id); notice("현재 필드·잠정 변경·근거를 조회했습니다.", "success"); } catch (error) { notice(error.message, "error"); } finally { state.busy = false; sync(); } }); $("daily-list").append(b); });
    if (!result.pending.length) $("daily-list").append(node("p", "검토할 잠정 변경이 없습니다.", "empty")); notice("일일 검토 목록을 조회했습니다.", "success");
  });
  async function dailyDecision(decision) {
    if (!confirm(decision === "confirm" ? "이 잠정 변경을 확정할까요?" : "이 잠정 변경을 거절하고 Owner의 정확한 delta 복구를 요청할까요?")) return;
    const receipt = await api("daily/decide", {effect_id: state.daily.effect_id, decision}, true); state.daily = null;
    delete $("daily-action-state").dataset.observed; delete $("daily-action-state").dataset.label;
    $("daily-action-state").textContent = json(receipt); notice("Owner 일일 검토 응답을 받았습니다. 목록을 새로 조회하세요.", "success");
  }
  bind("daily-confirm", "click", () => dailyDecision("confirm")); bind("daily-reject", "click", () => dailyDecision("reject"));
  const steeringLabels = {answer: "질문 응답", parameter: "허용 parameter 변경", cutover: "부분 Graph 교체", amend_work: "Director 승인 WorkSpec 개정", resume: "닫힌 Graph에서 계속 실행"};
  function steeringFields() {
    const kind = $("steering-kind").value;
    $("steering-answer-fields").hidden = kind !== "answer";
    $("steering-parameter-fields").hidden = kind !== "parameter";
    $("steering-reason-fields").hidden = !["cutover", "resume"].includes(kind);
    $("steering-amendment-fields").hidden = kind !== "amend_work";
    state.steeringArtifact = null; $("steering-artifact").textContent = "입력 내용에 맞는 조정 근거를 등록하세요."; sync();
  }
  $("steering-kind").addEventListener("change", steeringFields);
  function parameterOptions() {
    const values = state.steering?.parameter_bounds?.[$("steering-parameter-id").value] || [];
    $("steering-parameter-value").replaceChildren();
    values.forEach(value => { const option = node("option", value); option.value = value; $("steering-parameter-value").append(option); });
  }
  $("steering-parameter-id").addEventListener("input", parameterOptions);
  ["steering-answer", "steering-parameter-id", "steering-parameter-value", "steering-reason", "steering-successor-spec"].forEach(id => $(id).addEventListener("input", () => { state.steeringArtifact = null; sync(); }));
  bind("refresh-steering", "click", async () => {
    state.steering = null; state.steeringArtifact = null; $("steering-kind").replaceChildren();
    const view = await api("work/steering/view", {work_run_id: state.work.run.work_run_id});
    state.steering = view; state.steeringAt = Date.now(); $("steering-view").textContent = json(view); observed($("steering-observed"));
    $("steering-parameter-id").value = Object.keys(view.parameter_bounds || {})[0] || ""; parameterOptions();
    (view.supported_kinds || []).forEach(kind => { if (steeringLabels[kind]) { const option = node("option", steeringLabels[kind]); option.value = kind; $("steering-kind").append(option); } });
    steeringFields(); notice("현재 질문·허용 조정과 기존 명령의 접수·적용 상태를 조회했습니다.", "success");
  });
  function steeringInput() {
    const kind = $("steering-kind").value;
    if (!state.steering?.supported_kinds?.includes(kind) || !fresh(state.steeringAt)) throw new Error("Owner의 허용 조정을 먼저 새로 조회하세요.");
    if (kind === "answer") return {kind, content: {answer: $("steering-answer").value}};
    if (kind === "parameter") {
      const parameter_id = $("steering-parameter-id").value;
      const value = $("steering-parameter-value").value;
      if (!state.steering.parameter_bounds?.[parameter_id]?.includes(value)) throw new Error("Owner가 허용한 parameter와 값을 선택하세요.");
      return {kind, parameter_id, content: {value}};
    }
    if (kind === "amend_work") {
      const successor_spec = JSON.parse($("steering-successor-spec").value);
      return {kind, successor_spec, content: {successor_spec_digest: successor_spec.spec_digest}};
    }
    return {kind, content: {reason: $("steering-reason").value}};
  }
  bind("steering-artifact-form", "submit", async () => {
    const selected = steeringInput();
    const artifact = await api("work/steering/artifact", {work_run_id: state.work.run.work_run_id, content: selected.content}, true);
    state.steeringArtifact = {reference: artifact, selected};
    $("steering-artifact").textContent = json(artifact); notice("조정 근거를 등록했습니다. 명령은 아직 접수하지 않았습니다.", "success");
  });
  bind("submit-steering", "click", async () => {
    if (!state.steeringArtifact || !fresh(state.steeringAt)) throw new Error("현재 revision의 조정 근거를 먼저 등록하세요.");
    if (!confirm("이 정확한 revision에 조정 명령을 접수할까요? 실제 적용 여부는 별도 조회해야 합니다.")) return;
    const {reference, selected} = state.steeringArtifact;
    const command_id = "web.steering." + crypto.randomUUID();
    const payload = {work_run_id: state.work.run.work_run_id, command_id, kind: selected.kind, target: state.steering.target, expires_at: new Date(Date.now() + 300000).toISOString(), directive_reference: reference};
    if (selected.parameter_id !== undefined) payload.parameter_id = selected.parameter_id;
    if (selected.successor_spec !== undefined) payload.successor_spec = selected.successor_spec;
    state.steeringArtifact = null; $("steering-receipt").textContent = json({command_id, state: "응답 대기"});
    const receipt = await api("work/steering/request", payload, true);
    $("steering-receipt").textContent = json(receipt); state.steeringAt = 0;
    notice("Owner의 명령 접수 응답을 받았습니다. 실제 적용 여부는 질문·허용 조정 새로 조회에서 확인하세요.", "success");
  });
  bind("steering-export", "click", async () => {
    const exported = await api("work/steering/export", {work_run_id: state.work.run.work_run_id});
    const bytes = Uint8Array.from(atob(exported.content_base64), character => character.charCodeAt(0));
    $("steering-export-text").textContent = new TextDecoder("utf-8", {fatal: true}).decode(bytes);
    $("steering-export-record").textContent = json({reference: exported.reference, assessment: exported.assessment});
    observed($("steering-export-observed")); notice("Owner가 독립 평가한 정확한 artifact bytes를 조회했습니다.", "success");
  });
  setInterval(() => {
    document.querySelectorAll("[data-observed]").forEach(el => { if (!fresh(Number(el.dataset.observed))) { el.classList.add("stale"); el.textContent = el.dataset.label + " · 오래된 관측값 (60초 경과) · 새로 조회 필요"; } });
    sync();
  }, 1000);
  window.addEventListener("offline", () => { notice("네트워크 연결 끊김. 표시 값은 이전 관측이며 자동 재시도하지 않습니다.", "error"); state.target = null; state.proposalAt = 0; state.dailyAt = 0; state.steeringAt = 0; document.querySelectorAll("[data-observed]").forEach(el => { el.classList.add("stale"); el.textContent = el.dataset.label + " · 연결 끊김 · 이전 관측값"; }); sync(); });
  window.addEventListener("pagehide", clearPrivate);
  sync();
  $("refresh-owner").click();
})();
