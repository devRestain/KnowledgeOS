"""Real disposable owner Gateway admission; no foreign filesystem or transport."""
from __future__ import annotations

from uuid import uuid4

from vaultops.adapters.core import canonical, digest
from vaultops.application.gateway import GatewayIdentity, KnowledgeGateway


def accepted_exchange(app, source_reference, *, canary=None):
    pins = app.core.binding
    suffix = uuid4().hex
    query = {"query": "Disclose synthetic resource metadata only"}
    envelope = {
        "contract_version": pins["capsule_version"], "core_version": pins["core_version"],
        "core_digest": pins["core_digest"], "schema_version": pins["schema_version_pin"],
        "semantic_version": pins["semantic_version"], "semantic_digest": pins["semantic_digest"],
        "action": "read", "classification": "internal", "hop_count": 1,
        "idempotency_key": "request." + suffix, "issued_at": app.clock().isoformat(),
        "message_id": "message.request." + suffix, "message_kind": "information_request",
        "payload_digest": digest(canonical(query)), "payload_reference_id": "payload.query." + suffix,
        "payload_size_bytes": len(canonical(query)), "payload_type": "information.query", "purpose": "status",
        "recipient_operation_id": "syntheticexchange", "request_id": "request." + suffix,
        "resource_id": "operation.syntheticexchange", "resource_kind": "operation",
        "resource_owner_operation_id": "syntheticexchange", "revision": 1,
        "scope": "synthetic metadata only; no foreign content disclosure", "semantic_term_ids": [],
        "sender_operation_id": "knowledgeos", "sender_principal_id": "knowledgeos.local", "ttl_seconds": 43200,
    }
    gateway = KnowledgeGateway(app)
    outbound = gateway.register_outbound_request(envelope, query,
        GatewayIdentity("knowledgeos", "knowledgeos.local", ("read",)),
        recipient_available=lambda recipient: recipient == "syntheticexchange")
    assert outbound["dispatch_performed"] is False
    foreign = {**source_reference, "owner_operation_id": "syntheticexchange", "resource_kind": "artifact",
               "logical_id": canary or "synthetic." + uuid4().hex}
    payload = {"resources": [foreign]}
    response = {**envelope, "action": "export", "hop_count": 0,
                "message_id": "message.response." + suffix, "idempotency_key": "response." + suffix,
                "message_kind": "correlated_response", "payload_type": "information.result",
                "payload_reference_id": "payload.result." + suffix, "payload_digest": digest(canonical(payload)),
                "payload_size_bytes": len(canonical(payload)), "recipient_operation_id": "knowledgeos",
                "sender_operation_id": "syntheticexchange", "sender_principal_id": "syntheticexchange.local",
                "resource_owner_operation_id": "knowledgeos", "resource_id": "exchange." + suffix,
                "resource_kind": "exchange", "correlation_id": envelope["request_id"]}
    receipt = gateway.admit(response, payload, GatewayIdentity("syntheticexchange", "syntheticexchange.local", ("export",)))
    return receipt, response, payload
