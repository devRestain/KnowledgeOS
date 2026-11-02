const { ItemView, MarkdownView, Modal, Notice, Plugin, PluginSettingTab, Setting, requestUrl, setIcon } = require("obsidian");
const { execFile } = require("node:child_process");
const { homedir } = require("node:os");
const { join } = require("node:path");

// These are existing, configured commands, not plugin implementation APIs.
const COMMAND_ACTIONS = [
  { id: "capture", label: "Capture thought", group: "Capture", plugin: "quickadd", command: "quickadd:choice:f29c95dc-1f39-4ff0-81bd-2ef12181bdcf", description: "Run the configured QuickAdd capture; creates a local Inbox note." },
  { id: "question", label: "New question", group: "Capture", plugin: "quickadd", command: "quickadd:choice:5eddd4fc-24b8-4189-b926-c91f34b20ce1", description: "Run the configured QuickAdd question template." },
  { id: "idea", label: "New idea", group: "Capture", plugin: "quickadd", command: "quickadd:choice:9bba3ca7-d9c7-4df3-8ab4-425ddd7da0a6", description: "Run the configured QuickAdd idea template." },
  { id: "project", label: "New project", group: "Capture", plugin: "quickadd", command: "quickadd:choice:dc798d84-85be-465e-b461-c4cc13d198aa", description: "Run the configured QuickAdd project template." },
  { id: "knowledge", label: "New knowledge note", group: "Capture", plugin: "quickadd", command: "quickadd:choice:c44007fc-f3e5-47f0-b295-d9c99cc5aa9b", description: "Run the configured QuickAdd knowledge template." },
  { id: "daily-note", label: "Daily note", group: "Journal", core: "daily-notes", command: "daily-notes", description: "Open today's Daily Note with the configured Core Daily Notes template." },
  { id: "weekly-note", label: "Weekly note", group: "Journal", plugin: "notebook-navigator", command: "notebook-navigator:open-weekly-note", description: "Open this week's note with Notebook Navigator; its configured creation flow may create a note." },
  { id: "monthly-note", label: "Monthly note", group: "Journal", plugin: "notebook-navigator", command: "notebook-navigator:open-monthly-note", description: "Open this month's note with Notebook Navigator; its configured creation flow may create a note." },
  { id: "task-search", label: "Search tasks", group: "Tasks", plugin: "obsidian-tasks-plugin", command: "obsidian-tasks-plugin:quick-search", description: "Open Tasks quick search; note tasks are separate from Owner Work." },
  { id: "task-edit", label: "Edit task at cursor", group: "Tasks", plugin: "obsidian-tasks-plugin", command: "obsidian-tasks-plugin:edit-task", context: "task", description: "Open the Tasks editor for the #task checkbox line at the current Markdown cursor; saving changes the note." },
  { id: "task-toggle", label: "Toggle task at cursor", group: "Tasks", plugin: "obsidian-tasks-plugin", command: "obsidian-tasks-plugin:toggle-done", context: "task", description: "Change completion of the #task checkbox line at the current Markdown cursor. This edits the note, not Owner Work." },
  { id: "relations-tree", label: "Relations tree", group: "Relations", plugin: "breadcrumbs", command: "breadcrumbs:open-tree-view", context: "note", description: "Open Breadcrumbs tree for the active note; navigation is not an Owner-approved relation." },
  { id: "relations-matrix", label: "Relations matrix", group: "Relations", plugin: "breadcrumbs", command: "breadcrumbs:open-matrix-view", context: "note", description: "Open Breadcrumbs matrix for the active note; navigation is not an Owner-approved relation." },
];

const FILE_ACTIONS = [
  { id: "home", label: "Home", group: "Library", path: "Home.md" },
  { id: "inbox-base", label: "Inbox Base", group: "Bases", path: "99_System/Bases/Inbox.base" },
  { id: "knowledge-base", label: "Knowledge Base", group: "Bases", path: "99_System/Bases/Knowledge.base" },
  { id: "projects-base", label: "Projects Base", group: "Bases", path: "99_System/Bases/Projects.base" },
  { id: "sources-base", label: "Sources Base", group: "Bases", path: "99_System/Bases/Sources.base" },
  { id: "decisions-base", label: "Decisions Base", group: "Bases", path: "99_System/Bases/Decisions.base" },
  { id: "compass-base", label: "Compass Base", group: "Bases", path: "99_System/Bases/Compass.base" },
  { id: "journal-base", label: "Journal Base", group: "Bases", path: "99_System/Bases/Journal.base" },
  { id: "review-base", label: "Review Base", group: "Bases", path: "99_System/Bases/Review.base" },
  { id: "tasks-dashboard", label: "Tasks dashboard", group: "Dashboards", path: "99_System/Dashboards/Tasks.md" },
  { id: "today-dashboard", label: "Today focus", group: "Dashboards", path: "99_System/Dashboards/Today_Focus.md" },
  { id: "weekly-dashboard", label: "Weekly review", group: "Dashboards", path: "99_System/Dashboards/Weekly_Review.md" },
];

const VIEW_COMMANDS = [
  { id: "open-work-review", name: "Open KnowledgeOS sidebar", section: "work" },
  { id: "open-search", name: "Search KnowledgeOS", section: "search" },
  { id: "open-note-work", name: "Open current note Work", section: "work", note: true },
  { id: "open-pending", name: "Review Owner Pending", section: "pending" },
  { id: "open-daily-review", name: "Open daily enrichment review", section: "daily" },
  { id: "request-metadata", name: "Prepare metadata and relations request", section: "metadata", note: true },
  { id: "request-enhancement", name: "Prepare document enhancement request", section: "enhancement", note: true },
  { id: "open-library", name: "Open document library and integrations", section: "library" },
];

function markdownPath(file) {
  return file && file.extension === "md" && typeof file.path === "string" ? file.path : null;
}

function activeMarkdownView(app) {
  return app.workspace.getActiveViewOfType(MarkdownView);
}

function activeNotePath(app) {
  return markdownPath(app.workspace.getActiveFile());
}

function pluginLoaded(app, id) {
  if (app.plugins?.enabledPlugins && !app.plugins.enabledPlugins.has(id)) return false;
  return Boolean(app.plugins?.getPlugin?.(id) || app.plugins?.plugins?.[id]);
}

function unavailableReason(app, action) {
  if (action.path) {
    const file = app.vault.getAbstractFileByPath(action.path);
    if (!file || !["md", "base"].includes(file.extension)) {
      return `Restore the configured in-Vault file ${action.path}. No file was created.`;
    }
    if (file.extension === "base" && !app.internalPlugins?.getPluginById?.("bases")?.enabled) {
      return "Enable Obsidian's Bases core plugin in Settings → Core plugins.";
    }
    return null;
  }
  if (action.core) {
    if (!app.internalPlugins?.getPluginById?.(action.core)?.enabled) return `Enable ${action.core} in Settings → Core plugins, then retry.`;
  } else if (!pluginLoaded(app, action.plugin)) {
    return `Enable ${action.plugin} in Settings → Community plugins, then retry.`;
  }
  const command = app.commands?.commands?.[action.command];
  if (!command) {
    return `Command ${action.command} is unavailable. Check the plugin version and configured command-enabled choices.`;
  }
  if (action.context === "note" && !activeNotePath(app)) {
    return "Open a Markdown note first, then retry this relation command.";
  }
  if (action.context === "task") {
    const view = activeMarkdownView(app);
    const line = view?.editor?.getLine(view.editor.getCursor().line) || "";
    if (!markdownPath(view?.file) || view.getMode?.() === "preview" || !/^\s*(?:[-*+]|\d+[.)])\s+\[[^\]]\]\s+/.test(line) || !/(?:^|\s)#task(?=\s|$|\/)/.test(line)) {
      return "Place the cursor on a Markdown checkbox line containing the configured #task tag, then retry.";
    }
  }
  return null;
}

function integrationActions(app) {
  return [...COMMAND_ACTIONS, ...FILE_ACTIONS].map((action) => {
    const reason = unavailableReason(app, action);
    const description = action.description || `Open ${action.path} inside this Vault; does not create or modify the file.`;
    return { id: action.id, label: action.label, group: action.group, available: !reason,
      description: reason ? `${description} Unavailable: ${reason}` : description };
  });
}

async function executeIntegration(app, id) {
  const action = [...COMMAND_ACTIONS, ...FILE_ACTIONS].find((entry) => entry.id === id);
  if (!action) {
    new Notice(`Unknown KnowledgeOS integration: ${id}. Choose an action from the document library.`);
    return false;
  }
  const reason = unavailableReason(app, action);
  if (reason) {
    new Notice(`${action.label}: ${reason}`);
    return false;
  }
  try {
    if (action.path) {
      // Reuse an actual file rather than openLinkText, which can create missing notes.
      const file = app.vault.getAbstractFileByPath(action.path);
      await app.workspace.getLeaf("tab").openFile(file);
    } else {
      const executed = await app.commands.executeCommandById(action.command);
      if (executed === false) {
        new Notice(`${action.label} is not available in the current view. Open the appropriate note or editor and retry.`);
        return false;
      }
    }
    return true;
  } catch (error) {
    new Notice(`${action.label} failed: ${error.message || error}. Check the configured plugin or destination and retry.`);
    return false;
  }
}

async function openSection(plugin, command, path = null) {
  if (command.note && !path) {
    new Notice("Open a Markdown note first, then use this KnowledgeOS command. No Work was submitted.");
    return;
  }
  try {
    // Opening metadata/enhancement prepares a form only. Submission belongs to the view.
    await plugin.activateView(command.section, path);
  } catch (error) {
    new Notice(`Could not open KnowledgeOS: ${error.message || error}`);
  }
}

function addNoteMenu(plugin, menu, file) {
  const path = markdownPath(file);
  if (!path) return;
  const ids = new Set(["open-note-work", "open-pending", "request-metadata", "request-enhancement"]);
  menu.addSeparator();
  for (const command of VIEW_COMMANDS.filter((item) => ids.has(item.id))) {
    menu.addItem((item) => item.setTitle(`KnowledgeOS: ${command.name}`)
      .setIcon(command.section === "pending" ? "inbox" : "network")
      .onClick(() => openSection(plugin, command, path)));
  }
}

function registerIntegrations(plugin) {
  for (const command of VIEW_COMMANDS) {
    plugin.addCommand({ id: command.id, name: command.name,
      callback: () => openSection(plugin, command, command.note ? activeNotePath(plugin.app) : null) });
  }
  plugin.registerEvent(plugin.app.workspace.on("file-menu", (menu, file) => addNoteMenu(plugin, menu, file)));
  plugin.registerEvent(plugin.app.workspace.on("editor-menu", (menu, _editor, view) => addNoteMenu(plugin, menu, view.file)));
}

const DEFAULT_SETTINGS = { ownerEndpoint: "http://127.0.0.1:17900/owner", lastWorkRunId: "" };
const VIEW_TYPE = "knowledgeos-owner-sidebar";
const json = (value) => JSON.stringify(value, null, 2);
const decisionValue = (value) => value?.decision || value?.response?.chosen_action || value?.receipt?.chosen_action;

function endpoint(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !url.port || url.username ||
      url.password || url.search || url.hash || url.pathname !== "/owner") {
    throw new Error("Owner 주소는 http://127.0.0.1:<port>/owner 형식이어야 합니다.");
  }
  return url.origin + url.pathname;
}

function backendResult(stdout) {
  const invalid = () => new Error("로컬 백엔드 helper의 응답 규약이 올바르지 않습니다. Runtime/Worker 상태는 미관측입니다.");
  if (typeof stdout !== "string" || new TextEncoder().encode(stdout).length > 65536) throw invalid();
  let result;
  try { result = JSON.parse(stdout); } catch (_) { throw invalid(); }
  const keys = ["version", "operation", "state", "owner_endpoint", "owner_http", "runtime_observation", "error"];
  if (!result || typeof result !== "object" || Array.isArray(result) ||
      Object.keys(result).length !== keys.length || !keys.every(key => Object.hasOwn(result, key)) ||
      result.version !== 1 || result.operation !== "knowledgeos" || result.runtime_observation !== "unavailable" ||
      !["owner_connected", "starting", "unreachable", "blocked", "failed"].includes(result.state) ||
      !["reachable", "unreachable", "refused", "unavailable"].includes(result.owner_http)) throw invalid();
  if (result.owner_endpoint !== null) {
    try { if (endpoint(result.owner_endpoint) !== result.owner_endpoint) throw invalid(); }
    catch (_) { throw invalid(); }
  }
  const accepted = ["owner_connected", "starting"].includes(result.state);
  if (accepted ? result.error !== null : !result.error || typeof result.error !== "object" ||
      Array.isArray(result.error) || Object.keys(result.error).length !== 2 ||
      typeof result.error.code !== "string" || !result.error.code || result.error.code.length > 128 ||
      typeof result.error.message !== "string" || !result.error.message || result.error.message.length > 4096) throw invalid();
  if (accepted && result.owner_endpoint === null ||
      result.state === "owner_connected" && result.owner_http !== "reachable" ||
      result.state !== "owner_connected" && result.owner_http === "reachable") throw invalid();
  return result;
}

function localBackend(operation) {
  if (!["status", "start"].includes(operation)) return Promise.reject(new Error("지원하지 않는 로컬 백엔드 동작입니다."));
  return new Promise((resolve, reject) => {
    execFile(join(homedir(), ".local", "bin", "knowledgeos-local"), [operation, "--json"],
      { shell: false, encoding: "utf8", timeout: 65000, maxBuffer: 65536 }, (error, stdout) => {
        if (error?.code === "ENOENT") return reject(new Error("로컬 백엔드 helper가 설치되어 있지 않습니다. Operation 관리 설치를 확인하세요."));
        if (error?.killed || error?.code === "ETIMEDOUT") return reject(new Error("로컬 백엔드 helper 응답 시간이 만료되었습니다. 백엔드를 중단하거나 자동 재시도하지 않습니다. Owner 새로 조회로 확인하세요."));
        if (error && !Number.isInteger(error.code)) return reject(new Error("로컬 백엔드 helper 실행 실패: " + (error.code || "실행 불가")));
        try {
          const result = backendResult(stdout);
          const accepted = ["owner_connected", "starting"].includes(result.state);
          if (accepted === Boolean(error)) throw new Error("로컬 백엔드 helper 종료 코드와 응답 상태가 일치하지 않습니다.");
          resolve(result);
        } catch (failure) { reject(failure); }
      });
  });
}

async function sha256(text) {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (item) => item.toString(16).padStart(2, "0")).join("");
}

async function ownerCall(settings, route, payload = {}) {
  const url = `${endpoint(settings.ownerEndpoint)}/${route}`;
  const body = JSON.stringify(payload);
  if (new TextEncoder().encode(body).length > 16384) throw new Error("Owner 요청은 UTF-8 16384바이트 이하여야 합니다.");
  let response;
  try {
    response = await requestUrl({ url, method: "POST", headers: { "Content-Type": "application/json" }, body, throw: false });
    const data = response.json;
    if (response.status < 200 || response.status >= 300 || !data || data.status === "FAIL") {
      const detail = data?.errors?.[0];
      const error = new Error(`${detail?.code || response.status}: ${detail?.message || data?.message || "Owner 응답을 확인할 수 없습니다."}`);
      error.uncertain = response.status >= 500 || response.status >= 200 && response.status < 300 && !data;
      throw error;
    }
    return data;
  } catch (error) {
    if (!response || response.status >= 500 || response.status >= 200 && response.status < 300 && error.uncertain === undefined) error.uncertain = true;
    throw error;
  }
}

function confirmAction(app, text) {
  return new Promise((resolve) => {
    class Confirmation extends Modal {
      onOpen() {
        this.contentEl.createEl("h2", { text: "결정 확인" });
        this.contentEl.createEl("p", { text });
        const row = this.contentEl.createDiv({ cls: "knowledgeos-actions" });
        row.createEl("button", { text: "돌아가기" }).onclick = () => this.close();
        const accept = row.createEl("button", { text: "확인", cls: "mod-warning" });
        accept.onclick = () => { this.accepted = true; this.close(); };
      }
      onClose() { resolve(this.accepted === true); }
    }
    new Confirmation(app).open();
  });
}

class StaleView extends Error {}

class WorkReviewSidebar extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.selectedPath = null;
    this.section = "work";
    this.mode = "lexical";
    this.query = "";
    this.epoch = 0;
    this.searchRequest = 0;
    this.workListRequest = 0;
    this.workResultRequest = 0;
    this.proposalRequest = 0;
    this.dailyRequest = 0;
    this.alive = true;
    this.owner = null;
    this.requestIds = plugin.requestIds || (plugin.requestIds = new Map());
    this.methods = [];
    plugin.unknownMutations ||= new Map();
    this.observedEndpoint = plugin.settings.ownerEndpoint;
  }
  get unknownRequest() {
    const prefix = this.plugin.settings.ownerEndpoint + "\n";
    const targets = [this.proposal?.source_path, this.currentDaily?.path, this.selectedPath,
      this.work?.run?.work_run_id ? "work:" + this.work.run.work_run_id : null].filter(Boolean);
    return [...this.plugin.unknownMutations.entries()].find(([key, request]) =>
      key.startsWith(prefix) && targets.includes(request.path))?.[1];
  }
  get uncertain() { return !!this.unknownRequest; }
  getViewType() { return VIEW_TYPE; }
  getDisplayText() { return "KnowledgeOS"; }
  getIcon() { return "network"; }
  async onOpen() {
    this.alive = true;
    this.selectedPath = this.app.workspace.getActiveFile()?.path || null;
    this.contentEl.addClass("knowledgeos-sidebar");
    this.render();
    this.registerEvent(this.app.workspace.on("file-open", (file) => this.setTarget(file?.path || null)));
    this.registerEvent(this.app.workspace.on("active-leaf-change", (leaf) => {
      if (leaf === this.leaf) return;
      const file = this.app.workspace.getActiveFile();
      if (file || leaf?.view?.getViewType() === "empty") this.setTarget(file?.path || null);
    }));
    this.registerInterval(setInterval(() => this.sync(), 1000));
    await this.run(() => this.refreshOwner());
    if (this.owner && this.selectedPath) await this.run(() => this.showWorkList());
  }
  async onClose() { this.disconnect(); this.alive = false; }
  current(epoch) {
    if (!this.alive || epoch !== this.epoch) throw new StaleView("이전 화면 응답을 폐기했습니다.");
  }
  async call(route, payload = {}, mutation = false) {
    if (this.observedEndpoint !== this.plugin.settings.ownerEndpoint) {
      this.sync();
      throw new StaleView("Owner 주소가 바뀌었습니다. 새로 조회한 뒤 요청하세요.");
    }
    const epoch = this.epoch;
    const selectedEndpoint = endpoint(this.plugin.settings.ownerEndpoint);
    const current = () => {
      this.current(epoch);
      if (this.plugin.settings.ownerEndpoint !== selectedEndpoint) throw new StaleView("Owner 주소가 변경되어 이전 응답을 폐기했습니다.");
    };
    current();
    if (!this.owner && route !== "owner/status") throw new Error("Owner 새로 조회로 로컬 연결을 확인하세요.");
    const affectedPath = payload.scope?.path || this.proposal?.source_path || this.currentDaily?.path ||
      (payload.work_run_id ? "work:" + payload.work_run_id : this.selectedPath);
    if (mutation && [...this.plugin.unknownMutations.entries()].some(([key, request]) =>
      key.startsWith(selectedEndpoint + "\n") && request.path === affectedPath)) {
      throw new Error("이 문서의 앞선 요청 응답이 불명입니다. 요청 응답 대조로 같은 ID를 조회하세요.");
    }
    try {
      const result = await ownerCall(this.plugin.settings, route, payload);
      current();
      return result;
    } catch (error) {
      if (mutation && error.uncertain) {
        this.plugin.unknownMutations.set(selectedEndpoint + "\n" + affectedPath, {
          route, path: affectedPath, payload, request_id: payload.request_id, command_id: payload.command_id,
          work_run_id: payload.work_run_id, proposal_reference: payload.proposal_reference, effect_id: payload.effect_id });
      }
      current();
      throw error;
    }
  }
  async reconcileMutation() {
    const request = this.unknownRequest;
    if (!request) return;
    const selectedEndpoint = this.plugin.settings.ownerEndpoint;
    const result = await this.call("mutation/receipt", { route: request.route, request: request.payload });
    if (!result.found) {
      this.message("정확한 완료·접수 영수증이 아직 없습니다. 이 문서의 요청만 보류하며 다른 문서는 계속 사용할 수 있습니다.", true);
      return;
    }
    const key = selectedEndpoint + "\n" + request.path;
    if (this.plugin.unknownMutations.get(key) === request) this.plugin.unknownMutations.delete(key);
    this.message("기존 요청의 Owner 영수증을 확인했습니다. 요청을 재전송하지 않았습니다.");
    this.sync();
    return result;
  }
  async run(action) {
    if (this.busy || !this.alive) return;
    const epoch = this.epoch;
    this.busy = true;
    this.sync();
    try { await action(); }
    catch (error) {
      if (!(error instanceof StaleView) && this.alive && epoch === this.epoch) this.message(error.message, true);
    } finally { this.busy = false; if (this.alive) this.sync(); }
  }
  message(text, error = false) {
    if (!this.statusEl) return;
    this.statusEl.textContent = text;
    this.statusEl.classList.toggle("knowledgeos-error", error);
  }
  button(parent, text, action, binding = null, mutation = false, presentation = {}) {
    const label = presentation.label ?? text;
    const button = parent.createEl("button", {
      cls: label ? "knowledgeos-button" : "knowledgeos-button knowledgeos-icon-button",
      attr: { type: "button", "aria-label": label || text, title: text },
    });
    if (presentation.icon) {
      const icon = button.createSpan({ cls: "knowledgeos-button-icon", attr: { "aria-hidden": "true" } });
      setIcon(icon, presentation.icon);
    }
    if (label) button.createSpan({ text: label, cls: "knowledgeos-button-label" });
    if (binding) button.dataset.binding = binding;
    if (mutation) button.dataset.mutation = "true";
    button.onclick = () => this.run(action);
    return button;
  }
  field(parent, label, tag = "input", attrs = {}) {
    const wrapper = parent.createEl("label", { cls: "knowledgeos-field" });
    wrapper.createSpan({ text: label });
    return wrapper.createEl(tag, { attr: attrs });
  }
  panel(name, heading) {
    const section = this.contentEl.createEl("section", { cls: `knowledgeos-panel knowledgeos-panel-${name}` });
    section.hidden = name !== this.section;
    section.createEl("h3", { text: heading });
    this.panels[name] = section;
    return section;
  }
  output(parent, cls, heading) {
    const section = parent.querySelector(`.${cls}`) || parent.createDiv({ cls });
    section.empty();
    if (heading) section.createEl("h4", { text: heading });
    return section;
  }
  facts(parent, values) {
    const list = parent.createEl("dl", { cls: "knowledgeos-facts" });
    for (const [key, value] of values) {
      list.createEl("dt", { text: key });
      list.createEl("dd", { text: value == null ? "미제공" : String(value) });
    }
  }
  writeAllowed(binding) { return !!this.owner && !this.owner.read_only && !this.uncertain && !!this.owner.bindings?.[binding]; }
  requireWrite(binding) {
    if (!this.writeAllowed(binding)) throw new Error("이 Owner의 현재 바인딩·권한 또는 응답 불명 상태 때문에 요청할 수 없습니다.");
  }
  sync() {
    if (!this.contentEl) return;
    if (this.observedEndpoint !== this.plugin.settings.ownerEndpoint) {
      this.observedEndpoint = this.plugin.settings.ownerEndpoint;
      this.disconnect();
      return;
    }
    if (this.startButton) this.startButton.disabled = !!this.busy || !!this.plugin.backendStartActive;
    for (const button of this.contentEl.querySelectorAll("button[data-binding]")) {
      const binding = button.dataset.binding;
      button.disabled = !!this.busy || !this.owner || !this.owner.bindings?.[binding] ||
        button.dataset.mutation === "true" && (this.owner.read_only || this.uncertain);
      if (button.dataset.fresh === "proposal") button.disabled ||= !this.proposal;
      if (button.dataset.fresh === "daily") button.disabled ||= !this.currentDaily;
      if (button.dataset.fresh === "target") button.disabled ||= !this.target || !!this.work?.run?.pending_action_ids?.length;
      if (button.dataset.fresh === "steering") button.disabled ||= !this.steering;
      if (button.dataset.choice === "approve") button.disabled ||= !!this.proposal?.decision || !!this.proposal?.source_stale;
      if (button.dataset.choice === "reject") button.disabled ||= !!this.proposal?.decision;
      if (button.dataset.choice === "apply") button.disabled ||= !this.proposal?.apply_allowed || this.proposal?.source_stale || decisionValue(this.proposal?.decision) !== "approve";
      if (button.dataset.choice === "daily") button.disabled ||= this.currentDaily?.review_status !== "pending";
      if (button.dataset.choice === "steer") button.disabled ||= !this.steeringArtifact;
    }
    if (this.reconcileButton) this.reconcileButton.hidden = !this.uncertain;
    if (this.uncertain && this.statusEl) this.message("이 문서의 요청 응답 불명 · 요청 응답 대조로 확인하세요. 다른 문서는 계속 사용할 수 있습니다.", true);
    if (this.ownerEl && this.owner) this.ownerEl.textContent = `${this.owner.read_only ? "읽기 전용" : "Owner 연결됨"} · Worker 미관측`;
    if (this.ownerDetails) this.ownerDetails.textContent = this.owner ? json(this.owner) : "Owner 미연결";
    if (this.section === "library") for (const action of integrationActions(this.app)) {
      const button = this.contentEl.querySelector(`button[data-integration="${action.id}"]`);
      if (!button) continue;
      button.disabled = !!this.busy || !action.available;
      button.title = `${action.label} · ${action.description}`;
      button.parentElement.querySelector("details").hidden = action.available;
      button.parentElement.querySelector("small").textContent = action.available ? "" : action.description;
    }
  }
  render() {
    const root = this.contentEl;
    root.empty();
    const header = root.createDiv({ cls: "knowledgeos-header" });
    const identity = header.createDiv({ cls: "knowledgeos-identity" });
    identity.createEl("h2", { text: "KnowledgeOS" });
    identity.createEl("small", { cls: "knowledgeos-version", text: this.plugin.manifest?.version || "0.5.1" });
    const connection = header.createDiv({ cls: "knowledgeos-actions" });
    this.button(connection, "Owner 새로 조회", () => this.refreshOwner(), null, false, { icon: "refresh-cw", label: "" });
    this.startButton = this.button(connection, "로컬 백엔드 시작", () => this.startLocalBackend(), null, false, { icon: "power", label: "" });
    const clear = this.button(connection, "연결 해제", () => this.disconnect(), null, false, { icon: "unplug", label: "" });
    clear.onclick = () => this.disconnect();
    this.reconcileButton = this.button(connection, "요청 응답 대조", () => this.reconcileMutation(), null, false, { icon: "receipt-text", label: "" });
    root.createEl("p", { cls: "knowledgeos-target", text: this.selectedPath || "활성 노트 없음" });
    const details = root.createEl("details", { cls: "knowledgeos-connection" });
    const summary = details.createEl("summary", { attr: { "aria-label": "연결 상세", title: "연결 상세" } });
    this.ownerEl = summary.createSpan({ text: "Owner 미연결" });
    details.createEl("h4", { text: "연결 상세" });
    this.ownerDetails = details.createEl("pre");
    this.statusEl = root.createEl("p", { cls: "knowledgeos-notice", attr: { role: "status", "aria-live": "polite", "aria-atomic": "true" } });
    const nav = root.createEl("nav", { cls: "knowledgeos-tabs", attr: { "aria-label": "KnowledgeOS 작업실 보기" } });
    for (const [id, label, icon] of [["search", "검색", "search"], ["work", "작업", "workflow"], ["pending", "Pending", "inbox"], ["daily", "일일 검토", "calendar-check"], ["library", "노트 도구", "panels-top-left"]]) {
      const button = nav.createEl("button", { cls: "knowledgeos-icon-button", attr: { type: "button", title: label, "aria-label": label, "aria-pressed": String(id === this.section) } });
      const glyph = button.createSpan({ cls: "knowledgeos-button-icon", attr: { "aria-hidden": "true" } });
      setIcon(glyph, icon);
      button.dataset.section = id;
      button.onclick = () => this.openSection(id);
    }
    this.panels = {};
    this.renderSearch(this.panel("search", "검색"));
    this.renderWork(this.panel("work", "작업"));
    const proposals = this.panel("pending", "Pending");
    this.button(proposals, "제안 조회", () => this.showProposals(), "proposal_review", false, { icon: "refresh-cw", label: "새로고침" });
    this.button(this.panel("daily", "일일 검토"), "검토 목록 조회", () => this.showDaily(), "daily_review", false, { icon: "refresh-cw", label: "새로고침" });
    this.renderLibrary(this.panel("library", "노트 도구"));
    this.sync();
  }
  renderSearch(section) {
    this.searchInput = this.field(section, "검색어", "input", { type: "search", maxlength: "512", placeholder: "검색" });
    this.searchInput.value = this.query;
    this.searchInput.oninput = () => { this.query = this.searchInput.value; };
    this.searchInput.onkeydown = (event) => { if (event.key === "Enter") { event.preventDefault(); void this.run(() => this.search()); } };
    this.modeInput = this.field(section, "검색 방식", "select");
    for (const [value, label] of [["lexical", "Lexical"], ["vector", "Vector"], ["hybrid", "Hybrid RRF"], ["ontology", "Ontology"]]) this.modeInput.createEl("option", { value, text: label });
    this.modeInput.value = this.mode;
    this.modeInput.onchange = () => { this.mode = this.modeInput.value; };
    this.button(section, "검색 실행", () => this.search(), null, false, { icon: "search", label: "검색" });
    section.createDiv({ cls: "knowledgeos-results" });
  }
  renderWork(section) {
    const actions = section.createDiv({ cls: "knowledgeos-actions" });
    this.button(actions, "메타데이터·태그·관계 제안 요청", () => this.submitTemplate("note_metadata"), "structured_templates", true, { icon: "tags", label: "메타데이터" });
    this.button(actions, "근거 기반 문서 개선 요청", () => this.submitTemplate("document_enhancement"), "structured_templates", true, { icon: "file-pen-line", label: "문서 개선" });
    this.questionInput = this.field(section, "질문", "textarea", { rows: "3", maxlength: "512" });
    this.button(section, "Director에 Research 요청", () => this.submitWork(), "director_intake", true, { icon: "send", label: "질문" });
    const selected = section.createEl("details");
    selected.createEl("summary", { text: "다른 작업" });
    this.methodInput = this.field(selected, "Method", "select");
    this.methodInfo = selected.createEl("pre", { cls: "knowledgeos-method-info" });
    this.methodInfo.textContent = "목록을 조회하세요.";
    this.methodInput.onchange = () => this.renderMethodInfo();
    this.button(selected, "Method 목록 새로 조회", () => this.showMethods(), null, false, { icon: "refresh-cw", label: "목록" });
    this.button(selected, "선택 Method에 요청", () => this.submitSelected(), "selected_methods", true, { icon: "play", label: "요청" });
    const receipt = section.createEl("details");
    receipt.createEl("summary", { text: "접수 영수증" });
    receipt.createEl("pre", { cls: "knowledgeos-intake-receipt", text: "접수 없음" });
    this.button(section, "이 노트의 작업 새로 조회", () => this.showWorkList(), null, false, { icon: "refresh-cw", label: "작업 목록" });
    section.createDiv({ cls: "knowledgeos-work-list" });
    const lookup = section.createEl("details");
    lookup.createEl("summary", { text: "ID로 열기" });
    this.workIdInput = this.field(lookup, "WorkRun ID", "input", { maxlength: "160", placeholder: "workrun.…" });
    this.workIdInput.value = this.plugin.settings.lastWorkRunId || "";
    this.button(lookup, "작업 ID로 열기", () => this.showWorkResult(this.workIdInput.value.trim()), null, false, { icon: "arrow-up-right", label: "열기" });
    section.createDiv({ cls: "knowledgeos-work-result" });
  }
  renderLibrary(section) {
    const icons = { Capture: "plus", Journal: "calendar", Tasks: "list-checks", Relations: "git-fork", Library: "house", Bases: "table", Dashboards: "layout-dashboard" };
    const actionIcons = { capture: "inbox", question: "circle-help", idea: "lightbulb", project: "folder", knowledge: "book-open", "daily-note": "calendar-days", "task-search": "search", "task-edit": "pencil", "task-toggle": "square-check", "relations-matrix": "grid-2x2" };
    const names = { capture: "Capture", question: "질문", idea: "아이디어", project: "프로젝트", knowledge: "지식", "daily-note": "일간", "weekly-note": "주간", "monthly-note": "월간", "task-search": "할 일 검색", "task-edit": "할 일 편집", "task-toggle": "완료 전환", "relations-tree": "트리", "relations-matrix": "행렬" };
    let group, grid;
    for (const action of integrationActions(this.app)) {
      if (action.group !== group) {
        group = action.group;
        section.createEl("h4", { text: group });
        grid = section.createDiv({ cls: "knowledgeos-tool-grid" });
      }
      const row = grid.createDiv({ cls: "knowledgeos-native-action" });
      const button = this.button(row, action.label, () => executeIntegration(this.app, action.id), null, false,
        { icon: actionIcons[action.id] || icons[group], label: names[action.id] || action.label.replace(/ Base$| dashboard$/g, "") });
      button.dataset.integration = action.id;
      button.title = `${action.label} · ${action.description}`;
      button.disabled = !action.available;
      const detail = row.createEl("details");
      detail.createEl("summary", { text: "사용 불가" });
      detail.hidden = action.available;
      detail.createEl("small", { text: action.available ? "" : action.description });
    }
  }
  async openSection(section = "work", path = null) {
    if (path !== null) this.setTarget(path);
    if (["metadata", "enhancement"].includes(section)) {
      this.preparedTemplate = section === "metadata" ? "note_metadata" : "document_enhancement";
      section = "work";
      this.message("요청 준비됨 · 아직 제출하지 않았습니다.");
    }
    if (!this.panels[section]) section = "work";
    if (section !== this.section) this.epoch++;
    this.section = section;
    for (const [name, panel] of Object.entries(this.panels)) panel.hidden = name !== section;
    for (const button of this.contentEl.querySelectorAll("button[data-section]")) button.setAttribute("aria-pressed", String(button.dataset.section === section));
    if (section === "search") this.searchInput.focus();
    else if (section === "work") this.questionInput.focus();
    this.sync();
    if (!this.owner) return;
    if (section === "pending") await this.run(() => this.showProposals());
    if (section === "daily") await this.run(() => this.showDaily());
  }
  setTarget(path) {
    if (path === this.selectedPath) return;
    this.epoch++;
    this.selectedPath = path;
    this.work = this.target = this.proposal = this.currentDaily = this.steering = this.steeringArtifact = null;
    this.render();
    if (this.owner) void this.run(() => this.showWorkList());
  }
  disconnect() {
    this.epoch++;
    this.owner = null;
    this.work = this.target = this.proposal = this.currentDaily = this.steering = this.steeringArtifact = null;
    this.query = "";
    this.methods = [];
    this.render();
    this.message("연결 해제됨 · 백엔드는 계속 실행됩니다.");
  }
  async refreshOwner() {
    this.epoch++;
    const epoch = this.epoch;
    this.owner = null;
    this.observedEndpoint = this.plugin.settings.ownerEndpoint;
    this.work = this.target = this.proposal = this.currentDaily = this.steering = this.steeringArtifact = null;
    this.methods = [];
    this.render();
    try { this.owner = await this.call("owner/status"); }
    catch (error) {
      if (epoch === this.epoch) { this.owner = null; this.sync(); this.message("Owner 연결 실패: " + error.message, true); }
      throw error;
    }
    this.message("Owner 연결됨");
    this.sync();
    if (Object.hasOwn(this.owner.bindings || {}, "selected_methods")) await this.showMethods();
  }
  async startLocalBackend() {
    if (this.plugin.backendStartActive || !this.alive) return;
    const epoch = this.epoch, selectedEndpoint = endpoint(this.plugin.settings.ownerEndpoint);
    const current = () => {
      this.current(epoch);
      if (this.plugin.settings.ownerEndpoint !== selectedEndpoint) throw new StaleView("Owner 주소가 바뀌어 시작 결과를 화면에 연결하지 않았습니다.");
    };
    const check = result => {
      current();
      if (result.owner_endpoint !== selectedEndpoint) throw new Error("설정된 Owner 주소와 개인 로컬 백엔드 주소가 다릅니다. 백엔드를 시작하거나 연결하지 않았습니다.");
      if (["blocked", "failed"].includes(result.state)) throw new Error(`${result.error.code}: ${result.error.message}`);
    };
    this.plugin.backendStartActive = true;
    this.sync();
    try {
      let result = await localBackend("status");
      check(result);
      if (result.state !== "owner_connected") {
        this.message("로컬 백엔드 시작을 요청합니다. Runtime/Worker 실시간 상태는 미관측입니다.");
        result = await localBackend("start");
        check(result);
      }
      for (let attempt = 0; result.state === "starting" && attempt < 3; attempt++) {
        this.message("로컬 백엔드 시작 중 · Owner HTTP만 제한적으로 다시 조회합니다. Runtime/Worker 상태는 미관측입니다.");
        await new Promise(resolve => setTimeout(resolve, 1000));
        current();
        result = await localBackend("status");
        check(result);
        if (result.state === "unreachable") result = { ...result, state: "starting" };
      }
      if (result.state === "owner_connected") {
        current();
        await this.refreshOwner();
      } else if (result.state === "starting") {
        this.message("시작 요청은 접수됐지만 Owner 연결은 아직 확인되지 않았습니다. Owner 새로 조회로 확인하세요. 자동 시작 재시도는 하지 않습니다.");
      } else {
        throw new Error(`${result.error.code}: ${result.error.message}`);
      }
    } finally {
      this.plugin.backendStartActive = false;
      if (this.alive) this.sync();
    }
  }
  async scope() {
    const path = this.selectedPath;
    const epoch = this.epoch;
    if (!path) throw new Error("먼저 Markdown 노트를 여세요.");
    const file = this.app.vault.getAbstractFileByPath(path);
    if (!file || file.extension !== "md") throw new Error("선택한 Markdown 노트가 없습니다.");
    const content_sha256 = await sha256(await this.app.vault.read(file));
    this.current(epoch);
    if (path !== this.selectedPath) throw new StaleView();
    return { path, content_sha256 };
  }
  async openPath(path, revision = null) {
    if (typeof path !== "string" || path.startsWith("/") || path.includes("\\") || path.split("/").some(part => !part || part === "." || part === "..") || path.includes(":")) throw new Error("Vault 내부 경로만 열 수 있습니다.");
    const file = this.app.vault.getAbstractFileByPath(path);
    if (!file) throw new Error("이 경로의 문서가 더 이상 Vault에 없습니다.");
    if (revision && await sha256(await this.app.vault.read(file)) !== revision.replace(/^sha256:/, "")) {
      this.message("인용 당시와 원문이 다릅니다. 현재 노트를 열며, 과거 인용이 최신 근거라는 뜻은 아닙니다.");
    }
    await this.app.workspace.getLeaf("tab").openFile(file);
  }
  async search() {
    const serial = ++this.searchRequest;
    if (!this.query.trim()) throw new Error("검색어를 입력하세요.");
    const section = this.output(this.panels.search, "knowledgeos-results", "검색 중…");
    try {
      const result = await this.call("search/query", { query: this.query.trim(), mode: this.mode });
      if (serial !== this.searchRequest) return;
      section.empty();
      section.createEl("p", { text: `${result.mode} · generation ${result.index_generation_id} · ${result.results.length}개 노트` });
      for (const item of result.results) {
        const article = section.createDiv({ cls: "knowledgeos-result-card" });
        this.button(article, item.title || item.path, () => this.openPath(item.path, item.resource_reference?.source_revision));
        article.createEl("small", { text: item.path });
        article.createEl("p", { text: item.excerpt || "" });
        article.createEl("small", { text: `${item.reason || ""} · ${item.chunk_locator || ""} · ${item.chunk_hash || ""}` });
      }
      if (!result.results.length) section.createEl("p", { text: "검색 결과가 없습니다. 다른 단어나 검색 방식을 선택하세요." });
    } catch (error) {
      if (error instanceof StaleView || serial !== this.searchRequest) return;
      section.empty(); section.createEl("p", { text: error.message }); throw error;
    }
  }
  async showMethods() {
    const result = await this.call("work/methods");
    this.methods = result.methods;
    this.methodInput.empty();
    for (const method of this.methods) {
      const option = this.methodInput.createEl("option", { text: `${method.method_id}${method.available ? "" : " · 사용 불가"}`, value: method.method_id });
      option.disabled = !method.available;
    }
    this.methodInput.value = this.methods.find(item => item.available)?.method_id || "";
    this.renderMethodInfo();
  }
  renderMethodInfo() {
    const method = this.methods.find(item => item.method_id === this.methodInput.value);
    this.methodInfo.textContent = method ? json({ purpose: method.purpose, available: method.available, reason: method.reason, owner_selected_parameters: method.parameters }) : "실행 가능한 Method 바인딩이 없습니다.";
  }
  async submitSelected() {
    this.requireWrite("selected_methods");
    const method = this.methods.find(item => item.method_id === this.methodInput.value && item.available);
    if (!method) throw new Error("현재 Owner가 실행 가능하다고 반환한 Method를 선택하세요.");
    const scope = await this.scope();
    const key = `${method.method_id}:${scope.path}:${scope.content_sha256}`;
    if (!this.requestIds.has(key)) this.requestIds.set(key, globalThis.crypto.randomUUID());
    const result = await this.call("work/intake-selected", { method_id: method.method_id, scope, request_id: this.requestIds.get(key) }, true);
    await this.acceptReceipt(result);
  }
  async submitTemplate(templateId) {
    this.requireWrite("structured_templates");
    const scope = await this.scope();
    const key = `${templateId}:${scope.path}:${scope.content_sha256}`;
    if (!this.requestIds.has(key)) this.requestIds.set(key, globalThis.crypto.randomUUID());
    const result = await this.call("work/intake-template", { template_id: templateId, scope, request_id: this.requestIds.get(key) }, true);
    await this.acceptReceipt(result);
  }
  async submitWork() {
    this.requireWrite("director_intake");
    const question = this.questionInput.value.trim();
    if (!question || question.length > 512) throw new Error("Research 요청을 1–512자로 입력하세요.");
    const result = await this.call("work/intake", { question, scope: await this.scope() }, true);
    await this.acceptReceipt(result);
  }
  async acceptReceipt(result) {
    const epoch = this.epoch;
    const id = result.work_run_id || result.run?.work_run_id || result.receipts?.[0]?.work_run_id;
    if (id) { this.plugin.settings.lastWorkRunId = id; this.workIdInput.value = id; await this.plugin.saveSettings(); }
    this.current(epoch);
    this.contentEl.querySelector(".knowledgeos-intake-receipt").textContent = json(result);
    this.message(result.reconciliation_required ? "이전 전달 결과 대조가 필요합니다. 새 요청으로 우회하지 마세요." : "Owner 접수 영수증을 받았습니다. 실행 완료·독립 평가와 원본 적용은 별도입니다.");
    await this.showWorkList();
  }
  async showWorkList() {
    const serial = ++this.workListRequest;
    const path = this.selectedPath;
    const section = this.output(this.panels.work, "knowledgeos-work-list", "이 노트의 작업");
    if (!path || this.app.vault.getAbstractFileByPath(path)?.extension !== "md") { section.createEl("p", { text: "Markdown 노트를 열면 해당 작업을 조회합니다. 정확한 ID로도 작업을 열 수 있습니다." }); return; }
    const result = await this.call("work/list", { path });
    if (serial !== this.workListRequest || path !== this.selectedPath) return;
    for (const [key, requestId] of this.requestIds) {
      const members = (result.works || []).filter(item => item.request_id === requestId);
      const expected = key.startsWith("note_metadata:") ? 2 : 1;
      if (members.length === expected && members.every(item => item.application_state === "recorded" &&
          ["completed", "failed", "cancelled"].includes(item.run_state))) this.requestIds.delete(key);
    }
    const groups = new Map();
    for (const item of result.works || []) {
      const card = section.createDiv({ cls: "knowledgeos-work-card" });
      this.button(card, item.work_run_id, () => this.showWorkResult(item.work_run_id));
      card.createEl("p", { text: `${item.team_id || "Officer"} · 접수 ${item.receipt_state} / 전달 ${item.dispatch_state} / 적용 ${item.application_state} · 실행 ${item.run_state} · 평가 ${item.evaluation_state}` });
      if (item.source_stale) card.createEl("p", { text: "원문 변경됨 · 이 Work의 과거 결과를 현재 제안으로 적용하지 않습니다." });
      if (item.proposal_reference) this.button(card, "제안 검토", () => this.showProposal(item.proposal_reference), "proposal_review", false, { icon: "inbox", label: "제안" });
      if (item.template_id === "note_metadata") {
        card.createEl("p", { text: `결합 제안 ${item.metadata_promotion_state || "pending"}${item.metadata_promotion_code ? ` · ${item.metadata_promotion_code}` : ""}` });
        const members = groups.get(item.request_id) || []; members.push(item); groups.set(item.request_id, members);
      }
    }
    for (const [request_id, members] of groups) if (members.length === 2 && members.every(item => !item.source_stale && item.evaluation_state === "passed" && !item.proposal_reference && item.metadata_promotion_state !== "no_change")) {
      this.button(section, "결합 메타데이터 제안 다시 준비", async () => {
        this.requireWrite("structured_templates");
        const result = await this.call("work/prepare-metadata", { request_id }, true);
        if (result.proposal_reference) await this.showProposal(result.proposal_reference);
        await this.showWorkList();
      }, "structured_templates", true, { icon: "rotate-cw", label: "제안 준비" });
    }
    if (!result.works?.length) section.createEl("p", { text: "이 노트로 접수된 작업이 없습니다." });
  }
  async showWorkResult(id) {
    if (!/^[a-z][a-z0-9_.-]{0,159}$/.test(id)) throw new Error("정확한 WorkRun ID를 입력하세요.");
    const serial = ++this.workResultRequest;
    const epoch = this.epoch;
    this.work = this.target = this.steering = this.steeringArtifact = null;
    this.output(this.panels.work, "knowledgeos-work-result", "작업 조회 중…");
    const work = await this.call("work/read", { work_run_id: id });
    if (serial !== this.workResultRequest) return;
    this.plugin.settings.lastWorkRunId = id;
    this.workIdInput.value = id;
    await this.plugin.saveSettings();
    this.current(epoch);
    if (serial !== this.workResultRequest) return;
    this.work = work;
    const section = this.output(this.panels.work, "knowledgeos-work-result", "작업·완료 기준·독립 평가");
    this.facts(section, [["WorkRun", id], ["목표", work.spec.objective], ["실행", work.application_state === "unknown" ? "결과 미확인 (과거 실행 상태: " + work.run.state + ")" : work.run.state], ["접수 / 전달 / 적용", `${work.receipt.receipt_state} / ${work.dispatch_state} / ${work.application_state}`], ["담당", work.spec.manager_executor_id || work.spec.officer_executor_id], ["독립 EvalOfficer", work.spec.eval_officer_executor_id], ["현재 GraphRun", work.run.active_graph_run_id], ["revision / control", `${work.run.revision} / ${work.run.control_revision}`], ["대기 명령", (work.run.pending_action_ids || []).join(", ") || "없음"], ["미해결 효과", (work.run.unresolved_effect_ids || []).join(", ") || "없음"]]);
    let inspection;
    try { inspection = await this.call("work/inspection", { work_run_id: id }); }
    catch (error) { if (error instanceof StaleView) throw error; section.createEl("p", { text: "계보·평가 조회 사용 불가: " + error.message }); }
    if (serial !== this.workResultRequest) return;
    for (const criterion of work.spec.acceptance_criteria || []) {
      const outcome = inspection?.assessment?.criterion_outcomes?.find(item => item.criterion_id === criterion.criterion_id);
      section.createEl("p", { text: `${criterion.description} · ${outcome?.outcome || "독립 평가 미제공"}` });
      if (outcome?.evidence_references) section.createEl("pre", { text: json(outcome.evidence_references) });
    }
    const lineage = section.createEl("details"); lineage.createEl("summary", { text: "세그먼트·Profile pin·사용 한도·평가 gap" });
    lineage.createEl("pre", { text: json({ work, inspection }) });
    try {
      const result = await this.call("work/result", { work_run_id: id });
      if (serial !== this.workResultRequest) return;
      section.createEl("h4", { text: "독립 평가된 결과와 근거" });
      section.createEl("p", { text: result.reason || result.state });
      if (result.text) section.createEl("pre", { text: result.text });
      if (result.report) section.createEl("pre", { text: json(result.report) });
      if (Array.isArray(result.evidence)) for (const item of result.evidence) {
        const article = section.createDiv({ cls: "knowledgeos-result-card" });
        if (item.path) this.button(article, item.path, () => this.openPath(item.path, item.resource_reference?.source_revision));
        article.createEl("small", { text: item.chunk_locator || "" });
        article.createEl("p", { text: item.excerpt || "" });
      } else if (result.evidence != null) section.createEl("pre", { text: json(result.evidence) });
      if (result.truth_evaluated === false) section.createEl("p", { text: "근거의 참조·해시를 검증한 결과이며 내용의 진실성 평가는 아닙니다." });
      if (result.truncated) section.createEl("p", { text: "Owner가 반환한 본문은 일부만 표시됩니다 (truncated)." });
      const raw = section.createEl("details"); raw.createEl("summary", { text: "정확한 평가·참조 원본" }); raw.createEl("pre", { text: json(result) });
      if (result.proposal_reference) this.button(section, "결과 제안 검토", () => this.showProposal(result.proposal_reference), "proposal_review", false, { icon: "inbox", label: "제안" });
    } catch (error) { if (error instanceof StaleView) throw error; section.createEl("p", { text: "결과 사용 불가: " + error.message }); }
    if (serial !== this.workResultRequest) return;
    this.renderControl(section);
    this.renderSteering(section);
    this.section = "work";
    for (const [name, panel] of Object.entries(this.panels)) panel.hidden = name !== "work";
    for (const button of this.contentEl.querySelectorAll("button[data-section]")) button.setAttribute("aria-pressed", String(button.dataset.section === "work"));
    this.sync();
  }
  renderControl(parent) {
    const section = parent.createEl("details", { cls: "knowledgeos-control" }); section.createEl("summary", { text: "실행 제어" });
    section.createEl("p", { text: this.owner?.bindings?.run_control ? "접수 ≠ 적용 · Owner가 정확한 대상을 확인합니다" : "사용 불가 · 실행 제어 바인딩 없음" });
    this.button(section, "제어 대상 새로 확인", () => this.showControlTarget(), "run_control", false, { icon: "refresh-cw", label: "대상 조회" });
    section.createEl("pre", { cls: "knowledgeos-control-target" });
    const row = section.createDiv({ cls: "knowledgeos-actions" });
    for (const [kind, label, icon, short] of [["pause", "일시 정지 요청", "pause", "정지"], ["resume", "다시 실행 요청", "play", "재개"], ["cancel", "취소 요청", "square", "취소"]]) {
      const button = this.button(row, label, () => this.control(kind), "control_write", true, { icon, label: short });
      button.dataset.fresh = "target";
    }
    this.commandInput = this.field(section, "명령 ID", "input", { maxlength: "96" });
    this.button(section, "접수·적용 상태 조회", () => this.showCommand(this.commandInput.value.trim()), "run_control", false, { icon: "receipt-text", label: "상태" });
    section.createEl("pre", { cls: "knowledgeos-command-receipt" });
  }
  async showControlTarget() {
    this.target = null;
    this.target = await this.call("work/control/target", { work_run_id: this.work.run.work_run_id });
    this.contentEl.querySelector(".knowledgeos-control-target").textContent = json(this.target);
    this.sync();
  }
  async control(kind) {
    this.requireWrite("control_write");
    if (!this.target) throw new Error("현재 제어 대상을 조회하세요.");
    const epoch = this.epoch;
    const target = this.target;
    const id = this.work.run.work_run_id;
    if (!await confirmAction(this.app, `정확한 WorkRun ${id}에 ${kind} 명령을 접수할까요? 접수는 실제 적용이 아닙니다.`)) return;
    this.current(epoch);
    if (target !== this.target) throw new Error("확인 중 대상이 바뀌었습니다. 다시 조회하세요.");
    const command_id = "obsidian." + globalThis.crypto.randomUUID();
    this.commandInput.value = command_id;
    const payload = { work_run_id: id, command_id, target, expires_at: new Date(Date.now() + 300000).toISOString() };
    this.target = null;
    this.contentEl.querySelector(".knowledgeos-command-receipt").textContent = json({ command_id, application_state: "응답 대기" });
    const result = await this.call("work/control/" + kind, payload, true);
    this.contentEl.querySelector(".knowledgeos-command-receipt").textContent = json(result);
  }
  async showCommand(commandId) {
    const result = await this.call("work/control/command", { work_run_id: this.work.run.work_run_id, command_id: commandId });
    this.contentEl.querySelector(".knowledgeos-command-receipt").textContent = json(result);
  }
  renderSteering(parent) {
    const section = parent.createEl("details", { cls: "knowledgeos-steering" }); section.createEl("summary", { text: "작업 조정" });
    section.createEl("p", { text: this.owner?.bindings?.safe_steering ? "근거 등록 → 명령 접수 · 적용은 별도 조회" : "사용 불가 · 작업 조정 바인딩 없음" });
    this.button(section, "질문·허용 조정 새로 조회", () => this.showSteering(), "safe_steering", false, { icon: "refresh-cw", label: "조회" });
    section.createEl("pre", { cls: "knowledgeos-steering-view" });
    this.steeringKind = this.field(section, "조정", "select");
    this.steeringFields = section.createDiv({ cls: "knowledgeos-steering-fields" });
    this.steeringKind.onchange = () => this.renderSteeringFields();
    this.button(section, "조정 근거 등록", () => this.registerSteering(), "control_write", true, { icon: "file-plus", label: "근거 등록" }).dataset.fresh = "steering";
    const send = this.button(section, "조정 명령 접수", () => this.submitSteering(), "control_write", true, { icon: "send", label: "접수" }); send.dataset.fresh = "steering"; send.dataset.choice = "steer";
    section.createEl("pre", { cls: "knowledgeos-steering-receipt" });
    this.button(section, "조정 Work 결과 조회", () => this.exportSteering(), "safe_steering", false, { icon: "file-check", label: "결과" });
    section.createEl("pre", { cls: "knowledgeos-steering-export" });
  }
  async showSteering() {
    this.steering = this.steeringArtifact = null;
    const view = await this.call("work/steering/view", { work_run_id: this.work.run.work_run_id });
    this.steering = view;
    this.contentEl.querySelector(".knowledgeos-steering-view").textContent = json(view);
    this.steeringKind.empty();
    const labels = { answer: "질문 응답", parameter: "허용 parameter 변경", cutover: "부분 Graph 교체", amend_work: "Director 승인 WorkSpec 개정", resume: "닫힌 Graph에서 계속 실행" };
    for (const kind of view.supported_kinds || []) if (labels[kind]) this.steeringKind.createEl("option", { text: labels[kind], value: kind });
    this.renderSteeringFields();
  }
  renderSteeringFields() {
    this.steeringArtifact = null;
    this.steeringFields.empty();
    const kind = this.steeringKind.value;
    if (kind === "answer") this.steeringAnswer = this.field(this.steeringFields, "질문에 대한 응답", "textarea", { maxlength: "256", rows: "3" });
    else if (kind === "parameter") {
      this.parameterId = this.field(this.steeringFields, "허용 parameter", "select");
      this.parameterValue = this.field(this.steeringFields, "허용 값", "select");
      const values = () => { this.parameterValue.empty(); for (const value of this.steering.parameter_bounds?.[this.parameterId.value] || []) this.parameterValue.createEl("option", { text: value, value }); };
      for (const id of Object.keys(this.steering?.parameter_bounds || {})) this.parameterId.createEl("option", { text: id, value: id });
      this.parameterId.onchange = values; values();
    } else if (kind === "amend_work") this.successorSpec = this.field(this.steeringFields, "별도 Director가 승인한 successor WorkSpec JSON", "textarea", { rows: "6" });
    else if (["cutover", "resume"].includes(kind)) this.steeringReason = this.field(this.steeringFields, "구조 조정 사유", "textarea", { maxlength: "256", rows: "3" });
    this.steeringFields.oninput = () => { this.steeringArtifact = null; this.sync(); };
    this.sync();
  }
  steeringInput() {
    const kind = this.steeringKind.value;
    if (!this.steering?.supported_kinds?.includes(kind)) throw new Error("Owner의 현재 허용 조정을 조회하세요.");
    if (kind === "answer") return { kind, content: { answer: this.steeringAnswer.value } };
    if (kind === "parameter") {
      const parameter_id = this.parameterId.value, value = this.parameterValue.value;
      if (!this.steering.parameter_bounds?.[parameter_id]?.includes(value)) throw new Error("허용된 parameter와 값만 선택하세요.");
      return { kind, parameter_id, content: { value } };
    }
    if (kind === "amend_work") {
      const successor_spec = JSON.parse(this.successorSpec.value);
      return { kind, successor_spec, content: { successor_spec_digest: successor_spec.spec_digest } };
    }
    return { kind, content: { reason: this.steeringReason.value } };
  }
  async registerSteering() {
    this.requireWrite("control_write");
    const selected = this.steeringInput();
    const reference = await this.call("work/steering/artifact", { work_run_id: this.work.run.work_run_id, content: selected.content }, true);
    if (json(this.steeringInput()) !== json(selected)) throw new Error("등록 중 입력이 바뀌었습니다. 이전 근거는 명령에 사용하지 않으며 현재 입력으로 다시 등록해야 합니다.");
    this.steeringArtifact = { reference, selected };
    this.contentEl.querySelector(".knowledgeos-steering-receipt").textContent = "근거 등록됨 · 명령 미접수\n" + json(reference);
    this.sync();
  }
  async submitSteering() {
    this.requireWrite("control_write");
    if (!this.steeringArtifact) throw new Error("현재 revision의 조정 근거를 먼저 등록하세요.");
    const epoch = this.epoch, artifact = this.steeringArtifact, steering = this.steering;
    const work_run_id = this.work.run.work_run_id;
    if (!await confirmAction(this.app, "현재 정확한 revision에 조정 명령을 접수할까요? 실제 적용은 별도로 조회합니다.")) return;
    this.current(epoch);
    if (artifact !== this.steeringArtifact || steering !== this.steering) throw new Error("확인 중 조정 대상이 바뀌었습니다. 다시 조회하세요.");
    const { reference, selected } = artifact;
    if (json(this.steeringInput()) !== json(selected)) throw new Error("현재 입력과 등록된 근거가 다릅니다. 현재 입력으로 근거를 다시 등록하세요.");
    const payload = { work_run_id, command_id: "obsidian.steer." + globalThis.crypto.randomUUID(), kind: selected.kind, target: steering.target, expires_at: new Date(Date.now() + 300000).toISOString(), directive_reference: reference };
    if (selected.parameter_id !== undefined) payload.parameter_id = selected.parameter_id;
    if (selected.successor_spec !== undefined) payload.successor_spec = selected.successor_spec;
    this.steeringArtifact = null;
    this.contentEl.querySelector(".knowledgeos-steering-receipt").textContent = json({ command_id: payload.command_id, application_state: "응답 대기" });
    const receipt = await this.call("work/steering/request", payload, true);
    this.contentEl.querySelector(".knowledgeos-steering-receipt").textContent = json(receipt);
  }
  async exportSteering() {
    const result = await this.call("work/steering/export", { work_run_id: this.work.run.work_run_id });
    const bytes = Uint8Array.from(atob(result.content_base64), char => char.charCodeAt(0));
    this.contentEl.querySelector(".knowledgeos-steering-export").textContent = new TextDecoder("utf-8", { fatal: true }).decode(bytes) + "\n\n" + json({ reference: result.reference, assessment: result.assessment });
  }
  async showProposals() {
    const result = await this.call("proposal/list");
    const section = this.output(this.panels.pending, "knowledgeos-proposals", "제안 목록");
    for (const item of result.proposals || []) this.button(section, `${item.action} · ${item.proposal_reference.logical_id || item.proposal_reference.artifact_id}`, () => this.showProposal(item.proposal_reference), "proposal_review");
    if (!result.proposals?.length) section.createEl("p", { text: "Owner가 export한 Pending 제안이 없습니다." });
  }
  async showProposal(reference) {
    const serial = ++this.proposalRequest;
    this.proposal = null;
    const result = await this.call("proposal/read", { proposal_reference: reference });
    if (serial !== this.proposalRequest) return;
    this.proposal = result;
    const section = this.output(this.panels.pending, "knowledgeos-proposal-detail", "원문·근거·변경 diff");
    this.facts(section, [["원문", result.source_path], ["대상", result.target_path], ["접수 원문 digest", result.source_digest], ["원문 변경", result.source_stale ? "변경됨 · 승인·적용 차단" : "변경 없음"], ["결정", decisionValue(result.decision) || "미결정"]]);
    section.createEl("h5", { text: "현재 원문" });
    section.createEl("pre", { text: result.source_text ?? "Owner에서 원문을 반환하지 않았습니다." });
    if (result.source_truncated) section.createEl("p", { text: "원문은 앞 16384자까지만 반환되었습니다 (truncated). 전체 노트는 Obsidian에서 확인하세요." });
    if (result.source_path) this.button(section, "원문 노트 열기", () => this.openPath(result.source_path), null, false, { icon: "file-text", label: "원문" });
    if (result.field_evidence) section.createEl("pre", { text: json(result.field_evidence) });
    section.createEl("h5", { text: "원본 변경 diff" });
    section.createEl("pre", { text: result.diff ?? "이 제안에는 정본 적용 diff가 없습니다. 결정만 가능한 계획일 수 있습니다." });
    const artifact = section.createEl("details"); artifact.createEl("summary", { text: "Pending 원문 전체" }); artifact.createEl("pre", { text: result.text });
    this.reasonInput = this.field(section, "결정 사유", "textarea", { maxlength: "2048", rows: "2" });
    const row = section.createDiv({ cls: "knowledgeos-actions" });
    for (const [choice, label, icon, short] of [["approve", "승인 기록", "check", "승인"], ["reject", "거절 기록", "x", "거절"]]) {
      const button = this.button(row, label, () => this.decideProposal(choice), "proposal_decide", true, { icon, label: short }); button.dataset.fresh = "proposal"; button.dataset.choice = choice;
    }
    const apply = this.button(section, "승인한 제안 원본 적용", () => this.applyProposal(), "proposal_apply", true, { icon: "check-check", label: "적용" }); apply.dataset.fresh = "proposal"; apply.dataset.choice = "apply";
    this.section = "pending"; for (const [name, panel] of Object.entries(this.panels)) panel.hidden = name !== "pending";
    for (const button of this.contentEl.querySelectorAll("button[data-section]")) button.setAttribute("aria-pressed", String(button.dataset.section === "pending"));
    this.sync();
  }
  async decideProposal(choice) {
    this.requireWrite("proposal_decide");
    const proposal = this.proposal;
    if (!proposal || proposal.decision || choice === "approve" && proposal.source_stale) throw new Error("현재 미결정 제안을 조회하세요. 변경된 원문이나 이미 결정된 제안은 승인하지 않습니다.");
    const result = await this.call("proposal/decide", { proposal_reference: proposal.proposal_reference, decision: choice, reason: this.reasonInput.value }, true);
    this.message(choice === "approve" ? "승인을 기록했습니다. 원본은 아직 바꾸지 않았습니다." : "거절을 기록했습니다. 원본 적용은 하지 않았습니다.");
    await this.showProposal(proposal.proposal_reference);
    return result;
  }
  async applyProposal() {
    this.requireWrite("proposal_apply");
    const proposal = this.proposal, epoch = this.epoch;
    if (!proposal?.apply_allowed || proposal.source_stale || decisionValue(proposal.decision) !== "approve") throw new Error("현재 원문에 대해 승인된 적용 가능 제안을 조회하세요.");
    if (!await confirmAction(this.app, "승인한 제안을 원본에 적용할까요? 결정 기록과 별개의 Vault 변경입니다.")) return;
    this.current(epoch);
    if (proposal !== this.proposal) throw new Error("확인 중 제안이 바뀌었습니다. 다시 조회하세요.");
    const receipt = await this.call("proposal/apply", { proposal_reference: proposal.proposal_reference }, true);
    this.proposal = null;
    this.contentEl.querySelector(".knowledgeos-proposal-detail").empty();
    this.message("Owner 원본 적용 응답을 받았습니다. 이전 검색과 Work 원문 범위는 오래된 값입니다. 다시 조회하세요. " + json(receipt));
    this.output(this.panels.search, "knowledgeos-results");
    this.sync();
    return receipt;
  }
  async showDaily() {
    const result = await this.call("daily/list");
    const section = this.output(this.panels.daily, "knowledgeos-daily", "검토할 잠정 변경");
    for (const item of result.pending || []) this.button(section, `${item.path} · ${item.review_status}`, () => this.showDailyItem(item.effect_id), "daily_review");
    if (!result.pending?.length) section.createEl("p", { text: "검토할 잠정 변경이 없습니다." });
  }
  async showDailyItem(effectId) {
    const serial = ++this.dailyRequest;
    this.currentDaily = null;
    const item = await this.call("daily/read", { effect_id: effectId });
    if (serial !== this.dailyRequest) return;
    this.currentDaily = item;
    const section = this.output(this.panels.daily, "knowledgeos-daily-detail", item.path);
    section.createEl("pre", { text: json(item) });
    if (item.path) this.button(section, "현재 노트 열기", () => this.openPath(item.path), null, false, { icon: "file-text", label: "노트" });
    if (item.review_status === "conflict") section.createEl("p", { text: "잠정 변경 후 원문이 바뀌었습니다. 자동 덮어쓰기나 전체 되돌리기를 하지 않습니다." });
    for (const [choice, label, icon, short] of [["confirm", "잠정 변경 확정", "check", "확정"], ["reject", "잠정 변경 거절", "x", "거절"]]) {
      const button = this.button(section, label, () => this.decideDaily(choice), "daily_decide", true, { icon, label: short }); button.dataset.fresh = "daily"; button.dataset.choice = "daily";
    }
    this.sync();
  }
  async decideDaily(choice) {
    this.requireWrite("daily_decide");
    const item = this.currentDaily, epoch = this.epoch;
    if (!item || item.review_status !== "pending") throw new Error("충돌하지 않는 현재 잠정 변경을 조회하세요.");
    if (!await confirmAction(this.app, choice === "confirm" ? "이 잠정 추가를 확정할까요?" : "이 잠정 추가를 거절하고 Owner의 정확한 delta 복구를 요청할까요?")) return;
    this.current(epoch);
    if (item !== this.currentDaily) throw new Error("확인 중 검토 대상이 바뀌었습니다.");
    const result = await this.call("daily/decide", { effect_id: item.effect_id, decision: choice }, true);
    this.currentDaily = null;
    this.contentEl.querySelector(".knowledgeos-daily-detail").empty();
    await this.showDaily();
    this.message("Owner 일일 검토 응답: " + json(result));
    return result;
  }
}

class OwnerSettingsTab extends PluginSettingTab {
  display() {
    this.containerEl.empty();
    new Setting(this.containerEl).setName("Owner 주소")
      .setDesc("이 기기의 loopback 주소")
      .addText((field) => field.setValue(this.plugin.settings.ownerEndpoint).onChange(async (value) => {
        try { this.plugin.settings.ownerEndpoint = endpoint(value); await this.plugin.saveSettings(); }
        catch (error) { new Notice(error.message); }
      }));
  }
}

class KnowledgeOSThinClient extends Plugin {
  async onload() {
    const stored = await this.loadData() || {};
    this.settings = { ownerEndpoint: stored.ownerEndpoint || DEFAULT_SETTINGS.ownerEndpoint, lastWorkRunId: stored.lastWorkRunId || "" };
    this.registerView(VIEW_TYPE, (leaf) => new WorkReviewSidebar(leaf, this));
    this.addSettingTab(new OwnerSettingsTab(this.app, this));
    registerIntegrations(this);
    this.addRibbonIcon("network", "KnowledgeOS 작업실", () => this.activateView());
  }
  async activateView(section = "work", path = null) {
    const leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0] || this.app.workspace.getRightLeaf(false);
    if (!leaf) throw new Error("KnowledgeOS 사이드바를 열 수 없습니다.");
    await leaf.setViewState({ type: VIEW_TYPE, active: true });
    await this.app.workspace.revealLeaf(leaf);
    await leaf.view.openSection(section, path);
    return leaf.view;
  }
  async saveSettings() { await this.saveData(this.settings); }
}

module.exports = KnowledgeOSThinClient;
module.exports.WorkReviewSidebar = WorkReviewSidebar;
module.exports.endpoint = endpoint;
module.exports.ownerCall = ownerCall;
module.exports.localBackend = localBackend;
module.exports.backendResult = backendResult;
module.exports.registerIntegrations = registerIntegrations;
module.exports.integrationActions = integrationActions;
module.exports.executeIntegration = executeIntegration;
