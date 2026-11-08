"""Method-scoped envelopes over the single real Hermes role runtime.

The host preserves actual model/tool evidence. The Owner validates source,
receipt and domain semantics after retaining the response attempt.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _sibling(name):
    spec = importlib.util.spec_from_file_location(
        "knowledgeos_native_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_binding = _sibling("role_binding")
_agent = _sibling("agent_runtime")
binding_for = _binding.binding_for


class NativeRuntimeError(RuntimeError):
    """Secret-free specialist boundary error."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise NativeRuntimeError(message)


def run_native(request: dict[str, Any], *, policy: Any, hermes_root: str | Path,
               profile_home: str | Path, docker_command: str | Path,
               container_name: str) -> dict[str, Any]:
    """Invoke the actual admitted specialist once, preserving full role evidence."""
    policy._validate_request(request)
    tools = list(policy.stage_tools(request))
    step = request.get("agent_step")
    if "agent_step" not in request:
        # A standalone Officer is an explicit separate target, never a Team fallback.
        _require(getattr(policy, "STANDALONE_ROLE", None) == "knowledgeos.normalization_officer",
                 "Team specialist requires its admitted Manager step")
        role_id = policy.STANDALONE_ROLE
        route = None
        manager_instruction = ""
    else:
        _require(isinstance(request["agent_handoffs"], list)
                 and all(isinstance(handoff, dict) for handoff in request["agent_handoffs"]),
                 "Prior agent handoffs must be actual result objects")
        _require(isinstance(step, dict) and set(step) == {
            "stage_id", "node_id", "role_id", "executor_id", "instruction", "route", "criterion_ids", "tools"},
            "Manager step fields differ from the admitted plan")
        for key in ("stage_id", "node_id", "role_id", "executor_id", "instruction", "route"):
            _require(isinstance(step[key], str) and bool(step[key].strip()),
                     "Manager step identity or instruction is missing")
        _require(step["executor_id"] == request["executor_id"]
                 and ("stage_id" not in request or step["stage_id"] == request["stage_id"]),
                 "Manager step differs from the requested executor or stage")
        _require(isinstance(step["tools"], list) and len(step["tools"]) == len(tools)
                 and all(isinstance(name, str) for name in step["tools"])
                 and set(step["tools"]) == set(tools),
                 "Manager step tools differ from the method stage")
        _require(isinstance(step["criterion_ids"], list) and bool(step["criterion_ids"])
                 and all(isinstance(value, str) and value for value in step["criterion_ids"])
                 and len(set(step["criterion_ids"])) == len(step["criterion_ids"]),
                 "Manager step has no exact criterion links")
        role_id, route = step["role_id"], step["route"]
        manager_instruction = "\n\n## Admitted Manager task (within the method contract)\n\n" + step["instruction"]
    system, task = policy._prompt(request, {})
    role_request = {
        "role_id": role_id, "responsibility": "specialist",
        "method_id": request.get("method_id") or policy.METHOD_ID,
        "context": {"owner_request": request, "method_task": task},
        "instructions": (manager_instruction
                         + "\n\n## Binding Owner method contract\n\n"
                         + "The following tool and output schema overrides conflicting Manager formatting directions.\n"
                         + system),
        "tools": tools,
        **{key: request[key] for key in ("work_run_id", "graph_run_id", "executor_id", "control_root")},
    }
    if route is not None:
        role_request["route"] = route
    control = Path(policy.__file__).resolve().parents[3]
    try:
        evidence = _agent.run_role(role_request, control_root=control, hermes_root=hermes_root,
                                   profile_home=profile_home, docker_command=docker_command,
                                   container_name=container_name)
    except _agent.AgentRuntimeError as error:
        raise NativeRuntimeError(str(error)) from None
    _require(evidence["role_id"] == role_id and evidence["responsibility"] == "specialist"
             and evidence["exposed_tools"] == tools
             and (route is None or evidence["route_name"] == route),
             "Actual specialist evidence differs from the admitted step")
    # The method envelope omits call_id; full role evidence stays untouched.
    calls = [{key: value for key, value in call.items() if key != "call_id"}
             for call in evidence["tool_calls"]]
    binding = policy.runner_binding()
    _require(request["runner_binding"] == binding, "Native adapter changed during execution")
    return {
        "request_digest": request["request_digest"], "runner_binding": binding,
        **{key: evidence[key] for key in ("provider", "model", "session_id", "final_text", "exposed_tools")},
        "tool_calls": calls, "agent_evidence": evidence,
    }
