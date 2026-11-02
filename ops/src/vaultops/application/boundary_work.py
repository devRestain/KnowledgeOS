"""Concrete native Exchange and Maintenance Teams with independent owner assessment."""
from __future__ import annotations

import json
from types import SimpleNamespace

from ..adapters.core import canonical, digest
from ..interfaces.mcp_server import _convert_report, _operation
from .boundary_contract import (
    EXCHANGE_PREFIX,
    disclosure,
    exchange_evidence,
    exchange_receipt_id,
    validate_boundary_plan,
)
from .native_team import (
    NativeTeamExecutor,
    NativeTeamPolicy,
    native_admission,
    native_digest,
    require,
)
from .work import segment_digest
from .work_tools import TeamToolSession

_MODULES = ("boundary_work.py", "boundary_contract.py", "boundary_temporal.py", "boundary_workflow.py",
            "native_team_temporal.py", "gateway.py", "read_tools.py", "mcp_effects.py", "work_tools.py")
EXCHANGE_POLICY = NativeTeamPolicy(
    namespace="exchange", method_id="knowledgeos.knowledge_exchange_team", team_id="knowledge-exchange",
    roles=("knowledgeos.gateway_reader", "knowledgeos.knowledge_adapter"),
    criterion={"criterion_id": "criterion.disclosed_adaptation", "description":
               "One current authenticated correlated Gateway result is actually read with exact disclosure and provenance; one receipt-bound local Pending adaptation preserves foreign reference limits and separate human review, without foreign filesystem access or adoption."},
    stages=("gateway_evidence", "adaptation"),
    tools={"gateway_evidence": ("gateway_evidence_read",), "adaptation": ("proposal_adaptation",)}, modules=_MODULES,
)
MAINTENANCE_POLICY = NativeTeamPolicy(
    namespace="maintenance", method_id="knowledgeos.vault_maintenance_team", team_id="vault-maintenance",
    roles=("knowledgeos.schema_auditor", "knowledgeos.recovery_inspector"),
    criterion={"criterion_id": "criterion.scoped_recovery_plan", "description":
               "One actual selected-note diagnostic precedes one exact preimage and diagnostic-bound Pending recovery plan; unresolved owner outcomes and unexamined limits remain explicit, diagnosis is not declared complete and no repair is executed."},
    stages=("schema_diagnosis", "recovery_plan"),
    tools={"schema_diagnosis": ("vault_diagnose",), "recovery_plan": ("proposal_recovery_plan",)}, modules=_MODULES,
)


def exchange_native_digest(binding):
    return native_digest(EXCHANGE_POLICY, binding)


def maintenance_native_digest(binding):
    return native_digest(MAINTENANCE_POLICY, binding)


def exchange_admission(app, binding, source_reference, *, gateway_receipt_id=None, objective=None, **identities):
    if objective is not None:
        selected = exchange_receipt_id(objective)
        require(gateway_receipt_id is None or gateway_receipt_id == selected, "Exchange receipt selection changed")
        gateway_receipt_id = selected
    exchange_evidence(app, gateway_receipt_id)
    return native_admission(app, binding, source_reference, policy=EXCHANGE_POLICY,
                            objective=EXCHANGE_PREFIX + gateway_receipt_id, **identities)


def maintenance_admission(app, binding, source_reference, **identities):
    return native_admission(app, binding, source_reference, policy=MAINTENANCE_POLICY, **identities)


class _BoundaryExecutor(NativeTeamExecutor):
    def request_context(self, record, stage):
        first = self.policy.stages[0]
        exchange = self.policy is EXCHANGE_POLICY
        return {"boundary_kind": "exchange" if exchange else "maintenance",
                "gateway_receipt_id": exchange_receipt_id(record["spec"]["objective"]) if exchange else None,
                "evidence": None if stage == first else self.evidence(record, first)}

    def validate_stage(self, record, request, response):
        calls = response["tool_calls"]
        stage = request["stage_id"]
        tool = self.policy.tools[stage][0]
        require(len(calls) == 1 and calls[0]["name"] == tool, "Boundary stage requires exactly one selected tool")
        call = calls[0]
        arguments, result = call["arguments"], call["result"]
        app = self.exops.application
        if stage == self.policy.stages[0]:
            expected = ({"receipt_id": request["gateway_receipt_id"]} if stage == "gateway_evidence"
                        else {"resource_reference": request["source_reference"]})
            require(arguments == expected and result.get("status") == "ok"
                    and result.get("effect") == "read_only", "Boundary read changed exact selected scope")
            session = TeamToolSession(app, request["work_run_id"], request["executor_id"],
                                      request["graph_run_id"], self.exops)
            actual, error = _convert_report(SimpleNamespace(services=app), tool, arguments,
                _operation(app, tool, arguments, session=session)())
            require(not error and {k: v for k, v in actual.items() if k != "request_id"}
                    == {k: v for k, v in result.items() if k != "request_id"},
                    "Boundary trace differs from fresh actual Owner evidence")
            if stage == "gateway_evidence":
                evidence = exchange_evidence(app, request["gateway_receipt_id"])
                require(result["data"] == evidence, "Gateway evidence changed")
                return evidence
            diagnostic = result["data"]
            require(diagnostic["diagnosis_complete"] is False and bool(diagnostic["unexamined"]),
                    "Maintenance cannot claim a complete diagnosis")
            return {"diagnostic": diagnostic, "diagnostic_digest": digest(canonical(diagnostic))}
        require(set(arguments) <= {"source_reference", "content", "idempotency_key"}
                and arguments.get("source_reference") == request["source_reference"]
                and result.get("status") == "created" and result.get("execution_outcome") == "completed"
                and result.get("effect") == "proposal_created"
                and result.get("data", {}).get("apply_allowed") is False,
                "Boundary result lacks one exact Pending-only proposal effect")
        payload = json.loads(arguments["content"])
        evidence = validate_boundary_plan(app, tool, request["source_reference"], payload,
                                         receipt_id=request["gateway_receipt_id"])
        if stage == "adaptation":
            require(request["evidence"] == evidence and payload["disclosure_scope"] == disclosure(evidence),
                    "Adaptation did not preserve actual accepted Gateway evidence")
        else:
            require(request["evidence"] == {"diagnostic": evidence, "diagnostic_digest": digest(canonical(evidence))},
                    "Recovery plan did not preserve its actual diagnostic")
        return payload

    def result_call(self, execution):
        calls = execution["response"]["tool_calls"]
        require(len(calls) == 1 and calls[0]["name"] == self.policy.tools[self.policy.stages[-1]][0],
                "Boundary result requires one selected proposal effect")
        return calls[0]

    def effect_payload(self, call):
        return json.loads(call["arguments"]["content"])

    def validate_result(self, record, candidate):
        validate_boundary_plan(self.exops.application, self.policy.tools[self.policy.stages[-1]][0],
                               record["method_source_reference"], candidate,
                               receipt_id=exchange_receipt_id(record["spec"]["objective"])
                               if self.policy is EXCHANGE_POLICY else None)


class ExchangeTeamExecutor(_BoundaryExecutor):
    def __init__(self, exops, *, runner):
        super().__init__(exops, runner=runner, policy=EXCHANGE_POLICY)

    def admission(self, binding, source, **identities):
        return exchange_admission(self.exops.application, binding, source,
                                  runner_binding=self.runner_binding, **identities)

    def bundle(self, spec, run, source):
        bundle, artifacts = super().bundle(spec, run, source)
        receipt_id = exchange_receipt_id(spec["objective"])
        evidence = exchange_evidence(self.exops.application, receipt_id)
        raw = canonical(evidence)
        reference = {
            "owner_operation_id": "knowledgeos", "artifact_id": "artifact.gateway." + receipt_id[10:],
            "source_revision": digest(raw)[7:], "content_digest": digest(raw),
            "media_type": "application/json", "size_bytes": len(raw),
            "classification": evidence["envelope"]["classification"],
            "availability": "available_by_owner_export",
            "export_capability_id": "knowledgeos.gateway_evidence_read",
        }
        bundle["segment"]["input_references"].append(reference)
        bundle["segment"]["segment_digest"] = segment_digest(bundle["segment"])
        artifacts[reference["artifact_id"]] = reference
        return bundle, artifacts

    async def gateway_evidence(self, work_run_id):
        await self.execute_stage(work_run_id, "gateway_evidence")

    async def adaptation(self, work_run_id):
        await self.execute_stage(work_run_id, "adaptation")


class MaintenanceTeamExecutor(_BoundaryExecutor):
    def __init__(self, exops, *, runner):
        super().__init__(exops, runner=runner, policy=MAINTENANCE_POLICY)

    async def schema_diagnosis(self, work_run_id):
        await self.execute_stage(work_run_id, "schema_diagnosis")

    async def recovery_plan(self, work_run_id):
        await self.execute_stage(work_run_id, "recovery_plan")
