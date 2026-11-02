"""Fresh-process Hermes isolation shared by concrete, separately validated native adapters.

No provider fallback, install, persistent Gateway, profile configuration write or broad tools.
The caller supplies its own exact request/prompt/trace semantics and adapter pins.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import site
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any


class NativeRuntimeError(RuntimeError):
    """Secret-free native transport boundary error."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise NativeRuntimeError(message)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _native_execution(request: dict[str, Any], *, policy: Any, root: Path, profile: Path,
                      docker: Path, container: str, home: Path) -> dict[str, Any]:
    tools = policy.stage_tools(request)
    # The caller pins the selected profile before the first native import. Resolve
    # the existing dependency generation without bootstrap, leases, repairs or sync.
    from pm.environments import committed_venv, site_packages
    environment = committed_venv(root)
    _require(environment is not None, "Hermes has no committed native dependency environment")
    packages = site_packages(environment)
    _require(packages.is_dir(), "Hermes selected dependency packages are unavailable")
    _require(sys.prefix != sys.base_prefix and Path(sys.prefix).resolve() == environment.resolve(),
             "Native adapter must use the selected Hermes venv/bin/python interpreter")
    site.addsitedir(str(packages))
    from hermes_constants import (
        pin_process_hermes_home,
        reset_hermes_home_override,
        set_hermes_home_override,
    )
    pin_process_hermes_home(profile)
    # Legacy process-global writers and all context-local writers see only this
    # disposable home. No credentials are copied there. A routed-home scope also
    # prevents run_agent's import-time project .env loading.
    os.environ["HERMES_HOME"] = str(home)
    os.environ["TMPDIR"] = str(home)
    os.environ["TMP"] = str(home)
    os.environ["TEMP"] = str(home)
    os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
    scope = set_hermes_home_override(home)
    try:
        from agent.secret_scope import reset_secret_scope, set_secret_scope
        secret_scope = set_secret_scope({}, profile_home=str(home))
        try:
            from hermes_cli.auth import (
                _codex_access_token_is_expiring,
                resolve_codex_runtime_credentials,
            )
            from hermes_cli.config import load_config, require_parseable_user_config
            selected_scope = set_hermes_home_override(profile)
            os.environ["HERMES_HOME"] = str(profile)
            try:
                require_parseable_user_config()
                configured = load_config()
                model = configured.get("model", {})
                _require(model.get("provider") == policy.PROVIDER and model.get("default") == policy.MODEL
                         and model.get("base_url", "").rstrip("/") == policy.BASE_URL,
                         "Selected profile route is not the approved native route")
                _require(model.get("api_mode") in {None, "codex_responses"}
                         and model.get("openai_runtime") in {None, "", "native", "responses"},
                         "Selected profile requests a different native transport")
                provider_config = configured.get("providers", {}).get(policy.PROVIDER, {})
                _require(provider_config.get("enabled", True) is not False,
                         "Selected profile disables the approved provider")
                credentials = resolve_codex_runtime_credentials(read_only=True)
                key = credentials.get("api_key")
                if isinstance(key, str) and key and _codex_access_token_is_expiring(key, policy.DEADLINE_SECONDS):
                    # Inference may refresh this existing grant; diagnostics stay read-only.
                    credentials = resolve_codex_runtime_credentials(
                        force_refresh=credentials.get("source") == "credential_pool",
                        refresh_skew_seconds=policy.DEADLINE_SECONDS)
            finally:
                os.environ["HERMES_HOME"] = str(home)
                reset_hermes_home_override(selected_scope)
            _require(credentials.get("provider") == policy.PROVIDER
                     and credentials.get("base_url", "").rstrip("/") == policy.BASE_URL,
                     "Resolved native credential route differs from the approved route")
            key = credentials.get("api_key")
            _require(isinstance(key, str) and bool(key)
                     and not _codex_access_token_is_expiring(key, policy.DEADLINE_SECONDS),
                     "Selected native credential is missing or expires before the deadline")
            # This is a new config, not a profile clone. JSON is valid YAML. No
            # auth/.env, installed skills, histories or broad MCP surface follow it.
            config = {
                "model": {**model, "provider": policy.PROVIDER, "default": policy.MODEL, "base_url": policy.BASE_URL},
                "platform_toolsets": {"cli": [policy.SERVER_NAME]},
                "mcp_servers": {policy.SERVER_NAME: {
                    "command": str(docker),
                    "args": ["exec", "-i", container, "vaultmcp", "--control-root", request["control_root"],
                             "--work-run-id", request["work_run_id"], "--executor-id", request["executor_id"],
                             "--graph-run-id", request["graph_run_id"]],
                    "connect_timeout": 30, "timeout": 30, "lazy": False,
                    "trust": "full", "events": False,
                    "tools": {"include": list(tools), "resources": False, "prompts": False},
                }},
                "tools": {"tool_search": {"enabled": "off"}},
                "memory": {"memory_enabled": False, "user_profile_enabled": False},
                "skills": {"auto_load": []}, "curator": {"enabled": False},
                "agent": {"max_turns": policy.MAX_ITERATIONS, "api_max_retries": 0,
                          "auto_recovery_cycles": 0, "run_budget_seconds": 180},
                "compression": {"enabled": False, "codex_gpt55_autoraise": False},
                "telemetry": {"shared_metrics": {"enabled": False, "send": False}},
            }
            # Only non-secret transport settings are retained from model config.
            config["model"] = {name: value for name, value in config["model"].items()
                               if name in {"provider", "default", "base_url", "context_length", "api_mode"}}
            (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
            from hermes_cli.version_info import get_version_info
            version = get_version_info()
            _require(version.base_version == policy.VERSION and version.commit == policy.REVISION and not version.dirty,
                     "Installed Hermes identity differs from the admitted binding")
            from hermes_cli._early_recovery import ZIP_SWAP_JOURNAL, interrupted_pull_marker
            _require(not any(path.exists() for path in (
                interrupted_pull_marker(root), root / ZIP_SWAP_JOURNAL,
                root / ".update-incomplete", root / ".lazy-refresh-incomplete",
            )), "Hermes has pending recovery work; native adapter cannot mutate the installation")
            from run_agent import AIAgent
            from tools.mcp_tool_discovery import register_mcp_servers
            from tools.mcp_tool_lifecycle import shutdown_mcp_servers
            from tools.mcp_tool_schema import mcp_prefixed_tool_name
            raw_names = {name: mcp_prefixed_tool_name(policy.SERVER_NAME, name) for name in tools}
            canonical_names = {value: name for name, value in raw_names.items()}
            calls: list[dict[str, Any]] = []
            callback_errors: list[str] = []
            callback_lock = threading.Lock()
            agent = None

            def completed(call_id: str, name: str, arguments: dict, result: Any) -> None:
                del call_id
                try:
                    _require(name in canonical_names and isinstance(arguments, dict),
                             "Native callback escaped the admitted tools")
                    entry = {"name": canonical_names[name], "arguments": arguments,
                             "result": policy._owner_result(result), "raw_callback_name": name}
                    # Serialize a snapshot now: later model/transcript changes cannot
                    # manufacture or replace callback receipts.
                    snapshot = json.loads(_canonical(entry))
                    with callback_lock:
                        calls.append(snapshot)
                except (NativeRuntimeError, ValueError, TypeError):
                    with callback_lock:
                        callback_errors.append("Native callback did not contain a successful exact Owner receipt")

            try:
                registered = register_mcp_servers(config["mcp_servers"])
                _require(set(registered) == set(canonical_names), "Native MCP discovery did not expose exactly the stage tools")
                system_prompt, user_prompt = policy._prompt(request, raw_names)
                agent = AIAgent(
                    model=policy.MODEL, provider=policy.PROVIDER, requested_provider=policy.PROVIDER,
                    api_key=key, base_url=policy.BASE_URL, api_mode="codex_responses",
                    enabled_toolsets=[policy.SERVER_NAME], max_iterations=policy.MAX_ITERATIONS,
                    run_budget_seconds=180, fallback_model={}, credential_pool=None,
                    quiet_mode=True, verbose_logging=False, save_trajectories=False,
                    skip_context_files=True, load_soul_identity=False, skip_memory=True,
                    skip_background_review=True, checkpoints_enabled=False,
                    cwd=str(home), tool_complete_callback=completed,
                )
                _require(agent.valid_tool_names == set(canonical_names)
                         and {tool["function"]["name"] for tool in agent.tools} == set(canonical_names),
                         "Native AIAgent surface is not exactly the admitted stage tools")
                _require(agent.provider == policy.PROVIDER and agent.model == policy.MODEL
                         and agent.api_mode == "codex_responses"
                         and agent.base_url.rstrip("/") == policy.BASE_URL,
                         "Native AIAgent route differs from the admitted binding")
                result = agent.run_conversation(user_prompt, system_message=system_prompt)
                _require(not callback_errors, "Native callback evidence is invalid")
                _require(isinstance(result, dict) and result.get("completed") is True
                         and not any(result.get(flag) for flag in ("failed", "error", "partial", "interrupted")),
                         "Native model did not complete the admitted Work")
                _require(agent.provider == policy.PROVIDER and agent.model == policy.MODEL
                         and agent.api_mode == "codex_responses"
                         and agent.base_url.rstrip("/") == policy.BASE_URL,
                         "Native model route changed during execution")
                policy._validate_trace(request, calls)
                final = result.get("final_response")
                _require(isinstance(final, str) and 0 < len(final.strip()) <= 16384,
                         "Native model returned missing or oversized final text")
                _require(1 <= len(calls) <= 64 and isinstance(agent.session_id, str)
                         and 0 < len(agent.session_id) <= 256,
                         "Native model trace or session identity exceeds the owner boundary")
                _require(request["runner_binding"] == policy.runner_binding(), "Native adapter changed during execution")
                return {
                    "request_digest": request["request_digest"], "runner_binding": policy.runner_binding(),
                    "model": agent.model, "provider": agent.provider, "session_id": agent.session_id,
                    "final_text": final, "tool_calls": calls, "exposed_tools": list(tools),
                }
            finally:
                # Close the real stdio transport even on discovery/agent/callback failure.
                try:
                    if agent is not None:
                        agent.close()
                finally:
                    shutdown_mcp_servers(timeout=15)
        finally:
            reset_secret_scope(secret_scope)
    finally:
        reset_hermes_home_override(scope)


def run_native(request: dict[str, Any], *, policy: Any, hermes_root: str | Path,
                       profile_home: str | Path, docker_command: str | Path,
                       container_name: str) -> dict[str, Any]:
    """Run once in a fresh host process; never call from the profile Gateway."""
    policy._validate_request(request)
    _require("run_agent" not in sys.modules, "Native adapter requires a fresh process")
    _require(threading.current_thread() is threading.main_thread(), "Native adapter requires the process main thread")
    root, profile, docker = (Path(path).expanduser().resolve(strict=True)
                             for path in (hermes_root, profile_home, docker_command))
    _require(root.is_dir() and profile.is_dir() and docker.is_file()
             and os.access(docker, os.X_OK), "Native operator startup paths are unavailable")
    _require(isinstance(container_name, str)
             and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container_name) is not None,
             "Native operator container name is invalid")
    original_env, original_path = dict(os.environ), list(sys.path)
    original_cwd, original_bytecode = Path.cwd(), sys.dont_write_bytecode
    original_handler = signal.getsignal(signal.SIGALRM)

    def deadline(signum: int, frame: Any) -> None:
        del signum, frame
        raise NativeRuntimeError("Native model exceeded its execution deadline")

    try:
        sys.dont_write_bytecode = True
        os.environ["HERMES_HOME"] = str(profile)
        sys.path.insert(0, str(root))
        signal.signal(signal.SIGALRM, deadline)
        signal.alarm(policy.DEADLINE_SECONDS)
        with tempfile.TemporaryDirectory(prefix="knowledgeos-native-officer-") as temporary:
            home = Path(temporary)
            os.chdir(home)
            # Hermes may emit provider diagnostics containing credentials or source
            # excerpts. Do not forward those bytes across the operator boundary.
            with (
                open(os.devnull, "w", encoding="utf-8") as quiet,
                contextlib.redirect_stdout(quiet),
                contextlib.redirect_stderr(quiet),
            ):
                return _native_execution(request, policy=policy, root=root, profile=profile, docker=docker,
                                         container=container_name, home=home)
    except NativeRuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - redact arbitrary provider errors at the host API boundary
        raise NativeRuntimeError("Native Hermes execution failed (" + type(exc).__name__ + ")") from None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, original_handler)
        os.chdir(original_cwd)
        os.environ.clear()
        os.environ.update(original_env)
        sys.path[:] = original_path
        sys.dont_write_bytecode = original_bytecode

