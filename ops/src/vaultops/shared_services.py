"""Compatibility import for the single owner application factory."""

from .application.knowledge import KnowledgeApplication, LocalIdentity
from .paths import ResolvedPaths


def KnowledgeServices(roots: ResolvedPaths) -> KnowledgeApplication:
    return KnowledgeApplication.from_roots(roots)


__all__ = ["KnowledgeApplication", "KnowledgeServices", "LocalIdentity"]
