"""Hermes host adapters (Blueprint §4.4, §4.5; capabilities C8, C9).

- ``HermesHost``: the configured ``hermes`` binary and Hermes home (``settings.hermes``).
- ``AcpClient``: a small ACP client (JSON-RPC 2.0, one JSON message per line over stdio) for
  ``hermes -p <profile> acp``. ``roles.HermesRunner`` drives it; a role's tools reach the Owner
  only through the per-session ``kos`` MCP server (``knowledgeos.mcp_shim``).
- Installation from source declarations: ``config/roles.json`` + ``roles/`` become one Hermes
  profile distribution per role plus the model-free ``kos-ops`` distribution that only sends
  Discord notices. Distributions are generated under Runtime and installed with
  ``hermes profile install|update``. Credentials are never installed; the report says
  ``present`` (profile-local), ``borrowed`` (root pool for the role's provider) or ``needs credential``.
- ``SendNotifier``: C8 delivery through ``hermes -p kos-ops send`` (outbound only).

``settings.hermes`` keys: ``binary`` (enables Hermes), ``home`` (absolute Hermes root; default
``$HERMES_HOME`` or ``~/.hermes``), ``python`` and ``pythonpath`` (how Hermes starts the shim;
default this interpreter and this source tree),
``setup_timeout_seconds`` (120), ``turn_timeout_seconds`` (1800), ``command_timeout_seconds`` (120).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .catalog import Catalog
from .config import LOOPBACK_HOSTS, ConfigError, OwnerConfig
from .util import sha256

OPS_PROFILE = "kos-ops"
PACKAGE_PARENT = str(Path(__file__).resolve().parents[1])
# Blueprint §4.4 "disabled everywhere" plus every other built-in toolset except the learning
# ones (memory, skills), which §4.5 needs. Platform toolsets are also an explicit empty list.
DISABLED_TOOLSETS = (
    "terminal", "file", "browser", "web", "code_execution", "delegation", "cronjob", "vision", "image_gen",
    "tts", "clarify", "kanban", "computer_use", "connections", "session_search", "todo",
)
SAFE_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TERM", "SHELL")
ROLE_OWNED = ["SOUL.md", "config.yaml", "distribution.yaml"]  # skills stay profile-owned (§4.5)
OPS_OWNED = ["config.yaml", "distribution.yaml", "gateway.parked"]
DISTRIBUTION_VERSION = "1.0.0"
CHANNEL_RE = re.compile(r"^\d+(?::\d+)?$")
LEDGER = ".curator_ledger.jsonl"
LEDGER_ROWS = 20
TAIL_CHARS = 400
# Hermes 0.22.5: agent.reasoning_effort levels and the minimum context it admits for a model.
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
MIN_CONTEXT = 64_000


# ---------------------------------------------------------------- host


@dataclass(frozen=True)
class HermesHost:
    binary: str
    home: Path
    python: str = sys.executable
    pythonpath: str = PACKAGE_PARENT
    setup_timeout: float = 120.0
    turn_timeout: float = 1800.0
    command_timeout: float = 120.0

    def profile_dir(self, profile: str) -> Path:
        return self.home / "profiles" / profile

    def argv(self, *args: str, profile: str | None = None) -> list[str]:
        """Always pass ``-p`` explicitly: Hermes otherwise uses a sticky active profile."""
        return [self.binary, *(("-p", profile) if profile else ()), *args]

    def env(self) -> dict[str, str]:
        """A minimal environment: Owner secrets never reach Hermes subprocesses."""
        env = {key: os.environ[key] for key in SAFE_ENV if key in os.environ}
        env["HERMES_HOME"] = str(self.home)
        return env

    def run(self, argv: list[str], *, data: bytes | None = None, timeout: float | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv,
            input=data,
            stdin=subprocess.DEVNULL if data is None else None,
            capture_output=True,
            env=self.env(),
            timeout=timeout or self.command_timeout,
            check=False,
        )


def host_from_config(config: OwnerConfig) -> HermesHost | None:
    """The Hermes host from ``settings.hermes``; ``None`` when no binary is configured."""
    settings = config.settings.get("hermes", {})
    if not isinstance(settings, dict) or not settings.get("binary"):
        return None
    home = Path(str(settings.get("home") or os.environ.get("HERMES_HOME") or "~/.hermes")).expanduser()
    if not home.is_absolute():
        raise ConfigError("settings.hermes.home must be an absolute path")
    try:
        return HermesHost(
            binary=str(settings["binary"]),
            home=home,
            python=str(settings.get("python") or sys.executable),
            pythonpath=str(settings.get("pythonpath", PACKAGE_PARENT)),
            setup_timeout=float(settings.get("setup_timeout_seconds", 120)),
            turn_timeout=float(settings.get("turn_timeout_seconds", 1800)),
            command_timeout=float(settings.get("command_timeout_seconds", 120)),
        )
    except (TypeError, ValueError) as error:
        raise ConfigError(f"invalid settings.hermes: {error}") from error


# ---------------------------------------------------------------- profile state


def skill_digest(skills: Path) -> str:
    """sha256 over sorted relative paths (length-prefixed UTF-8) and exact file bytes."""
    files = sorted(path for path in skills.rglob("*") if path.is_file()) if skills.is_dir() else []
    hasher = hashlib.sha256()
    for path in files:
        # Skills must remain inside their own profile, including linked files.
        if not path.resolve().is_relative_to(skills.resolve()):
            raise ConfigError("skill path escapes its role profile")
        name = path.relative_to(skills).as_posix().encode("utf-8")
        content = path.read_bytes()
        hasher.update(len(name).to_bytes(8, "big"))
        hasher.update(name)
        hasher.update(len(content).to_bytes(8, "big"))
        hasher.update(content)
    return hasher.hexdigest()


def _pool_has(auth: Path, provider: str) -> bool:
    """Key presence only: ``auth.json`` holds a ``credential_pool[provider]`` list with a dict
    entry, or a ``providers[provider]`` login state. No value is inspected, kept or validated."""
    try:
        data = json.loads(auth.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    pool, states = data.get("credential_pool"), data.get("providers")
    entries = pool.get(provider) if isinstance(pool, dict) else None
    state = states.get(provider) if isinstance(states, dict) else None
    return (isinstance(entries, list) and any(isinstance(entry, dict) for entry in entries)) or (
        isinstance(state, dict) and bool(state))


def credential(home: Path, *, root: Path | None = None, provider: str | None = None, model: bool = True,
               base_url: str | None = None) -> str:
    """Credential state by key presence only (never validated, no value read or printed):

    - ``not required``: the route is a loopback endpoint (a local Ollama server).
    - ``present``: profile-local ``auth.json`` carries the role's provider (``credential_pool``
      entry or ``providers`` state), or a non-empty ``.env`` exists (``kos-ops`` Discord token).
    - ``borrowed``: the profile has none for the provider, so Hermes 0.22.5 reads the root
      Hermes home's ``auth.json`` pool for it (``read_credential_pool`` fallback, applied only
      when the profile has zero entries for the provider; refreshes write through to root).
    - ``needs credential``: neither exists.
    """
    if model and base_url and urlsplit(base_url).hostname in LOOPBACK_HOSTS:
        return "not required"
    env = home / ".env"
    if env.is_file() and env.stat().st_size > 0:
        return "present"
    if not model or not provider:
        return "needs credential"
    if _pool_has(home / "auth.json", provider):
        return "present"
    if root is not None and root != home and _pool_has(root / "auth.json", provider):
        return "borrowed"
    return "needs credential"


def _profile_model(home: Path) -> dict[str, Any]:
    """The model block pinned in the installed profile's ``config.yaml``."""
    try:
        settings = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    model = settings.get("model") if isinstance(settings, dict) else None
    return model if isinstance(model, dict) else {}


def ledger_tail(path: Path) -> list[dict[str, Any]]:
    """The newest skill-ledger rows, scalar fields only, bounded."""
    try:
        lines = path.read_text("utf-8", errors="replace").splitlines()[-LEDGER_ROWS:]
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            rows.append({
                str(key): value[:200] if isinstance(value, str) else value
                for key, value in entry.items()
                if value is None or isinstance(value, (str, int, float, bool))
            })
    return rows


def profile_state(host: HermesHost, profile: str) -> dict[str, Any]:
    """What the console shows per role profile: install, skills, SOUL, credential, ledger."""
    home = host.profile_dir(profile)
    if not home.is_dir():
        return {"installed": False, "skill_digest": None, "soul_digest": None,
                "credential": "needs credential", "ledger": []}
    soul = home / "SOUL.md"
    pinned = _profile_model(home)
    return {
        "installed": True,
        "skill_digest": skill_digest(home / "skills"),
        "soul_digest": sha256(soul.read_bytes()) if soul.is_file() else None,
        "credential": credential(home, root=host.home, provider=pinned.get("provider"), base_url=pinned.get("base_url")),
        "ledger": ledger_tail(home / "skills" / LEDGER),
    }


# ---------------------------------------------------------------- ACP client


class AcpError(Exception):
    """The agent answered a request with a JSON-RPC error."""

    def __init__(self, method: str, error: Any):
        message = str(error.get("message", "")) if isinstance(error, dict) else str(error)
        self.code = error.get("code") if isinstance(error, dict) else None
        # Hermes says "turn outcome is unknown ... do not resend" when it lost the turn.
        self.unknown = "outcome" in message.lower() and "unknown" in message.lower()
        super().__init__(f"{method}: JSON-RPC error {self.code}: {message[:TAIL_CHARS]}")


class AcpLost(Exception):
    """No answer: the process exited, the transport closed or the deadline passed."""


class AcpClient:
    """One ``hermes acp`` subprocess. Requests the agent sends to the client are answered
    by ``on_request(method, params) -> result`` (raise ``LookupError`` for "not offered")."""

    def __init__(self, argv: list[str], *, cwd: Path, env: dict[str, str],
                 on_request: Callable[[str, dict[str, Any]], Any]):
        self._proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=cwd, env=env, start_new_session=True,
        )
        self._on_request = on_request
        self._write_lock = threading.Lock()
        self._cond = threading.Condition()
        self._answers: dict[int, dict[str, Any] | None] = {}
        self._next = 0
        self._closed = False
        self._reader = threading.Thread(target=self._read, name="kos-acp", daemon=True)
        self._reader.start()

    @property
    def returncode(self) -> int | None:
        return self._proc.poll()

    def request(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        with self._cond:
            if self._closed:
                raise AcpLost(f"{method}: transport closed (exit {self._proc.poll()})")
            self._next += 1
            request_id = self._next
            self._answers[request_id] = None
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._answers.get(request_id) is None and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._answers.pop(request_id, None)
                    raise AcpLost(f"{method}: no answer within {timeout:g} s")
                self._cond.wait(remaining)
            answer = self._answers.pop(request_id, None)
        if answer is None:
            self._proc.poll()
            raise AcpLost(f"{method}: transport closed (exit {self._proc.returncode})")
        if "error" in answer:
            raise AcpError(method, answer["error"])
        result = answer.get("result")
        return result if isinstance(result, dict) else {}

    def notify(self, method: str, params: dict[str, Any]) -> None:
        with contextlib.suppress(AcpLost):
            self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def close(self, grace: float = 2.0) -> None:
        with contextlib.suppress(OSError, ValueError):
            self._proc.stdin.close()  # type: ignore[union-attr]
        try:
            self._proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self.kill()
        self._reader.join(timeout=1)

    def kill(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGKILL):
            if self._proc.poll() is not None:
                break
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self._proc.pid, signum)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._proc.wait(timeout=1)
        with contextlib.suppress(OSError, ValueError):
            self._proc.stdin.close()  # type: ignore[union-attr]
        self._reader.join(timeout=1)

    def _send(self, message: dict[str, Any]) -> None:
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                self._proc.stdin.write(data)  # type: ignore[union-attr]
                self._proc.stdin.flush()  # type: ignore[union-attr]
            except (OSError, ValueError) as error:
                raise AcpLost(f"transport closed: {error}") from error

    def _read(self) -> None:
        stdout = self._proc.stdout
        assert stdout is not None
        try:
            while line := stdout.readline():
                try:
                    message = json.loads(line)
                except ValueError:
                    continue  # not protocol output
                if not isinstance(message, dict):
                    continue
                if "method" in message:
                    if "id" in message:
                        self._answer(message)
                    continue  # notifications (session/update) carry nothing the Owner relies on
                with self._cond:
                    if message.get("id") in self._answers:
                        self._answers[message["id"]] = message
                        self._cond.notify_all()
        finally:
            with self._cond:
                self._closed = True
                self._cond.notify_all()

    def _answer(self, message: dict[str, Any]) -> None:
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        try:
            reply = {"jsonrpc": "2.0", "id": message["id"], "result": self._on_request(str(message["method"]), params)}
        except LookupError:
            reply = {"jsonrpc": "2.0", "id": message["id"],
                     "error": {"code": -32601, "message": f"{message['method']} is not offered by KnowledgeOS"}}
        with contextlib.suppress(AcpLost):
            self._send(reply)


# ---------------------------------------------------------------- installation


def _yaml(data: dict[str, Any], source: str) -> str:
    header = f"# Generated by `knowledgeos install` from {source}. Edit the source, not this file.\n"
    return header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _locked_down(learning: bool) -> dict[str, Any]:
    """Profile settings shared by every KnowledgeOS profile: no fallback route, no silent
    recovery cycles, no built-in tools, direct tool visibility, learning on or off."""
    return {
        "fallback_providers": [],
        "agent": {"auto_recovery_cycles": 0, "disabled_toolsets": list(DISABLED_TOOLSETS)},
        "platform_toolsets": {"cli": [], "acp": []},
        "tools": {"tool_search": {"enabled": "off"}},
        "skills": {"ledger": learning},
        "curator": {"enabled": learning},
        "auxiliary": {"background_review": {"enabled": learning}},
    }


def _distribution(profile: str, description: str, owned: list[str], files: dict[str, str]) -> dict[str, str]:
    content = hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()
    manifest = {"name": profile, "version": f"{DISTRIBUTION_VERSION}+{content[:12]}",
                "description": description, "distribution_owned": owned}
    return {"distribution.yaml": _yaml(manifest, "KnowledgeOS source declarations"), **files}


def _write_tree(target: Path, files: dict[str, str]) -> None:
    """Regenerate ``target`` with exactly ``files``. The path is stable because
    ``hermes profile update`` re-reads the recorded install source."""
    staging = target.with_name(f".{target.name}.staging-{os.getpid()}")
    shutil.rmtree(staging, ignore_errors=True)
    for rel, content in files.items():
        path = staging / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)


def _distributions(config: OwnerConfig) -> Path:
    return config.runtime / "hermes" / "distributions"


def _tail(data: bytes) -> str:
    return data.decode("utf-8", "replace").strip()[-TAIL_CHARS:]


def _execute(host: HermesHost, argv: list[str], dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {"status": "planned"}
    try:
        result = host.run(argv)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"status": "failed", "error": str(error)}
    if result.returncode == 0:
        return {"status": "done"}
    return {"status": "failed", "error": f"exit {result.returncode}: {_tail(result.stderr or result.stdout)}"}


def _install(host: HermesHost, profile: str, distribution: Path, dry_run: bool) -> dict[str, Any]:
    """``profile install`` for a new profile, ``profile update --force-config`` for an existing
    one (declared configuration wins; user-owned memories, skills and credentials stay)."""
    existing = host.profile_dir(profile).is_dir()
    if existing:
        argv = host.argv("profile", "update", profile, "--force-config", "-y")
    else:
        argv = host.argv("profile", "install", str(distribution), "--name", profile, "-y")
    entry = {"profile": profile, "action": "update" if existing else "install",
             "distribution": str(distribution), "command": argv}
    entry.update(_execute(host, argv, dry_run))
    return entry


def _roles_document(config: OwnerConfig) -> dict[str, Any]:
    path = config.root / "config" / "roles.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("roles"), dict):
        raise ConfigError("config/roles.json needs a roles object")
    return document


def role_files(config: OwnerConfig, role: str, declared: dict[str, Any], common: str) -> dict[str, str]:
    """``SOUL.md`` (COMMON + role SOUL) and ``config.yaml`` with the pinned model route, its
    optional endpoint (``base_url``, ``ollama_num_ctx``) and ``reasoning_effort``."""
    route = declared.get("model_route")
    if not isinstance(route, str) or "/" not in route:
        raise ConfigError(f"role {role} needs a provider/model route in config/roles.json")
    provider, model = route.split("/", 1)
    block: dict[str, Any] = {"default": model, "provider": provider}
    base_url = declared.get("base_url")
    if base_url is not None:
        parts = urlsplit(base_url) if isinstance(base_url, str) else None
        if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
            raise ConfigError(f"role {role} base_url must be an http(s) URL")
        block["base_url"] = base_url
    num_ctx = declared.get("ollama_num_ctx")
    if num_ctx is not None:
        if type(num_ctx) is not int or num_ctx < MIN_CONTEXT:
            raise ConfigError(f"role {role} ollama_num_ctx must be an integer of at least {MIN_CONTEXT}")
        block["ollama_num_ctx"] = num_ctx
    settings = {"model": block, **_locked_down(learning=True)}
    effort = declared.get("reasoning_effort")
    if effort is not None:
        if effort not in REASONING_EFFORTS:
            raise ConfigError(f"role {role} reasoning_effort must be one of {', '.join(REASONING_EFFORTS)}")
        settings["agent"]["reasoning_effort"] = effort
    soul = (config.root / declared["soul"]).read_text(encoding="utf-8")
    files = {"SOUL.md": common.rstrip("\n") + "\n\n" + soul, "config.yaml": _yaml(settings, "config/roles.json")}
    return _distribution(declared["profile"], f"KnowledgeOS {role} role", ROLE_OWNED, files)


def install_profiles(config: OwnerConfig, host: HermesHost, *, dry_run: bool = False) -> dict[str, Any]:
    """Generate and install one profile per model role in ``config/roles.json``, then the
    ``kos-ops`` profile that only sends Discord notices."""
    document = _roles_document(config)
    common = (config.root / document["common"]).read_text(encoding="utf-8")
    entries = []
    for role, declared in sorted(document["roles"].items()):
        target = _distributions(config) / declared["profile"]
        _write_tree(target, role_files(config, role, declared, common))
        entry = {"role": role, **_install(host, declared["profile"], target, dry_run)}
        provider = declared["model_route"].split("/", 1)[0]
        entry["credential"] = credential(host.profile_dir(declared["profile"]), root=host.home, provider=provider,
                                         base_url=declared.get("base_url"))
        entries.append(entry)
    operations = _install_operations(config, host, document, dry_run)
    return {"target": "profiles", "dry_run": dry_run, "hermes_home": str(host.home), "profiles": entries,
            "operations": operations,
            "ok": all(entry["status"] != "failed" for entry in [*entries, operations])}


def _install_operations(config: OwnerConfig, host: HermesHost, document: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    """The ``kos-ops`` profile runs no model and no scheduler. It is parked so its Discord token
    cannot start an inbound adapter; the Owner only calls ``hermes -p kos-ops send``."""
    profile = (document.get("operations_profile") or {}).get("profile")
    if not isinstance(profile, str) or not profile:
        raise ConfigError("config/roles.json needs operations_profile.profile")
    files = {"config.yaml": _yaml(_locked_down(learning=False), "config/roles.json"),
             "gateway.parked": "KnowledgeOS outbound-only: the gateway stays parked; the Owner only runs `hermes -p kos-ops send`.\n"}
    target = _distributions(config) / profile
    _write_tree(target, _distribution(profile, "KnowledgeOS outbound Discord delivery (no model)", OPS_OWNED, files))
    entry = _install(host, profile, target, dry_run)
    entry["credential"] = credential(host.profile_dir(profile), model=False)
    return entry


# ---------------------------------------------------------------- notifier


def render_notice(notice: dict[str, Any]) -> str:
    """Plain text of a composed notice: title, lines, links. Nothing is added or decided."""
    parts = [notice.get("title"), *notice.get("lines", []), *notice.get("links", [])]
    return "\n".join(str(part) for part in parts if part)


def send_outcome(returncode: int, stdout: bytes) -> str:
    """Exit 0 with Hermes success/skipped is delivered; exit 1 with a parsed failure
    is not delivered; malformed, contradictory or missing receipts are unknown."""
    text = stdout.decode("utf-8", "replace").strip()
    payload: Any = None
    for candidate in (text, text.splitlines()[-1] if text else ""):
        try:
            payload = json.loads(candidate)
            break
        except ValueError:
            continue
    if not isinstance(payload, dict):
        return "unknown"
    if returncode == 0 and (payload.get("success") is True or payload.get("skipped") is True) and not payload.get("error"):
        return "delivered"
    if returncode == 1 and payload.get("success") is not True and (payload.get("error") or payload.get("success") is False):
        return "not_delivered"
    return "unknown"


class SendNotifier:
    """The Owner's notifier (``owner.post_notice``) through ``hermes -p kos-ops send``. One
    attempt per notice; Owner settles unknown deliveries and never re-sends them."""

    def __init__(self, host: HermesHost, channel: str, *, timeout: float = 10.0, profile: str = OPS_PROFILE):
        if not CHANNEL_RE.match(channel):
            raise ConfigError("C8.discord_channel must be a numeric Discord channel ID, optionally :<thread ID>")
        self.host = host
        self.timeout = timeout
        self.argv = host.argv("send", "--to", f"discord:{channel}", "--json", "--file", "-", profile=profile)

    def __call__(self, notice: dict[str, Any]) -> str:
        try:
            result = self.host.run(self.argv, data=render_notice(notice).encode("utf-8"), timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        return send_outcome(result.returncode, result.stdout)


def notifier_from_config(config: OwnerConfig, host: HermesHost | None) -> SendNotifier | None:
    """The default C8 notifier: Hermes ``send`` to ``C8.discord_channel`` unless a custom
    ``C8.notifier_command`` is configured (C8 runs that one itself)."""
    settings = {**Catalog(config.root).settings("C8"), **config.settings.get("C8", {})}
    channel = settings.get("discord_channel")
    if settings.get("notifier_command") or host is None or not channel:
        return None
    return SendNotifier(host, str(channel), timeout=float(settings.get("notifier_timeout_seconds", 10)))
