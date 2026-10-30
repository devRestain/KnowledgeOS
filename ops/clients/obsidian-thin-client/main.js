const { ItemView, Notice, Plugin, PluginSettingTab, Setting, requestUrl } = require("obsidian");

const DEFAULT_SETTINGS = { ownerEndpoint: "http://127.0.0.1:17900/owner", lastWorkRunId: "" };
const VIEW_TYPE = "knowledgeos-owner-sidebar";

function endpoint(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !url.port || url.username ||
      url.password || url.search || url.hash || url.pathname !== "/owner") {
    throw new Error("The owner endpoint must be the configured loopback /owner route.");
  }
  return url.origin + url.pathname;
}

async function sha256(text) {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (item) => item.toString(16).padStart(2, "0")).join("");
}

async function ownerCall(settings, token, route, payload) {
  if (!token || token.length > 256 || /\s/.test(token)) throw new Error("Enter the owner session token.");
  const response = await requestUrl({
    url: `${endpoint(settings.ownerEndpoint)}/${route}`, method: "POST",
    headers: { "Content-Type": "application/json", "X-KnowledgeOS-CSRF": token },
    body: JSON.stringify(payload), throw: false,
  });
  if (response.status < 200 || response.status >= 300 || !response.json || response.json.status === "FAIL") {
    throw new Error(`Owner rejected the request: ${response.json?.errors?.[0]?.message || response.json?.message || response.status}`);
  }
  return response.json;
}

class WorkReviewSidebar extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.selectedPath = null;
    this.mode = "lexical";
    this.query = "";
    this.searchState = null;
    this.searchRequest = 0;
    this.workListRequest = 0;
    this.workResultRequest = 0;
    this.requestIds = new Map();
  }
  getViewType() { return VIEW_TYPE; }
  getDisplayText() { return "KnowledgeOS"; }
  getIcon() { return "network"; }
  async onOpen() {
    this.contentEl.addClass("knowledgeos-sidebar");
    this.selectedPath = this.app.workspace.getActiveFile()?.path || null;
    this.render();
    this.registerEvent(this.app.workspace.on("file-open", (file) => {
      this.setTarget(file?.path || null);
    }));
    this.registerEvent(this.app.workspace.on("active-leaf-change", (leaf) => {
      if (leaf === this.leaf) return;
      const file = this.app.workspace.getActiveFile();
      if (file || leaf?.view?.getViewType() === "empty") this.setTarget(file?.path || null);
    }));
  }
  get token() { return this.plugin.sessionToken; }
  set token(value) { this.plugin.sessionToken = value; }
  setTarget(path) {
    if (path === this.selectedPath) return;
    this.selectedPath = path;
    this.workListRequest += 1;
    this.workResultRequest += 1;
    const target = this.contentEl.querySelector(".knowledgeos-target");
    if (target) target.textContent = path || "No active note";
    for (const selector of [".knowledgeos-work-list", ".knowledgeos-work-result",
      ".knowledgeos-proposal-detail", ".knowledgeos-work-status"]) {
      this.contentEl.querySelector(selector)?.remove();
    }
    if (this.token) void this.showWorkList();
  }
  async scope() {
    if (!this.selectedPath) throw new Error("Open a Markdown note first.");
    const file = this.app.vault.getAbstractFileByPath(this.selectedPath);
    if (!file || file.extension !== "md") throw new Error("The selected note is unavailable.");
    return { path: file.path, content_sha256: await sha256(await this.app.vault.read(file)) };
  }

  render() {
    const root = this.contentEl;
    root.empty();
    root.createEl("h2", { text: "KnowledgeOS" });
    new Setting(root).setName("Owner session token").addText((field) => {
      field.inputEl.type = "password";
      field.setPlaceholder("Session token").setValue(this.token).onChange((value) => { this.token = value; });
      field.inputEl.addEventListener("change", () => {
        if (this.token) void this.refresh();
      });
    });
    root.createEl("p", { cls: "knowledgeos-target", text: this.selectedPath || "No active note" });
    const templates = root.createDiv({ cls: "knowledgeos-work-actions" });
    templates.createEl("button", { text: "Establish properties, tags and relations" })
      .onclick = () => this.submitTemplate("note_metadata");
    templates.createEl("button", { text: "Request document enhancement" })
      .onclick = () => this.submitTemplate("document_enhancement");
    const search = root.createDiv({ cls: "knowledgeos-search" });
    search.createEl("h3", { text: "Search documents" });
    const searchInput = search.createEl("input", { type: "search", value: this.query,
      attr: { "aria-label": "Search KnowledgeOS documents" } });
    searchInput.placeholder = "Search the owner corpus";
    searchInput.oninput = () => { this.query = searchInput.value; };
    searchInput.onkeydown = (event) => { if (event.key === "Enter") void this.search(); };
    const mode = search.createEl("select", { attr: { "aria-label": "Search mode" } });
    for (const name of ["lexical", "vector", "hybrid", "ontology"]) {
      mode.createEl("option", { text: name[0].toUpperCase() + name.slice(1), value: name });
    }
    mode.value = this.mode;
    mode.onchange = () => { this.mode = mode.value; };
    search.createEl("button", { text: "Search" }).onclick = () => this.search();
    this.renderSearchState();
    const question = root.createEl("textarea", { attr: { rows: "4", maxlength: "8192" } });
    question.placeholder = "Ask the admitted KnowledgeOS team to investigate…";
    const actions = root.createDiv({ cls: "knowledgeos-work-actions" });
    actions.createEl("button", { text: "Submit Work" }).onclick = async () => {
      try {
        if (!question.value.trim()) throw new Error("Enter a request.");
        const result = await ownerCall(this.plugin.settings, this.token, "work/intake", {
          question: question.value.trim(),
          scope: await this.scope(),
        });
        const id = result.work_run_id || (result.run && result.run.work_run_id);
        if (id) {
          this.plugin.settings.lastWorkRunId = id;
          await this.plugin.saveSettings();
        }
        new Notice("Work accepted by KnowledgeOS owner.");
        question.value = "";
        await this.showWorkList();
      } catch (error) { new Notice(error.message); }
    };
    actions.createEl("button", { text: "Refresh Work status" }).onclick = async () => {
      try {
        if (!this.plugin.settings.lastWorkRunId) throw new Error("No Work ID is selected.");
        const result = await ownerCall(this.plugin.settings, this.token, "work/read", {
          work_run_id: this.plugin.settings.lastWorkRunId,
        });
        const panel = root.querySelector(".knowledgeos-work-status") || root.createEl("pre", { cls: "knowledgeos-work-status" });
        panel.textContent = JSON.stringify(result, null, 2).slice(0, 16000);
      } catch (error) { new Notice(error.message); }
    };
    actions.createEl("button", { text: "Pending proposals" }).onclick = () => this.showProposals();
    actions.createEl("button", { text: "Review daily enrichment" }).onclick = () => this.showDaily();
    actions.createEl("button", { text: "Work for note" }).onclick = () => this.showWorkList();
  }

  async submitTemplate(templateId) {
    try {
      const scope = await this.scope();
      const key = `${templateId}:${scope.path}:${scope.content_sha256}`;
      if (!this.requestIds.has(key)) this.requestIds.set(key, globalThis.crypto.randomUUID());
      const result = await ownerCall(this.plugin.settings, this.token, "work/intake-template", {
        template_id: templateId, scope, request_id: this.requestIds.get(key),
      });
      const first = result.receipts?.[0];
      if (first?.work_run_id) {
        this.plugin.settings.lastWorkRunId = first.work_run_id;
        await this.plugin.saveSettings();
      }
      new Notice(result.reconciliation_required
        ? "An earlier Work delivery needs owner reconciliation before a new request."
        : result.replayed ? "Owner returned the existing Work receipt."
          : "Owner accepted the structured Work request.");
      await this.showWorkList();
    } catch (error) { new Notice(error.message); }
  }

  async search() {
    const request = ++this.searchRequest;
    try {
      if (!this.query.trim()) throw new Error("Enter a search query.");
      this.searchState = { status: "loading", mode: this.mode };
      this.renderSearchState();
      const result = await ownerCall(this.plugin.settings, this.token, "search/query", {
        query: this.query.trim(), mode: this.mode,
      });
      if (request !== this.searchRequest) return;
      this.searchState = { status: "ready", result };
    } catch (error) {
      if (request !== this.searchRequest) return;
      this.searchState = { status: "error", message: error.message };
    }
    this.renderSearchState();
  }

  renderSearchState() {
    const section = this.contentEl.querySelector(".knowledgeos-results") ||
      this.contentEl.createDiv({ cls: "knowledgeos-results" });
    section.empty();
    const state = this.searchState;
    if (!state) return;
    if (state.status === "loading") { section.createEl("p", { text: `Searching ${state.mode}…` }); return; }
    if (state.status === "error") { section.createEl("p", { text: state.message }); return; }
    const result = state.result;
    section.createEl("h3", { text: `Document results · ${result.mode}` });
    for (const item of result.results || []) {
        const card = section.createDiv({ cls: "knowledgeos-result-card" });
        card.createEl("button", { text: item.title || item.path }).onclick = async () => {
          const file = this.app.vault.getAbstractFileByPath(item.path);
          if (!file) { new Notice("This result is no longer in the Vault."); return; }
          await this.app.workspace.getLeaf("tab").openFile(file);
        };
        card.createEl("small", { text: item.path });
        card.createEl("p", { text: item.excerpt || "" });
        card.createEl("small", { text: item.reason || "" });
    }
    if (!result.results?.length) section.createEl("p", { text: "No matching documents." });
  }

  async showWorkList() {
    const request = ++this.workListRequest;
    const path = this.selectedPath;
    const section = this.contentEl.querySelector(".knowledgeos-work-list") ||
      this.contentEl.createDiv({ cls: "knowledgeos-work-list" });
    section.empty();
    section.createEl("h3", { text: "Work for this note" });
    if (!path) { section.createEl("p", { text: "Open a note to see its Work." }); return; }
    try {
      const result = await ownerCall(this.plugin.settings, this.token, "work/list", { path });
      if (request !== this.workListRequest || path !== this.selectedPath) return;
      for (const item of result.works || []) {
        const card = section.createDiv({ cls: "knowledgeos-work-card" });
        card.createEl("strong", { text: `${item.template_id || "Custom Work"} · ${item.team_id || "Team"}` });
        card.createEl("p", { text: `Receipt: ${item.receipt_state}; dispatch: ${item.dispatch_state}; execution: ${item.run_state || item.application_state}; evaluation: ${item.evaluation_state || "pending"}; human review: ${item.proposal_reference ? "available" : "not ready"}` });
        if (item.template_id === "note_metadata") card.createEl("p", {
          text: `Metadata proposal: ${item.metadata_promotion_state || "pending"}${item.metadata_promotion_code ? ` (${item.metadata_promotion_code})` : ""}`,
        });
        if (item.source_stale) card.createEl("p", { text: "Source changed since this Work was admitted. Request new Work for the current note." });
        card.createEl("button", { text: "View result" }).onclick = () => this.showWorkResult(item.work_run_id);
        if (item.proposal_reference) card.createEl("button", { text: "Review combined proposal" })
          .onclick = () => this.showProposal(item.proposal_reference);
      }
      const grouped = new Map();
      for (const item of result.works || []) {
        if (item.template_id === "note_metadata") {
          const members = grouped.get(item.request_id) || [];
          members.push(item);
          grouped.set(item.request_id, members);
        }
      }
      for (const [requestId, members] of grouped) {
        if (members.length === 2 && members.every((item) => !item.source_stale && item.evaluation_state === "passed"
            && item.metadata_promotion_state !== "no_change")
            && members.every((item) => !item.proposal_reference)) {
          section.createEl("button", { text: "Retry combined metadata proposal" }).onclick = async () => {
            try {
              const prepared = await ownerCall(this.plugin.settings, this.token, "work/prepare-metadata",
                { request_id: requestId });
              if (prepared.proposal_reference) await this.showProposal(prepared.proposal_reference);
              await this.showWorkList();
            } catch (error) { new Notice(error.message); }
          };
        }
      }
      if (!result.works?.length) section.createEl("p", { text: "No Work for this note." });
    } catch (error) {
      if (request === this.workListRequest && path === this.selectedPath)
        section.createEl("p", { text: error.message });
    }
  }

  async showWorkResult(workRunId) {
    const request = ++this.workResultRequest;
    const section = this.contentEl.querySelector(".knowledgeos-work-result") ||
      this.contentEl.createDiv({ cls: "knowledgeos-work-result" });
    section.empty();
    section.createEl("h3", { text: "Work result and evaluation" });
    try {
      const result = await ownerCall(this.plugin.settings, this.token, "work/result", { work_run_id: workRunId });
      if (request !== this.workResultRequest) return;
      section.createEl("p", { text: `${result.work_run_id} · ${result.state}` });
      if (result.assessment) section.createEl("pre", { text: JSON.stringify(result.assessment, null, 2) });
      if (result.report) {
        section.createEl("h4", { text: "Improvements" });
        for (const item of result.report.improvements) {
          const card = section.createDiv({ cls: "knowledgeos-result-card" });
          card.createEl("p", { text: item.point });
          card.createEl("small", { text: `Evidence: ${item.citation_indices.map((index) => `[${index + 1}]`).join(", ")}` });
        }
        section.createEl("h4", { text: "Cited evidence" });
        for (const [index, item] of (result.evidence || []).entries()) {
          const card = section.createDiv({ cls: "knowledgeos-result-card" });
          card.createEl("strong", { text: `[${index + 1}] ${item.path} · ${item.chunk_locator}` });
          card.createEl("p", { text: item.excerpt });
        }
        for (const [label, values] of [["Uncertainty", result.report.uncertainty],
          ["Missing materials", result.report.missing_materials]]) {
          section.createEl("h4", { text: label });
          if (!values.length) section.createEl("p", { text: "None reported." });
          for (const value of values) section.createEl("p", { text: value });
        }
      } else if (result.text) section.createEl("pre", { text: result.text });
      if (result.proposal_reference) section.createEl("button", { text: "Review proposal" })
        .onclick = () => this.showProposal(result.proposal_reference);
      if (!result.text && !result.report)
        section.createEl("p", { text: result.reason || "No evaluated result is available yet." });
    } catch (error) {
      if (request === this.workResultRequest) section.createEl("p", { text: error.message });
    }
  }

  async refresh() { await this.showWorkList(); await this.showProposals(); await this.showDaily(); }

  async showDaily() {
    try {
      const result = await ownerCall(this.plugin.settings, this.token, "daily/list", {});
      const section = this.contentEl.querySelector(".knowledgeos-daily") ||
        this.contentEl.createDiv({ cls: "knowledgeos-daily" });
      section.empty();
      section.createEl("h3", { text: "Daily enrichment awaiting review" });
      for (const item of result.pending || []) {
        section.createEl("button", { text: `${item.path} · ${item.review_status} · ${Object.keys(item.fields).join(", ")}` })
          .onclick = () => this.showDailyItem(item.effect_id);
      }
      if (!result.pending?.length) section.createEl("p", { text: "No daily changes await review." });
    } catch (error) { new Notice(error.message); }
  }

  async showDailyItem(effectId) {
    try {
      const item = await ownerCall(this.plugin.settings, this.token, "daily/read", { effect_id: effectId });
      const section = this.contentEl.querySelector(".knowledgeos-daily-detail") ||
        this.contentEl.createDiv({ cls: "knowledgeos-daily-detail" });
      section.empty();
      section.createEl("h3", { text: item.path });
      section.createEl("pre", { text: JSON.stringify({
        before_digest: item.before_digest, current_digest: item.current_digest,
        original: item.original_fields, added: item.added_fields,
        current: item.current_fields, evidence: item.evidence,
      }, null, 2) });
      section.createEl("pre", { text: item.body_excerpt });
      if (item.review_status === "conflict") {
        section.createEl("p", { text: "The note changed after the provisional write. Resolve this in owner review." });
        return;
      }
      for (const decision of ["confirm", "reject"]) {
        section.createEl("button", { text: decision === "confirm" ? "Confirm additions" : "Reject additions" })
          .onclick = async () => {
            try {
              await ownerCall(this.plugin.settings, this.token, "daily/decide", {
                effect_id: effectId, decision,
              });
              await this.showDaily();
              section.empty();
            } catch (error) { new Notice(error.message); }
          };
      }
    } catch (error) { new Notice(error.message); }
  }

  async showProposals() {
    try {
      const result = await ownerCall(this.plugin.settings, this.token, "proposal/list", {});
      const root = this.contentEl;
      const section = root.querySelector(".knowledgeos-proposals") || root.createDiv({ cls: "knowledgeos-proposals" });
      section.empty();
      section.createEl("h3", { text: "Pending review" });
      for (const item of result.proposals || []) {
        section.createEl("button", { text: `${item.action} · ${item.proposal_reference.logical_id}` })
          .onclick = () => this.showProposal(item.proposal_reference);
      }
    } catch (error) { new Notice(error.message); }
  }

  async showProposal(reference) {
    try {
      const result = await ownerCall(this.plugin.settings, this.token, "proposal/read", {
        proposal_reference: reference,
      });
      const root = this.contentEl;
      const section = root.querySelector(".knowledgeos-proposal-detail") || root.createDiv({ cls: "knowledgeos-proposal-detail" });
      section.empty();
      section.createEl("h3", { text: "Source, content, and diff" });
      section.createEl("pre", { text: JSON.stringify({ path: result.source_path,
        bound_digest: result.source_digest, current_reference: result.source_reference,
        stale: result.source_stale }, null, 2) });
      if (result.field_evidence) {
        section.createEl("h4", { text: "Proposed values and evidence" });
        for (const item of result.field_evidence) {
          const card = section.createDiv({ cls: "knowledgeos-result-card" });
          card.createEl("strong", { text: `${item.field}: ${item.value}` });
          for (const quote of item.evidence) card.createEl("p", { text: quote });
        }
        if (!result.field_evidence.length) section.createEl("p", { text: "No new values proposed." });
      }
      if (result.target_path) section.createEl("p", { text: `Target: ${result.target_path}` });
      if (result.diff !== null) section.createEl("pre", { text: result.diff || "No canonical line changes." });
      const fullArtifact = section.createEl("details");
      fullArtifact.createEl("summary", { text: "Full proposal artifact" });
      fullArtifact.createEl("pre", { text: result.text.slice(0, 65536) });
      if (result.decision) section.createEl("p", { text: `Decision: ${result.decision.decision || result.decision.response?.chosen_action}` });
      for (const choice of ["approve", "reject"]) {
        section.createEl("button", { text: choice === "approve" ? "Approve" : "Reject" }).onclick = async () => {
          try {
            await ownerCall(this.plugin.settings, this.token, "proposal/decide", {
              proposal_reference: reference, decision: choice, reason: "Reviewed in Obsidian",
            });
            new Notice(`Owner recorded ${choice}.`);
            await this.showProposal(reference);
          } catch (error) { new Notice(error.message); }
        };
      }
      if (result.apply_allowed) {
        section.createEl("button", { text: "Apply approved proposal" }).onclick = async () => {
          try {
            await ownerCall(this.plugin.settings, this.token, "proposal/apply", { proposal_reference: reference });
            new Notice("Owner applied the approved canonical change.");
            await this.showProposal(reference);
          } catch (error) { new Notice(error.message); }
        };
      }
    } catch (error) { new Notice(error.message); }
  }
}

class OwnerSettingsTab extends PluginSettingTab {
  display() {
    this.containerEl.empty();
    new Setting(this.containerEl).setName("KnowledgeOS owner endpoint")
      .setDesc("Authenticated local Work and review route")
      .addText((field) => field.setValue(this.plugin.settings.ownerEndpoint).onChange(async (value) => {
        this.plugin.settings.ownerEndpoint = value;
        await this.plugin.saveSettings();
      }));
  }
}

class KnowledgeOSThinClient extends Plugin {
  async onload() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
    this.sessionToken = "";
    this.registerView(VIEW_TYPE, (leaf) => new WorkReviewSidebar(leaf, this));
    this.addSettingTab(new OwnerSettingsTab(this.app, this));
    this.addCommand({ id: "open-work-review", name: "Open KnowledgeOS sidebar",
                      callback: () => this.activateView() });
    this.addRibbonIcon("network", "KnowledgeOS sidebar", () => this.activateView());
  }
  async activateView() {
    const leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0] || this.app.workspace.getRightLeaf(false);
    await leaf.setViewState({ type: VIEW_TYPE, active: true });
    this.app.workspace.revealLeaf(leaf);
  }
  async saveSettings() { await this.saveData(this.settings); }
}

module.exports = KnowledgeOSThinClient;
module.exports.endpoint = endpoint;
module.exports.ownerCall = ownerCall;
