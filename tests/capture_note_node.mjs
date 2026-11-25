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
  constructor(folderPath, children = []) {
    this.path = folderPath;
    this.name = folderPath.split("/").pop() ?? "";
    this.children = children;
  }
}

class TFile {
  constructor(filePath) {
    this.path = filePath;
    this.name = filePath.split("/").pop() ?? "";
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
        if (!entry.isDirectory()) return new TFile(vaultPath);
        const children = readdirSync(full).map((name) => {
          const childPath = vaultPath ? `${vaultPath}/${name}` : name;
          const childFull = fullPath(root, childPath);
          return statSync(childFull).isDirectory()
            ? new TFolder(childPath)
            : new TFile(childPath);
        });
        return new TFolder(vaultPath, children);
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
  if (options.seeds) {
    for (const item of options.seeds) seed(root, item.path, item.content);
  }
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
  const fileContents = Object.fromEntries(
    files.map((file) => [file, readFileSync(fullPath(root, file), "utf8")]),
  );
  const target = result || options.expectedTarget || "";
  const caseResult = {
    aborted: Boolean(error),
    error,
    result,
    events,
    files,
    fileContents,
    target,
  };
  if (target && files.includes(target)) {
    caseResult.content = fileContents[target];
  }
  if (options.failCreate) {
    assert.deepEqual(files, [], "failed create must not leave a file");
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
assert.equal(cases.bodyOnly.result, "00_Inbox/Captures/leading.md");
assert.equal(cases.bodyOnly.target, "00_Inbox/Captures/leading.md");
assert.equal(savedNotice(cases.bodyOnly), true);
assert.equal(opened(cases.bodyOnly), true);
assert.match(cases.bodyOnly.content, /^---\nid: 11111111-1111-4111-8111-111111111111\n/);
assert.match(cases.bodyOnly.content, /\ncreated: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\n/);
assert.doesNotMatch(cases.bodyOnly.content, /\ntitle:/);
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
assert.equal(cases.url.result, "00_Inbox/Captures/memo.md");
assert.match(cases.url.content, /\ncreated: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\n/);
assert.match(cases.url.content, /## 원문\nmemo\n## 출처\nhttps:\/\/example\.com\/a\?b=1&c=%20\n$/);

cases.urlOnly = await runCase("url-only", {
  source_url: "https://example.com/only",
});
assert.equal(cases.urlOnly.aborted, false, cases.urlOnly.error);
assert.equal(cases.urlOnly.result, "00_Inbox/Captures/example.com.md");
assert.match(cases.urlOnly.content, /\ncreated: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\n/);
assert.match(cases.urlOnly.content, /## 원문\n\n## 출처\nhttps:\/\/example\.com\/only\n$/);

cases.whitespaceBodyUrl = await runCase("whitespace-body-url", {
  body: "   \n\t  ",
  source_url: "https://docs.example.com/guide",
});
assert.equal(cases.whitespaceBodyUrl.aborted, false, cases.whitespaceBodyUrl.error);
assert.equal(cases.whitespaceBodyUrl.result, "00_Inbox/Captures/docs.example.com.md");
assert.match(
  cases.whitespaceBodyUrl.content,
  /\ncreated: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\n/,
);

cases.hostileLine = await runCase("hostile-line", {
  body: "../a:b*?|#[x]\nsecond line",
});
assert.equal(cases.hostileLine.aborted, false, cases.hostileLine.error);
assert.equal(cases.hostileLine.result, "00_Inbox/Captures/a b x.md");
assert.deepEqual(cases.hostileLine.files, ["00_Inbox/Captures/a b x.md"]);
assert.match(cases.hostileLine.content, /\ncreated: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\n/);
assert.match(cases.hostileLine.content, /## 원문\n\.\.\/a:b\*\?\|#\[x\]\nsecond line$/);

const korean40 = "한글제목".repeat(10);
cases.koreanCap = await runCase("korean-cap", {
  body: "한글제목".repeat(12),
});
assert.equal(cases.koreanCap.aborted, false, cases.koreanCap.error);
assert.equal(cases.koreanCap.result, `00_Inbox/Captures/${korean40}.md`);
assert.equal(Array.from(korean40).length, 40);
assert.match(cases.koreanCap.content, /\ncreated: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\n/);

const surrogate40 = `${"가".repeat(39)}😀`;
cases.surrogateCap = await runCase("surrogate-cap", {
  body: `${surrogate40}나`,
});
assert.equal(cases.surrogateCap.aborted, false, cases.surrogateCap.error);
assert.equal(cases.surrogateCap.result, `00_Inbox/Captures/${surrogate40}.md`);
assert.equal(Array.from(surrogate40).length, 40);

cases.fallbackName = await runCase("fallback-name", {
  body: "...:::***",
});
assert.equal(cases.fallbackName.aborted, false, cases.fallbackName.error);
assert.equal(cases.fallbackName.result, "00_Inbox/Captures/캡처.md");
assert.match(cases.fallbackName.content, /\ncreated: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\n/);

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
assert.equal(cases.openFailure.result, "00_Inbox/Captures/memo.md");
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

cases.nameCollision = await runCase(
  "name-collision",
  { body: "memo" },
  { seed: { path: "00_Inbox/Captures/Memo.md", content: "keep" } },
);
assert.equal(cases.nameCollision.aborted, false, cases.nameCollision.error);
assert.equal(cases.nameCollision.result, "00_Inbox/Captures/memo 2.md");
assert.equal(savedNotice(cases.nameCollision), true);
assert.equal(cases.nameCollision.fileContents["00_Inbox/Captures/Memo.md"], "keep");
assert.match(cases.nameCollision.content, /\ncreated: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\n/);
assert.equal(cases.nameCollision.content.includes("## 원문\nmemo"), true);

const exhaustedSeeds = Array.from({ length: 99 }, (_, index) => ({
  path: index === 0 ? "00_Inbox/Captures/memo.md" : `00_Inbox/Captures/memo ${index + 1}.md`,
  content: "keep",
}));
cases.collisionExhausted = await runCase(
  "collision-exhausted",
  { body: "memo" },
  { seeds: exhaustedSeeds },
);
assert.equal(cases.collisionExhausted.aborted, true);
assert.equal(savedNotice(cases.collisionExhausted), false);
assert.equal(cases.collisionExhausted.files.length, 99);
assert.equal(cases.collisionExhausted.fileContents["00_Inbox/Captures/memo.md"], "keep");
assert.equal(cases.collisionExhausted.fileContents["00_Inbox/Captures/memo 99.md"], "keep");

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
