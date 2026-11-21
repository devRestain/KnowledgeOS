"""Role tool ``system_one``: a model teammate asks System One one closed-set question about a
note it read in this Work (CAPABILITIES, System One section).

``question`` with ``choices`` is a ``choice`` over those values plus ``none_of_these``; without
``choices`` it is a ``predicate``. The note version the Work disclosed is sent only when that
version and the current file are both ``approve``. Each step may send at most
``STEP_LIMIT`` requests; the answer is advisory and Owner code never writes it anywhere.
"""

from __future__ import annotations

from typing import Any

from .notes import parse
from .policy import approved, effective, strictest
from .system_one import SystemOneUnavailable, choice, note_input, predicate

STEP_LIMIT = 10
ADVISORY = "System One answer; advisory only. Decide from the notes you read."
MAX_QUESTION = 300
MAX_CHOICE = 100
MAX_CHOICES = 10


def _problem(question: Any, choices: Any) -> str | None:
    if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION:
        return f"question must be 1-{MAX_QUESTION} characters"
    if choices is None:
        return None
    if (not isinstance(choices, list) or not 2 <= len(choices) <= MAX_CHOICES
            or not all(isinstance(c, str) and c.strip() and len(c) <= MAX_CHOICE and c != "none_of_these"
                       for c in choices)
            or len(set(choices)) != len(choices)):
        return f"choices must be 2-{MAX_CHOICES} distinct values of 1-{MAX_CHOICE} characters"
    return None


def reserve(ctx, by: str) -> bool:
    """Count one request against step ``by``; False when the step reached ``STEP_LIMIT``."""
    granted = []

    def count(work: dict[str, Any]) -> None:
        used = work.setdefault("system_one_calls", {})
        granted.append(used.get(by, 0) < STEP_LIMIT)
        if granted[0]:
            used[by] = used.get(by, 0) + 1

    ctx.owner.update_work(ctx.work_id, count)
    return granted[0]


def ask(ctx, by: str, handle: Any = None, question: Any = None, choices: Any = None) -> dict[str, Any]:
    entry = ctx.evidence.get(handle) if isinstance(handle, str) else None
    if entry is None or not entry["read"]:
        return {"handle": handle, "status": "not_read", "reason": "handle must name a note read in this Work"}
    if problem := _problem(question, choices):
        return {"handle": handle, "status": "invalid", "reason": problem}
    service = ctx.owner.system_one
    if service is None:
        return {"handle": handle, "status": "unconfigured"}
    current = ctx.owner.vault.read(entry["path"])
    if current is None or not approved(strictest([effective(current), entry["policy"]])):
        return {"handle": handle, "status": "denied", "reason": "the note's ai_policy is not approve"}
    if not reserve(ctx, by):
        return {"handle": handle, "status": "limit_reached", "reason": f"at most {STEP_LIMIT} requests per step"}
    data = ctx.owner.store.get_blob(entry["blob"])
    if data is None:
        return {"handle": handle, "status": "unavailable"}
    asked = (choice("answer", question, {value: value for value in choices}) if choices is not None
             else predicate("answer", question))
    try:
        answers = service.decide(note_input(parse(entry["path"], data), service.max_input_chars), [asked],
                                 purpose=f"{ctx.capability}.role_question", ref=ctx.work_id)
    except SystemOneUnavailable:
        return {"handle": handle, "status": "unavailable"}
    answer = answers.get("answer")
    if choices is not None:
        value, reason = service.accepted(answer, asked)
        return {"handle": handle, "status": "answered", "advisory": ADVISORY, "choice": value, "reason": reason,
                "confidence": (answer or {}).get("confidence")}
    holds = service.holds(answer)
    return {"handle": handle, "status": "answered", "advisory": ADVISORY, "holds": holds,
            "probability": (answer or {}).get("probability") if holds is not None else None}
