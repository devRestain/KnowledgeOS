"""Shared fixtures: disposable Vaults, a controllable clock and an in-process Owner + ExOps.

Fixtures (function-scoped unless noted, parallel-safe):
- ``clock``: ``FakeClock`` frozen at the current second (Asia/Seoul); ``advance``/``set``.
- ``vault``: ``VaultBuilder`` over ``tmp_path/vault``; ``write(rel, text, age=...)``.
- ``runner``: an empty ``ScriptedRunner``; add role scripts with ``runner.script(role, fn)``.
- ``make_kos(**config)``: starts an Owner and ExOps on an ephemeral loopback port and
  returns a ``Kos`` client; ``runner=`` and ``notifier=`` override the injected services.
  The job scheduler is off unless ``settings={"C7": {"scheduler_enabled": True}}``.
- ``kos``: ``make_kos()`` with defaults.
- ``temporal_settings``: session/xdist-worker-local real dev server address and namespace.

Helpers (import from ``conftest``): ``conforming_note(type, status, body, extra, policy)`` and
Thinking Team (C4) role scripts for the ``runner``: ``enhance_scripts(runner, graph=,
successor=, proposals=)`` installs a satisfied Team; ``manager_script``, ``connector_script``,
``challenger_script``, ``synthesizer_script``, ``evaluator_script(unmet=)`` and
``focus_and_candidates(session)`` let a test replace one role.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import io
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from knowledgeos import cli
from knowledgeos.config import OwnerConfig
from knowledgeos.exops import ExOps
from knowledgeos.owner import TERMINAL, Owner
from knowledgeos.roles import ScriptedRunner
from knowledgeos.util import SEOUL, sha256


class FakeClock:
    """A clock tests control. Starts at the current second so fresh files look fresh."""

    def __init__(self, start: datetime | None = None):
        self._now = start or datetime.now(SEOUL).replace(microsecond=0)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, **delta: float) -> datetime:
        with self._lock:
            self._now += timedelta(**delta)
            return self._now

    def set(self, moment: datetime) -> None:
        with self._lock:
            self._now = moment


class VaultBuilder:
    """Writes a disposable Vault. ``age`` sets the file mtime relative to the clock."""

    def __init__(self, root: Path, clock: FakeClock):
        self.root = root
        self.clock = clock
        root.mkdir(parents=True, exist_ok=True)

    def path(self, rel: str) -> Path:
        return self.root / rel

    def write(self, rel: str, content: str | bytes, age: timedelta | None = None) -> str:
        target = self.path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        target.write_bytes(data)
        if age is not None:
            self.set_age(rel, age)
        return sha256(data)

    def set_age(self, rel: str, age: timedelta) -> None:
        moment = (self.clock.now() - age).timestamp()
        os.utime(self.path(rel), (moment, moment))

    def read(self, rel: str) -> bytes:
        return self.path(rel).read_bytes()

    def text(self, rel: str) -> str:
        return self.read(rel).decode("utf-8")

    def sha(self, rel: str) -> str:
        return sha256(self.read(rel))

    def snapshot(self) -> dict[str, tuple[bytes, int]]:
        """Every file's bytes and mtime, to assert that nothing was written."""
        return {
            path.relative_to(self.root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in sorted(self.root.rglob("*"))
            if path.is_file()
        }


def conforming_note(note_type: str, status: str, body: str = "Body.\n", extra: str = "", policy: str = "approve") -> str:
    """A note whose frontmatter already satisfies standardization."""
    return (
        f"---\nid: {uuid.uuid4()}\ntype: {note_type}\ncreated: 2026-11-01T09:00:00+09:00\n"
        f"status: {status}\nai_policy: {policy}\n{extra}---\n{body}"
    )


# ---------------------------------------------------------------- Thinking Team scripts (C4)

FULL_GRAPH = ("retriever", "connector", "challenger", "synthesizer")


def focus_and_candidates(session) -> tuple[dict[str, Any], list[str]]:
    """``work_context`` plus handles a scripted teammate may cite: the focal note's first
    handle (read through the ``read`` tool) and the retriever's candidates (read by it)."""
    context = session.call("work_context")
    handles: list[str] = []
    focus = context.get("focus")
    if focus and focus["handles"]:
        session.call("read", handle=focus["handles"][0])
        handles.append(focus["handles"][0])
    for handoff in context["handoffs"]:
        if handoff["teammate"] == "retriever":
            for candidate in handoff["handoff"]["candidates"]:
                if candidate["handles"][0] not in handles:
                    handles.append(candidate["handles"][0])
    return context, handles


def manager_script(graph=FULL_GRAPH, successor=("connector", "synthesizer"), proposals=("Consider a counterpoint pass",)):
    """ThinkingManager: ``graph`` first, ``successor`` when RemainingWork exists; TeamReview
    rates every producing teammate 4 and adds ``proposals``."""

    def script(session) -> None:
        view = session.call("team_view")
        if session.binding.submission_tool == "submit_team_review":
            ratings = {name: {"rating": 4, "note": "useful handoff"} for name in view["producing"]}
            session.call("submit_team_review", payload={"ratings": ratings, "proposals": [{"text": p} for p in proposals]})
        else:
            steps = successor if view["remaining_work"] else graph
            session.call("submit_graph", payload={"steps": list(steps), "evaluation": "after_last"})

    return script


def connector_script(related: str = "An earlier note addresses the same subject.", tension: str | None = None):
    def script(session) -> None:
        _, handles = focus_and_candidates(session)
        payload = {"related": [{"text": related, "citations": handles[-1:]}], "connections": [], "tensions": []}
        if tension and len(handles) > 1:
            payload["tensions"] = [{"text": tension, "citations": handles[:2]}]
        session.call("submit_handoff", payload=payload)

    return script


def challenger_script(session) -> None:
    _, handles = focus_and_candidates(session)
    gap = {"text": "The note assumes the tool is always at hand.", "citations": handles[:1]}
    session.call("submit_handoff", payload={"gaps": [gap], "counterpoints": [], "open_questions": []})


def synthesizer_script(core: str = "The note says tools shape how we think.",
                       tension: str = "Two notes pull in different directions."):
    """A valid report: the first section cites the focus, a tension cites two notes when there
    are two, every other section is one ``not_found`` claim."""

    def script(session) -> None:
        context, handles = focus_and_candidates(session)
        ids = context["sections"]
        sections = {sid: [{"text": "The Vault does not cover this.", "label": "not_found", "citations": []}] for sid in ids}
        sections[ids[0]] = [{"text": core, "label": "cited", "citations": handles[:1]}]
        if len(handles) > 1:
            where = "connections_tensions" if "connections_tensions" in sections else ids[1]
            sections[where] = [{"text": tension, "label": "cited", "citations": handles[:2]}]
        session.call("submit_report", payload={"sections": sections})

    return script


def evaluator_script(unmet: dict[str, str] | None = None):
    """EvalOfficer: every semantic criterion satisfied except ``unmet`` ``{criterion: reason}``."""

    def script(session) -> None:
        view = session.call("eval_view")
        judgments = []
        for criterion in view["criteria"]:
            reason = (unmet or {}).get(criterion["id"])
            judgments.append({"criterion": criterion["id"], "target": None, "verdict": "unmet" if reason else "satisfied",
                              "reason": reason or "supported by the cited excerpts"})
        session.call("submit_evaluation", payload={"judgments": judgments})

    return script


def enhance_scripts(runner: ScriptedRunner, *, graph=FULL_GRAPH, successor=("connector", "synthesizer"),
                    proposals=("Consider a counterpoint pass",)) -> None:
    """Scripts for a satisfied Thinking Team Work (C4 ``enhance``); override single roles after."""
    runner.script("thinking_manager", manager_script(graph, successor, proposals))
    runner.script("connector", connector_script())
    runner.script("challenger", challenger_script)
    runner.script("synthesizer", synthesizer_script())
    runner.script("eval_officer", evaluator_script())


class Kos:
    """An in-process Owner + ExOps and an HTTP client for it."""

    def __init__(self, owner: Owner, exops: ExOps, vault: VaultBuilder, clock: FakeClock, runner: Any):
        self.owner = owner
        self.exops = exops
        self.vault = vault
        self.clock = clock
        self.runner = runner
        self.port = exops.port
        self.endpoint = exops.url
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self.owner.close()
            finally:
                self.exops.stop()

    # ------------------------------------------------------------ HTTP

    def raw(
        self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None
    ) -> tuple[int, Any, dict[str, str]]:
        """One HTTP exchange with exact headers (``Host`` may be overridden)."""
        headers = dict(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.putrequest(method, path, skip_host="Host" in headers, skip_accept_encoding=True)
            for name, value in headers.items():
                connection.putheader(name, value)
            if body is not None:
                connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body)
            response = connection.getresponse()
            data = response.read()
            payload = json.loads(data) if data and "json" in response.getheader("Content-Type", "") else data
            return response.status, payload, {name.lower(): value for name, value in response.getheaders()}
        finally:
            connection.close()

    def get(self, path: str) -> tuple[int, Any]:
        status, payload, _ = self.raw("GET", path)
        return status, payload

    def post(self, path: str, body: Any) -> tuple[int, Any]:
        data = json.dumps(body).encode("utf-8")
        status, payload, _ = self.raw("POST", path, data, {"Content-Type": "application/json"})
        return status, payload

    # ------------------------------------------------------------ requests

    def submit(
        self, command: str, params: dict[str, Any], actor: str | None = None, request_id: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        body: dict[str, Any] = {"request_id": request_id or str(uuid.uuid4()), "command": command, "params": params}
        if actor:
            body["actor"] = actor
        return self.post("/api/requests", body)

    def wait(self, request_id: str, timeout: float = 15.0) -> dict[str, Any]:
        """Poll a receipt until it settles."""
        deadline = time.monotonic() + timeout
        while True:
            status, receipt = self.get(f"/api/requests/{request_id}")
            assert status == 200, receipt
            if receipt["status"] in TERMINAL:
                return receipt
            if time.monotonic() > deadline:
                raise AssertionError(f"request {request_id} did not settle: {receipt}")
            time.sleep(0.02)

    def run(self, command: str, params: dict[str, Any], actor: str | None = None) -> dict[str, Any]:
        status, receipt = self.submit(command, params, actor)
        assert status == 202, receipt
        return self.wait(receipt["request_id"])

    def search(self, **body: Any) -> dict[str, Any]:
        status, result = self.post("/api/search", body)
        assert status == 200, result
        return result

    def changes(self) -> list[dict[str, Any]]:
        status, records = self.get("/api/changes")
        assert status == 200, records
        return records

    # ------------------------------------------------------------ CLI

    def cli(self, *args: str) -> tuple[int, str, str]:
        """Run the ``knowledgeos`` CLI in-process against this Owner."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main([*args, "--endpoint", self.endpoint])
        return code, out.getvalue(), err.getvalue()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def vault(tmp_path: Path, clock: FakeClock) -> VaultBuilder:
    return VaultBuilder(tmp_path / "vault", clock)


@pytest.fixture
def runner() -> ScriptedRunner:
    return ScriptedRunner()


@pytest.fixture(scope="session")
def temporal_settings(tmp_path_factory) -> dict[str, str]:
    """Run the image-cached CLI once per xdist worker, never downloading a server."""
    from temporalio.client import Client

    binary = shutil.which("temporal")
    if binary is None:
        pytest.fail("cached Temporal CLI is required on PATH; runtime downloads are forbidden")
    directory = tmp_path_factory.mktemp("temporal")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    settings = {"address": f"127.0.0.1:{port}", "namespace": "default"}
    log_path = directory / "server.log"
    with log_path.open("wb") as logs:
        process = subprocess.Popen(
            [
                binary, "server", "start-dev", "--ip", "127.0.0.1", "--port", str(port),
                "--namespace", settings["namespace"], "--db-filename", str(directory / "temporal.sqlite"),
                "--headless",
            ],
            stdout=logs,
            stderr=subprocess.STDOUT,
        )
        try:
            async def ready() -> None:
                deadline = time.monotonic() + 30
                last_error = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    try:
                        await asyncio.wait_for(
                            Client.connect(settings["address"], namespace=settings["namespace"]), timeout=0.5
                        )
                        return
                    except (TimeoutError, RuntimeError) as error:
                        last_error = error
                        await asyncio.sleep(0.05)
                raise RuntimeError(
                    f"Temporal dev server did not become ready: {last_error}\n"
                    f"{log_path.read_text(errors='replace')}"
                )

            asyncio.run(ready())
            yield settings
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


@pytest.fixture
def make_kos(tmp_path: Path, vault: VaultBuilder, clock: FakeClock, runner: ScriptedRunner, temporal_settings):
    started: list[Kos] = []

    def factory(**overrides: Any) -> Kos:
        role_runner = overrides.pop("runner", runner)
        notifier = overrides.pop("notifier", None)
        capability_settings = dict(overrides.pop("settings", {}))
        capability_settings["runtime"] = {**temporal_settings, **capability_settings.get("runtime", {})}
        capability_settings["C7"] = {"scheduler_enabled": False, **capability_settings.get("C7", {})}
        settings = {
            "vault": vault.root,
            "state": tmp_path / "state",
            "runtime": tmp_path / "runtime",
            "port": 0,
            "settings": capability_settings,
            **overrides,
        }
        owner = Owner(OwnerConfig(**settings), runner=role_runner, clock=clock, notifier=notifier)
        kos = Kos(owner, ExOps(owner, port=0).start(), vault, clock, role_runner)
        started.append(kos)
        return kos

    yield factory
    for kos in reversed(started):
        kos.close()


@pytest.fixture
def kos(make_kos) -> Kos:
    return make_kos()


# ------------------------------------------------------------------ Core schemas


def core_root() -> Path:
    """The pinned AgentFabric Core checkout: ``$KNOWLEDGEOS_CORE_ROOT`` or ``<AgentFabric>/Core``."""
    configured = os.environ.get("KNOWLEDGEOS_CORE_ROOT")
    root = Path(configured) if configured else Path(__file__).resolve().parents[3] / "Core"
    assert (root / "schemas").is_dir(), f"AgentFabric Core schemas not found under {root}"
    return root


_CORE_VALIDATORS: dict[str, Any] = {}


def core_validate(name: str, record: Any) -> None:
    """Validate ``record`` against ``Core/schemas/<name>.schema.json`` (its declared draft, with
    format assertion as the Core serialization profile requires). Raises ``ValidationError``."""
    import jsonschema
    from referencing import Registry, Resource

    validator = _CORE_VALIDATORS.get(name)
    if validator is None:
        schemas = {}
        resources = []
        for path in (core_root() / "schemas").glob("*.schema.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            schemas[path.stem.removesuffix(".schema")] = value
            resource = Resource.from_contents(value)
            resources.extend([(value["$id"], resource), (path.as_uri(), resource)])
        schema = schemas[name]
        cls = jsonschema.validators.validator_for(schema)
        cls.check_schema(schema)
        validator = cls(schema, registry=Registry().with_resources(resources), format_checker=cls.FORMAT_CHECKER)
        _CORE_VALIDATORS[name] = validator
    validator.validate(record)


# ---------------------------------------------------------------- Curation Team scripts (C1)


def curation_manager_script(graph=("scout", "classifier", "linker"), successor=("classifier",),
                            proposals=("Skip linker for captures without candidates",)):
    return manager_script(graph, successor, proposals)


def classifier_script(note_type="idea", status="seed", title=None, tags=(), folder=True):
    """Classify all targets, or only handle-targeted RemainingWork in a successor."""
    def script(session):
        context = session.call("work_context")
        gaps = (context.get("remaining_work") or {}).get("gaps", [])
        targets = {h for gap in gaps for h in gap["targets"]}
        notes = []
        for target in context["targets"]:
            handle = target["target"]
            if targets and not targets.intersection(target["handles"]):
                continue
            session.call("read", handle=handle)
            values = [{"value": tag, "new": tag not in context["tag_vocabulary"]} for tag in tags]
            notes.append({"target": handle, "type": note_type, "status": status,
                          "title": title or target["title"], "tags": values,
                          "folder": target["folders"]["archived" if status == "archived" else note_type],
                          "evidence": [handle]})
        session.call("submit_handoff", payload={"notes": notes})
    return script


def linker_script(property="related", link=True):
    def script(session):
        context = session.call("work_context")
        candidates = {}
        for handoff in context["handoffs"]:
            if handoff["teammate"] == "scout":
                candidates.update({row["target"]: row["candidates"] for row in handoff["handoff"]["targets"]})
        notes = []
        for target in context["targets"]:
            handle = target["target"]
            session.call("read", handle=handle)
            picked = candidates.get(handle, [])
            links = []
            if link and picked:
                other = picked[0]["handles"][0]
                session.call("read", handle=other)
                links = [{"property": property, "to": other, "evidence": [handle, other]}]
            notes.append({"target": handle, "links": links, "attach_to": None})
        session.call("submit_handoff", payload={"notes": notes})
    return script


def organize_scripts(runner, *, graph=("scout", "classifier", "linker"), successor=("classifier",),
                     proposals=("Skip linker for captures without candidates",),
                     note_type="idea", status="seed", title=None, tags=(), link=True):
    runner.script("curation_manager", curation_manager_script(graph, successor, proposals))
    runner.script("classifier", classifier_script(note_type, status, title, tags))
    runner.script("linker", linker_script(link=link))
    runner.script("eval_officer", evaluator_script())


# ------------------------------------------------------------------ System One (Decisions API)


class FakeDecisions:
    """``POST /v1/decisions`` on 127.0.0.1 with the OpenAI Decisions response shape.

    ``rules`` maps a question name to an answer function ``(question, body) -> dict`` (without
    ``name``/``type``); questions without a rule are refused. ``down`` answers HTTP 503.
    ``calls`` logs every accepted request body: what reached System One is what the Owner sent.
    """

    def __init__(self) -> None:
        self.rules: dict[str, Any] = {}
        self.calls: list[dict[str, Any]] = []
        self.keys: list[str] = []
        self.down = False
        self._lock = threading.Lock()
        self._server: Any = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1/decisions"

    def start(self) -> FakeDecisions:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                status, payload = fake.answer(self.path, self.headers.get("Authorization", ""), body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def choose(self, name: str, value: Any, confidence: float = 0.95) -> None:
        """Answer the choice question ``name`` with ``value`` (or ``value(question, body)``)."""
        def rule(question, body):
            picked = value(question, body) if callable(value) else value
            return {"choice": picked, "confidence": confidence,
                    "probabilities": [{"value": option["value"], "probability": confidence if option["value"] == picked
                                       else (1 - confidence) / (len(question["choices"]) - 1)}
                                      for option in question["choices"]]}
        self.rules[name] = rule

    def predict(self, name: str, probability: Any) -> None:
        """Answer predicate questions named ``name`` (or ``name*`` as a prefix) with a probability."""
        self.rules[name] = lambda question, body: {
            "probability": probability(question, body) if callable(probability) else probability}

    def _rule(self, name: str) -> Any:
        if name in self.rules:
            return self.rules[name]
        return next((rule for key, rule in self.rules.items() if key.endswith("*") and name.startswith(key[:-1])), None)

    def answer(self, path: str, authorization: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        with self._lock:
            if path != "/v1/decisions":
                return 404, {"error": {"message": "not found"}}
            if self.down:
                return 503, {"error": {"message": "service unavailable"}}
            self.calls.append(body)
            self.keys.append(authorization)
            answers = []
            for question in body["questions"]:
                rule = self._rule(question["name"])
                if rule is None:
                    answers.append({"type": "refusal", "name": question["name"]})
                else:
                    answers.append({"type": question["type"], "name": question["name"], **rule(question, body)})
            return 200, {"model": body["model"], "answers": answers,
                         "usage": {"input_tokens": 10, "output_tokens": 0, "total_tokens": 10}}

    def questions(self) -> list[str]:
        return [question["name"] for call in self.calls for question in call["questions"]]

    def sent_text(self) -> str:
        """Everything sent, as one string, to assert what was (not) disclosed."""
        return "\n".join(json.dumps(call, ensure_ascii=False) for call in self.calls)


@pytest.fixture
def decisions():
    fake = FakeDecisions().start()
    yield fake
    fake.stop()


def system_one_settings(decisions: FakeDecisions, tmp_path: Path, **values: Any) -> dict[str, Any]:
    """``settings.system_one`` for a fake Decisions server with a private key file."""
    key = tmp_path / "decisions.key"
    if not key.exists():
        key.write_text("sk-test-key\n")
        key.chmod(0o600)
    return {"api_key_file": str(key), "url": decisions.url, "model": "gpt-6-luna", **values}
