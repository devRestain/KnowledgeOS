"""Capability C2 deterministic standardization rules (pure: no I/O, no clock).

Each rule applies only where a value is missing or malformed and a deterministic fix
exists; values that need judgment (an unknown type, an invalid status, a non-UUID id)
are left to capability C1. Edits go through ``FrontmatterEditor`` so untouched lines
keep their exact bytes, missing keys are appended at the end of the frontmatter and the
body changes only by removing rule-8 trailing list lines.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import yaml

from .notes import KEY_RE, UUID4_RE, Change, FrontmatterEditor, Note, load_yaml, parse
from .policy import DEFAULT_POLICY, ORDER
from .vault import RESERVED_REQUESTS, Vault

LIST_KEYS = ("tags", "aliases", "related", "projects", "sources", "supports", "contradicts", "derived_from")
LINK_KEYS = LIST_KEYS[2:]
STATUSES: dict[str, tuple[str, ...]] = {
    "capture": ("unprocessed", "processed"),
    "idea": ("seed", "developing", "mature", "dropped"),
    "knowledge": ("seed", "developing", "evergreen"),
    "question": ("open", "answered", "dropped"),
    "project": ("planned", "active", "paused", "done"),
    "source": ("unread", "reading", "read"),
}
INITIAL_STATUS = {note_type: values[0] for note_type, values in STATUSES.items()}
OUTPUT_TYPES = ("report", "proposal")
NAVIGATION_TYPES = ("home", "system")
KNOWN_TYPES = (*STATUSES, *OUTPUT_TYPES)
ENUMS: dict[str, tuple[str, ...]] = {
    "ai_policy": ORDER,
    "sensitivity": ("public", "personal", "confidential"),
    "confidence": ("low", "medium", "high"),
    "capture_kind": ("text", "link", "voice", "image"),
}
MANAGED_KEYS = ("id", "type", "created", "status", *ENUMS, *LIST_KEYS)
FOLDER_TYPES = (
    ("00_Inbox/", "capture"),
    ("40_Knowledge/Ideas/", "idea"),
    ("40_Knowledge/Notes/", "knowledge"),
    ("40_Knowledge/Questions/", "question"),
    ("40_Knowledge/Sources/", "source"),
)

ISO_OFFSET_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$")
LOOSE_DATETIME_RE = re.compile(
    r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"
    r"(?:[T ]+(\d{1,2}):(\d{2})(?::(\d{2})(\.\d+)?)?)?[ \t]*(Z|[+-]\d{2}:?\d{2})?$"
)
TRAILING_KEY_RE = re.compile(rf"^({'|'.join(LIST_KEYS)})[ \t]*:(?:[ \t]+(.*?))?[ \t]*$")
TRAILING_ITEM_RE = re.compile(r"^[ \t]*-[ \t]+(.+?)[ \t]*$")
LINK_TEXT_RE = re.compile(r"\[\[[^\[\]]+\]\]")


@dataclass
class Plan:
    """The deterministic outcome for one note: a skip reason, no change, or result bytes."""

    path: str
    preimage: str
    result: bytes | None = None
    changes: list[Change] = field(default_factory=list)
    skip: str | None = None

    @property
    def changed(self) -> bool:
        return self.result is not None


# ---------------------------------------------------------------- eligibility


def derive_type(path: str) -> str | None:
    for prefix, note_type in FOLDER_TYPES:
        if path.startswith(prefix):
            return note_type
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "20_Projects" and parts[2] == f"{parts[1]}.md":
        return "project"
    return None


def path_skip_reason(path: str) -> str | None:
    """Paths the Never list excludes, decided without reading the file."""
    if Vault.ignored(path):
        return "ignored"
    if Vault.system(path):
        return "system"
    if Vault.review(path):
        return "review"
    if path.startswith(RESERVED_REQUESTS):
        return "reserved"
    return None


def note_skip_reason(note: Note, policy: str, duplicate_ids: Iterable[str] = ()) -> str | None:
    """Content-based skips shared by every path (Never list and Skips)."""
    if policy == "deny":
        return "deny"
    if note.conflict:
        return "conflict"
    if note.decode_error:
        return "not_utf8"
    if note.yaml_error is not None:
        return "invalid_yaml"
    if note.note_id is not None and note.note_id in set(duplicate_ids):
        return "duplicate_id"
    if note.type in NAVIGATION_TYPES:
        return "navigation"
    return None


# ---------------------------------------------------------------- value shapes


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def normalize_datetime(text: str) -> str | None:
    """ISO-8601 with an offset (Asia/Seoul when none is stated); None when not a date."""
    text = text.strip()
    if ISO_OFFSET_RE.match(text):
        return text
    match = LOOSE_DATETIME_RE.match(text)
    if not match:
        return None
    year, month, day, hour, minute, second, fraction, offset = match.groups()
    try:
        datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0), int(second or 0))
    except ValueError:
        return None
    if offset is None:
        offset = "+09:00"
    elif offset != "Z" and ":" not in offset:
        offset = f"{offset[:3]}:{offset[3:]}"
    return (
        f"{year}-{int(month):02d}-{int(day):02d}T{int(hour or 0):02d}:{minute or '00'}:"
        f"{second or '00'}{fraction or ''}{offset}"
    )


def _canonical_enum(value: Any, allowed: Iterable[str]) -> str | None:
    """The canonical spelling when value differs from an allowed value only by case or spaces."""
    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    return lowered if lowered in tuple(allowed) and lowered != value else None


def _scalar_items(items: list[Any]) -> list[str] | None:
    out: list[str] = []
    for item in items:
        if isinstance(item, (list, dict)):
            return None
        if item is None:
            continue
        text = item if isinstance(item, str) else str(item)
        text = text.strip()
        if text and text not in out:
            out.append(text)
    return out


def normalize_tags(value: Any) -> list[str] | None:
    """Tags as a list of strings without ``#``; None when it cannot be normalized."""
    if isinstance(value, str):
        items: list[Any] = re.split(r"[,\s]+", value)
    elif isinstance(value, list):
        items = value
    elif value is None or isinstance(value, dict):
        return None
    else:
        items = [value]
    if any(isinstance(item, (list, dict)) for item in items):
        return None
    return _scalar_items([str(item).strip().lstrip("#") for item in items if item is not None])


def normalize_aliases(value: Any) -> list[str] | None:
    if isinstance(value, str):
        value = [value]
    elif not isinstance(value, list):
        if value is None or isinstance(value, dict):
            return None
        value = [value]
    return _scalar_items(value)


def normalize_links(value: Any) -> list[str] | None:
    """Link property values as ``[[target]]`` strings; repairs ``[[x]]`` parsed as a nested list."""
    if value is None:
        return []
    if isinstance(value, str):
        found = LINK_TEXT_RE.findall(value)
        return found or ([value.strip()] if value.strip() else [])
    if not isinstance(value, list):
        return None
    out: list[str] = []
    for item in value:
        if isinstance(item, list) and len(item) == 1 and isinstance(item[0], str):
            item = f"[[{item[0]}]]"
        if not isinstance(item, str):
            return None
        if item.strip() and item.strip() not in out:
            out.append(item.strip())
    return out


def _normalize_list(key: str, value: Any) -> list[str] | None:
    if key == "tags":
        return normalize_tags(value)
    if key == "aliases":
        return normalize_aliases(value)
    return normalize_links(value)


# ---------------------------------------------------------------- rule 8


def _unquote(text: str) -> str | None:
    """A YAML scalar as written in a body line; quoted forms are YAML-decoded."""
    text = text.strip()
    if text[:1] not in ("'", '"'):
        return text
    try:
        loaded = load_yaml(f"v: {text}")
    except yaml.YAMLError:
        return None
    value = loaded.get("v") if isinstance(loaded, dict) else None
    return value if isinstance(value, str) else None


def _line_values(key: str, raw: str) -> list[str] | None:
    raw = raw.strip()
    if not raw:
        return None
    if key in LINK_KEYS:
        return LINK_TEXT_RE.findall(raw) or None
    if raw.startswith("[") and raw.endswith("]"):
        parts = raw[1:-1].split(",")
        if key == "tags":
            parts = [piece for part in parts for piece in part.split()]
    elif key == "tags":
        parts = re.split(r"[,\s]+", raw)
    else:
        parts = raw.split(",")
    items = [_unquote(part) for part in parts if part.strip()]
    if any(item is None for item in items):
        return None
    return _normalize_list(key, items) or None


def _item_value(key: str, raw: str) -> list[str] | None:
    if key in LINK_KEYS:
        return LINK_TEXT_RE.findall(raw) or None
    item = _unquote(raw)
    if item is None:
        return None
    return _normalize_list(key, [item]) or None


def _parse_region(lines: list[str]) -> tuple[dict[str, list[tuple[int, list[str]]]], int | None]:
    """Parse trailing key/item lines. Returns groups per key or the index of an unparseable line."""
    groups: dict[str, list[tuple[int, list[str]]]] = {}
    pending: tuple[str, list[int], list[str]] | None = None

    def flush() -> int | None:
        nonlocal pending
        if pending is None:
            return None
        key, indexes, values = pending
        pending = None
        if not values:
            return indexes[0]
        for offset, index in enumerate(indexes):
            groups.setdefault(key, []).append((index, values if offset == 0 else []))
        return None

    for index, line in enumerate(lines):
        text = line.rstrip("\r\n")
        key_match = TRAILING_KEY_RE.match(text)
        if key_match:
            bad = flush()
            if bad is not None:
                return groups, bad
            key, raw = key_match.group(1), key_match.group(2) or ""
            if raw:
                values = _line_values(key, raw)
                if values is None:
                    return groups, index
                groups.setdefault(key, []).append((index, values))
            else:
                pending = (key, [index], [])
            continue
        item_match = TRAILING_ITEM_RE.match(text)
        if item_match:
            if pending is None:
                return groups, index
            values = _item_value(pending[0], item_match.group(1))
            if values is None:
                return groups, index
            pending[1].append(index)
            pending[2].extend(value for value in values if value not in pending[2])
            continue
        bad = flush()  # a blank line ends a block list
        if bad is not None:
            return groups, bad
    bad = flush()
    return groups, bad


def extract_trailing_lists(body: str) -> tuple[list[str], dict[str, list[str]], dict[str, list[int]]]:
    """Rule 8: find trailing body lines that are known list-property keys.

    Returns the body lines, the values per key and the body line indexes per key.
    """
    lines = body.splitlines(keepends=True)
    start = len(lines)
    while start > 0:
        text = lines[start - 1].rstrip("\r\n")
        if not text.strip() or TRAILING_KEY_RE.match(text) or TRAILING_ITEM_RE.match(text):
            start -= 1
            continue
        break
    while start < len(lines):
        groups, bad = _parse_region(lines[start:])
        if bad is None:
            values: dict[str, list[str]] = {}
            indexes: dict[str, list[int]] = {}
            for key, entries in groups.items():
                for index, found in entries:
                    indexes.setdefault(key, []).append(start + index)
                    values.setdefault(key, []).extend(v for v in found if v not in values.get(key, []))
            return lines, values, indexes
        start += bad + 1
    return lines, {}, {}


# ---------------------------------------------------------------- planning


def plan(
    path: str,
    note: Note,
    *,
    policy: str,
    created: str,
    new_id: Callable[[], str],
    default_policy: str = DEFAULT_POLICY,
    duplicate_ids: Iterable[str] = (),
) -> Plan:
    """Apply rules 1-8 to one note snapshot. ``created`` is the first-observed time."""
    result = Plan(path=path, preimage=note.sha)
    result.skip = path_skip_reason(path) or note_skip_reason(note, policy, duplicate_ids)
    if result.skip:
        return result
    data = note.data
    editor = FrontmatterEditor(note)
    changes: list[Change] = []

    def change(key: str, rule: str, after: Any, keep_block: bool = False) -> None:
        changes.append(Change(key, rule, data.get(key), after))
        editor.set(key, after, keep_block=keep_block)

    if not note.has_frontmatter:
        changes.append(Change("frontmatter", "frontmatter", None, "created"))
    # rule 2
    identifier = data.get("id")
    if not isinstance(identifier, str) or not UUID4_RE.fullmatch(identifier.lower()):
        if isinstance(identifier, str) and UUID4_RE.fullmatch(identifier.strip().lower()):
            change("id", "shape", identifier.strip().lower())
        else:
            change("id", "id", new_id())
    # rule 3
    note_type = note.type
    if _missing(data.get("type")):
        note_type = derive_type(path)
        if note_type:
            change("type", "type", note_type)
    elif (canonical := _canonical_enum(data["type"], KNOWN_TYPES)) is not None:
        change("type", "shape", canonical)
    # rule 4
    if _missing(data.get("created")):
        change("created", "created", created)
    elif isinstance(data["created"], str):
        normalized = normalize_datetime(data["created"])
        if normalized is not None and normalized != data["created"]:
            change("created", "shape", normalized)
    # rule 5
    if note_type in STATUSES:
        if _missing(data.get("status")):
            change("status", "status", INITIAL_STATUS[note_type])
        elif (canonical := _canonical_enum(data["status"], (*STATUSES[note_type], "archived"))) is not None:
            change("status", "shape", canonical)
    # rule 6
    if _missing(data.get("ai_policy")):
        change("ai_policy", "ai_policy", default_policy)
    # rule 7: enum case
    for key, allowed in ENUMS.items():
        if key in data and (canonical := _canonical_enum(data[key], allowed)) is not None:
            change(key, "shape", canonical)
    # rule 7 list shapes, merged with rule 8 trailing lines
    body_lines, moved, moved_indexes = extract_trailing_lists(note.body)
    removed: set[int] = set()
    for key in LIST_KEYS:
        original = data.get(key)
        additions = moved.get(key, [])
        if key in ("tags", "aliases") and original is not None:
            base = _normalize_list(key, original)
        elif additions:
            base = normalize_links(original) if key in LINK_KEYS else ([] if original is None else None)
        else:
            continue
        if base is None:
            continue  # existing value needs judgment; leave it and its trailing lines alone
        merged = base + [value for value in additions if value not in base]
        if additions:
            removed.update(moved_indexes[key])
            if merged == original:  # values already present: only the body lines go
                changes.append(Change(key, "trailing_list", original, merged))
                continue
        if merged != original:
            change(key, "trailing_list" if additions else "shape", merged, keep_block=True)
    if removed:
        editor.body = "".join(line for index, line in enumerate(body_lines) if index not in removed)
    if not changes:
        return result
    rendered = editor.render()
    if rendered != note.raw:
        result.result = rendered
        result.changes = changes
    return result


def diff(path: str, before: bytes, after: bytes) -> str:
    return "".join(
        difflib.unified_diff(
            before.decode("utf-8", "replace").splitlines(keepends=True),
            after.decode("utf-8", "replace").splitlines(keepends=True),
            f"a/{path}",
            f"b/{path}",
        )
    )


# ---------------------------------------------------------------- verification


def _key_blocks(note: Note) -> list[tuple[str | None, str]]:
    """Frontmatter split into (key, exact text) blocks in order."""
    blocks: list[tuple[str | None, str]] = []
    for line in note.fm_lines:
        match = KEY_RE.match(line)
        if match or not blocks or (line[:1] not in (" ", "\t", "-") and line.strip()):
            blocks.append((match.group(1) if match else None, line))
        else:
            key, text = blocks[-1]
            blocks[-1] = (key, text + line)
    return blocks


def _valid(key: str, value: Any, note_type: str | None) -> bool:
    """Whether a value standardization wrote has the contract shape (Blueprint §5.3)."""
    if key == "id":
        return isinstance(value, str) and bool(UUID4_RE.fullmatch(value.lower()))
    if key == "created":
        return isinstance(value, str) and bool(ISO_OFFSET_RE.match(value))
    if key == "type":
        return value in KNOWN_TYPES
    if key == "status":
        return value in (*STATUSES.get(note_type or "", ()), "archived")
    if key in ENUMS:
        return value in ENUMS[key]
    if key in LIST_KEYS:
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return True


def verify(path: str, before: Note, after_raw: bytes, changed_keys: Iterable[str]) -> dict[str, str | None]:
    """Independent static checks of one planned result.

    Returns a problem (or None) for ``result_valid``, ``properties_preserved`` and
    ``minimal_diff``.
    """
    after = parse(path, after_raw)
    changed = set(changed_keys)
    problems: dict[str, str | None] = {"result_valid": None, "properties_preserved": None, "minimal_diff": None}
    if not after.valid or not after.has_frontmatter or after.conflict:
        problems["result_valid"] = "result does not parse as a note with YAML frontmatter"
        return problems
    note_type = after.type
    missing = [key for key in ("id", "created", "ai_policy") if _missing(after.data.get(key))]
    invalid = [key for key in sorted(changed & set(MANAGED_KEYS)) if not _valid(key, after.data.get(key), note_type)]
    if missing:
        problems["result_valid"] = f"result lacks {', '.join(missing)}"
    elif invalid:
        problems["result_valid"] = f"result has invalid {', '.join(invalid)}"
    elif derive_type(path) and _missing(after.data.get("type")):
        problems["result_valid"] = "result lacks the folder-derived type"
    before_keys = list(before.data)
    after_keys = list(after.data)
    if after_keys[: len(before_keys)] != before_keys:
        problems["properties_preserved"] = "a property was deleted, renamed or reordered"
    elif not changed <= set(MANAGED_KEYS) | {"frontmatter"}:
        problems["properties_preserved"] = f"unmanaged keys changed: {sorted(changed - set(MANAGED_KEYS))}"
    elif any(before.data[key] != after.data.get(key) for key in before_keys if key not in changed):
        problems["properties_preserved"] = "an unchanged property has a different value"
    untouched = [text for key, text in _key_blocks(before) if key not in changed]
    after_text = "".join(after.fm_lines)
    position = 0
    for text in untouched:
        found = after_text.find(text, position)
        if found < 0:
            problems["minimal_diff"] = "an untouched frontmatter line changed"
            break
        position = found + len(text)
    if problems["minimal_diff"] is None:
        after_body = after.body.splitlines(keepends=True)
        pointer = 0
        for line in before.body.splitlines(keepends=True):
            if pointer < len(after_body) and after_body[pointer] == line:
                pointer += 1
                continue
            text = line.rstrip("\r\n")
            if not (TRAILING_KEY_RE.match(text) or TRAILING_ITEM_RE.match(text)):
                problems["minimal_diff"] = "body text other than trailing list lines changed"
                break
        else:
            if pointer != len(after_body):
                problems["minimal_diff"] = "body text was added"
    return problems
