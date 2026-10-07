"""Cross-document semantic checks for the current KnowledgeOS Blueprint."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

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
    "cssclasses",
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

EXPECTED_COMMANDS = (
    "vaultctl version",
    "vaultctl bootstrap",
    "vaultctl configure",
    "vaultctl doctor",
    "vaultctl reconcile",
    "vaultctl git status",
    "vaultctl plugins audit",
    "vaultctl foundation source",
    "vaultctl foundation check",
    "vaultctl blueprint validate",
    "vaultctl schema export",
    "vaultctl vault-artifacts check",
    "vaultctl index build",
    "vaultctl index verify",
    "vaultctl index export",
    "vaultctl note validate",
    "vaultctl repair plan",
    "vaultctl repair apply",
    "vaultctl receipts verify",
    "vaultctl operation check",
    "vaultctl operation status",
    "vaultctl operation recover",
    "vaultctl work validate",
    "vaultctl storage archive",
    "vaultctl storage core-cutover",
)

EXPECTED_BASE_DIRECTORY = "99_System/Bases"

EXPECTED_BASES: dict[str, dict[str, dict[str, Any]]] = {
    "Journal.base": {
        "Today Focus": {
            "filters": {"type": "daily", "period_start": "today"},
            "sort": ["file_name_desc"],
            "limit": 1,
            "columns": ["file.link", "today_focus"],
        },
        "Open Reviews": {
            "filters": {"type_in": ["weekly", "monthly"], "status": "open"},
            "sort": ["period_start_desc", "file_name_asc"],
            "limit": 2,
            "columns": ["file.link", "type", "period_end"],
        },
        "Due Areas": {
            "filters": {
                "type": "area",
                "status_in": ["active", "paused"],
                "next_review_on_or_before_today": True,
            },
            "sort": ["next_review_asc", "file_name_asc"],
            "limit": 3,
            "columns": ["file.link", "next_review", "review_cadence"],
        },
    },
    "Compass.base": {
        "Signals": {
            "filters": {
                "or": [
                    {
                        "and": [
                            {"type_in": ["knowledge", "source"]},
                            {"contradicts_nonempty": True},
                        ]
                    },
                    {
                        "and": [
                            {"type": "knowledge"},
                            {"confidence_in": ["low", "unknown"]},
                        ]
                    },
                    {
                        "and": [
                            {"type": "idea"},
                            {"status_in": ["seed", "incubating", "testing"]},
                            {"projects_empty": True},
                            {"related_empty": True},
                            {"raises_empty": True},
                        ]
                    },
                ]
            },
            "sort": ["file_mtime_desc", "file_name_asc"],
            "limit": 6,
            "columns": ["file.link", "type", "status", "confidence", "contradicts", "projects", "related"],
        },
        "Tensions": {
            "filters": {
                "or": [
                    {"and": [{"type": "knowledge"}, {"contradicts_nonempty": True}]},
                    {"and": [{"type": "source"}, {"contradicts_nonempty": True}]},
                ]
            },
            "sort": ["file_mtime_desc", "file_name_asc"],
            "limit": 10,
            "columns": ["file.link", "type", "confidence", "contradicts"],
        },
        "Research Gaps": {
            "filters": {
                "and": [
                    {"type": "question"},
                    {"status_in": ["open", "deciding"]},
                    {"question_kind_in": ["research", "problem"]},
                    {"projects_empty": True},
                ]
            },
            "sort": ["decision_by_asc_nulls_last", "priority_high_to_low", "file_mtime_desc", "file_name_asc"],
            "limit": 10,
            "columns": ["file.link", "question_kind", "projects", "priority", "decision_by"],
        },
        "Unconnected Ideas": {
            "filters": {
                "and": [
                    {"type": "idea"},
                    {"status_in": ["seed", "incubating", "testing"]},
                    {"projects_empty": True},
                    {"related_empty": True},
                    {"raises_empty": True},
                ]
            },
            "sort": ["file_mtime_desc", "file_name_asc"],
            "limit": 10,
            "columns": ["file.link", "status", "possibility", "projects", "related", "raises"],
        },
        "Low Confidence": {
            "filters": {
                "and": [
                    {"type": "knowledge"},
                    {"confidence_in": ["low", "unknown"]},
                ]
            },
            "sort": ["file_mtime_desc", "file_name_asc"],
            "limit": 10,
            "columns": ["file.link", "confidence", "supports", "contradicts", "applies_to"],
        },
        "Project Bridges": {
            "filters": {
                "and": [
                    {"type": "project"},
                    {"status_in": ["active", "blocked"]},
                    {"implements_nonempty": True},
                ]
            },
            "sort": ["focus_rank_asc", "priority_high_to_low", "target_date_asc_nulls_last", "file_name_asc"],
            "limit": 10,
            "columns": ["file.link", "status", "next_action", "implements", "target_date"],
        },
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
        "Active": {
            "filters": {"type": "project", "status_in": ["active", "blocked"]},
            "sort": [
                "focus_rank_asc",
                "priority_high_to_low",
                "target_date_asc_nulls_last",
                "file_name_asc",
            ],
            "limit": 100,
            "columns": ["file.link", "status", "next_action", "target_date"],
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
            },
            "sort": ["decision_by_asc_nulls_last", "priority_high_to_low", "file_name_asc"],
            "limit": 5,
            "columns": ["file.link", "decision", "decision_by", "projects", "priority"],
        },
        "Research Questions": {
            "filters": {
                "type": "question",
                "status_in": ["open", "deciding"],
                "question_kind_in": ["research", "problem"],
            },
            "sort": [
                "decision_by_asc_nulls_last",
                "priority_high_to_low",
                "file_mtime_desc",
                "file_name_asc",
            ],
            "limit": 3,
            "columns": ["file.link", "question_kind", "projects"],
        },
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
        },
        "Active Maps": {
            "filters": {"type": "moc", "status": "active"},
            "sort": ["file_mtime_desc", "file_name_asc"],
            "limit": 3,
            "columns": ["file.link", "scope", "file.mtime"],
        },
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
            "tasks",
            "inbox",
            "ai_review",
            "projects",
            "decisions",
            "review_pulse",
            "compass",
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
    "inbox": ("Inbox.base", "Unprocessed", 10),
        "projects": ("Projects.base", "Now", 3),
        "decisions": ("Decisions.base", "Open", 5),
    },
    "mobile": {
        "focus_projects_and_next_actions": ("Projects.base", "Mobile", 3),
        "needs_desktop_review": ("Inbox.base", "Mobile Review", 5),
        "ai_results": ("Review.base", "Mobile Results", 5),
    },
}

EXPECTED_HOME_CAPTURE_CONTRACT = {
    "visible_on_home": False,
    "actions": ["CAPTURE_THOUGHT", "NEW_IDEA", "NEW_PROJECT", "NEW_QUESTION", "NEW_KNOWLEDGE"],
    "hotkeys": ["option_command_c", "option_command_j", "option_command_p", "option_command_q", "option_command_k"],
    "authority": "quickadd_profile_and_blueprint_choice_registry",
}

EXPECTED_HOME_MULTI_VIEW_SECTIONS = {
    "review_pulse": {
        "views": [
            {"source": "Sources.base", "view": "Reading queue", "limit": 10},
            {"source": "Journal.base", "view": "Open Reviews", "limit": 2},
        ],
        "priority": "P1",
    },
    "compass": {
        "views": [
            {"source": "Compass.base", "view": "Signals", "limit": 6},
            {"source": "Compass.base", "view": "Tensions", "limit": 10},
        ],
        "priority": "P1",
    },
    "ai_review": {
        "views": [{"source": "Review.base", "view": "PendingOrConflict", "limit": 10}],
        "priority": "P1",
    },
}

EXPECTED_HOME_SECTION_CONTRACT = {
    "tasks": {"task_query": "Home.md#tasks[1]", "layout_span": 12, "priority": "P0"},
    "inbox": {"layout_span": 12, "priority": "P0"},
    "ai_review": {"layout_span": 12, "priority": "P1"},
    "projects": {
        "layout_span": 6,
        "status_in": ["active", "blocked"],
        "sort": ["focus_rank_asc", "priority_high_to_low", "target_date_asc_nulls_last", "file_name_asc"],
    },
    "decisions": {
        "layout_span": 6,
        "sort": ["decision_by_asc_nulls_last", "priority_high_to_low", "file_name_asc"],
    },
    "review_pulse": {
        "layout_span": 6,
        "priority": "P1",
    },
    "compass": {"layout_span": 6, "priority": "P1"},
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

EXPECTED_RETRIEVAL_PHASES = [
    "native_exact_and_links",
    "lexical_fts",
    "bounded_typed_graph_expansion",
]

EXPECTED_RETRIEVAL_GRAPH = {
    "default_hops": 1,
    "maximum_hops": 1,
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
        ".obsidian-*/**",
        "99_System/**",
        "01_AI_Review/Rejected/**",
        "01_AI_Review/Expired/**",
    ],
    "review_corpus_separate": ["01_AI_Review/Pending/**", "01_AI_Review/Conflict/**"],
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

        if dashboard_name == "home":
            for field, expected in {
                "path": "Home.md",
                "type": "home",
                "audience": "desktop",
                "layout": "twelve_column_five_row",
                "cssclass": "knowledgeos-home",
                "toolbar_policy": "compact_navigation_only",
            }.items():
                _check_mapping_value(
                    errors,
                    dashboard.get(field),
                    expected,
                    locator=_path("dashboards", "home", field),
                    code="SEMANTIC_HOME_LAYOUT_CONTRACT",
                )
            _check_mapping_value(
                errors,
                dict(_as_mapping(dashboard.get("capture_contract"))),
                EXPECTED_HOME_CAPTURE_CONTRACT,
                locator="/dashboards/home/capture_contract",
                code="SEMANTIC_HOME_CAPTURE_CONTRACT",
            )
            for section_name, expected in EXPECTED_HOME_MULTI_VIEW_SECTIONS.items():
                section = _as_mapping(actual_by_name.get(section_name))
                for field, value in expected.items():
                    _check_mapping_value(
                        errors,
                        section.get(field),
                        value,
                        locator=_path("dashboards", "home", "sections", section_name, field),
                        code="SEMANTIC_HOME_SECTION_CONTRACT",
                    )
            for section_name, expected in EXPECTED_HOME_SECTION_CONTRACT.items():
                section = _as_mapping(actual_by_name.get(section_name))
                for field, value in expected.items():
                    _check_mapping_value(
                        errors,
                        section.get(field),
                        value,
                        locator=_path("dashboards", "home", "sections", section_name, field),
                        code="SEMANTIC_HOME_SECTION_CONTRACT",
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
            "before_candidate_selection",
            "during_link_expansion",
            "immediately_before_context_return",
        ],
        locator="/retrieval/policy_gate_points",
        code="SEMANTIC_RETRIEVAL_POLICY_GATES",
    )
    _check_mapping_value(
        errors,
        retrieval.get("filter_before_context"),
        ["scope", "path", "type", "sensitivity", "ai_policy"],
        locator="/retrieval/filter_before_context",
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
            "graph_path",
            "parser_and_chunker_version",
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



def _check_exact_registries(blueprint: Mapping[str, Any], errors: list[dict[str, Any]]) -> None:
    _check_exact_set(errors, blueprint.get("commands"), EXPECTED_COMMANDS,
                     locator="/commands", code="SEMANTIC_COMMAND_REGISTRY_EXACT_SET")


def validate_semantic_contract(blueprint: Mapping[str, Any]) -> SemanticValidation:
    """Run the C03 registry and C04 consumer/lifecycle semantic checks."""

    errors: list[dict[str, Any]] = []
    _check_note_paths_and_templates(blueprint, errors)
    _check_relations(blueprint, errors)
    _check_exact_registries(blueprint, errors)
    _check_bases(blueprint, errors)
    _check_dashboards(blueprint, errors)
    _check_projection(blueprint, errors)
    _check_retrieval(blueprint, errors)
    _check_transactions(blueprint, errors)
    errors.sort(key=lambda item: (item["locator"], item["code"], item["message"]))
    return SemanticValidation(tuple(errors))
