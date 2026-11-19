"""ai_policy evaluation (Blueprint §5.4)."""

from __future__ import annotations

from collections.abc import Iterable

from .notes import Note, raw_policy_hint

ORDER = ("remote_ok", "ask", "local_only", "deny")
DEFAULT_POLICY = "ask"


def strictest(policies: Iterable[str]) -> str:
    values = list(policies) or [DEFAULT_POLICY]
    return max(values, key=ORDER.index)


def effective(note: Note, default: str = DEFAULT_POLICY) -> str:
    """The policy KnowledgeOS applies to a note. Unreadable notes fail closed."""
    if not note.valid:
        return "deny" if raw_policy_hint(note) == "deny" else "local_only"
    value = note.data.get("ai_policy")
    if value is None or (isinstance(value, str) and not value.strip()):
        policy = default
    elif isinstance(value, str) and value.strip().lower() in ORDER:
        policy = value.strip().lower()
    else:
        policy = "local_only"
    sensitivity = note.data.get("sensitivity")
    if isinstance(sensitivity, str) and sensitivity.strip().lower() == "confidential":
        policy = strictest([policy, "local_only"])
    return policy


def declared_looser_than_confidential(note: Note) -> bool:
    """A confidential note whose declared ai_policy is looser than local_only."""
    if not note.valid:
        return False
    sensitivity = note.data.get("sensitivity")
    declared = note.data.get("ai_policy")
    if not (isinstance(sensitivity, str) and sensitivity.strip().lower() == "confidential"):
        return False
    declared_value = declared.strip().lower() if isinstance(declared, str) else DEFAULT_POLICY
    return declared_value in ("remote_ok", "ask")


def model_may_read(policy: str, origin: str, local_route: bool = False) -> bool:
    """Whether a model role may receive a note's content.

    origin is ``user`` for Work the user requested, ``schedule`` for jobs and
    ``gateway`` for Work triggered by another Operation's submission.
    """
    if policy == "deny":
        return False
    if policy == "local_only":
        return local_route
    if policy == "ask":
        return origin == "user"
    return True


def gateway_exportable(policy: str) -> bool:
    return policy == "remote_ok"
