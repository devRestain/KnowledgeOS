"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let linkedWork = new URLSearchParams(location.hash.slice(1)).get("work_run_id");
  if (linkedWork && !/^[a-z][a-z0-9_.-]{0,159}$/.test(linkedWork)) linkedWork = null;
  if (location.hash) history.replaceState(null, "", location.pathname);
  const state = {epoch: 0, busy: false, owner: null, work: null,
    target: null, targetAt: 0, uncertain: false, methods: [], historyCursor: null, policy: null};
  const MAX_AGE = 60000;
  const json = value => JSON.stringify(value, null, 2);
  const node = (tag, text, cls) => { const item = document.createElement(tag); if (text !== undefined) item.textContent = text; if (cls) item.className = cls; return item; };
  const staleResponse = () => Object.assign(new Error("연결이 바뀌어 이전 응답을 폐기했습니다."), {staleResponse: true});
  function disclosure(root, title, content) {
    const detail = node("details"); detail.append(node("summary", title), node("pre", typeof content === "string" ? content : json(content))); root.append(detail); return detail;
  }
  function notice(text, kind = "") { $("notice").textContent = text; $("notice").className = "notice " + kind; }
  function fresh(at) { return at > 0 && Date.now() - at < MAX_AGE; }
  function observed(element) { element.classList.remove("stale"); element.dataset.observed = String(Date.now()); element.dataset.label = "조회 " + new Date().toLocaleTimeString("ko-KR"); element.textContent = element.dataset.label + " · 수동 관측값"; }
  function sync() {
    const connected = !!state.owner;
    document.querySelectorAll("[data-owner]").forEach(b => b.disabled = state.busy || !connected);
    document.querySelectorAll("[data-write]").forEach(b => b.disabled = state.busy || !connected || state.owner.read_only || state.uncertain);
    $("refresh-owner").disabled = state.busy;
    const bindings = state.owner?.bindings || {};
    $("control-target").disabled ||= !bindings.run_control || !state.work;
    const currentTarget = bindings.control_write && state.target && fresh(state.targetAt) && !state.work?.run.pending_action_ids?.length;
    $("control-cancel").disabled ||= !currentTarget;
    $("control-pause").disabled ||= !currentTarget || state.work?.run.state !== "awaiting_human";
    $("control-resume").disabled ||= !currentTarget || state.work?.run.state !== "paused";
    $("refresh-work").disabled ||= !state.work;
  }
  async function api(route, payload = {}, mutation = false) {
    const epoch = state.epoch;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    let response;
    try {
      response = await fetch("/owner/" + route, {method: "POST", cache: "no-store", credentials: "omit", redirect: "error", signal: controller.signal,
        headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
      const data = await response.json();
      if (epoch !== state.epoch) throw staleResponse();
      if (!response.ok || data.status === "FAIL") {
        const error = data.errors?.[0];
        throw new Error((error?.code || response.status) + ": " + (error?.message || data.message || "Owner 요청 실패"));
      }
      return data;
    } catch (error) {
      if (epoch !== state.epoch) throw staleResponse();
      state.target = null;
      if (mutation && (!response || response.ok || response.status >= 500)) { state.uncertain = true; throw new Error("응답 불명: 요청이 접수되었을 수 있습니다. 자동 재시도하지 않습니다. 같은 명령 ID 또는 WorkRun 상태를 조회하고 Owner에서 대조하세요. " + error.message); }
      throw error;
    } finally { clearTimeout(timer); }
  }
  async function action(fn) {
    if (state.busy) return;
    state.busy = true; sync(); notice("Owner에 요청 중…");
    try {
      await fn();
      if ($("notice").textContent === "Owner에 요청 중…") notice("Owner 요청을 완료했습니다.", "success");
    } catch (error) {
      if (error.staleResponse) return;
      notice(error.message, "error");
      document.querySelectorAll("[data-observed]").forEach(el => { el.classList.add("stale"); el.textContent = el.dataset.label + " · 이전 관측값 (최근 요청 실패)"; });
    } finally { state.busy = false; sync(); }
  }
  function bind(id, event, fn) { $(id).addEventListener(event, e => { e.preventDefault(); return action(fn); }); }
  function clearWork() {
    state.work = null; state.target = null; state.targetAt = 0;
    $("command-id").value = "";
    ["work-summary", "criteria", "segments", "profiles", "gaps", "invocations", "prior-assessments", "target", "command-state", "result-state", "result-evidence", "result-text", "result-record", "work-observed"].forEach(id => $(id).replaceChildren());
    $("work-detail").hidden = true;
    history.replaceState(null, "", location.pathname);
  }
  function clearPrivate() {
    state.epoch++; state.owner = null; state.uncertain = false;
    state.methods = []; state.historyCursor = null; state.policy = null;
    clearWork();
    ["method-id", "method-status", "method-parameters", "method-record", "history-list", "history-observed", "role-policy", "policy-observed", "owner-record"].forEach(id => $(id).replaceChildren());
    $("history-more").hidden = true;
    document.querySelectorAll("[data-observed]").forEach(el => { delete el.dataset.observed; delete el.dataset.label; el.classList.remove("stale"); });
    $("work-id").value = "";
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
    $("owner-record").textContent = json(state.owner);
    notice("연결되었습니다. 보관된 Work 기록을 선택하거나 정확한 ID로 여세요. 문서 작업은 Obsidian에서 수행합니다.", "success");
    for (const load of [loadHistory, loadPolicy, loadMethods]) {
      try { await load(); } catch (error) { if (error.staleResponse) throw error; notice(error.message, "error"); }
    }
    if (linkedWork) { const id = linkedWork; linkedWork = null; await loadWork(id); }
  });
  async function loadHistory(more = false) {
    const before = more ? state.historyCursor : null;
    if (!more) { $("history-list").replaceChildren(); state.historyCursor = null; }
    $("history-more").hidden = true;
    const result = await api("work/history", {limit: 30, ...(before ? {before} : {})});
    observed($("history-observed")); state.historyCursor = result.next_before;
    if (!result.works.length && !more) $("history-list").append(node("p", "보관된 Work가 없습니다.", "muted"));
    for (const work of result.works) {
      const card = node("article", undefined, "history-card"), link = node("a", work.objective || work.work_run_id);
      link.href = "/console/#work_run_id=" + encodeURIComponent(work.work_run_id);
      link.addEventListener("click", event => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button > 0) return;
        event.preventDefault(); if (state.owner) return action(() => loadWork(work.work_run_id));
      });
      card.append(link, node("small", work.work_run_id), node("p", `${work.method_id || work.target_kind || "method 미제공"} · ${work.team_id || "Officer"} · ${work.run_state} · 독립 평가 ${work.assessment_state}`),
        node("small", `접수 ${work.receipt_state} / 전달 ${work.dispatch_state} / 적용 ${work.application_state} · 세그먼트 ${work.segments_started} · 접수 ${work.recorded_at || "시간 미제공"}`));
      $("history-list").append(card);
    }
    $("history-more").hidden = !state.historyCursor;
  }
  bind("refresh-history", "click", () => loadHistory());
  bind("history-more", "click", () => loadHistory(true));
  async function loadPolicy() {
    state.policy = null; $("role-policy").replaceChildren(); $("policy-observed").replaceChildren();
    const policy = await api("agents/policy");
    state.policy = policy; observed($("policy-observed"));
    if (policy.availability !== "available") { $("role-policy").append(node("p", policy.reason || "현재 역할 설정 사용 불가", "muted")); return; }
    $("role-policy").append(node("p", "현재 정책 digest · " + policy.policy_digest, "identity"));
    disclosure($("role-policy"), "현재 route별 모델과 reasoning 설정", policy.routes);
    for (const role of policy.roles) {
      const detail = node("details"), body = node("div"), route = policy.routes[role.default_route] || {};
      detail.append(node("summary", `${role.role_id} · ${role.default_route} · ${route.model || "모델 미제공"} · ${route.reasoning_effort ?? (route.think === false ? "think=false" : "effort 미제공")}`), body);
      body.append(node("p", role.purpose || "별도 purpose 미정의 · 아래 SOUL 참조"));
      facts(body, [["허용 route", role.allowed_routes.join(", ")], ["수동 home 이름", role.manual_homes.join(", ") || "없음"], ["현재 COMMON+SOUL digest", role.prompt_digest]]);
      const button = node("button", "현재 COMMON + SOUL 원문 조회"); button.type = "button"; button.dataset.owner = "";
      const prompt = node("div"); body.append(button, prompt);
      button.addEventListener("click", () => action(async () => {
        prompt.replaceChildren();
        const result = await api("agents/prompt", {role_id: role.role_id});
        if (result.availability !== "available") { prompt.append(node("p", result.reason || "프롬프트 사용 불가")); return; }
        if (result.policy_digest !== policy.policy_digest || result.prompt_digest !== role.prompt_digest) {
          state.policy = null; $("role-policy").replaceChildren();
          throw new Error("현재 역할 설정이 바뀌었습니다. 역할 설정을 새로 조회하세요.");
        }
        prompt.append(node("p", result.composition));
        disclosure(prompt, `${result.common.path} · ${result.common.content_digest}`, result.common.text);
        disclosure(prompt, `${result.soul.path} · ${result.soul.content_digest}`, result.soul.text);
      }));
      $("role-policy").append(detail);
    }
  }
  bind("refresh-policy", "click", loadPolicy);
  function renderExecution(inspection) {
    const invocations = inspection.invocations || [];
    facts($("invocations"), Object.entries(inspection.role_observations || {}).map(([role, status]) => [role, status]));
    if (!invocations.length) $("invocations").append(node("p", "호출 증거 미제공 · 실행하지 않았다는 뜻은 아닙니다.", "muted"));
    for (const invocation of invocations) {
      const actual = invocation.observed || {}, card = node("article", undefined, "invocation");
      card.append(node("h4", `${invocation.responsibility} · ${invocation.role_id}`),
        node("p", `${invocation.observation_state} · ${actual.provider || "provider 미관측"} / ${actual.model || "model 미관측"} / ${actual.reasoning_effort ?? (actual.think === false ? "think=false" : "reasoning 미관측")}`));
      facts(card, [["Graph / stage", `${invocation.graph_run_id || "없음"} / ${invocation.stage_id || "미제공"}`],
        ["실제 route / session", `${actual.route_name || "미관측"} / ${actual.session_id || "미관측"}`],
        ["요청 route (실제 실행 아님)", invocation.requested_route || "미제공"],
        ["과거 COMMON+SOUL digest", actual.prompt_digest || "미관측"],
        ["합성 instructions digest", actual.instructions_digest || "미관측"],
        ["역할 요청 digest", actual.request_digest || "미관측"],
        ["Owner 요청 envelope digest", invocation.owner_request_digest || "미제공"],
        ["보관된 호출 증거 digest", invocation.evidence_digest || "미제공"],
        ["과거 프롬프트 원문", invocation.historical_prompt_text === "not_retained" ? "보관되지 않음 · 현재 원문과 구분" : invocation.historical_prompt_text],
        ["보관된 증거 무결성", invocation.integrity || "별도 digest 없음"]]);
      disclosure(card, invocation.instruction_scope === "retained_manager_step" ? "보관된 Manager step 지시문 (최종 합성 프롬프트 아님)" : "보관된 역할 요청 지시문 (COMMON+SOUL 전체 원문 아님)", invocation.instructions || "미제공");
      disclosure(card, "실제 wire digest · tool 호출 요약 · 원래 runtime pins", {
        observed: actual, transport_requests: invocation.transport_requests, tool_calls: invocation.tool_calls,
        runner_binding: invocation.runner_binding, source: invocation.source, trace_truncated: invocation.trace_truncated});
      $("invocations").append(card);
    }
    const previous = inspection.prior_assessments || [];
    if (!previous.length) $("prior-assessments").append(node("p", "이전 독립 평가가 별도로 보관되지 않았습니다.", "muted"));
    previous.forEach(item => disclosure($("prior-assessments"), `${item.source} · ${item.assessment.completion_state}`, item));
  }
  const parameterLabels = {
    objective: "작업 목표", question: "질문", scope: "작업 범위", target_path: "초안 저장 경로 (Vault 내부 .md)",
    target_type: "문서 유형", title: "문서 제목", period_start: "검토 시작일", period_end: "검토 종료일",
    query: "검토 검색어", base: "Base 경로", view: "Base 보기", gateway_receipt_id: "수락된 Gateway 영수증 ID",
    control_checkpoint: "독립 평가 후 제어 checkpoint에서 대기", continuation_policy: "계속 실행 한도",
    max_segments: "최대 세그먼트", max_repeated_gap_assessments: "같은 gap 최대 평가 횟수", max_profile_invocations: "최대 Profile 실행 횟수",
  };
  function parameterPreview(parent, schema, defaults = {}) {
    Object.entries(schema?.properties || {}).forEach(([name, field]) => {
      const label = (parameterLabels[name] || name) + ((schema.required || []).includes(name) ? " · 필수" : " · 선택");
      const value = defaults[name] ?? field.default;
      if (field.type === "object") {
        const group = node("fieldset"); group.append(node("legend", label)); parent.append(group);
        parameterPreview(group, field, value || {}); return;
      }
      const wrapper = node("label", label), input = node("input");
      input.dataset.parameter = name;
      input.type = field.type === "boolean" ? "checkbox" : field.type === "integer" ? "number" : name.startsWith("period_") ? "date" : "text";
      if (field.type === "boolean") input.checked = value === true;
      else input.value = value == null ? "" : String(value);
      if (field.maxLength) input.maxLength = field.maxLength;
      if (field.minimum !== undefined) input.min = field.minimum;
      if (field.maximum !== undefined) input.max = field.maximum;
      wrapper.append(input); parent.append(wrapper);
    });
  }
  function renderMethod() {
    const method = state.methods.find(item => item.method_id === $("method-id").value);
    $("method-parameters").replaceChildren();
    $("method-status").textContent = method ? `${method.purpose} · ${method.available ? "Owner 등록 가능 (실행·Worker 미관측)" : "사용 불가: " + method.reason}` : "목록을 조회하세요.";
    $("method-record").textContent = method ? json(method) : "";
    if (method) parameterPreview($("method-parameters"), method.parameter_schema, method.parameters);
  }
  async function loadMethods() {
    const result = await api("work/methods");
    state.methods = result.methods; $("method-id").replaceChildren();
    for (const method of state.methods) {
      const option = node("option", `${method.method_id}${method.available ? "" : " · 사용 불가"}`);
      option.value = method.method_id; $("method-id").append(option);
    }
    renderMethod();
  }
  $("method-id").addEventListener("change", renderMethod);
  bind("refresh-methods", "click", loadMethods);
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
      if (epoch !== state.epoch || !state.owner) throw staleResponse();
    }
    function requireSameWork(value) {
      if (value.run_revision !== work.run.revision || value.work_spec_digest !== work.spec.spec_digest) {
        clearWork();
        throw Object.assign(new Error("조회 중 Work revision이 바뀌었습니다. 작업을 새로 여세요."), {workChanged: true});
      }
    }
    clearWork(); $("target").textContent = "상세 조회 후 제어 대상을 다시 확인하세요.";
    const work = await api("work/read", {work_run_id: id});
    requireCurrentView(); state.work = work; $("work-detail").hidden = false;
    $("work-id").value = id; observed($("work-observed")); $("work-summary").replaceChildren();
    history.replaceState(null, "", location.pathname + "#work_run_id=" + encodeURIComponent(id));
    facts($("work-summary"), [["작업", work.spec.objective], ["WorkRun", id], ["실행 상태", work.application_state === "unknown" ? "unknown · 마지막 Run 기록 " + work.run.state : work.run.state], ["접수 / 전달 / 적용", `${work.receipt.receipt_state} / ${work.dispatch_state} / ${work.application_state}`], ["배정", work.spec.manager_executor_id || work.spec.officer_executor_id], ["EvalOfficer", work.spec.eval_officer_executor_id], ["현재 GraphRun", work.run.active_graph_run_id || "활성 GraphRun 없음"], ["Revision / control", `${work.run.revision} / ${work.run.control_revision}`], ["누적 사용량", `세그먼트 ${work.run.segments_started} / Profile ${work.run.profile_invocations} / 반복 gap ${work.run.repeated_gap_count}`], ["대기 명령", (work.run.pending_action_ids || []).join(", ") || "없음"], ["미해결 효과", (work.run.unresolved_effect_ids || []).join(", ") || "없음"], ["Runtime", "실시간 Worker/Workflow Query 바인딩 없음 · 상태 미관측"]]);
    $("profiles").textContent = json({current_work_spec: work.spec});
    $("segments").replaceChildren(); $("criteria").replaceChildren(); $("gaps").replaceChildren();
    $("result-text").textContent = ""; $("result-record").textContent = ""; $("result-evidence").replaceChildren();
    let inspection = null;
    try { inspection = await api("work/inspection", {work_run_id: id}); requireSameWork(inspection); }
    catch (error) {
      if (error.workChanged) throw error;
      requireCurrentView();
      $("segments").append(node("p", "계보 조회 사용 불가: " + error.message, "muted"));
    }
    requireCurrentView();
    work.spec.acceptance_criteria.forEach(criterion => {
      const outcome = inspection?.assessment?.criterion_outcomes?.find(o => o.criterion_id === criterion.criterion_id);
      const block = node("article", undefined, "criterion " + (outcome?.outcome || ""));
      block.append(node("strong", criterion.description), node("p", `${criterion.criterion_id} · ${outcome?.outcome || "독립 평가 미제공"}`));
      if (outcome?.evidence_references?.length) block.append(node("pre", json(outcome.evidence_references))); $("criteria").append(block);
    });
    if (inspection) {
      $("profiles").textContent = json({current_work_spec: work.spec, retained_prior_work_specs: inspection.work_spec_history || []});
      disclosure($("criteria"), "독립 평가의 정확한 criterion outcomes · evaluation 기록", {assessment: inspection.assessment, evaluations: inspection.evaluations});
      if (!inspection.segments.length) $("segments").append(node("p", "Owner에 export 가능한 승인 세그먼트가 없습니다. 아래 WorkRun 계보를 확인하세요.", "muted"));
      inspection.segments.forEach(item => {
        const detail = node("details");
        detail.append(node("summary", `세그먼트 ${item.segment.segment_index} · ${item.segment.graph_run_id} · Graph ${item.graph_availability || "unavailable"}`));
        if (item.plan) {
          detail.append(node("p", item.plan.rationale));
          const steps = node("ol", undefined, "graph-steps");
          for (const step of item.plan.steps) steps.append(node("li", `${step.node_id} → ${step.role_id} · ${step.route} · 기준 ${step.criterion_ids.join(", ")}`));
          detail.append(steps);
        }
        disclosure(detail, "실제 immutable Graph · nodes · edges · 배정 · 원래 pins/digests", item);
        $("segments").append(detail);
      });
      renderExecution(inspection);
    }
    $("segments").append(node("pre", json({graph_run_ids: work.run.graph_run_ids, active_graph_run_id: work.run.active_graph_run_id})));
    const remaining = inspection ? inspection.remaining_work : work.remaining_work;
    const outcomes = inspection?.assessment?.criterion_outcomes || [];
    $("gaps").append(node("p", inspection?.assessment ?
      `독립 평가 ${inspection.assessment.completion_state} · 미충족 ${outcomes.filter(item => item.outcome === "unmet").length} · 미실행 ${outcomes.filter(item => item.outcome === "not_run").length}` :
      "독립 평가 미제공 · 구조 검증이나 실행 상태만으로 완료를 판단하지 않습니다."));
    if (remaining) {
      $("gaps").append(node("h3", "남은 작업"));
      for (const gap of remaining.gaps || []) $("gaps").append(node("p", `${gap.criterion_id}: ${gap.summary}`));
      $("gaps").append(node("pre", json(remaining)));
    }
    try {
      requireCurrentView();
      const result = await api("work/result", {work_run_id: id});
      requireCurrentView();
      requireSameWork(result);
      $("result-state").textContent = result.reason || `Owner 결과 · ${result.state} · 독립 평가 ${result.assessment?.completion_state || "없음"}`;
      if (result.truth_evaluated === false) $("result-state").textContent += " · 내용 진위 평가 아님";
      if (result.source_stale) $("result-state").textContent += " · 원문 변경됨: 현재 문서에 적용 가능한 결과가 아닙니다.";
      $("result-text").textContent = result.text || "아직 독립 평가된 결과 본문이 없습니다.";
      $("result-record").textContent = json({state: result.state, source_stale: result.source_stale, source_scope: result.source_scope, assessment: result.assessment, remaining_work: result.remaining_work, continuation_state: result.continuation_state, report: result.report, evidence: result.evidence, evaluations: inspection?.evaluations, result_reference: work.run.result_reference, truncated: result.truncated, truth_evaluated: result.truth_evaluated, apply_allowed: result.apply_allowed});
      renderResultEvidence(result.evidence);
    } catch (error) {
      if (error.workChanged) throw error;
      requireCurrentView();
      $("result-state").textContent = "결과 사용 불가: " + error.message;
    }
    requireCurrentView();
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
  setInterval(() => {
    document.querySelectorAll("[data-observed]").forEach(el => { if (!fresh(Number(el.dataset.observed))) { el.classList.add("stale"); el.textContent = el.dataset.label + " · 오래된 관측값 (60초 경과) · 새로 조회 필요"; } });
    sync();
  }, 1000);
  window.addEventListener("offline", () => { clearPrivate(); notice("네트워크 연결 끊김. 비공개 화면 데이터를 지웠습니다. Owner 새로 조회로 다시 시작하세요. 요청은 자동 재시도하지 않습니다.", "error"); });
  window.addEventListener("pagehide", clearPrivate);
  sync();
  $("refresh-owner").click();
})();
