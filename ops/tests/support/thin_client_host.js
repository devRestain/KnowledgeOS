"use strict";

// Deliberately bounded host adapter, NOT a browser, layout engine or Obsidian proof.
// It implements only the DOM/Obsidian surface consumed by this maintained client.
// Network responses are explicitly synthetic unless the runner selects --endpoint.
const path = require("node:path");
const { webcrypto } = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const activeHosts = new Set();
function cleanupHosts() {
  for (const cleanup of [...activeHosts]) cleanup();
}

class Element {
  constructor(tag = "div", options = {}) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parentElement = null;
    this.attributes = {};
    this._text = "";
    this.value = "";
    this.disabled = false;
    this.style = {};
    this.dataset = {};
    this.listeners = new Map();
    this.classList = {
      add: (...names) => names.forEach(name => this.classes.add(name)),
      remove: (...names) => names.forEach(name => this.classes.delete(name)),
      contains: name => this.classes.has(name),
      toggle: (name, force) => {
        const present = force === undefined ? !this.classes.has(name) : force;
        present ? this.classes.add(name) : this.classes.delete(name);
        return present;
      },
    };
    this.classes = new Set();
    if (options.text !== undefined) this.textContent = options.text;
    if (options.cls) this.addClass(...(Array.isArray(options.cls) ? options.cls : options.cls.split(" ")));
    for (const [key, value] of Object.entries(options.attr || {})) this.setAttribute(key, value);
    for (const key of ["type", "value", "title"]) if (options[key] !== undefined) this[key] = options[key];
  }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set textContent(value) { this.empty(); this._text = String(value ?? ""); }
  get innerText() { return this.textContent; }
  set innerText(value) { this.textContent = value; }
  get className() { return [...this.classes].join(" "); }
  set className(value) { this.classes = new Set(String(value).split(/\s+/).filter(Boolean)); }
  get isConnected() { return this._root === true || Boolean(this.parentElement?.isConnected); }
  createEl(tag, options = {}, callback) {
    const child = new Element(tag, options);
    this.appendChild(child);
    if (callback) callback(child);
    return child;
  }
  createDiv(options = {}, callback) { return this.createEl("div", typeof options === "string" ? { cls: options } : options, callback); }
  createSpan(options = {}, callback) { return this.createEl("span", options, callback); }
  appendChild(child) {
    child.remove();
    child.parentElement = this;
    if (this.tagName === "SELECT" && child.tagName === "OPTION" && !this.children.length) this.value = child.value;
    this.children.push(child);
    return child;
  }
  append(...children) { children.forEach(child => typeof child === "string" ? this.appendText(child) : this.appendChild(child)); }
  appendText(value) { this._text += String(value); }
  empty() {
    this.children.forEach(child => { child.parentElement = null; });
    this.children = [];
    this._text = "";
    if (this.tagName === "SELECT") this.value = "";
  }
  replaceChildren(...children) { this._text = ""; this.empty(); this.append(...children); }
  remove() {
    if (this.parentElement) {
      this.parentElement.children = this.parentElement.children.filter(child => child !== this);
      this.parentElement = null;
    }
  }
  addClass(...names) { this.classList.add(...names); }
  removeClass(...names) { this.classList.remove(...names); }
  toggleClass(name, enabled) { this.classList.toggle(name, enabled); }
  hasClass(name) { return this.classes.has(name); }
  setText(value) { this.textContent = value; return this; }
  setAttr(name, value) { this.setAttribute(name, value); return this; }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "class") this.className = value;
    if (name === "disabled") this.disabled = true;
  }
  getAttribute(name) {
    if (name.startsWith("data-")) return this.dataset[name.slice(5).replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase())] ?? null;
    return this.attributes[name] ?? null;
  }
  removeAttribute(name) { delete this.attributes[name]; if (name === "disabled") this.disabled = false; }
  addEventListener(name, callback) { this.listeners.set(name, [...(this.listeners.get(name) || []), callback]); }
  removeEventListener(name, callback) { this.listeners.set(name, (this.listeners.get(name) || []).filter(item => item !== callback)); }
  matches(selector) {
    if (selector === "*") return true;
    const attribute = selector.match(/\[([\w-]+)(?:=["']?([^\]"']+)["']?)?\]/);
    const base = selector.replace(/\[[^\]]+\]/g, "");
    const tag = base.match(/^[\w-]+/);
    const classes = [...base.matchAll(/\.([\w-]+)/g)].map(match => match[1]);
    if (tag && this.tagName !== tag[0].toUpperCase()) return false;
    if (classes.some(name => !this.classes.has(name))) return false;
    if (attribute && (this.getAttribute(attribute[1]) === null || attribute[2] !== undefined && this.getAttribute(attribute[1]) !== attribute[2])) return false;
    return !base.includes(":disabled") || this.disabled;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(",").map(value => value.trim());
    const result = [];
    const visit = element => {
      for (const child of element.children) {
        if (selectors.some(value => child.matches(value))) result.push(child);
        visit(child);
      }
    };
    visit(this);
    return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  find(selector) { return this.querySelector(selector); }
  findAll(selector) { return this.querySelectorAll(selector); }
  focus() {}
  async fire(type, details = {}) {
    if (this.disabled && type === "click") return;
    const event = { target: this, currentTarget: this, preventDefault() {}, stopPropagation() {}, ...details };
    if (this[`on${type}`]) await this[`on${type}`](event);
    for (const callback of this.listeners.get(type) || []) await callback(event);
  }
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function host({ execFile: injectedExecFile } = {}) {
  const calls = [], notices = [], commands = [], opened = [], cleanups = [], modals = [];
  const helperCalls = [];
  let helperResponder = injectedExecFile || ((_file, _args, _options, callback) => {
    callback(Object.assign(new Error("Synthetic missing helper"), { code: "ENOENT" }), "", "");
  });
  const files = new Map([
    ["Notes/Alpha.md", "# Alpha\n\nSynthetic α evidence.\n"],
    ["Notes/Beta.md", "# Beta\n\nSynthetic β evidence.\n"],
  ]);
  let activePath = "Notes/Alpha.md";
  let responder = async () => ({ status: 200, json: {} });
  const events = new Map();
  const app = {
    vault: {
      getAbstractFileByPath: selected => files.has(selected) ? { path: selected, extension: selected.split(".").at(-1), basename: path.basename(selected, ".md") } : null,
      read: async file => files.get(file.path),
      cachedRead: async file => files.get(file.path),
    },
    workspace: {
      getActiveFile: () => app.vault.getAbstractFileByPath(activePath),
      getActiveViewOfType: () => null,
      on: (name, callback) => { events.set(name, [...(events.get(name) || []), callback]); return { name, callback }; },
      offref: ref => events.set(ref.name, (events.get(ref.name) || []).filter(callback => callback !== ref.callback)),
      getLeaf: () => ({ openFile: async file => { opened.push(file.path); } }),
      getLeavesOfType: () => [],
      getRightLeaf: () => ({}),
      revealLeaf() {},
    },
    plugins: { enabledPlugins: new Set(), plugins: {} },
    commands: { commands: {}, executeCommandById: id => { commands.push(id); return true; } },
    metadataCache: { getFileCache: () => ({ frontmatter: {} }) },
  };
  class ItemView {
    constructor(leaf) {
      this.leaf = leaf;
      this.app = app;
      this.containerEl = new Element();
      this.containerEl._root = true;
      this.contentEl = this.containerEl.createDiv();
    }
    registerEvent(ref) { cleanups.push(() => app.workspace.offref(ref)); }
    register(callback) { cleanups.push(callback); }
    registerInterval(id) { id.unref?.(); cleanups.push(() => clearInterval(id)); }
    registerDomEvent(element, name, callback) { element.addEventListener(name, callback); }
  }
  class Plugin {
    constructor() { this.app = app; this.commands = []; this.settings = {}; }
    async loadData() { return {}; }
    async saveData() {}
    addCommand(command) { this.commands.push(command); }
    registerView() {}
    registerEvent(ref) { cleanups.push(() => app.workspace.offref(ref)); }
    register(callback) { cleanups.push(callback); }
    addSettingTab() {}
    addRibbonIcon() {}
    addStatusBarItem() { return new Element(); }
  }
  const field = new Proxy({}, { get: (_target, name) => name === "inputEl" ? new Element("input") : () => field });
  class Setting {
    constructor(container) { this.settingEl = container.createDiv(); }
    setName() { return this; }
    setDesc() { return this; }
    addText(callback) { callback(field); return this; }
    addToggle(callback) { callback(field); return this; }
    addButton(callback) { callback(field); return this; }
    addDropdown(callback) { callback(field); return this; }
  }
  const obsidian = {
    ItemView, Plugin, Setting,
    Modal: class {
      constructor() { this.contentEl = new Element(); }
      open() { modals.push(this); this.onOpen(); }
      close() { modals.splice(modals.indexOf(this), 1); this.onClose(); }
    },
    MarkdownView: class {},
    PluginSettingTab: class { constructor(_app, plugin) { this.app = app; this.plugin = plugin; this.containerEl = new Element(); } },
    Notice: class { constructor(message) { notices.push(String(message)); } },
    requestUrl: async options => {
      const call = { ...options, payload: JSON.parse(options.body), route: new URL(options.url).pathname.replace("/owner/", "") };
      calls.push(call);
      return responder(call);
    },
    setIcon() {}, normalizePath: value => value,
  };
  // Match Obsidian's external-only CommonJS evaluator, not Node's file loader.
  const packageRoot = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "knowledgeos-thin-client-")));
  const sourceRoot = path.resolve(__dirname, "../../clients/obsidian-thin-client");
  for (const filename of ["main.js", "manifest.json", "contract.json", "styles.css"]) {
    fs.copyFileSync(path.join(sourceRoot, filename), path.join(packageRoot, filename));
  }
  cleanups.push(() => {
    fs.rmSync(packageRoot, { recursive: true, force: true });
  });
  const clientPath = path.join(packageRoot, "main.js");
  const cleanup = () => {
    activeHosts.delete(cleanup);
    cleanups.splice(0).forEach(callback => callback());
  };
  activeHosts.add(cleanup);
  function load(filename) {
    if (filename !== clientPath) throw new Error("Refuse evaluation outside the selected plugin entrypoint");
    const externalRequire = request => {
      if (request === "obsidian") return obsidian;
      if (request === "node:child_process") return { execFile: (file, args, options, callback) => {
        helperCalls.push({ file, args: [...args], options: { ...options } });
        return helperResponder(file, args, options, callback);
      } };
      if (["node:os", "node:path"].includes(request)) return require(request);
      throw new Error("Native plugin evaluator cannot resolve module: " + request);
    };
    globalThis.crypto ??= webcrypto;
    const evaluated = { exports: {} };
    new Function("require", "module", "exports", fs.readFileSync(filename, "utf8"))(externalRequire, evaluated, evaluated.exports);
    return evaluated.exports;
  }
  return {
    app, calls, notices, commands, opened, files, Element, load, helperCalls,
    clientPath,
    respond: callback => { responder = callback; },
    exec: implementation => { helperResponder = implementation; },
    response: json => ({ status: 200, json }),
    active: selected => { activePath = selected; },
    confirm: async () => {
      const modal = modals.at(-1);
      if (!modal) throw new Error("Expected an explicit confirmation modal");
      const accept = modal.contentEl.querySelector("button.mod-warning");
      if (!accept) throw new Error("Confirmation modal lacks its explicit accept control");
      await accept.fire("click");
    },
    cancel: () => {
      const modal = modals.at(-1);
      if (!modal) throw new Error("Expected a confirmation modal to cancel");
      modal.close();
    },
    cleanup,
    emit: async (name, ...args) => { for (const callback of events.get(name) || []) await callback(...args); },
  };
}

module.exports = { host, deferred, Element, cleanupHosts };
