"""The Owner: admission, State, snapshots, effects, notices and read APIs (Blueprint §4.1, §6).

``Owner.submit`` commits an idempotent receipt and answers at once; a worker thread then
runs the static Director (``Director.admit``), creates the Work with its snapshotted
inputs and hands it to the single engine. Capability behavior comes from the modules in
``knowledgeos.capabilities``; declarations come from the catalog.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import re
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from typing import Any, NoReturn

from . import core_records, roles, semantic, system_one
from .capabilities import Done, Refused, WorkDraft, build_registry
from .catalog import Catalog
from .config import OwnerConfig
from .engine import Engine
from .policy import effective, strictest
from .roles import RoleRunner
from .search import Doc, SearchIndex, role_filter
from .store import NAME_RE, Store
from .util import Clock, digest, iso, new_id
from .vault import Vault, VaultPathError

log = logging.getLogger("knowledgeos.owner")

RECEIPT_FIELDS = ("request_id", "command", "actor", "received_at", "status", "work_id", "outcome", "notice")
TERMINAL = ("completed", "unmet", "failed", "refused", "cancelled", "unknown")
ACTOR_RE = re.compile(r"^(user/local|schedule/[a-z][a-z0-9_-]{0,63}|operation/[a-z][a-z0-9_.-]{0,63})$")
ORIGINS = {"user": "user", "schedule": "schedule", "operation": "gateway"}
PARAM_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "boolean": bool,
    "integer": int,
    "array": list,
    "object": dict,
}
TARGET_PROBLEMS = {
    "deny": "ai_policy deny",
    "conflict": "merge-conflict markers",
    "invalid_yaml": "unparseable YAML frontmatter",
    "duplicate_id": "id shared with another note",
    "ignored": "not a note KnowledgeOS processes",
}
# Free text (Blueprint §4.2): fields only the catalog sets, params only the Owner computes,
# and params that name notes (the model passes target refs from ``catalog_view``).
DIRECTOR_AUTHORITY = ("criteria", "budget", "evaluation", "target", "target_kind", "workspec", "inputs", "route",
                      "model_route", "mode_criteria")
OWNER_PARAMS = ("digest", "digests")
TARGET_PARAMS = ("path", "paths")
MAX_TARGETS = 20
LINK_RE = re.compile(r"\[\[([^\]|#]+)")


class BadRequest(ValueError):
    """A malformed request envelope (HTTP 400); no receipt is created."""


class Conflict(Exception):
    """Same request ID with different content (HTTP 409)."""


def origin_of(actor: str) -> str:
    return ORIGINS[actor.split("/", 1)[0]]


@functools.cache
def implementation_identity() -> str:
    """Digest of this package's source, computed once per process (Blueprint §8.2)."""
    package = Path(__file__).resolve().parent
    hasher = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        hasher.update(path.relative_to(package).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return hasher.hexdigest()[:16]


def validate_params(schema: dict[str, dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    """Check params against the catalog command's ``params`` declaration."""
    unknown = sorted(set(params) - set(schema))
    if unknown:
        raise Refused(f"unknown parameter: {', '.join(unknown)}")
    checked: dict[str, Any] = {}
    for name, rule in schema.items():
        if name not in params:
            if rule.get("required"):
                raise Refused(f"missing parameter: {name}")
            if "default" in rule:
                checked[name] = rule["default"]
            continue
        value = params[name]
        expected = PARAM_TYPES[rule["type"]]
        if not isinstance(value, expected) or (rule["type"] == "integer" and isinstance(value, bool)):
            raise Refused(f"parameter {name} must be {rule['type']}")
        if "enum" in rule and value not in rule["enum"]:
            raise Refused(f"parameter {name} must be one of {', '.join(map(str, rule['enum']))}")
        if isinstance(value, (list, str)) and (len(value) < rule.get("min", 0) or len(value) > rule.get("max", 10_000)):
            raise Refused(f"parameter {name} has an invalid length")
        if (rule["type"] == "array" and "items" in rule
                and any(not isinstance(item, PARAM_TYPES[rule["items"]]) for item in value)):
            raise Refused(f"parameter {name} items must be {rule['items']}")
        if (rule["type"] == "object" and "values" in rule
                and any(not isinstance(item, PARAM_TYPES[rule["values"]]) for item in value.values())):
            raise Refused(f"parameter {name} values must be {rule['values']}")
        checked[name] = value
    return checked


class Admission:
    """The static Director's view of one structured request, passed to command ``admit``."""

    def __init__(
        self,
        owner: Owner,
        record: dict[str, Any],
        capability: str,
        declaration: dict[str, Any],
        params: dict[str, Any],
    ):
        self.owner = owner
        self.request_id: str = record["request_id"]
        self.command: str = record["command"]
        self.actor: str = record["actor"]
        self.origin = origin_of(self.actor)
        self.capability = capability
        self.declaration = declaration
        self.params = params

    def refuse(self, reason: str) -> NoReturn:
        raise Refused(reason)

    def setting(self, key: str, default: Any = None) -> Any:
        return self.owner.setting(self.capability, key, default)

    def work(self, work_id: Any) -> dict[str, Any]:
        if not isinstance(work_id, str) or not NAME_RE.match(work_id):
            self.refuse("invalid Work ID")
        work = self.owner.get_work(work_id)
        if work is None:
            self.refuse(f"no Work {work_id}")
        return work

    def resolve(
        self,
        paths: Iterable[str],
        digests: dict[str, str] | None = None,
        *,
        strict: bool = True,
        exclude: Callable[[str], str | None] | None = None,
    ) -> tuple[list[str], list[dict[str, str]]]:
        """Resolve note paths, folders (``"."`` is the whole Vault) and ``id:<id>`` targets.

        Missing, invalid and ambiguous-ID targets and digest mismatches are refused. Notes
        with a problem (deny, conflict markers, invalid YAML, duplicate ID) are refused when
        ``strict`` and otherwise returned in ``skipped``. ``exclude(path)`` names
        capability-specific path exclusions: silently dropped inside folders, skipped when
        named explicitly.
        """
        docs = self.owner.index.refresh()
        duplicate_ids = set(self.owner.index.duplicate_ids(docs))
        digests = digests or {}
        targets: list[str] = []
        skipped: list[dict[str, str]] = []
        for raw in paths:
            if not isinstance(raw, str) or not raw.strip():
                self.refuse("empty target")
            folder = False
            if raw.startswith("id:"):
                matches = sorted(p for p, d in docs.items() if d.policy != "deny" and d.note_id == raw[3:])
                if not matches:
                    self.refuse(f"no note with id {raw[3:]}")
                if len(matches) > 1:
                    self.refuse(f"id {raw[3:]} is ambiguous: {len(matches)} notes share it")
                candidates = matches
            elif raw.strip() == ".":
                candidates, folder = sorted(docs), True
            else:
                try:
                    rel = self.owner.vault.normalize(raw.strip().rstrip("/"))
                    location = self.owner.vault.abs(rel)
                except VaultPathError:
                    self.refuse(f"invalid or excluded path: {raw}")
                if location.is_dir():
                    candidates, folder = sorted(p for p in docs if p.startswith(rel + "/")), True
                elif rel in docs:
                    candidates = [rel]
                elif location.is_file():
                    if strict:
                        self.refuse(f"{rel}: {TARGET_PROBLEMS['ignored']}")
                    skipped.append({"path": rel, "reason": "ignored"})
                    continue
                else:
                    self.refuse(f"not found: {rel}")
            for path in candidates:
                doc = docs[path]
                reason = exclude(path) if exclude else None
                if reason and folder:
                    continue
                if not folder and doc.policy != "deny" and path in digests and digests[path] != doc.sha:
                    self.refuse(f"{path} changed after the client read it; run the command again")
                reason = reason or self.problem(doc, duplicate_ids)
                if reason:
                    if strict:
                        self.refuse(f"{path}: {TARGET_PROBLEMS.get(reason, reason)}")
                    if not any(item["path"] == path for item in skipped):
                        skipped.append({"path": path, "reason": reason})
                elif path not in targets:
                    targets.append(path)
        return targets, skipped

    @staticmethod
    def problem(doc: Doc, duplicate_ids: set[str]) -> str | None:
        """The generic target problem for an indexed note, or None."""
        if doc.policy == "deny":
            return "deny"
        if doc.conflict:
            return "conflict"
        if not doc.valid:
            return "invalid_yaml"
        if doc.note_id in duplicate_ids:
            return "duplicate_id"
        return None


def derived_request_id(request_id: str) -> str:
    """The deterministic ID of the structured request a free-text request was mapped to."""
    return "freetext-" + digest({"request": request_id})[:32]


class Director:
    """Admission (Blueprint §4.2). Structured requests are admitted by static code. Free text
    goes to the ``kos-director`` model, which only drafts one structured request; that draft
    passes the same static admission, and the Owner admits it as a derived request."""

    def __init__(self, owner: Owner):
        self.owner = owner

    def admit(self, record: dict[str, Any]) -> WorkDraft | Done:
        owner = self.owner
        declared = owner.catalog.command(record["command"])
        command = owner.registry.commands.get(record["command"])
        if declared is None or command is None:
            raise Refused(f"unknown command {record['command']}")
        capability, declaration = declared
        origin = origin_of(record["actor"])
        if origin not in declaration.get("origins", ["user"]):
            raise Refused(f"{record['command']} is not available to {record['actor']}")
        if origin == "schedule" and record["actor"].split("/", 1)[1] not in owner.catalog.jobs():
            raise Refused(f"{record['actor']} is not a declared job")
        params = validate_params(declaration.get("params", {}), record["params"])
        return command.admit(Admission(owner, record, capability, declaration, params))

    # ------------------------------------------------------------ free text

    def free_text(self, adm: Admission) -> Done:
        """``free_text {text}``: the model submits ``{command, params}`` or ``{refuse}``.

        When System One is configured it first judges, from the request text alone, whether any
        offered command could apply; a confident ``none_of_these`` refuses at once with no
        Director session. Every other answer (and a failure) leaves the Director path unchanged.
        A valid draft is admitted as the derived request ``derived_request_id(request_id)``,
        processed in this worker, and this receipt settles with its outcome. A refusal, a
        draft still invalid after the corrective turn, or an unavailable route admits no Work.
        """
        owner = self.owner
        text = adm.params["text"].strip()
        if not text:
            adm.refuse("the request is empty")
        # The model call may have effects outside the Owner's view: never re-admit after a stop.
        owner._update_receipt(adm.request_id, status="running")
        child_id = derived_request_id(adm.request_id)
        decision, nothing_fits = self._prefilter(adm, text)
        trail = {} if decision is None else {"system_one": decision}
        if nothing_fits:
            return self._unmapped(adm, "refused", "refused", None, trail,
                                  "Free text not admitted: System One found no KnowledgeOS command for this request")
        drafted: dict[str, Any] = {}
        invocation: dict[str, Any] | None = None
        try:
            if owner.runner is None:
                raise roles.RoleUnavailable("no role runner is configured")
            binding = roles.binding(owner.catalog, "director", adm.request_id, "director", "submit_workspec")
            invocation = dict(owner.runner.identity(binding))
            targets = self._targets(text, adm.origin, binding.local)
            view = self._catalog_view(text, adm.origin, targets)
            session = roles.RoleSession(
                binding,
                {"catalog_view": lambda: view, "feedback": lambda: owner.engine._feedback("director")},
                lambda payload: self._draft_problems(adm, child_id, payload, view, targets, drafted),
                context={"invocation": invocation},
            )
            roles.run_session(owner.runner, session)
        except roles.RoleOutcomeUnknown as error:
            unknown_id = owner.add_unknown("role_invocation", {"request_id": adm.request_id, "role": "director"})
            return self._unmapped(adm, "unknown", "unknown", invocation, {**trail, "unknown_id": unknown_id},
                                  f"Director outcome unknown ({error}); not re-run, no Work admitted")
        except roles.RoleUnavailable as error:
            return self._unmapped(adm, "failed", "unavailable", invocation, trail,
                                  f"Free text not admitted: the Director is unavailable ({error}); not retried or rerouted")
        except Exception as error:
            log.exception("director failed for %s", adm.request_id)
            return self._unmapped(adm, "failed", "unavailable", invocation, trail,
                                  f"Free text not admitted: the Director failed ({type(error).__name__}); not retried")
        if not session.accepted:
            reason = f"authority violation: {session.violation}" if session.violation else (
                "; ".join(session.diagnostics) or "no submission was made")
            return self._unmapped(adm, "refused", "invalid", invocation, trail,
                                  f"Free text not admitted: the Director could not form a valid request ({reason})")
        if "refuse" in session.submission:
            return self._unmapped(adm, "refused", "refused", invocation, trail,
                                  f"Free text not admitted: {session.submission['refuse'].strip()}")
        derived = drafted["request"]
        try:
            _, created = owner.submit(derived, actor=adm.actor, process=False)
        except Conflict:
            return self._unmapped(adm, "failed", "invalid", invocation, trail,
                                  f"Free text not admitted: derived request {child_id} exists with other content")
        if created:
            owner._process(child_id)
        child = owner.store.get("requests", child_id) or {}
        status = child.get("status") if child.get("status") in TERMINAL else "unknown"
        self._feedback(adm, "refused" if status == "refused" else "admitted", derived["command"], child.get("work_id"))
        mapped = f"Free text mapped to {derived['command']}"
        return Done(
            status=status,
            outcome={"derived_request_id": child_id, "command": derived["command"], "invocation": invocation,
                     "outcome": child.get("outcome"), **trail},
            notice=f"{mapped}: {child['notice']}" if child.get("notice") else mapped,
            work_id=child.get("work_id"),
        )

    def _prefilter(self, adm: Admission, text: str) -> tuple[dict[str, Any] | str | None, bool]:
        """System One's one closed question: which offered command could this request mean?

        Only the request text is sent. Returns the decision for the receipt (None when System One
        is unconfigured, ``unavailable`` on failure) and whether it confidently found no command."""
        one = self.owner.system_one
        if one is None:
            return None, False
        offered = {}
        for item in self._catalog_view(text, adm.origin, {})["commands"]:
            modes = f" (modes: {', '.join(item['modes'])})" if item["modes"] else ""
            offered[item["command"]] = f"{item['name'] or item['command']}{modes}"
        if not offered:
            return None, False
        question = system_one.choice(
            "free_text_command",
            "The input is a request a user typed into their personal knowledge-note assistant. Choose the one "
            "command that could fulfil it, or none_of_these when the request is not about the user's "
            "Markdown notes (for example weather, email or general chat).",
            offered)
        content = [{"type": "input_text", "text": text[:one.max_input_chars]}]
        try:
            answers = one.decide([{"role": "user", "content": content}], [question],
                                 purpose="free_text", ref=adm.request_id)
        except system_one.SystemOneUnavailable:
            return "unavailable", False
        answer = answers.get(question["name"]) or {}
        _, reason = one.accepted(answer, question)
        confidence = answer.get("confidence") if answer.get("type") == "choice" else None
        decision = {"choice": answer.get("choice") if answer.get("type") == "choice" else None,
                    "confidence": confidence}
        confident = isinstance(confidence, (int, float)) and not isinstance(confidence, bool) \
            and confidence >= one.min_confidence
        return decision, reason == system_one.NONE_OF_THESE and confident

    def _unmapped(self, adm: Admission, status: str, feedback: str, invocation: dict[str, Any] | None,
                  extra: dict[str, Any], notice: str) -> Done:
        """A free-text request that admits no Work; the notice says why."""
        self._feedback(adm, feedback, None, None)
        return Done(status=status, notice=notice,
                    outcome={"derived_request_id": None, "command": None, "invocation": invocation, **extra})

    def _feedback(self, adm: Admission, outcome: str, command: str | None, work_id: str | None) -> None:
        """Director learning signal (C9): admissions and clarification failures, no request text."""
        from .learning import record_feedback

        record_feedback(self.owner, role="director", work_id=work_id or "", kind="admission", outcome=outcome,
                        change={"command": command}, detail={"request_id": adm.request_id})

    def _targets(self, text: str, origin: str, local: bool) -> dict[str, dict[str, Any]]:
        """Notes the request names (title, wikilink or path), as refs ``t1``, ``t2``, ...

        Only notes a model role may read in this origin; never deny, system or proposal notes."""
        index = self.owner.index
        docs = index.refresh()
        allow = role_filter(origin, local)
        folded = text.casefold()
        linked = {index.resolve(match.group(1), docs) for match in LINK_RE.finditer(text)}
        found = []
        for path, doc in sorted(docs.items()):
            if not SearchIndex.visible(doc, "people", allow):
                continue
            title = doc.title.casefold()
            if path in linked or (len(title) >= 2 and title in folded) or path.casefold().removesuffix(".md") in folded:
                found.append(doc)
        return {
            f"t{number}": {"path": doc.path, "sha": doc.sha, "title": doc.title, "type": doc.type, "status": doc.status}
            for number, doc in enumerate(found[:MAX_TARGETS], 1)
        }

    def _catalog_view(self, text: str, origin: str, targets: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Tool ``catalog_view``: offered commands with modes and params, and target refs.
        No note content, paths, IDs or digests."""
        catalog = self.owner.catalog
        commands = []
        for name in self.owner.setting("C11", "free_text_commands", []):
            declared = catalog.command(name)
            if declared is None or name not in self.owner.registry.commands:
                continue
            capability, declaration = declared
            if origin not in declaration.get("origins", ["user"]):
                continue
            params: dict[str, Any] = {}
            for param, rule in declaration.get("params", {}).items():
                if param in OWNER_PARAMS:
                    continue
                shown = {key: rule[key] for key in ("type", "required", "enum", "default", "items", "values", "min", "max")
                         if key in rule}
                if param in TARGET_PARAMS:
                    shown = {"type": rule["type"], "required": bool(rule.get("required")), "values": "target refs"}
                params[param] = shown
            entry = catalog.capability(capability) or {}
            commands.append({"command": name, "capability": capability, "name": entry.get("name"),
                             "modes": sorted(entry.get("modes", {})), "params": params})
        return {
            "request": text,
            "commands": commands,
            "targets": [{"ref": ref, **{key: item[key] for key in ("title", "type", "status")}}
                        for ref, item in targets.items()],
            "submission": {"command": "<command>", "params": {}, "or": {"refuse": "<short reason>"}},
        }

    def _draft_problems(self, adm: Admission, child_id: str, payload: Any, view: dict[str, Any],
                        targets: dict[str, dict[str, Any]], drafted: dict[str, Any]) -> list[str]:
        """Validate a draft through the static admission; a valid one is kept in ``drafted``."""
        if not isinstance(payload, dict):
            return ["the submission must be an object"]
        if authority := sorted(set(payload) & set(DIRECTOR_AUTHORITY)):
            raise roles.AuthorityViolation(f"{', '.join(authority)} come from the catalog, never from the Director")
        if "refuse" in payload:
            reason = payload["refuse"]
            if set(payload) != {"refuse"}:
                return ["refuse takes no other fields"]
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
                return ["refuse needs a short reason (1-500 characters)"]
            return []
        if set(payload) != {"command", "params"}:
            return ["submit exactly {command, params} or {refuse}"]
        command, params = payload["command"], payload["params"]
        if command not in {item["command"] for item in view["commands"]}:
            return [f"command {command!r} is not offered; choose one from catalog_view or refuse"]
        if not isinstance(params, dict):
            return ["params must be an object"]
        if computed := sorted(set(params) & set(OWNER_PARAMS)):
            return [f"{', '.join(computed)} are computed by the Owner; omit them"]
        declaration = self.owner.catalog.command(command)[1].get("params", {})  # type: ignore[index]
        expanded = dict(params)
        named: list[str] = []
        for param in TARGET_PARAMS:
            if param not in params:
                continue
            value = params[param]
            refs = value if isinstance(value, list) else [value]
            unknown = [ref for ref in refs if not isinstance(ref, str) or ref not in targets]
            if unknown:
                return [f"{param} must name target refs from catalog_view, not {unknown!r}"]
            paths = [targets[ref]["path"] for ref in refs]
            expanded[param] = paths if isinstance(value, list) else paths[0]
            named.extend(paths)
        if "digest" in declaration and "path" in expanded and isinstance(expanded["path"], str):
            expanded["digest"] = next(t["sha"] for t in targets.values() if t["path"] == expanded["path"])
        if "digests" in declaration and named:
            expanded["digests"] = {t["path"]: t["sha"] for t in targets.values() if t["path"] in named}
        record = {"request_id": child_id, "command": command, "actor": adm.actor, "params": expanded}
        try:
            self.admit(record)  # dry run: the offered commands' admission has no side effects
        except Refused as refusal:
            reason = refusal.reason
            for ref, item in targets.items():
                reason = reason.replace(item["path"], ref)
            return [f"admission refused: {reason}"]
        drafted["request"] = {"request_id": child_id, "command": command, "params": expanded}
        return []


class Owner:
    def __init__(
        self,
        config: OwnerConfig,
        runner: RoleRunner | None = None,
        clock: Clock | None = None,
        notifier: Callable[[dict[str, Any]], Any] | None = None,
    ):
        self.config = config
        self.runner = runner
        self.clock = clock or Clock()
        self.notifier = notifier
        self.identity = implementation_identity()
        self.store = Store(config.state)
        config.runtime.mkdir(parents=True, exist_ok=True)
        self.vault = Vault(config.vault)
        self.catalog = Catalog(config.root)
        self.registry = build_registry(self.catalog)
        self.index = SearchIndex(self.vault, config.default_policy, observe=self._observe)
        self.index.semantic_backend = semantic.build(config, self.catalog)
        self.system_one = system_one.build(
            config.settings.get("system_one", {}),
            lambda entry: self.store.append("system_one", {"at": iso(self.clock.now()), **entry}),
        )
        self.engine = Engine(self)
        self.director = Director(self)
        self._caches: dict[str, Any] = {}
        self._cache_lock = threading.Lock()
        self._first_seen_lock = threading.Lock()
        self._first_seen: dict[str, str] = {}
        self._shutdown_callbacks: list[Callable[[], None]] = []
        self._quiesce_callbacks: list[Callable[[], None]] = []
        self._admission_lock = threading.RLock()
        self._admission_done = threading.Condition(self._admission_lock)
        self._active_admissions = 0
        self._closing = False
        for entry in self.store.read_stream("first_seen"):
            self._first_seen.setdefault(entry["path"], entry["at"])
        self._persisted_first_seen = set(self._first_seen)
        self._executor = ThreadPoolExecutor(max_workers=config.workers, thread_name_prefix="kos-work")
        try:
            self.engine.start()
            self._recover()
            for start in self.registry.starters:
                start(self)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Drain request workers and capability observers before stopping the runtime."""
        with ExitStack() as cleanup:
            cleanup.callback(self.engine.close)
            for callback in self._shutdown_callbacks:
                cleanup.callback(callback)
            self._shutdown_callbacks.clear()
            with ExitStack() as quiesce:
                for callback in self._quiesce_callbacks:
                    quiesce.callback(callback)
                self._quiesce_callbacks.clear()
            with self._admission_done:
                self._closing = True
                while self._active_admissions:
                    self._admission_done.wait()
                self.engine.prepare_shutdown()
            self._executor.shutdown(wait=True)

    def on_close(self, callback: Callable[[], None]) -> None:
        """Register a capability observer's stop-and-join callback."""
        self._shutdown_callbacks.append(callback)

    def on_quiesce(self, callback: Callable[[], None]) -> None:
        """Register a stop-and-join callback for a source of new requests (the job scheduler);
        ``close`` runs it before refusing new requests and draining workers."""
        self._quiesce_callbacks.append(callback)

    # ------------------------------------------------------------ shared services

    def setting(self, capability: str, key: str, default: Any = None) -> Any:
        override = self.config.settings.get(capability, {})
        if key in override:
            return override[key]
        return self.catalog.settings(capability).get(key, default)

    def cache(self, key: str, factory: Callable[[], Any]) -> Any:
        """A per-Owner object for a capability module (cursors, in-memory views)."""
        with self._cache_lock:
            if key not in self._caches:
                self._caches[key] = factory()
            return self._caches[key]

    def _observe(self, path: str) -> None:
        """Remember observations without making read-only requests write State."""
        with self._first_seen_lock:
            self._first_seen.setdefault(path, iso(self.clock.now()))

    def first_seen(self, path: str) -> str:
        """Commit a first observation only from admitted work that needs it."""
        self._observe(path)
        with self._first_seen_lock:
            moment = self._first_seen[path]
            if path not in self._persisted_first_seen:
                self.store.append("first_seen", {"path": path, "at": moment})
                self._persisted_first_seen.add(path)
            return moment

    def journal(self, work_id: str | None, request_id: str | None = None) -> Callable[[str, dict[str, Any]], None]:
        """An effect journal recorder for ``Vault`` effects: intent before effect, then outcome."""
        current: dict[str, str | None] = {"effect_id": None}

        def record(kind: str, payload: dict[str, Any]) -> None:
            if kind == "intent":
                current["effect_id"] = new_id("effect")
            entry = {
                "kind": kind,
                "effect_id": current["effect_id"],
                "work_id": work_id,
                "request_id": request_id,
                "at": iso(self.clock.now()),
                **payload,
            }
            self.store.append("effects", entry)

        return record

    def record_action(
        self, work_id: str | None, request_id: str | None, action: str, state: str, action_id: str | None = None
    ) -> str:
        """A KnowledgeOS action record: ``received`` first, then applied, not_applied or unknown.

        ``response`` is the Core HumanActionResponse for the submitted action, fixed at receipt;
        acceptance and effect completion stay in ``state`` (owned by this Operation).
        """
        now = iso(self.clock.now())
        if action_id is None:
            action_id = new_id("action")
            work = self.get_work(work_id) if work_id else None
            record = {
                "action_id": action_id,
                "work_id": work_id,
                "request_id": request_id,
                "action": action,
                "state": state,
                "received_at": now,
                "updated_at": now,
                "response": core_records.human_action_response(
                    self, action_id=action_id, action=action, request_id=request_id, work=work, received_at=now
                ),
            }
            self.store.put("actions", action_id, record)
            if work_id:
                self.update_work(work_id, lambda work: work.setdefault("actions", []).append(action_id))
        else:
            self.store.update("actions", action_id, lambda record: record.update(state=state, updated_at=now))
        return action_id

    def add_unknown(self, kind: str, ref: dict[str, Any], observed: str = "unknown") -> str:
        unknown_id = new_id("unknown")
        record = {
            "unknown_id": unknown_id,
            "kind": kind,
            "ref": ref,
            "observed": observed,
            "recorded_at": iso(self.clock.now()),
            "resolution": None,
        }
        self.store.put("unknowns", unknown_id, record)
        return unknown_id

    def post_notice(
        self, kind: str, title: str, links: Iterable[str] = (), item_id: str | None = None,
        *, lines: Iterable[str] = (), counts: dict[str, int] | None = None,
    ) -> dict:
        """Record a notice in the outbox, then deliver it once through the notifier.

        A delivery whose outcome cannot be observed stays ``unknown`` and is never re-sent.
        """
        notice_id = new_id("notice")
        notice = {
            "notice_id": notice_id,
            "kind": kind,
            "title": title,
            "links": list(links),
            "item_id": item_id,
            "lines": list(lines),
            "counts": counts or {},
            "created_at": iso(self.clock.now()),
            "delivery": "pending" if self.notifier else "unconfigured",
        }
        self.store.put("notices", notice_id, notice)
        if self.notifier is None:
            return notice
        self.store.update("notices", notice_id, lambda record: record.update(delivery="delivering"))
        try:
            result = self.notifier(notice)
            state = "delivered" if result in (None, True, "delivered") else "not_delivered" if result == "not_delivered" else "unknown"
        except Exception:
            log.exception("notice %s delivery outcome unknown", notice_id)
            state = "unknown"
        if state == "unknown":
            self.add_unknown("notice", {"notice_id": notice_id})
        return self.store.update("notices", notice_id, lambda record: record.update(delivery=state))

    # ------------------------------------------------------------ Works

    def get_work(self, work_id: str) -> dict[str, Any] | None:
        if not isinstance(work_id, str) or not NAME_RE.match(work_id):
            return None
        return self.store.get("works", work_id)

    def update_work(self, work_id: str, change: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        return self.store.update("works", work_id, change)

    def cancel_work(self, work_id: str) -> str:
        """Console cancellation: ``cancelled`` at once when waiting, else ``cancelling``."""
        return self.engine.cancel(work_id)

    def _snapshot(self, path: str) -> dict[str, Any]:
        note = self.vault.read(path)
        if note is None:
            raise Refused(f"not found: {path}")
        policy = effective(note, self.config.default_policy)
        if policy == "deny":
            raise Refused(f"{path}: ai_policy deny")
        return {
            "path": path,
            "sha": note.sha,
            "blob": self.store.put_blob(note.raw),
            "note_id": note.note_id,
            "policy": policy,
            "title": note.title,
        }

    def _create_work(self, record: dict[str, Any], draft: WorkDraft) -> str:
        capability, declaration = self.catalog.command(record["command"])  # type: ignore[misc]
        entry = self.catalog.capability(capability) or {}
        target = entry.get("target", {})
        inputs = [self._snapshot(path) for path in draft.targets]
        now = iso(self.clock.now())
        workspec = {
            "capability": capability,
            "capability_version": entry["version"],
            "command": record["command"],
            "mode": draft.mode,
            "target_kind": declaration.get("target_kind", target.get("kind", "officer")),
            "target": draft.target or declaration.get("target") or target.get("name"),
            "criteria": self.catalog.criteria(capability, draft.mode),
            "budget": self.catalog.budget(capability, draft.mode),
            "evaluation": entry.get("evaluation", {"officer": "EvalOfficer", "runtime": "static"}),
            "origin": origin_of(record["actor"]),
            "policy": strictest(item["policy"] for item in inputs),
            "inputs": inputs,
            "skipped": draft.skipped,
            "params": draft.params,
        }
        work_id = new_id("work")
        work = {
            "work_id": work_id,
            "request_id": record["request_id"],
            "actor": record["actor"],
            "status": "admitted",
            "created_at": now,
            "updated_at": now,
            "workspec": workspec,
            "segments": [],
            "effects": [],
            "actions": [],
            "remaining_work": None,
            "team_review": None,
            "outcome": None,
            "notice": None,
            "implementation": {"source_revision": self.identity},
        }
        self.store.put("works", work_id, work)
        return work_id

    # ------------------------------------------------------------ requests

    def submit(
        self, request: dict[str, Any], actor: str | None = None, *, process: bool = True
    ) -> tuple[dict[str, Any], bool]:
        """Commit a receipt and start processing. Returns ``(receipt, created)``.

        The same request ID with the same content returns the original receipt; with
        different content it raises ``Conflict``. The content digest includes the
        capability version (Blueprint §6.6). ``process=False`` only commits the receipt: the
        caller (the free-text Director) runs ``_process`` itself in its own worker.
        """
        if not isinstance(request, dict):
            raise BadRequest("request must be a JSON object")
        request_id = request.get("request_id")
        command = request.get("command")
        params = request.get("params", {})
        actor = actor or request.get("actor") or "user/local"
        if not isinstance(request_id, str) or not NAME_RE.match(request_id) or len(request_id) > 128:
            raise BadRequest("request_id must be 1-128 characters [A-Za-z0-9._-]")
        if not isinstance(command, str) or not command:
            raise BadRequest("command must be a string")
        if not isinstance(params, dict):
            raise BadRequest("params must be an object")
        if not isinstance(actor, str) or not ACTOR_RE.match(actor):
            raise BadRequest("invalid actor")
        unknown = set(request) - {"request_id", "command", "params", "actor"}
        if unknown:
            raise BadRequest(f"unknown request fields: {', '.join(sorted(unknown))}")
        declared = self.catalog.command(command)
        capability = declared[0] if declared else None
        content = {
            "command": command,
            "params": params,
            "actor": actor,
            "capability": capability,
            "version": self.catalog.version(capability) if capability else None,
        }
        record = {
            "request_id": request_id,
            "command": command,
            "actor": actor,
            "received_at": iso(self.clock.now()),
            "status": "received",
            "work_id": None,
            "outcome": None,
            "notice": None,
            "params": params,
            "digest": digest(content),
        }
        with self._admission_lock:
            with self.store.lock(f"requests/{request_id}"):
                if self._closing:
                    existing = self.store.get("requests", request_id)
                    if existing is None:
                        raise BadRequest("Owner is shutting down; new requests are not accepted")
                    if existing.get("digest") != record["digest"]:
                        raise Conflict(request_id)
                    return self.public_receipt(existing), False
                if not self.store.create("requests", request_id, record):
                    existing = self.store.get("requests", request_id) or {}
                    if existing.get("digest") != record["digest"]:
                        raise Conflict(request_id)
                    return self.public_receipt(existing), False
            if process:
                self._executor.submit(self._process, request_id)
        return self.public_receipt(record), True

    @staticmethod
    def public_receipt(record: dict[str, Any]) -> dict[str, Any]:
        return {name: record.get(name) for name in RECEIPT_FIELDS}

    def _update_receipt(self, request_id: str, **fields: Any) -> None:
        self.store.update("requests", request_id, lambda record: record.update(fields))

    def _settle(self, request_id: str, done: Done) -> None:
        fields: dict[str, Any] = {
            "status": done.status,
            "outcome": done.outcome,
            "notice": done.notice,
            "settled_at": iso(self.clock.now()),
        }
        if done.work_id:
            fields["work_id"] = done.work_id
        self._update_receipt(request_id, **fields)

    def _process(self, request_id: str) -> None:
        with self._admission_lock:
            record = self.store.get("requests", request_id)
            if self._closing or record is None or record["status"] not in ("received", "admitted", "running"):
                return
            self._active_admissions += 1
        try:
            try:
                try:
                    action = self.engine.reattach_action(request_id)
                    if action is not None:
                        self._settle(request_id, action)
                        return
                    existing_work = self.get_work(record["work_id"]) if record.get("work_id") else None
                    if existing_work is not None and existing_work.get("runtime"):
                        work_id = existing_work["work_id"]
                    elif record["status"] == "received" and not record.get("work_id"):
                        admitted = self.director.admit(record)
                        if isinstance(admitted, Done):
                            self._settle(request_id, admitted)
                            return
                        work_id = self._create_work(record, admitted)
                    else:
                        return
                    self.engine.prepare_work(work_id)
                except Refused as refusal:
                    self._settle(request_id, Done(status="refused", notice=refusal.reason))
                    from .learning import on_refusal

                    on_refusal(self, request_id, refusal.reason)
                    return
                self._update_receipt(request_id, status="admitted", work_id=work_id)
                self._update_receipt(request_id, status="running")
            finally:
                with self._admission_done:
                    self._active_admissions -= 1
                    self._admission_done.notify_all()
            self._settle(request_id, self.engine.run(work_id, request_id, record["actor"]))
        except Exception as error:
            log.exception("request %s failed", request_id)
            self._settle(request_id, Done(status="failed", notice=f"request failed: {error}"))

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        if not NAME_RE.match(request_id or ""):
            return None
        record = self.store.get("requests", request_id)
        return None if record is None else self.public_receipt(record)

    # ------------------------------------------------------------ recovery

    def _recover(self) -> None:
        """Reconcile exact Vault intents and reattach Temporal-bound execution.

        Legacy unbound in-flight records remain unknown; external calls are never replayed.
        """
        open_intents: dict[str, dict[str, Any]] = {}
        for entry in self.store.read_stream("effects"):
            if entry.get("kind") == "intent":
                open_intents[entry.get("effect_id")] = entry
            elif entry.get("kind") == "outcome":
                open_intents.pop(entry.get("effect_id"), None)
        for effect_id, intent in open_intents.items():
            try:
                state = self.vault.observe_effect(intent)
            except Exception:
                log.exception("could not reconcile Vault effect %s", effect_id)
                state = "unknown"
            self.store.append(
                "effects",
                {
                    "kind": "outcome",
                    "effect_id": effect_id,
                    "work_id": intent.get("work_id"),
                    "op": intent.get("op"),
                    "path": intent.get("path"),
                    "state": state,
                    "recovered": True,
                    "at": iso(self.clock.now()),
                },
            )
            if state not in ("applied", "not_applied"):
                self.add_unknown("effect", {"effect_id": effect_id, "path": intent.get("path")}, state)
        restart = []
        for record in self.store.list("requests"):
            if record["status"] == "received" and not record.get("work_id"):
                restart.append(record["request_id"])
            elif record["status"] in ("received", "admitted", "running"):
                work = self.get_work(record["work_id"]) if record.get("work_id") else None
                if work is not None and work.get("runtime"):
                    restart.append(record["request_id"])
                else:
                    self._settle(
                        record["request_id"],
                        Done(status="unknown", notice="the Owner stopped during this request; outcome unknown"),
                    )
                    self.add_unknown("request", {"request_id": record["request_id"], "work_id": record.get("work_id")})
        for work in self.store.list("works"):
            if work["status"] in ("admitted", "running") and not work.get("runtime"):
                self.engine._set_status(work["work_id"], "unknown", notice="the Owner stopped during this Work")
        for request_id in restart:
            self._executor.submit(self._process, request_id)

    # ------------------------------------------------------------ read APIs

    def health(self) -> dict[str, Any]:
        """Core HealthReport fields plus the KnowledgeOS ``dependencies`` extension."""
        blockers = []
        if not self.vault.root.is_dir():
            blockers.append("vault_not_found")
        if not self.store.root.is_dir():
            blockers.append("state_not_found")
        try:
            core_records.pins(self.config.root)
        except core_records.AdoptionError:
            blockers.append("core_adoption_invalid")
        dependencies = {
            "search_semantic": "ready" if self.index.semantic_ready() else "unavailable",
            "system_one": self.system_one.status() if self.system_one else "unconfigured",
            "notifier": "configured" if self.notifier else "unconfigured",
            "hermestrace": "unconfigured",
            "role_runner": "configured" if self.runner else "unconfigured",
        }
        for name, probe in self.registry.dependencies.items():
            try:
                dependencies[name] = probe(self)
            except Exception as error:
                log.exception("dependency %s probe failed", name)
                dependencies[name] = f"error: {error}"
        return {
            "operation_id": self.config.operation_id,
            "instance_id": self.config.instance_id,
            "alive": True,
            "ready": not blockers,
            "healthy": not blockers,
            "blockers": blockers,
            "observed_at": iso(self.clock.now()),
            "source_revision": self.identity,
            "dependencies": dependencies,
        }

    def works(self, limit: int = 200) -> list[dict[str, Any]]:
        summaries = []
        for work in self.store.list("works"):
            spec = work["workspec"]
            summaries.append(
                {
                    "work_id": work["work_id"],
                    "request_id": work["request_id"],
                    "actor": work["actor"],
                    "status": work["status"],
                    "capability": spec["capability"],
                    "command": spec["command"],
                    "mode": spec["mode"],
                    "target": spec["target"],
                    "created_at": work["created_at"],
                    "updated_at": work["updated_at"],
                    "notice": work.get("notice"),
                }
            )
        summaries.sort(key=lambda item: (item["created_at"], item["work_id"]), reverse=True)
        return summaries[:limit]

    def notices(self) -> list[dict[str, Any]]:
        return sorted(self.store.list("notices"), key=lambda item: item["created_at"], reverse=True)

    def unknowns(self) -> list[dict[str, Any]]:
        return sorted(self.store.list("unknowns"), key=lambda item: item["recorded_at"], reverse=True)

    def roles_view(self) -> list[dict[str, Any]]:
        return [{"role": role, **declared} for role, declared in sorted(self.catalog.roles().items())]

    def search(self, body: dict[str, Any]) -> dict[str, Any]:
        """C3 for people (Thin Client, console): read-only, no receipt."""
        mode = body.get("mode")
        if mode not in ("keyword", "semantic", "related"):
            raise BadRequest("mode must be keyword, semantic or related")
        limit = body.get("limit", self.setting("C3", "default_limit", 20))
        maximum = self.setting("C3", "max_limit", 100)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= maximum:
            raise BadRequest("invalid limit")
        if mode == "related":
            path = body.get("path")
            if not isinstance(path, str):
                raise BadRequest("related needs a path")
            try:
                path = self.vault.normalize(path)
            except VaultPathError as error:
                raise BadRequest(str(error)) from error
            return self.index.related(path, limit=limit, system_one=self.system_one, origin="user")
        query = body.get("query")
        if not isinstance(query, str) or not query.strip():
            raise BadRequest(f"{mode} search needs a query")
        if mode == "semantic":
            return self.index.semantic(query, limit=limit)
        return self.index.keyword(query, limit=limit)

    def index_rebuild(self) -> dict[str, Any]:
        return {**self.index.rebuild(), "rebuilt_at": iso(self.clock.now())}

    def index_verify(self) -> dict[str, Any]:
        return {**self.index.verify(), "observed_at": iso(self.clock.now())}

    def vault_check(self) -> dict[str, Any]:
        """Invalid YAML, conflict markers and duplicate IDs (deny notes are never listed)."""
        docs = self.index.refresh()
        visible = {path: doc for path, doc in docs.items() if doc.policy != "deny"}
        return {
            "checked": len(visible),
            "invalid_yaml": sorted(path for path, doc in visible.items() if not doc.valid),
            "conflicts": sorted(path for path, doc in visible.items() if doc.conflict),
            "duplicate_ids": self.index.duplicate_ids(docs),
            "observed_at": iso(self.clock.now()),
        }
