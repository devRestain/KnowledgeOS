const { MarkdownView, Modal, Notice, Plugin, PluginSettingTab, Setting, requestUrl } = require("obsidian");

const DEFAULT_SETTINGS = { ownerEndpoint: "http://127.0.0.1:17900/owner", lastWorkRunId: "" };

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
    throw new Error(`Owner rejected the request (${response.status}).`);
  }
  return response.json;
}

class WorkReviewModal extends Modal {
  constructor(app, plugin) { super(app); this.plugin = plugin; this.token = ""; }
  onOpen() { this.render(); }

  render() {
    const root = this.contentEl;
    root.empty();
    root.createEl("h2", { text: "KnowledgeOS Work" });
    new Setting(root).setName("Owner session token").addText((field) => {
      field.inputEl.type = "password";
      field.setPlaceholder("Session token").onChange((value) => { this.token = value; });
    });
    const question = root.createEl("textarea", { attr: { rows: "4", maxlength: "8192" } });
    question.placeholder = "Ask the admitted KnowledgeOS team to investigate…";
    const actions = root.createDiv({ cls: "knowledgeos-work-actions" });
    actions.createEl("button", { text: "Submit Work" }).onclick = async () => {
      try {
        const view = this.app.workspace.getActiveViewOfType(MarkdownView);
        if (!view || !view.file || !question.value.trim()) throw new Error("Open a note and enter a request.");
        const text = await this.app.vault.cachedRead(view.file);
        const result = await ownerCall(this.plugin.settings, this.token, "work/intake", {
          question: question.value.trim(),
          scope: { path: view.file.path, content_sha256: await sha256(text) },
        });
        const id = result.work_run_id || (result.run && result.run.work_run_id);
        if (id) {
          this.plugin.settings.lastWorkRunId = id;
          await this.plugin.saveSettings();
        }
        new Notice("Work accepted by KnowledgeOS owner.");
        this.render();
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
      section.createEl("pre", { text: result.text.slice(0, 65536) });
      section.createEl("pre", { text: JSON.stringify({ path: result.source_path,
        bound_digest: result.source_digest, current_reference: result.source_reference,
        stale: result.source_stale }, null, 2) });
      if (result.target_path) section.createEl("p", { text: `Target: ${result.target_path}` });
      if (result.diff !== null) section.createEl("pre", { text: result.diff || "No canonical line changes." });
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
    this.addSettingTab(new OwnerSettingsTab(this.app, this));
    this.addCommand({ id: "open-work-review", name: "Open KnowledgeOS Work and review",
                      callback: () => new WorkReviewModal(this.app, this).open() });
    this.addRibbonIcon("message-square", "KnowledgeOS Work", () => new WorkReviewModal(this.app, this).open());
  }
  async saveSettings() { await this.saveData(this.settings); }
}

module.exports = KnowledgeOSThinClient;
module.exports.endpoint = endpoint;
module.exports.ownerCall = ownerCall;
