"""Secret-free native callback failure diagnostics without provider execution."""
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.agent_fixture import CONTROL, model_binding

RUNTIME = runpy.run_path(str(Path(__file__).resolve().parents[1] / "clients/hermes/native_runtime.py"))


def native_case():
    binding = model_binding()
    step = {"stage_id": "source_evidence", "node_id": "node.source_evidence",
            "role_id": "knowledgeos.source_extractor", "executor_id": "executor.source_extractor",
            "instruction": "Read the selected source and preserve its exact citation.",
            "route": "light", "criterion_ids": ["criterion.source"], "tools": ["knowledge_read"]}
    request = {"request_digest": "sha256:" + "1" * 64, "runner_binding": binding,
               "method_id": "knowledgeos.note_draft_team", "stage_id": step["stage_id"],
               "work_run_id": "workrun.adapter", "graph_run_id": "graphrun.adapter",
               "executor_id": step["executor_id"], "control_root": str(CONTROL),
               "agent_step": step, "agent_handoffs": [{"source": "actual preceding result"}]}
    policy = SimpleNamespace(__file__=str(CONTROL / "ops/clients/hermes/native_authoring.py"),
        _validate_request=lambda value: None, stage_tools=lambda value: ["knowledge_read"],
        _prompt=lambda value, names: ("Read only the admitted source.", "unused legacy prompt"),
        runner_binding=lambda: binding)
    return request, policy


def invoke_native(request, policy):
    return RUNTIME["run_native"](request, policy=policy, hermes_root="/synthetic/hermes",
        profile_home="/synthetic/profile", docker_command="/synthetic/docker", container_name="synthetic-owner")





@pytest.mark.parametrize("change", ["missing_step", "executor", "tools", "handoffs"])
def test_native_adapter_rejects_changed_admitted_step_before_role_call(monkeypatch, change):
    request, policy = native_case()
    if change == "missing_step":
        del request["agent_step"]
    elif change == "executor":
        request["agent_step"]["executor_id"] = "executor.foreign"
    elif change == "tools":
        request["agent_step"]["tools"] = ["proposal_create"]
    else:
        request["agent_handoffs"] = ["invented result"]
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid admission must not reach the role transport")
    monkeypatch.setattr(RUNTIME["_agent"], "run_role", forbidden)
    with pytest.raises(RUNTIME["NativeRuntimeError"]):
        invoke_native(request, policy)


def test_native_role_transport_failure_never_selects_another_route(monkeypatch):
    request, policy = native_case()
    calls = []
    def fail(agent, **kwargs):
        calls.append(agent["route"])
        raise RUNTIME["_agent"].AgentRuntimeError("Selected provider unavailable")
    monkeypatch.setattr(RUNTIME["_agent"], "run_role", fail)
    with pytest.raises(RUNTIME["NativeRuntimeError"], match="Selected provider unavailable"):
        invoke_native(request, policy)
    assert calls == [request["agent_step"]["route"]]




def test_driver_retains_only_the_exact_safe_callback_diagnostic():
    from vaultops.interfaces.native_owner_driver import callback_failure_diagnostic

    prefix = "Native callback evidence is invalid; observed="
    observed = '[{"name":"proposal_create","reason":"owner_scope_denied"}]'
    assert callback_failure_diagnostic("private provider output\n" + prefix + observed, ["proposal_create"]) == {
        "code": "NATIVE_CALLBACK_EVIDENCE_INVALID",
        "observed": [{"name": "proposal_create", "reason": "owner_scope_denied"}]}
    for stderr in ("private provider output", prefix + "not-json",
                   prefix + '[{"name":"private name","reason":"owner_refusal"}]',
                   prefix + '[{"name":{},"reason":"owner_refusal"}]'):
        assert callback_failure_diagnostic(stderr, ["proposal_create"]) == {"code": "NATIVE_ADAPTER_FAILED"}
    assert callback_failure_diagnostic(prefix + observed, ["knowledge_read"]) == {"code": "NATIVE_ADAPTER_FAILED"}
    assert callback_failure_diagnostic(
        "private provider output\nNative provider HTTP request failed (status=429)", []) == {
            "code": "NATIVE_ADAPTER_FAILED", "reason": "provider_http_refusal", "http_status": 429}








def test_explicit_native_error_does_not_poison_the_next_independent_request(monkeypatch):
    import asyncio
    import io
    import sys

    from vaultops.adapters.core import AdmissionError
    from vaultops.interfaces.writable_owner import NativeStdioTransport

    replies = iter(['{"error":"NATIVE_RESULT_UNKNOWN","diagnostic_code":"NATIVE_CALLBACK_EVIDENCE_INVALID"}\n',
                    '{"response":{"independent":true}}\n'])
    monkeypatch.setattr(sys, "stdin", type("Input", (), {"readline": lambda self, limit: next(replies)})())
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    async def exercise():
        transport = NativeStdioTransport()
        with pytest.raises(AdmissionError, match="NATIVE_CALLBACK_EVIDENCE_INVALID") as failure:
            await transport.call("fixture", {"work_run_id": "workrun.first"})
        assert failure.value.code == "NATIVE_RESULT_UNKNOWN"
        assert not transport.failed
        assert await transport.call("fixture", {"work_run_id": "workrun.independent"}) == {"independent": True}
    asyncio.run(exercise())
    assert output.getvalue().count("KNOWLEDGEOS_NATIVE_REQUEST=") == 2


@pytest.mark.parametrize("reply", ["", "bad-json\n", '{"response":{},"error":"NATIVE_RESULT_UNKNOWN"}\n',
                                   '{"error":"NATIVE_RESULT_UNKNOWN","diagnostic_code":"private"}\n'])
def test_ambiguous_native_frames_still_require_transport_reconstruction(monkeypatch, reply):
    import asyncio
    import io
    import sys

    from vaultops.adapters.core import AdmissionError
    from vaultops.interfaces.writable_owner import NativeStdioTransport

    monkeypatch.setattr(sys, "stdin", type("Input", (), {"readline": lambda self, limit: reply})())
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    async def exercise():
        transport = NativeStdioTransport()
        with pytest.raises((AdmissionError, ValueError)):
            await transport.call("fixture", {})
        assert transport.failed
        with pytest.raises(AdmissionError, match="reconstruction"):
            await transport.call("fixture", {})
    asyncio.run(exercise())
    assert output.getvalue().count("KNOWLEDGEOS_NATIVE_REQUEST=") == 1
