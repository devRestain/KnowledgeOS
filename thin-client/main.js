"use strict";
// KnowledgeOS Thin Client (Blueprint §3.1, capability C11): one command surface.
// Requests carry note paths and the SHA-256 of the bytes last seen, never note text.
// Owner 시작/중지 only signal the Owner's launchd service and report what /api/health shows.
const { ItemView, Modal, Notice, Plugin, PluginSettingTab, Setting, SuggestModal, TFile, TFolder, requestUrl, setIcon, setTooltip } = require("obsidian");

const DEFAULT_ENDPOINT = "http://127.0.0.1:8765";
const SIDEBAR_VIEW = "knowledgeos-sidebar";
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);
const HTTP_TIMEOUT_MS = 10000;
const HEALTH_INTERVAL_MS = 30000;
const POLL_MIN_MS = 500;
const POLL_MAX_MS = 5000;
const SETTLE_DEADLINE_MS = 30 * 60 * 1000;
const SEARCH_LIMIT = 20;
const SETTLED = new Set(["completed", "unmet", "failed", "refused", "cancelled", "unknown"]);
const ERROR_STATUSES = new Set(["failed", "refused", "unknown"]);
const DEFAULT_OWNER_LABEL = "com.agentfabric.knowledgeos.owner";
const OWNER_LABEL_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const LAUNCHCTL_TIMEOUT_MS = 20000;
// Owner start/stop wait for /api/health in at most this many observations, one second apart.
const OWNER_POLL_MS = 1000;
const OWNER_START_POLLS = 60;
const OWNER_STOP_POLLS = 30;
const OWNER_START_SECONDS = (OWNER_START_POLLS * OWNER_POLL_MS) / 1000;
const OWNER_STOP_SECONDS = (OWNER_STOP_POLLS * OWNER_POLL_MS) / 1000;
const STATUS_LABELS = {
  completed: "완료",
  unmet: "기준 미충족",
  failed: "실패",
  refused: "거부됨",
  cancelled: "취소됨",
  unknown: "결과 불명",
};
const CONNECTION_LABELS = { unknown: "확인 중", up: "연결됨", down: "연결 끊김" };

class NetworkError extends Error {}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function blockerText(probe) {
  return probe.blockers.length ? ` (blockers: ${probe.blockers.join(", ")})` : "";
}

function isNote(file) {
  return file instanceof TFile && file.extension === "md";
}

function collectNotes(items) {
  const seen = new Map();
  const visit = (item) => {
    if (item instanceof TFolder) item.children.forEach(visit);
    else if (isNote(item) && !seen.has(item.path)) seen.set(item.path, item);
  };
  items.forEach(visit);
  return [...seen.values()];
}

async function sha256Hex(buffer) {
  const hash = await globalThis.crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function localDate(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function resultPath(receipt) {
  const outcome = receipt.outcome;
  if (outcome && typeof outcome === "object") {
    for (const key of ["result_path", "report_path", "proposal_path", "path"]) {
      if (typeof outcome[key] === "string" && outcome[key]) return outcome[key];
    }
  }
  const match = /\[\[([^\]|#]+)/.exec(receipt.notice || "");
  return match ? match[1] : null;
}

// Commands that act on notes. scope: "targets" (note, selection, folder), "note", "proposal".
const NOTE_ACTIONS = [
  { id: "standardize-preview", name: "규격화 미리보기", icon: "list-checks", scope: "targets", run: (p, items, ui) => p.standardizePreview(items, ui) },
  { id: "organize", name: "정리 요청", icon: "folder-tree", scope: "targets", run: (p, items, ui) => p.organize(items, ui) },
  { id: "enhance-develop", name: "강화: 발전", icon: "sprout", scope: "note", run: (p, file, ui) => p.enhanceDevelop(file, ui) },
  { id: "enhance-ask", name: "강화: 묻기", icon: "help-circle", scope: "note", run: (p, file, ui) => p.enhanceAsk(file, ui) },
  { id: "enhance-review", name: "강화: 돌아보기", icon: "calendar-range", scope: "note", run: (p, file, ui) => p.enhanceReview(file, ui) },
  { id: "related-notes", name: "관련 노트", icon: "links", scope: "note", run: (p, file, ui) => p.relatedNotes(file, ui) },
  { id: "review-apply", name: "적용", icon: "check", scope: "proposal", run: (p, file, ui) => p.reviewDecision(file, "review.apply", "적용", ui) },
  { id: "review-reject", name: "거절", icon: "x", scope: "proposal", run: (p, file, ui) => p.reviewDecision(file, "review.reject", "거절", ui) },
  { id: "review-undo", name: "자동 변경 되돌리기", icon: "undo-2", scope: "note", run: (p, file, ui) => p.reviewUndo(file, ui) },
];

class InputModal extends Modal {
  constructor(app, title, fields, onSubmit) {
    super(app);
    this.title = title;
    this.fields = fields;
    this.onSubmit = onSubmit;
    this.values = Object.fromEntries(fields.map((field) => [field.key, field.value || ""]));
  }

  onOpen() {
    this.titleEl.setText(this.title);
    this.contentEl.empty();
    for (const field of this.fields) {
      const setting = new Setting(this.contentEl).setName(field.label);
      const bind = (component) => component
        .setPlaceholder(field.placeholder || "")
        .setValue(this.values[field.key])
        .onChange((value) => { this.values[field.key] = value; });
      if (field.multiline) setting.addTextArea(bind);
      else setting.addText(bind);
    }
    new Setting(this.contentEl).addButton((button) => button
      .setButtonText("요청")
      .setCta()
      .onClick(() => this.submit()));
  }

  submit() {
    const values = Object.fromEntries(Object.entries(this.values).map(([key, value]) => [key, String(value).trim()]));
    if (this.fields.some((field) => !values[field.key])) {
      new Notice("모든 입력란을 채워 주세요.");
      return;
    }
    this.close();
    void this.onSubmit(values);
  }

  onClose() {
    this.contentEl.empty();
  }
}

// Values System One decided (C2 rule 9), shown above the diff with their confidence.
function decisionLines(note) {
  return (note.decisions || []).map((d) => `System One: ${d.key} → ${d.value} (${Math.round(d.confidence * 100)}%)`);
}

class PreviewModal extends Modal {
  constructor(app, outcome, decide) {
    super(app);
    this.outcome = outcome;
    this.decide = decide;
    this.decided = false;
  }

  onOpen() {
    this.titleEl.setText("규격화 미리보기");
    this.contentEl.empty();
    for (const note of this.outcome.notes || []) {
      this.contentEl.createEl("h4", { text: note.path });
      for (const line of decisionLines(note)) this.contentEl.createEl("p", { text: line });
      this.contentEl.createEl("pre", { text: note.diff || "" });
    }
    for (const skipped of this.outcome.skipped || []) {
      this.contentEl.createEl("p", { text: `건너뜀: ${skipped.path} (${skipped.reason})` });
    }
    new Setting(this.contentEl)
      .addButton((button) => button.setButtonText("적용").setCta().onClick(() => this.finish(true)))
      .addButton((button) => button.setButtonText("취소").onClick(() => this.finish(false)));
  }

  finish(confirmed) {
    if (this.decided) return;
    this.decided = true;
    this.close();
    void this.decide(confirmed);
  }

  onClose() {
    this.contentEl.empty();
    if (!this.decided) {
      this.decided = true;
      void this.decide(false);
    }
  }
}

class ResultsModal extends SuggestModal {
  constructor(app, plugin, results) {
    super(app);
    this.plugin = plugin;
    this.results = results;
    this.setPlaceholder("결과 거르기");
  }

  getSuggestions(query) {
    const needle = query.trim().toLowerCase();
    if (!needle) return this.results;
    return this.results.filter((result) =>
      `${result.title || ""} ${result.path} ${result.excerpt || ""}`.toLowerCase().includes(needle));
  }

  renderSuggestion(result, el) {
    el.createEl("div", { text: result.title || result.path });
    const where = result.line ? `${result.path}:${result.line}` : result.path;
    el.createEl("small", { text: result.excerpt ? `${where} — ${result.excerpt}` : where });
  }

  onChooseSuggestion(result) {
    void this.plugin.openNote(result.path, result.line);
  }
}

class ThinClientSettingTab extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    this.containerEl.empty();
    new Setting(this.containerEl)
      .setName("KnowledgeOS endpoint")
      .setDesc(`Loopback ExOps 주소 (기본 ${DEFAULT_ENDPOINT})`)
      .addText((text) => text
        .setPlaceholder(DEFAULT_ENDPOINT)
        .setValue(this.plugin.settings.endpoint)
        .onChange((value) => this.plugin.updateEndpoint(value)));
    new Setting(this.containerEl)
      .setName("Owner launchd 라벨")
      .setDesc(`Owner 시작·중지가 다루는 LaunchAgent 라벨 (기본 ${DEFAULT_OWNER_LABEL})`)
      .addText((text) => text
        .setPlaceholder(DEFAULT_OWNER_LABEL)
        .setValue(this.plugin.settings.ownerLabel)
        .onChange((value) => this.plugin.updateOwnerLabel(value)));
  }
}

class KnowledgeOSSidebar extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.modes = { enhance: "develop", find: "semantic" };
    this.drafts = {};
  }

  getViewType() { return SIDEBAR_VIEW; }
  getDisplayText() { return "KnowledgeOS"; }
  getIcon() { return "network"; }

  async onOpen() {
    this.contentEl.empty();
    this.contentEl.addClass("kos-sidebar");
    this.noteEl = this.contentEl.createDiv({ cls: "kos-sidebar-note" });
    const actions = this.contentEl.createDiv({ cls: "kos-sidebar-actions" });
    this.navigation = new Map();
    for (const [id, label] of [
      ["standardize", "규격화"], ["organize", "정리"],
      ["enhance", "강화"], ["find", "찾기"],
    ]) {
      this.navigation.set(id, this.button(actions, label, null, () => this.select(id)));
    }
    this.panelEl = this.contentEl.createDiv({ cls: "kos-sidebar-panel", attr: { role: "region", "aria-label": "기능" } });
    this.feedbackEl = this.contentEl.createDiv({ cls: "kos-sidebar-feedback", attr: { role: "status", "aria-live": "polite" } });
    this.proposalEl = this.contentEl.createDiv({ cls: "kos-sidebar-proposal" });
    this.button(this.proposalEl, "적용", "check", (button) => this.run(button, () => this.noteAction("review-apply")));
    this.button(this.proposalEl, "거절", "x", (button) => this.run(button, () => this.noteAction("review-reject")));
    const footer = this.contentEl.createDiv({ cls: "kos-sidebar-footer" });
    this.status = footer.createSpan({ cls: "kos-sidebar-status", attr: { role: "status", "aria-live": "polite" } });
    this.power = this.button(footer, "", "power", () => this.invoke(() =>
      this.plugin.connection === "up" ? this.plugin.ownerStop(this.surface()) : this.plugin.ownerStart(this.surface())));
    this.more = this.button(footer, "", "ellipsis", () => this.select("more"), "더보기");
    this.select("find");
    this.refresh();
  }

  button(parent, label, icon, run, tooltip = label) {
    const button = parent.createEl("button", {
      cls: label ? "kos-sidebar-action" : "kos-sidebar-icon",
      attr: { type: "button", "aria-label": tooltip },
    });
    setTooltip(button, tooltip);
    if (icon) setIcon(button.createSpan({ attr: { "aria-hidden": "true" } }), icon);
    if (label) button.createSpan({ text: label });
    if (run) button.addEventListener("click", () => run(button));
    return button;
  }

  select(section, mode = this.modes[section], force = false) {
    if (!force && this.section === section && this.mode === mode) return;
    this.cancelPreview?.();
    this.section = section;
    this.mode = mode;
    if (mode) this.modes[section] = mode;
    for (const [id, button] of this.navigation) button.setAttribute("aria-pressed", String(id === section));
    this.more.setAttribute("aria-expanded", String(section === "more"));
    this.panelEl.empty();
    const body = { root: this.panelEl.createDiv({ cls: "kos-sidebar-body" }) };
    this.body = body;
    const choices = section === "enhance"
      ? [["develop", "발전"], ["ask", "묻기"], ["review", "돌아보기"]]
      : section === "find" ? [["semantic", "의미 검색"], ["related", "관련 노트"]] : [];
    if (choices.length) {
      const modes = body.root.createDiv({ cls: "kos-sidebar-modes" });
      for (const [id, label] of choices) {
        const button = this.button(modes, label, null, () => this.select(section, id));
        button.setAttribute("aria-pressed", String(id === mode));
      }
    }
    body.controls = body.root.createDiv({ cls: "kos-sidebar-controls" });
    body.output = body.root.createDiv({ cls: "kos-sidebar-output" });
    if (section === "standardize") this.actionButton(body.controls, "미리보기", "standardize-preview");
    else if (section === "organize") this.actionButton(body.controls, "정리 요청", "organize");
    else if (section === "enhance") {
      if (mode === "develop") this.actionButton(body.controls, "발전 요청", "enhance-develop");
      else this.noteAction(mode === "ask" ? "enhance-ask" : "enhance-review");
    } else if (section === "find") {
      if (mode === "related") this.actionButton(body.controls, "관련 노트 찾기", "related-notes");
      else this.plugin.semanticSearch(this.surface());
    } else if (section === "more") {
      this.plugin.freeText(this.surface());
      const tools = body.root.createDiv({ cls: "kos-sidebar-tools" });
      this.actionButton(tools, "되돌리기", "review-undo");
      this.button(tools, "관리 콘솔", "external-link", () => this.plugin.openConsole(this.surface()));
    }
  }

  actionButton(parent, label, id) {
    const action = NOTE_ACTIONS.find((candidate) => candidate.id === id);
    const button = this.button(parent, label, action.icon, (el) => this.run(el, () => this.noteAction(id)));
    button.disabled = !this.available(action, this.plugin.contextFile);
    return button;
  }

  available(action, file) {
    return action.id === "enhance-ask" || this.plugin.accepts(action, file);
  }

  noteAction(id) {
    const action = NOTE_ACTIONS.find((candidate) => candidate.id === id);
    const file = this.plugin.contextFile;
    if (!this.available(action, file)) return;
    if (file && this.app.vault.getAbstractFileByPath(file.path) !== file) {
      this.notify("노트를 다시 열어 주세요.");
      return;
    }
    return this.invoke(() => action.run(this.plugin, action.scope === "targets" ? [file] : file, this.surface()));
  }

  // Each action owns its output region; late responses cannot replace a different panel or note.
  surface() {
    const body = this.body;
    return {
      notify: (text, sticky, path) => this.notify(text, sticky, path),
      input: (title, fields, submit) => {
        if (this.body === body) this.input(body.controls, title, fields, submit);
      },
      preview: (outcome, decide) => this.body === body
        ? this.preview(body.output, outcome, decide) : decide(false),
      results: (results) => {
        if (this.body !== body) return;
        body.output.empty();
        for (const result of results) {
          const row = body.output.createDiv({ cls: "kos-sidebar-result" });
          this.noteLink(row, result.title || result.path, result.path, result.line);
          row.createEl("small", { text: `${result.path}${result.line ? `:${result.line}` : ""}` });
          if (result.excerpt) row.createEl("p", { text: result.excerpt });
        }
      },
      clear: () => body.output.empty(),
    };
  }

  input(parent, title, fields, submit) {
    const form = parent.createEl("form", { cls: "kos-sidebar-form", attr: { "aria-label": title } });
    form.noValidate = true;
    const values = this.drafts[title] ||= Object.fromEntries(fields.map((field) => [field.key, field.value || ""]));
    const inputs = fields.map((field) => {
      const label = form.createEl("label", { cls: "kos-sidebar-field" });
      label.createSpan({ text: field.label });
      const errorId = `kos-field-${globalThis.crypto.randomUUID()}`;
      const input = label.createEl(field.multiline ? "textarea" : "input", {
        attr: { name: field.key, "aria-describedby": errorId, autocomplete: "off" },
      });
      input.value = values[field.key];
      input.placeholder = field.placeholder || "";
      if (field.multiline) input.rows = 4;
      const error = label.createSpan({ cls: "kos-sidebar-field-error", attr: { id: errorId } });
      input.addEventListener("input", () => {
        values[field.key] = input.value;
        error.setText("");
        input.setAttribute("aria-invalid", "false");
      });
      return { key: field.key, input, error };
    });
    const button = this.button(form, title === "의미 검색" ? "검색" : "요청", "arrow-up", null);
    button.setAttribute("type", "submit");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (button.disabled) return;
      const submitted = Object.fromEntries(inputs.map(({ key, input }) => [key, input.value.trim()]));
      const missing = inputs.filter(({ key }) => !submitted[key]);
      for (const { input, error } of missing) {
        error.setText("입력해 주세요.");
        input.setAttribute("aria-invalid", "true");
      }
      if (missing.length) return missing[0].input.focus();
      return this.run(button, () => submit(submitted));
    });
  }

  preview(parent, outcome, decide) {
    parent.empty();
    for (const note of outcome.notes || []) {
      parent.createEl("h4", { text: note.path });
      for (const line of decisionLines(note)) parent.createEl("p", { text: line });
      parent.createEl("pre", { text: note.diff || "" });
    }
    for (const skipped of outcome.skipped || []) parent.createEl("p", { text: `건너뜀: ${skipped.path} (${skipped.reason})` });
    return new Promise((resolve) => {
      let decided = false;
      const finish = async (confirmed) => {
        if (decided) return;
        decided = true;
        this.cancelPreview = null;
        apply.disabled = cancel.disabled = true;
        try { await decide(confirmed); } finally { resolve(); }
      };
      const buttons = parent.createDiv({ cls: "kos-sidebar-tools" });
      const apply = this.button(buttons, "적용", "check", () => finish(true));
      const cancel = this.button(buttons, "취소", "x", () => finish(false));
      this.cancelPreview = () => void finish(false);
    });
  }

  noteLink(parent, title, path, line) {
    const link = parent.createEl("a", { text: title, attr: { href: "#" } });
    link.addEventListener("click", (event) => {
      event.preventDefault();
      return this.invoke(() => this.plugin.openNote(path, line));
    });
  }

  notify(text, _sticky, path) {
    this.feedbackEl.empty();
    this.feedbackEl.createSpan({ text: text.replace(/^KnowledgeOS /, "") });
    if (path) this.noteLink(this.feedbackEl, path, path);
  }

  async run(button, action) {
    if (button.disabled) return;
    button.disabled = true;
    try { await this.invoke(action); } finally { button.disabled = false; }
  }

  async invoke(run) {
    try { return await run(); } catch (error) { this.notify(error.message); }
  }

  refresh() {
    if (!this.noteEl) return;
    const { contextFile: file, connection, inflight, lifecycleBusy } = this.plugin;
    this.noteEl.setText(file ? file.basename : "노트 선택");
    setTooltip(this.noteEl, file ? file.path : "노트 선택");
    if (this.target !== file || this.targetPath !== file?.path) {
      this.target = file;
      this.targetPath = file?.path;
      this.select(this.section, this.mode, true);
    }
    this.proposalEl.hidden = !file || !this.plugin.isProposal(file);
    this.status.setText(`${CONNECTION_LABELS[connection]}${inflight ? ` · ${inflight}` : ""}`);
    this.power.disabled = connection === "unknown" || Boolean(lifecycleBusy);
    const powerLabel = lifecycleBusy ? "Owner 전환 중…" : connection === "up" ? "Owner 중지" : "Owner 시작";
    this.power.setAttribute("aria-label", powerLabel);
    this.power.setAttribute("aria-pressed", String(connection === "up"));
    setTooltip(this.power, powerLabel);
  }

  async onClose() {
    this.body = null;
    this.cancelPreview?.();
  }
}

class KnowledgeOSThinClient extends Plugin {
  async onload() {
    const stored = await this.loadData();
    this.stored = stored && typeof stored === "object" ? stored : {};
    const text = (key) => (typeof this.stored[key] === "string" ? this.stored[key].trim() : "");
    this.settings = {
      endpoint: text("endpoint") || DEFAULT_ENDPOINT,
      ownerLabel: text("ownerLabel") || DEFAULT_OWNER_LABEL,
    };
    this.connection = "unknown";
    this.inflight = 0;
    this.contextFile = isNote(this.app.workspace.getActiveFile()) ? this.app.workspace.getActiveFile() : null;
    this.registerView(SIDEBAR_VIEW, (leaf) => new KnowledgeOSSidebar(leaf, this));
    this.addRibbonIcon("network", "KnowledgeOS", () => void this.openSidebar());
    this.addCommand({ id: "open-sidebar", name: "사이드바 열기", callback: () => void this.openSidebar() });
    // Keep the last note when the command tab takes focus; never persist navigation.
    this.registerEvent(this.app.workspace.on("file-open", (file) => {
      if (file) this.contextFile = isNote(file) ? file : null;
      this.refreshSidebars();
    }));
    this.registerEvent(this.app.metadataCache.on("changed", () => this.refreshSidebars()));
    this.registerEvent(this.app.vault.on("rename", () => this.refreshSidebars()));
    this.registerEvent(this.app.vault.on("delete", (file) => {
      if (this.contextFile === file) this.contextFile = null;
      this.refreshSidebars();
    }));
    this.statusEl = this.addStatusBarItem();
    this.renderStatus();

    for (const action of NOTE_ACTIONS) {
      if (action.id === "enhance-ask") continue;
      this.addCommand({
        id: action.id,
        name: action.name,
        checkCallback: (checking) => {
          const file = this.app.workspace.getActiveFile();
          if (!this.accepts(action, file)) return false;
          if (!checking) void action.run(this, action.scope === "targets" ? [file] : file);
          return true;
        },
      });
    }
    this.addCommand({
      id: "enhance-ask",
      name: "강화: 묻기",
      callback: () => {
        const file = this.app.workspace.getActiveFile();
        void this.enhanceAsk(isNote(file) ? file : null);
      },
    });
    this.addCommand({ id: "free-text", name: "자유 요청", callback: () => this.freeText() });
    this.addCommand({ id: "semantic-search", name: "의미 검색", callback: () => this.semanticSearch() });
    this.addCommand({ id: "open-console", name: "관리 콘솔 열기", callback: () => this.openConsole() });
    this.addCommand({ id: "owner-start", name: "Owner 시작", callback: () => void this.ownerStart() });
    this.addCommand({ id: "owner-stop", name: "Owner 중지", callback: () => void this.ownerStop() });

    this.registerEvent(this.app.workspace.on("file-menu", (menu, file) => {
      if (file instanceof TFolder) this.addMenuItems(menu, [file], "targets");
      else if (isNote(file)) this.addMenuItems(menu, file, "note");
    }));
    this.registerEvent(this.app.workspace.on("files-menu", (menu, files) => {
      this.addMenuItems(menu, files, "targets");
    }));
    this.registerEvent(this.app.workspace.on("editor-menu", (menu, _editor, info) => {
      const file = info && info.file;
      if (isNote(file)) this.addMenuItems(menu, file, "note");
    }));

    this.addSettingTab(new ThinClientSettingTab(this.app, this));
    this.registerInterval(window.setInterval(() => void this.checkHealth(), HEALTH_INTERVAL_MS));
    void this.checkHealth();
  }

  async openSidebar() {
    const active = this.app.workspace.getActiveFile();
    if (active) this.contextFile = isNote(active) ? active : null;
    let leaf = this.app.workspace.getLeavesOfType(SIDEBAR_VIEW)[0];
    if (!leaf) {
      leaf = this.app.workspace.getRightLeaf(false);
      if (!leaf) return;
      await leaf.setViewState({ type: SIDEBAR_VIEW, active: true });
    }
    await this.app.workspace.revealLeaf(leaf);
    this.refreshSidebars();
  }

  refreshSidebars() {
    for (const leaf of this.app.workspace.getLeavesOfType(SIDEBAR_VIEW)) leaf.view.refresh();
  }

  onunload() {
    this.app.workspace.detachLeavesOfType(SIDEBAR_VIEW);
  }

  accepts(action, file) {
    if (!isNote(file)) return false;
    return action.scope !== "proposal" || this.isProposal(file);
  }

  isProposal(file) {
    const cache = this.app.metadataCache.getFileCache(file);
    return Boolean(cache && cache.frontmatter && cache.frontmatter.type === "proposal");
  }

  // context "targets": items is a list of files/folders; "note": items is a single note.
  addMenuItems(menu, items, context) {
    const actions = context === "targets"
      ? NOTE_ACTIONS.filter((action) => action.scope === "targets")
      : NOTE_ACTIONS.filter((action) => this.accepts(action, items));
    for (const action of actions) {
      menu.addItem((item) => item
        .setTitle(`KnowledgeOS: ${action.name}`)
        .setIcon(action.icon)
        .onClick(() => void action.run(this, context === "note" && action.scope === "targets" ? [items] : items)));
    }
  }

  // Only a user edit writes settings, and only the keys the user has set.
  async saveSetting(key, value) {
    this.stored = { ...this.stored, [key]: value };
    await this.saveData(this.stored);
  }

  async updateEndpoint(value) {
    this.settings.endpoint = value.trim() || DEFAULT_ENDPOINT;
    await this.saveSetting("endpoint", this.settings.endpoint);
    void this.checkHealth();
  }

  async updateOwnerLabel(value) {
    this.settings.ownerLabel = value.trim() || DEFAULT_OWNER_LABEL;
    await this.saveSetting("ownerLabel", this.settings.ownerLabel);
  }

  // ---- status bar ------------------------------------------------------------

  setConnection(state) {
    if (this.connection === state) return;
    this.connection = state;
    this.renderStatus();
  }

  renderStatus() {
    this.statusEl.setText(`KOS ${CONNECTION_LABELS[this.connection]} · 진행 ${this.inflight}`);
    this.refreshSidebars();
  }

  async checkHealth() {
    try {
      await this.http("GET", "/api/health");
    } catch (error) {
      if (!(error instanceof NetworkError)) this.setConnection("down");
    }
  }

  // ---- transport ---------------------------------------------------------------

  baseUrl() {
    let url;
    try {
      url = new URL(this.settings.endpoint);
    } catch (error) {
      throw new Error(`endpoint가 올바른 URL이 아닙니다: ${this.settings.endpoint}`);
    }
    if (url.protocol !== "http:" || !LOOPBACK_HOSTS.has(url.hostname)) {
      throw new Error(`endpoint는 loopback http 주소여야 합니다: ${this.settings.endpoint}`);
    }
    return url.origin;
  }

  async http(method, path, body) {
    const options = { url: this.baseUrl() + path, method, throw: false };
    if (body !== undefined) {
      options.contentType = "application/json";
      options.body = body;
    }
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = window.setTimeout(() => reject(new NetworkError("응답 시간 초과")), HTTP_TIMEOUT_MS);
    });
    let response;
    try {
      response = await Promise.race([requestUrl(options), timeout]);
    } catch (error) {
      this.setConnection("down");
      throw error instanceof NetworkError ? error : new NetworkError((error && error.message) || String(error));
    } finally {
      window.clearTimeout(timer);
    }
    this.setConnection("up");
    let data = null;
    try {
      data = JSON.parse(response.text);
    } catch (error) {
      data = null;
    }
    return { status: response.status, data };
  }

  receiptFrom(response) {
    if ((response.status === 200 || response.status === 202) && response.data && response.data.request_id) {
      return response.data;
    }
    if (response.status >= 500) throw new NetworkError(`HTTP ${response.status}`);
    if (response.status === 409) throw new Error("같은 요청 ID가 다른 내용으로 이미 접수되었습니다 (conflict).");
    const reason = response.data && response.data.error ? response.data.error : "unexpected response";
    throw new Error(`HTTP ${response.status}: ${reason}`);
  }

  // Submit once, then only look the request up. A lookup that finds nothing (404) means the Owner
  // never received it, so the identical body with the same request_id is sent again.
  async settle(requestId, body) {
    const deadline = Date.now() + SETTLE_DEADLINE_MS;
    let receipt = null;
    let mustLookUp = false;
    let delay = POLL_MIN_MS;
    while (Date.now() < deadline) {
      try {
        if (!receipt && !mustLookUp) {
          mustLookUp = true;
          receipt = this.receiptFrom(await this.http("POST", "/api/requests", body));
        } else {
          const response = await this.http("GET", `/api/requests/${encodeURIComponent(requestId)}`);
          if (response.status === 404 && !receipt) {
            mustLookUp = false;
            continue;
          }
          receipt = this.receiptFrom(response);
        }
        if (SETTLED.has(receipt.status)) return receipt;
      } catch (error) {
        if (!(error instanceof NetworkError)) throw error;
      }
      await sleep(delay);
      delay = Math.min(delay * 2, POLL_MAX_MS);
    }
    throw new Error(`요청 ${requestId}의 결과를 아직 확인하지 못했습니다. 관리 콘솔에서 확인하세요.`);
  }

  async request(label, command, params, announceCompleted = true, surface = null) {
    const requestId = globalThis.crypto.randomUUID();
    const body = JSON.stringify({ request_id: requestId, command, params });
    this.inflight += 1;
    this.renderStatus();
    if (surface) surface.notify(`${label}: 진행 중…`);
    try {
      const receipt = await this.settle(requestId, body);
      if (announceCompleted || receipt.status !== "completed") this.announce(label, receipt, surface);
      return receipt;
    } catch (error) {
      this.notify(`KnowledgeOS ${label}: ${error.message}`, true, null, surface);
      return null;
    } finally {
      this.inflight -= 1;
      this.renderStatus();
    }
  }

  // ---- feedback ------------------------------------------------------------------

  announce(label, receipt, surface = null) {
    const status = STATUS_LABELS[receipt.status] || receipt.status;
    const text = `KnowledgeOS ${label}: ${status}${receipt.notice ? ` — ${receipt.notice}` : ""}`;
    this.notify(text, ERROR_STATUSES.has(receipt.status), resultPath(receipt), surface);
  }

  notify(text, sticky, path = null, surface = null) {
    if (surface) return surface.notify(text, sticky, path);
    let message = text;
    if (path && typeof createFragment === "function") {
      message = createFragment((fragment) => {
        fragment.appendText(`${text} `);
        const link = fragment.createEl("a", { text: path, href: "#" });
        link.addEventListener("click", (event) => {
          event.preventDefault();
          void this.openNote(path);
        });
      });
    } else if (path) {
      message = `${text} → ${path}`;
    }
    return new Notice(message, sticky ? 0 : undefined);
  }

  async openNote(path, line) {
    const file = this.app.vault.getAbstractFileByPath(path);
    if (file instanceof TFile) {
      await this.app.workspace.getLeaf(false).openFile(file, line ? { eState: { line: Math.max(0, line - 1) } } : {});
    } else {
      await this.app.workspace.openLinkText(path, "", false);
    }
  }

  // ---- references ------------------------------------------------------------------

  async digestOf(file) {
    return sha256Hex(await this.app.vault.readBinary(file));
  }

  async noteReference(file) {
    return { path: file.path, digest: await this.digestOf(file) };
  }

  async targetReferences(items) {
    const notes = collectNotes(items);
    const paths = [];
    const digests = {};
    for (const note of notes) {
      paths.push(note.path);
      digests[note.path] = await this.digestOf(note);
    }
    return { paths, digests };
  }

  // ---- commands ----------------------------------------------------------------------

  prompt(title, fields, submit, surface) {
    if (surface) surface.input(title, fields, submit);
    else new InputModal(this.app, title, fields, submit).open();
  }

  async standardizePreview(items, surface = null) {
    const params = await this.targetReferences(items);
    if (!params.paths.length) {
      this.notify("규격화할 Markdown 노트가 없습니다.", false, null, surface);
      return;
    }
    const receipt = await this.request("규격화 미리보기", "standardize.preview", params, false, surface);
    if (!receipt || receipt.status !== "completed") return;
    const outcome = receipt.outcome || {};
    if (!(outcome.notes || []).length) {
      const skipped = (outcome.skipped || []).length;
      this.notify(`KnowledgeOS 규격화 미리보기: 바꿀 내용이 없습니다${skipped ? ` (건너뜀 ${skipped})` : ""}.`, false, null, surface);
      return;
    }
    const decide = (confirmed) => confirmed
      ? this.request("규격화 적용", "standardize.confirm", { preview_id: outcome.preview_id }, true, surface)
      : this.request("규격화 취소", "standardize.cancel", { preview_id: outcome.preview_id }, true, surface);
    if (surface) await surface.preview(outcome, decide);
    else new PreviewModal(this.app, outcome, decide).open();
  }

  async organize(items, surface = null) {
    const params = await this.targetReferences(items);
    if (!params.paths.length) {
      this.notify("정리할 Markdown 노트가 없습니다.", false, null, surface);
      return;
    }
    await this.request("정리 요청", "organize", params, true, surface);
  }

  async enhanceDevelop(file, surface = null) {
    await this.request("강화: 발전", "enhance", { mode: "develop", ...(await this.noteReference(file)) }, true, surface);
  }

  enhanceAsk(file, surface = null) {
    this.prompt("강화: 묻기", [{ key: "question", label: "질문", multiline: true }], async ({ question }) => {
      const reference = file ? await this.noteReference(file) : {};
      await this.request("강화: 묻기", "enhance", { mode: "ask", question, ...reference }, true, surface);
    }, surface);
  }

  enhanceReview(file, surface = null) {
    const today = new Date();
    const weekAgo = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 7);
    this.prompt("강화: 돌아보기", [
      { key: "from", label: "시작일", value: localDate(weekAgo), placeholder: "YYYY-MM-DD" },
      { key: "to", label: "종료일", value: localDate(today), placeholder: "YYYY-MM-DD" },
    ], async ({ from, to }) => {
      const valid = /^\d{4}-\d{2}-\d{2}$/;
      if (!valid.test(from) || !valid.test(to) || from > to) {
        this.notify("날짜는 YYYY-MM-DD 형식이고 시작일이 종료일보다 늦을 수 없습니다.", true, null, surface);
        return;
      }
      await this.request("강화: 돌아보기", "enhance", { mode: "review", ...(await this.noteReference(file)), window: { from, to } }, true, surface);
    }, surface);
  }

  freeText(surface = null) {
    this.prompt("자유 요청", [{ key: "text", label: "요청", multiline: true }], async ({ text }) => {
      await this.request("자유 요청", "free_text", { text }, true, surface);
    }, surface);
  }

  async relatedNotes(file, surface = null) {
    await this.search("관련 노트", { mode: "related", path: file.path, limit: SEARCH_LIMIT }, surface);
  }

  semanticSearch(surface = null) {
    this.prompt("의미 검색", [{ key: "query", label: "검색어" }], async ({ query }) => {
      await this.search("의미 검색", { mode: "semantic", query, limit: SEARCH_LIMIT }, surface);
    }, surface);
  }

  async search(label, payload, surface = null) {
    this.inflight += 1;
    this.renderStatus();
    if (surface) {
      surface.clear();
      surface.notify(`${label}: 찾는 중…`);
    }
    try {
      const response = await this.http("POST", "/api/search", JSON.stringify(payload));
      const data = response.data || {};
      if (response.status !== 200) {
        this.notify(`KnowledgeOS ${label}: HTTP ${response.status} ${data.error || ""}`.trim(), true, null, surface);
        return;
      }
      if (data.status === "unavailable") {
        this.notify(`KnowledgeOS ${label}: 검색을 사용할 수 없습니다 (unavailable). 결과가 없습니다.`, false, null, surface);
        return;
      }
      const results = data.results || [];
      if (!results.length) {
        this.notify(`KnowledgeOS ${label}: 결과가 없습니다.`, false, null, surface);
        return;
      }
      if (surface) {
        surface.results(results);
        surface.notify(`${label}: ${results.length}`);
      } else new ResultsModal(this.app, this, results).open();
    } catch (error) {
      this.notify(`KnowledgeOS ${label}: ${error.message}`, true, null, surface);
    } finally {
      this.inflight -= 1;
      this.renderStatus();
    }
  }

  async reviewDecision(file, command, label, surface = null) {
    await this.request(label, command, await this.noteReference(file), true, surface);
  }

  async reviewUndo(file, surface = null) {
    await this.request("자동 변경 되돌리기", "review.undo", await this.noteReference(file), true, surface);
  }

  openConsole(surface = null) {
    try {
      window.open(`${this.baseUrl()}/`);
    } catch (error) {
      this.notify(`KnowledgeOS 관리 콘솔: ${error.message}`, true, null, surface);
    }
  }

  // ---- Owner lifecycle ------------------------------------------------------------------
  // The Owner is an on-demand launchd user service (`knowledgeos install service`). These commands
  // only signal launchd and then report what GET /api/health shows; they send no note text.

  ownerStart(surface = null) {
    return this.ownerLifecycle("Owner 시작", async (say) => {
      const { label, target } = this.ownerService();
      const before = await this.probeOwner();
      if (before.state === "ready") return say("이미 실행 중입니다 (health 정상).", false);
      if (before.state === "unready") return say(`이미 실행 중이지만 준비되지 않았습니다${blockerText(before)}.`, true);
      const launched = await this.runLaunchctl(["kickstart", target]);
      if (!launched.ok) return say(this.launchctlProblem(launched, label).text, true);
      const { reached, probe } = await this.pollOwner((state) => state === "ready", OWNER_START_POLLS);
      if (reached) return say("시작되었습니다 (health 정상).", false);
      if (probe.state === "unready") {
        return say(`Owner가 응답하지만 약 ${OWNER_START_SECONDS}초 후에도 준비되지 않았습니다${blockerText(probe)}.`, true);
      }
      return say(`시작을 요청했지만 약 ${OWNER_START_SECONDS}초 안에 health 응답이 없습니다. Runtime logs의 owner.err.log를 확인하세요.`, true);
    }, surface);
  }

  ownerStop(surface = null) {
    return this.ownerLifecycle("Owner 중지", async (say) => {
      const { label, target } = this.ownerService();
      const before = await this.probeOwner();
      const sent = await this.runLaunchctl(["kill", "SIGTERM", target]);
      if (!sent.ok) {
        const problem = this.launchctlProblem(sent, label);
        if (problem.kind !== "idle") return say(problem.text, true);
        if (before.state === "down") return say("실행 중이 아닙니다 (launchd에 등록되어 있고 프로세스는 없습니다).", false);
        return say("launchd가 관리하는 Owner 프로세스는 없지만 endpoint는 응답합니다. 터미널 등에서 직접 실행한 Owner일 수 있어 중지하지 않았습니다.", true);
      }
      const { reached } = await this.pollOwner((state) => state === "down", OWNER_STOP_POLLS);
      if (!reached) return say(`SIGTERM을 보냈지만 약 ${OWNER_STOP_SECONDS}초 후에도 Owner가 응답합니다. 종료 중이거나 아직 실행 중입니다.`, true);
      if (before.state === "down") return say("SIGTERM을 보냈습니다. 보내기 전에도 health 응답이 없어 중지 여부를 확인할 수 없습니다 (unknown).", true);
      return say("중지되었습니다 (health 응답 없음).", false);
    }, surface);
  }

  // One lifecycle operation at a time; `act` reports through `say(text, sticky)`.
  async ownerLifecycle(title, act, surface = null) {
    const say = (text, sticky) => void this.notify(`KnowledgeOS ${title}: ${text}`, sticky, null, surface);
    if (this.lifecycleBusy) {
      say("이미 진행 중인 Owner 시작/중지가 있습니다.", false);
      return;
    }
    this.lifecycleBusy = true;
    this.inflight += 1;
    this.renderStatus();
    try {
      await act(say);
    } catch (error) {
      say(error.message, true);
    } finally {
      this.lifecycleBusy = false;
      this.inflight -= 1;
      this.renderStatus();
    }
  }

  ownerService() {
    const label = this.settings.ownerLabel;
    if (!OWNER_LABEL_PATTERN.test(label)) throw new Error(`launchd 라벨이 올바르지 않습니다: ${label}`);
    return { label, target: `gui/${process.getuid()}/${label}` };
  }

  // Resolves with the exit code (null when launchctl never ran), a Node errno string or "", and its output.
  runLaunchctl(args) {
    return new Promise((resolve) => {
      try {
        const { execFile } = require("child_process");
        execFile("launchctl", args, { timeout: LAUNCHCTL_TIMEOUT_MS }, (error, stdout, stderr) => {
          const output = `${stderr || ""}${stdout || ""}`.trim();
          if (!error) resolve({ ok: true, code: 0, errno: "", output });
          else resolve({
            ok: false,
            code: typeof error.code === "number" ? error.code : null,
            errno: typeof error.code === "string" ? error.code : "",
            output: output || error.message,
          });
        });
      } catch (error) {
        resolve({ ok: false, code: null, errno: "EXEC", output: error.message });
      }
    });
  }

  // kind: "missing" (no launchctl), "unregistered" (not loaded in launchd), "idle" (loaded, no process), "failed".
  launchctlProblem(result, label) {
    if (result.errno === "ENOENT") {
      return { kind: "missing", text: "launchctl을 실행할 수 없습니다 (macOS 데스크톱에서만 지원)." };
    }
    if (result.code === 113 || /could not find service/i.test(result.output)) {
      return {
        kind: "unregistered",
        text: `launchd에 ${label} 서비스가 등록되어 있지 않습니다. 터미널에서 \`knowledgeos install service --config <owner.json>\`을 먼저 실행하세요.`,
      };
    }
    const idle = result.code === 3 || /no such process/i.test(result.output);
    return { kind: idle ? "idle" : "failed", text: `launchctl 실패 (코드 ${result.code === null ? "없음" : result.code}): ${result.output}` };
  }

  // One health observation: "ready", "unready" (answers, blockers listed) or "down" (no answer).
  async probeOwner() {
    try {
      const response = await this.http("GET", "/api/health");
      const data = response.data || {};
      const ready = response.status === 200 && data.ready === true;
      return { state: ready ? "ready" : "unready", blockers: Array.isArray(data.blockers) ? data.blockers : [] };
    } catch (error) {
      if (error instanceof NetworkError) return { state: "down", blockers: [] };
      throw error;
    }
  }

  // Bounded: at most `polls` observations, OWNER_POLL_MS apart. `reached` says whether `done(state)` held.
  async pollOwner(done, polls) {
    let probe = { state: "down", blockers: [] };
    for (let attempt = 0; attempt < polls; attempt += 1) {
      await sleep(OWNER_POLL_MS);
      probe = await this.probeOwner();
      if (done(probe.state)) return { reached: true, probe };
    }
    return { reached: false, probe };
  }
}

module.exports = KnowledgeOSThinClient;
