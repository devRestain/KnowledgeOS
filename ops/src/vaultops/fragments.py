"""Strict daily-fragment locators shared by C18 and C27.

Daily notes are source documents, not an arbitrary text container.  A caller
may bind a proposal to one bounded fragment, but the fragment digest is only
trusted after this module resolves the locator against the current UTF-8
Markdown bytes and hashes the resolved bytes again.

The supported locator forms are deliberately small:

* ``#^block-id`` selects the exact block-id line without its line ending.
* ``#Heading`` (and ``#Heading@2`` for a repeated heading) selects a heading
  section through the next heading of the same or a higher level.
* ``/body`` selects the complete body, and ``/body/preamble`` selects the
  text before the first heading.
* ``line:N`` or ``line:N-M`` selects one-based body lines without line endings.

Returned bytes are UTF-8 bytes of the resolved, edge-trimmed text.  Trimming
is part of the locator contract and is performed before hashing, never after a
digest has been accepted.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from .note_engine import FrontmatterError, parse_frontmatter

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BLOCK_LOCATOR_RE = re.compile(r"^#\^[-A-Za-z0-9_:.]+$")
_HEADING_LOCATOR_RE = re.compile(r"^#[^\r\n#][^\r\n]*$")
_LINE_LOCATOR_RE = re.compile(r"^line:(?P<start>[1-9][0-9]*)(?:-(?P<end>[1-9][0-9]*))?$")
_HEADING_RE = re.compile(r"^(?P<marks>#{1,6})[ \t]+(?P<title>.+?)[ \t]*$", re.MULTILINE)


class FragmentError(ValueError):
    """Raised when a daily-fragment locator cannot be resolved safely."""


@dataclass(frozen=True)
class DailyFragment:
    """Resolved daily-fragment metadata and its canonical text."""

    locator: str
    text: str

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    @property
    def byte_length(self) -> int:
        return len(self.content)


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _without_line_end(value: str) -> str:
    return value.rstrip("\r\n")


def _body(markdown: str) -> str:
    if not isinstance(markdown, str):
        raise FragmentError("daily source must be UTF-8 Markdown text")
    if any(marker in markdown for marker in ("\r", "\v", "\f", "\x85", "\u2028", "\u2029")):
        raise FragmentError("daily source must use LF line endings")
    try:
        return parse_frontmatter(markdown).body
    except (FrontmatterError, UnicodeError) as error:
        raise FragmentError(f"daily source frontmatter is invalid: {error}") from error


def _heading_fragment(body: str, locator: str) -> str:
    requested = locator[1:]
    requested_title = requested
    requested_index: int | None = None
    suffix_match = re.fullmatch(r"(.+)@([2-9][0-9]*)", requested)
    if suffix_match:
        requested_title = suffix_match.group(1)
        requested_index = int(suffix_match.group(2))

    matches = list(_HEADING_RE.finditer(body))
    selected: re.Match[str] | None = None
    occurrence = 0
    for match in matches:
        title = _nfc(match.group("title").strip())
        if title != _nfc(requested_title.strip()):
            continue
        occurrence += 1
        if requested_index is None and occurrence == 1:
            selected = match
            break
        if requested_index == occurrence:
            selected = match
            break
    if selected is None:
        raise FragmentError(f"daily heading locator does not exist: {locator}")

    level = len(selected.group("marks"))
    end = len(body)
    for match in matches:
        if match.start() <= selected.start():
            continue
        if len(match.group("marks")) <= level:
            end = match.start()
            break
    value = body[selected.start() : end].strip()
    if not value:
        raise FragmentError(f"daily heading locator resolves to empty text: {locator}")
    return value


def _block_fragment(body: str, locator: str) -> str:
    marker = locator[1:]
    for line in body.splitlines(keepends=True):
        if _without_line_end(line).strip() == marker:
            return _without_line_end(line).strip()
    raise FragmentError(f"daily block locator does not exist: {locator}")


def _line_fragment(body: str, locator: str) -> str:
    match = _LINE_LOCATOR_RE.fullmatch(locator)
    if match is None:
        raise FragmentError(f"invalid daily line locator: {locator}")
    start = int(match.group("start"))
    end = int(match.group("end") or start)
    if end < start:
        raise FragmentError("daily line locator end precedes start")
    lines = body.splitlines()
    if start > len(lines) or end > len(lines):
        raise FragmentError(f"daily line locator is outside the source: {locator}")
    value = "\n".join(line.strip() for line in lines[start - 1 : end]).strip()
    if not value:
        raise FragmentError(f"daily line locator resolves to empty text: {locator}")
    return value


def resolve_daily_fragment(markdown: str, locator: str) -> DailyFragment:
    """Resolve one locator against current daily Markdown bytes."""

    if not isinstance(locator, str) or not locator or len(locator) > 500:
        raise FragmentError("daily fragment locator must be bounded non-empty text")
    if any(marker in locator for marker in ("\x00", "\r", "\n")):
        raise FragmentError("daily fragment locator contains a forbidden character")
    body = _body(markdown)
    if _BLOCK_LOCATOR_RE.fullmatch(locator):
        value = _block_fragment(body, locator)
    elif _HEADING_LOCATOR_RE.fullmatch(locator):
        value = _heading_fragment(body, locator)
    elif locator == "/body":
        value = body.strip()
    elif locator == "/body/preamble":
        match = _HEADING_RE.search(body)
        value = (body if match is None else body[: match.start()]).strip()
    elif _LINE_LOCATOR_RE.fullmatch(locator):
        value = _line_fragment(body, locator)
    else:
        raise FragmentError(f"unsupported daily fragment locator: {locator}")
    if not value:
        raise FragmentError(f"daily fragment locator resolves to empty text: {locator}")
    return DailyFragment(locator=locator, text=value)


def daily_fragment_bytes(markdown: str, locator: str) -> bytes:
    """Return the canonical bytes selected by a daily locator."""

    return resolve_daily_fragment(markdown, locator).content


def verify_daily_fragment(markdown: str, locator: str, expected_sha256: str) -> DailyFragment:
    """Resolve and verify a daily fragment against a lowercase SHA-256."""

    if not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(expected_sha256):
        raise FragmentError("fragment_sha256 must be lowercase SHA-256")
    fragment = resolve_daily_fragment(markdown, locator)
    if fragment.sha256 != expected_sha256:
        raise FragmentError("daily fragment digest does not match the resolved source bytes")
    return fragment


__all__ = [
    "DailyFragment",
    "FragmentError",
    "daily_fragment_bytes",
    "resolve_daily_fragment",
    "verify_daily_fragment",
]
