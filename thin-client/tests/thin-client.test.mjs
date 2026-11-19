// Thin Client tests (C11). Run: node --test thin-client/tests/thin-client.test.mjs
// The `obsidian` module is replaced by ./obsidian-stub.cjs; the Owner by a fake transport.
import { test } from "node:test";
import assert from "node:assert/strict";
import Module, { createRequire } from "node:module";
import { createHash, webcrypto } from "node:crypto";

const require = createRequire(import.meta.url);
const obsidian = require("./obsidian-stub.cjs");
const MAIN_JS = require.resolve("../main.js");
// Stand-in for the plugin's lazy require("child_process"): records each launchctl call and answers from
// `launchctl.answer(args)`: {} is exit 0, {code, stderr} a failure (code mirrors Node's error.code).
// `launchctl.hold`, a promise, delays the callback so a test can act while a command is in flight.
const launchctl = { calls: [], answer: () => ({}), hold: null };
const childProcess = {
  execFile(file, args, options, callback) {
    launchctl.calls.push({ file, args });
    const hold = launchctl.hold;
    const { code, stdout = "", stderr = "" } = launchctl.answer(args);
    const error = code === undefined ? null : Object.assign(new Error(`Command failed: ${file} ${args.join(" ")}`), { code });
    const finish = () => callback(error, stdout, stderr);
    if (hold) hold.then(finish);
    else setImmediate(finish);
  },
};
const originalLoad = Module._load;
Module._load = function load(request, parent, isMain) {
  if (request === "obsidian") return obsidian;
  if (request === "child_process" && parent && parent.filename === MAIN_JS) return childProcess;
  return originalLoad.call(this, request, parent, isMain);
};
const ThinClient = require("../main.js");
const manifest = require("../manifest.json");
const { ui, TFile, TFolder, SuggestModal } = obsidian;

if (!globalThis.crypto) Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
// Plugin timers run on a compressed clock so polling and timeouts finish in milliseconds.
globalThis.window = {
  setTimeout: (callback, ms) => setTimeout(callback, Math.min(ms, 2)),
  clearTimeout: (id) => clearTimeout(id),
  setInterval: (callback) => { ui.intervals.push(callback); return ui.intervals.length; },
  clearInterval() {},
  open: (url) => { ui.opened.push(url); },
};

const MARKER = "본문-비밀-7c1e";
const PROPOSAL = "90_System/Reviews/proposal.md";
const NOTES = {
  "00_Inbox/a.md": `---\ntype: capture\n---\n${MARKER} alpha 생각\n`,
  "00_Inbox/b.md": `${MARKER} beta 메모\n`,
  "40_Knowledge/Ideas/c.md": `---\ntype: idea\n---\n${MARKER} gamma\n`,
  "40_Knowledge/Ideas/Sub/d.md": `${MARKER} delta\n`,
  "40_Knowledge/Ideas/image.png": "PNG-bytes",
  [PROPOSAL]: `---\ntype: proposal\n---\n- [x] ${MARKER} proposal checklist\n`,
};
const COMMAND_IDS = [
  "standardize-preview", "organize", "enhance-develop", "enhance-ask", "enhance-review", "free-text",
  "related-notes", "semantic-search", "review-apply", "review-reject", "review-undo", "open-console",
  "owner-start", "owner-stop", "open-sidebar",
];
const RESULT_NOTE = "90_System/Reports/result.md";

const sha = (text) => createHash("sha256").update(text, "utf8").digest("hex");
const OWNER_LABEL = "com.agentfabric.knowledgeos.owner";
const OWNER_TARGET = `gui/${process.getuid()}/${OWNER_LABEL}`;
const READY = { status: 200, body: { alive: true, ready: true, healthy: true, blockers: [] } };
const UNREADY = { status: 200, body: { alive: true, ready: false, healthy: false, blockers: ["vault_not_found"] } };

// Health answers in order and the last one repeats; "down" refuses the connection.
function sequence(...answers) {
  let next = 0;
  return () => {
    const answer = answers[Math.min(next, answers.length - 1)];
    next += 1;
    if (answer === "down") throw new Error("connect ECONNREFUSED 127.0.0.1:8765");
    return answer;
  };
}

function makeApp(notes, frontmatter = {}) {
  const byPath = new Map();
  const root = new TFolder("/");
  const folderFor = (path) => {
    if (!path) return root;
    if (byPath.has(path)) return byPath.get(path);
    const folder = new TFolder(path);
    byPath.set(path, folder);
    folderFor(path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "").children.push(folder);
    return folder;
  };
  for (const [path, content] of Object.entries(notes)) {
    const file = new TFile(path, content);
    byPath.set(path, file);
    folderFor(path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "").children.push(file);
  }
  const handlers = new Map();
  const workspace = {
    active: null,
    viewFactories: new Map(),
    leaves: [],
    getLeavesOfType(type) { return this.leaves.filter((leaf) => leaf.type === type); },
    getRightLeaf() {
      const leaf = {
        app,
        async setViewState(state) {
          this.type = state.type;
          this.view = workspace.viewFactories.get(this.type)(this);
          await this.view.onOpen();
        },
      };
      this.leaves.push(leaf);
      return leaf;
    },
    async revealLeaf(leaf) { this.revealed = leaf; },
    detachLeavesOfType(type) { this.leaves = this.leaves.filter((leaf) => leaf.type !== type); },
    opened: [],
    on(name, callback) {
      if (!handlers.has(name)) handlers.set(name, []);
      handlers.get(name).push(callback);
      return { name, callback };
    },
    async trigger(name, ...args) {
      for (const callback of handlers.get(name) || []) await callback(...args);
    },
    getActiveFile() { return this.active; },
    getLeaf() {
      return {
        async openFile(file, state) {
          workspace.opened.push({ path: file.path, state });
          workspace.active = file;
        },
      };
    },
    async openLinkText(link) { workspace.opened.push({ path: link }); },
  };
  const forbidden = (name) => async () => { throw new Error(`Thin Client must not call vault.${name}`); };
  const vault = {
    getAbstractFileByPath: (path) => byPath.get(path) || null,
    on: (...args) => workspace.on(...args),
    async readBinary(file) {
      const bytes = new TextEncoder().encode(file.content);
      return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    },
    read: forbidden("read"),
    cachedRead: forbidden("cachedRead"),
    modify: forbidden("modify"),
    create: forbidden("create"),
    delete: forbidden("delete"),
    rename: forbidden("rename"),
  };
  const metadataCache = {
    getFileCache: (file) => ({ frontmatter: frontmatter[file.path] }),
    on: (...args) => workspace.on(...args),
  };
  const app = { vault, workspace, metadataCache, storedData: undefined, file: (path) => byPath.get(path) };
  return app;
}

// Fake ExOps backend: records every call, keeps receipts by request_id, can lose responses.
function makeServer() {
  const server = {
    calls: [],
    receipts: new Map(),
    down: false,
    status: "completed",
    searchStatus: "ok",
    pollsBeforeSettle: 0,
    losePostOnce: false, // request lost before the Owner records it
    dropPostOnce: false, // recorded, response lost
    hangPostOnce: false, // recorded, response never arrives
    posts(command) {
      return this.calls
        .filter((call) => call.method === "POST" && call.path === "/api/requests")
        .map((call) => JSON.parse(call.body))
        .filter((request) => !command || request.command === command);
    },
    lookups() {
      return this.calls.filter((call) => call.method === "GET" && call.path.startsWith("/api/requests/"));
    },
  };
  server.fetch = async (url, init) => {
    const { pathname } = new URL(url);
    server.calls.push({ url, path: pathname, method: init.method, headers: init.headers, body: init.body });
    if (pathname === "/api/health" && server.health) return server.health();
    if (server.down) throw new Error("connect ECONNREFUSED 127.0.0.1:8765");
    if (pathname === "/api/health") return { status: 200, body: { alive: true, ready: true, healthy: true, blockers: [] } };
    if (pathname === "/api/search" && init.method === "POST") {
      if (server.searchStatus === "unavailable") return { status: 200, body: { status: "unavailable", results: [] } };
      return {
        status: 200,
        body: { status: "ok", results: [{ title: "Beta", path: "00_Inbox/b.md", type: "capture", status: "inbox", excerpt: "beta", line: 3 }] },
      };
    }
    if (pathname === "/api/requests" && init.method === "POST") {
      const request = JSON.parse(init.body);
      if (server.losePostOnce) {
        server.losePostOnce = false;
        throw new Error("socket hang up");
      }
      const existing = server.receipts.get(request.request_id);
      if (existing) {
        return existing.body === init.body ? { status: 200, body: existing.view() } : { status: 409, body: { error: "conflict" } };
      }
      const preview = request.command === "standardize.preview";
      const final = {
        request_id: request.request_id,
        command: request.command,
        actor: "user/local",
        received_at: "2026-11-19T10:00:00+09:00",
        status: preview ? "completed" : server.status,
        work_id: null,
        outcome: preview
          ? {
            preview_id: `preview-${server.receipts.size + 1}`,
            notes: request.params.paths.map((path) => ({
              path,
              changes: [{ key: "id", rule: "id", before: null, after: "00000000-0000-4000-8000-000000000000" }],
              diff: `--- ${path}\n+++ ${path}\n+id: 00000000-0000-4000-8000-000000000000`,
            })),
            skipped: [],
          }
          : { result_path: RESULT_NOTE },
        notice: preview ? null : "처리했습니다",
      };
      const entry = {
        body: init.body,
        polls: server.pollsBeforeSettle,
        view() { return this.polls > 0 ? { ...final, status: "running", outcome: null } : final; },
      };
      server.receipts.set(request.request_id, entry);
      if (server.dropPostOnce) {
        server.dropPostOnce = false;
        throw new Error("socket hang up");
      }
      if (server.hangPostOnce) {
        server.hangPostOnce = false;
        return new Promise(() => {});
      }
      return { status: 202, body: entry.view() };
    }
    const match = /^\/api\/requests\/(.+)$/.exec(pathname);
    if (match && init.method === "GET") {
      const entry = server.receipts.get(decodeURIComponent(match[1]));
      if (!entry) return { status: 404, body: { error: "not_found" } };
      if (entry.polls > 0) entry.polls -= 1;
      return { status: 200, body: entry.view() };
    }
    return { status: 404, body: { error: "not_found" } };
  };
  return server;
}

async function until(predicate, label, ms = 3000) {
  const deadline = Date.now() + ms;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${label}`);
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
}

async function start(app, server) {
  ui.notices.length = 0;
  ui.modals.length = 0;
  ui.intervals.length = 0;
  ui.menus.length = 0;
  ui.opened.length = 0;
  launchctl.calls.length = 0;
  launchctl.answer = () => ({});
  launchctl.hold = null;
  ui.transport = server.fetch;
  const plugin = new ThinClient(app, manifest);
  await plugin.onload();
  return plugin;
}

function runCommand(plugin, id) {
  const command = plugin.commands.find((candidate) => candidate.id === id);
  assert.ok(command, `command ${id} registered`);
  if (command.checkCallback) {
    assert.equal(command.checkCallback(true), true, `${id} available on the active note`);
    command.checkCallback(false);
  } else {
    command.callback();
  }
}

function settingsIn(el) {
  return [...el.settings, ...el.children.flatMap(settingsIn)];
}

async function waitModal(title, count) {
  await until(() => ui.modals.length > count && ui.modals.at(-1).titleEl.text === title, `modal ${title}`);
  return ui.modals.at(-1);
}

function buttonIn(modal, text) {
  const button = settingsIn(modal.contentEl).flatMap((setting) => setting.buttons).find((item) => item.text === text);
  assert.ok(button, `button ${text}`);
  return button;
}

function textsIn(modal) {
  return settingsIn(modal.contentEl).flatMap((setting) => setting.texts);
}

async function settled(action, label) {
  const before = ui.notices.length;
  await action();
  await until(() => ui.notices.length > before, `notice after ${label}`);
  return ui.notices.at(-1);
}

async function waitResults(count) {
  await until(() => ui.modals.length > count && ui.modals.at(-1) instanceof SuggestModal, "search results");
  return ui.modals.at(-1);
}

function menuFor() {
  const menu = { items: [] };
  menu.addItem = (callback) => {
    const item = {
      setTitle(title) { this.title = title; return this; },
      setIcon() { return this; },
      setSection() { return this; },
      onClick(handler) { this.handler = handler; return this; },
    };
    menu.items.push(item);
    callback(item);
    return menu;
  };
  menu.addSeparator = () => menu;
  menu.titles = () => menu.items.map((item) => item.title);
  menu.click = (title) => {
    const item = menu.items.find((candidate) => candidate.title === title);
    assert.ok(item, `menu item ${title}`);
    item.handler();
  };
  return menu;
}

test("test_SUR_1_thin_client_requests_carry_path_and_digest_only", async () => {
  const app = makeApp(NOTES, { [PROPOSAL]: { type: "proposal" } });
  const server = makeServer();
  server.pollsBeforeSettle = 1;
  const plugin = await start(app, server);
  const note = app.file("00_Inbox/a.md");
  const proposal = app.file(PROPOSAL);
  assert.deepEqual(plugin.commands.map((command) => command.id).sort(), [...COMMAND_IDS].sort());

  const drive = {
    "standardize-preview": async () => {
      const modals = ui.modals.length;
      runCommand(plugin, "standardize-preview");
      const modal = await waitModal("규격화 미리보기", modals);
      assert.match(modal.contentEl.allText, /\+id: /);
      const confirm = buttonIn(modal, "적용");
      await settled(() => confirm.click(), "standardize.confirm");
    },
    organize: () => settled(() => runCommand(plugin, "organize"), "organize"),
    "enhance-develop": () => settled(() => runCommand(plugin, "enhance-develop"), "develop"),
    "enhance-ask": async () => {
      const modals = ui.modals.length;
      runCommand(plugin, "enhance-ask");
      const modal = await waitModal("강화: 묻기", modals);
      await textsIn(modal)[0].type("무엇이 빠졌나?");
      const submit = buttonIn(modal, "요청");
      await settled(() => submit.click(), "ask");
    },
    "enhance-review": async () => {
      const modals = ui.modals.length;
      runCommand(plugin, "enhance-review");
      const modal = await waitModal("강화: 돌아보기", modals);
      const [from, to] = textsIn(modal);
      await from.type("2026-11-01");
      await to.type("2026-11-18");
      const submit = buttonIn(modal, "요청");
      await settled(() => submit.click(), "review window");
    },
    "free-text": async () => {
      const modals = ui.modals.length;
      runCommand(plugin, "free-text");
      const modal = await waitModal("자유 요청", modals);
      await textsIn(modal)[0].type("이번 주 아이디어를 묶어 줘");
      const submit = buttonIn(modal, "요청");
      await settled(() => submit.click(), "free text");
    },
    "related-notes": async () => {
      const modals = ui.modals.length;
      runCommand(plugin, "related-notes");
      const results = await waitResults(modals);
      const [first] = results.getSuggestions("");
      results.onChooseSuggestion(first);
      await until(() => app.workspace.opened.some((open) => open.path === "00_Inbox/b.md"), "result opened");
      assert.deepEqual(app.workspace.opened.at(-1), { path: "00_Inbox/b.md", state: { eState: { line: 2 } } });
    },
    "semantic-search": async () => {
      const modals = ui.modals.length;
      runCommand(plugin, "semantic-search");
      const modal = await waitModal("의미 검색", modals);
      await textsIn(modal)[0].type("생각 정리");
      const submit = buttonIn(modal, "요청");
      const before = ui.modals.length;
      await submit.click();
      await waitResults(before);
    },
    "review-apply": () => settled(() => runCommand(plugin, "review-apply"), "apply"),
    "review-reject": () => settled(() => runCommand(plugin, "review-reject"), "reject"),
    "review-undo": () => settled(() => runCommand(plugin, "review-undo"), "undo"),
    "open-console": async () => {
      runCommand(plugin, "open-console");
      assert.deepEqual(ui.opened, ["http://127.0.0.1:8765/"]);
    },
    "open-sidebar": async () => {
      runCommand(plugin, "open-sidebar");
      await until(() => app.workspace.revealed, "sidebar revealed");
    },
    "owner-start": () => settled(() => runCommand(plugin, "owner-start"), "owner start"),
    "owner-stop": async () => {
      launchctl.answer = () => { server.down = true; return {}; };
      await settled(() => runCommand(plugin, "owner-stop"), "owner stop");
      server.down = false;
    },
  };
  assert.deepEqual(Object.keys(drive).sort(), [...COMMAND_IDS].sort());
  for (const id of COMMAND_IDS) {
    app.workspace.active = id.startsWith("review-a") || id === "review-reject" ? proposal : note;
    await drive[id]();
  }
  assert.deepEqual(launchctl.calls, [{ file: "launchctl", args: ["kill", "SIGTERM", OWNER_TARGET] }], "only Owner 중지 signals launchd");

  // Menus: folder, multi-file selection, proposal note, editor.
  const folderMenu = menuFor();
  await app.workspace.trigger("file-menu", folderMenu, app.file("40_Knowledge/Ideas"), "file-explorer");
  assert.deepEqual(folderMenu.titles(), ["KnowledgeOS: 규격화 미리보기", "KnowledgeOS: 정리 요청"]);
  await settled(() => folderMenu.click("KnowledgeOS: 정리 요청"), "folder organize");
  assert.deepEqual(server.posts("organize").at(-1).params.paths.sort(), ["40_Knowledge/Ideas/Sub/d.md", "40_Knowledge/Ideas/c.md"]);

  const selectionMenu = menuFor();
  await app.workspace.trigger("files-menu", selectionMenu, [note, app.file("00_Inbox/b.md")], "file-explorer");
  let modals = ui.modals.length;
  selectionMenu.click("KnowledgeOS: 규격화 미리보기");
  const preview = await waitModal("규격화 미리보기", modals);
  const cancel = buttonIn(preview, "취소");
  await settled(() => cancel.click(), "standardize.cancel");
  assert.deepEqual(server.posts("standardize.preview").at(-1).params.paths, ["00_Inbox/a.md", "00_Inbox/b.md"]);

  const proposalMenu = menuFor();
  await app.workspace.trigger("file-menu", proposalMenu, proposal, "file-explorer");
  assert.ok(proposalMenu.titles().includes("KnowledgeOS: 적용"));
  assert.ok(proposalMenu.titles().includes("KnowledgeOS: 거절"));
  await settled(() => proposalMenu.click("KnowledgeOS: 거절"), "menu reject");

  const editorMenu = menuFor();
  await app.workspace.trigger("editor-menu", editorMenu, {}, { file: note });
  assert.ok(!editorMenu.titles().includes("KnowledgeOS: 적용"), "apply is offered only on proposal notes");
  await settled(() => editorMenu.click("KnowledgeOS: 강화: 발전"), "editor develop");
  await settled(() => editorMenu.click("KnowledgeOS: 정리 요청"), "editor organize");
  modals = ui.modals.length;
  editorMenu.click("KnowledgeOS: 강화: 묻기");
  const ask = await waitModal("강화: 묻기", modals);
  await textsIn(ask)[0].type("다음 단계는?");
  const submitAsk = buttonIn(ask, "요청");
  await settled(() => submitAsk.click(), "editor ask");

  // Every request: JSON, unique request_id, no note text, and path + digest of the bytes last seen.
  const posts = server.calls.filter((call) => call.method === "POST");
  assert.ok(posts.length > 0);
  for (const call of posts) {
    assert.equal(call.headers["Content-Type"], "application/json");
    assert.ok(!call.body.includes(MARKER), `request body must not contain note text: ${call.body}`);
    for (const content of Object.values(NOTES)) assert.ok(!call.body.includes(content.trim()));
  }
  const requests = server.posts();
  assert.equal(new Set(requests.map((request) => request.request_id)).size, requests.length);
  const commands = new Set(requests.map((request) => request.command));
  for (const command of ["standardize.preview", "standardize.confirm", "standardize.cancel", "organize", "enhance",
    "free_text", "review.apply", "review.reject", "review.undo"]) {
    assert.ok(commands.has(command), `${command} was requested`);
  }
  for (const { command, params } of requests) {
    if (command === "standardize.preview" || command === "organize") {
      assert.ok(params.paths.length > 0);
      assert.deepEqual(Object.keys(params.digests).sort(), [...params.paths].sort());
      for (const path of params.paths) assert.equal(params.digests[path], sha(NOTES[path]), path);
    } else if (command === "enhance" || command.startsWith("review.")) {
      assert.ok(params.path in NOTES, `${command} carries a note path`);
      assert.equal(params.digest, sha(NOTES[params.path]), `${command} carries the last-seen digest`);
    } else if (command === "standardize.confirm" || command === "standardize.cancel") {
      assert.deepEqual(Object.keys(params), ["preview_id"]);
    } else if (command === "free_text") {
      assert.deepEqual(params, { text: "이번 주 아이디어를 묶어 줘" });
    } else {
      assert.fail(`unexpected command ${command}`);
    }
  }
  const enhance = server.posts("enhance");
  assert.deepEqual(enhance.map((request) => request.params.mode).sort(), ["ask", "ask", "develop", "develop", "review"]);
  assert.deepEqual(enhance.find((request) => request.params.mode === "review").params.window, { from: "2026-11-01", to: "2026-11-18" });
  for (const request of server.posts("review.apply").concat(server.posts("review.reject"))) assert.equal(request.params.path, PROPOSAL);
  const searches = posts.filter((call) => call.path === "/api/search").map((call) => JSON.parse(call.body));
  assert.deepEqual(searches.map((search) => search.mode), ["related", "semantic"]);
  assert.equal(searches[0].path, "00_Inbox/a.md");
  assert.equal(searches[1].query, "생각 정리");
  // Polling happened (receipts started as running) and settled notices link the result note.
  assert.ok(server.lookups().length > 0);
  assert.ok(ui.notices.some((notice) => notice.message.includes(RESULT_NOTE)));
  assert.equal(plugin.inflight, 0);
});

test("test_SUR_2_thin_client_writes_no_settings", async () => {
  for (const stored of [undefined, { endpoint: "http://127.0.0.1:8765" }]) {
    const app = makeApp(NOTES);
    app.storedData = stored;
    const server = makeServer();
    server.down = true;
    const plugin = await start(app, server);
    await until(() => plugin.statusBarItem.text.includes("연결 끊김"), "disconnected status");

    for (const path of ["00_Inbox/a.md", "00_Inbox/b.md", "40_Knowledge/Ideas/c.md", "00_Inbox/a.md"]) {
      const file = app.file(path);
      await app.workspace.getLeaf(false).openFile(file);
      await app.workspace.trigger("file-open", file);
      await app.workspace.trigger("active-leaf-change", { view: { file } });
    }
    const failed = await settled(() => runCommand(plugin, "related-notes"), "read while disconnected");
    assert.equal(failed.timeout, 0);

    server.down = false;
    for (const tick of ui.intervals) tick();
    await until(() => plugin.statusBarItem.text.includes("연결됨"), "reconnected status");
    const modals = ui.modals.length;
    runCommand(plugin, "related-notes");
    await waitResults(modals);
    plugin.settingTabs[0].display();

    assert.deepEqual(plugin.saveCalls, [], "the client wrote its settings");
    assert.equal(plugin.settings.endpoint, "http://127.0.0.1:8765");
  }
});

test("settings tab saves only the edited endpoint", async () => {
  const app = makeApp(NOTES);
  const plugin = await start(app, makeServer());
  const tab = plugin.settingTabs[0];
  tab.display();
  assert.deepEqual(plugin.saveCalls, []);
  await settingsIn(tab.containerEl)[0].texts[0].type("http://localhost:9000");
  assert.deepEqual(plugin.saveCalls, [{ endpoint: "http://localhost:9000" }]);
});

test("a lost response is looked up by request id, never resent", async () => {
  const app = makeApp(NOTES);
  const server = makeServer();
  const plugin = await start(app, server);
  app.workspace.active = app.file("00_Inbox/a.md");
  server.dropPostOnce = true;
  const notice = await settled(() => runCommand(plugin, "organize"), "organize after lost response");
  assert.equal(server.posts("organize").length, 1);
  assert.ok(server.lookups().length >= 1);
  assert.match(notice.message, /완료/);

  server.hangPostOnce = true;
  await settled(() => runCommand(plugin, "review-undo"), "undo after timeout");
  assert.equal(server.posts("review.undo").length, 1);
});

test("a request the Owner never recorded is resent with the same request id", async () => {
  const app = makeApp(NOTES);
  const server = makeServer();
  const plugin = await start(app, server);
  app.workspace.active = app.file("00_Inbox/a.md");
  server.losePostOnce = true;
  await settled(() => runCommand(plugin, "enhance-develop"), "develop after lost request");
  const bodies = server.calls.filter((call) => call.method === "POST" && call.path === "/api/requests").map((call) => call.body);
  assert.equal(bodies.length, 2);
  assert.equal(bodies[0], bodies[1]);
  assert.equal(server.receipts.size, 1);
});

test("error notices stay until dismissed; other notices expire", async () => {
  const app = makeApp(NOTES);
  const server = makeServer();
  const plugin = await start(app, server);
  app.workspace.active = app.file("00_Inbox/a.md");
  const done = await settled(() => runCommand(plugin, "organize"), "completed organize");
  assert.equal(done.timeout, undefined);
  server.status = "refused";
  const refused = await settled(() => runCommand(plugin, "organize"), "refused organize");
  assert.equal(refused.timeout, 0);
  assert.match(refused.message, /거부됨/);
});

test("semantic search unavailable shows a notice and no results", async () => {
  const app = makeApp(NOTES);
  const server = makeServer();
  server.searchStatus = "unavailable";
  const plugin = await start(app, server);
  const modals = ui.modals.length;
  runCommand(plugin, "semantic-search");
  const modal = await waitModal("의미 검색", modals);
  await textsIn(modal)[0].type("아무거나");
  const submit = buttonIn(modal, "요청");
  const notice = await settled(() => submit.click(), "unavailable search");
  assert.match(notice.message, /unavailable/);
  assert.ok(!ui.modals.some((item) => item instanceof SuggestModal));
});

test("proposal commands are offered only on proposal notes", async () => {
  const app = makeApp(NOTES, { [PROPOSAL]: { type: "proposal" } });
  const plugin = await start(app, makeServer());
  app.workspace.active = app.file("00_Inbox/a.md");
  for (const id of ["review-apply", "review-reject"]) {
    assert.equal(plugin.commands.find((command) => command.id === id).checkCallback(true), false);
  }
  app.workspace.active = null;
  assert.equal(plugin.commands.find((command) => command.id === "organize").checkCallback(true), false);
});

test("a non-loopback endpoint is never contacted", async () => {
  const app = makeApp(NOTES);
  app.storedData = { endpoint: "http://192.168.0.2:8765" };
  const server = makeServer();
  const plugin = await start(app, server);
  app.workspace.active = app.file("00_Inbox/a.md");
  const notice = await settled(() => runCommand(plugin, "organize"), "refused endpoint");
  assert.equal(notice.timeout, 0);
  assert.equal(server.calls.length, 0);
  assert.deepEqual(plugin.saveCalls, []);
});

test("test_SUR_6_thin_client_owner_start_stop_follow_launchd_and_report_observed_health", async () => {
  const app = makeApp(NOTES);
  const server = makeServer();
  server.down = true;
  const plugin = await start(app, server);
  await until(() => plugin.connection === "down", "initial health");
  assert.deepEqual(
    plugin.commands.filter((command) => command.id.startsWith("owner-")).map((command) => [command.id, command.name]),
    [["owner-start", "Owner 시작"], ["owner-stop", "Owner 중지"]],
  );
  server.calls.length = 0;

  // Start: kickstart the launchd service, then report ready only once /api/health says so.
  server.health = sequence("down", "down", UNREADY, READY);
  const started = await settled(() => runCommand(plugin, "owner-start"), "owner start");
  assert.deepEqual(launchctl.calls, [{ file: "launchctl", args: ["kickstart", OWNER_TARGET] }]);
  assert.match(started.message, /시작되었습니다 \(health 정상\)/);
  assert.equal(started.timeout, undefined);
  assert.equal(plugin.connection, "up");
  assert.equal(plugin.statusBarItem.text, "KOS 연결됨 · 진행 0");

  // Stop: SIGTERM the service, then report stopped only after health stops answering.
  launchctl.calls.length = 0;
  server.health = sequence(READY, READY, "down");
  const stopped = await settled(() => runCommand(plugin, "owner-stop"), "owner stop");
  assert.deepEqual(launchctl.calls, [{ file: "launchctl", args: ["kill", "SIGTERM", OWNER_TARGET] }]);
  assert.match(stopped.message, /중지되었습니다 \(health 응답 없음\)/);
  assert.equal(stopped.timeout, undefined);
  assert.equal(plugin.connection, "down");

  // Both commands only GET /api/health: no request submission, no note text, no settings written.
  assert.ok(server.calls.length > 0);
  for (const call of server.calls) {
    assert.equal(call.method, "GET");
    assert.equal(call.path, "/api/health");
    assert.equal(call.body, undefined);
  }
  assert.deepEqual(plugin.saveCalls, []);
  assert.equal(plugin.inflight, 0);
});

test("owner start reports launchd and health problems instead of success", async () => {
  const server = makeServer();
  server.down = true;
  const plugin = await start(makeApp(NOTES), server);
  await until(() => plugin.connection === "down", "initial health");
  const healthCalls = () => server.calls.filter((call) => call.path === "/api/health").length;
  const attempt = async (health, answer) => {
    server.calls.length = 0;
    launchctl.calls.length = 0;
    server.health = health;
    launchctl.answer = answer || (() => ({}));
    return settled(() => runCommand(plugin, "owner-start"), "owner start");
  };

  let notice = await attempt(sequence(READY));
  assert.match(notice.message, /이미 실행 중입니다/);
  assert.equal(notice.timeout, undefined);
  assert.deepEqual(launchctl.calls, []);

  notice = await attempt(sequence(UNREADY));
  assert.match(notice.message, /이미 실행 중이지만 준비되지 않았습니다 \(blockers: vault_not_found\)/);
  assert.equal(notice.timeout, 0);
  assert.deepEqual(launchctl.calls, []);

  notice = await attempt(sequence("down"), () => ({
    code: 113, stderr: `Could not find service "${OWNER_LABEL}" in domain for user gui: 501`,
  }));
  assert.match(notice.message, /launchd에 com\.agentfabric\.knowledgeos\.owner 서비스가 등록되어 있지 않습니다/);
  assert.match(notice.message, /knowledgeos install service/);
  assert.equal(notice.timeout, 0);
  assert.equal(healthCalls(), 1, "no waiting after a failed kickstart");

  notice = await attempt(sequence("down"), () => ({ code: 5, stderr: "Input/output error" }));
  assert.match(notice.message, /launchctl 실패 \(코드 5\): Input\/output error/);
  assert.equal(notice.timeout, 0);

  notice = await attempt(sequence("down"), () => ({ code: "ENOENT" }));
  assert.match(notice.message, /launchctl을 실행할 수 없습니다/);
  assert.equal(notice.timeout, 0);

  notice = await attempt(sequence("down"));
  assert.match(notice.message, /health 응답이 없습니다/);
  assert.doesNotMatch(notice.message, /시작되었습니다/);
  assert.equal(notice.timeout, 0);
  assert.ok(healthCalls() > 1 && healthCalls() < 200, "waiting is bounded");

  notice = await attempt(sequence("down", UNREADY));
  assert.match(notice.message, /준비되지 않았습니다 \(blockers: vault_not_found\)/);
  assert.doesNotMatch(notice.message, /시작되었습니다/);
  assert.equal(notice.timeout, 0);
  assert.equal(plugin.inflight, 0);
});

test("owner stop never claims a stop it did not observe", async () => {
  const server = makeServer();
  server.down = true;
  const plugin = await start(makeApp(NOTES), server);
  await until(() => plugin.connection === "down", "initial health");
  const healthCalls = () => server.calls.filter((call) => call.path === "/api/health").length;
  const attempt = async (health, answer) => {
    server.calls.length = 0;
    launchctl.calls.length = 0;
    server.health = health;
    launchctl.answer = answer || (() => ({}));
    return settled(() => runCommand(plugin, "owner-stop"), "owner stop");
  };
  const sigterm = [{ file: "launchctl", args: ["kill", "SIGTERM", OWNER_TARGET] }];

  let notice = await attempt(sequence(READY));
  assert.match(notice.message, /후에도 Owner가 응답합니다/);
  assert.doesNotMatch(notice.message, /중지되었습니다/);
  assert.equal(notice.timeout, 0);
  assert.deepEqual(launchctl.calls, sigterm);
  assert.ok(healthCalls() > 1 && healthCalls() < 200, "waiting is bounded");

  notice = await attempt(sequence("down"));
  assert.match(notice.message, /중지 여부를 확인할 수 없습니다 \(unknown\)/);
  assert.doesNotMatch(notice.message, /중지되었습니다/);
  assert.equal(notice.timeout, 0);
  assert.deepEqual(launchctl.calls, sigterm);

  notice = await attempt(sequence("down"), () => ({ code: 113, stderr: "Could not find service" }));
  assert.match(notice.message, /등록되어 있지 않습니다/);
  assert.equal(notice.timeout, 0);

  notice = await attempt(sequence("down"), () => ({ code: 3, stderr: "No such process" }));
  assert.match(notice.message, /실행 중이 아닙니다/);
  assert.equal(notice.timeout, undefined);

  notice = await attempt(sequence(READY), () => ({ code: 3, stderr: "No such process" }));
  assert.match(notice.message, /직접 실행한 Owner/);
  assert.equal(notice.timeout, 0);
  assert.equal(healthCalls(), 1, "no waiting when launchd had nothing to signal");

  notice = await attempt(sequence(READY), () => ({ code: 1, stderr: "Operation not permitted" }));
  assert.match(notice.message, /launchctl 실패 \(코드 1\): Operation not permitted/);
  assert.equal(notice.timeout, 0);
  assert.equal(plugin.inflight, 0);
});

test("owner commands run one at a time and use the configured launchd label", async () => {
  const app = makeApp(NOTES);
  const server = makeServer();
  const plugin = await start(app, server);
  await until(() => plugin.connection === "up", "initial health");
  const tab = plugin.settingTabs[0];
  tab.display();
  const label = settingsIn(tab.containerEl)[1].texts[0];
  await label.type("dev.example.owner");

  let release;
  launchctl.hold = new Promise((resolve) => { release = resolve; });
  server.health = sequence("down", READY);
  const before = ui.notices.length;
  runCommand(plugin, "owner-start");
  await until(() => launchctl.calls.length === 1, "kickstart issued");
  runCommand(plugin, "owner-stop");
  assert.match(ui.notices.at(-1).message, /이미 진행 중인 Owner 시작\/중지가 있습니다/);
  assert.match(plugin.statusBarItem.text, /진행 1$/);
  release();
  await until(() => ui.notices.length > before + 1, "start settled");
  assert.match(ui.notices.at(-1).message, /시작되었습니다/);
  assert.deepEqual(launchctl.calls, [{ file: "launchctl", args: ["kickstart", `gui/${process.getuid()}/dev.example.owner`] }]);

  // An invalid label never reaches launchctl.
  launchctl.calls.length = 0;
  await label.type("bad/label");
  const invalid = await settled(() => runCommand(plugin, "owner-start"), "invalid label");
  assert.match(invalid.message, /launchd 라벨이 올바르지 않습니다: bad\/label/);
  assert.equal(invalid.timeout, 0);
  assert.deepEqual(launchctl.calls, []);
});

test("settings tab saves the Owner label next to the endpoint, only when edited", async () => {
  const plugin = await start(makeApp(NOTES), makeServer());
  const tab = plugin.settingTabs[0];
  tab.display();
  assert.deepEqual(plugin.saveCalls, []);
  const [endpoint, label] = settingsIn(tab.containerEl);
  assert.equal(label.texts[0].value, OWNER_LABEL);
  await label.texts[0].type("dev.example.owner");
  assert.deepEqual(plugin.saveCalls, [{ ownerLabel: "dev.example.owner" }]);
  await endpoint.texts[0].type("http://localhost:9000");
  assert.deepEqual(plugin.saveCalls.at(-1), { ownerLabel: "dev.example.owner", endpoint: "http://localhost:9000" });
  await label.texts[0].type("  ");
  assert.equal(plugin.settings.ownerLabel, OWNER_LABEL);
  assert.deepEqual(plugin.saveCalls.at(-1), { ownerLabel: OWNER_LABEL, endpoint: "http://localhost:9000" });
});

function sidebarElements(el) {
  return [el, ...el.children.flatMap(sidebarElements)];
}

function sidebarButton(view, label) {
  const button = sidebarElements(view.contentEl).find((el) => el.tag === "button" && el.attributes["aria-label"] === label);
  assert.ok(button, `sidebar button ${label}`);
  return button;
}

async function sidebarSubmit(view, values) {
  const form = sidebarElements(view.body.controls).find((el) => el.tag === "form");
  assert.ok(form, "inline form");
  for (const [name, value] of Object.entries(values)) {
    const input = sidebarElements(form).find((el) => el.attributes.name === name);
    assert.ok(input, `inline field ${name}`);
    await input.type(value);
  }
  return form.submit();
}

test("sidebar keeps navigation local and submits the visible note without popups", async () => {
  const app = makeApp(NOTES);
  const server = makeServer();
  app.workspace.active = app.file("00_Inbox/a.md");
  const plugin = await start(app, server);
  await plugin.openSidebar();
  const view = app.workspace.revealed.view;
  app.workspace.active = null;
  await app.workspace.trigger("file-open", null);
  await plugin.openSidebar();
  assert.equal(app.workspace.leaves.length, 1);
  await sidebarButton(view, "정리").click();
  assert.equal(server.posts().length, 0, "selecting a function never executes it");
  await sidebarButton(view, "정리 요청").click();
  assert.deepEqual(server.posts("organize").at(-1).params, {
    paths: ["00_Inbox/a.md"], digests: { "00_Inbox/a.md": sha(NOTES["00_Inbox/a.md"]) },
  });
  const next = app.file("00_Inbox/b.md");
  await app.workspace.trigger("file-open", next);
  assert.equal(view.noteEl.text, next.basename);
  await sidebarButton(view, "강화").click();
  await sidebarButton(view, "발전 요청").click();
  assert.deepEqual(server.posts("enhance").at(-1).params, { mode: "develop", path: next.path, digest: sha(next.content) });
  await app.workspace.trigger("delete", next);
  assert.equal(sidebarButton(view, "발전 요청").disabled, true);
  assert.deepEqual(plugin.saveCalls, []);
  assert.equal(ui.modals.length, 0);
  assert.equal(ui.menus.length, 0);
  assert.equal(ui.notices.length, 0);
  assert.match(view.feedbackEl.allText, /완료/);
  plugin.onunload();
  assert.equal(app.workspace.leaves.length, 0);
});

test("sidebar inline inputs preserve drafts validate dates and honor proposal and note context", async () => {
  const frontmatter = { [PROPOSAL]: { type: "capture" } };
  const app = makeApp(NOTES, frontmatter);
  const server = makeServer();
  app.workspace.active = app.file(PROPOSAL);
  const plugin = await start(app, server);
  await plugin.openSidebar();
  const view = app.workspace.revealed.view;
  assert.equal(view.proposalEl.hidden, true);
  frontmatter[PROPOSAL].type = "proposal";
  await app.workspace.trigger("changed", app.file(PROPOSAL));
  assert.equal(view.proposalEl.hidden, false);
  await sidebarButton(view, "적용").click();
  await sidebarButton(view, "거절").click();
  for (const command of ["review.apply", "review.reject"]) {
    assert.deepEqual(server.posts(command)[0].params, { path: PROPOSAL, digest: sha(NOTES[PROPOSAL]) });
  }
  await sidebarButton(view, "강화").click();
  await sidebarButton(view, "돌아보기").click();
  await sidebarSubmit(view, { from: "2026-11-20", to: "2026-11-01" });
  assert.equal(server.posts("enhance").length, 0);
  assert.match(view.feedbackEl.allText, /시작일/);
  await sidebarSubmit(view, { from: "2026-11-01", to: "2026-11-19" });
  assert.deepEqual(server.posts("enhance")[0].params.window, { from: "2026-11-01", to: "2026-11-19" });
  await app.workspace.trigger("file-open", app.file("40_Knowledge/Ideas/image.png"));
  assert.equal(view.proposalEl.hidden, true);
  await sidebarButton(view, "정리").click();
  assert.equal(sidebarButton(view, "정리 요청").disabled, true);
  await sidebarButton(view, "강화").click();
  await sidebarButton(view, "묻기").click();
  await sidebarSubmit(view, { question: "   " });
  assert.equal(server.posts("enhance").length, 1);
  const input = sidebarElements(view.body.controls).find((el) => el.attributes.name === "question");
  assert.equal(input.attributes["aria-invalid"], "true");
  await input.type("노트 없는 질문");
  await sidebarButton(view, "찾기").click();
  await sidebarButton(view, "강화").click();
  assert.equal(sidebarElements(view.body.controls).find((el) => el.attributes.name === "question").value, "노트 없는 질문");
  await sidebarSubmit(view, {});
  assert.deepEqual(server.posts("enhance").at(-1).params, { mode: "ask", question: "노트 없는 질문" });
  assert.equal(ui.modals.length, 0);
});

test("sidebar renders search results and utility feedback inline with existing transport and lifecycle", async () => {
  const app = makeApp(NOTES);
  const server = makeServer();
  const note = app.file("00_Inbox/a.md");
  app.workspace.active = note;
  const plugin = await start(app, server);
  await plugin.openSidebar();
  const view = app.workspace.revealed.view;
  await sidebarSubmit(view, { query: "연결" });
  const searchCall = server.calls.filter((call) => call.path === "/api/search").at(-1);
  assert.deepEqual(JSON.parse(searchCall.body), { mode: "semantic", query: "연결", limit: 20 });
  const link = sidebarElements(view.body.output).find((el) => el.tag === "a");
  assert.equal(link.text, "Beta");
  await link.click();
  assert.deepEqual(app.workspace.opened.at(-1), { path: "00_Inbox/b.md", state: { eState: { line: 2 } } });
  await sidebarButton(view, "관련 노트").click();
  await sidebarButton(view, "관련 노트 찾기").click();
  assert.deepEqual(JSON.parse(server.calls.filter((call) => call.path === "/api/search").at(-1).body), {
    mode: "related", path: note.path, limit: 20,
  });
  server.searchStatus = "unavailable";
  await sidebarButton(view, "관련 노트 찾기").click();
  assert.match(view.feedbackEl.allText, /unavailable/);
  assert.equal(sidebarElements(view.body.output).some((el) => el.tag === "a"), false, "unavailable cannot retain stale results");
  await sidebarButton(view, "더보기").click();
  await sidebarSubmit(view, { text: "아이디어 발전" });
  assert.deepEqual(server.posts("free_text")[0].params, { text: "아이디어 발전" });
  await sidebarButton(view, "되돌리기").click();
  assert.deepEqual(server.posts("review.undo")[0].params, { path: note.path, digest: sha(note.content) });
  await sidebarButton(view, "관리 콘솔").click();
  assert.deepEqual(ui.opened, ["http://127.0.0.1:8765/"]);
  assert.deepEqual(launchctl.calls, []);
  launchctl.answer = () => { server.down = true; return {}; };
  await sidebarButton(view, "Owner 중지").click();
  assert.deepEqual(launchctl.calls.at(-1).args, ["kill", "SIGTERM", OWNER_TARGET]);
  assert.equal(sidebarButton(view, "Owner 시작").disabled, false);
  launchctl.answer = () => { server.down = false; return {}; };
  await sidebarButton(view, "Owner 시작").click();
  assert.deepEqual(launchctl.calls.at(-1).args, ["kickstart", OWNER_TARGET]);
  assert.equal(ui.modals.length, 0);
  assert.equal(ui.menus.length, 0);
  assert.equal(ui.notices.length, 0);
});

test("sidebar preview requires one explicit decision and cancels previews on navigation including late responses", async () => {
  const app = makeApp(NOTES);
  const server = makeServer();
  app.workspace.active = app.file("00_Inbox/a.md");
  const plugin = await start(app, server);
  await plugin.openSidebar();
  const view = app.workspace.revealed.view;
  await sidebarButton(view, "규격화").click();
  let pending = sidebarButton(view, "미리보기").click();
  await until(() => view.cancelPreview, "inline preview");
  assert.match(view.body.output.allText, /\+id:/);
  assert.equal(server.posts("standardize.confirm").length, 0);
  const apply = sidebarButton(view, "적용");
  await Promise.all([apply.click(), apply.click()]);
  await pending;
  assert.equal(server.posts("standardize.confirm").length, 1);
  pending = sidebarButton(view, "미리보기").click();
  await until(() => view.cancelPreview, "second inline preview");
  const previewId = server.posts("standardize.preview").at(-1).request_id;
  await sidebarButton(view, "찾기").click();
  await pending;
  assert.equal(server.posts("standardize.cancel").length, 1);
  assert.equal(server.posts("standardize.cancel")[0].params.preview_id, server.receipts.get(previewId).view().outcome.preview_id);
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const originalFetch = server.fetch;
  ui.transport = async (url, init) => {
    const response = await originalFetch(url, init);
    if (init.body && JSON.parse(init.body).command === "standardize.preview") await gate;
    return response;
  };
  await sidebarButton(view, "규격화").click();
  pending = sidebarButton(view, "미리보기").click();
  await until(() => server.posts("standardize.preview").length === 3, "pending preview response");
  await app.workspace.trigger("file-open", app.file("00_Inbox/b.md"));
  release();
  await pending;
  assert.equal(server.posts("standardize.confirm").length, 1);
  assert.equal(server.posts("standardize.cancel").length, 2);
  assert.equal(view.cancelPreview, null);
  assert.doesNotMatch(view.body.output.allText, /00_Inbox\/a/);
  assert.equal(ui.modals.length, 0);
});
