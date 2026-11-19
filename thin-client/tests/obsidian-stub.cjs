"use strict";
// Minimal stand-in for the `obsidian` module: only the surface the Thin Client uses.
// UI calls are recorded in `ui` so tests can drive modals and read notices.

const ui = { notices: [], modals: [], menus: [], intervals: [], opened: [], transport: null };

class El {
  constructor(tag = "div", text = "") {
    this.tag = tag;
    this.text = text;
    this.children = [];
    this.settings = [];
    this.attributes = {};
    this.listeners = {};
  }
  empty() { this.children = []; this.settings = []; this.text = ""; }
  setText(text) { this.text = String(text); }
  createEl(tag, options = {}) {
    const el = new El(tag, options.text || "");
    for (const [name, value] of Object.entries(options.attr || {})) el.setAttribute(name, value);
    this.children.push(el);
    return el;
  }
  createDiv(options = {}) { return this.createEl("div", options); }
  createSpan(options = {}) { return this.createEl("span", options); }
  addClass() {}
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  async click() { if (!this.disabled) return this.listeners.click?.({ preventDefault() {} }); }
  async submit() { return this.listeners.submit?.({ preventDefault() {} }); }
  async type(value) { this.value = value; return this.listeners.input?.(); }
  focus() { this.focused = true; }
  get allText() { return [this.text, ...this.children.map((child) => child.allText)].join("\n"); }
}

class Notice {
  constructor(message, timeout) {
    this.message = String(message);
    this.timeout = timeout;
    ui.notices.push(this);
  }
  hide() {}
}

class Modal {
  constructor(app) {
    this.app = app;
    this.titleEl = new El();
    this.contentEl = new El();
    this.isOpen = false;
  }
  open() {
    this.isOpen = true;
    ui.modals.push(this);
    if (this.onOpen) this.onOpen();
  }
  close() {
    if (!this.isOpen) return;
    this.isOpen = false;
    if (this.onClose) this.onClose();
  }
}

class SuggestModal extends Modal {
  setPlaceholder(text) { this.placeholder = text; }
}

class ItemView {
  constructor(leaf) {
    this.leaf = leaf;
    this.app = leaf.app;
    this.contentEl = new El();
  }
}

class Menu {
  constructor() { this.items = []; }
  addItem(fill) {
    const item = {
      setTitle(title) { this.title = title; return this; },
      setIcon() { return this; },
      setDisabled(disabled) { this.disabled = disabled; return this; },
      onClick(handler) { this.handler = handler; return this; },
      async click() { if (!this.disabled) await this.handler(); },
    };
    fill(item);
    this.items.push(item);
    return this;
  }
  addSeparator() { return this; }
  showAtPosition() { ui.menus.push(this); }
}

class TextComponent {
  constructor() { this.value = ""; }
  setPlaceholder() { return this; }
  setValue(value) { this.value = value; return this; }
  getValue() { return this.value; }
  onChange(callback) { this.changeCallback = callback; return this; }
  async type(value) {
    this.value = value;
    if (this.changeCallback) await this.changeCallback(value);
  }
}

class ButtonComponent {
  setButtonText(text) { this.text = text; return this; }
  setCta() { return this; }
  setWarning() { return this; }
  setIcon() { return this; }
  onClick(callback) { this.clickCallback = callback; return this; }
  async click() { if (this.clickCallback) await this.clickCallback(); }
}

class Setting {
  constructor(containerEl) {
    this.texts = [];
    this.buttons = [];
    containerEl.settings.push(this);
  }
  setName(name) { this.name = name; return this; }
  setDesc() { return this; }
  setHeading() { return this; }
  addText(callback) { const component = new TextComponent(); this.texts.push(component); callback(component); return this; }
  addTextArea(callback) { return this.addText(callback); }
  addButton(callback) { const component = new ButtonComponent(); this.buttons.push(component); callback(component); return this; }
}

class Plugin {
  constructor(app, manifest) {
    this.app = app;
    this.manifest = manifest;
    this.commands = [];
    this.settingTabs = [];
    this.saveCalls = [];
  }
  addCommand(command) { this.commands.push(command); return command; }
  registerView(type, factory) { this.app.workspace.viewFactories.set(type, factory); }
  addRibbonIcon(icon, title, callback) { this.ribbon = { icon, title, callback }; }
  registerEvent() {}
  registerInterval(id) { return id; }
  register() {}
  addStatusBarItem() { this.statusBarItem = new El(); return this.statusBarItem; }
  addSettingTab(tab) { this.settingTabs.push(tab); }
  async loadData() { return this.app.storedData === undefined ? null : structuredClone(this.app.storedData); }
  async saveData(data) { this.saveCalls.push(structuredClone(data)); }
}

class PluginSettingTab {
  constructor(app, plugin) {
    this.app = app;
    this.plugin = plugin;
    this.containerEl = new El();
  }
}

class TAbstractFile {
  constructor(path) {
    this.path = path;
    this.name = path.split("/").pop();
  }
}

class TFile extends TAbstractFile {
  constructor(path, content) {
    super(path);
    const dot = this.name.lastIndexOf(".");
    this.extension = dot >= 0 ? this.name.slice(dot + 1) : "";
    this.basename = dot >= 0 ? this.name.slice(0, dot) : this.name;
    this.content = content;
  }
}

class TFolder extends TAbstractFile {
  constructor(path) {
    super(path);
    this.children = [];
  }
}

// Mirrors Obsidian's requestUrl({url, method, contentType, body, throw}) over the test's fake fetch.
async function requestUrl(options) {
  const headers = options.contentType ? { "Content-Type": options.contentType } : {};
  const response = await ui.transport(options.url, { method: options.method || "GET", headers, body: options.body });
  return { status: response.status, text: JSON.stringify(response.body) };
}

module.exports = {
  ui, El, ItemView, Menu, Notice, Modal, SuggestModal, Setting, Plugin, PluginSettingTab,
  setIcon() {},
  setTooltip(el, text) { el.setAttribute("aria-label", text); el.setAttribute("title", text); },
  TAbstractFile, TFile, TFolder, requestUrl,
};
