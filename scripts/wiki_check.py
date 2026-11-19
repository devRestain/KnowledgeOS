#!/usr/bin/env python3
"""Check the generated KnowledgeOS wiki: freshness, spec and state coverage, links, no remote loads."""

from __future__ import annotations

import posixpath
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_wiki import CAPS, ROOT, WIKI, WIKI_REL, BuildError, build, generated_files, load_data, markdown_ids, nav_order  # noqa: E402

SCHEME = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)")
REMOTE_CSS = re.compile(r"url\(\s*['\"]?\s*(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)|@import", re.I)
REMOTE_JS = re.compile(r"\bfetch\s*\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon|\bimport\s*\(|importScripts", re.I)
CSS_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})[ \t\r\n\f]?|(\r\n|[\n\r\f])|(.))", re.S)


def css_loads_remote(text: str) -> bool:
    def decode(match: re.Match[str]) -> str:
        if match[1]:
            codepoint = int(match[1], 16)
            return chr(codepoint) if 0 < codepoint <= 0x10FFFF and not 0xD800 <= codepoint <= 0xDFFF else "\uFFFD"
        return "" if match[2] else match[3]

    text = CSS_ESCAPE.sub(decode, re.sub(r"/\*.*?\*/", "", text, flags=re.S))
    if REMOTE_CSS.search(text):
        return True
    for match in re.finditer(r"(?:-webkit-)?image-set\s*\(", text, re.I):
        depth, i = 1, match.end()
        while depth and i < len(text):
            char = text[i]
            if char in "\"'":
                end = text.find(char, i + 1)
                if end < 0:
                    break
                if SCHEME.match(text[i + 1:end].strip()):
                    return True
                i = end + 1
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            i += 1
    return False


class Parsed(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.duplicate_ids: set[str] = set()
        self.violations: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.loads: list[tuple[str, str]] = []
        self.styles: list[str] = []
        self.scripts: list[str] = []
        self.text: list[str] = []
        self._raw: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        if tag == "base":
            self.violations.append("<base> is not allowed")
        for name in values:
            if name.startswith("on"):
                self.violations.append(f"event-handler attribute {name} is not allowed")
        if "id" in values:
            if values["id"] in self.ids:
                self.duplicate_ids.add(values["id"])
            self.ids.add(values["id"])
        if tag == "a" and "href" in values:
            if re.sub(r"[\x00-\x20]", "", values["href"]).lower().startswith("javascript:"):
                self.violations.append("javascript: links are not allowed")
            self.links.append((tag, values["href"]))
        elif tag == "link" and "href" in values:
            self.loads.append((tag, values["href"]))
            self.links.append((tag, values["href"]))
        for candidate in values.get("srcset", "").split(","):
            parts = candidate.split()
            if parts:
                self.loads.append((f"{tag} srcset", parts[0]))
        for name in ("src", "xlink:href", "data", "poster", "action"):
            if name in values:
                self.loads.append((tag, values[name]))
                if name == "src":
                    self.links.append((tag, values[name]))
        if tag in {"image", "use", "feimage"} and "href" in values:
            self.loads.append((tag, values["href"]))
        if "style" in values:
            self.styles.append(values["style"])
        if tag in {"style", "script"}:
            self._raw = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == self._raw:
            self._raw = None

    def handle_data(self, data: str) -> None:
        if self._raw == "style":
            self.styles.append(data)
        elif self._raw == "script":
            self.scripts.append(data)
        else:
            self.text.append(data)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def main() -> int:
    failures: list[str] = []
    try:
        outputs = build()
        data = load_data()
    except BuildError as error:
        print(f"wiki-check failed: build error\n{error}", file=sys.stderr)
        return 1

    on_disk = generated_files()
    for rel in sorted(set(outputs) | set(on_disk)):
        path = WIKI / rel
        if rel not in outputs:
            failures.append(f"stale file not produced by the generator: {rel}")
        elif not path.is_file():
            failures.append(f"missing generated file (run make wiki): {rel}")
        elif path.read_bytes() != outputs[rel]:
            failures.append(f"generated file is out of date (run make wiki): {rel}")
    for rel in ("assets/wiki.css", "assets/wiki.js", "assets/search-index.js"):
        if rel not in outputs:
            failures.append(f"required asset missing: {rel}")

    parsed: dict[str, Parsed] = {}
    for slug in nav_order():
        path = WIKI / f"{slug}.html"
        if not path.is_file():
            failures.append(f"required page missing: {slug}.html")
            continue
        parser = Parsed()
        parser.feed(path.read_text(encoding="utf-8"))
        parsed[f"{slug}.html"] = parser
    texts = {name: normalize(" ".join(p.text)) for name, p in parsed.items()}
    all_ids = set().union(*(p.ids for p in parsed.values())) if parsed else set()
    all_text = " ".join(texts.values())

    for cap in CAPS:
        page = f"{cap.lower()}.html"
        if page not in texts or cap not in texts[page]:
            failures.append(f"capability {cap} has no page mentioning it")
    for cap, rows in data.acceptance.items():
        for acc, _ in rows:
            if acc not in all_ids:
                failures.append(f"acceptance {acc} ({cap}) has no anchor in the wiki")
    for record in data.records:
        record_id = record.fields.get("id")
        if record.name in {"goal", "accept", "blocker", "decision", "evidence"}:
            if record_id not in all_ids:
                failures.append(f"PROJECT_STATE {record.name} {record_id} is missing from the wiki")
        elif record.name == "handoff":
            if normalize(record.fields.get("action", "")) not in all_text:
                failures.append(f"PROJECT_STATE handoff on line {record.line} is missing from the wiki")

    links = 0
    for name, parser in parsed.items():
        failures.extend(f"{name}: {violation}" for violation in parser.violations)
        failures.extend(f"{name}: duplicate ID {anchor}" for anchor in sorted(parser.duplicate_ids))
        for tag, url in parser.loads:
            if SCHEME.match(url.strip()):
                failures.append(f"{name}: <{tag}> loads remote resource {url}")
        for style in parser.styles:
            if css_loads_remote(style):
                failures.append(f"{name}: inline style loads a remote resource")
        for script in parser.scripts:
            if script.strip():
                failures.append(f"{name}: inline script is not allowed")
        for tag, url in parser.links:
            if tag == "a" and SCHEME.match(url.strip()):
                continue
            links += 1
            path, _, anchor = url.partition("#")
            path, anchor = unquote(path), unquote(anchor)
            if not path:
                target = name
            else:
                resolved = posixpath.normpath(posixpath.join(WIKI_REL, path))
                if resolved.startswith("../") or not (ROOT / resolved).exists():
                    failures.append(f"{name}: link {url} does not resolve")
                    continue
                target = posixpath.relpath(resolved, WIKI_REL)
            if anchor and target.endswith(".html") and posixpath.dirname(target) == "":
                if target not in parsed or anchor not in parsed[target].ids:
                    failures.append(f"{name}: anchor {url} does not resolve")
            elif anchor and target.endswith(".md"):
                if anchor not in markdown_ids((ROOT / WIKI_REL / target).read_text(encoding="utf-8")):
                    failures.append(f"{name}: anchor {url} does not resolve")

    for rel, content in outputs.items():
        text = content.decode("utf-8", errors="replace")
        if rel.endswith(".css") and css_loads_remote(text):
            failures.append(f"{rel}: CSS loads a remote resource (url() with scheme or @import)")
        if rel.endswith(".js") and rel != "assets/search-index.js" and REMOTE_JS.search(text):
            failures.append(f"{rel}: script performs network requests")

    if failures:
        print("wiki-check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    acc_count = sum(len(rows) for rows in data.acceptance.values())
    print(
        f"wiki-check passed: {len(parsed)} pages, {len(CAPS)} capabilities, {acc_count} acceptance IDs, "
        f"{len(data.records)} state records, {links} internal links, no remote loads"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
