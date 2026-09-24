const {
  MarkdownView,
  Modal,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  requestUrl,
} = require("obsidian");

const CAPABILITY = "C41";
const OPERATION = "ai thin client";
const DEFAULT_SETTINGS = Object.freeze({
  brokerEndpoint: "http://127.0.0.1:17900/broker",
  policySha256: "",
  privacyPolicySha256: "",
  indexGenerationId: "",
});
const MAX_QUESTION_BYTES = 8 * 1024;
const MAX_LOCATOR_BYTES = 512;
const MAX_SELECTION_BYTES = 16 * 1024;
const MAX_DIFF_BYTES = 32 * 1024;
const MAX_RESULT_BYTES = 64 * 1024;
const SHA256 = /^[0-9a-f]{64}$/;
const SAFE_SCOPE_KINDS = new Set(["current_note", "selection"]);
const REVIEW_DECISIONS = new Set(["approve", "reject"]);

class ThinClientError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "ThinClientError";
    this.code = code;
  }
}

function utf8Bytes(value) {
  return new TextEncoder().encode(value);
}

function byteLength(value) {
  return utf8Bytes(value).byteLength;
}

function boundedText(value, label, maximum) {
  if (typeof value !== "string" || !value.trim() || value.includes("\u0000")) {
    throw new ThinClientError("C41_TEXT_INVALID", `${label} must be non-empty UTF-8 text`);
  }
  if (byteLength(value) > maximum) {
    throw new ThinClientError("C41_TEXT_TOO_LARGE", `${label} exceeds its byte limit`);
  }
  return value;
}

function validateHash(value, label) {
  if (typeof value !== "string" || !SHA256.test(value)) {
    throw new ThinClientError("C41_HASH_INVALID", `${label} must be a lowercase SHA-256`);
  }
  return value;
}

function safeRelativePath(value, label) {
  if (
    typeof value !== "string" ||
    !value ||
    value.includes("\u0000") ||
    value.includes("\\") ||
    value.startsWith("/")
  ) {
    throw new ThinClientError("C41_PATH_INVALID", `${label} must be a safe relative path`);
  }
  const parts = value.split("/");
  if (parts.some((part) => !part || part === "." || part === "..")) {
    throw new ThinClientError("C41_PATH_INVALID", `${label} must be a safe relative path`);
  }
  return parts.join("/");
}

function safeLocator(value, label) {
  if (value === null || value === undefined) {
    return null;
  }
  return boundedText(value, label, MAX_LOCATOR_BYTES);
}

function validateLoopbackEndpoint(value) {
  if (typeof value !== "string" || value.length > 200) {
    throw new ThinClientError("C41_BROKER_ENDPOINT_INVALID", "broker endpoint must be a bounded URL");
  }
  let parsed;
  try {
    parsed = new URL(value);
  } catch (_error) {
    throw new ThinClientError("C41_BROKER_ENDPOINT_INVALID", "broker endpoint is not a URL");
  }
  if (
    parsed.protocol !== "http:" ||
    parsed.hostname !== "127.0.0.1" ||
    parsed.username ||
    parsed.password ||
    !parsed.port ||
    parsed.search ||
    parsed.hash ||
    Number(parsed.port) < 1 ||
    Number(parsed.port) > 65535
  ) {
    throw new ThinClientError(
      "C41_BROKER_ENDPOINT_INVALID",
      "broker endpoint must use authenticated HTTP loopback on 127.0.0.1",
    );
  }
  return value;
}

function canonicalJson(value) {
  if (value === null || typeof value !== "object") {
    const scalar = JSON.stringify(value);
    if (scalar === undefined) {
      throw new ThinClientError("C41_SERIALIZATION_INVALID", "request contains an unsupported value");
    }
    return scalar;
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  return `{${Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
    .join(",")}}`;
}

async function sha256Hex(value) {
  const webCrypto = globalThis.crypto;
  if (!webCrypto || !webCrypto.subtle) {
    throw new ThinClientError("C41_CRYPTO_UNAVAILABLE", "Web Crypto SHA-256 is unavailable");
  }
  const bytes = typeof value === "string" ? utf8Bytes(value) : value;
  const digest = await webCrypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function withoutRequestDigest(request) {
  const value = JSON.parse(JSON.stringify(request));
  delete value.request_sha256;
  return value;
}

function withoutAuthorizationDigest(request) {
  const value = withoutRequestDigest(request);
  value.broker.auth_sha256 = "";
  return value;
}

async function authorizationDigest(token, request) {
  return sha256Hex(`${token}:${await sha256Hex(canonicalJson(withoutAuthorizationDigest(request)))}`);
}

async function requestDigest(request) {
  return sha256Hex(canonicalJson(withoutRequestDigest(request)));
}

function newUuid4() {
  const webCrypto = globalThis.crypto;
  if (!webCrypto || !webCrypto.getRandomValues) {
    throw new ThinClientError("C41_CRYPTO_UNAVAILABLE", "Web Crypto UUID generation is unavailable");
  }
  const bytes = new Uint8Array(16);
  webCrypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function validateSettings(settings) {
  validateLoopbackEndpoint(settings.brokerEndpoint);
  validateHash(settings.policySha256, "policySha256");
  validateHash(settings.privacyPolicySha256, "privacyPolicySha256");
  if (
    typeof settings.indexGenerationId !== "string" ||
    !settings.indexGenerationId.trim() ||
    settings.indexGenerationId.length > 128
  ) {
    throw new ThinClientError("C41_GENERATION_INVALID", "indexGenerationId is invalid");
  }
}

async function buildC41Request({ question, scopeKind, path, contentText, selectionText, settings, token }) {
  validateSettings(settings);
  boundedText(token, "authorization token", 256);
  if (/\s/.test(token)) {
    throw new ThinClientError("C41_AUTH_INVALID", "authorization token must not contain whitespace");
  }
  const questionText = boundedText(question, "question", MAX_QUESTION_BYTES);
  const relativePath = safeRelativePath(path, "scope.path");
  const contentSha256 = await sha256Hex(contentText);
  const scope = {
    kind: scopeKind,
    path: relativePath,
    locator: null,
    content_sha256: contentSha256,
    selection_sha256: null,
    selection_byte_length: null,
  };
  if (!SAFE_SCOPE_KINDS.has(scopeKind)) {
    throw new ThinClientError("C41_SCOPE_INVALID", "scope kind must be current_note or selection");
  }
  if (scopeKind === "selection") {
    const selected = boundedText(selectionText, "selection", MAX_SELECTION_BYTES);
    scope.selection_sha256 = await sha256Hex(selected);
    scope.selection_byte_length = byteLength(selected);
  }

  const request = {
    schema_version: 1,
    capability: CAPABILITY,
    operation: OPERATION,
    job_id: newUuid4(),
    request_id: newUuid4(),
    created_at: new Date().toISOString(),
    action: "answer",
    question: {
      text: questionText,
      sha256: await sha256Hex(questionText),
      byte_length: byteLength(questionText),
    },
    scope,
    policy: {
      policy_sha256: settings.policySha256,
      privacy_policy_sha256: settings.privacyPolicySha256,
      index_generation_id: settings.indexGenerationId,
    },
    broker: {
      transport: "loopback",
      endpoint: settings.brokerEndpoint,
      audience: "knowledgeos-broker",
      auth_sha256: "",
    },
    proposal_only: true,
    canonical_apply_allowed: false,
    request_sha256: null,
  };
  request.broker.auth_sha256 = await authorizationDigest(token, request);
  request.request_sha256 = await requestDigest(request);
  return request;
}

function exactKeys(value, expected) {
  return Object.keys(value).sort().join("\u0000") === expected.slice().sort().join("\u0000");
}

function validateBrokerResponse(response, request) {
  const required = [
    "schema_version",
    "capability",
    "request_sha256",
    "brokered",
    "direct_provider_called",
    "content_digest_rechecked",
    "policy_digest_rechecked",
    "index_digest_rechecked",
    "mutation_performed",
    "canonical_apply_allowed",
    "proposal_only",
    "status",
    "result",
  ];
  if (!response || typeof response !== "object" || Array.isArray(response) || !exactKeys(response, required)) {
    throw new ThinClientError("C41_RESPONSE_INVALID", "broker response fields are not exact");
  }
  if (response.schema_version !== 1 || response.capability !== CAPABILITY) {
    throw new ThinClientError("C41_RESPONSE_INVALID", "broker response schema is invalid");
  }
  if (response.request_sha256 !== request.request_sha256) {
    throw new ThinClientError("C41_RESPONSE_BINDING", "broker response is not bound to the client request");
  }
  if (
    response.brokered !== true ||
    response.direct_provider_called !== false ||
    response.content_digest_rechecked !== true ||
    response.policy_digest_rechecked !== true ||
    response.index_digest_rechecked !== true ||
    response.mutation_performed !== false ||
    response.canonical_apply_allowed !== false ||
    response.proposal_only !== true
  ) {
    throw new ThinClientError("C41_AUTHORITY_INVALID", "broker response crossed a forbidden authority boundary");
  }
  if (!["completed", "refused", "failed", "conflict"].includes(response.status)) {
    throw new ThinClientError("C41_RESPONSE_INVALID", "broker response status is invalid");
  }
  if (!response.result || typeof response.result !== "object" || Array.isArray(response.result)) {
    throw new ThinClientError("C41_RESPONSE_INVALID", "broker result must be an object");
  }
  if (byteLength(canonicalJson(response.result)) > MAX_RESULT_BYTES) {
    throw new ThinClientError("C41_RESPONSE_TOO_LARGE", "broker result exceeds the bounded display limit");
  }
  return response;
}

async function callBroker(request, token) {
  const response = await requestUrl({
    url: request.broker.endpoint,
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: canonicalJson(request),
  });
  if (response.status < 200 || response.status >= 300) {
    throw new ThinClientError("C41_BROKER_HTTP", `broker returned HTTP ${response.status}`);
  }
  let payload;
  try {
    payload = JSON.parse(response.text || "");
  } catch (_error) {
    throw new ThinClientError("C41_RESPONSE_INVALID", "broker response is not JSON");
  }
  return validateBrokerResponse(payload, request);
}

async function buildReviewDecision(proposalSha256, decision, reason) {
  validateHash(proposalSha256, "proposal_sha256");
  if (!REVIEW_DECISIONS.has(decision)) {
    throw new ThinClientError("C41_DECISION_INVALID", "decision must be approve or reject");
  }
  const payload = {
    decision,
    proposal_sha256: proposalSha256,
    reason: reason ? boundedText(reason, "decision.reason", 1000) : null,
    reviewed_at: new Date().toISOString(),
    canonical_apply_requested: false,
    canonical_apply_allowed: false,
    next_authority: decision === "approve" ? "c19_review_required" : "none",
  };
  return {
    ...payload,
    decision_sha256: await sha256Hex(canonicalJson(payload)),
  };
}

function displayText(value, label, maximum) {
  if (typeof value !== "string" || !value.trim()) {
    return null;
  }
  if (byteLength(value) > maximum) {
    return `${label} omitted because it exceeds the bounded display limit`;
  }
  return value;
}

function citationTarget(citation) {
  if (!citation || citation.exact_source !== true || citation.canonical_apply_allowed !== false) {
    throw new ThinClientError("C41_CITATION_INVALID", "citation is not exact and review-only");
  }
  const path = safeRelativePath(citation.path, "citation.path");
  const locator = safeLocator(citation.locator || citation.chunk_locator, "citation.locator");
  if (!locator) {
    throw new ThinClientError("C41_CITATION_INVALID", "citation locator is required");
  }
  return { path, locator, target: `${path}${locator.startsWith("#") || locator.startsWith("^") ? locator : `#${locator}`}` };
}

class ThinClientModal extends Modal {
  constructor(app, plugin, initialScope = "current_note") {
    super(app);
    this.plugin = plugin;
    this.initialScope = initialScope;
    this.busy = false;
  }

  onOpen() {
    this.contentEl.empty();
    this.contentEl.addClass("kos-thin-client-modal");
    this.contentEl.createEl("h2", { text: "KnowledgeOS AI review" });
    this.contentEl.createEl("p", {
      text: "Presentation only: the client sends a digest-bound request to the local broker and never writes the Vault.",
      cls: "kos-thin-client-description",
    });

    this.contentEl.createEl("label", { text: "Question", cls: "kos-thin-client-label" });
    this.questionInput = this.contentEl.createEl("textarea");
    this.questionInput.rows = 5;
    this.questionInput.placeholder = "Ask about the current note or selection";
    this.questionInput.addClass("kos-thin-client-question");

    this.contentEl.createEl("label", { text: "Scope", cls: "kos-thin-client-label" });
    this.scopeSelect = this.contentEl.createEl("select");
    for (const [value, label] of [
      ["current_note", "Current note"],
      ["selection", "Current selection"],
    ]) {
      const option = this.scopeSelect.createEl("option", { text: label, value });
      option.selected = value === this.initialScope;
    }

    this.contentEl.createEl("label", { text: "Broker token (memory only; never saved)", cls: "kos-thin-client-label" });
    this.tokenInput = this.contentEl.createEl("input");
    this.tokenInput.type = "password";
    this.tokenInput.autocomplete = "off";
    this.tokenInput.placeholder = "Enter the current broker session token";
    this.tokenInput.addClass("kos-thin-client-token");

    this.configStatus = this.contentEl.createDiv({ cls: "kos-thin-client-status" });
    this.refreshConfigStatus();

    const controls = this.contentEl.createDiv({ cls: "kos-thin-client-controls" });
    this.submitButton = controls.createEl("button", { text: "Ask KnowledgeOS" });
    this.submitButton.addClass("mod-cta");
    this.submitButton.addEventListener("click", () => void this.submit());
    const closeButton = controls.createEl("button", { text: "Close" });
    closeButton.addEventListener("click", () => this.close());

    this.results = this.contentEl.createDiv({ cls: "kos-thin-client-results" });
  }

  refreshConfigStatus() {
    try {
      validateSettings(this.plugin.settings);
      this.configStatus.setText(`Configured for ${this.plugin.settings.brokerEndpoint}`);
      this.configStatus.removeClass("kos-thin-client-error");
    } catch (error) {
      this.configStatus.setText(`Configure the loopback broker first: ${error.message}`);
      this.configStatus.addClass("kos-thin-client-error");
    }
  }

  setBusy(value) {
    this.busy = value;
    this.submitButton.disabled = value;
    this.questionInput.disabled = value;
    this.scopeSelect.disabled = value;
    this.tokenInput.disabled = value;
  }

  setStatus(text, error = false) {
    const status = this.results.createDiv({ cls: "kos-thin-client-status" });
    status.setText(text);
    if (error) {
      status.addClass("kos-thin-client-error");
    }
    return status;
  }

  async currentScope() {
    const view = this.app.workspace.getActiveViewOfType(MarkdownView);
    const file = this.app.workspace.getActiveFile();
    if (!view || !view.file || !file || view.file.path !== file.path || !view.editor) {
      throw new ThinClientError("C41_SCOPE_UNAVAILABLE", "open a Markdown note before using the thin client");
    }
    const persistedText = await this.app.vault.read(file);
    const editorText = view.editor.getValue();
    if (persistedText !== editorText) {
      throw new ThinClientError("C41_SOURCE_DIRTY", "save the current note before creating a digest-bound request");
    }
    const kind = this.scopeSelect.value;
    const selection = kind === "selection" ? view.editor.getSelection() : null;
    if (kind === "selection" && !selection) {
      throw new ThinClientError("C41_SELECTION_EMPTY", "select non-empty text before choosing selection scope");
    }
    return {
      kind,
      path: file.path,
      contentText: persistedText,
      selectionText: selection,
    };
  }

  async submit() {
    if (this.busy) {
      return;
    }
    this.results.empty();
    let token = this.tokenInput.value;
    try {
      const scope = await this.currentScope();
      const request = await buildC41Request({
        question: this.questionInput.value,
        scopeKind: scope.kind,
        path: scope.path,
        contentText: scope.contentText,
        selectionText: scope.selectionText,
        settings: this.plugin.settings,
        token,
      });
      this.setBusy(true);
      this.setStatus("Sending one proposal-only request to the authenticated loopback broker…");
      const response = await callBroker(request, token);
      this.renderResponse(response);
    } catch (error) {
      const message = error instanceof ThinClientError ? `${error.code}: ${error.message}` : "request failed";
      this.setStatus(message, true);
      new Notice(message);
    } finally {
      token = "";
      this.tokenInput.value = "";
      this.setBusy(false);
    }
  }

  renderResponse(response) {
    this.results.empty();
    this.results.createEl("h3", { text: `Broker result: ${response.status}` });
    const result = response.result;
    const summary = displayText(result.summary || result.answer || result.message, "Result", 16 * 1024);
    if (summary) {
      this.results.createEl("pre", { text: summary, cls: "kos-thin-client-summary" });
    } else {
      this.results.createEl("p", { text: "The broker returned no displayable summary." });
    }

    const citations = Array.isArray(result.citations)
      ? result.citations
      : Array.isArray(result.citation_actions)
        ? result.citation_actions
        : [];
    if (citations.length) {
      const section = this.results.createDiv({ cls: "kos-thin-client-section" });
      section.createEl("h4", { text: "Exact citations" });
      for (const citation of citations.slice(0, 8)) {
        try {
          const target = citationTarget(citation);
          const button = section.createEl("button", { text: `${target.path}${target.locator}` });
          button.addEventListener("click", () => {
            void this.app.workspace.openLinkText(target.target, "", false);
          });
        } catch (_error) {
          section.createEl("p", { text: "A broker citation was rejected because its source binding was not exact." });
        }
      }
    }

    if (result.diff && typeof result.diff === "object") {
      const diff = result.diff;
      if (diff.bounded === true && diff.canonical_apply_allowed === false && typeof diff.diff === "string") {
        const diffText = displayText(diff.diff, "Diff", MAX_DIFF_BYTES);
        if (diffText) {
          const section = this.results.createDiv({ cls: "kos-thin-client-section" });
          section.createEl("h4", { text: "Bounded diff preview" });
          section.createEl("pre", { text: diffText, cls: "kos-thin-client-diff" });
        }
      }
    }

    if (typeof result.proposal_sha256 === "string" && SHA256.test(result.proposal_sha256)) {
      this.renderReviewControls(result.proposal_sha256);
    } else {
      this.results.createEl("p", {
        text: "No digest-bound proposal was returned; no review control is available.",
        cls: "kos-thin-client-muted",
      });
    }
  }

  renderReviewControls(proposalSha256) {
    const section = this.results.createDiv({ cls: "kos-thin-client-section" });
    section.createEl("h4", { text: "Human review" });
    section.createEl("p", {
      text: "Approval records a session-only handoff to C19 review. It never applies a Vault change.",
      cls: "kos-thin-client-muted",
    });
    const reasonInput = section.createEl("input");
    reasonInput.placeholder = "Optional rejection reason";
    const controls = section.createDiv({ cls: "kos-thin-client-controls" });
    for (const decision of ["approve", "reject"]) {
      const button = controls.createEl("button", { text: decision === "approve" ? "Approve for C19 review" : "Reject" });
      button.addEventListener("click", () => {
        void (async () => {
          try {
            const receipt = await buildReviewDecision(
              proposalSha256,
              decision,
              decision === "reject" ? reasonInput.value : null,
            );
            section.createEl("pre", {
              text: `${decision.toUpperCase()} recorded for this session\n${receipt.decision_sha256}`,
              cls: "kos-thin-client-receipt",
            });
            button.disabled = true;
          } catch (error) {
            section.createEl("p", { text: error.message, cls: "kos-thin-client-error" });
          }
        })();
      });
    }
  }
}

class ThinClientSettingTab extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "KnowledgeOS Thin Client" });
    containerEl.createEl("p", {
      text: "Only non-secret broker and digest bindings are stored here. Enter the broker token in the review dialog; it is never saved.",
      cls: "kos-thin-client-muted",
    });
    new Setting(containerEl)
      .setName("Broker endpoint")
      .setDesc("HTTP loopback endpoint; the client accepts only 127.0.0.1 with an explicit port")
      .addText((text) => {
        text.setValue(this.plugin.settings.brokerEndpoint);
        text.onChange(async (value) => {
          this.plugin.settings.brokerEndpoint = value.trim();
          await this.plugin.saveSettings();
        });
      });
    for (const [key, name, desc] of [
      ["policySha256", "Policy SHA-256", "Current policy digest supplied by the broker deployment"],
      ["privacyPolicySha256", "Privacy policy SHA-256", "Current privacy policy digest supplied by the broker deployment"],
      ["indexGenerationId", "Index generation ID", "Immutable retrieval generation identifier"],
    ]) {
      new Setting(containerEl)
        .setName(name)
        .setDesc(desc)
        .addText((text) => {
          text.setValue(this.plugin.settings[key]);
          text.onChange(async (value) => {
            this.plugin.settings[key] = value.trim();
            await this.plugin.saveSettings();
          });
        });
    }
  }
}

class KnowledgeOSThinClient extends Plugin {
  async onload() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
    this.addSettingTab(new ThinClientSettingTab(this.app, this));
    this.addCommand({
      id: "open-review",
      name: "Open KnowledgeOS review",
      callback: () => new ThinClientModal(this.app, this).open(),
    });
    this.addCommand({
      id: "ask-current-note",
      name: "Ask KnowledgeOS about current note",
      callback: () => new ThinClientModal(this.app, this, "current_note").open(),
    });
    this.addRibbonIcon("message-square", "Open KnowledgeOS review", () => {
      new ThinClientModal(this.app, this).open();
    });
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }
}

module.exports = KnowledgeOSThinClient;
