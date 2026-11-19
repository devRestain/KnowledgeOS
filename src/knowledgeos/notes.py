"""Note parsing and minimal-diff frontmatter editing (Blueprint §5.3, §5.7).

Edits operate on the original lines: untouched lines keep their exact bytes, line
endings and order. Values are parsed with YAML timestamps kept as strings.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from .util import sha256


class _StringTimestampLoader(yaml.SafeLoader):
    """Safe loader that keeps dates as written."""


_StringTimestampLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def load_yaml(text: str) -> Any:
    return yaml.load(text, Loader=_StringTimestampLoader)


CONFLICT_RE = re.compile(r"^(<{7} |={7}$|>{7} )", re.MULTILINE)
KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)[ \t]*:(?:[ \t]|$)")
WIKILINK_RE = re.compile(r"\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|[^\]]*)?\]\]")
UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


@dataclass
class Note:
    """One parsed Markdown file."""

    path: str
    raw: bytes
    text: str = ""
    bom: bool = False
    newline: str = "\n"
    has_frontmatter: bool = False
    fm_lines: list[str] = field(default_factory=list)  # with line endings
    body: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    yaml_error: str | None = None
    decode_error: bool = False
    conflict: bool = False

    @property
    def sha(self) -> str:
        return sha256(self.raw)

    @property
    def note_id(self) -> str | None:
        value = self.data.get("id")
        return str(value) if isinstance(value, str) and value.strip() else None

    @property
    def type(self) -> str | None:
        value = self.data.get("type")
        return value.strip().lower() if isinstance(value, str) else None

    @property
    def status(self) -> str | None:
        value = self.data.get("status")
        return value.strip().lower() if isinstance(value, str) else None

    @property
    def title(self) -> str:
        value = self.data.get("title")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return self.path.rsplit("/", 1)[-1].removesuffix(".md")

    @property
    def body_start_line(self) -> int:
        """1-based line number of the first body line."""
        return len(self.fm_lines) + 3 if self.has_frontmatter else 1

    @property
    def valid(self) -> bool:
        return not self.decode_error and self.yaml_error is None

    def list_value(self, key: str) -> list[str]:
        value = self.data.get(key)
        if isinstance(value, list):
            return [str(item) for item in value if item is not None]
        if isinstance(value, str) and value.strip():
            return [value]
        return []

    def links(self) -> set[str]:
        """Wikilink targets in body and link properties, as written (no extension)."""
        targets = {match.group(1).strip() for match in WIKILINK_RE.finditer(self.text)}
        return {target.removesuffix(".md") for target in targets if target}


def parse(path: str, raw: bytes) -> Note:
    note = Note(path=path, raw=raw)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        note.decode_error = True
        return note
    if text.startswith("\ufeff"):
        note.bom = True
        text = text[1:]
    note.text = text
    note.newline = "\r\n" if "\r\n" in text else "\n"
    note.conflict = bool(CONFLICT_RE.search(text))
    lines = text.splitlines(keepends=True)
    if lines and lines[0].rstrip("\r\n") == "---":
        for index in range(1, len(lines)):
            if lines[index].rstrip("\r\n") in ("---", "..."):
                note.has_frontmatter = True
                note.fm_lines = lines[1:index]
                note.body = "".join(lines[index + 1 :])
                break
    if not note.has_frontmatter:
        note.body = text
        return note
    try:
        loaded = load_yaml("".join(note.fm_lines))
    except yaml.YAMLError as error:
        note.yaml_error = str(error).splitlines()[0]
        return note
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        note.yaml_error = "frontmatter is not a mapping"
        return note
    note.data = {str(key): value for key, value in loaded.items()}
    return note


def raw_policy_hint(note: Note) -> str | None:
    """Best-effort ai_policy for notes whose YAML cannot be parsed (fail closed on deny)."""
    for line in note.fm_lines:
        match = re.match(r"^ai_policy[ \t]*:[ \t]*['\"]?([A-Za-z_]+)", line)
        if match:
            return match.group(1).lower()
    return None


# ---------------------------------------------------------------- rendering

# Plain scalars may contain ':' only when not followed by whitespace (e.g. ISO timestamps).
_PLAIN_RE = re.compile(r"^[^\s\-?:,\[\]{}#&*!|>'\"%@`](?:[^\n#:]|:(?=\S))*$")


def render_scalar(value: Any, flow: bool = False) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    plain_ok = bool(_PLAIN_RE.match(text)) and text == text.strip()
    if plain_ok and flow and any(ch in text for ch in ",[]{}:"):
        plain_ok = False
    if plain_ok:
        try:
            plain_ok = load_yaml(f"k: {text}") == {"k": text}
        except yaml.YAMLError:
            plain_ok = False
    return text if plain_ok else json.dumps(text, ensure_ascii=False)


def render_list(values: list[Any]) -> str:
    return "[" + ", ".join(render_scalar(item, flow=True) for item in values) + "]"


def render_value(value: Any) -> str:
    return render_list(value) if isinstance(value, list) else render_scalar(value)


# ---------------------------------------------------------------- editing


@dataclass
class Change:
    key: str
    rule: str
    before: Any
    after: Any

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "rule": self.rule, "before": self.before, "after": self.after}


class FrontmatterEditor:
    """Line-preserving editor over a parsed note."""

    def __init__(self, note: Note):
        self.note = note
        self.newline = note.newline
        self.lines = list(note.fm_lines)
        self.body = note.body
        self.created_block = not note.has_frontmatter

    def _spans(self) -> dict[str, tuple[int, int]]:
        """Line span per key; trailing blank lines stay outside the span."""
        spans: dict[str, tuple[int, int]] = {}
        current: str | None = None
        start = 0

        def close(end: int) -> None:
            while end > start + 1 and not self.lines[end - 1].strip():
                end -= 1
            if current is not None:
                spans.setdefault(current, (start, end))

        for index, line in enumerate(self.lines):
            match = KEY_RE.match(line)
            starts_new = match is not None or (line[:1] not in (" ", "\t", "-") and line.strip() != "")
            if starts_new:
                close(index)
                current = match.group(1) if match else None
                start = index
        close(len(self.lines))
        return spans

    def is_block(self, key: str) -> bool:
        span = self._spans().get(key)
        return span is not None and span[1] - span[0] > 1

    def _line(self, text: str) -> str:
        return text + self.newline

    def set(self, key: str, value: Any, keep_block: bool = False) -> None:
        spans = self._spans()
        if key in spans and keep_block and isinstance(value, list) and self.is_block(key):
            start, end = spans[key]
            indent = re.match(r"^([ \t]*)-", self.lines[start + 1] or "")
            prefix = indent.group(1) if indent else "  "
            rendered = [self._line(f"{key}:")] + [
                self._line(f"{prefix}- {render_scalar(item)}") for item in value
            ]
        else:
            rendered = [self._line(f"{key}: {render_value(value)}")]
        if key in spans:
            start, end = spans[key]
            self.lines[start:end] = rendered
        else:
            if self.lines and not self.lines[-1].endswith(("\n", "\r")):
                self.lines[-1] = self.lines[-1] + self.newline
            self.lines.extend(rendered)

    def render(self) -> bytes:
        delimiter = self._line("---")
        text = delimiter + "".join(self.lines) + delimiter + self.body
        if not self.created_block:
            # keep the original closing delimiter bytes
            original = self.note.text.splitlines(keepends=True)
            closing = original[len(self.note.fm_lines) + 1]
            text = original[0] + "".join(self.lines) + closing + self.body
        data = text.encode("utf-8")
        return (b"\xef\xbb\xbf" + data) if self.note.bom else data
