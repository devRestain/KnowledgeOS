"""Model role invocation: bindings, sessions and runners (Blueprint §4.4).

A ``RoleSession`` is bound to one Work and one step. The binding, not model input,
decides which tools exist; only tools granted to the role in ``config/roles.json`` are
callable. A submission is validated by the Owner; a failing submission gets one
corrective turn with diagnostics and a second failure ends the step as failed. A
submission that claims authority the role does not have (``AuthorityViolation``) is
rejected at once, without a corrective turn.

``ScriptedRunner`` drives sessions from per-role Python callables in tests.
``HermesRunner`` exposes the same bound tools over a per-session MCP transport.
"""

from __future__ import annotations

import secrets
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .catalog import Catalog

MAX_SUBMISSIONS = 2


class ToolDenied(PermissionError):
    """The tool is not granted to this role in this Work binding."""


class RoleUnavailable(RuntimeError):
    """The role's profile, model route or credential is unavailable. Never rerouted."""


class RoleOutcomeUnknown(RuntimeError):
    """The invocation's outcome cannot be observed (e.g. a timeout). Never re-run."""


class AuthorityViolation(Exception):
    """Raised by a validator: the submission addresses authority the role does not have."""


@dataclass(frozen=True)
class RoleBinding:
    work_id: str
    step_id: str
    role: str
    profile: str
    model_route: str | None
    tools: tuple[str, ...]
    submission_tool: str | None


def binding(
    catalog: Catalog, role: str, work_id: str, step_id: str, submission: str | None = None
) -> RoleBinding:
    """Bind a declared role to one Work step. ``submission`` picks the submission tool when
    the role declares several (Managers: ``submit_graph`` or ``submit_team_review``)."""
    declared = catalog.role(role)
    if declared is None:
        raise RoleUnavailable(f"role {role} is not declared in config/roles.json")
    submissions = list(declared.get("submissions", ()))
    if submission is None and len(submissions) == 1:
        submission = submissions[0]
    if submission not in submissions:
        raise RoleUnavailable(f"role {role} has no submission tool {submission}")
    return RoleBinding(
        work_id=work_id,
        step_id=step_id,
        role=role,
        profile=declared["profile"],
        model_route=declared.get("model_route"),
        tools=tuple(declared.get("tools", ())),
        submission_tool=submission,
    )


Validator = Callable[[Any], list[str]]


@dataclass
class RoleSession:
    """One role invocation. ``tools`` maps tool names to Owner callables for this Work.

    Only tools granted by the binding are exposed. The binding's submission tool maps to
    ``submit``; ``validate(payload)`` returns diagnostics (empty when valid).
    """

    binding: RoleBinding
    tools: dict[str, Callable[..., Any]]
    validate: Validator
    context: dict[str, Any] = field(default_factory=dict)
    submission: Any = None
    attempts: int = 0
    diagnostics: list[str] = field(default_factory=list)
    corrective: list[str] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    violation: str | None = None

    @property
    def role(self) -> str:
        return self.binding.role

    @property
    def accepted(self) -> bool:
        return self.submission is not None

    @property
    def failed(self) -> bool:
        return not self.accepted and self.attempts >= MAX_SUBMISSIONS

    def tool_names(self) -> list[str]:
        names = [name for name in self.binding.tools if name in self.tools]
        if self.binding.submission_tool and self.binding.submission_tool not in names:
            names.append(self.binding.submission_tool)
        return names

    def call(self, name: str, **args: Any) -> Any:
        """Invoke a granted tool by name (how runners reach the Owner)."""
        self.calls.append({"tool": name})
        if name == self.binding.submission_tool:
            return self.submit(args.get("payload", args))
        if name not in self.binding.tools or name not in self.tools:
            raise ToolDenied(f"{self.role} has no tool {name}")
        return self.tools[name](**args)

    def submit(self, payload: Any) -> dict[str, Any]:
        if self.accepted or self.failed:
            return {"ok": False, "diagnostics": ["the submission for this step is closed"], "final": True}
        self.attempts += 1
        try:
            problems = list(self.validate(payload))
        except AuthorityViolation as violation:  # rejected outright: no corrective turn
            self.violation = str(violation)
            self.attempts = MAX_SUBMISSIONS
            self.diagnostics = [f"authority violation: {violation}"]
            return {"ok": False, "diagnostics": self.diagnostics, "final": True}
        except Exception as error:  # noqa: BLE001 - validator extension boundary; retain failure diagnostics
            problems = [f"invalid submission: {error}"]
        if not problems:
            self.submission = payload
            return {"ok": True}
        self.diagnostics = problems
        return {"ok": False, "diagnostics": problems, "final": self.attempts >= MAX_SUBMISSIONS}


class RoleRunner(Protocol):
    """Runs one role turn: the role calls ``session.call`` until it submits or stops."""

    def invoke(self, session: RoleSession) -> None: ...

    def identity(self, binding: RoleBinding) -> dict[str, Any]:
        """Profile name, model route and skill-set digest, taken once at session start."""
        ...


def run_session(runner: RoleRunner, session: RoleSession) -> RoleSession:
    """One turn plus at most one corrective turn with diagnostics (Blueprint §4.3)."""
    runner.invoke(session)
    if not session.accepted and not session.failed:
        session.corrective = session.diagnostics or ["no submission was made"]
        runner.invoke(session)
        if not session.accepted and session.attempts < MAX_SUBMISSIONS:
            session.attempts = MAX_SUBMISSIONS  # stopped again without a valid submission
    return session


Script = Callable[[RoleSession], None]


class ScriptedRunner:
    """Test runner: ``scripts[role](session)`` plays the role by calling session tools.

    A script may be a list of callables, consumed one per invocation (the corrective turn
    is the next invocation). ``invocations`` records what ran, without content.
    """

    def __init__(self, scripts: dict[str, Script | list[Script]] | None = None):
        self.scripts: dict[str, Script | list[Script]] = dict(scripts or {})
        self.invocations: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def script(self, role: str, fn: Script | list[Script]) -> None:
        with self._lock:
            self.scripts[role] = fn

    def identity(self, binding: RoleBinding) -> dict[str, Any]:
        return {"profile": binding.profile, "model_route": binding.model_route, "skill_digest": "scripted"}

    def invoke(self, session: RoleSession) -> None:
        with self._lock:
            script = self.scripts.get(session.role)
            if isinstance(script, list):
                script = script.pop(0) if script else None
            self.invocations.append(
                {
                    "role": session.role,
                    "work_id": session.binding.work_id,
                    "step_id": session.binding.step_id,
                    "submission": session.binding.submission_tool,
                    "corrective": bool(session.corrective),
                }
            )
        if script is None:
            raise RoleUnavailable(f"no scripted response for role {session.role}")
        script(session)

    def invoked(self, role: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [entry for entry in self.invocations if role is None or entry["role"] == role]


DESCRIPTIONS = {
    "system_one": "Ask a fast model one closed question about a note read in this Work (handle). With choices "
                  "it picks one of them; without choices it gives a yes/no probability. The classifier may omit "
                  "question for a target's type/status suggestion. Advisory only; at most 10 requests per step.",
}


def tool_manifest(session: RoleSession) -> list[dict[str, Any]]:
    """Stable schemas: the binding chooses the tool names; the Owner chooses the Work."""
    result = []
    for name in session.tool_names():
        properties: dict[str, Any] = {}
        required = []
        if name.startswith("submit_"):
            properties = {"payload": {"type": "object"}}
            required = ["payload"]
        elif name in ("read", "related", "system_one"):
            properties = {"handle": {"type": "string"}}
            required = ["handle"]
        elif name == "search":
            properties = {"query": {"type": "string"}}
            required = ["query"]
        if name in ("search", "related"):
            properties["limit"] = {"type": "integer", "minimum": 1, "maximum": 20}
        if name == "system_one":
            properties["question"] = {"type": "string", "maxLength": 300}
            properties["choices"] = {"type": "array", "items": {"type": "string", "maxLength": 100},
                                     "minItems": 2, "maxItems": 10}
        result.append({
            "name": name,
            "description": DESCRIPTIONS.get(name, f"{name.replace('_', ' ')} for this bound {session.role} step."),
            "inputSchema": {"type": "object", "properties": properties, "required": required,
                            "additionalProperties": False},
        })
    return result


class HermesRunner:
    """One Hermes ACP process per role step, including its single corrective turn.

    The token table belongs to this Owner's runner; no token, Work identifier or scope
    supplied by a model can change its binding. Tokens expire at the step boundary.
    """

    def __init__(self, host, runtime: Path, owner_url: str = ""):
        self.host = host
        self.runtime = Path(runtime)
        self.owner_url = owner_url
        self._guard = threading.Lock()
        self._sessions: dict[str, RoleSession] = {}
        self._live: dict[int, tuple[Any, str, str, Path]] = {}

    def identity(self, binding: RoleBinding) -> dict[str, Any]:
        from .hermes import skill_digest

        home = self.host.profile_dir(binding.profile)
        if not home.is_dir():
            raise RoleUnavailable(f"profile {binding.profile} is not installed")
        return {"profile": binding.profile, "model_route": binding.model_route,
                "skill_digest": skill_digest(home / "skills")}

    def profile_state(self, profile: str) -> dict[str, Any]:
        from .hermes import profile_state

        return profile_state(self.host, profile)

    def session_for(self, token: str) -> RoleSession | None:
        with self._guard:
            return self._sessions.get(token)

    @staticmethod
    def _client_request(method: str, params: dict[str, Any]) -> dict[str, Any]:
        # No filesystem/terminal capabilities are advertised. Permission is only granted
        # for our session-local MCP server, never for a built-in or another integration.
        if method != "session/request_permission":
            raise LookupError(method)
        call = params.get("toolCall", {})
        title = str(call.get("title", "")).lower()
        if "kos" in title:
            for option in params.get("options", []):
                if option.get("kind") == "allow_once":
                    return {"outcome": {"outcome": "selected", "optionId": option["optionId"]}}
        return {"outcome": {"outcome": "cancelled"}}

    def _open(self, session: RoleSession) -> tuple[Any, str, str, Path]:
        from .hermes import AcpClient, AcpError, AcpLost

        if not self.owner_url:
            raise RoleUnavailable("Owner ExOps URL is not bound")
        token = secrets.token_urlsafe(32)
        workdir = self.runtime / "hermes" / "works" / session.binding.work_id
        workdir.mkdir(parents=True, exist_ok=True)
        cwd = Path(tempfile.mkdtemp(prefix=session.binding.step_id + "-", dir=workdir))
        with self._guard:
            self._sessions[token] = session
        client = None
        try:
            client = AcpClient(self.host.argv("acp", profile=session.binding.profile),
                               cwd=cwd, env=self.host.env(), on_request=self._client_request)
            client.request("initialize", {"protocolVersion": 1, "clientInfo": {"name": "knowledgeos", "version": "1"},
                                         "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                                                "terminal": False}}, self.host.setup_timeout)
            env = [{"name": "KOS_OWNER_URL", "value": self.owner_url},
                   {"name": "KOS_BINDING", "value": token}]
            if self.host.pythonpath:
                env.append({"name": "PYTHONPATH", "value": self.host.pythonpath})
            opened = client.request("session/new", {
                "cwd": str(cwd),
                "mcpServers": [{"name": "kos", "command": self.host.python,
                                "args": ["-m", "knowledgeos.mcp_shim"], "env": env}],
            }, self.host.setup_timeout)
            session_id = opened.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                raise RoleUnavailable("Hermes session/new returned no sessionId")
            return client, session_id, token, cwd
        except (OSError, AcpError, AcpLost, RoleUnavailable) as error:
            with self._guard:
                self._sessions.pop(token, None)
            if client is not None:
                client.kill()
            self._remove_cwd(cwd)
            raise RoleUnavailable(f"{session.binding.profile}: {error}") from error

    @staticmethod
    def _remove_cwd(cwd: Path) -> None:
        # Never delete files left by Hermes: the directory was empty at admission.
        try:
            cwd.rmdir()
            cwd.parent.rmdir()
        except OSError:
            pass

    def _close(self, live: tuple[Any, str, str, Path], *, kill: bool = False) -> None:
        client, _, token, cwd = live
        with self._guard:
            self._sessions.pop(token, None)
        if kill:
            client.kill()
        else:
            client.close()
        self._remove_cwd(cwd)

    def invoke(self, session: RoleSession) -> None:
        from .hermes import AcpError, AcpLost

        with self._guard:
            live = self._live.pop(id(session), None)
        live = live or self._open(session)
        client, session_id, _, _ = live
        keep = False
        try:
            instruction = (
                f"Perform your bound {session.role} step. Read your context/view tool and feedback first. "
                f"Use only the tools offered by this session. Submit through {session.binding.submission_tool}; "
                "final response text is not a submission. Your installed SOUL and COMMON rules apply."
            )
            if session.corrective:
                instruction = "Correct the rejected submission in this same bound step. Diagnostics:\n" + "\n".join(session.corrective)
            try:
                result = client.request("session/prompt", {"sessionId": session_id,
                                        "prompt": [{"type": "text", "text": instruction}]}, self.host.turn_timeout)
            except AcpError as error:
                if error.unknown:
                    raise RoleOutcomeUnknown(str(error)) from error
                raise RoleUnavailable(str(error)) from error
            except AcpLost as error:
                client.notify("session/cancel", {"sessionId": session_id})
                raise RoleOutcomeUnknown(f"{error}; prompt not re-sent") from error
            stop = result.get("stopReason", result.get("stop_reason"))
            if stop != "end_turn":
                raise RoleUnavailable(f"{session.binding.profile} stopped with {stop!r}, not end_turn")
            if client.returncode not in (None, 0):
                raise RoleUnavailable(f"{session.binding.profile} exited {client.returncode}")
            keep = not session.accepted and not session.failed and not session.corrective
        finally:
            if keep:
                with self._guard:
                    self._live[id(session)] = live
            else:
                self._close(live, kill=not keep and not session.accepted)
