"""Capability modules and the registration API.

Every module ``c<N>_<name>.py`` in this package is discovered automatically. It declares
``CAPABILITY = "C<N>"`` and a ``register(reg)`` function. Declarations (commands with
origins and params, modes, targets, criteria, budgets, settings) live only in the catalog
file ``config/capabilities/C<N>.json``; a module supplies behavior for them: command
admission, Officer steps and finishes, static criteria checks, Team hooks, HTTP routes,
read views, dependency probes and start hooks. There is no other registry.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass, field
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..catalog import Catalog


class Refused(Exception):
    """The Director (or a later admission check) refuses the request; nothing was done."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class WorkDraft:
    """A static Director admission for one Work. The Owner turns it into a WorkSpec.

    ``targets`` are Vault paths whose exact bytes are snapshotted at admission.
    ``skipped`` lists resolved targets excluded with a reason (``{path, reason}``).
    ``target`` overrides the catalog's Officer or Team name.
    """

    mode: str
    targets: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    skipped: list[dict[str, str]] = field(default_factory=list)
    target: str | None = None


@dataclass
class Done:
    """A settled result: the receipt status, outcome and notice for the request.

    ``work_status`` sets the Work's status when it differs from the receipt status, e.g.
    a preview request completes while its Work waits as ``awaiting_confirmation``.
    """

    status: str = "completed"
    outcome: Any = None
    notice: str | None = None
    work_id: str | None = None
    work_status: str | None = None


@dataclass
class Command:
    name: str
    capability: str
    admit: Callable[..., WorkDraft | Done]


@dataclass
class Officer:
    name: str
    capability: str
    step: Callable[..., dict[str, Any]]
    finish: Callable[..., Done]


@dataclass
class Route:
    method: str
    pattern: str
    handler: Callable[..., Any]
    capability: str | None = None


class RegistryError(ValueError):
    pass


class Registry:
    """Collects what capability modules provide. Built once per Owner."""

    def __init__(self) -> None:
        self.capability: str | None = None  # the module currently registering
        self.commands: dict[str, Command] = {}
        self.officers: dict[str, Officer] = {}
        self.teams: dict[str, dict[str, Any]] = {}
        self.checks: dict[str, Callable[..., list[dict[str, Any]]]] = {}
        self.routes: list[Route] = []
        self.views: dict[str, tuple[Callable[..., Any], Callable[..., Any] | None]] = {}
        self.dependencies: dict[str, Callable[..., str]] = {}
        self.starters: list[Callable[..., None]] = []

    def _owner_capability(self) -> str:
        if self.capability is None:
            raise RegistryError("register only from a capability module's register(reg)")
        return self.capability

    def command(self, name: str, admit: Callable[..., WorkDraft | Done]) -> None:
        """``admit(adm: owner.Admission) -> WorkDraft | Done``; raise ``Refused`` to refuse."""
        if name in self.commands:
            raise RegistryError(f"command {name} registered twice")
        self.commands[name] = Command(name, self._owner_capability(), admit)

    def officer(self, name: str, step: Callable[..., dict[str, Any]], finish: Callable[..., Done]) -> None:
        """``step(ctx: engine.StepContext) -> artifact``; ``finish(ctx: engine.EffectContext, artifact) -> Done``."""
        if name in self.officers:
            raise RegistryError(f"officer {name} registered twice")
        self.officers[name] = Officer(name, self._owner_capability(), step, finish)

    def team(self, name: str, **hooks: Any) -> None:
        """Team hooks for the Team flow in ``engine.Engine._run_team`` (teammates, validators, render)."""
        if name in self.teams:
            raise RegistryError(f"team {name} registered twice")
        self.teams[name] = {"capability": self._owner_capability(), **hooks}

    def check(self, criterion: str, fn: Callable[..., list[dict[str, Any]]]) -> None:
        """A static EvalOfficer check: ``fn(ctx: engine.StepContext, artifact) -> [judgment]``.

        A judgment is ``{"criterion", "target", "verdict": "satisfied"|"unmet", "reason"}``.
        """
        if criterion in self.checks:
            raise RegistryError(f"check {criterion} registered twice")
        self.checks[criterion] = fn

    def route(self, method: str, pattern: str, handler: Callable[..., Any]) -> None:
        """An ExOps route. ``pattern`` segments in braces bind params: ``/api/changes/{change_id}``.

        ``handler(owner, req: operationkit.exops.Req)`` returns a JSON payload (200) or ``(status, payload)``.
        """
        self.routes.append(Route(method.upper(), pattern, handler, self._owner_capability()))

    def view(self, name: str, list_fn: Callable[..., Any], get_fn: Callable[..., Any] | None = None) -> None:
        """A read-only view at ``GET /api/<name>`` (``list_fn(owner, query)``) and optionally
        ``GET /api/<name>/<id>`` (``get_fn(owner, id)`` returning None for 404)."""
        if name in self.views:
            raise RegistryError(f"view {name} registered twice")
        self._owner_capability()
        self.views[name] = (list_fn, get_fn)

    def dependency(self, name: str, probe: Callable[..., str]) -> None:
        """A health dependency probe ``probe(owner) -> status string``."""
        self.dependencies[name] = probe

    def on_start(self, fn: Callable[..., None]) -> None:
        """Called with the Owner once it is constructed (after recovery)."""
        self.starters.append(fn)


def discover() -> list[ModuleType]:
    modules = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name):
        if info.ispkg or not info.name.startswith("c") or "_" not in info.name:
            continue
        modules.append(importlib.import_module(f"{__name__}.{info.name}"))
    return modules


def build_registry(catalog: Catalog) -> Registry:
    """Register every discovered module and check it against the catalog."""
    registry = Registry()
    capabilities = catalog.capabilities()
    for module in discover():
        capability = getattr(module, "CAPABILITY", None)
        if capability not in capabilities:
            raise RegistryError(f"{module.__name__}: no catalog file config/capabilities/{capability}.json")
        registry.capability = capability
        module.register(registry)
        registry.capability = None
    for name, command in registry.commands.items():
        declared = catalog.command(name)
        if declared is None or declared[0] != command.capability:
            raise RegistryError(f"command {name} is not declared in {command.capability}.json")
    for capability_id, entry in capabilities.items():
        for name in entry.get("commands", {}):
            if name not in registry.commands:
                raise RegistryError(f"{capability_id}.json declares {name} but no module registers it")
        for criterion, spec in entry.get("criteria", {}).items():
            if spec.get("runtime") == "static" and criterion not in registry.checks:
                raise RegistryError(f"{capability_id}.json static criterion {criterion} has no check")
    return registry
