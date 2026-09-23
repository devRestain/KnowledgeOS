"""Cross-document semantic checks for the KnowledgeOS Blueprint.

C03 and C04 validate declarations that are present in the Blueprint itself.
They do not require generated action, prompt, schema, Vault, or bridge files to
exist; those files belong to later ownership/generation sessions. Keeping the
expected registries and consumer contracts here makes a rename, deletion,
addition, or cross-document drift fail even when the permissive portions of the
JSON Schema still accept the document.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .runtime import RUNTIME_DIRECTORIES

EXPECTED_PROPERTY_KEYS = (
    "applies_to",
    "areas",
    "artifact_hash",
    "artifact_kind",
    "artifact_repo",
    "artifact_uri",
    "asset",
    "asset_hash",
    "attendees",
    "audience",
    "authors",
    "canonical_date",
    "canonical_timezone",
    "capture_device",
    "capture_kind",
    "capture_label",
    "captured_from",
    "citation_key",
    "claim",
    "confidence",
    "contradicts",
    "decision",
    "decision_by",
    "derived_from",
    "device_timezone",
    "explains",
    "extractor",
    "extractor_version",
    "focus_rank",
    "implements",
    "last_reviewed",
    "meeting_at",
    "needs_desktop_review",
    "next_action",
    "next_review",
    "note_kind",
    "organization",
    "outcome",
    "page_locator_scheme",
    "people",
    "period_end",
    "period_start",
    "possibility",
    "priority",
    "projects",
    "proposal_id",
    "published_date",
    "purpose",
    "question_kind",
    "raises",
    "related",
    "review_cadence",
    "scope",
    "source_hashes",
    "source_kind",
    "source_url",
    "sources",
    "standard",
    "supports",
    "target_date",
    "today_focus",
    "topics",
    "triage_hint",
    "triage_projects",
)

EXPECTED_NOTE_TYPES = (
    "area",
    "artifact",
    "capture",
    "daily",
    "home",
    "idea",
    "knowledge",
    "meeting",
    "moc",
    "monthly",
    "person",
    "project",
    "project_note",
    "proposal",
    "question",
    "source",
    "system",
    "weekly",
)

EXPECTED_TEMPLATES = (
    "T00_Capture.md",
    "T01_AI_Proposal.md",
    "T10_Daily.md",
    "T11_Weekly.md",
    "T12_Monthly.md",
    "T20_Project.md",
    "T21_Idea.md",
    "T22_Question.md",
    "T23_Artifact.md",
    "T24_Project_Note.md",
    "T30_Area.md",
    "T40_Knowledge.md",
    "T41_Source.md",
    "T42_Person.md",
    "T50_MOC.md",
    "T60_Meeting.md",
)

EXPECTED_NOTE_CONTRACTS: dict[str, dict[str, Any]] = {
    "area": {"path_globs": ("30_Areas/**/*.md",), "template": "T30_Area.md"},
    "artifact": {
        "path_globs": (
            "20_Projects/*/Artifacts/**/*.md",
            "90_Archive/Projects/*/*/Artifacts/**/*.md",
        ),
        "template": "T23_Artifact.md",
    },
    "capture": {
        "path_globs": (
            "00_Inbox/Captures/**/*.md",
            "90_Archive/Captures/**/*.md",
        ),
        "template": "T00_Capture.md",
    },
    "daily": {"path_globs": ("10_Journal/Daily/**/*.md",), "template": "T10_Daily.md"},
    "home": {"exact_paths": ("Home.md", "Mobile.md")},
    "idea": {"path_globs": ("40_Knowledge/Ideas/**/*.md",), "template": "T21_Idea.md"},
    "knowledge": {
        "path_globs": ("40_Knowledge/Notes/**/*.md",),
        "template": "T40_Knowledge.md",
    },
    "meeting": {"path_globs": ("60_Meetings/**/*.md",), "template": "T60_Meeting.md"},
    "moc": {
        "path_globs": (
            "50_Maps/**/*.md",
            "20_Projects/*/Working/*MOC.md",
            "90_Archive/Projects/*/*/Working/*MOC.md",
        ),
        "template": "T50_MOC.md",
    },
    "monthly": {
        "path_globs": ("10_Journal/Monthly/**/*.md",),
        "template": "T12_Monthly.md",
    },
    "person": {
        "path_globs": ("40_Knowledge/People/**/*.md",),
        "template": "T42_Person.md",
    },
    "project": {
        "path_globs": ("20_Projects/*/*.md", "90_Archive/Projects/*/*/*.md"),
        "template": "T20_Project.md",
    },
    "project_note": {
        "path_globs": (
            "20_Projects/*/Working/**/*.md",
            "90_Archive/Projects/*/*/Working/**/*.md",
        ),
        "template": "T24_Project_Note.md",
    },
    "proposal": {"path_globs": ("01_AI_Review/**/*.md",), "template": "T01_AI_Proposal.md"},
    "question": {
        "path_globs": ("40_Knowledge/Questions/**/*.md",),
        "template": "T22_Question.md",
    },
    "source": {"path_globs": ("40_Knowledge/Sources/**/*.md",), "template": "T41_Source.md"},
    "system": {"path_globs": ("99_System/**/*.md",)},
    "weekly": {"path_globs": ("10_Journal/Weekly/**/*.md",), "template": "T11_Weekly.md"},
}

EXPECTED_CANONICAL_RELATIONS = {
    "applies_to": {"subject": ("knowledge",), "object": ("project", "idea")},
    "contradicts": {"subject": ("source", "knowledge"), "object": ("knowledge",)},
    "derived_from": {
        "subject": ("idea", "question", "knowledge", "project", "artifact", "source"),
        "object": ("capture", "daily", "weekly", "monthly", "source", "idea", "meeting"),
    },
    "explains": {"subject": ("knowledge",), "object": ("knowledge", "question")},
    "implements": {"subject": ("project",), "object": ("idea",)},
    "raises": {
        "subject": ("idea", "knowledge", "project"),
        "object": ("question",),
    },
    "supports": {"subject": ("source", "knowledge"), "object": ("knowledge",)},
}

EXPECTED_CONTEXT_RELATIONS = {
    "areas": ("area",),
    "people": ("person",),
    "projects": ("project",),
    "related": ("any_note",),
    "sources": ("source",),
    "topics": ("moc",),
}

EXPECTED_PIPELINES = {
    "triage": "triage.json",
    "draft_note": "draft-note.json",
    "summarize": "summarize.json",
    "link_suggestions": "link-suggestions.json",
    "normalize": "normalize.json",
    "answer": "answer.json",
}

EXPECTED_ROUTES = {
    "organize": ("triage", "normalize"),
    "summarize": ("summarize",),
    "relate": ("link_suggestions",),
    "extract": ("draft_note",),
    "inbox": ("triage",),
    "project-summary": ("summarize",),
}

EXPECTED_BRIDGE_ACTIONS = {
    "triage": "ops/schemas/triage-result.schema.json",
    "draft_note": "ops/schemas/proposal.schema.json",
    "summarize": "ops/schemas/answer.schema.json#summary",
    "link_suggestions": "ops/schemas/proposal.schema.json",
    "answer": "ops/schemas/answer.schema.json#answer",
}

EXPECTED_COMMANDS = (
    "vaultctl doctor",
    "vaultctl bootstrap",
    "vaultctl configure",
    "vaultctl blueprint validate",
    "vaultctl schema export",
    "vaultctl capture text",
    "vaultctl capture url",
    "vaultctl capture finalize",
    "vaultctl note create",
    "vaultctl note validate",
    "vaultctl asset import",
    "vaultctl project archive",
    "vaultctl fmt",
    "vaultctl ai queue",
    "vaultctl ai organize",
    "vaultctl ai summarize",
    "vaultctl ai relate",
    "vaultctl ai extract",
    "vaultctl ai inbox",
    "vaultctl ai project-summary",
    "vaultctl ai worker",
    "vaultctl ai review",
    "vaultctl ai approve",
    "vaultctl ai authorize-remote",
    "vaultctl ai reject",
    "vaultctl ai apply",
    "vaultctl bridge ingest",
    "vaultctl bridge status",
    "vaultctl bridge publish",
    "vaultctl export jsonl",
    "vaultctl graph validate",
    "vaultctl index build",
    "vaultctl index verify",
    "vaultctl search",
    "vaultctl retrieve",
    "vaultctl ask",
    "vaultctl reconcile",
    "vaultctl git status",
    "vaultctl commit",
    "vaultctl receipts verify",
    "vaultctl plugins audit",
    "vaultctl launchd install",
    "vaultctl repair plan",
    "vaultctl repair apply",
    "vaultctl obsidian status",
    "vaultctl ui",
)

EXPECTED_PHASES = (
    "inventory_and_migration_plan",
    "portable_vault",
    "git_and_mobile_baseline",
    "mac_plugin_profile",
    "vaultctl_without_llm",
    "readonly_llm_proposal",
    "retrieval",
    "background_and_optional_remote",
    "optional_thin_chat_client",
)

EXPECTED_ACCEPTANCE_SCENARIOS = (
    "fresh_bootstrap",
    "existing_vault_additive_migration",
    "home_cockpit_limits",
    "mobile_core_only",
    "working_copy_pro_and_linked_external_worktree_gate",
    "single_sync_transport_and_default_obsidian_ignored",
    "root_sentinel_remote_branch_roundtrip",
    "root_sentinel_schema_and_remote_fingerprint_canonicalization",
    "portable_vault_name_uri_on_iphone_and_ipad",
    "iphone_unique_capture",
    "mobile_offline_auth_push_failure_preserves_outbox",
    "mobile_unexpected_staged_path_fails_closed",
    "mobile_shortcut_cancel_and_reboot_recovery",
    "mobile_remote_observed_ack_and_seven_day_cleanup",
    "mobile_existing_edit_privacy_before_index_and_push",
    "mobile_defer_rejects_dirty_or_unreadable_committed_blob",
    "repository_scoped_credential_and_lost_device_revoke_drill",
    "mobile_stale_snapshot_disclosure",
    "mobile_asset_budget_refusal",
    "heic_conversion_or_refusal_core_render",
    "ipad_non_destructive_triage_hint",
    "ipad_exact_file_edit_commit",
    "external_worktree_relink_without_data_loss",
    "working_copy_conflict_fail_closed",
    "bridge_request_hash_binding",
    "bridge_replay_noop",
    "bridge_same_id_different_digest_quarantine",
    "bridge_remote_authorization_wait",
    "bridge_exact_response_commit",
    "bridge_all_terminal_outcomes_visible_after_sync",
    "bridge_publish_crash_resume_or_quarantine",
    "prompt_injection_is_data",
    "immutable_job_separate_remote_authorization_receipt",
    "approval_digest_binding",
    "historical_receipt_survives_later_note_update",
    "deterministic_jsonl_projection",
    "generation_pointer_prevents_mixed_jsonl",
    "retrieval_policy_before_embedding_and_context",
    "stale_index_fails_closed",
    "lexical_typed_link_answer_with_citations",
    "optional_vector_rrf_improves_baseline",
    "thin_chat_plugin_removal_preserves_cli",
    "plugin_free_readability",
    "sleep_wake_reconciliation",
    "guestbook_horror_fixture_exact_bytes_and_frozen_mtime",
    "guestbook_horror_end_to_end",
    "capture_finalize_transaction_recovery",
    "source_capture_asset_provenance_roundtrip",
    "project_local_link_cardinality_and_final_artifact_locator",
    "blueprint_cross_validator_negative_fixtures",
)

EXPECTED_BRIDGE_STATES = (
    "committed_request",
    "ingested",
    "awaiting_remote_authorization",
    "queued",
    "rejected",
    "running",
    "proposal_ready",
    "answer_ready",
    "no_change",
    "insufficient_input",
    "refused",
    "failed",
    "conflict",
    "expired",
    "published_local",
    "human_push",
    "mobile_visible",
)

EXPECTED_BRIDGE_TRANSITIONS = {
    "committed_request": ("ingested",),
    "ingested": ("awaiting_remote_authorization", "queued", "rejected", "conflict"),
    "awaiting_remote_authorization": ("queued", "rejected", "expired"),
    "queued": ("running", "rejected", "conflict"),
    "running": (
        "proposal_ready",
        "answer_ready",
        "no_change",
        "insufficient_input",
        "refused",
        "failed",
        "conflict",
    ),
    "proposal_ready": ("published_local", "rejected", "expired", "conflict"),
    "answer_ready": ("published_local", "expired", "conflict"),
    "no_change": ("published_local",),
    "insufficient_input": ("published_local",),
    "refused": ("published_local",),
    "failed": ("published_local",),
    "conflict": ("published_local",),
    "rejected": ("published_local",),
    "expired": ("published_local",),
    "published_local": ("human_push",),
    "human_push": ("mobile_visible",),
}

EXPECTED_STATE_MAPPING = {
    "awaiting_remote_authorization": ("awaiting_remote_authorization", "awaiting_remote_authorization"),
    "queued": ("queued", "queue"),
    "rejected": ("rejected", "rejected"),
    "proposal_ready": ("needs_review", "review"),
    "answer_ready": ("answer_ready", "review"),
    "no_change": ("no_change", "done"),
    "insufficient_input": ("insufficient_input", "done"),
    "refused": ("refused", "done"),
    "failed": ("failed", "failed"),
    "conflict": ("conflict", "conflict"),
    "expired": ("expired", "expired"),
}

EXPECTED_BASE_DIRECTORY = "99_System/Bases"

EXPECTED_BASES: dict[str, dict[str, dict[str, Any]]] = {
    "Journal.base": {
        "Today Focus": {
            "filters": {"type": "daily", "period_start": "today"},
            "sort": ["file_name_desc"],
            "limit": 1,
            "columns": ["file.link", "today_focus"],
        }
    },
    "Projects.base": {
        "Now": {
            "filters": {"type": "project", "status_in": ["active", "blocked"]},
            "sort": [
                "focus_rank_asc",
                "priority_high_to_low",
                "target_date_asc_nulls_last",
                "file_name_asc",
            ],
            "limit": 3,
            "columns": ["file.link", "status", "next_action", "focus_rank", "priority", "target_date"],
        },
        "Mobile": {
            "filters": {"type": "project", "status_in": ["active", "blocked"]},
            "sort": [
                "focus_rank_asc",
                "priority_high_to_low",
                "target_date_asc_nulls_last",
                "file_name_asc",
            ],
            "limit": 3,
            "columns": ["file.link", "next_action", "status"],
        },
        "Next Actions": {
            "filters": {
                "type": "project",
                "status_in": ["active", "blocked"],
                "next_action_nonempty": True,
            },
            "sort": [
                "focus_rank_asc",
                "priority_high_to_low",
                "target_date_asc_nulls_last",
                "file_name_asc",
            ],
            "limit": 3,
            "columns": ["file.link", "next_action", "status"],
        },
        "Blocked": {
            "filters": {"type": "project", "status": "blocked"},
            "sort": ["focus_rank_asc", "priority_high_to_low", "file_name_asc"],
            "limit": 5,
            "columns": ["file.link", "next_action", "target_date"],
        },
    },
    "Decisions.base": {
        "Open": {
            "filters": {
                "type": "question",
                "status_in": ["open", "deciding"],
                "question_kind": "decision",
                "decision_nonempty": True,
            },
            "sort": ["decision_by_asc_nulls_last", "priority_high_to_low", "file_name_asc"],
            "limit": 5,
            "columns": ["file.link", "decision", "decision_by", "projects", "priority"],
        }
    },
    "Knowledge.base": {
        "Ideas": {
            "filters": {"type": "idea", "status_in": ["seed", "incubating", "testing"]},
            "sort": ["file_mtime_desc", "file_name_asc"],
            "limit": 10,
            "columns": ["file.link", "status", "possibility", "projects", "file.mtime"],
        },
        "Radar": {
            "filters": {"type_in": ["knowledge", "idea"]},
            "sort": ["file_mtime_desc", "file_name_asc"],
            "limit": 6,
            "columns": ["file.link", "type", "status", "confidence", "file.mtime"],
        }
    },
    "Inbox.base": {
        "Unprocessed": {
            "filters": {"type": "capture", "status": "unprocessed"},
            "sort": ["created_asc", "file_name_asc"],
            "limit": 10,
            "columns": ["file.link", "created", "capture_kind", "triage_hint", "needs_desktop_review"],
        },
        "Mobile Review": {
            "filters": {"type": "capture", "status": "unprocessed", "needs_desktop_review": True},
            "sort": ["created_asc", "file_name_asc"],
            "limit": 5,
            "columns": ["file.link", "capture_label", "triage_hint"],
        },
    },
    "Sources.base": {
        "Reading queue": {
            "filters": {"type": "source", "status_in": ["queued", "reading"]},
            "sort": ["published_date_desc_nulls_last", "file_name_asc"],
            "limit": 10,
            "columns": ["file.link", "status", "source_kind", "authors", "published_date"],
        },
        "Processed": {
            "filters": {"type": "source", "status": "processed"},
            "sort": ["file_mtime_desc", "file_name_asc"],
            "limit": 20,
            "columns": ["file.link", "source_kind", "published_date", "source_url"],
        },
    },
    "Review.base": {
        "PendingOrConflict": {
            "filters": {"type": "proposal", "status_in": ["pending", "conflict"]},
            "sort": ["created_asc", "file_name_asc"],
            "limit": 10,
            "columns": ["file.link", "status", "proposal_id", "created"],
        },
        "Mobile Results": {
            "filters": {"type": "proposal", "status_in": ["pending", "conflict"]},
            "sort": ["created_desc", "file_name_asc"],
            "limit": 5,
            "columns": ["file.link", "status", "created"],
        },
    },
}

EXPECTED_DASHBOARD_SECTIONS = {
    "home": (
        "today_direction",
        "quick_capture",
        "now",
        "needs_a_decision",
        "next_actions",
        "knowledge_radar",
        "inbox",
        "ai_review",
        "support_status",
    ),
    "mobile": (
        "quick_capture",
        "today",
        "focus_projects_and_next_actions",
        "needs_desktop_review",
        "ai_results",
        "bookmarks",
    ),
}

EXPECTED_DASHBOARD_SOURCES = {
    "home": {
        "today_direction": ("Journal.base", "Today Focus", 1),
        "now": ("Projects.base", "Now", 3),
        "needs_a_decision": ("Decisions.base", "Open", 5),
        "next_actions": ("Projects.base", "Next Actions", 3),
        "knowledge_radar": ("Knowledge.base", "Radar", 6),
        "inbox": ("Inbox.base", "Unprocessed", 10),
        "ai_review": ("Review.base", "PendingOrConflict", 10),
    },
    "mobile": {
        "focus_projects_and_next_actions": ("Projects.base", "Mobile", 3),
        "needs_desktop_review": ("Inbox.base", "Mobile Review", 5),
        "ai_results": ("Review.base", "Mobile Results", 5),
    },
}

EXPECTED_PROJECTION_TOP_LEVEL = {
    "output_path_namespace": "runtime_relative_posix",
    "schema_path_namespace": "control_root_relative_posix",
    "generation_root": "index/exports/generations/GENERATION_ID",
    "notes_jsonl": "index/exports/generations/GENERATION_ID/notes.jsonl",
    "edges_jsonl": "index/exports/generations/GENERATION_ID/edges.jsonl",
    "manifest": "index/exports/generations/GENERATION_ID/manifest.json",
    "current_pointer": "index/exports/current.json",
}

EXPECTED_PROJECTION_SCHEMAS = {
    "note_record": "ops/schemas/note-record.schema.json",
    "edge_record": "ops/schemas/edge-record.schema.json",
    "retrieval_candidate": "ops/schemas/retrieval-candidate.schema.json",
    "answer": "ops/schemas/answer.schema.json",
}

EXPECTED_NOTE_RECORD_FIELDS = {
    "id": "uuid_or_system_id",
    "path": "vault_relative_posix",
    "title": "text",
    "type": "note_type_enum",
    "status": "type_status_enum",
    "properties": "flat_object_excluding_promoted_fields",
    "body": "text",
    "wikilinks": "list_of_wikilink_records",
    "created": "iso8601_with_offset",
    "modified": "iso8601_with_offset",
    "content_hash": "sha256",
    "schema_version": "integer",
}

EXPECTED_WIKILINK_FIELDS = {
    "raw_target": "text",
    "resolved_id": "uuid_or_system_id_or_null",
    "resolved_path": "vault_relative_posix_or_null",
    "locator": "text_or_null",
}

EXPECTED_EDGE_RECORD_FIELDS = {
    "subject_id": "uuid_or_system_id",
    "edge_kind": ["semantic", "context"],
    "predicate": "registry_value",
    "object_id": "uuid_or_system_id",
    "subject_path": "vault_relative_posix",
    "object_path": "vault_relative_posix",
    "provenance": "canonical_property",
    "source_locator": "frontmatter_predicate_index",
    "object_locator": "text_or_null",
    "source_content_hash": "sha256",
    "relation_schema_version": "integer",
}

EXPECTED_PROJECTION_SERIALIZATION = {
    "encoding": "utf8",
    "bom": False,
    "newline": "lf",
    "terminal_newline_count": 1,
    "json_separators": "compact",
    "object_key_order": "unicode_codepoint_lexicographic_after_nfc",
    "record_sort": {
        "notes": ["path_nfc_utf8_bytes", "id"],
        "edges": ["subject_id", "edge_kind", "predicate", "object_id", "object_locator_nulls_first"],
    },
    "source_list_order": "preserve",
    "non_lf_or_invalid_utf8_source": "reject",
}

EXPECTED_CANONICAL_EDGE_FIELDS = [
    "subject_id",
    "edge_kind",
    "predicate",
    "object_id",
    "subject_path",
    "object_path",
    "provenance",
    "source_locator",
    "object_locator",
    "source_content_hash",
    "relation_schema_version",
]

EXPECTED_PROJECTION_EXCLUSIONS = [
    "file_mtime",
    "local_receipt_provenance",
    "proposal_confidence",
]

EXPECTED_PUBLISH_PROTOCOL = [
    "freeze_source_path_hash_snapshot",
    "write_and_fsync_generation_files",
    "write_and_fsync_generation_manifest",
    "verify_source_snapshot_unchanged",
    "atomic_replace_current_pointer",
    "fsync_parent_directory",
]

EXPECTED_CAPTURE_FINALIZE_TRANSACTION = [
    "verify_source_hash_and_capture_schema",
    "validate_outcome_triaged_or_discarded",
    "resolve_optional_related_target",
    "journal_status_and_related_update_intent",
    "publish_frontmatter_update",
    "link_aware_move_to_90_Archive/Captures/YYYY",
    "verify_destination_schema_and_links",
    "append_completion_receipt",
]

EXPECTED_RETRIEVAL_PHASES = [
    "native_exact_and_links",
    "lexical_fts",
    "local_vectors",
    "reciprocal_rank_fusion",
    "bounded_typed_graph_expansion",
]

EXPECTED_RETRIEVAL_GRAPH = {
    "default_hops": 1,
    "maximum_hops": 2,
    "per_hop_node_cap": 20,
    "total_edge_cap": 60,
    "total_candidate_cap": 50,
    "one_hop_allowlist": {
        "predicates": [
            "supports",
            "contradicts",
            "explains",
            "applies_to",
            "derived_from",
            "implements",
            "raises",
            "projects",
            "areas",
            "topics",
            "sources",
            "people",
            "related",
        ],
        "directions": ["outgoing", "incoming"],
    },
    "two_hop_sequence_allowlist": [
        [
            {"direction": "incoming", "predicate": "applies_to"},
            {"direction": "incoming", "predicate": "supports"},
        ],
        [
            {"direction": "incoming", "predicate": "applies_to"},
            {"direction": "incoming", "predicate": "contradicts"},
        ],
        [
            {"direction": "outgoing", "predicate": "implements"},
            {"direction": "outgoing", "predicate": "derived_from"},
        ],
        [
            {"direction": "outgoing", "predicate": "raises"},
            {"direction": "incoming", "predicate": "explains"},
        ],
        [
            {"direction": "incoming", "predicate": "projects"},
            {"direction": "outgoing", "predicate": "sources"},
        ],
    ],
    "unknown_sequence_policy": "reject",
}

EXPECTED_RETRIEVAL_CORPUS = {
    "default_include_types": [
        "knowledge",
        "source",
        "project",
        "project_note",
        "artifact",
        "idea",
        "question",
    ],
    "journal_requires_explicit_scope": True,
    "exclude_paths": [
        ".vault-bridge/**",
        ".obsidian-*/**",
        "99_System/**",
        "01_AI_Review/Rejected/**",
        "01_AI_Review/Expired/**",
    ],
    "review_corpus_separate": ["01_AI_Review/Pending/**", "01_AI_Review/Conflict/**"],
}

EXPECTED_RETRIEVAL_REMOTE_EMBEDDING = {
    "allowed_ai_policy": ["remote_ok"],
    "ask_requires_digest_bound_interactive_authorization": True,
    "local_only_and_deny_forbidden": True,
}

EXPECTED_RETRIEVAL_STALENESS = {
    "default": "fail_closed_and_require_rebuild",
    "running_query_pins_generation": True,
}


@dataclass(frozen=True)
class SemanticValidation:
    """Result of C03 semantic checks."""

    errors: tuple[dict[str, Any], ...]

    @property
    def passed(self) -> bool:
        return not self.errors


def _error(
    code: str,
    locator: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "code": code,
        "keyword": "semantic",
        "locator": locator,
        "schema_locator": "#",
        "message": message,
    }
    if details:
        result["details"] = dict(details)
    return result


def _path(*parts: object) -> str:
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(escaped)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _items(value: Any) -> list[Any]:
    if isinstance(value, Mapping):
        return list(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _check_exact_set(
    errors: list[dict[str, Any]],
    actual: Any,
    expected: Iterable[str],
    *,
    locator: str,
    code: str,
) -> None:
    expected_set = set(expected)
    actual_items = _items(actual)
    actual_set = {str(item) for item in actual_items}
    for item in sorted(expected_set - actual_set):
        errors.append(
            _error(
                code,
                f"{locator}/{item}",
                f"required registry item is missing: {item}",
                details={"kind": "missing", "item": item},
            )
        )
    for item in sorted(actual_set - expected_set):
        errors.append(
            _error(
                code,
                f"{locator}/{item}",
                f"unexpected registry item is present: {item}",
                details={"kind": "extra", "item": item},
            )
        )
    duplicates = sorted(str(item) for item in actual_items if actual_items.count(item) > 1)
    for item in sorted(set(duplicates)):
        errors.append(
            _error(
                f"{code}_DUPLICATE",
                f"{locator}/{item}",
                f"registry item is duplicated: {item}",
                details={"kind": "duplicate", "item": item},
            )
        )


def _check_mapping_value(
    errors: list[dict[str, Any]],
    actual: Any,
    expected: Any,
    *,
    locator: str,
    code: str,
) -> None:
    if actual != expected:
        errors.append(
            _error(
                code,
                locator,
                "declared semantic mapping does not match the canonical contract",
                details={"expected": expected, "actual": actual},
            )
        )


def _check_note_paths_and_templates(blueprint: Mapping[str, Any], errors: list[dict[str, Any]]) -> None:
    note_types = _as_mapping(blueprint.get("note_types"))
    _check_exact_set(
        errors,
        note_types,
        EXPECTED_NOTE_TYPES,
        locator="/note_types",
        code="SEMANTIC_NOTE_TYPE_EXACT_SET",
    )
    templates = _as_mapping(blueprint.get("templates"))
    _check_exact_set(
        errors,
        templates.get("required"),
        EXPECTED_TEMPLATES,
        locator="/templates/required",
        code="SEMANTIC_TEMPLATE_EXACT_SET",
    )

    for note_type in EXPECTED_NOTE_TYPES:
        actual = _as_mapping(note_types.get(note_type))
        expected = EXPECTED_NOTE_CONTRACTS[note_type]
        actual_paths = actual.get("path_globs", actual.get("exact_paths"))
        expected_paths = expected.get("path_globs", expected.get("exact_paths"))
        if set(_items(actual_paths)) != set(expected_paths):
            errors.append(
                _error(
                    "SEMANTIC_NOTE_PATH_PATTERN",
                    _path("note_types", note_type, "path_globs" if "path_globs" in expected else "exact_paths"),
                    "note type path declaration does not match the canonical path contract",
                    details={"expected": list(expected_paths), "actual": actual_paths},
                )
            )
        expected_template = expected.get("template")
        actual_template = actual.get("template")
        if actual_template != expected_template:
            errors.append(
                _error(
                    "SEMANTIC_NOTE_TEMPLATE_MAPPING",
                    _path("note_types", note_type, "template"),
                    "note type template mapping does not match the canonical contract",
                    details={"expected": expected_template, "actual": actual_template},
                )
            )

    actual_templates = [
        _as_mapping(note_types.get(note_type)).get("template")
        for note_type in EXPECTED_NOTE_TYPES
        if "template" in EXPECTED_NOTE_CONTRACTS[note_type]
    ]
    for template in EXPECTED_TEMPLATES:
        owners = [note_type for note_type, contract in EXPECTED_NOTE_CONTRACTS.items() if contract.get("template") == template]
        if actual_templates.count(template) != len(owners):
            errors.append(
                _error(
                    "SEMANTIC_TEMPLATE_OWNERSHIP",
                    "/note_types",
                    f"template ownership count is not one per canonical note type: {template}",
                    details={"template": template, "expected_owners": owners},
                )
            )


def _check_relations(blueprint: Mapping[str, Any], errors: list[dict[str, Any]]) -> None:
    registry = _as_mapping(blueprint.get("property_registry"))
    _check_exact_set(
        errors,
        registry,
        EXPECTED_PROPERTY_KEYS,
        locator="/property_registry",
        code="SEMANTIC_PROPERTY_REGISTRY_EXACT_SET",
    )
    relation_registry = _as_mapping(blueprint.get("relation_registry"))
    canonical = _as_mapping(relation_registry.get("canonical_predicates"))
    context = _as_mapping(relation_registry.get("context_predicates"))
    _check_exact_set(
        errors,
        canonical,
        EXPECTED_CANONICAL_RELATIONS,
        locator="/relation_registry/canonical_predicates",
        code="SEMANTIC_CANONICAL_RELATION_EXACT_SET",
    )
    _check_exact_set(
        errors,
        context,
        EXPECTED_CONTEXT_RELATIONS,
        locator="/relation_registry/context_predicates",
        code="SEMANTIC_CONTEXT_RELATION_EXACT_SET",
    )

    for predicate, expected in EXPECTED_CANONICAL_RELATIONS.items():
        actual = _as_mapping(canonical.get(predicate))
        _check_mapping_value(
            errors,
            tuple(actual.get("allowed_subject_types", ())),
            expected["subject"],
            locator=_path("relation_registry", "canonical_predicates", predicate, "allowed_subject_types"),
            code="SEMANTIC_RELATION_DIRECTION",
        )
        _check_mapping_value(
            errors,
            tuple(actual.get("allowed_object_types", ())),
            expected["object"],
            locator=_path("relation_registry", "canonical_predicates", predicate, "allowed_object_types"),
            code="SEMANTIC_RELATION_DIRECTION",
        )
        property_definition = _as_mapping(registry.get(predicate))
        if property_definition.get("obsidian_type") != "list":
            errors.append(
                _error(
                    "SEMANTIC_RELATION_PROPERTY_BINDING",
                    _path("property_registry", predicate, "obsidian_type"),
                    "canonical relation property must be a list",
                )
            )

    for predicate, expected_objects in EXPECTED_CONTEXT_RELATIONS.items():
        actual = _as_mapping(context.get(predicate))
        actual_objects = tuple(actual.get("allowed_object_types", ()))
        if actual_objects != expected_objects:
            errors.append(
                _error(
                    "SEMANTIC_CONTEXT_RELATION_DIRECTION",
                    _path("relation_registry", "context_predicates", predicate, "allowed_object_types"),
                    "context relation object type does not match the canonical contract",
                    details={"expected": expected_objects, "actual": actual_objects},
                )
            )
        if _as_mapping(registry.get(predicate)).get("obsidian_type") != "list":
            errors.append(
                _error(
                    "SEMANTIC_RELATION_PROPERTY_BINDING",
                    _path("property_registry", predicate, "obsidian_type"),
                    "context relation property must be a list",
                )
            )


def _check_actions(blueprint: Mapping[str, Any], errors: list[dict[str, Any]]) -> None:
    actions = _as_mapping(blueprint.get("actions"))
    pipeline_registry = _as_mapping(actions.get("pipeline_registry"))
    pipelines = _as_mapping(pipeline_registry.get("filenames"))
    _check_exact_set(
        errors,
        pipelines,
        EXPECTED_PIPELINES,
        locator="/actions/pipeline_registry/filenames",
        code="SEMANTIC_PIPELINE_REGISTRY_EXACT_SET",
    )
    for pipeline, filename in EXPECTED_PIPELINES.items():
        if pipelines.get(pipeline) != filename:
            errors.append(
                _error(
                    "SEMANTIC_PIPELINE_FILENAME",
                    _path("actions", "pipeline_registry", "filenames", pipeline),
                    "pipeline registry filename does not match the canonical contract",
                    details={"expected": filename, "actual": pipelines.get(pipeline)},
                )
            )

    routes = _as_mapping(actions.get("user_action_routes"))
    _check_exact_set(
        errors,
        routes,
        EXPECTED_ROUTES,
        locator="/actions/user_action_routes",
        code="SEMANTIC_USER_ACTION_ROUTE_EXACT_SET",
    )
    command_set = set(EXPECTED_COMMANDS)
    for route, expected_pipelines in EXPECTED_ROUTES.items():
        actual_route = _as_mapping(routes.get(route))
        actual_pipelines = tuple(actual_route.get("pipelines", ()))
        if len(actual_pipelines) != len(set(actual_pipelines)):
            errors.append(
                _error(
                    "SEMANTIC_ACTION_PIPELINE_MAPPING",
                    _path("actions", "user_action_routes", route, "pipelines"),
                    "user action contains a duplicate pipeline mapping",
                    details={"actual": actual_pipelines},
                )
            )
        if set(actual_pipelines) != set(expected_pipelines):
            errors.append(
                _error(
                    "SEMANTIC_ACTION_PIPELINE_MAPPING",
                    _path("actions", "user_action_routes", route, "pipelines"),
                    "user action pipeline mapping does not match the canonical contract",
                    details={"expected": expected_pipelines, "actual": actual_pipelines},
                )
            )
        expected_command = f"vaultctl ai {route}"
        if expected_command not in command_set:
            errors.append(
                _error(
                    "SEMANTIC_ACTION_COMMAND_BINDING",
                    _path("commands", expected_command),
                    "user action has no canonical command declaration",
                    details={"action": route, "command": expected_command},
                )
            )
        for pipeline in actual_pipelines:
            if pipeline not in EXPECTED_PIPELINES:
                errors.append(
                    _error(
                        "SEMANTIC_ACTION_PIPELINE_MAPPING",
                        _path("actions", "user_action_routes", route, "pipelines", pipeline),
                        "user action references an unknown pipeline",
                        details={"pipeline": pipeline},
                    )
                )

    bridge = _as_mapping(blueprint.get("bridge"))
    bridge_actions = _as_mapping(bridge.get("action_contracts"))
    _check_exact_set(
        errors,
        bridge_actions,
        EXPECTED_BRIDGE_ACTIONS,
        locator="/bridge/action_contracts",
        code="SEMANTIC_BRIDGE_ACTION_SUBSET",
    )
    for action, expected_schema in EXPECTED_BRIDGE_ACTIONS.items():
        actual = _as_mapping(bridge_actions.get(action))
        if actual.get("output_schema") != expected_schema:
            errors.append(
                _error(
                    "SEMANTIC_ACTION_OUTPUT_SCHEMA",
                    _path("bridge", "action_contracts", action, "output_schema"),
                    "bridge action output schema declaration does not match the canonical contract",
                    details={"expected": expected_schema, "actual": actual.get("output_schema")},
                )
            )
    if "normalize" in bridge_actions:
        errors.append(
            _error(
                "SEMANTIC_BRIDGE_ACTION_SUBSET",
                "/bridge/action_contracts/normalize",
                "normalize is a local route pipeline and must not be exposed as a bridge action",
            )
        )


def _check_bases(blueprint: Mapping[str, Any], errors: list[dict[str, Any]]) -> None:
    bases = _as_mapping(blueprint.get("bases"))
    _check_mapping_value(
        errors,
        bases.get("directory"),
        EXPECTED_BASE_DIRECTORY,
        locator="/bases/directory",
        code="SEMANTIC_BASE_PATH",
    )
    _check_exact_set(
        errors,
        bases.get("required"),
        EXPECTED_BASES,
        locator="/bases/required",
        code="SEMANTIC_BASE_EXACT_SET",
    )
    views = _as_mapping(bases.get("views"))
    _check_exact_set(
        errors,
        views,
        EXPECTED_BASES,
        locator="/bases/views",
        code="SEMANTIC_BASE_EXACT_SET",
    )
    for base_name, expected_views in EXPECTED_BASES.items():
        actual_base = _as_mapping(views.get(base_name))
        _check_exact_set(
            errors,
            actual_base,
            expected_views,
            locator=_path("bases", "views", base_name),
            code="SEMANTIC_BASE_VIEW_EXACT_SET",
        )
        for view_name, expected_query in expected_views.items():
            actual_query = _as_mapping(actual_base.get(view_name))
            _check_mapping_value(
                errors,
                dict(actual_query),
                expected_query,
                locator=_path("bases", "views", base_name, view_name),
                code="SEMANTIC_BASE_QUERY",
            )


def _check_dashboards(blueprint: Mapping[str, Any], errors: list[dict[str, Any]]) -> None:
    dashboards = _as_mapping(blueprint.get("dashboards"))
    _check_exact_set(
        errors,
        dashboards,
        EXPECTED_DASHBOARD_SECTIONS,
        locator="/dashboards",
        code="SEMANTIC_DASHBOARD_EXACT_SET",
    )
    for dashboard_name, expected_sections in EXPECTED_DASHBOARD_SECTIONS.items():
        dashboard = _as_mapping(dashboards.get(dashboard_name))
        sections = dashboard.get("sections")
        actual_section_names = [
            _as_mapping(section).get("name")
            for section in _items(sections)
        ]
        _check_exact_set(
            errors,
            actual_section_names,
            expected_sections,
            locator=_path("dashboards", dashboard_name, "sections"),
            code="SEMANTIC_DASHBOARD_SECTION_EXACT_SET",
        )
        actual_by_name = {
            section.get("name"): section
            for section in (_as_mapping(item) for item in _items(sections))
            if section.get("name") is not None
        }
        expected_sources = EXPECTED_DASHBOARD_SOURCES[dashboard_name]
        for section_name in expected_sections:
            actual_section = _as_mapping(actual_by_name.get(section_name))
            expected_source = expected_sources.get(section_name)
            locator = _path("dashboards", dashboard_name, "sections", section_name)
            actual_source = actual_section.get("source")
            actual_view = actual_section.get("view")
            actual_limit = actual_section.get("limit")
            if expected_source is None:
                if any(value is not None for value in (actual_source, actual_view, actual_limit)):
                    errors.append(
                        _error(
                            "SEMANTIC_DASHBOARD_SOURCE_VIEW",
                            locator,
                            "dashboard section unexpectedly declares a Base source/view/limit",
                            details={
                                "expected": {"source": None, "view": None, "limit": None},
                                "actual": {
                                    "source": actual_source,
                                    "view": actual_view,
                                    "limit": actual_limit,
                                },
                            },
                        )
                    )
                continue
            expected_mapping = {
                "source": expected_source[0],
                "view": expected_source[1],
                "limit": expected_source[2],
            }
            actual_mapping = {
                "source": actual_source,
                "view": actual_view,
                "limit": actual_limit,
            }
            _check_mapping_value(
                errors,
                actual_mapping,
                expected_mapping,
                locator=locator,
                code="SEMANTIC_DASHBOARD_SOURCE_VIEW",
            )
            if expected_source[0] not in EXPECTED_BASES or expected_source[1] not in EXPECTED_BASES.get(
                expected_source[0], {}
            ):
                errors.append(
                    _error(
                        "SEMANTIC_DASHBOARD_SOURCE_VIEW",
                        locator,
                        "dashboard source/view does not reference a declared Base view",
                        details={"source": expected_source[0], "view": expected_source[1]},
                    )
                )


def _check_projection(blueprint: Mapping[str, Any], errors: list[dict[str, Any]]) -> None:
    projection = _as_mapping(blueprint.get("projection"))
    for field, expected in EXPECTED_PROJECTION_TOP_LEVEL.items():
        _check_mapping_value(
            errors,
            projection.get(field),
            expected,
            locator=_path("projection", field),
            code="SEMANTIC_PROJECTION_PATH",
        )
    _check_mapping_value(
        errors,
        dict(_as_mapping(projection.get("schemas"))),
        EXPECTED_PROJECTION_SCHEMAS,
        locator="/projection/schemas",
        code="SEMANTIC_PROJECTION_PATH",
    )

    contracts = _as_mapping(projection.get("record_contracts"))
    note = _as_mapping(contracts.get("note"))
    _check_mapping_value(
        errors,
        note.get("additional_properties"),
        False,
        locator="/projection/record_contracts/note/additional_properties",
        code="SEMANTIC_PROJECTION_REQUIRED_FIELDS",
    )
    _check_mapping_value(
        errors,
        note.get("required"),
        list(EXPECTED_NOTE_RECORD_FIELDS),
        locator="/projection/record_contracts/note/required",
        code="SEMANTIC_PROJECTION_REQUIRED_FIELDS",
    )
    _check_mapping_value(
        errors,
        dict(_as_mapping(note.get("fields"))),
        EXPECTED_NOTE_RECORD_FIELDS,
        locator="/projection/record_contracts/note/fields",
        code="SEMANTIC_PROJECTION_REQUIRED_FIELDS",
    )
    _check_mapping_value(
        errors,
        note.get("promoted_frontmatter_fields"),
        ["schema_version", "id", "type", "title", "status", "created", "modified"],
        locator="/projection/record_contracts/note/promoted_frontmatter_fields",
        code="SEMANTIC_PROJECTION_REQUIRED_FIELDS",
    )
    _check_mapping_value(
        errors,
        dict(_as_mapping(note.get("properties_contract"))),
        {
            "key_order": "unicode_codepoint_lexicographic_after_nfc",
            "values": "validated_flat_frontmatter_values",
        },
        locator="/projection/record_contracts/note/properties_contract",
        code="SEMANTIC_PROJECTION_ORDERING",
    )
    wikilink = _as_mapping(note.get("wikilink_item"))
    _check_mapping_value(
        errors,
        wikilink.get("additional_properties"),
        False,
        locator="/projection/record_contracts/note/wikilink_item/additional_properties",
        code="SEMANTIC_PROJECTION_NULLABILITY",
    )
    _check_mapping_value(
        errors,
        wikilink.get("required"),
        ["raw_target", "resolved_id", "resolved_path", "locator"],
        locator="/projection/record_contracts/note/wikilink_item/required",
        code="SEMANTIC_PROJECTION_NULLABILITY",
    )
    _check_mapping_value(
        errors,
        dict(_as_mapping(wikilink.get("fields"))),
        EXPECTED_WIKILINK_FIELDS,
        locator="/projection/record_contracts/note/wikilink_item/fields",
        code="SEMANTIC_PROJECTION_NULLABILITY",
    )
    _check_mapping_value(
        errors,
        wikilink.get("unresolved_link"),
        "preserve_raw_target_with_null_resolved_fields",
        locator="/projection/record_contracts/note/wikilink_item/unresolved_link",
        code="SEMANTIC_PROJECTION_NULLABILITY",
    )
    _check_mapping_value(
        errors,
        note.get("content_hash_domain"),
        "sha256_of_exact_utf8_lf_source_markdown_bytes",
        locator="/projection/record_contracts/note/content_hash_domain",
        code="SEMANTIC_PROJECTION_HASH_DOMAIN",
    )

    edge = _as_mapping(contracts.get("edge"))
    _check_mapping_value(
        errors,
        edge.get("additional_properties"),
        False,
        locator="/projection/record_contracts/edge/additional_properties",
        code="SEMANTIC_PROJECTION_REQUIRED_FIELDS",
    )
    _check_mapping_value(
        errors,
        edge.get("required"),
        list(EXPECTED_EDGE_RECORD_FIELDS),
        locator="/projection/record_contracts/edge/required",
        code="SEMANTIC_PROJECTION_REQUIRED_FIELDS",
    )
    _check_mapping_value(
        errors,
        dict(_as_mapping(edge.get("fields"))),
        EXPECTED_EDGE_RECORD_FIELDS,
        locator="/projection/record_contracts/edge/fields",
        code="SEMANTIC_PROJECTION_REQUIRED_FIELDS",
    )
    _check_mapping_value(
        errors,
        dict(_as_mapping(edge.get("nullability"))),
        {"object_locator": "text_or_null"},
        locator="/projection/record_contracts/edge/nullability",
        code="SEMANTIC_PROJECTION_NULLABILITY",
    )
    _check_mapping_value(
        errors,
        edge.get("duplicate_policy"),
        "duplicate_canonical_edge_tuple_is_validation_error",
        locator="/projection/record_contracts/edge/duplicate_policy",
        code="SEMANTIC_PROJECTION_NULLABILITY",
    )

    serialization = _as_mapping(projection.get("serialization"))
    _check_mapping_value(
        errors,
        dict(serialization),
        EXPECTED_PROJECTION_SERIALIZATION,
        locator="/projection/serialization",
        code="SEMANTIC_PROJECTION_SERIALIZATION",
    )
    _check_mapping_value(
        errors,
        serialization.get("record_sort"),
        EXPECTED_PROJECTION_SERIALIZATION["record_sort"],
        locator="/projection/serialization/record_sort",
        code="SEMANTIC_PROJECTION_ORDERING",
    )
    _check_mapping_value(
        errors,
        projection.get("deterministic_sort"),
        True,
        locator="/projection/deterministic_sort",
        code="SEMANTIC_PROJECTION_ORDERING",
    )
    _check_mapping_value(
        errors,
        projection.get("byte_stable_for_same_inputs"),
        True,
        locator="/projection/byte_stable_for_same_inputs",
        code="SEMANTIC_PROJECTION_ORDERING",
    )
    _check_mapping_value(
        errors,
        projection.get("publish_protocol"),
        EXPECTED_PUBLISH_PROTOCOL,
        locator="/projection/publish_protocol",
        code="SEMANTIC_PROJECTION_SERIALIZATION",
    )
    _check_mapping_value(
        errors,
        projection.get("reader_contract"),
        "read_pointer_once_and_use_one_generation_only",
        locator="/projection/reader_contract",
        code="SEMANTIC_PROJECTION_SERIALIZATION",
    )
    for field, expected in {
        "reject_mixed_generation_or_digest_mismatch": True,
        "modifies_vault": False,
        "canonical_edges_only": True,
    }.items():
        _check_mapping_value(
            errors,
            projection.get(field),
            expected,
            locator=_path("projection", field),
            code="SEMANTIC_PROJECTION_SERIALIZATION",
        )
    _check_mapping_value(
        errors,
        projection.get("canonical_edge_fields"),
        EXPECTED_CANONICAL_EDGE_FIELDS,
        locator="/projection/canonical_edge_fields",
        code="SEMANTIC_PROJECTION_REQUIRED_FIELDS",
    )
    _check_mapping_value(
        errors,
        projection.get("excluded_from_byte_stable_projection"),
        EXPECTED_PROJECTION_EXCLUSIONS,
        locator="/projection/excluded_from_byte_stable_projection",
        code="SEMANTIC_PROJECTION_ORDERING",
    )


def _check_retrieval(blueprint: Mapping[str, Any], errors: list[dict[str, Any]]) -> None:
    retrieval = _as_mapping(blueprint.get("retrieval"))
    _check_mapping_value(
        errors,
        retrieval.get("phases"),
        EXPECTED_RETRIEVAL_PHASES,
        locator="/retrieval/phases",
        code="SEMANTIC_RETRIEVAL_PHASE_ORDER",
    )
    _check_mapping_value(
        errors,
        dict(_as_mapping(retrieval.get("graph_expansion"))),
        EXPECTED_RETRIEVAL_GRAPH,
        locator="/retrieval/graph_expansion",
        code="SEMANTIC_RETRIEVAL_GRAPH_BOUNDS",
    )
    _check_mapping_value(
        errors,
        dict(_as_mapping(retrieval.get("corpus"))),
        EXPECTED_RETRIEVAL_CORPUS,
        locator="/retrieval/corpus",
        code="SEMANTIC_RETRIEVAL_CORPUS_POLICY",
    )
    _check_mapping_value(
        errors,
        retrieval.get("policy_gate_points"),
        [
            "before_remote_embedding_or_indexing",
            "during_candidate_fusion_and_graph_expansion",
            "immediately_before_model_context",
        ],
        locator="/retrieval/policy_gate_points",
        code="SEMANTIC_RETRIEVAL_POLICY_GATES",
    )
    _check_mapping_value(
        errors,
        dict(_as_mapping(retrieval.get("remote_embedding"))),
        EXPECTED_RETRIEVAL_REMOTE_EMBEDDING,
        locator="/retrieval/remote_embedding",
        code="SEMANTIC_RETRIEVAL_PRIVACY_POLICY",
    )
    _check_mapping_value(
        errors,
        dict(_as_mapping(retrieval.get("local_embedding"))),
        {"deny_forbidden": True},
        locator="/retrieval/local_embedding",
        code="SEMANTIC_RETRIEVAL_PRIVACY_POLICY",
    )
    _check_mapping_value(
        errors,
        retrieval.get("filter_before_model"),
        ["scope", "path", "type", "sensitivity", "ai_policy"],
        locator="/retrieval/filter_before_model",
        code="SEMANTIC_RETRIEVAL_FILTER_ORDER",
    )
    _check_mapping_value(
        errors,
        retrieval.get("frozen_candidate_fields"),
        [
            "query_sha256",
            "policy_decision_sha256",
            "index_generation_id",
            "note_id",
            "path",
            "content_hash",
            "chunk_id",
            "chunk_hash",
            "chunk_locator",
            "retrieval_reason",
            "lexical_score_and_rank",
            "vector_score_and_rank",
            "rrf_parameter_and_rank",
            "graph_path",
            "parser_and_chunker_version",
            "embedding_provider_model_dimension_and_artifact_digest",
            "indexer_version",
            "retrieval_config_sha256",
        ],
        locator="/retrieval/frozen_candidate_fields",
        code="SEMANTIC_RETRIEVAL_CANDIDATE_FIELDS",
    )
    _check_mapping_value(
        errors,
        dict(_as_mapping(retrieval.get("staleness"))),
        EXPECTED_RETRIEVAL_STALENESS,
        locator="/retrieval/staleness",
        code="SEMANTIC_RETRIEVAL_STALENESS",
    )
    _check_mapping_value(
        errors,
        retrieval.get("answer_requires"),
        ["note_id", "path", "locator", "evidence_hash", "uncertainty"],
        locator="/retrieval/answer_requires",
        code="SEMANTIC_RETRIEVAL_ANSWER_CONTRACT",
    )
    _check_mapping_value(
        errors,
        retrieval.get("graphrag_default"),
        "deferred",
        locator="/retrieval/graphrag_default",
        code="SEMANTIC_RETRIEVAL_GRAPH_BOUNDS",
    )


def _check_transactions(blueprint: Mapping[str, Any], errors: list[dict[str, Any]]) -> None:
    quickadd = _as_mapping(blueprint.get("quickadd_choices"))
    new_project = _as_mapping(quickadd.get("NEW_PROJECT"))
    for field, expected in {
        "template": "T20_Project.md",
        "target_pattern": "20_Projects/title/title.md",
        "create_sibling_directories": ["Working", "Artifacts"],
        "create_project_moc": False,
    }.items():
        _check_mapping_value(
            errors,
            new_project.get(field),
            expected,
            locator=_path("quickadd_choices", "NEW_PROJECT", field),
            code="SEMANTIC_PROJECT_BUNDLE_CARDINALITY",
        )

    note_types = _as_mapping(blueprint.get("note_types"))
    for note_type in ("project_note", "artifact"):
        constraints = _as_mapping(_as_mapping(note_types.get(note_type)).get("field_constraints"))
        _check_mapping_value(
            errors,
            dict(_as_mapping(constraints.get("projects"))),
            {
                "min_items": 1,
                "max_items": 1,
                "must_resolve_to_parent_project_bundle": True,
            },
            locator=_path("note_types", note_type, "field_constraints", "projects"),
            code="SEMANTIC_PROJECT_BUNDLE_CARDINALITY",
        )

    llm = _as_mapping(blueprint.get("llm"))
    draft_note = _as_mapping(llm.get("draft_note"))
    _check_mapping_value(
        errors,
        draft_note.get("project_target_creates_deterministic_bundle_directories"),
        True,
        locator="/llm/draft_note/project_target_creates_deterministic_bundle_directories",
        code="SEMANTIC_PROJECT_BUNDLE_CARDINALITY",
    )
    capture_finalize = _as_mapping(_as_mapping(llm.get("triage")).get("capture_finalize"))
    _check_mapping_value(
        errors,
        capture_finalize.get("transaction"),
        EXPECTED_CAPTURE_FINALIZE_TRANSACTION,
        locator="/llm/triage/capture_finalize/transaction",
        code="SEMANTIC_CAPTURE_FINALIZE_TRANSACTION",
    )


def _check_exact_registries(blueprint: Mapping[str, Any], errors: list[dict[str, Any]]) -> None:
    for field, expected, code in (
        ("commands", EXPECTED_COMMANDS, "SEMANTIC_COMMAND_REGISTRY_EXACT_SET"),
        ("implementation_phases", EXPECTED_PHASES, "SEMANTIC_PHASE_REGISTRY_EXACT_SET"),
        ("acceptance_scenarios", EXPECTED_ACCEPTANCE_SCENARIOS, "SEMANTIC_ACCEPTANCE_REGISTRY_EXACT_SET"),
    ):
        _check_exact_set(errors, blueprint.get(field), expected, locator=f"/{field}", code=code)


def _check_bridge(blueprint: Mapping[str, Any], errors: list[dict[str, Any]]) -> None:
    bridge = _as_mapping(blueprint.get("bridge"))
    states = bridge.get("states")
    _check_exact_set(
        errors,
        states,
        EXPECTED_BRIDGE_STATES,
        locator="/bridge/states",
        code="SEMANTIC_BRIDGE_STATE_EXACT_SET",
    )
    state_set = set(EXPECTED_BRIDGE_STATES)
    transitions = _as_mapping(bridge.get("allowed_transitions"))
    _check_exact_set(
        errors,
        transitions,
        EXPECTED_BRIDGE_TRANSITIONS,
        locator="/bridge/allowed_transitions",
        code="SEMANTIC_BRIDGE_TRANSITION_EXACT_SET",
    )
    for source, expected_targets in EXPECTED_BRIDGE_TRANSITIONS.items():
        actual_targets = tuple(_as_mapping(transitions).get(source, ()))
        if set(actual_targets) != set(expected_targets):
            errors.append(
                _error(
                    "SEMANTIC_BRIDGE_TRANSITION",
                    _path("bridge", "allowed_transitions", source),
                    "bridge transition targets do not match the canonical state machine",
                    details={"expected": expected_targets, "actual": actual_targets},
                )
            )
        for target in actual_targets:
            if target not in state_set:
                errors.append(
                    _error(
                        "SEMANTIC_BRIDGE_TRANSITION_CLOSURE",
                        _path("bridge", "allowed_transitions", source, target),
                        "bridge transition target is not a declared state",
                        details={"target": target},
                    )
                )
        if source in actual_targets:
            errors.append(
                _error(
                    "SEMANTIC_BRIDGE_TRANSITION_CLOSURE",
                    _path("bridge", "allowed_transitions", source),
                    "bridge state machine must not contain a self-transition",
                )
            )

    mapping = _as_mapping(bridge.get("state_mapping"))
    _check_exact_set(
        errors,
        mapping,
        EXPECTED_STATE_MAPPING,
        locator="/bridge/state_mapping",
        code="SEMANTIC_BRIDGE_STATE_MAPPING_EXACT_SET",
    )
    for state, (public_status, runtime_path) in EXPECTED_STATE_MAPPING.items():
        actual = _as_mapping(mapping.get(state))
        expected = {"public_status": public_status, "runtime": runtime_path}
        actual_projection = {"public_status": actual.get("public_status"), "runtime": actual.get("runtime")}
        if actual_projection != expected:
            errors.append(
                _error(
                    "SEMANTIC_BRIDGE_STATE_RUNTIME_MAPPING",
                    _path("bridge", "state_mapping", state),
                    "bridge public status/runtime mapping does not match the canonical contract",
                    details={"expected": expected, "actual": actual_projection},
                )
            )
        if runtime_path not in RUNTIME_DIRECTORIES:
            errors.append(
                _error(
                    "SEMANTIC_BRIDGE_RUNTIME_PATH",
                    _path("bridge", "state_mapping", state, "runtime"),
                    "bridge state maps to an undeclared runtime directory",
                    details={"runtime": runtime_path},
                )
            )

    reachable = {"committed_request"}
    changed = True
    while changed:
        changed = False
        for source, targets in EXPECTED_BRIDGE_TRANSITIONS.items():
            if source in reachable:
                before = len(reachable)
                reachable.update(targets)
                changed = len(reachable) != before
    unreachable = state_set - reachable
    for state in sorted(unreachable):
        errors.append(
            _error(
                "SEMANTIC_BRIDGE_TRANSITION_CLOSURE",
                _path("bridge", "states", state),
                "bridge state is unreachable from committed_request",
                details={"state": state},
            )
        )


def validate_semantic_contract(blueprint: Mapping[str, Any]) -> SemanticValidation:
    """Run the C03 registry and C04 consumer/lifecycle semantic checks."""

    errors: list[dict[str, Any]] = []
    _check_note_paths_and_templates(blueprint, errors)
    _check_relations(blueprint, errors)
    _check_actions(blueprint, errors)
    _check_exact_registries(blueprint, errors)
    _check_bridge(blueprint, errors)
    _check_bases(blueprint, errors)
    _check_dashboards(blueprint, errors)
    _check_projection(blueprint, errors)
    _check_retrieval(blueprint, errors)
    _check_transactions(blueprint, errors)
    errors.sort(key=lambda item: (item["locator"], item["code"], item["message"]))
    return SemanticValidation(tuple(errors))
