"""SDK-independent names shared by the owner dispatch and sandboxed Workflow."""

WORKFLOW_TYPE = "knowledgeos.work.v1"
ACTIVITY_TYPE = "knowledgeos.execute_admitted_work.v1"
TEAM_WORKFLOW_TYPE = "KnowledgeOSCurationTeamV1"
CURATION_GRAPH_NAME = "knowledgeos-curation-v1"
CONTINUATION_WORKFLOW_TYPE = "KnowledgeOSCurationContinuationV1"
CONTINUATION_METHOD_ID = "knowledgeos.capture_normalization_continuation_team"
CURATION_INITIAL_GRAPH_NAME = "knowledgeos-curation-initial-v1"
CURATION_SUCCESSOR_GRAPH_NAME = "knowledgeos-curation-successor-v1"
CURATION_RECOVERY_UPDATE = "curation.reconcile.v1"
CURATION_RECOVERY_QUERY = "curation.recovery_status.v1"
CURATION_MARK_UNKNOWN_ACTIVITY = "knowledgeos.curation.mark_unknown.v1"
CURATION_RECONCILE_ACTIVITY = "knowledgeos.curation.reconcile.v1"
CONTROL_WORKFLOW_TYPE = "KnowledgeOSCurationControlV1"
CONTROL_UPDATE = "curation.control.v1"
CONTROL_QUERY = "curation.control_status.v1"
CONTROL_CHECKPOINT_ACTIVITY = "knowledgeos.curation.control_checkpoint.v1"
CONTROL_APPLY_ACTIVITY = "knowledgeos.curation.apply_control.v1"
CONTROL_ROLLOVER_UPDATE = "curation.rollover.v1"
CONTROL_ROLLOVER_PREPARE_ACTIVITY = "knowledgeos.curation.prepare_rollover.v1"
CONTROL_ROLLOVER_ADOPT_ACTIVITY = "knowledgeos.curation.adopt_rollover.v1"
RESEARCH_WORKFLOW_TYPE = "KnowledgeOSEvidenceResearchV1"
RESEARCH_GRAPH_NAME = "knowledgeos-evidence-research-v1"
RESEARCH_METHOD_ID = "knowledgeos.cited_research_team"
METADATA_METHOD_ID = "knowledgeos.note_metadata_team"
METADATA_WORKFLOW_TYPE = "knowledgeos.note-metadata.v1"
METADATA_GRAPH_NAME = "knowledgeos-note-metadata-v1"
RELATION_METHOD_ID = "knowledgeos.relation_ontology_team"
RELATION_WORKFLOW_TYPE = "knowledgeos.relation-ontology.v1"
RELATION_GRAPH_NAME = "knowledgeos-relation-ontology-v1"
DAILY_METHOD_ID = "knowledgeos.daily_corpus_stewardship_team"
DAILY_WORKFLOW_TYPE = "knowledgeos.daily-corpus-stewardship.v1"
DAILY_GRAPH_NAME = "knowledgeos-daily-corpus-stewardship-v1"
AUTHORING_DRAFT_GRAPH_NAME = "knowledgeos-cited-draft-v1"
AUTHORING_DRAFT_WORKFLOW_TYPE = "knowledgeos.cited-draft.v1"
AUTHORING_REVIEW_GRAPH_NAME = "knowledgeos-project-review-v1"
AUTHORING_REVIEW_WORKFLOW_TYPE = "knowledgeos.project-review.v1"
EXCHANGE_METHOD_ID = "knowledgeos.knowledge_exchange_team"
EXCHANGE_GRAPH_NAME = "knowledgeos-knowledge-exchange-v1"
EXCHANGE_WORKFLOW_TYPE = "knowledgeos.knowledge-exchange.v1"
MAINTENANCE_METHOD_ID = "knowledgeos.vault_maintenance_team"
MAINTENANCE_GRAPH_NAME = "knowledgeos-vault-maintenance-v1"
MAINTENANCE_WORKFLOW_TYPE = "knowledgeos.vault-maintenance.v1"

# Only native constructors use these exact criterion identities. Generic source
# fixtures retain their original admitted semantics and never select this route.
NATIVE_TEAM_ROUTES = {
    METADATA_METHOD_ID: ("corpus-stewardship", METADATA_WORKFLOW_TYPE, "note_metadata"),
    RELATION_METHOD_ID: ("relation-ontology", RELATION_WORKFLOW_TYPE, "relation_ontology"),
    DAILY_METHOD_ID: ("corpus-stewardship", DAILY_WORKFLOW_TYPE, "daily_corpus"),
    "knowledgeos.cited_draft_team": ("knowledge-curation", AUTHORING_DRAFT_WORKFLOW_TYPE, "cited_draft"),
    "knowledgeos.project_review_team": ("project-review", AUTHORING_REVIEW_WORKFLOW_TYPE, "project_review"),
    EXCHANGE_METHOD_ID: ("knowledge-exchange", EXCHANGE_WORKFLOW_TYPE, "exchange"),
    MAINTENANCE_METHOD_ID: ("vault-maintenance", MAINTENANCE_WORKFLOW_TYPE, "maintenance"),
}
NATIVE_TEAM_CRITERIA = {
    METADATA_METHOD_ID: "criterion.native_note_metadata",
    RELATION_METHOD_ID: "criterion.native_relation_ontology",
    DAILY_METHOD_ID: "criterion.native_daily_corpus",
    "knowledgeos.cited_draft_team": "criterion.cited_draft",
    "knowledgeos.project_review_team": "criterion.project_review",
    EXCHANGE_METHOD_ID: "criterion.disclosed_adaptation",
    MAINTENANCE_METHOD_ID: "criterion.scoped_recovery_plan",
}


def native_team_route(spec):
    method_id = spec.get("method_contract_ref", {}).get("method_id")
    route = NATIVE_TEAM_ROUTES.get(method_id)
    if route is None:
        return None
    criteria = spec.get("acceptance_criteria", [])
    if len(criteria) != 1 or criteria[0].get("criterion_id") != NATIVE_TEAM_CRITERIA[method_id]:
        return None
    return route

CURATION_DISPATCH_FIELDS = frozenset(
    ("owner_operation_id", "work_run_id", "action_id", "content_digest", "work_spec_digest")
)
CURATION_RECOVERY_TARGET_FIELDS = CURATION_DISPATCH_FIELDS | {
    "temporal_run_id",
    "activity_id",
    "run_revision",
    "generation",
    "fencing_token",
}


def curation_recovery_target(payload: object) -> dict:
    """Validate only the wire shape; owner authorization never comes from input."""
    if (
        not isinstance(payload, dict)
        or set(payload) != {"recovery_id", "target"}
        or not isinstance(payload["recovery_id"], str)
        or not payload["recovery_id"]
        or not isinstance(payload["target"], dict)
        or set(payload["target"]) != CURATION_RECOVERY_TARGET_FIELDS
    ):
        raise ValueError("Recovery payload must contain exactly recovery_id and target")
    target = payload["target"]
    if (
        target["owner_operation_id"] != "knowledgeos"
        or any(
            not isinstance(target[field], str) or not target[field]
            for field in CURATION_DISPATCH_FIELDS | {"temporal_run_id", "activity_id"}
        )
        or any(
            type(target[field]) is not int or target[field] < 1
            for field in ("run_revision", "generation")
        )
        or not isinstance(target["fencing_token"], str)
        or not target["fencing_token"]
    ):
        raise ValueError("Recovery target has invalid logical identities")
    return target


CURATION_CONTROL_TARGET_FIELDS = frozenset((
    "owner_operation_id", "work_run_id", "work_spec_revision", "work_spec_digest",
    "target_control_revision", "generation", "fencing_token", "temporal_run_id",
    "checkpoint_digest",
))


def curation_control_target(payload: object) -> dict:
    """Strict wire validation only; an owner intent supplies all authority."""
    if (
        not isinstance(payload, dict)
        or set(payload) != {"command_id", "kind", "target"}
        or not isinstance(payload["command_id"], str)
        or not payload["command_id"]
        or len(payload["command_id"]) > 96
        or not isinstance(payload["kind"], str)
        or payload["kind"] not in {"cancel", "pause", "resume"}
        or not isinstance(payload["target"], dict)
        or set(payload["target"]) != CURATION_CONTROL_TARGET_FIELDS
    ):
        raise ValueError("Control payload must contain exactly command_id, kind and target")
    target = payload["target"]
    if (
        target["owner_operation_id"] != "knowledgeos"
        or any(
            not isinstance(target[key], str) or not target[key]
            for key in CURATION_CONTROL_TARGET_FIELDS - {
                "work_spec_revision", "target_control_revision", "generation",
            }
        )
        or any(
            type(target[key]) is not int or target[key] < 1
            for key in ("work_spec_revision", "generation")
        )
        or type(target["target_control_revision"]) is not int
        or target["target_control_revision"] < 1
    ):
        raise ValueError("Control target has invalid logical identities")
    return target


def curation_dispatch(payload: object) -> dict:
    """Validate the immutable five-field native dispatch, without authority."""
    if (
        not isinstance(payload, dict) or set(payload) != CURATION_DISPATCH_FIELDS
        or any(not isinstance(value, str) or not value for value in payload.values())
        or payload["owner_operation_id"] != "knowledgeos"
    ):
        raise ValueError("Curation payload differs from exact owner dispatch")
    return payload


def curation_rollover_state(payload: object) -> dict:
    """Validate retained checkpoint/cache wire shape; owner checks canonicality."""
    if (
        not isinstance(payload, dict) or set(payload) != {"status", "target", "commands"}
        or payload["status"] not in ("awaiting_human", "paused")
        or not isinstance(payload["commands"], dict)
    ):
        raise ValueError("Rollover state differs from a quiescent checkpoint")
    target = curation_control_target({
        "command_id": "rollover.validation", "kind": "pause", "target": payload["target"],
    })
    for command_id, command in payload["commands"].items():
        if not isinstance(command, dict) or set(command) != {"payload", "receipt"}:
            raise ValueError("Rollover cache differs from exact command and receipt")
        command_target = curation_control_target(command["payload"])
        receipt = command["receipt"]
        if (
            command_id != command["payload"]["command_id"]
            or command_target["work_run_id"] != target["work_run_id"]
            or command_target["temporal_run_id"] != target["temporal_run_id"]
            or not isinstance(receipt, dict)
            or receipt.get("application_state") != "applied"
            or receipt.get("command_id") != command_id
            or receipt.get("kind") != command["payload"]["kind"]
            or receipt.get("work_run_id") != target["work_run_id"]
            or receipt.get("target_control_revision") != command_target["target_control_revision"]
        ):
            raise ValueError("Rollover cache has mismatched command identities")
    return payload


def curation_rollover_envelope(payload: object) -> dict:
    """Validate exact owner handoff wire shape, not its digest or authority."""
    if not isinstance(payload, dict) or set(payload) != {"dispatch", "rollover"}:
        raise ValueError("Rollover requires exact dispatch and handoff")
    dispatch = curation_dispatch(payload["dispatch"])
    handoff = payload["rollover"]
    if (
        not isinstance(handoff, dict)
        or set(handoff) != {
            "source_temporal_run_id", "workflow_state", "work_record_digest",
            "pending_commands", "handoff_digest",
        }
        or any(
            not isinstance(handoff[key], str) or not handoff[key]
            for key in ("source_temporal_run_id", "work_record_digest", "handoff_digest")
        )
        or not isinstance(handoff["pending_commands"], list)
    ):
        raise ValueError("Rollover handoff has invalid native identities")
    state = curation_rollover_state(handoff["workflow_state"])
    target = state["target"]
    if (
        target["work_run_id"] != dispatch["work_run_id"]
        or target["work_spec_digest"] != dispatch["work_spec_digest"]
        or target["temporal_run_id"] != handoff["source_temporal_run_id"]
    ):
        raise ValueError("Rollover handoff differs from original dispatch/checkpoint")
    seen = set()
    for command in handoff["pending_commands"]:
        if curation_control_target(command) != target or command["command_id"] in seen:
            raise ValueError("Rollover pending command differs from checkpoint")
        seen.add(command["command_id"])
    return payload
