"""ai_policy evaluation (Blueprint §5.4): ``approve`` or ``deny``."""

from __future__ import annotations

from collections.abc import Iterable

from .notes import Note, raw_policy_hint

ORDER = ("approve", "deny")
DEFAULT_POLICY = "approve"


def strictest(policies: Iterable[str]) -> str:
    """The strictest policy; a value outside ``ORDER`` (such as a legacy State record) is ``deny``."""
    values = [policy if policy in ORDER else "deny" for policy in policies] or [DEFAULT_POLICY]
    return max(values, key=ORDER.index)


def effective(note: Note) -> str:
    """The policy KnowledgeOS applies to a note. A missing or empty value is the default;
    any value outside ``ORDER`` fails closed to ``deny``. Unparseable frontmatter is read
    from its raw ``ai_policy`` line."""
    value = note.data.get("ai_policy") if note.valid else raw_policy_hint(note)
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_POLICY
    if isinstance(value, str) and value.strip().lower() in ORDER:
        return value.strip().lower()
    return "deny"


def approved(policy: str) -> bool:
    """Whether KnowledgeOS automation, models (Hermes roles and System One) and authorized
    Gateway recipients may use a note's content."""
    return policy == "approve"
