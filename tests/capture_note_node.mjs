import assert from "node:assert/strict";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const scriptPath = path.join(
  process.env.KNOWLEDGEOS_VAULT_SYSTEM ?? "",
  "Scripts",
  "QuickAdd",
  "CaptureNote.js",
);
const source = readFileSync(scriptPath, "utf8");
const module = { exports: {} };
const exports = module.exports;
const load = new Function("require", "module", "exports", source + "\n");
load(undefined, module, exports);
const captureNote = module.exports.default || module.exports;
assert.equal(typeof captureNote, "function");

class TFolder {
  constructor(folderPath) {
    this.path = folderPath;
  }
}

class TFile {
  constructor(filePath) {
    this.path = filePath;
  }
}

const fixedUuid = "11111111-1111-4111-8111-111111111111";
const originalCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto");
let nextUuid = fixedUuid;
Object.defineProperty(globalThis, "crypto", {
  configurable: true,
  value: { randomUUID: () => nextUuid },
});

function fullPath(root, vaultPath) {
  return path.join(root, ...vaultPath.split("/"));
}

function listFiles(root, current = "") {
  const directory = current ? fullPath(root, current) : root;
  const result = [];
  for (const name of readdirSync(directory)) {
    const relative = current ? `${current}/${name}` : name;
    const full = fullPath(root, relative);
    if (statSync(full).isDirectory()) result.push(...listFiles(root, relative));
    else result.push(relative);
  }
  return result.sort();
}

function makeAdapter(root, options = {}) {
  const events = [];
  const vault = {
    getAbstractFileByPath(vaultPath) {
      const full = fullPath(root, vaultPath);
      try {
        const entry = statSync(full);
        return entry.isDirectory() ? new TFolder(vaultPath) : new TFile(vaultPath);
      } catch {
        return null;
      }
    },
    async createFolder(vaultPath) {
      const full = fullPath(root, vaultPath);
      mkdirSync(full);
      return new TFolder(vaultPath);
    },
    async create(vaultPath, content) {
      if (options.failCreate) throw new Error("simulated create failure");
      const full = fullPath(root, vaultPath);
      writeFileSync(full, content, { flag: "wx" });
      events.push({ type: "create", path: vaultPath, content });
      return new TFile(vaultPath);
    },
  };
  const app = {
    vault,
    workspace: {
      getLeaf() {
        return {
          async openFile(file) {
            events.push({ type: "open", path: file.path });
            if (options.failOpen) throw new Error("simulated open failure");
          },
        };
      },
    },
  };
  class Notice {
    constructor(message) {
      events.push({ type: "notice", message });
    }
  }
  const obsidian = {
    TFolder,
    TFile,
    Notice,
  };
  return { app, obsidian, events };
}

function seed(root, vaultPath, content = "keep") {
  const full = fullPath(root, vaultPath);
  mkdirSync(path.dirname(full), { recursive: true });
  writeFileSync(full, content);
}

async function runCase(name, variables, options = {}) {
  const root = mkdtempSync(path.join(tmpdir(), `kos-capture-${name}-`));
  const { app, obsidian, events } = makeAdapter(root, options);
  if (options.seed) seed(root, options.seed.path, options.seed.content);
  if (options.seedFolder) seed(root, options.seedFolder.path, options.seedFolder.content);
  nextUuid = options.uuid ?? fixedUuid;
  let error = "";
  let result = "";
  try {
    result = await captureNote({
      app,
      obsidian,
      variables,
      abort(message) {
        const failure = new Error(message);
        failure.name = "AbortError";
        throw failure;
      },
    });
  } catch (caught) {
    error = String(caught?.message ?? caught);
  }
  const files = listFiles(root);
  const caseResult = {
    aborted: Boolean(error),
    error,
    result,
    events,
    files,
    target: `00_Inbox/Captures/${nextUuid}.md`,
  };
  if (files.includes(caseResult.target)) {
    caseResult.content = readFileSync(fullPath(root, caseResult.target), "utf8");
  }
  if (options.failCreate) {
    assert.equal(files.includes(caseResult.target), false, "failed create must not leave a file");
  }
  return caseResult;
}

const cases = {};

function notices(value) {
  return value.events
    .filter((event) => event.type === "notice")
    .map((event) => event.message);
}

function savedNotice(value) {
  return notices(value).includes("저장완료");
}

function opened(value) {
  return value.events.some((event) => event.type === "open");
}

cases.bodyOnly = await runCase("body-only", {
  body: "  leading\nemoji 😀 +&#% <% templater %> {{QuickAdd}}\n---\nkey: value\n  trailing  ",
  id: "source-provided-id",
  created: "1999-01-01T00:00:00Z",
});
assert.equal(cases.bodyOnly.aborted, false, cases.bodyOnly.error);
assert.equal(cases.bodyOnly.result, cases.bodyOnly.target);
assert.equal(savedNotice(cases.bodyOnly), true);
assert.equal(opened(cases.bodyOnly), true);
assert.match(cases.bodyOnly.content, /^---\nid: 11111111-1111-4111-8111-111111111111\n/);
assert.match(cases.bodyOnly.content, /\ncreated: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\n/);
assert.doesNotMatch(cases.bodyOnly.content, /source-provided-id|1999-01-01/);
assert.match(
  cases.bodyOnly.content,
  /## 원문\n  leading\nemoji 😀 \+&#% <% templater %> \{\{QuickAdd\}\}\n---\nkey: value\n  trailing  $/,
);
assert.doesNotMatch(cases.bodyOnly.content, /## 출처/);

cases.url = await runCase("url", {
  body: "memo",
  source_url: "https://example.com/a?b=1&c=%20",
});
assert.equal(cases.url.aborted, false, cases.url.error);
assert.match(cases.url.content, /## 원문\nmemo\n## 출처\nhttps:\/\/example\.com\/a\?b=1&c=%20\n$/);

cases.urlOnly = await runCase("url-only", {
  source_url: "https://example.com/only",
});
assert.equal(cases.urlOnly.aborted, false, cases.urlOnly.error);
assert.match(cases.urlOnly.content, /## 원문\n\n## 출처\nhttps:\/\/example\.com\/only\n$/);

cases.empty = await runCase("empty", {});
assert.equal(cases.empty.aborted, true);
assert.deepEqual(cases.empty.files, []);
assert.equal(savedNotice(cases.empty), false);

cases.invalidUrl = await runCase("invalid-url", {
  body: "memo",
  source_url: "javascript:alert(1)",
});
assert.equal(cases.invalidUrl.aborted, true);
assert.deepEqual(cases.invalidUrl.files, []);
assert.equal(savedNotice(cases.invalidUrl), false);

cases.failedCreate = await runCase("failed-create", { body: "memo" }, { failCreate: true });
assert.equal(cases.failedCreate.aborted, true);
assert.deepEqual(cases.failedCreate.files, []);
assert.equal(savedNotice(cases.failedCreate), false);

cases.openFailure = await runCase("open-failure", { body: "memo" }, { failOpen: true });
assert.equal(cases.openFailure.aborted, false, cases.openFailure.error);
assert.equal(cases.openFailure.result, cases.openFailure.target);
assert.equal(savedNotice(cases.openFailure), true);
assert.equal(
  notices(cases.openFailure).includes("노트는 저장했지만 열지 못했습니다"),
  true,
);
assert.equal(
  cases.openFailure.events.filter((event) => event.type === "create").length,
  1,
);
assert.equal(
  cases.openFailure.files.filter((file) => file.startsWith("00_Inbox/Captures/")).length,
  1,
);
assert.equal(cases.openFailure.content.includes("## 원문\nmemo"), true);

cases.folderCollision = await runCase(
  "folder-collision",
  { body: "memo" },
  { seedFolder: { path: "00_Inbox", content: "not a folder" } },
);
assert.equal(cases.folderCollision.aborted, true);
assert.equal(savedNotice(cases.folderCollision), false);
assert.doesNotMatch(cases.folderCollision.files.join("\n"), /^00_Inbox\/Captures\//m);

cases.uuidCollision = await runCase(
  "uuid-collision",
  { body: "memo" },
  { seed: { path: `00_Inbox/Captures/${fixedUuid}.md`, content: "keep" } },
);
assert.equal(cases.uuidCollision.aborted, true);
assert.equal(cases.uuidCollision.content, "keep");
assert.equal(savedNotice(cases.uuidCollision), false);

process.stdout.write(
  JSON.stringify({
    loader: typeof captureNote,
    cases: Object.fromEntries(
      Object.entries(cases).map(([name, value]) => [
        name,
        {
          aborted: value.aborted,
          error: value.error,
          result: value.result,
          saved: savedNotice(value),
          notices: notices(value),
          files: value.files,
          content: value.content ?? null,
        },
      ]),
    ),
    sample: {
      path: cases.bodyOnly.target,
      content: cases.bodyOnly.content,
    },
  }),
);

if (originalCrypto) Object.defineProperty(globalThis, "crypto", originalCrypto);
