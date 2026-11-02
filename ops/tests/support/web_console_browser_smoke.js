// Actual-browser DOM regression for the disposable Owner's opt-in inspection gate.
// Network proof comes from actual fixture stdout, not app-realm fetch wrappers or
// ResourceTiming. No private text or fabricated HTTP response is handled.
const markerId = "knowledgeos-view-race-smoke";
const cleanupEvent = "knowledgeos-view-race-cleanup";
const selectors = ["#criteria", "#segments", "#gaps", "#profiles", "#work-summary", "#result-text", "#result-record"];

export async function beginDisconnectedWorkRead(page, workRunId) {
  await page.evaluate(({watched, id, markerName, cleanupName}) => {
    if (document.getElementById("refresh-owner").disabled || document.getElementById("connection-state").textContent.includes("연결 안 됨")) {
      throw new Error("View-race smoke needs an idle connected disposable Owner page");
    }
    if (document.getElementById(markerName)) throw new Error("View-race smoke is already active");
    const marker = document.createElement("div");
    marker.id = markerName;
    marker.hidden = true;
    marker.dataset.workReadRendered = "false";
    marker.dataset.disconnected = "false";
    marker.dataset.privateReappeared = "false";
    document.body.append(marker);
    const disconnect = document.getElementById("disconnect");
    function markDisconnected() { marker.dataset.disconnected = "true"; }
    disconnect.addEventListener("click", markDisconnected, true);
    const summary = document.getElementById("work-summary");
    const badge = document.getElementById("connection-state");
    badge.dataset.kosWorkReadRendered = "false";
    const readObserver = new MutationObserver(() => {
      if (summary.textContent.includes(id)) {
        marker.dataset.workReadRendered = "true";
        badge.dataset.kosWorkReadRendered = "true";
      }
    });
    readObserver.observe(summary, {childList: true, subtree: true, characterData: true});
    const privateObserver = new MutationObserver(() => {
      if (marker.dataset.disconnected === "true" && watched.some(selector => document.querySelector(selector).textContent.trim())) {
        marker.dataset.privateReappeared = "true";
      }
    });
    privateObserver.observe(document.body, {childList: true, subtree: true, characterData: true});
    function cleanup() {
      readObserver.disconnect();
      privateObserver.disconnect();
      disconnect.removeEventListener("click", markDisconnected, true);
      document.removeEventListener(cleanupName, cleanup);
      delete badge.dataset.kosWorkReadRendered;
      marker.remove();
    }
    document.addEventListener(cleanupName, cleanup);
    document.getElementById("work-id").value = id;
  }, {watched: selectors, id: workRunId, markerName: markerId, cleanupName: cleanupEvent});
  await page.click("#open-work-form button[type=submit]");
  return {stage: "read submitted", next: "Wait for actual fixture WEB_CONSOLE_INSPECTION_HELD stdout; then disconnectPendingWorkRead"};
}

export async function disconnectPendingWorkRead(page) {
  // A selector wait depends only on shared DOM, not page.evaluate globals,
  // performance time origins, or the application's JavaScript execution realm.
  await page.waitForSelector("#connection-state[data-kos-work-read-rendered='true']", {timeout: 30000});
  const pending = await page.evaluate(() => document.getElementById("refresh-owner").disabled);
  if (!pending) throw new Error("Work loader already finished; no in-flight disconnect exercised. Use the fixture delay and actual HELD signal");
  await page.click("#disconnect");
  return {stage: "disconnected", next: "Wait for matching actual fixture WEB_CONSOLE_INSPECTION_COMPLETED http_status=200; then finishDisconnectedWorkRead"};
}

export async function finishDisconnectedWorkRead(page, {timeout = 35000} = {}) {
  try {
    await page.waitForSelector("#refresh-owner:not([disabled])", {timeout});
    const observed = await page.evaluate(({watched, markerName}) => {
      const marker = document.getElementById(markerName);
      if (!marker) throw new Error("View-race observation marker is unavailable");
      return {
        workReadRendered: marker.dataset.workReadRendered === "true",
        disconnected: marker.dataset.disconnected === "true",
        privateReappeared: marker.dataset.privateReappeared === "true",
        privateNodesEmpty: watched.every(selector => !document.querySelector(selector).textContent.trim()),
        workHidden: document.getElementById("work-detail").hidden,
        selectorCleared: document.getElementById("work-id").value === "",
      };
    }, {watched: selectors, markerName: markerId});
    if (!observed.workReadRendered || !observed.disconnected || observed.privateReappeared || !observed.privateNodesEmpty || !observed.workHidden || !observed.selectorCleared) {
      throw new Error("Disconnected Work response crossed DOM boundary: " + JSON.stringify(observed));
    }
    return {scope: "actual browser DOM observation only", ...observed,
      networkEvidenceRequired: "Reconcile matching fixture HELD/COMPLETED(http_status=200) and unchanged result_requests through browser loader completion; no network assertion is inferred here"};
  } finally {
    await cleanupDisconnectedWorkRead(page);
  }
}

export async function cleanupDisconnectedWorkRead(page) {
  await page.evaluate(name => document.dispatchEvent(new Event(name)), cleanupEvent);
}

// Convenience for a fixture already launched with an actual bounded delay.
// For cmux prefer the staged functions above with parent-observed stdout gates.
// A successful return proves DOM clearing only, not an unobserved network event.
export async function verifyDisconnectedWorkRead(page, workRunId, {delaySeconds = 10} = {}) {
  if (!Number.isInteger(delaySeconds) || delaySeconds < 1 || delaySeconds > 20) {
    throw new Error("Use the fixture's explicit inspection delay in the range 1..20 seconds");
  }
  await beginDisconnectedWorkRead(page, workRunId);
  try {
    await disconnectPendingWorkRead(page);
    return await finishDisconnectedWorkRead(page, {timeout: (delaySeconds + 15) * 1000});
  } catch (error) {
    await cleanupDisconnectedWorkRead(page);
    throw error;
  }
}


// Run only against a held real native Owner result on the local backend.
export async function verifyStructuredWorkResult(page, workRunId) {
  const expected = await page.evaluate(async ({id}) => {
    if (document.getElementById("connection-state").textContent.includes("연결 안 됨")) {
      throw new Error("Wait for the console's initial Owner status read before verifying its real Work result");
    }
    const response = await fetch("/owner/work/result", {
      method: "POST", cache: "no-store", credentials: "omit", redirect: "error",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({work_run_id: id}),
    });
    if (!response.ok) throw new Error("Actual Owner result read failed with HTTP " + response.status);
    return await response.json();
  }, {id: workRunId});
  if (expected.work_run_id !== workRunId || expected.state !== "completed" ||
      expected.assessment?.completion_state !== "complete" || !expected.text ||
      !expected.evidence || typeof expected.evidence !== "object" || Array.isArray(expected.evidence)) {
    throw new Error("Proof needs a real completed independently assessed result with structured Owner evidence");
  }
  const cleanupName = "knowledgeos-result-evidence-smoke-cleanup";
  await page.evaluate(({id, cleanup}) => {
    if (document.getElementById("refresh-owner").disabled) throw new Error("Wait for the current browser request to finish");
    const badge = document.getElementById("connection-state");
    badge.dataset.kosStructuredResultReady = "false";
    const observer = new MutationObserver(() => {
      if (document.getElementById("work-summary").textContent.includes(id) &&
          document.querySelector("#result-evidence [data-evidence-kind='structured']") &&
          document.getElementById("result-record").textContent.trim()) {
        badge.dataset.kosStructuredResultReady = "true";
      }
    });
    observer.observe(document.getElementById("result-record"), {childList: true, subtree: true, characterData: true});
    observer.observe(document.getElementById("result-evidence"), {childList: true, subtree: true, characterData: true});
    function remove() {
      observer.disconnect();
      delete badge.dataset.kosStructuredResultReady;
      document.removeEventListener(cleanup, remove);
    }
    document.addEventListener(cleanup, remove);
    document.getElementById("work-id").value = id;
  }, {id: workRunId, cleanup: cleanupName});
  try {
    await page.click("#open-work-form button[type=submit]");
    await page.waitForSelector("#connection-state[data-kos-structured-result-ready='true']", {timeout: 30000});
    await page.waitForSelector("#refresh-owner:not([disabled])", {timeout: 30000});
    const rendered = await page.evaluate(() => ({
      state: document.getElementById("result-state").textContent,
      text: document.getElementById("result-text").textContent,
      evidence: JSON.parse(document.querySelector("#result-evidence [data-evidence-kind='structured'] pre").textContent),
      record: JSON.parse(document.getElementById("result-record").textContent),
      citationCards: document.querySelectorAll("#result-evidence [data-evidence-kind='citation']").length,
    }));
    const canonical = value => Array.isArray(value) ? value.map(canonical) :
      value !== null && typeof value === "object" ?
        Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
    const same = (left, right) => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
    if (rendered.state.includes("결과 사용 불가") || rendered.citationCards !== 0 ||
        rendered.text !== expected.text || !same(rendered.evidence, expected.evidence) ||
        !same(rendered.record.evidence, expected.evidence) || !same(rendered.record.report, expected.report) ||
        !same(rendered.record.assessment, expected.assessment) ||
        rendered.record.truth_evaluated !== expected.truth_evaluated ||
        expected.truth_evaluated === false && !rendered.state.includes("내용 진위 평가 아님") ||
        rendered.record.apply_allowed !== expected.apply_allowed) {
      throw new Error("Actual structured result was not preserved by the browser renderer");
    }
    return {
      scope: "actual browser rendering matched independent local Owner result",
      work_run_id: workRunId, evidence_kind: "structured", evidence_keys: Object.keys(expected.evidence),
      exact_artifact_text: true, structured_evidence_preserved: true,
      independent_assessment_preserved: true, fabricated_citation_cards: 0,
      truth_evaluated: expected.truth_evaluated, apply_allowed: expected.apply_allowed,
      claims_excluded: ["all console paths", "other native methods", "Obsidian GUI", "production adoption"],
    };
  } finally {
    await page.evaluate(name => document.dispatchEvent(new Event(name)), cleanupName);
  }
}
