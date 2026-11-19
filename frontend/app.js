"use strict";
// KnowledgeOS browser console. Same-origin only; all server data is untrusted and rendered via textContent.
(() => {
  const $ = id => document.getElementById(id);
  const STALE_MS = 60000;
  const HEALTH_INTERVAL_MS = 15000;
  const POLL_DEADLINE_MS = 60000;
  const TERMINAL = new Set(["completed", "unmet", "failed", "refused", "cancelled", "unknown"]);
  const VIEWS = ["works", "work", "jobs", "unknowns", "attention", "notices", "experience", "roles", "requests"];

  // ---------- small helpers ----------
  const isObj = v => v !== null && typeof v === "object" && !Array.isArray(v);
  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  }
  function pick(obj, ...keys) {
    if (!isObj(obj)) return undefined;
    for (const key of keys) {
      let value = obj;
      for (const part of key.split(".")) value = isObj(value) ? value[part] : undefined;
      if (value !== undefined && value !== null && value !== "") return value;
    }
    return undefined;
  }
  const k = (...keys) => row => pick(row, ...keys);
  function fmt(value) {
    if (value === undefined || value === null || value === "") return "—";
    if (typeof value === "boolean") return value ? "예" : "아니오";
    if (Array.isArray(value)) {
      if (!value.length) return "—";
      return value.every(item => !isObj(item) && !Array.isArray(item)) ? value.join(", ") : JSON.stringify(value);
    }
    if (isObj(value)) return JSON.stringify(value);
    return String(value);
  }
  function time(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("ko-KR", {hour12: false});
  }
  function short(value) {
    if (typeof value !== "string" || !value) return fmt(value);
    const span = el("span", value.length > 16 ? value.slice(0, 12) + "…" : value, "mono");
    span.title = value;
    return span;
  }
  function listOf(data, ...keys) {
    if (Array.isArray(data)) return data;
    if (!isObj(data)) return null;
    for (const key of [...keys, "items"]) {
      const value = data[key];
      if (Array.isArray(value)) return value;
      if (isObj(value)) return Object.entries(value).map(([id, item]) => isObj(item) ? {id, ...item} : {id, value: item});
    }
    const arrays = Object.values(data).filter(Array.isArray);
    return arrays.length === 1 ? arrays[0] : null;
  }
  function newRequestId() {
    if (crypto.randomUUID) return "console." + crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    return "console." + Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
  }

  const STATUS = {
    ok: ["completed", "applied", "satisfied", "ready", "ok", "available", "delivered", "sent", "active", "healthy", "resolved", "succeeded", "success", "considered", "acknowledged"],
    warn: ["received", "admitted", "running", "pending", "paused", "queued", "unmet", "not_run", "stale", "sending", "waiting", "skipped", "blocked", "degraded"],
    bad: ["failed", "refused", "unknown", "unavailable", "cancelled", "error", "expired", "conflict", "not_applied", "notice"],
  };
  const LABEL = {
    received: "접수됨", admitted: "승인됨", running: "실행 중", completed: "완료", unmet: "미충족", failed: "실패",
    refused: "거부", cancelled: "취소됨", unknown: "결과 불명", applied: "적용됨", sending: "전송 중", satisfied: "충족",
    not_run: "미실행", paused: "일시정지", active: "활성", ready: "준비됨", unavailable: "사용 불가", stale: "오래됨",
    pending: "대기", delivered: "전달됨", sent: "전송됨", expired: "만료", resolved: "확인됨", conflict: "충돌",
    considered: "검토함", notice: "알림 확인", blocked: "차단",
    local_only: "로컬 관측", not_evaluated: "미평가",
  };
  function badge(status) {
    if (status === undefined || status === null || status === "") return el("span", "—", "badge s-neutral");
    const value = String(status);
    const group = Object.keys(STATUS).find(name => STATUS[name].includes(value)) || "neutral";
    const span = el("span", LABEL[value] || value, "badge s-" + group);
    span.title = value;
    return span;
  }
  function facts(pairs) {
    const dl = el("dl", undefined, "facts");
    for (const [label, value] of pairs) {
      const dd = el("dd");
      if (value instanceof Node) dd.append(value); else dd.textContent = fmt(value);
      dl.append(el("dt", label), dd);
    }
    return dl;
  }
  function raw(value, label = "원본 JSON") {
    const details = el("details", undefined, "raw");
    details.append(el("summary", label), el("pre", JSON.stringify(value, null, 2)));
    return details;
  }
  function table(rows, cols) {
    const wrap = el("div", undefined, "table-wrap"), tbl = el("table"), head = el("tr"), body = el("tbody");
    cols.forEach(([label]) => head.append(el("th", label)));
    const thead = el("thead"); thead.append(head);
    rows.forEach((row, index) => {
      const tr = el("tr");
      cols.forEach(([, get]) => {
        const td = el("td");
        let value;
        try { value = get(row, index); } catch { value = undefined; }
        if (value instanceof Node) td.append(value); else td.textContent = fmt(value);
        tr.append(td);
      });
      body.append(tr);
    });
    tbl.append(thead, body); wrap.append(tbl);
    return wrap;
  }
  function unavailable(res, what = "") {
    const detail = res && res.status ? ` · HTTP ${res.status}` : res && res.error ? ` · ${res.error}` : "";
    return el("p", `${what ? what + " " : ""}사용 불가 (unavailable)${detail}`, "unavailable");
  }
  const empty = text => el("p", text || "없음", "muted");
  function section(title, ...children) {
    const sec = el("section", undefined, "block");
    sec.append(el("h2", title), ...children.filter(Boolean));
    return sec;
  }
  function iconButton(icon, label, onClick, cls = "") {
    const button = el("button", icon, "icon " + cls);
    button.type = "button"; button.title = label; button.setAttribute("aria-label", label);
    button.addEventListener("click", onClick);
    return button;
  }
  function workLink(id) {
    if (!id) return "—";
    const a = el("a", String(id), "mono");
    a.href = "#work/" + encodeURIComponent(String(id));
    return a;
  }
  function notice(text, kind = "") { $("notice").textContent = text; $("notice").className = "notice " + kind; }

  // ---------- fetch ----------
  async function getJSON(path) {
    try {
      const res = await fetch(path, {cache: "no-store", credentials: "same-origin", redirect: "error", headers: {Accept: "application/json"}});
      let data = null;
      try { data = await res.json(); } catch { data = null; }
      return {ok: res.ok && data !== null, status: res.status, data};
    } catch (error) {
      return {ok: false, status: 0, data: null, error: String(error && error.message || error)};
    }
  }
  const sequence = {};
  async function load(view, path) {
    const n = (sequence[view] = (sequence[view] || 0) + 1);
    const res = await getJSON(path);
    return n === sequence[view] ? res : null;
  }
  function stamp(view) {
    document.querySelectorAll(`[data-observed-for="${view}"]`).forEach(node => {
      node.dataset.at = String(Date.now());
      node.dataset.label = "조회 " + new Date().toLocaleTimeString("ko-KR", {hour12: false});
      node.textContent = node.dataset.label;
      node.classList.remove("stale");
    });
  }
  function replace(id, ...children) { $(id).replaceChildren(...children.filter(Boolean)); }

  // ---------- health ----------
  let healthAt = 0;
  async function loadHealth() {
    const res = await load("health", "/api/health");
    if (!res) return;
    const badgeNode = $("health-badge");
    if (!res.ok || !isObj(res.data)) {
      badgeNode.textContent = "Owner 사용 불가"; badgeNode.className = "badge s-bad";
      replace("health-facts", ...facts([["상태", res.status ? `HTTP ${res.status}` : "연결 불가"]]).childNodes);
      replace("health-deps"); replace("health-blockers");
      return;
    }
    const h = res.data;
    healthAt = Date.now();
    const state = h.healthy === true ? ["정상", "s-ok"] : h.ready === true ? ["준비됨 · 주의", "s-warn"] : h.alive === true ? ["실행 중 · 준비 안 됨", "s-warn"] : ["중단 또는 불명", "s-bad"];
    badgeNode.textContent = state[0]; badgeNode.className = "badge " + state[1];
    const observedAt = Date.parse(h.observed_at);
    const age = Number.isNaN(observedAt) ? null : Math.max(0, Date.now() - observedAt);
    const observed = el("span", time(h.observed_at));
    if (age === null) observed.append(el("span", " · 관측 시각 불명", "warn-text"));
    else if (age > STALE_MS) observed.append(el("span", ` · ${Math.round(age / 1000)}초 전 (오래됨)`, "warn-text"));
    $("health-observed").textContent = "관측 " + time(h.observed_at);
    replace("health-facts", ...facts([
      ["관측 시각", observed], ["alive / ready / healthy", `${fmt(h.alive)} / ${fmt(h.ready)} / ${fmt(h.healthy)}`],
      ["Operation", h.operation_id], ["Instance", h.instance_id], ["Source revision", short(h.source_revision)],
    ]).childNodes);
    const deps = isObj(h.dependencies) ? Object.entries(h.dependencies) : [];
    replace("health-deps", ...(deps.length ? deps.map(([name, dep]) => {
      const status = isObj(dep) ? pick(dep, "status", "state") : dep;
      const chip = el("span", undefined, "chip");
      chip.append(el("span", name + " "), badge(status));
      const at = isObj(dep) ? pick(dep, "observed_at") : undefined;
      if (at) chip.append(el("small", " " + time(at)));
      return chip;
    }) : [el("span", "의존성 정보 없음", "muted")]));
    const blockers = Array.isArray(h.blockers) ? h.blockers : [];
    replace("health-blockers", ...blockers.map(b => el("li", "⛔ " + fmt(b))));
  }

  // ---------- admin requests ----------
  const requests = [];
  function phaseOf(status) {
    if (status === "received" || status === "admitted" || status === "running") return "received";
    if (status === "completed") return "applied";
    if (status === "unknown") return "unknown";
    return "notice"; // refused, failed, unmet, cancelled or anything unexpected: show the Owner's notice
  }
  function applyReceipt(entry, receipt) {
    if (!isObj(receipt)) return;
    entry.status = pick(receipt, "status") || entry.status;
    entry.notice = pick(receipt, "notice") || null;
    entry.work_id = pick(receipt, "work_id") || entry.work_id;
    entry.outcome = receipt.outcome ?? entry.outcome;
    entry.phase = phaseOf(entry.status);
  }
  function describe(entry) {
    const phase = LABEL[entry.phase] || entry.phase;
    const status = entry.status && entry.status !== entry.phase ? ` (${LABEL[entry.status] || entry.status})` : "";
    return `${entry.label}: ${phase}${status}${entry.notice ? " · " + fmt(entry.notice) : ""}`;
  }
  function showEntry(entry) {
    const kind = entry.phase === "applied" ? "success" : entry.phase === "received" || entry.phase === "sending" ? "" : "error";
    notice(describe(entry), kind);
    if (entry.inline && entry.inline.isConnected) {
      entry.inline.replaceChildren(badge(entry.phase));
      if (entry.notice) entry.inline.append(el("small", " " + fmt(entry.notice)));
    }
    if (current === "requests") renderRequests();
  }
  async function poll(entry) {
    const started = Date.now(), deadline = started + POLL_DEADLINE_MS;
    let delay = 400;
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, delay));
      delay = Math.min(delay * 1.5, 3000);
      const res = await getJSON("/api/requests/" + encodeURIComponent(entry.request_id));
      if (res.status === 404 && !entry.receivedByOwner && Date.now() - started > 3000) {
        entry.phase = "notice"; entry.status = "not_received"; entry.notice = "Owner에 접수 기록이 없습니다. 필요하면 새로 요청하세요.";
        showEntry(entry); return;
      }
      if (!res.ok) continue;
      entry.receivedByOwner = true;
      applyReceipt(entry, res.data); showEntry(entry);
      if (TERMINAL.has(entry.status)) return;
    }
    if (entry.phase === "received" || entry.phase === "sending" || entry.phase === "unknown") {
      entry.notice = (entry.notice ? fmt(entry.notice) + " · " : "") + "아직 확정되지 않았습니다. 요청 기록에서 ⟳로 다시 조회하세요.";
      showEntry(entry);
    }
  }
  async function submit(command, params, label, inline, button) {
    const entry = {request_id: newRequestId(), command, params, label, phase: "sending", status: null, notice: null, at: new Date().toISOString(), inline};
    requests.unshift(entry);
    if (requests.length > 100) requests.pop();
    if (button) button.disabled = true;
    showEntry(entry);
    try {
      let res;
      try {
        res = await fetch("/api/requests", {
          method: "POST", cache: "no-store", credentials: "same-origin", redirect: "error",
          headers: {"Content-Type": "application/json", Accept: "application/json"},
          body: JSON.stringify({request_id: entry.request_id, command, params}),
        });
      } catch {
        entry.phase = "unknown";
        entry.notice = "응답 없음: 접수되었을 수 있습니다. 재전송하지 않고 같은 요청 ID로 상태만 조회합니다.";
        showEntry(entry);
        await poll(entry);
        return;
      }
      let data = null;
      try { data = await res.json(); } catch { data = null; }
      if (res.status === 409) {
        entry.phase = "notice"; entry.status = "conflict"; entry.notice = "같은 요청 ID에 다른 내용이 이미 있습니다.";
        showEntry(entry); return;
      }
      if (!res.ok) {
        entry.status = "HTTP " + res.status;
        entry.notice = pick(data, "notice", "error", "message") || null;
        entry.phase = res.status >= 500 ? "unknown" : "notice";
        showEntry(entry);
        if (res.status >= 500) await poll(entry);
        return;
      }
      entry.receivedByOwner = true;
      applyReceipt(entry, data);
      if (!entry.status) entry.phase = "received";
      showEntry(entry);
      if (!TERMINAL.has(entry.status)) await poll(entry);
    } finally {
      if (button && button.isConnected) button.disabled = false;
      loadHealth();
      if (current && current !== "requests" && TERMINAL.has(entry.status)) refresh(current);
    }
  }
  async function requery(entry) {
    const res = await getJSON("/api/requests/" + encodeURIComponent(entry.request_id));
    if (res.ok) { entry.receivedByOwner = true; applyReceipt(entry, res.data); }
    else if (res.status === 404) { entry.phase = "notice"; entry.status = "not_received"; entry.notice = "Owner에 접수 기록이 없습니다."; }
    else entry.notice = "조회 사용 불가" + (res.status ? ` (HTTP ${res.status})` : "");
    showEntry(entry);
  }
  function renderRequests() {
    if (!requests.length) { replace("requests-body", empty("아직 보낸 관리 요청이 없습니다.")); return; }
    replace("requests-body", table(requests, [
      ["시각", r => time(r.at)], ["명령", r => r.command], ["대상", r => JSON.stringify(r.params)],
      ["결과", r => badge(r.phase)], ["Owner 상태", r => r.status], ["알림", r => r.notice],
      ["Work", r => r.work_id ? workLink(r.work_id) : "—"], ["요청 ID", r => short(r.request_id)],
      ["", r => iconButton("⟳", "상태 다시 조회", () => requery(r))],
    ]));
  }

  // ---------- works ----------
  const workStatus = w => pick(w, "status", "state", "run.state", "work_run.state");
  async function loadWorks() {
    const res = await load("works", "/api/works");
    if (!res) return;
    stamp("works");
    const list = res.ok ? listOf(res.data, "works") : null;
    if (!list) { replace("works-body", unavailable(res, "작업 목록")); return; }
    if (!list.length) { replace("works-body", empty("기록된 작업이 없습니다.")); return; }
    replace("works-body", table(list, [
      ["상태", w => badge(workStatus(w))],
      ["Work", w => workLink(pick(w, "work_id", "id"))],
      ["종류", w => pick(w, "target_kind", "workspec.target_kind", "spec.target_kind", "kind")],
      ["명령", w => pick(w, "command", "workspec.command", "spec.command", "capability")],
      ["목표", w => pick(w, "objective", "workspec.objective", "spec.objective", "title")],
      ["요청자", w => pick(w, "actor", "origin")],
      ["접수", w => time(pick(w, "created_at", "received_at", "admitted_at"))],
      ["갱신", w => time(pick(w, "updated_at", "ended_at", "completed_at"))],
    ]));
  }

  let currentWorkId = null;
  async function loadWork() {
    const id = currentWorkId;
    if (!id) { replace("work-body", empty("작업을 선택하세요.")); return; }
    const res = await load("work", "/api/works/" + encodeURIComponent(id));
    if (!res || id !== currentWorkId) return;
    stamp("work");
    if (!res.ok || !isObj(res.data)) { replace("work-body", unavailable(res, "작업 " + id)); return; }
    const w = isObj(res.data.work) ? {...res.data, ...res.data.work} : res.data;
    const workId = pick(w, "work_id", "id") || id;
    const status = workStatus(w);
    const parts = [];

    const summary = section("요약", facts([
      ["Work", el("span", workId, "mono")], ["상태", badge(status)],
      ["종류", pick(w, "target_kind", "workspec.target_kind", "spec.target_kind")],
      ["명령", pick(w, "command", "workspec.command", "spec.command")],
      ["요청자", pick(w, "actor", "origin")], ["요청 ID", pick(w, "request_id")],
      ["접수", time(pick(w, "created_at", "received_at", "admitted_at"))],
      ["갱신", time(pick(w, "updated_at", "ended_at", "completed_at"))],
      ["알림", pick(w, "notice")], ["결과", pick(w, "result", "result_reference", "outputs")],
    ]));
    if (!TERMINAL.has(status)) {
      const inline = el("span", undefined, "inline-status");
      const cancel = iconButton("✕ 취소", "작업 취소 요청", () => {
        if (!confirm(`작업 ${workId} 취소를 요청할까요? 접수는 적용 완료가 아닙니다.`)) return;
        submit("work.cancel", {work_id: workId}, "작업 취소 " + workId, inline, cancel);
      }, "danger");
      const row = el("div", undefined, "row"); row.append(cancel, inline);
      summary.append(row);
    }
    parts.push(summary);

    const spec = pick(w, "workspec", "work_spec", "spec");
    if (isObj(spec)) {
      const criteria = listOf(pick(spec, "acceptance_criteria") || []) || [];
      const sec = section("WorkSpec", facts([
        ["목표", pick(spec, "objective")], ["대상", pick(spec, "target_kind")], ["위임", pick(spec, "delegation_mode")],
        ["Manager / Officer", pick(spec, "manager_executor_id", "officer_executor_id")],
        ["EvalOfficer", pick(spec, "eval_officer_executor_id")], ["허용 profile", pick(spec, "allowed_profiles")],
        ["계속 실행 한도", pick(spec, "continuation_policy")], ["Spec digest", short(pick(spec, "spec_digest"))],
      ]));
      if (criteria.length) sec.append(el("h3", "완료 기준"), table(criteria, [
        ["기준", c => isObj(c) ? pick(c, "criterion_id", "id") : c], ["설명", c => isObj(c) ? pick(c, "description", "text") : "—"],
      ]));
      sec.append(raw(spec));
      parts.push(sec);
    } else parts.push(section("WorkSpec", empty("사용 불가 (unavailable)")));

    const segments = listOf(pick(w, "segments", "graph_segments"));
    const segSec = section("Graph 세그먼트");
    if (!segments) segSec.append(empty("사용 불가 (unavailable)"));
    else if (!segments.length) segSec.append(empty("세그먼트 없음 (Officer 작업이거나 아직 시작 전)"));
    segments?.forEach((seg, index) => {
      const details = el("details", undefined, "segment");
      details.open = index === segments.length - 1;
      const head = el("summary");
      head.append(el("span", `세그먼트 ${fmt(pick(seg, "segment_index", "index") ?? index)} `), badge(pick(seg, "status", "state")),
        el("span", ` · ${fmt(pick(seg, "trigger_kind", "trigger"))} · ${fmt(pick(seg, "manager", "manager_role", "role"))}`, "muted"));
      details.append(head);
      const steps = pick(seg, "graph.steps", "steps", "plan.steps", "graph_spec.steps");
      details.append(facts([
        ["GraphRun", pick(seg, "graph_run_id")], ["대상 기준", pick(seg, "target_criterion_ids", "criteria")],
        ["시작 / 종료", `${time(pick(seg, "started_at"))} / ${time(pick(seg, "ended_at", "completed_at"))}`],
        ["평가 위치", pick(seg, "graph.evaluation", "evaluation_position")], ["거부 사유", pick(seg, "refusal", "rejection", "error")],
      ]));
      if (Array.isArray(steps) && steps.length) {
        const ol = el("ol", undefined, "steps");
        steps.forEach(step => ol.append(el("li", isObj(step) ? `${fmt(pick(step, "teammate", "role", "role_id", "node_id", "id"))}${pick(step, "status") ? " · " + step.status : ""}` : fmt(step))));
        details.append(el("h4", "구성 (Manager가 정한 순서)"), ol);
      }
      const segEval = pick(seg, "evaluation", "evaluations");
      if (segEval) details.append(el("h4", "평가"), renderEvaluations(Array.isArray(segEval) ? segEval : [segEval]));
      const segAssess = pick(seg, "assessment");
      if (segAssess) details.append(el("h4", "판정"), renderAssessment(segAssess));
      const segRemain = pick(seg, "remaining_work");
      if (segRemain) details.append(el("h4", "남은 작업"), renderRemaining(segRemain));
      details.append(raw(seg));
      segSec.append(details);
    });
    parts.push(segSec);

    const invocations = listOf(pick(w, "invocations", "role_invocations"));
    const invSec = section("역할 호출");
    if (!invocations) invSec.append(empty("사용 불가 (unavailable)"));
    else if (!invocations.length) invSec.append(empty("기록된 역할 호출 없음 (정적 역할만 실행되었을 수 있음)"));
    else {
      invSec.append(table(invocations, [
        ["역할", i => pick(i, "role", "role_id", "teammate")], ["세그먼트", i => pick(i, "segment_index", "segment")],
        ["Profile", i => pick(i, "profile", "hermes_profile")], ["모델 route", i => pick(i, "model_route", "route", "model")],
        ["Skill digest", i => short(pick(i, "skill_digest", "skills_digest"))], ["상태", i => badge(pick(i, "status", "outcome", "state"))],
        ["턴", i => pick(i, "turns", "turn_count")], ["시작", i => time(pick(i, "started_at"))], ["종료", i => time(pick(i, "ended_at", "completed_at"))],
        ["진단", i => pick(i, "diagnostics", "error", "notice")],
      ]), raw(invocations));
    }
    parts.push(invSec);

    const evaluations = listOf(pick(w, "evaluations"));
    const evalSec = section("독립 평가 (EvalOfficer)");
    if (!evaluations) evalSec.append(empty("사용 불가 (unavailable)"));
    else if (!evaluations.length) evalSec.append(empty("평가 없음"));
    else evalSec.append(renderEvaluations(evaluations));
    parts.push(evalSec);

    const absent = (...keys) => keys.every(key => !(key in w));
    const assessment = pick(w, "assessment", "completion_assessment", "assessments");
    parts.push(section("판정 (CompletionAssessment)", assessment ? renderAssessment(assessment)
      : absent("assessment", "completion_assessment", "assessments") ? empty("사용 불가 (unavailable)") : empty("판정 없음")));

    const remaining = pick(w, "remaining_work");
    parts.push(section("남은 작업 (RemainingWork)", remaining ? renderRemaining(remaining)
      : absent("remaining_work") ? empty("사용 불가 (unavailable)") : empty("없음")));

    const review = pick(w, "team_review", "teamreview", "review");
    parts.push(section("TeamReview", review ? renderTeamReview(review, workId)
      : absent("team_review", "teamreview", "review") ? empty("사용 불가 (unavailable)") : empty("없음 (Team 작업 완료 후 Manager가 남깁니다)")));

    parts.push(raw(res.data, "작업 전체 원본 JSON"));
    replace("work-body", ...parts);
  }

  function renderEvaluations(list) {
    const box = el("div");
    list.forEach((ev, index) => {
      if (!isObj(ev)) { box.append(el("p", fmt(ev))); return; }
      const card = el("div", undefined, "card");
      card.append(el("h4", `평가 ${fmt(pick(ev, "evaluation_id", "id") ?? index + 1)} · ${fmt(pick(ev, "evaluator_id", "evaluator", "role"))} · 세그먼트 ${fmt(pick(ev, "segment_index", "segment"))}`));
      const judgments = pick(ev, "judgments");
      if (Array.isArray(judgments)) {
        card.append(table(judgments, [
          ["기준", j => pick(j, "criterion", "criterion_id")], ["대상", j => pick(j, "target", "target_path")],
          ["판정", j => badge(pick(j, "verdict", "outcome"))], ["이유", j => pick(j, "reason")],
        ]));
      } else {
        card.append(facts([
          ["충족", pick(ev, "passed_criterion_ids")], ["미충족", pick(ev, "failed_criterion_ids")],
          ["미실행", pick(ev, "not_run_criterion_ids")], ["상태", pick(ev, "status")],
        ]));
      }
      card.append(raw(ev));
      box.append(card);
    });
    return box;
  }
  function renderAssessment(value) {
    const list = Array.isArray(value) ? value : [value];
    const box = el("div");
    list.forEach(a => {
      if (!isObj(a)) { box.append(el("p", fmt(a))); return; }
      const card = el("div", undefined, "card");
      card.append(facts([["결과", badge(pick(a, "completion_state", "state", "status"))], ["판정 시각", time(pick(a, "assessed_at"))], ["판정자", pick(a, "assessor_id")]]));
      const outcomes = pick(a, "criterion_outcomes");
      if (Array.isArray(outcomes) && outcomes.length) card.append(table(outcomes, [
        ["기준", o => pick(o, "criterion_id")], ["결과", o => badge(pick(o, "outcome"))],
        ["평가", o => pick(o, "evaluation_ids")], ["근거 수", o => Array.isArray(o?.evidence_references) ? o.evidence_references.length : "—"],
      ]));
      card.append(raw(a));
      box.append(card);
    });
    return box;
  }
  function renderRemaining(value) {
    const list = Array.isArray(value) ? value : [value];
    const box = el("div");
    list.forEach(r => {
      if (!isObj(r)) { box.append(el("p", fmt(r))); return; }
      const card = el("div", undefined, "card");
      card.append(facts([["미충족 기준", pick(r, "unmet_criterion_ids")], ["미실행 기준", pick(r, "not_run_criterion_ids")], ["Gap signature", short(pick(r, "gap_signature"))]]));
      const gaps = pick(r, "gaps");
      if (Array.isArray(gaps) && gaps.length) {
        const ul = el("ul");
        gaps.forEach(g => ul.append(el("li", isObj(g) ? `${fmt(pick(g, "criterion_id"))}: ${fmt(pick(g, "summary", "text"))}` : fmt(g))));
        card.append(ul);
      }
      card.append(raw(r));
      box.append(card);
    });
    return box;
  }
  function renderTeamReview(review, workId) {
    const box = el("div");
    if (!isObj(review)) { box.append(el("p", fmt(review))); return box; }
    box.append(facts([["Manager", pick(review, "manager", "manager_role", "role")], ["기록", time(pick(review, "recorded_at", "created_at"))]]));
    const ratings = pick(review, "ratings");
    const rows = Array.isArray(ratings) ? ratings : isObj(ratings) ? Object.entries(ratings).map(([teammate, r]) => isObj(r) ? {teammate, ...r} : {teammate, rating: r}) : [];
    box.append(el("h3", "teammate 평가"));
    box.append(rows.length ? table(rows, [
      ["teammate", r => pick(r, "teammate", "role", "id")],
      ["점수", r => { const n = Number(pick(r, "rating")); return Number.isFinite(n) ? `${"★".repeat(Math.max(0, Math.min(5, Math.round(n))))} ${n}/5` : fmt(pick(r, "rating")); }],
      ["메모", r => pick(r, "note")],
    ]) : empty());
    box.append(el("h3", "개선 제안"));
    const proposals = Array.isArray(review.proposals) ? review.proposals : [];
    if (!proposals.length) box.append(empty());
    else {
      const ul = el("ul", undefined, "proposals");
      proposals.forEach((p, index) => {
        const li = el("li");
        const text = isObj(p) ? pick(p, "text") : p;
        const considered = isObj(p) && (p.considered === true || !!pick(p, "considered_at") || pick(p, "status", "state") === "considered");
        const proposalIndex = isObj(p) && Number.isInteger(p.index) ? p.index : index;
        li.append(el("span", fmt(text)), el("span", " "), considered ? badge("considered") : badge("pending"));
        if (isObj(p) && p.considered_at) li.append(el("small", " " + time(p.considered_at), "muted"));
        if (!considered) {
          const inline = el("span", undefined, "inline-status");
          const button = iconButton("✓", "검토함으로 표시", () =>
            submit("teamreview.considered", {work_id: workId, proposal_index: proposalIndex}, `제안 ${proposalIndex + 1} 검토함`, inline, button));
          li.append(button, inline);
        }
        ul.append(li);
      });
      box.append(ul);
    }
    box.append(raw(review));
    return box;
  }

  // ---------- jobs ----------
  async function loadJobs() {
    const res = await load("jobs", "/api/jobs");
    if (!res) return;
    stamp("jobs");
    if (!res.ok) { replace("jobs-body", unavailable(res, "예약 작업")); return; }
    const data = res.data;
    const jobs = (Array.isArray(data) ? data : isObj(data) ? listOf({j: data.jobs ?? data.items}, "j") : null) || [];
    let runs = isObj(data) ? listOf({r: data.runs ?? data.job_runs}, "r") : null;
    if (!runs) runs = jobs.filter(isObj).flatMap(job => (Array.isArray(job.runs) ? job.runs : Array.isArray(job.recent_runs) ? job.recent_runs : [])
      .map(run => isObj(run) ? {job: pick(job, "job", "name", "id"), ...run} : {job: pick(job, "job", "name", "id"), status: run}));
    const parts = [];
    if (!Array.isArray(data) && !isObj(data)) parts.push(unavailable(res, "예약 작업"));
    parts.push(section("예약 작업", jobs.length ? table(jobs, [
      ["작업", j => pick(j, "job", "name", "id")], ["일정", j => pick(j, "schedule", "cron", "when")],
      ["상태", j => badge(jobPaused(j) === true ? "paused" : jobPaused(j) === false ? "active" : pick(j, "state", "status"))],
      ["마지막 실행", j => { const last = pick(j, "last_run"); return isObj(last) ? `${fmt(pick(last, "status", "outcome"))} · ${time(pick(last, "started_at", "at"))}` : time(last); }],
      ["다음 실행", j => time(pick(j, "next_run", "next_run_at"))],
      ["제어", j => jobControls(j)],
    ]) : empty("등록된 예약 작업 없음")));
    parts.push(section("실행 기록", runs.length ? table(runs, [
      ["작업", r => pick(r, "job", "name")], ["실행", r => short(pick(r, "run_id", "id", "request_id"))],
      ["시작", r => time(pick(r, "started_at", "at", "created_at"))], ["종료", r => time(pick(r, "ended_at", "completed_at"))],
      ["결과", r => badge(pick(r, "status", "outcome", "state"))], ["Work", r => workLink(pick(r, "work_id"))],
      ["대상 수", r => pick(r, "count", "targets", "changed")], ["알림", r => pick(r, "notice", "error", "summary", "reason")],
    ]) : empty("실행 기록 없음")));
    if (data !== null) parts.push(raw(data));
    replace("jobs-body", ...parts);
  }
  function jobPaused(job) {
    if (typeof job.paused === "boolean") return job.paused;
    const state = pick(job, "state", "status");
    if (state === "paused") return true;
    if (state === "active" || state === "enabled") return false;
    return undefined;
  }
  function jobControls(job) {
    const name = pick(job, "job", "name", "id");
    const box = el("span", undefined, "row");
    if (!name) return "—";
    const inline = el("span", undefined, "inline-status");
    const paused = jobPaused(job);
    if (paused !== true) {
      const pause = iconButton("⏸", `${name} 일시정지`, () => submit("job.pause", {job: name}, `예약 ${name} 일시정지`, inline, pause));
      box.append(pause);
    }
    if (paused !== false) {
      const resume = iconButton("▶", `${name} 재개`, () => submit("job.resume", {job: name}, `예약 ${name} 재개`, inline, resume));
      box.append(resume);
    }
    box.append(inline);
    return box;
  }

  // ---------- unknown outcomes ----------
  const RESOLUTIONS = [["applied", "적용됨으로 확인"], ["not_applied", "적용 안 됨으로 확인"]];
  async function loadUnknowns() {
    const res = await load("unknowns", "/api/unknowns");
    if (!res) return;
    stamp("unknowns");
    const list = res.ok ? listOf(res.data, "unknowns") : null;
    if (!list) { replace("unknowns-body", unavailable(res, "결과 불명 목록")); return; }
    if (!list.length) { replace("unknowns-body", empty("결과 불명 항목 없음")); return; }
    replace("unknowns-body", ...list.map(item => {
      const card = el("div", undefined, "card");
      const id = pick(item, "unknown_id", "id", "intent_id");
      const resolved = pick(item, "resolution", "resolved_at") !== undefined || pick(item, "status", "state") === "resolved";
      const head = el("h3");
      head.append(badge(resolved ? "resolved" : "unknown"), el("span", " " + fmt(pick(item, "kind", "effect", "category", "command")) + " · "), el("span", fmt(id), "mono"));
      card.append(head, facts([
        ["내용", pick(item, "summary", "description", "title", "intent.kind")], ["대상", pick(item, "path", "target", "intent.path", "intent.rel")],
        ["Work", workLink(pick(item, "work_id"))], ["요청", pick(item, "request_id")],
        ["발생", time(pick(item, "created_at", "at", "recorded_at"))], ["확인 기록", pick(item, "resolution")],
      ]));
      if (id && !resolved) {
        const row = el("div", undefined, "row");
        const select = el("select");
        select.setAttribute("aria-label", "확인 결과");
        RESOLUTIONS.forEach(([value, label]) => { const option = el("option", label); option.value = value; select.append(option); });
        const inline = el("span", undefined, "inline-status");
        const button = iconButton("⚖ 기록", "확인 결과 기록", () => {
          const label = select.options[select.selectedIndex].textContent;
          if (!confirm(`결과 불명 ${id} 을(를) "${label}"(으)로 기록할까요?\n실제 상태를 직접 확인한 경우에만 기록하세요. 효과는 다시 실행되지 않습니다.`)) return;
          submit("outcome.reconcile", {unknown_id: id, resolution: select.value}, `결과 불명 ${id} 확인 기록`, inline, button);
        });
        row.append(select, button, inline);
        card.append(row);
      }
      card.append(raw(item));
      return card;
    }));
  }

  // ---------- attention / notices / experience ----------
  async function loadAttention() {
    const res = await load("attention", "/api/attention");
    if (!res) return;
    stamp("attention");
    const list = res.ok ? listOf(res.data, "attention", "items") : null;
    if (!list) { replace("attention-body", unavailable(res, "주의 항목")); return; }
    if (!list.length) { replace("attention-body", empty("주의 항목 없음")); return; }
    replace("attention-body", table(list, [
      ["종류", a => pick(a, "kind", "category", "type")], ["제목", a => pick(a, "title", "name")],
      ["경로", a => pick(a, "paths", "path", "target")], ["세부", a => pick(a, "detail", "reason", "summary", "message")],
      ["Work", a => pick(a, "work_id") ? workLink(a.work_id) : "—"], ["시각", a => time(pick(a, "since", "created_at", "at", "observed_at"))],
    ]), raw(res.data));
  }
  async function loadNotices() {
    const res = await load("notices", "/api/notices");
    if (!res) return;
    stamp("notices");
    const list = res.ok ? listOf(res.data, "notices") : null;
    if (!list) { replace("notices-body", unavailable(res, "알림 기록")); return; }
    if (!list.length) { replace("notices-body", empty("알림 없음")); return; }
    replace("notices-body", table(list, [
      ["시각", n => time(pick(n, "created_at", "at", "recorded_at"))], ["종류", n => pick(n, "kind", "type", "category")],
      ["제목", n => pick(n, "title", "subject", "summary")], ["전달", n => badge(pick(n, "delivery_state", "status", "outcome", "state"))],
      ["항목", n => pick(n, "item_id", "dedupe_key", "item_ids")], ["내용", n => pick(n, "text", "lines", "counts")],
      ["ID", n => short(pick(n, "notice_id", "id"))],
    ]), raw(res.data));
  }
  async function loadExperience() {
    const res = await load("experience", "/api/experience");
    if (!res) return;
    stamp("experience");
    const list = res.ok ? listOf(res.data, "experience", "outbox", "candidates") : null;
    if (!list) { replace("experience-body", unavailable(res, "Experience outbox")); return; }
    if (!list.length) { replace("experience-body", empty("Experience 기록 없음")); return; }
    const localOnly = e => pick(e, "delivery") === "local_only";
    replace("experience-body", table(list, [
      ["기록", e => el("span", fmt(pick(e, "experience_id", "candidate_id", "event_id", "outbox_id", "id")), "mono")],
      ["유형", e => localOnly(e) ? "SourceEvent · Candidate 없음" : "Candidate"],
      ["Signal", e => pick(e, "signal", "kind")],
      ["분류", e => pick(e, "source_event.category", "category", "candidate.category")],
      ["평가", e => badge(pick(e, "source_event.evaluation_state", "candidate.evaluation_state", "evaluation_state"))],
      ["전달", e => badge(pick(e, "delivery", "delivery_state", "outbox.delivery_state", "status", "state"))],
      ["시도", e => localOnly(e) ? "해당 없음" : pick(e, "outbox.attempt_count", "attempt_count")],
      ["생성", e => time(pick(e, "created_at", "source_event.occurred_at", "occurred_at"))],
      ["만료", e => localOnly(e) ? "해당 없음" : time(pick(e, "outbox.expires_at", "expires_at", "outbox.retention_until", "retention_until"))],
      ["오류", e => localOnly(e) ? "해당 없음" : pick(e, "outbox.last_error_code", "last_error_code", "error")],
      ["출처", e => pick(e, "source", "source_event_id", "event_id", "work_id")],
    ]), raw(res.data));
  }

  // ---------- roles ----------
  async function loadRoles() {
    const res = await load("roles", "/api/roles");
    if (!res) return;
    stamp("roles");
    let list = res.ok ? listOf(res.data, "roles") : null;
    if (!list && isObj(res.data) && Object.values(res.data).length && Object.values(res.data).every(isObj)) {
      list = Object.entries(res.data).map(([role, item]) => ({role, ...item}));
    }
    if (!list) { replace("roles-body", unavailable(res, "역할 정보")); return; }
    if (!list.length) { replace("roles-body", empty("역할 없음")); return; }
    const ledgers = isObj(res.data) && isObj(res.data.ledgers) ? res.data.ledgers : {};
    replace("roles-body", ...list.map(role => {
      const name = pick(role, "role", "role_id", "id", "name");
      const profile = pick(role, "profile", "hermes_profile");
      const route = pick(role, "model_route", "route", "model");
      const details = el("details", undefined, "role");
      const head = el("summary");
      head.append(el("strong", fmt(name)), el("span", ` · ${fmt(profile)} · ${fmt(route)}`, "muted"));
      if (role.local === true) head.append(el("span", " 🔒 로컬", "muted"));
      details.append(head, facts([
        ["Profile", profile], ["모델 route", route], ["로컬 route", role.local], ["도구", pick(role, "tools", "tool_grants")],
        ["Skill digest", short(pick(role, "skill_digest", "skills_digest"))], ["SOUL digest", short(pick(role, "soul_digest"))],
      ]));
      const ledgerSource = pick(role, "ledger", "learning_ledger", "skill_ledger", "learning") ?? ledgers[name] ?? ledgers[profile];
      const ledger = ledgerSource === undefined ? null : listOf(Array.isArray(ledgerSource) ? ledgerSource : isObj(ledgerSource) ? ledgerSource : [], "entries", "changes");
      details.append(el("h3", "학습 기록"));
      if (ledger === null) details.append(empty("사용 불가 (unavailable)"));
      else if (!ledger.length) details.append(empty("기록 없음"));
      else details.append(table(ledger, [
        ["시각", l => time(pick(l, "at", "recorded_at", "time", "created_at"))], ["Skill", l => pick(l, "skill", "name", "path")],
        ["변경", l => pick(l, "action", "change", "kind", "event")], ["Digest", l => short(pick(l, "digest", "skill_digest", "sha256"))],
        ["요약", l => pick(l, "summary", "note", "reason")],
      ]));
      details.append(raw(role));
      return details;
    }));
  }

  // ---------- routing ----------
  const loaders = {works: loadWorks, work: loadWork, jobs: loadJobs, unknowns: loadUnknowns, attention: loadAttention,
    notices: loadNotices, experience: loadExperience, roles: loadRoles, requests: async () => renderRequests()};
  let current = null;
  function refresh(view) {
    const loader = loaders[view];
    if (!loader) return;
    loader().catch(error => notice("화면 표시 실패: " + (error && error.message || error), "error"));
  }
  function route() {
    let hash = "";
    try { hash = decodeURIComponent(location.hash.slice(1)); } catch { hash = ""; }
    let view = VIEWS.includes(hash) && hash !== "work" ? hash : "works";
    if (hash.startsWith("work/") && hash.length > 5) { view = "work"; currentWorkId = hash.slice(5); }
    current = view;
    document.querySelectorAll("[data-view]").forEach(node => { node.hidden = node.dataset.view !== view; });
    const tab = view === "work" ? "works" : view;
    document.querySelectorAll("[data-tab]").forEach(a => {
      if (a.dataset.tab === tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    if (view === "work") $("work-body").replaceChildren(el("p", "불러오는 중…", "muted"));
    refresh(view);
  }
  document.querySelectorAll("[data-refresh]").forEach(button => button.addEventListener("click", () => refresh(button.dataset.refresh)));
  $("health-refresh").addEventListener("click", () => loadHealth());
  window.addEventListener("hashchange", route);
  setInterval(() => {
    const now = Date.now();
    document.querySelectorAll("[data-observed-for]").forEach(node => {
      if (node.dataset.at && now - Number(node.dataset.at) > STALE_MS && !node.classList.contains("stale")) {
        node.classList.add("stale");
        node.textContent = node.dataset.label + " · 오래됨";
      }
    });
    const healthNode = $("health-observed");
    healthNode.classList.toggle("stale", !healthAt || now - healthAt > STALE_MS);
  }, 1000);
  setInterval(loadHealth, HEALTH_INTERVAL_MS);
  loadHealth();
  route();
})();
