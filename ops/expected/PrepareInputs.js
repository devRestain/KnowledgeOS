/*
 * Bounded QuickAdd helpers for the five KnowledgeOS primary choices.
 *
 * This file is intentionally limited to two local effects:
 *   - prepare QuickAdd variables used by the canonical templates; and
 *   - create the two empty project companion directories.
 *
 * It does not call a provider, use the network, execute shell/system
 * commands, modify an existing note, or delete anything.
 *
 * Configure one QuickAdd Macro choice named KOS_HELPERS with this file as its
 * UserScript command. QuickAdd 2.9.3 addresses exported members with a double
 * colon, not JavaScript dot notation. Use these exact tokens:
 *   {{MACRO:KOS_HELPERS::prepareInputs}}
 *   {{MACRO:KOS_HELPERS::ensureProjectDirectories}}
 */

const TITLE_MAX_LENGTH = 120;

function normalizeTitle(value) {
  return String(value ?? "")
    .normalize("NFC")
    .replace(/[\\/:*?"<>|\u0000-\u001f\u007f]/g, "-")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, TITLE_MAX_LENGTH);
}

function appendAlias(buffer, aliases) {
  const value = buffer.trim();
  if (value) aliases.push(value);
}

function splitCommaSeparatedAliases(value) {
  const text = String(value ?? "").trim();
  if (!text || text === "[]") return [];

  // Accept an already-entered JSON flow array as a convenience, but do not
  // require it from the user.
  if (text.startsWith("[") && text.endsWith("]")) {
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed) && parsed.every((item) => typeof item === "string")) {
        return parsed.map((item) => item.trim()).filter(Boolean);
      }
    } catch {
      // Fall through to the comma parser for a YAML-like or plain-text input.
    }
  }

  const source = text.startsWith("[") && text.endsWith("]") ? text.slice(1, -1) : text;
  const aliases = [];
  let current = "";
  let quote = null;

  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];

    if (quote) {
      if (character === "\\" && index + 1 < source.length) {
        current += source[index + 1];
        index += 1;
      } else if (character === quote) {
        quote = null;
      } else {
        current += character;
      }
      continue;
    }

    if ((character === '"' || character === "'") && current.trim() === "") {
      quote = character;
    } else if (character === ",") {
      appendAlias(current, aliases);
      current = "";
    } else {
      current += character;
    }
  }

  if (quote) throw new Error("collision aliases contain an unmatched quote");
  appendAlias(current, aliases);

  return aliases;
}

function normalizeAliases(value) {
  const uniqueAliases = [];
  const seen = new Set();
  for (const alias of splitCommaSeparatedAliases(value)) {
    if (!seen.has(alias)) {
      seen.add(alias);
      uniqueAliases.push(alias);
    }
  }
  // JSON flow arrays are valid YAML and quote/escape every text item safely.
  return JSON.stringify(uniqueAliases);
}

function assertSafeProjectTitle(title, abort) {
  const normalized = normalizeTitle(title);
  if (!normalized || normalized !== title || normalized === "." || normalized === "..") {
    abort("Project title contains an unsupported path character or is empty.");
  }
  return normalized;
}

async function prepareInputs({ quickAddApi, variables, abort }) {
  let title = variables.title;
  if (title == null || String(title).trim() === "") {
    title = await quickAddApi.inputPrompt("Title", "");
    if (title === undefined) abort("QuickAdd input cancelled.");
    title = normalizeTitle(title);
  } else {
    title = normalizeTitle(title);
  }
  if (!title) abort("Title is required.");
  variables.title = title;

  if (variables.collision_aliases_yaml == null) {
    const rawAliases = await quickAddApi.inputPrompt(
      "Collision aliases (optional; separate multiple aliases with commas)",
      "",
    );
    if (rawAliases === undefined) abort("QuickAdd input cancelled.");
    variables.collision_aliases_yaml = normalizeAliases(rawAliases);
  }

  return title;
}

function getFolder(app, obsidian, path) {
  const entry = app.vault.getAbstractFileByPath(path);
  if (entry && !(entry instanceof obsidian.TFolder)) {
    throw new Error(`Cannot create project directory because a file exists at '${path}'.`);
  }
  return entry;
}

async function ensureFolder(app, obsidian, path, abort) {
  if (getFolder(app, obsidian, path)) return;
  try {
    await app.vault.createFolder(path);
  } catch (error) {
    // Treat a concurrent/idempotent create as success only when the resulting
    // entry is actually a folder. Never overwrite a file at the target path.
    if (getFolder(app, obsidian, path)) return;
    abort(`Could not create project directory '${path}': ${error.message}`);
  }
}

async function ensureProjectDirectories({ app, obsidian, variables, abort }) {
  const title = assertSafeProjectTitle(String(variables.title ?? "").trim(), abort);
  const projectPath = `20_Projects/${title}`;

  await ensureFolder(app, obsidian, "20_Projects", abort);
  await ensureFolder(app, obsidian, projectPath, abort);
  await ensureFolder(app, obsidian, `${projectPath}/Working`, abort);
  await ensureFolder(app, obsidian, `${projectPath}/Artifacts`, abort);
  return "";
}

module.exports = {
  prepareInputs,
  ensureProjectDirectories,
};
