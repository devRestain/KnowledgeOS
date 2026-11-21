#!/usr/bin/env python3
"""Generate the KnowledgeOS static wiki in docs/wiki/ from docs/wiki/_source and the spec documents.

Stdlib only and deterministic: the same inputs always produce the same bytes.
"""

from __future__ import annotations

import hashlib
import html
import json
import posixpath
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from validate_state import Record, StateError, parse_line  # noqa: E402
import wiki_svg  # noqa: E402

ROOT = SCRIPTS.parent
WIKI_REL = "docs/wiki"
WIKI = ROOT / WIKI_REL
SOURCE = WIKI / "_source"

DOCS = {
    "CAPABILITIES": "docs/CAPABILITIES.md",
    "TRACE": "docs/CAPABILITY_TRACE.md",
    "STATE": "PROJECT_STATE.md",
    "USER": "USER_FEATURE_SPEC.md",
    "BLUEPRINT": "OBSIDIAN_VAULT_BLUEPRINT.md",
    "README": "README.md",
}
DOC_TITLES = {
    "CAPABILITIES": "기능 명세",
    "TRACE": "인수 예시 추적표",
    "STATE": "프로젝트 상태",
    "USER": "사용자 기능 명세",
    "BLUEPRINT": "Blueprint",
    "README": "README",
}
CAPS = [f"C{n}" for n in range(1, 13)]
GROUPS = [
    ("시작하기", ["index", "philosophy", "usage"]),
    ("구조", ["architecture", "glossary"]),
    ("기능", [cap.lower() for cap in CAPS]),
    ("현황", ["status", "roadmap"]),
]
NAV_LABELS = {
    "index": "개요",
    "philosophy": "운용 원칙",
    "usage": "사용 가이드",
    "architecture": "구조",
    "glossary": "용어",
    "status": "현재 상태",
    "roadmap": "로드맵",
}
STATE_KINDS = {
    "goals": "goal",
    "accepts": "accept",
    "blockers": "blocker",
    "decisions": "decision",
    "evidence": "evidence",
    "handoff": "handoff",
}
CALLOUT_TITLES = {"note": "참고", "tip": "도움말", "warning": "주의", "status": "상태"}
ACC_RE = re.compile(r"^- \*\*([A-Z]+-\d+)\*\* (.*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
LIST_RE = re.compile(r"^( *)([-*+]|\d+[.)])\s+(.*)$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,})\s*([\w-]*)\s*$")
DIRECTIVE_RE = re.compile(r"^\{\{(include|acceptance|state|svg|sources)(?::(.*))?\}\}$")
TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
CHROME_IDS = {"content", "sidebar", "wiki-search", "search-results", "backlinks"}


class BuildError(Exception):
    pass


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def slugify(text: str) -> str:
    plain = html.unescape(re.sub(r"<[^>]+>", "", text)).strip().lower()
    plain = re.sub(r"[^\w\s-]", "", plain)
    return re.sub(r"\s+", "-", plain).strip("-") or "section"


def indentation(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


# ---------------------------------------------------------------- source data


@dataclass
class Data:
    texts: dict[str, str]
    digests: dict[str, str]
    acceptance: dict[str, list[tuple[str, str]]]
    acc_cap: dict[str, str]
    trace: dict[str, tuple[list[str], list[str]]]
    records: list[Record]
    summary: list[dict[str, str]]


def split_cells(row: str) -> list[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    cells, current, in_code, depth, i = [], [], False, 0, 0
    while i < len(row):
        char = row[i]
        if char == "\\" and i + 1 < len(row) and row[i + 1] == "|":
            current.append("|")
            i += 2
            continue
        if char == "`":
            in_code = not in_code
        elif not in_code and row.startswith("[[", i):
            depth += 1
            current.append("[[")
            i += 2
            continue
        elif not in_code and depth and row.startswith("]]", i):
            depth -= 1
            current.append("]]")
            i += 2
            continue
        elif char == "|" and not in_code and not depth:
            cells.append("".join(current).strip())
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    cells.append("".join(current).strip())
    return cells


def heading_lines(lines: list[str]) -> list[tuple[int, int, str]]:
    found, fence = [], None
    for index, line in enumerate(lines):
        match = FENCE_RE.match(line)
        if match and (fence is None or match.group(1).startswith(fence)):
            fence = None if fence else match.group(1)
            continue
        if fence:
            continue
        heading = HEADING_RE.match(line)
        if heading:
            found.append((index, len(heading.group(1)), heading.group(2)))
    return found


def markdown_ids(text: str) -> set[str]:
    """Source-document heading fragments, with the same slugs and suffixes as the wiki."""
    ids: set[str] = set()
    for _, _, raw in heading_lines(text.splitlines()):
        raw = re.sub(r"\[\[([^]|]+)(?:\|([^]]+))?\]\]", lambda m: m[2] or m[1], raw)
        raw = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", raw)
        wanted = slugify(esc(raw))
        anchor, number = wanted, 2
        while anchor in ids:
            anchor = f"{wanted}-{number}"
            number += 1
        ids.add(anchor)
    return ids


def section(text: str, key: str, doc: str) -> tuple[str, int, list[str]]:
    lines = text.split("\n")
    heads = heading_lines(lines)
    matches = [h for h in heads if h[2] == key or h[2].startswith(key + " ")]
    if not matches:
        matches = [h for h in heads if h[2].startswith(key)]
    if len(matches) != 1:
        raise BuildError(f"include {doc}#{key}: {len(matches)} matching headings")
    start, level, title = matches[0]
    end = len(lines)
    for index, other_level, _ in heads:
        if index > start and other_level <= level:
            end = index
            break
    return title, level, lines[start:end]


def load_data() -> Data:
    texts, digests = {}, {}
    for key, rel in DOCS.items():
        raw = (ROOT / rel).read_bytes()
        texts[key] = raw.decode("utf-8")
        digests[key] = hashlib.sha256(raw).hexdigest()

    acceptance: dict[str, list[tuple[str, str]]] = {}
    acc_cap: dict[str, str] = {}
    current = None
    for line in texts["CAPABILITIES"].split("\n"):
        heading = re.match(r"^## (C\d+) ", line)
        if heading:
            current = heading.group(1)
            acceptance[current] = []
            continue
        if line.startswith("## "):
            current = None
        match = ACC_RE.match(line)
        if match:
            if current is None:
                raise BuildError(f"acceptance ID {match.group(1)} outside a capability section")
            acceptance[current].append((match.group(1), match.group(2)))
            acc_cap[match.group(1)] = current
    if sorted(acceptance, key=lambda c: int(c[1:])) != CAPS:
        raise BuildError(f"CAPABILITIES sections {sorted(acceptance)} are not C1..C12")

    trace: dict[str, tuple[list[str], list[str]]] = {}
    for line in texts["TRACE"].split("\n"):
        if not line.startswith("| ") or line.startswith("| ID ") or TABLE_SEP_RE.match(line):
            continue
        cells = split_cells(line)
        if len(cells) == 3:
            trace[cells[0]] = (re.findall(r"`([^`]+)`", cells[1]), re.findall(r"`([^`]+)`", cells[2]))

    records = []
    for number, line in enumerate(texts["STATE"].split("\n"), 1):
        if line.startswith("/"):
            try:
                records.append(parse_line(line, number))
            except StateError as error:
                raise BuildError(f"PROJECT_STATE.md: {error}") from error

    summary = []
    _, _, lines = section(texts["CAPABILITIES"], "Capability summary", "CAPABILITIES")
    for line in lines:
        if line.startswith("| C"):
            cells = split_cells(line)
            summary.append(dict(zip(["id", "name", "target", "roles", "writes"], cells)))
    return Data(texts, digests, acceptance, acc_cap, trace, records, summary)


def svg_data(data: Data) -> dict:
    records = []
    for record in data.records:
        if record.name in STATE_KINDS.values():
            fields = record.fields
            records.append({
                "kind": record.name,
                "id": fields.get("id"),
                "state": fields.get("state"),
                "result": fields.get("result"),
                "class": fields.get("class"),
                "phase": fields.get("phase"),
            })
    return {
        "caps": data.summary,
        "acceptance": {cap: [acc for acc, _ in rows] for cap, rows in data.acceptance.items()},
        "records": records,
    }


# ---------------------------------------------------------------- pages


@dataclass
class Page:
    slug: str
    text: str
    title: str
    label: str
    group: str
    state_kinds: set[str] = field(default_factory=set)
    acc_caps: set[str] = field(default_factory=set)
    body: str = ""
    ids: set[str] = field(default_factory=lambda: set(CHROME_IDS))
    headings: list[tuple[str, str]] = field(default_factory=list)
    links_to: set[str] = field(default_factory=set)
    refs: list[tuple[str, str, str]] = field(default_factory=list)


class Site:
    def __init__(self, data: Data, pages: dict[str, Page]):
        self.data = data
        self.pages = pages
        self.svg_data = svg_data(data)
        self.state_page: dict[str, str] = {}
        self.acc_page: dict[str, str] = {}
        for slug in nav_order():
            page = pages[slug]
            for kind in sorted(page.state_kinds):
                self.state_page.setdefault(kind, slug)
            for cap in sorted(page.acc_caps):
                for acc, _ in data.acceptance[cap]:
                    self.acc_page.setdefault(acc, slug)
        self.record_page: dict[str, str] = {}
        for record in data.records:
            record_id = record.fields.get("id")
            kind = next((k for k, v in STATE_KINDS.items() if v == record.name), None)
            if record_id and kind in self.state_page:
                self.record_page.setdefault(record_id, self.state_page[kind])


def nav_order() -> list[str]:
    return [slug for _, slugs in GROUPS for slug in slugs]


class Renderer:
    def __init__(self, site: Site, page: Page):
        self.site = site
        self.page = page
        self.heading_level = 1
        self.tokens: list[str] = []

    # ------------------------------------------------------------ ids and links

    def new_id(self, wanted: str) -> str:
        candidate, number = wanted, 2
        while candidate in self.page.ids:
            candidate = f"{wanted}-{number}"
            number += 1
        self.page.ids.add(candidate)
        return candidate

    def page_href(self, slug: str, anchor: str = "") -> str:
        self.page.refs.append((slug, anchor, f"{slug}#{anchor}"))
        if slug != self.page.slug:
            self.page.links_to.add(slug)
            base = f"{slug}.html"
        else:
            base = ""
        return f"{base}#{anchor}" if anchor else (base or "#")

    def wikilink(self, inner: str) -> str:
        target, _, label = inner.partition("|")
        target, _, anchor = target.strip().partition("#")
        label = label.strip()
        pages = self.site.pages
        if target.lower() in pages and (target.islower() or re.fullmatch(r"C\d+", target)):
            slug = target.lower()
            default = slug.upper() if re.fullmatch(r"c\d+", slug) else pages[slug].label
        elif target in self.site.data.acc_cap:
            slug = self.site.acc_page.get(target, self.site.data.acc_cap[target].lower())
            anchor, default = anchor or target, target
        elif target in self.site.record_page:
            slug = self.site.record_page[target]
            anchor, default = anchor or target, target
        else:
            raise BuildError(f"{self.page.slug}: unknown wiki link target [[{inner}]]")
        href = self.page_href(slug, anchor)
        return f'<a class="wikilink" href="{esc(href)}">{self.inline(label) if label else esc(default)}</a>'

    def link(self, label: str, url: str, base: str) -> str:
        label_html = self.inline(label, links=False)
        if SCHEME_RE.match(url):
            return f'<a class="external" href="{esc(url)}" rel="noopener noreferrer">{label_html}</a>'
        if url.startswith("#"):
            self.page.refs.append((self.page.slug, unquote(url[1:]), url))
            return f'<a href="{esc(url)}">{label_html}</a>'
        path, _, anchor = url.partition("#")
        resolved = posixpath.normpath(posixpath.join(base, path))
        if resolved.startswith("../") or resolved == "..":
            return f'<span class="offsite" title="{esc(resolved)}">{label_html}</span>'
        if posixpath.dirname(resolved) == WIKI_REL and resolved.endswith(".html"):
            slug = posixpath.basename(resolved)[:-5]
            if slug not in self.site.pages:
                raise BuildError(f"{self.page.slug}: link to unknown wiki page {url}")
            return f'<a href="{esc(self.page_href(slug, anchor))}">{label_html}</a>'
        if not (ROOT / resolved).exists():
            raise BuildError(f"{self.page.slug}: link target {url} ({resolved}) does not exist")
        if anchor and resolved.endswith(".md"):
            source_ids = markdown_ids((ROOT / resolved).read_text(encoding="utf-8"))
            if unquote(anchor) not in source_ids:
                raise BuildError(f"{self.page.slug}: link {url} has no anchor")
        href = posixpath.relpath(resolved, WIKI_REL) + (f"#{anchor}" if anchor else "")
        return f'<a class="repo" href="{esc(href)}">{label_html}</a>'

    # ------------------------------------------------------------ inline

    def hold(self, html_text: str) -> str:
        self.tokens.append(html_text)
        return f"\x00{len(self.tokens) - 1}\x00"

    def inline(self, text: str, links: bool = True, base: str = WIKI_REL) -> str:
        text = re.sub(r"`([^`]+)`", lambda m: self.hold(f"<code>{esc(m.group(1))}</code>"), text)
        if links:
            text = re.sub(r"\[\[([^\]]+)\]\]", lambda m: self.hold(self.wikilink(m.group(1))), text)
            text = re.sub(
                r"\[([^\]]+)\]\(([^)\s]+)\)",
                lambda m: self.hold(self.link(m.group(1), m.group(2), base)),
                text,
            )
        text = esc(text)
        text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"(?<!\*)\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?!\*)", r"<em>\1</em>", text)
        while "\x00" in text:
            text = re.sub(r"\x00(\d+)\x00", lambda m: self.tokens[int(m.group(1))], text)
        return text

    # ------------------------------------------------------------ blocks

    def blocks(self, lines: list[str], base: str = WIKI_REL, shift: int = 0, tight: bool = False) -> str:
        out: list[str] = []
        i, n = 0, len(lines)
        while i < n:
            line = lines[i]
            stripped = line.strip()
            if not stripped:
                i += 1
                continue
            fence = FENCE_RE.match(line)
            if fence:
                marker, lang = fence.group(1), fence.group(2)
                body = []
                i += 1
                while i < n and not (lines[i].strip().startswith(marker) and lines[i].strip().strip("`") == ""):
                    body.append(lines[i])
                    i += 1
                i += 1
                cls = f' class="language-{esc(lang)}"' if lang else ""
                out.append(f"<pre><code{cls}>{esc(chr(10).join(body))}</code></pre>")
                continue
            heading = HEADING_RE.match(line)
            if heading:
                out.append(self.heading(min(len(heading.group(1)) + shift, 6), heading.group(2), base, track=shift == 0))
                i += 1
                continue
            directive = DIRECTIVE_RE.match(stripped)
            if directive and shift == 0 and base == WIKI_REL:
                out.append(self.directive(directive.group(1), (directive.group(2) or "").strip()))
                i += 1
                continue
            if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
                out.append("<hr>")
                i += 1
                continue
            if stripped.startswith("|") and i + 1 < n and TABLE_SEP_RE.match(lines[i + 1].strip()):
                rows = [line]
                i += 2
                while i < n and lines[i].strip().startswith("|"):
                    rows.append(lines[i])
                    i += 1
                out.append(self.table(rows, base))
                continue
            if stripped.startswith(">"):
                quote = []
                while i < n and lines[i].strip().startswith(">"):
                    quote.append(re.sub(r"^\s*> ?", "", lines[i]))
                    i += 1
                out.append(self.quote(quote, base, shift))
                continue
            if LIST_RE.match(line):
                html_text, i = self.list_block(lines, i, base, shift)
                out.append(html_text)
                continue
            para = [stripped]
            i += 1
            while i < n and lines[i].strip() and not self.starts_block(lines, i):
                para.append(lines[i].strip())
                i += 1
            content = self.inline("\n".join(para), base=base)
            out.append(content if tight else f"<p>{content}</p>")
        return "\n".join(out)

    def starts_block(self, lines: list[str], i: int) -> bool:
        line = lines[i]
        stripped = line.strip()
        return bool(
            FENCE_RE.match(line)
            or HEADING_RE.match(line)
            or LIST_RE.match(line)
            or stripped.startswith(">")
            or DIRECTIVE_RE.match(stripped)
            or (stripped.startswith("|") and i + 1 < len(lines) and TABLE_SEP_RE.match(lines[i + 1].strip()))
        )

    def heading(self, level: int, raw: str, base: str, track: bool) -> str:
        content = self.inline(raw, base=base)
        if level == 1:
            anchor = self.new_id("top")
            return f'<h1 id="{anchor}">{content}</h1>'
        anchor = self.new_id(slugify(content))
        if track:
            self.heading_level = level
        if level <= 3:
            self.page.headings.append((anchor, html.unescape(re.sub(r"<[^>]+>", "", content))))
        return f'<h{level} id="{anchor}">{content}<a class="anchor" href="#{anchor}" aria-label="이 제목 링크">#</a></h{level}>'

    def table(self, rows: list[str], base: str) -> str:
        head = split_cells(rows[0])
        out = ['<div class="table-wrap"><table>', "<thead><tr>"]
        out += [f"<th>{self.inline(cell, base=base)}</th>" for cell in head]
        out.append("</tr></thead><tbody>")
        for row in rows[1:]:
            cells = split_cells(row)
            cells += [""] * (len(head) - len(cells))
            out.append("<tr>" + "".join(f"<td>{self.inline(cell, base=base)}</td>" for cell in cells[: len(head)]) + "</tr>")
        out.append("</tbody></table></div>")
        return "\n".join(out)

    def quote(self, lines: list[str], base: str, shift: int) -> str:
        callout = re.match(r"^\[!(note|tip|warning|status)\]\s*(.*)$", lines[0].strip()) if lines else None
        if not callout:
            return f"<blockquote>\n{self.blocks(lines, base, shift)}\n</blockquote>"
        kind, title = callout.group(1), callout.group(2) or CALLOUT_TITLES[callout.group(1)]
        return (
            f'<div class="callout callout-{kind}">\n<p class="callout-title">{self.inline(title, base=base)}</p>\n'
            f"{self.blocks(lines[1:], base, shift)}\n</div>"
        )

    def list_block(self, lines: list[str], i: int, base: str, shift: int) -> tuple[str, int]:
        first = LIST_RE.match(lines[i])
        indent, ordered = len(first.group(1)), first.group(2)[0].isdigit()
        start = int(first.group(2)[:-1]) if ordered else 1
        items: list[str] = []
        n = len(lines)
        while i < n:
            match = LIST_RE.match(lines[i])
            if not match or len(match.group(1)) != indent or match.group(2)[0].isdigit() != ordered:
                break
            content_indent = match.start(3)
            body = [match.group(3)]
            i += 1
            while i < n:
                line = lines[i]
                if not line.strip():
                    j = i
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n and indentation(lines[j]) > indent:
                        body.append("")
                        i += 1
                        continue
                    break
                level = indentation(line)
                if level > indent:
                    body.append(line[min(level, content_indent):])
                    i += 1
                    continue
                if body[-1].strip() and not self.starts_block(lines, i):
                    body.append(line.strip())
                    i += 1
                    continue
                break
            while body and not body[-1].strip():
                body.pop()
            items.append(f"<li>{self.blocks(body, base, shift, tight='' not in body)}</li>")
            j = i
            while j < n and not lines[j].strip():
                j += 1
            sibling = LIST_RE.match(lines[j]) if j < n else None
            if sibling and len(sibling.group(1)) == indent and sibling.group(2)[0].isdigit() == ordered:
                i = j
        tag = "ol" if ordered else "ul"
        start_attr = f' start="{start}"' if ordered and start != 1 else ""
        return f"<{tag}{start_attr}>\n" + "\n".join(items) + f"\n</{tag}>", i

    # ------------------------------------------------------------ directives

    def directive(self, name: str, arg: str) -> str:
        if name == "include":
            return self.include(arg)
        if name == "acceptance":
            return self.acceptance(arg)
        if name == "state":
            return self.state(arg)
        if name == "svg":
            try:
                svg = wiki_svg.render(arg, self.site.svg_data)
            except KeyError as error:
                raise BuildError(f"{self.page.slug}: unknown svg {arg}") from error
            for href in re.findall(r'href="([a-z0-9]+)\.html"', svg):
                if href not in self.site.pages:
                    raise BuildError(f"svg {arg}: link to unknown page {href}")
                if href != self.page.slug:
                    self.page.links_to.add(href)
            return f'<figure class="diagram diagram-{esc(arg)}">\n{svg}\n</figure>'
        if name == "sources":
            return self.sources()
        raise BuildError(f"{self.page.slug}: unknown directive {name}")

    def include(self, arg: str) -> str:
        doc, _, key = arg.partition("#")
        if doc not in DOCS or doc in {"TRACE", "STATE"} or not key:
            raise BuildError(f"{self.page.slug}: bad include {arg}")
        title, level, lines = section(self.site.data.texts[doc], key.strip(), doc)
        if doc == "CAPABILITIES":
            lines = [ACC_RE.sub(lambda m: f"- **[[{m.group(1)}]]** {m.group(2)}", line) for line in lines]
        rel = DOCS[doc]
        authored_level = self.heading_level
        target = min(authored_level + 1, 6)
        body = self.blocks(lines, base=posixpath.dirname(rel) or ".", shift=target - level)
        self.heading_level = authored_level
        href = posixpath.relpath(rel, WIKI_REL)
        return (
            f'<section class="source-include">\n<header class="source-label">원본: '
            f'<a class="repo" href="{esc(href)}">{esc(rel)}</a> · {self.inline(title, links=False)}</header>\n{body}\n</section>'
        )

    def acceptance(self, cap: str) -> str:
        rows = self.site.data.acceptance.get(cap)
        if rows is None:
            raise BuildError(f"{self.page.slug}: unknown capability {cap}")
        out = [
            '<div class="table-wrap"><table class="acceptance">',
            "<thead><tr><th>ID</th><th>인수 예시 (원문)</th><th>구현</th><th>테스트</th></tr></thead><tbody>",
        ]
        for acc, example in rows:
            anchor = self.new_id(acc)
            if anchor != acc:
                raise BuildError(f"{self.page.slug}: duplicate acceptance anchor {acc}")
            impl, tests = self.site.data.trace.get(acc, ([], []))
            impl_html = "<br>".join(f"<code>{esc(item)}</code>" for item in impl) or "추적 없음"
            if tests:
                items = "".join(f"<li><code>{esc(test)}</code></li>" for test in tests)
                tests_html = f"<details><summary>테스트 {len(tests)}개</summary><ul>{items}</ul></details>"
            else:
                tests_html = "추적 없음"
            out.append(
                f'<tr><td class="acc-id"><a class="acc-id" id="{acc}" href="#{acc}">{acc}</a></td>'
                f'<td class="acc-example">{self.inline(example, base="docs")}</td>'
                f'<td class="acc-impl">{impl_html}</td><td class="acc-tests">{tests_html}</td></tr>'
            )
        out.append("</tbody></table></div>")
        return "\n".join(out)

    def badge(self, value: str | None) -> str:
        if not value:
            return ""
        return f'<span class="badge badge-{esc(re.sub(r"[^a-z_]", "", value.lower()))}">{esc(value)}</span>'

    def record_link(self, record_id: str) -> str:
        page = self.site.record_page.get(record_id)
        if not page:
            return f"<code>{esc(record_id)}</code>"
        return f'<a class="record-ref" href="{esc(self.page_href(page, record_id))}"><code>{esc(record_id)}</code></a>'

    def state(self, kind: str) -> str:
        if kind not in STATE_KINDS:
            raise BuildError(f"{self.page.slug}: unknown state table {kind}")
        records = [r for r in self.site.data.records if r.name == STATE_KINDS[kind]]
        columns = {
            "goals": ["ID", "단계", "상태", "목표"],
            "accepts": ["ID", "상태", "인수 조건", "근거"],
            "blockers": ["ID", "상태", "조치", "출처"],
            "decisions": ["ID", "상태", "결정", "출처"],
            "evidence": ["ID", "분류", "결과", "관찰", "출처"],
            "handoff": ["#", "상태", "다음 작업"],
        }[kind]
        out = [f'<div class="table-wrap"><table class="state state-{kind}">', "<thead><tr>"]
        out += [f"<th>{col}</th>" for col in columns]
        out.append("</tr></thead><tbody>")
        for number, record in enumerate(records, 1):
            fields = record.fields
            record_id = fields.get("id") or f"handoff-{number}"
            anchor = self.new_id(record_id)
            if anchor != record_id:
                raise BuildError(f"{self.page.slug}: duplicate state anchor {record_id}")
            cells = [f'<code>{esc(record_id)}</code>' if fields.get("id") else str(number)]
            if kind == "goals":
                cells += [self.badge(fields.get("phase")), self.badge(fields.get("state")), esc(fields.get("action", ""))]
            elif kind == "accepts":
                refs = [self.record_link(e) for e in fields.get("evidence", "").split(",") if e]
                cells += [self.badge(fields.get("state")), esc(fields.get("action", "")), " ".join(refs)]
            elif kind in {"blockers", "decisions"}:
                cells += [self.badge(fields.get("state")), esc(fields.get("action", "")), f'<code>{esc(fields.get("source", ""))}</code>']
            elif kind == "evidence":
                cells += [
                    self.badge(fields.get("class")),
                    self.badge(fields.get("result")),
                    esc(fields.get("observed", "")),
                    f'<code>{esc(fields.get("source", ""))}</code>',
                ]
            else:
                cells += [self.badge(fields.get("state")), esc(fields.get("action", ""))]
            out.append(f'<tr id="{esc(record_id)}">' + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
        out.append("</tbody></table></div>")
        return "\n".join(out)

    def sources(self) -> str:
        out = ['<div class="table-wrap"><table class="sources">', "<thead><tr><th>문서</th><th>경로</th><th>SHA-256</th></tr></thead><tbody>"]
        for key, rel in DOCS.items():
            href = posixpath.relpath(rel, WIKI_REL)
            out.append(
                f'<tr><td>{esc(DOC_TITLES[key])}</td><td><a class="repo" href="{esc(href)}"><code>{esc(rel)}</code></a></td>'
                f"<td><code>{self.site.data.digests[key][:16]}</code></td></tr>"
            )
        out.append("</tbody></table></div>")
        return "\n".join(out)


# ---------------------------------------------------------------- site assembly


def load_pages() -> dict[str, Page]:
    group_of = {slug: group for group, slugs in GROUPS for slug in slugs}
    found = sorted(p.stem for p in SOURCE.glob("*.md"))
    missing = [slug for slug in nav_order() if slug not in found]
    extra = [slug for slug in found if slug not in group_of]
    if missing or extra:
        raise BuildError(f"page sources missing {missing} or not in navigation {extra}")
    pages = {}
    for slug in nav_order():
        text = (SOURCE / f"{slug}.md").read_text(encoding="utf-8").replace("\r\n", "\n")
        first = next((line for line in text.split("\n") if line.startswith("# ")), None)
        if first is None:
            raise BuildError(f"{slug}: no '# ' title")
        title = first[2:].strip()
        label = NAV_LABELS.get(slug) or re.sub(r"\s*\([^)]*\)\s*$", "", title)
        page = Page(slug, text, title, label, group_of[slug])
        page.state_kinds = set(re.findall(r"^\{\{state:(\w+)\}\}$", text, re.M))
        page.acc_caps = set(re.findall(r"^\{\{acceptance:(C\d+)\}\}$", text, re.M))
        pages[slug] = page
    return pages


def plain_text(fragment: str) -> str:
    text = re.sub(r"<(script|style|svg)\b.*?</\1>", " ", fragment, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip()


def chrome(page: Page, pages: dict[str, Page], digest: str) -> str:
    nav = []
    for group, slugs in GROUPS:
        items = []
        for slug in slugs:
            current = ' aria-current="page"' if slug == page.slug else ""
            items.append(f'<li><a href="{slug}.html"{current}>{esc(pages[slug].label)}</a></li>')
        nav.append(f'<div class="nav-group"><h2>{esc(group)}</h2><ul>{"".join(items)}</ul></div>')
    crumbs = '<a href="index.html">위키</a>'
    if page.slug != "index":
        crumbs += f" › <span>{esc(page.group)}</span> › <span aria-current=\"page\">{esc(page.label)}</span>"
    backlinks = sorted(
        (slug for slug in pages if page.slug in pages[slug].links_to and slug != page.slug),
        key=nav_order().index,
    )
    related = ""
    if backlinks:
        chips = "".join(f'<li><a href="{slug}.html">{esc(pages[slug].label)}</a></li>' for slug in backlinks)
        related = f'\n<section class="related"><h2 id="backlinks">이 페이지를 가리키는 문서</h2><ul class="chips">{chips}</ul></section>'
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{esc(page.label)} · KnowledgeOS 위키</title>
<link rel="stylesheet" href="assets/wiki.css">
</head>
<body class="wiki" data-slug="{page.slug}">
<a class="skip" href="#content">본문으로</a>
<header class="topbar"><button class="nav-toggle" type="button" aria-label="메뉴" aria-expanded="false" aria-controls="sidebar">☰</button><a class="brand" href="index.html">KnowledgeOS 위키</a>
<div class="search"><input id="wiki-search" type="search" placeholder="검색 (/)" aria-label="위키 검색" autocomplete="off"><ol id="search-results" hidden></ol></div></header>
<div class="layout">
<nav class="sidebar" id="sidebar" aria-label="위키 목차">{"".join(nav)}</nav>
<main id="content" class="content"><nav class="breadcrumb" aria-label="위치">{crumbs}</nav>
<article class="page">
{page.body}{related}
</article>
<footer class="meta">원본 digest <code>{digest}</code> · <a href="philosophy.html">위키 운용</a></footer></main></div>
<script src="assets/search-index.js"></script><script src="assets/wiki.js"></script>
</body>
</html>
"""


def build() -> dict[str, bytes]:
    """Return every generated file as {path relative to docs/wiki: bytes}."""
    data = load_data()
    pages = load_pages()
    site = Site(data, pages)
    for slug in nav_order():
        page = pages[slug]
        renderer = Renderer(site, page)
        page.body = renderer.blocks(page.text.split("\n"))
    errors = []
    for page in pages.values():
        for slug, anchor, shown in page.refs:
            if anchor and unquote(anchor) not in pages[slug].ids:
                errors.append(f"{page.slug}: link {shown} has no anchor")
    if errors:
        raise BuildError("\n".join(sorted(set(errors))))

    hasher = hashlib.sha256()
    for key in DOCS:
        hasher.update(data.digests[key].encode())
    for path in sorted(SOURCE.rglob("*")):
        if path.is_file():
            hasher.update(path.relative_to(SOURCE).as_posix().encode())
            hasher.update(hashlib.sha256(path.read_bytes()).digest())
    digest = hasher.hexdigest()[:16]

    outputs: dict[str, bytes] = {}
    index = []
    for slug in nav_order():
        page = pages[slug]
        outputs[f"{slug}.html"] = chrome(page, pages, digest).encode("utf-8")
        index.append({
            "slug": slug,
            "title": page.label,
            "group": page.group,
            "headings": [{"id": anchor, "text": text} for anchor, text in page.headings],
            "text": plain_text(page.body),
        })
    script = "window.KOS_WIKI_INDEX = " + json.dumps(index, ensure_ascii=False, separators=(",", ":")) + ";\n"
    outputs["assets/search-index.js"] = script.encode("utf-8")
    assets = SOURCE / "assets"
    if assets.is_dir():
        for path in sorted(assets.iterdir()):
            if path.is_file():
                if f"assets/{path.name}" in outputs:
                    raise BuildError(f"source asset shadows generated output: {path.name}")
                outputs[f"assets/{path.name}"] = path.read_bytes()
    return outputs


def generated_files() -> list[str]:
    """Existing generated files in docs/wiki (everything except _source)."""
    return sorted(
        path.relative_to(WIKI).as_posix()
        for path in WIKI.rglob("*")
        if path.is_file() and path.relative_to(WIKI).parts[0] != "_source"
    )


def main() -> int:
    try:
        outputs = build()
    except BuildError as error:
        print(f"wiki build failed:\n{error}", file=sys.stderr)
        return 1
    for rel in generated_files():
        if rel not in outputs:
            (WIKI / rel).unlink()
    (WIKI / "assets").mkdir(exist_ok=True)
    for rel, content in sorted(outputs.items()):
        path = WIKI / rel
        if not path.exists() or path.read_bytes() != content:
            path.write_bytes(content)
    print(f"wiki: {len(outputs)} files in {WIKI_REL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
