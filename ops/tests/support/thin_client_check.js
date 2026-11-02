"use strict";

// Run through `make thin-client-check`. No GUI, browser, installation or live roots.
// --endpoint is opt-in and READ-ONLY: use web_console_smoke.py's disposable Owner.
const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const { parseArgs } = require("node:util");
const os = require("node:os");
const path = require("node:path");
const { host, deferred, cleanupHosts } = require("./thin_client_host.js");
const { values } = parseArgs({ options: { endpoint: { type: "string" } } });
const realEndpoint = values.endpoint || process.env.KNOWLEDGEOS_THIN_CLIENT_ENDPOINT;
const tests = [];
const test = (name, run) => tests.push({ name, run });
const flush = async () => { for (let index = 0; index < 4; index++) await new Promise(resolve => setImmediate(resolve)); };
const digest = text => createHash("sha256").update(text).digest("hex");
const ref = { logical_id: "proposal.synthetic", content_digest: "sha256:" + "a".repeat(64), source_revision: "a".repeat(64) };
const readRoutes = new Set(["owner/status", "mutation/receipt", "search/query", "work/list", "work/read", "work/inspection", "work/result", "work/methods", "proposal/list", "proposal/read", "daily/list", "daily/read", "work/control/target", "work/control/command", "work/steering/view", "work/steering/export"]);
const mutations = h => h.calls.filter(call => !readRoutes.has(call.route));
const buttons = view => view.contentEl.querySelectorAll("button");
const button = (view, text) => buttons(view).find(element => element.getAttribute("title") === text || element.getAttribute("aria-label") === text || element.textContent === text);

function payload(route, marker = "Synthetic result") {
  const work = { work_run_id: "workrun.synthetic", template_id: "synthetic", team_id: marker, receipt_state: "accepted", dispatch_state: "pending", run_state: "ACCEPTED", evaluation_state: "pending", source_stale: false };
  switch (route) {
    case "owner/status": return { owner_operation_id: "knowledgeos", generation: 1, revision: 1, read_only: false, observed_at: "2026-11-01T00:00:00Z", runtime_observation: "unavailable", bindings: { director_intake: true, structured_templates: true, run_control: true, control_write: true, safe_steering: true, proposal_review: true, proposal_decide: true, proposal_apply: true, daily_review: true, daily_decide: true } };
    case "search/query": return { mode: "lexical", results: [{ path: "Notes/Beta.md", title: marker, excerpt: marker, reason: "Synthetic lexical match", resource_reference: { source_revision: digest("# Beta\n\nSynthetic β evidence.\n") } }] };
    case "work/list": return { works: [work] };
    case "work/read": return { run: { ...work, state: "ACCEPTED" }, record: work, spec: { objective: marker }, receipt: {} };
    case "work/inspection": return { work_run_id: work.work_run_id, observed_at: "2026-11-01T00:00:00Z", segments: [], assessment: null, evaluations: [], source_scope: { path: "Notes/Alpha.md", content_sha256: "a".repeat(64) }, runtime_observation: "unavailable" };
    case "work/result": return { ...work, state: "COMPLETED", text: marker, report: null, evidence: null, assessment: null, truth_evaluated: false };
    case "proposal/list": return { proposals: [{ action: marker, proposal_reference: ref, apply_allowed: true }] };
    case "proposal/read": return { proposal_reference: ref, text: marker, source_path: "Notes/Alpha.md", source_digest: "sha256:" + "a".repeat(64), source_reference: { content_digest: "sha256:" + "a".repeat(64) }, source_text: marker, source_stale: false, source_truncated: false, target_path: "Notes/Alpha.md", diff: marker, field_evidence: [], apply_allowed: true, decision: null, state: "completed" };
    case "daily/list": return { pending: [{ effect_id: "daily.synthetic", path: marker, review_status: "pending", fields: { tags: ["synthetic"] } }] };
    case "daily/read": return { effect_id: "daily.synthetic", path: marker, review_status: "pending", body_excerpt: marker, before_digest: "a".repeat(64), current_digest: "b".repeat(64), original_fields: {}, added_fields: { tags: ["synthetic"] }, current_fields: { tags: ["synthetic"] }, evidence: [] };
    case "methods/read": case "work/methods": return { methods: [], templates: [] };
    default: throw new Error(`Unconfigured synthetic request: ${route}`);
  }
}

async function viewFixture() {
  const h = host();
  h.respond(async call => h.response(payload(call.route)));
  const main = h.load(h.clientPath);
  assert.equal(typeof main.WorkReviewSidebar, "function", "Export WorkReviewSidebar for consumer-visible headless checks");
  const plugin = new main();
  plugin.settings = { ownerEndpoint: "http://127.0.0.1:17902/owner", lastWorkRunId: "" };
  plugin.saveSettings = async () => {};
  const view = new main.WorkReviewSidebar({}, plugin);
  await view.onOpen();
  await view.refreshOwner();
  await flush();
  return { h, main, plugin, view };
}

function backend(state = "owner_connected", changes = {}) {
  return { version: 1, operation: "knowledgeos", state, owner_endpoint: "http://127.0.0.1:17902/owner",
    owner_http: state === "owner_connected" ? "reachable" : "unreachable", runtime_observation: "unavailable",
    error: ["owner_connected", "starting"].includes(state) ? null : { code: "SYNTHETIC_UNREACHABLE", message: "Synthetic Owner unavailable" }, ...changes };
}
function backendReply(callback, result) {
  const error = ["owner_connected", "starting"].includes(result.state) ? null : Object.assign(new Error("Synthetic helper exit"), { code: 1 });
  callback(error, JSON.stringify(result), "");
}

test("only the explicit backend button starts; loading, tabs, targets and Owner refresh never invoke the helper", async () => {
  const { h, view } = await viewFixture();
  await view.openSection("search");
  await view.openSection("pending");
  view.setTarget("Notes/Beta.md"); await flush();
  await view.refreshOwner();
  assert.equal(h.helperCalls.length, 0);
  h.exec((_file, args, _options, callback) => backendReply(callback, backend(args[0] === "status" ? "unreachable" : "owner_connected")));
  await button(view, "로컬 백엔드 시작").fire("click");
  assert.deepEqual(h.helperCalls.map(call => call.args), [["status", "--json"], ["start", "--json"]]);
  for (const call of h.helperCalls) {
    assert.equal(call.file, path.join(os.homedir(), ".local", "bin", "knowledgeos-local"));
    assert.deepEqual(call.options, { shell: false, encoding: "utf8", timeout: 65000, maxBuffer: 65536 });
  }
  assert.ok(view.owner);
  assert.match(view.contentEl.textContent, /미관측/);
  assert.equal(mutations(h).length, 0, "Starting cannot admit Work, approve or apply");
  const beforeClose = h.helperCalls.length;
  await view.onClose();
  assert.equal(h.helperCalls.length, beforeClose, "Closing never stops/kills the backend");
  h.cleanup();
});

test("an already reachable helper only reconnects through read-only Owner status", async () => {
  const { h, view } = await viewFixture();
  h.exec((_file, _args, _options, callback) => backendReply(callback, backend()));
  view.disconnect();
  await button(view, "로컬 백엔드 시작").fire("click");
  assert.deepEqual(h.helperCalls.map(call => call.args[0]), ["status"]);
  assert.ok(view.owner);
  assert.equal(mutations(h).length, 0);
  await view.onClose(); h.cleanup();
});

test("helper status endpoint mismatch refuses start before any personal runtime mutation", async () => {
  const { h, view } = await viewFixture();
  h.exec((_file, _args, _options, callback) => backendReply(callback, backend("unreachable", { owner_endpoint: "http://127.0.0.1:17900/owner" })));
  const reads = h.calls.length;
  await button(view, "로컬 백엔드 시작").fire("click");
  assert.deepEqual(h.helperCalls.map(call => call.args[0]), ["status"]);
  assert.equal(h.calls.length, reads, "Mismatch cannot connect to either target");
  assert.match(view.contentEl.textContent, /주소가 다릅니다/);
  await view.onClose(); h.cleanup();
});

for (const failure of ["missing", "timeout", "execution", "blocked", "failed"]) {
  test(`backend ${failure} is visible and never retries start`, async () => {
    const { h, view } = await viewFixture();
    h.exec((_file, _args, _options, callback) => {
      if (["blocked", "failed"].includes(failure)) return backendReply(callback, backend(failure));
      const error = Object.assign(new Error("Synthetic helper failure"), failure === "missing" ? { code: "ENOENT" } :
        failure === "timeout" ? { code: "ETIMEDOUT", killed: true } : { code: "EACCES" });
      callback(error, "", "");
    });
    await button(view, "로컬 백엔드 시작").fire("click");
    assert.equal(h.helperCalls.length, 1);
    assert.match(view.statusEl.textContent, failure === "missing" ? /설치/ : failure === "timeout" ? /만료/ : failure === "execution" ? /실행 실패/ : /SYNTHETIC_UNREACHABLE/);
    assert.equal(view.uncertain, false, "Host startup failure is not an unknown domain mutation");
    await view.onClose(); h.cleanup();
  });
}

test("start failures remain explicit without retrying or treating helper state as Owner authority", async () => {
  const { h, view } = await viewFixture();
  view.disconnect();
  h.exec((_file, args, _options, callback) => backendReply(callback, backend(args[0] === "status" ? "unreachable" : "failed")));
  await button(view, "로컬 백엔드 시작").fire("click");
  assert.deepEqual(h.helperCalls.map(call => call.args[0]), ["status", "start"]);
  assert.equal(view.owner, null);
  assert.match(view.statusEl.textContent, /SYNTHETIC_UNREACHABLE/);
  await view.onClose(); h.cleanup();
});

test("backend responses reject oversized/malformed/wrong-operation/runtime claims and inconsistent exits", async () => {
  const { h, main } = await viewFixture();
  const invalid = ["x".repeat(65537), "{", JSON.stringify(backend("owner_connected", { version: 2 })),
    JSON.stringify(backend("owner_connected", { operation: "other" })),
    JSON.stringify(backend("owner_connected", { runtime_observation: "healthy" })),
    JSON.stringify(backend("owner_connected", { owner_http: "unreachable" })),
    JSON.stringify(backend("owner_connected", { owner_endpoint: null })),
    JSON.stringify(backend("owner_connected", { extra: "unexpected" })),
    JSON.stringify(backend("unreachable", { error: { code: "INVALID" } })),
    JSON.stringify(backend("owner_connected", { owner_endpoint: "http://localhost:17902/owner" }))];
  for (const stdout of invalid) {
    h.exec((_file, _args, _options, callback) => callback(null, stdout, ""));
    await assert.rejects(main.localBackend("status"), /규약|일치/);
  }
  for (const [error, result] of [[null, backend("unreachable")], [Object.assign(new Error("exit"), { code: 1 }), backend()]]) {
    h.exec((_file, _args, _options, callback) => callback(error, JSON.stringify(result), ""));
    await assert.rejects(main.localBackend("status"), /일치/);
  }
  const count = h.helperCalls.length;
  await assert.rejects(main.localBackend("restart"));
  assert.equal(h.helperCalls.length, count);
  h.cleanup();
});

test("duplicate clicks and multiple sidebar views share one pending backend start", async () => {
  const { h, main, plugin, view } = await viewFixture();
  const other = new main.WorkReviewSidebar({}, plugin);
  await other.onOpen();
  const held = deferred();
  h.exec((_file, args, _options, callback) => {
    if (args[0] === "status") held.promise.then(() => backendReply(callback, backend("unreachable")));
    else backendReply(callback, backend());
  });
  const starting = button(view, "로컬 백엔드 시작").fire("click");
  await button(view, "로컬 백엔드 시작").fire("click");
  await button(other, "로컬 백엔드 시작").fire("click");
  assert.equal(h.helperCalls.length, 1);
  held.resolve(); await starting;
  assert.deepEqual(h.helperCalls.map(call => call.args[0]), ["status", "start"]);
  await other.onClose(); await view.onClose(); h.cleanup();
});

for (const phase of ["status", "start"]) for (const transition of ["close", "disconnect", "endpoint", "note", "tab"]) {
  test(`late helper ${phase} cannot reconnect after ${transition}`, async () => {
    const { h, plugin, view } = await viewFixture();
    const held = deferred(), observed = deferred();
    h.exec((_file, args, _options, callback) => {
      if (args[0] === phase) {
        observed.resolve();
        held.promise.then(() => backendReply(callback, backend(phase === "status" ? "unreachable" : "owner_connected")));
      } else backendReply(callback, backend("unreachable"));
    });
    const pending = button(view, "로컬 백엔드 시작").fire("click");
    await observed.promise;
    if (transition === "close") await view.onClose();
    else if (transition === "disconnect") view.disconnect();
    else if (transition === "endpoint") plugin.settings.ownerEndpoint = "http://127.0.0.1:17903/owner";
    else if (transition === "note") view.setTarget("Notes/Beta.md");
    else await view.openSection("library");
    const before = h.calls.length;
    held.resolve(); await pending; await flush();
    assert.equal(h.calls.length, before, "Late helper cannot issue Owner reads at the old or new endpoint");
    assert.equal(h.helperCalls.filter(call => call.args[0] === "start").length, phase === "start" ? 1 : 0);
    if (transition === "endpoint") assert.equal(view.owner, null);
    if (transition !== "close") await view.onClose();
    h.cleanup();
  });
}

test("a target change during the post-start Owner HTTP read cannot restore stale authority", async () => {
  const { h, plugin, view } = await viewFixture();
  const held = deferred(), observed = deferred();
  h.exec((_file, _args, _options, callback) => backendReply(callback, backend()));
  h.respond(async call => {
    if (call.route === "owner/status") { observed.resolve(); await held.promise; }
    return h.response(payload(call.route));
  });
  const pending = button(view, "로컬 백엔드 시작").fire("click");
  await observed.promise;
  plugin.settings.ownerEndpoint = "http://127.0.0.1:17903/owner";
  held.resolve(); await pending;
  assert.equal(view.owner, null);
  assert.equal(view.writeAllowed("structured_templates"), false);
  assert.equal(mutations(h).length, 0);
  await view.onClose(); h.cleanup();
});

test("starting receives only bounded read-only polling, without a second start", async () => {
  const { h, view } = await viewFixture();
  view.disconnect();
  h.exec((_file, args, _options, callback) => backendReply(callback, backend(args[0] === "start" ? "starting" : "unreachable")));
  await button(view, "로컬 백엔드 시작").fire("click");
  assert.deepEqual(h.helperCalls.map(call => call.args[0]), ["status", "start", "status", "status", "status"]);
  assert.equal(view.owner, null);
  assert.match(view.statusEl.textContent, /아직 확인되지/);
  assert.equal(mutations(h).length, 0);
  await view.onClose(); h.cleanup();
});

test("starting can become connected through read-only status without claiming runtime readiness", async () => {
  const { h, view } = await viewFixture();
  let statusCount = 0;
  h.exec((_file, args, _options, callback) => backendReply(callback, backend(args[0] === "start" ? "starting" : ++statusCount === 1 ? "unreachable" : "owner_connected")));
  await button(view, "로컬 백엔드 시작").fire("click");
  assert.deepEqual(h.helperCalls.map(call => call.args[0]), ["status", "start", "status"]);
  assert.ok(view.owner);
  assert.match(view.contentEl.textContent, /미관측/);
  await view.onClose(); h.cleanup();
});
test("endpoint permits only explicit trusted loopback /owner and denies before I/O", async () => {
  const h = host(), main = h.load(h.clientPath);
  assert.equal(main.endpoint("http://127.0.0.1:17902/owner"), "http://127.0.0.1:17902/owner");
  for (const denied of ["https://127.0.0.1:17902/owner", "http://localhost:17902/owner", "http://[::1]:17902/owner", "http://example.com:17902/owner", "http://127.0.0.1/owner", "http://user:secret@127.0.0.1:17902/owner", "http://127.0.0.1:17902/owner?token=secret", "http://127.0.0.1:17902/owner#secret", "http://127.0.0.1:17902/owner/", "http://127.0.0.1:17902/other"]) {
    assert.throws(() => main.endpoint(denied));
    await assert.rejects(main.ownerCall({ ownerEndpoint: denied }, "owner/status", {}));
  }
  assert.equal(h.calls.length, 0);
});

test("ownerCall marks ambiguous outcomes without retry and distinguishes known denial/preflight failures", async () => {
  const h = host(), main = h.load(h.clientPath);
  for (const response of [() => { throw new Error("synthetic lost response"); }, () => ({ status: 503, json: { message: "synthetic unavailable" } }), () => ({ status: 200, json: null })]) {
    h.respond(response);
    const before = h.calls.length;
    await assert.rejects(main.ownerCall({ ownerEndpoint: "http://127.0.0.1:17902/owner" }, "work/intake-template", {}), error => error.uncertain === true);
    assert.equal(h.calls.length, before + 1, "Never silently retry a possibly committed mutation");
  }
  h.respond(async () => ({ status: 409, json: { status: "FAIL", errors: [{ code: "RESOURCE_STALE", message: "Synthetic stale source" }] } }));
  await assert.rejects(main.ownerCall({ ownerEndpoint: "http://127.0.0.1:17902/owner" }, "proposal/apply", {}), error => error.uncertain !== true);
  const beforeOversize = h.calls.length;
  await assert.rejects(main.ownerCall({ ownerEndpoint: "http://127.0.0.1:17902/owner" }, "work/intake", { question: "가".repeat(6000) }));
  assert.equal(h.calls.length, beforeOversize, "UTF-8 byte limits fail before transport");
});

test("source scope binds exact current UTF-8 bytes and rejects absent/non-Markdown sources", async () => {
  const { h, view } = await viewFixture();
  assert.deepEqual(await view.scope(), { path: "Notes/Alpha.md", content_sha256: digest(h.files.get("Notes/Alpha.md")) });
  h.files.set("Notes/Alpha.md", "# Alpha\n\nChanged β bytes.\n");
  assert.equal((await view.scope()).content_sha256, digest(h.files.get("Notes/Alpha.md")));
  view.setTarget("Notes/missing.md");
  await assert.rejects(view.scope());
  h.files.set("Notes/image.png", "synthetic");
  view.setTarget("Notes/image.png");
  await assert.rejects(view.scope());
  await view.onClose(); h.cleanup();
});

test("all four search modes submit exact mode and open the returned exact Vault file", async () => {
  const { h, view } = await viewFixture();
  await view.openSection("search");
  for (const mode of ["lexical", "vector", "hybrid", "ontology"]) {
    h.respond(async call => h.response(call.route === "search/query" ? { ...payload(call.route, `Unique ${mode} hit`), mode } : payload(call.route)));
    view.mode = mode; view.query = "  synthetic query  ";
    await view.search();
    const call = h.calls.filter(item => item.route === "search/query").at(-1);
    assert.deepEqual(call.payload, { query: "synthetic query", mode });
    const hit = button(view, `Unique ${mode} hit`);
    assert.ok(hit, `Render ${mode} result as an actionable document`);
    await hit.fire("click");
    assert.equal(h.opened.at(-1), "Notes/Beta.md");
  }
  const before = h.opened.length;
  h.files.set("Notes/Beta.md", "# Changed after the search snapshot\n");
  await button(view, "Unique ontology hit").fire("click");
  assert.equal(h.opened.length, before + 1, "Changed notes remain navigable with an explicit historical-evidence warning");
  assert.match(view.contentEl.textContent, /인용 당시와 원문이 다릅니다/);
  h.files.delete("Notes/Beta.md");
  await button(view, "Unique ontology hit").fire("click");
  assert.equal(h.opened.length, before + 1, "A missing result must not create a replacement file");
  await view.onClose(); h.cleanup();
});

const reads = [
  { name: "search", section: "search", route: "search/query", run: view => { view.query = "synthetic"; return view.search(); } },
  { name: "Work list", section: "work", route: "work/list", run: view => view.showWorkList() },
  { name: "Work result", section: "work", route: "work/result", run: view => view.showWorkResult("workrun.synthetic") },
  { name: "Pending list", section: "pending", route: "proposal/list", run: view => view.showProposals() },
  { name: "Pending detail", section: "pending", route: "proposal/read", run: view => view.showProposal(ref) },
  { name: "daily list", section: "daily", route: "daily/list", run: view => view.showDaily() },
  { name: "daily detail", section: "daily", route: "daily/read", run: view => view.showDailyItem("daily.synthetic") },
];
for (const read of reads) {
  for (const transition of ["note switch", "view close", "disconnect", "section switch"]) {
    test(`${read.name} suppresses a held response after ${transition}`, async () => {
      const { h, view } = await viewFixture();
      await view.openSection(read.section); await flush();
      const gate = deferred(), started = deferred();
      const marker = `LATE PRIVATE ${read.name} ${transition}`;
      let held = false;
      h.respond(async call => {
        if (call.route === read.route && !held) { held = true; started.resolve(); await gate.promise; return h.response(payload(call.route, marker)); }
        return h.response(payload(call.route));
      });
      const pending = view.run(() => read.run(view));
      // Explicit first-request observation avoids a test that passes without issuing I/O.
      let timer;
      try {
        await Promise.race([started.promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`No ${read.route} request`)), 1500); })]);
      } finally { clearTimeout(timer); }
      if (transition === "note switch") view.setTarget("Notes/Beta.md");
      else if (transition === "view close") await view.onClose();
      else if (transition === "disconnect") view.disconnect();
      else await view.openSection("library");
      gate.resolve(); await pending; await flush();
      assert.ok(!view.contentEl.textContent.includes(marker), "Stale private response must not repaint the consumer view");
      assert.ok(!h.notices.some(text => text.includes(marker)), "Stale errors/content must not leak through notices");
      if (transition !== "view close") await view.onClose();
      h.cleanup();
    });
  }
}

for (const transition of ["disconnect", "view close"]) {
  test(`a late Owner health reply cannot restore write authority after ${transition}`, async () => {
    const { h, view } = await viewFixture();
    await view.openSection("metadata", "Notes/Alpha.md");
    const gate = deferred();
    h.respond(async call => {
      if (call.route === "owner/status") await gate.promise;
      return h.response(payload(call.route));
    });
    const pending = view.run(() => view.refreshOwner());
    if (transition === "disconnect") view.disconnect();
    else await view.onClose();
    gate.resolve(); await pending; await flush();
    const before = mutations(h).length;
    await view.run(() => view.submitTemplate("note_metadata"));
    assert.equal(mutations(h).length, before, "Stale health must not re-enable submission");
    if (transition !== "view close") await view.onClose();
    h.cleanup();
  });
}

test("unknown mutation IDs stay locked across backend start, refresh, disconnect and a new same-endpoint view", async () => {
  const { h, main, plugin, view } = await viewFixture();
  await view.openSection("metadata", "Notes/Alpha.md");
  h.respond(async call => {
    if (call.route === "work/intake-template") throw new Error("Synthetic response lost after possible commit");
    return h.response(payload(call.route));
  });
  const before = h.calls.filter(call => call.route === "work/intake-template").length;
  await view.run(() => view.submitTemplate("note_metadata"));
  assert.equal(h.calls.filter(call => call.route === "work/intake-template").length, before + 1);
  const sent = h.calls.filter(call => call.route === "work/intake-template").at(-1).payload;
  assert.deepEqual(sent.scope, { path: "Notes/Alpha.md", content_sha256: digest(h.files.get("Notes/Alpha.md")) });
  assert.ok(typeof sent.request_id === "string" && sent.request_id.length > 0);
  assert.match(view.contentEl.textContent + h.notices.join(" "), /unknown|uncertain|불명|알 수|확인|재전송/i);
  await view.run(() => view.submitTemplate("note_metadata"));
  await view.refreshOwner();
  await view.run(() => view.submitTemplate("note_metadata"));
  assert.equal(h.calls.filter(call => call.route === "work/intake-template").length, before + 1, "A status refresh is not mutation reconciliation");
  const ids = [...plugin.requestIds.entries()];
  const unknown = { ...view.unknownRequest };
  h.exec((_file, args, _options, callback) => backendReply(callback, backend(args[0] === "status" ? "unreachable" : "owner_connected")));
  await button(view, "로컬 백엔드 시작").fire("click");
  assert.equal(view.uncertain, true);
  assert.deepEqual(view.unknownRequest, unknown);
  assert.deepEqual([...plugin.requestIds.entries()], ids);
  view.disconnect();
  assert.equal(view.uncertain, true);
  await view.refreshOwner();
  await view.run(() => view.submitTemplate("note_metadata"));
  const other = new main.WorkReviewSidebar({}, plugin);
  await other.onOpen();
  assert.equal(other.uncertain, true);
  assert.deepEqual(other.unknownRequest, unknown);
  await view.onClose();
  await other.run(() => other.submitTemplate("note_metadata"));
  assert.equal(h.calls.filter(call => call.route === "work/intake-template").length, before + 1, "New views cannot bypass an unknown outcome");
  plugin.settings.ownerEndpoint = "http://127.0.0.1:17903/owner";
  assert.equal(other.uncertain, false, "Uncertainty belongs to the exact Owner endpoint");
  plugin.settings.ownerEndpoint = "http://127.0.0.1:17902/owner";
  assert.equal(other.uncertain, true, "Returning to the original Owner restores its lock");
  assert.deepEqual([...plugin.requestIds.entries()], ids);
  await other.onClose(); h.cleanup();
});

test("a mutation response lost after its view closes locks an already reopened same-endpoint view", async () => {
  const { h, main, plugin, view } = await viewFixture();
  const held = deferred(), observed = deferred();
  h.respond(async call => {
    if (call.route === "work/intake-template") {
      observed.resolve();
      await held.promise;
      throw new Error("Synthetic late unknown outcome");
    }
    return h.response(payload(call.route));
  });
  const pending = view.run(() => view.submitTemplate("note_metadata"));
  await observed.promise;
  await view.onClose();
  const reopened = new main.WorkReviewSidebar({}, plugin);
  await reopened.onOpen();
  held.resolve(); await pending;
  assert.equal(reopened.uncertain, true);
  const request = h.calls.find(call => call.route === "work/intake-template").payload;
  assert.equal(reopened.unknownRequest.request_id, request.request_id);
  await reopened.run(() => reopened.submitTemplate("note_metadata"));
  assert.equal(h.calls.filter(call => call.route === "work/intake-template").length, 1);
  await reopened.onClose(); h.cleanup();
});

test("an unknown delivery only fences its document and can be resolved by an exact read-only receipt", async () => {
  const { h, view } = await viewFixture();
  let committed = null;
  h.respond(async call => {
    if (call.route === "work/intake-template" && call.payload.scope.path === "Notes/Alpha.md") {
      committed = call.payload;
      throw new Error("Lost acknowledgement");
    }
    if (call.route === "mutation/receipt") {
      assert.deepEqual(call.payload, { route: "work/intake-template", request: committed });
      return h.response({ found: true, receipt: { request_id: committed.request_id }, observation_only: true });
    }
    if (call.route === "work/intake-template") return h.response({ request_id: call.payload.request_id, receipts: [] });
    return h.response(payload(call.route));
  });
  await view.run(() => view.submitTemplate("note_metadata"));
  assert.equal(view.uncertain, true);
  view.setTarget("Notes/Beta.md"); await flush();
  assert.equal(view.uncertain, false);
  await view.submitTemplate("note_metadata");
  assert.equal(h.calls.filter(call => call.route === "work/intake-template").length, 2);
  view.setTarget("Notes/Alpha.md"); await flush();
  await view.reconcileMutation();
  assert.equal(view.uncertain, false);
  assert.equal(h.calls.filter(call => call.route === "work/intake-template").length, 2, "Receipt reconciliation must never resend");
  await view.onClose(); h.cleanup();
});

test("review duration does not lock an unchanged proposal after one minute", async () => {
  const { h, view } = await viewFixture();
  let detail = payload("proposal/read");
  h.respond(async call => {
    if (call.route === "proposal/read") return h.response(detail);
    if (call.route === "proposal/decide") {
      detail = { ...detail, decision: { response: { chosen_action: call.payload.decision } } };
      return h.response({ decision: detail.decision });
    }
    return h.response(payload(call.route));
  });
  await view.showProposal(ref);
  const now = Date.now;
  try {
    const later = now() + 120000;
    Date.now = () => later;
    view.sync();
    assert.equal(button(view, "승인 기록").disabled, false);
    await view.decideProposal("approve");
    assert.equal(h.calls.filter(call => call.route === "proposal/decide").length, 1);
    assert.equal(h.calls.filter(call => call.route === "proposal/apply").length, 0);
  } finally {
    Date.now = now;
    await view.onClose(); h.cleanup();
  }
});

test("an intentional new Work receives a new identity only after the prior group is definitively terminal", async () => {
  const { h, view } = await viewFixture();
  let requestId, terminal = false;
  h.respond(async call => {
    if (call.route === "work/intake-template") {
      requestId = call.payload.request_id;
      return h.response({ request_id: requestId, receipts: [] });
    }
    if (call.route === "work/list" && requestId) return h.response({ works: [1, 2].map(number => ({
      ...payload("work/list").works[0], work_run_id: "workrun.synthetic." + number,
      template_id: "note_metadata", request_id: requestId,
      run_state: terminal ? "completed" : "running", application_state: terminal ? "recorded" : "started",
    })) });
    return h.response(payload(call.route));
  });
  await view.submitTemplate("note_metadata");
  const first = requestId;
  await view.submitTemplate("note_metadata");
  assert.equal(requestId, first, "An in-flight group retains its request identity");
  terminal = true;
  await view.showWorkList();
  await view.submitTemplate("note_metadata");
  assert.notEqual(requestId, first, "A later explicit user submission is new Work");
  await view.onClose(); h.cleanup();
});

test("approval records a decision separately from explicit apply; decided/stale sources block controls", async () => {
  const { h, view } = await viewFixture();
  await view.openSection("pending");
  let detail = payload("proposal/read");
  h.respond(async call => {
    if (call.route === "proposal/read") return h.response(detail);
    if (call.route === "proposal/decide") { detail = { ...detail, decision: { response: { chosen_action: call.payload.decision } } }; return h.response({ status: "PASS", decision: detail.decision }); }
    if (call.route === "proposal/apply") return h.response({ status: "PASS" });
    return h.response(payload(call.route));
  });
  await view.showProposal(ref);
  const applyBefore = button(view, "승인한 제안 원본 적용");
  assert.ok(!applyBefore || applyBefore.disabled, "Unapproved proposal must not offer an enabled apply control");
  await view.decideProposal("approve");
  assert.equal(h.calls.filter(call => call.route === "proposal/decide").length, 1);
  assert.equal(h.calls.filter(call => call.route === "proposal/apply").length, 0, "Approval must not imply canonical apply");
  const approveAgain = button(view, "승인 기록");
  assert.ok(!approveAgain || approveAgain.disabled, "A decided proposal cannot be approved twice");
  const canceled = view.applyProposal();
  h.cancel();
  await canceled;
  assert.equal(h.calls.filter(call => call.route === "proposal/apply").length, 0, "Canceling explicit apply does not mutate");
  const applying = view.applyProposal();
  await h.confirm();
  await applying;
  assert.equal(h.calls.filter(call => call.route === "proposal/apply").length, 1);
  detail = { ...detail, source_stale: true };
  await view.showProposal(ref);
  const applyStale = button(view, "승인한 제안 원본 적용");
  assert.ok(!applyStale || applyStale.disabled, "Stale source cannot expose enabled apply");
  const before = mutations(h).length;
  await assert.rejects(view.decideProposal("approve"));
  await assert.rejects(view.applyProposal());
  assert.equal(mutations(h).length, before, "Programmatic activation cannot bypass stale/decided controls");
  await view.onClose(); h.cleanup();
});

test("daily conflict/decided records do not expose enabled repeat-decision controls", async () => {
  const { h, view } = await viewFixture();
  await view.openSection("daily");
  for (const review_status of ["conflict", "confirmed", "rejected"]) {
    h.respond(async call => h.response(call.route === "daily/read" ? { ...payload(call.route), review_status } : payload(call.route)));
    await view.showDailyItem("daily.synthetic");
    for (const label of ["잠정 변경 확정", "잠정 변경 거절"]) {
      const control = button(view, label);
      assert.ok(!control || control.disabled, `${review_status} must not enable ${label}`);
    }
    const before = mutations(h).length;
    await assert.rejects(view.decideDaily("confirm"));
    assert.equal(mutations(h).length, before);
  }
  await view.onClose(); h.cleanup();
});

test("control snapshots bind exact targets, expose received separately from applied, and require a fresh target for repeats", async () => {
  const { h, view } = await viewFixture();
  await view.showWorkResult("workrun.synthetic");
  const target = { work_run_id: "workrun.synthetic", temporal_run_id: "native.synthetic", revision: 4, control_revision: 2, fencing_token: 7 };
  h.respond(async call => {
    if (call.route === "work/control/target") return h.response(target);
    if (call.route === "work/control/pause" || call.route === "work/control/command") return h.response({ command_id: call.payload.command_id, application_state: "received", native_execution_observation: "unavailable" });
    return h.response(payload(call.route));
  });
  await view.showControlTarget();
  const pausing = view.control("pause");
  await h.confirm(); await pausing;
  const call = h.calls.find(item => item.route === "work/control/pause");
  assert.deepEqual(call.payload.target, target);
  assert.equal(call.payload.work_run_id, "workrun.synthetic");
  assert.ok(call.payload.command_id.startsWith("obsidian."));
  assert.match(view.contentEl.querySelector(".knowledgeos-command-receipt").textContent, /received/);
  await view.showCommand(call.payload.command_id);
  assert.equal(h.calls.at(-1).payload.command_id, call.payload.command_id);
  await assert.rejects(view.control("pause"));
  assert.equal(h.calls.filter(item => item.route === "work/control/pause").length, 1);
  await view.showControlTarget();
  const changedWhileConfirming = view.control("cancel");
  view.setTarget("Notes/Beta.md");
  await h.confirm();
  await assert.rejects(changedWhileConfirming);
  assert.equal(h.calls.filter(item => item.route === "work/control/cancel").length, 0, "A note switch invalidates a pending destructive confirmation");
  await view.onClose(); h.cleanup();
});

test("steering evidence registration is separate from command acceptance and never broadens Owner supported kinds", async () => {
  const { h, view } = await viewFixture();
  await view.showWorkResult("workrun.synthetic");
  const target = { work_run_id: "workrun.synthetic", revision: 5, control_revision: 2 };
  const reference = { artifact_id: "artifact.steering.synthetic", content_digest: "sha256:" + "c".repeat(64) };
  h.respond(async call => {
    if (call.route === "work/steering/view") return h.response({ target, supported_kinds: ["answer"], parameter_bounds: {} });
    if (call.route === "work/steering/artifact") return h.response(reference);
    if (call.route === "work/steering/request") return h.response({ command_id: call.payload.command_id, application_state: "received" });
    return h.response(payload(call.route));
  });
  await view.showSteering();
  assert.deepEqual(view.steeringKind.children.map(option => option.value), ["answer"]);
  view.steeringAnswer.value = "Synthetic bounded answer";
  await view.registerSteering();
  assert.equal(h.calls.filter(call => call.route === "work/steering/request").length, 0);
  assert.deepEqual(h.calls.find(call => call.route === "work/steering/artifact").payload.content, { answer: "Synthetic bounded answer" });
  view.steeringAnswer.value = "Changed answer";
  await view.steeringFields.fire("input");
  await assert.rejects(view.submitSteering(), "Editing evidence invalidates the registered directive");
  await view.registerSteering();
  const sending = view.submitSteering();
  await h.confirm(); await sending;
  const request = h.calls.find(call => call.route === "work/steering/request");
  assert.equal(request.payload.kind, "answer");
  assert.deepEqual(request.payload.target, target);
  assert.deepEqual(request.payload.directive_reference, reference);
  assert.match(view.contentEl.querySelector(".knowledgeos-steering-receipt").textContent, /received/);
  view.steeringKind.value = "unadmitted-kind";
  await assert.rejects(view.registerSteering());
  assert.equal(h.calls.filter(call => call.route === "work/steering/request").length, 1);
  await view.onClose(); h.cleanup();
});

test("selecting Work B clears Work A immediately and a settings-save race cannot attach B to an obsolete view", async () => {
  const { h, plugin, view } = await viewFixture();
  h.respond(async call => {
    const result = payload(call.route);
    if (call.route === "work/read") return h.response({ ...result, run: { ...result.run, work_run_id: call.payload.work_run_id }, spec: { ...result.spec, objective: `OBJECTIVE ${call.payload.work_run_id}` } });
    if (call.route === "work/result") return h.response({ ...result, text: `RESULT ${call.payload.work_run_id}` });
    return h.response(result);
  });
  await view.openSection("pending");
  await view.showWorkResult("workrun.alpha");
  assert.ok(view.contentEl.textContent.includes("RESULT workrun.alpha"));
  assert.ok(view.contentEl.querySelector(".knowledgeos-control"));
  assert.equal(view.panels.work.hidden, false);
  assert.deepEqual(buttons(view).filter(element => element.getAttribute("aria-pressed") === "true").map(element => element.dataset.section), ["work"]);
  const gate = deferred();
  let saving = false;
  plugin.saveSettings = async () => { saving = true; await gate.promise; };
  const pending = view.run(() => view.showWorkResult("workrun.beta"));
  await flush();
  assert.equal(saving, true, "Hold the actual awaited settings persistence boundary");
  assert.ok(!view.contentEl.textContent.includes("RESULT workrun.alpha"), "Selecting another Work removes the old private result immediately");
  assert.equal(view.contentEl.querySelector(".knowledgeos-control"), null, "Old Work controls must not remain active while another Work is loading");
  await view.openSection("search");
  gate.resolve(); await pending; await flush();
  assert.ok(!view.contentEl.textContent.includes("RESULT workrun.alpha"));
  assert.ok(!view.contentEl.textContent.includes("RESULT workrun.beta"), "Superseded selection cannot repaint after persistence returns");
  assert.equal(view.work, null, "No undisplayed Work B may remain attached to an obsolete Work A view");
  await assert.rejects(view.control("pause"));
  assert.equal(h.calls.filter(call => call.route.startsWith("work/control/")).length, 0);
  await view.onClose(); h.cleanup();
});

test("editing steering input while evidence registration is pending cannot resurrect the superseded directive", async () => {
  const { h, view } = await viewFixture();
  await view.showWorkResult("workrun.synthetic");
  const gate = deferred();
  let registering = false;
  h.respond(async call => {
    if (call.route === "work/steering/view") return h.response({ target: { work_run_id: "workrun.synthetic", revision: 5 }, supported_kinds: ["answer"] });
    if (call.route === "work/steering/artifact") {
      registering = true;
      await gate.promise;
      return h.response({ artifact_id: "artifact.superseded", content_digest: "sha256:" + "d".repeat(64) });
    }
    return h.response(payload(call.route));
  });
  await view.showSteering();
  view.steeringAnswer.value = "Old answer sent for registration";
  const pending = view.run(() => view.registerSteering());
  await flush();
  assert.equal(registering, true);
  view.steeringAnswer.value = "Current answer edited before registration returned";
  await view.steeringFields.fire("input");
  gate.resolve(); await pending; await flush();
  assert.ok(!view.steeringArtifact, "A returned artifact must match the current form, not merely the form captured before await");
  await assert.rejects(view.submitSteering());
  assert.equal(h.calls.filter(call => call.route === "work/steering/request").length, 0);
  await view.onClose(); h.cleanup();
});

test("library task controls follow cursor changes within one note without enabling absent plugins", async () => {
  const { h, view } = await viewFixture();
  const command = "obsidian-tasks-plugin:edit-task";
  h.app.plugins.enabledPlugins.add("obsidian-tasks-plugin");
  h.app.plugins.plugins["obsidian-tasks-plugin"] = {};
  h.app.commands.commands[command] = { id: command };
  let line = "Synthetic prose without a task";
  h.app.workspace.getActiveViewOfType = () => ({ file: h.app.vault.getAbstractFileByPath("Notes/Alpha.md"), editor: { getCursor: () => ({ line: 0 }), getLine: () => line } });
  view.render();
  await view.openSection("library");
  const task = () => view.contentEl.querySelector('button[data-integration="task-edit"]');
  const capture = () => view.contentEl.querySelector('button[data-integration="capture"]');
  assert.ok(task()?.disabled, "Prose cannot enable task editing");
  assert.ok(capture()?.disabled, "QuickAdd is absent in this fixture");
  line = "- [ ] Synthetic cursor task #task";
  view.sync();
  assert.equal(task()?.disabled, false, "Same-file cursor changes must refresh the rendered control");
  assert.equal(capture()?.disabled, true, "Refreshing context does not override missing plugin availability");
  await task().fire("click");
  assert.deepEqual(h.commands, [command]);
  line = "Back to prose in the same file";
  await view.openSection("work"); await view.openSection("library");
  assert.equal(task()?.disabled, true, "Reentering the library refreshes stale task context");
  line = "- [ ] Another task #task";
  await view.openSection("work"); await view.openSection("library");
  assert.equal(task()?.disabled, false);
  assert.equal(capture()?.disabled, true);
  await view.onClose(); h.cleanup();
});

test("a failed explicit Owner refresh reports its actual error in the current view", async () => {
  const { h, view } = await viewFixture();
  const message = "Synthetic Owner refresh transport failure, request 7831";
  h.respond(async call => {
    if (call.route === "owner/status") throw new Error(message);
    return h.response(payload(call.route));
  });
  await view.run(() => view.refreshOwner());
  assert.ok(view.contentEl.textContent.includes(message), "Refresh epoch invalidation must not suppress the current request's real failure");
  assert.equal(view.owner, null);
  assert.equal(view.uncertain, false, "A read failure is not an unknown mutation delivery");
  await view.onClose(); h.cleanup();
});

test("every native section command opens/prepares rather than submits Owner Work", async () => {
  const h = host(), main = h.load(h.clientPath);
  const plugin = new main(), opened = [];
  plugin.activateView = async (section, selected) => { opened.push({ section, selected }); };
  await plugin.onload();
  const expected = { "open-work-review": "work", "open-search": "search", "open-note-work": "work", "open-pending": "pending", "open-daily-review": "daily", "request-metadata": "metadata", "request-enhancement": "enhancement", "open-library": "library" };
  for (const [id, section] of Object.entries(expected)) {
    const command = plugin.commands.find(item => item.id === id);
    assert.ok(command, `Register ${id}`);
    await command.callback();
    assert.equal(opened.at(-1).section, section);
    if (["open-note-work", "request-metadata", "request-enhancement"].includes(id)) assert.equal(opened.at(-1).selected, "Notes/Alpha.md");
  }
  assert.equal(h.calls.length, 0);
  const menuItems = [];
  const menu = {
    addSeparator() {},
    addItem(callback) {
      const item = { setTitle() { return this; }, setIcon() { return this; }, onClick(run) { menuItems.push(run); return this; } };
      callback(item);
    },
  };
  await h.emit("file-menu", menu, h.app.vault.getAbstractFileByPath("Notes/Beta.md"));
  assert.equal(menuItems.length, 4, "Context menu exposes scoped Work/Pending/metadata/enhancement");
  for (const run of menuItems) {
    await run();
    assert.equal(opened.at(-1).selected, "Notes/Beta.md", "Context menu binds clicked note, not unrelated active note");
  }
  assert.equal(h.calls.length, 0);
  h.active(null);
  const before = opened.length;
  for (const id of ["open-note-work", "request-metadata", "request-enhancement"]) await plugin.commands.find(item => item.id === id).callback();
  assert.equal(opened.length, before, "Scoped commands require an actual Markdown note");
  h.cleanup();
});

test("metadata/enhancement sections prepare forms without mutation", async () => {
  const { h, view } = await viewFixture();
  const before = mutations(h).length;
  await view.openSection("metadata", "Notes/Beta.md"); await flush();
  assert.equal(view.selectedPath, "Notes/Beta.md");
  await view.openSection("enhancement", "Notes/Beta.md"); await flush();
  assert.equal(mutations(h).length, before);
  await view.onClose(); h.cleanup();
});

test("optional integrations fail closed when absent/disabled and dispatch only exact known active commands/files", async () => {
  const h = host(), integrations = h.load(h.clientPath);
  for (const action of integrations.integrationActions(h.app)) {
    assert.equal(action.available, false);
    assert.ok(action.description.length > 0);
    assert.equal(await integrations.executeIntegration(h.app, action.id), false);
  }
  assert.equal(await integrations.executeIntegration(h.app, "arbitrary:external-command"), false);
  assert.equal(h.commands.length, 0); assert.equal(h.opened.length, 0); assert.equal(h.calls.length, 0);
  const command = "quickadd:choice:f29c95dc-1f39-4ff0-81bd-2ef12181bdcf";
  h.app.commands.commands[command] = { id: command };
  h.app.plugins.plugins.quickadd = {};
  assert.equal(integrations.integrationActions(h.app).find(item => item.id === "capture").available, false, "Loaded object cannot override disabled plugin");
  h.app.plugins.enabledPlugins.add("quickadd");
  assert.equal(integrations.integrationActions(h.app).find(item => item.id === "capture").available, true);
  assert.equal(await integrations.executeIntegration(h.app, "capture"), true);
  assert.deepEqual(h.commands, [command]);
  h.app.plugins.enabledPlugins.delete("quickadd");
  assert.equal(await integrations.executeIntegration(h.app, "capture"), false, "Availability must be rechecked at click time");
  h.files.set("Home.md", "# Synthetic home\n");
  assert.equal(await integrations.executeIntegration(h.app, "home"), true);
  assert.equal(h.opened.at(-1), "Home.md");
  h.files.set("99_System/Bases/Knowledge.base", "views: []\n");
  assert.equal(await integrations.executeIntegration(h.app, "knowledge-base"), false, "Bases requires enabled core plugin");
  h.app.internalPlugins = { getPluginById: id => ({ enabled: id === "bases" }) };
  assert.equal(await integrations.executeIntegration(h.app, "knowledge-base"), true);
  assert.equal(h.opened.at(-1), "99_System/Bases/Knowledge.base");
  assert.equal(h.calls.length, 0);
});

test("Tasks editing requires an enabled plugin, exact registered command and a #task checkbox at the cursor", async () => {
  const h = host(), integrations = h.load(h.clientPath);
  const command = "obsidian-tasks-plugin:toggle-done";
  h.app.plugins.enabledPlugins.add("obsidian-tasks-plugin");
  h.app.plugins.plugins["obsidian-tasks-plugin"] = {};
  h.app.commands.commands[command] = { id: command };
  let line = "- [ ] Untagged checkbox";
  h.app.workspace.getActiveViewOfType = () => ({ file: h.app.vault.getAbstractFileByPath("Notes/Alpha.md"), editor: { getCursor: () => ({ line: 0 }), getLine: () => line } });
  assert.equal(await integrations.executeIntegration(h.app, "task-toggle"), false);
  line = "ordinary #task prose";
  assert.equal(await integrations.executeIntegration(h.app, "task-toggle"), false);
  line = "- [ ] Synthetic task #task/project";
  assert.equal(await integrations.executeIntegration(h.app, "task-toggle"), true);
  assert.deepEqual(h.commands, [command]);
  assert.equal(h.calls.length, 0, "Document Tasks are not Owner Work");
});

async function realOwner() {
  const h = host(), main = h.load(h.clientPath);
  const selected = main.endpoint(realEndpoint);
  // Port 17900 belongs to the maintained personal Owner; this runner is fixture-only.
  assert.notEqual(new URL(selected).port, "17900", "Use a separately started disposable fixture, not the personal Owner");
  h.respond(async call => {
    const response = await fetch(call.url, { method: call.method, headers: call.headers, body: call.body, signal: AbortSignal.timeout(15000), redirect: "error" });
    return { status: response.status, json: await response.json() };
  });
  const settings = { ownerEndpoint: selected };
  const status = await main.ownerCall(settings, "owner/status", {});
  assert.equal(status.owner_operation_id, "knowledgeos");
  assert.equal(typeof status.revision, "number"); assert.equal(typeof status.read_only, "boolean");
  assert.equal(typeof status.bindings, "object");
  const evidence = { endpoint: selected, owner: { revision: status.revision, generation: status.generation, read_only: status.read_only, bindings: status.bindings }, searches: [] };
  for (const mode of ["lexical", "vector", "hybrid", "ontology"]) {
    try {
      const result = await main.ownerCall(settings, "search/query", { query: "webconsolefixtureevidence", mode });
      assert.equal(result.mode, mode); assert.ok(Array.isArray(result.results));
      for (const item of result.results) { assert.equal(typeof item.path, "string"); assert.equal(typeof item.excerpt, "string"); }
      if (mode === "lexical") assert.ok(result.results.length > 0, "Disposable web fixture must expose its real synthetic lexical source");
      evidence.searches.push({ mode, outcome: "returned", fields: Object.keys(result), count: result.results.length, paths: result.results.map(item => item.path) });
    } catch (error) {
      if (mode === "lexical" || error instanceof assert.AssertionError) throw error;
      evidence.searches.push({ mode, outcome: "owner-rejected", message: error.message });
    }
  }
  const listed = await main.ownerCall(settings, "proposal/list", {});
  assert.ok(Array.isArray(listed.proposals));
  assert.ok(listed.proposals.length > 0, "web_console_smoke.py prepares one real Pending proposal");
  const detail = await main.ownerCall(settings, "proposal/read", { proposal_reference: listed.proposals[0].proposal_reference });
  assert.equal(typeof detail.source_stale, "boolean"); assert.equal(typeof detail.text, "string");
  assert.equal(typeof detail.source_digest, "string");
  evidence.pending = { count: listed.proposals.length, fields: Object.keys(detail), source_stale: detail.source_stale, apply_allowed: detail.apply_allowed };
  assert.ok(h.calls.every(call => readRoutes.has(call.route)), "Real fixture mode is read-only");
  console.log("REAL_OWNER_EVIDENCE " + JSON.stringify(evidence));
}

(async () => {
  console.log("BOUNDARY: Node + bounded Obsidian/DOM host adapter; synthetic transport except explicit --endpoint. No GUI/layout/plugin-loaded proof.");
  let failures = 0;
  for (const { name, run } of tests) {
    let deadline;
    try {
      await Promise.race([run(), new Promise((_, reject) => {
        deadline = setTimeout(() => reject(new Error("Headless scenario exceeded its 20-second deadline")), 20000);
      })]);
      console.log(`PASS ${name}`);
    }
    catch (error) { failures++; console.error(`FAIL ${name}\n${error.stack || error}`); }
    finally { clearTimeout(deadline); cleanupHosts(); }
  }
  if (realEndpoint) {
    try { await realOwner(); console.log("PASS real disposable Owner through exported plugin ownerCall (read-only)"); }
    catch (error) { failures++; console.error(`FAIL real disposable Owner\n${error.stack || error}`); }
    finally { cleanupHosts(); }
  }
  console.log(JSON.stringify({ checks: tests.length + (realEndpoint ? 1 : 0), failures, adapter: "bounded-headless-host", real_owner: Boolean(realEndpoint), gui: false }));
  process.exitCode = failures ? 1 : 0;
})();
