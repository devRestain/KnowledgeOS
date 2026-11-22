"""System One: fast closed-set judgments through the OpenAI Decisions API (Blueprint §4.2).

System One is an Owner service, not a role: Owner code asks it typed questions (``choice`` or
``predicate``) about one input and acts only on answers that are in the offered set and reach
the configured confidence. It never writes, never sees a note whose ``ai_policy`` is not
``approve`` (callers check ``policy.approved``) and never decides privacy fields. A call made for a Work or request
(``ref``) is recorded in the ``system_one`` State stream with question names, an input digest
and token usage, never content; a read-surface call (no ``ref``) writes no State and is only
logged. A failed call is reported to the caller and never retried.

Settings (``settings.system_one``): ``api_key_file`` (absolute path of a private 0600 file;
absent means unconfigured), ``url`` (default the OpenAI Decisions endpoint; ``http`` only on
loopback), ``model`` (default ``gpt-6-luna``), ``timeout_seconds`` (20), ``min_confidence``
(0.7) and ``max_input_chars`` (8000).
"""

from __future__ import annotations

import http.client
import json
import logging
import stat
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import LOOPBACK_HOSTS, ConfigError
from .notes import Note
from operationkit.canonical import canonical, sha256

log = logging.getLogger("knowledgeos.system_one")

DEFAULT_URL = "https://api.openai.com/v1/decisions"
DEFAULT_MODEL = "gpt-6-luna"
NONE_OF_THESE = "none_of_these"


class SystemOneUnavailable(Exception):
    """System One is unconfigured, unreachable or answered with an error."""


@dataclass(frozen=True)
class Settings:
    api_key_file: Path
    url: str = DEFAULT_URL
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 20.0
    min_confidence: float = 0.7
    max_input_chars: int = 8000


def build(values: dict[str, Any], record: Callable[[dict[str, Any]], None]) -> SystemOne | None:
    """System One from ``settings.system_one``, or None when no key file is configured."""
    key_file = values.get("api_key_file")
    if not key_file:
        return None
    if not isinstance(key_file, str) or not Path(key_file).is_absolute():
        raise ConfigError("system_one api_key_file must be an absolute path")
    settings = Settings(
        Path(key_file),
        url=values.get("url", DEFAULT_URL),
        model=values.get("model", DEFAULT_MODEL),
        timeout_seconds=values.get("timeout_seconds", 20.0),
        min_confidence=values.get("min_confidence", 0.7),
        max_input_chars=values.get("max_input_chars", 8000),
    )
    parts = urlsplit(settings.url) if isinstance(settings.url, str) else None
    if parts is None or not (parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS)):
        raise ConfigError(f"system_one url must be https (or http on loopback), not {settings.url!r}")
    if not isinstance(settings.model, str) or not settings.model:
        raise ConfigError("system_one model must be a non-empty string")
    if not _number(settings.timeout_seconds) or settings.timeout_seconds <= 0:
        raise ConfigError("system_one timeout_seconds must be positive")
    if not _number(settings.min_confidence) or not 0 < settings.min_confidence <= 1:
        raise ConfigError("system_one min_confidence must be in (0, 1]")
    if not isinstance(settings.max_input_chars, int) or isinstance(settings.max_input_chars, bool) \
            or settings.max_input_chars < 200:
        raise ConfigError("system_one max_input_chars must be an integer of at least 200")
    return SystemOne(settings, record)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def choice(name: str, instructions: str, choices: dict[str, str]) -> dict[str, Any]:
    """A choice question over ``{value: description}`` plus the ``none_of_these`` fallback."""
    options = [{"value": value, "description": text} for value, text in choices.items()]
    options.append({"value": NONE_OF_THESE, "description": "None of the other options clearly applies."})
    return {"type": "choice", "name": name, "instructions": instructions, "choices": options}


def predicate(name: str, instructions: str) -> dict[str, Any]:
    return {"type": "predicate", "name": name, "instructions": instructions}


def note_input(note: Note, max_chars: int, label: str = "note") -> list[dict[str, Any]]:
    """One user message carrying a note as JSON: path, title, frontmatter and a body excerpt."""
    return [{"role": "user", "content": [
        {"type": "input_text", "text": f"The {label} is a Markdown note from the user's Obsidian Vault."},
        {"type": "input_json", "data": note_json(note, max_chars)},
    ]}]


def note_json(note: Note, max_chars: int) -> dict[str, Any]:
    frontmatter = json.loads(json.dumps(note.data if note.valid else {}, ensure_ascii=False, default=str))
    return {"path": note.path, "title": note.title, "frontmatter": frontmatter, "body": note.body[:max_chars]}


class SystemOne:
    def __init__(self, settings: Settings, record: Callable[[dict[str, Any]], None]):
        self.settings = settings
        self.min_confidence = float(settings.min_confidence)
        self.max_input_chars = settings.max_input_chars
        self._record = record
        self._opener = urllib.request.build_opener()

    def status(self) -> str:
        """``ready`` when the key file is usable; no request is sent."""
        try:
            self._key()
        except SystemOneUnavailable:
            return "unavailable"
        return "ready"

    def _key(self) -> str:
        path = self.settings.api_key_file
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise SystemOneUnavailable("the API key file must be a private 0600 regular file")
            key = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as error:
            raise SystemOneUnavailable(f"the API key file is unreadable ({type(error).__name__})") from error
        if not key or any(char.isspace() for char in key):
            raise SystemOneUnavailable("the API key file must contain one key")
        return key

    def decide(self, input: Any, questions: list[dict[str, Any]], *, purpose: str, ref: str | None = None,
               ) -> dict[str, dict[str, Any]]:
        """Ask questions about one input; answers by question name. Refusals are kept as
        ``{"type": "refusal"}``. Raises ``SystemOneUnavailable``; never retried."""
        entry: dict[str, Any] = {"purpose": purpose, "ref": ref, "model": self.settings.model,
                                 "questions": [question["name"] for question in questions],
                                 "input_sha": sha256(canonical(input))}
        try:
            answers, usage = self._post(input, questions)
        except SystemOneUnavailable as error:
            self._note({**entry, "outcome": "failed", "error": str(error)})
            raise
        self._note({**entry, "outcome": "answered", "usage": usage})
        return answers

    def _note(self, entry: dict[str, Any]) -> None:
        if entry["ref"] is None:
            log.info("system_one %s %s: %s", entry["purpose"], entry["questions"], entry["outcome"])
        else:
            self._record(entry)

    def _post(self, input: Any, questions: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], Any]:
        body = json.dumps({"model": self.settings.model, "input": input, "questions": questions},
                          ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.settings.url, data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self._key()}"})
        try:
            with self._opener.open(request, timeout=float(self.settings.timeout_seconds)) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise SystemOneUnavailable(f"Decisions API answered HTTP {error.code}{_detail(error)}") from error
        except (OSError, http.client.HTTPException) as error:
            reason = getattr(error, "reason", None) or error
            raise SystemOneUnavailable(f"Decisions API unreachable: {type(reason).__name__}") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise SystemOneUnavailable("Decisions API sent an invalid response") from error
        rows = payload.get("answers") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or len(rows) != len(questions):
            raise SystemOneUnavailable("Decisions API sent malformed answers")
        answers: dict[str, dict[str, Any]] = {}
        for question, row in zip(questions, rows, strict=True):
            if not isinstance(row, dict) or row.get("name") != question["name"] \
                    or row.get("type") not in (question["type"], "refusal"):
                raise SystemOneUnavailable("Decisions API sent malformed answers")
            answers[question["name"]] = row
        return answers, payload.get("usage")

    def accepted(self, answer: dict[str, Any] | None, question: dict[str, Any]) -> tuple[str | None, str]:
        """The chosen value when it is offered, not ``none_of_these`` and confident enough;
        otherwise None and the reason (``refused``, ``none_of_these`` or ``low_confidence``)."""
        if not answer or answer.get("type") != "choice":
            return None, "refused"
        value = answer.get("choice")
        if value not in {option["value"] for option in question["choices"]}:
            return None, "refused"
        if value == NONE_OF_THESE:
            return None, NONE_OF_THESE
        confidence = answer.get("confidence")
        if not _number(confidence) or confidence < self.min_confidence:
            return None, "low_confidence"
        return value, "accepted"

    def holds(self, answer: dict[str, Any] | None, threshold: float | None = None) -> bool | None:
        """A predicate's verdict at ``threshold`` (default ``min_confidence``); None on refusal."""
        if not answer or answer.get("type") != "predicate" or not _number(answer.get("probability")):
            return None
        return answer["probability"] >= (self.min_confidence if threshold is None else threshold)


def _detail(error: urllib.error.HTTPError) -> str:
    try:
        message = json.loads(error.read(2000)).get("error")
        if isinstance(message, dict):
            message = message.get("message")
    except (OSError, ValueError, AttributeError):
        return ""
    return f": {message[:200]}" if isinstance(message, str) and message else ""
