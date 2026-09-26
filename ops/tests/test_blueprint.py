from __future__ import annotations

import copy
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from vaultops.blueprint import validate_blueprint
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def _mutation_root(tmp_path: Path, mutate: Callable[[dict[str, Any]], None]) -> Path:
    root = tmp_path / "control"
    (root / "blueprint").mkdir(parents=True)
    value = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")
    assert isinstance(value, dict)
    mutated = copy.deepcopy(value)
    mutate(mutated)
    (root / "blueprint/blueprint.yaml").write_text(
        yaml.safe_dump(mutated, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    shutil.copyfile(
        CONTROL_ROOT / "blueprint/blueprint.schema.json",
        root / "blueprint/blueprint.schema.json",
    )
    shutil.copyfile(
        CONTROL_ROOT / "blueprint/CHECKSUMS.sha256",
        root / "blueprint/CHECKSUMS.sha256",
    )
    return root


def _error_codes_and_locators(root: Path) -> tuple[set[str], set[str]]:
    result = validate_blueprint(root)
    assert not result.passed
    errors = result.report["errors"]
    return (
        {error["code"] for error in errors},
        {error["locator"] for error in errors},
    )


def _semantic_error_codes_and_locators(root: Path) -> tuple[set[str], set[str]]:
    result = validate_blueprint(root)
    assert not result.passed
    assert result.report["validation"]["json_schema"] == "PASS"
    assert result.report["validation"]["semantic_validation"] == "FAIL"
    errors = result.report["semantic_errors"]
    return (
        {error["code"] for error in errors},
        {error["locator"] for error in errors},
    )


def test_canonical_blueprint_passes_json_schema_and_semantic_gate_one() -> None:
    result = validate_blueprint(CONTROL_ROOT)

    assert result.passed
    assert result.report["errors"] == []
    assert result.report["validation"]["json_schema"] == "PASS"
    assert result.report["validation"]["semantic_validation"] == "PASS"
    assert result.report["semantic_errors"] == []


def test_missing_top_level_key_has_stable_reason_and_locator(tmp_path: Path) -> None:
    codes, locators = _error_codes_and_locators(
        _mutation_root(tmp_path, lambda blueprint: blueprint.pop("status"))
    )

    assert "JSON_SCHEMA_REQUIRED" in codes
    assert "/status" in locators


def test_extra_top_level_key_has_stable_reason_and_locator(tmp_path: Path) -> None:
    codes, locators = _error_codes_and_locators(
        _mutation_root(tmp_path, lambda blueprint: blueprint.update({"unexpected": True}))
    )

    assert "JSON_SCHEMA_ADDITIONAL_PROPERTY" in codes
    assert "/unexpected" in locators


def test_const_mutation_has_stable_reason_and_locator(tmp_path: Path) -> None:
    codes, locators = _error_codes_and_locators(
        _mutation_root(tmp_path, lambda blueprint: blueprint.update({"contract_id": "wrong"}))
    )

    assert "JSON_SCHEMA_CONST" in codes
    assert "/contract_id" in locators


def test_cardinality_mutation_has_stable_reason_and_locator(tmp_path: Path) -> None:
    def remove_command(blueprint: dict[str, Any]) -> None:
        commands = blueprint["commands"]
        assert isinstance(commands, list)
        commands.pop()

    codes, locators = _error_codes_and_locators(_mutation_root(tmp_path, remove_command))

    assert "JSON_SCHEMA_MIN_ITEMS" in codes
    assert "/commands" in locators


def test_type_mutation_has_stable_reason_and_locator(tmp_path: Path) -> None:
    def replace_string_with_list(blueprint: dict[str, Any]) -> None:
        contract_validation = blueprint["contract_validation"]
        assert isinstance(contract_validation, dict)
        contract_validation["post_bootstrap_operational_source_of_truth"] = ["wrong"]

    codes, locators = _error_codes_and_locators(
        _mutation_root(tmp_path, replace_string_with_list)
    )

    assert "JSON_SCHEMA_TYPE" in codes
    assert "/contract_validation/post_bootstrap_operational_source_of_truth" in locators


def test_path_type_template_mismatch_is_a_semantic_error(tmp_path: Path) -> None:
    def change_path(blueprint: dict[str, Any]) -> None:
        note_types = blueprint["note_types"]
        assert isinstance(note_types, dict)
        note_types["project"]["path_globs"] = ["30_Areas/**/*.md"]

    codes, locators = _semantic_error_codes_and_locators(_mutation_root(tmp_path, change_path))

    assert "SEMANTIC_NOTE_PATH_PATTERN" in codes
    assert "/note_types/project/path_globs" in locators


def test_property_registry_rename_is_a_semantic_error(tmp_path: Path) -> None:
    def rename_property(blueprint: dict[str, Any]) -> None:
        registry = blueprint["property_registry"]
        assert isinstance(registry, dict)
        registry["renamed_today_focus"] = registry.pop("today_focus")

    codes, locators = _semantic_error_codes_and_locators(_mutation_root(tmp_path, rename_property))

    assert "SEMANTIC_PROPERTY_REGISTRY_EXACT_SET" in codes
    assert "/property_registry/renamed_today_focus" in locators
    assert "/property_registry/today_focus" in locators


def test_relation_reverse_is_a_semantic_error(tmp_path: Path) -> None:
    def reverse_relation(blueprint: dict[str, Any]) -> None:
        relation = blueprint["relation_registry"]["canonical_predicates"]["supports"]
        assert isinstance(relation, dict)
        relation["allowed_subject_types"], relation["allowed_object_types"] = (
            relation["allowed_object_types"],
            relation["allowed_subject_types"],
        )

    codes, locators = _semantic_error_codes_and_locators(_mutation_root(tmp_path, reverse_relation))

    assert "SEMANTIC_RELATION_DIRECTION" in codes
    assert "/relation_registry/canonical_predicates/supports/allowed_subject_types" in locators
    assert "/relation_registry/canonical_predicates/supports/allowed_object_types" in locators


def test_action_output_schema_mismatch_is_a_semantic_error(tmp_path: Path) -> None:
    def change_output_schema(blueprint: dict[str, Any]) -> None:
        contracts = blueprint["bridge"]["action_contracts"]
        assert isinstance(contracts, dict)
        contracts["triage"]["output_schema"] = "ops/schemas/answer.schema.json"

    codes, locators = _semantic_error_codes_and_locators(_mutation_root(tmp_path, change_output_schema))

    assert "SEMANTIC_ACTION_OUTPUT_SCHEMA" in codes
    assert "/bridge/action_contracts/triage/output_schema" in locators


def test_command_registry_mutation_is_a_semantic_error(tmp_path: Path) -> None:
    def rename_command(blueprint: dict[str, Any]) -> None:
        commands = blueprint["commands"]
        assert isinstance(commands, list)
        index = commands.index("vaultctl ui")
        commands[index] = "vaultctl unknown"

    codes, locators = _semantic_error_codes_and_locators(_mutation_root(tmp_path, rename_command))

    assert "SEMANTIC_COMMAND_REGISTRY_EXACT_SET" in codes
    assert "/commands/vaultctl unknown" in locators


def test_bridge_transition_reverse_is_a_semantic_error(tmp_path: Path) -> None:
    def reverse_transition(blueprint: dict[str, Any]) -> None:
        transitions = blueprint["bridge"]["allowed_transitions"]
        assert isinstance(transitions, dict)
        transitions["proposal_ready"] = ["running"]

    codes, locators = _semantic_error_codes_and_locators(_mutation_root(tmp_path, reverse_transition))

    assert "SEMANTIC_BRIDGE_TRANSITION" in codes
    assert "/bridge/allowed_transitions/proposal_ready" in locators


def test_bridge_runtime_mapping_drift_is_a_semantic_error(tmp_path: Path) -> None:
    def change_runtime(blueprint: dict[str, Any]) -> None:
        mapping = blueprint["bridge"]["state_mapping"]
        assert isinstance(mapping, dict)
        mapping["queued"]["runtime"] = "running"

    codes, locators = _semantic_error_codes_and_locators(_mutation_root(tmp_path, change_runtime))

    assert "SEMANTIC_BRIDGE_STATE_RUNTIME_MAPPING" in codes
    assert "/bridge/state_mapping/queued" in locators


def test_base_limit_mutation_is_a_semantic_error(tmp_path: Path) -> None:
    def change_limit(blueprint: dict[str, Any]) -> None:
        blueprint["bases"]["views"]["Projects.base"]["Now"]["limit"] = 4

    codes, locators = _semantic_error_codes_and_locators(_mutation_root(tmp_path, change_limit))

    assert "SEMANTIC_BASE_QUERY" in codes
    assert "/bases/views/Projects.base/Now" in locators


def test_base_sort_and_status_mutations_are_semantic_errors(tmp_path: Path) -> None:
    def change_sort_and_status(blueprint: dict[str, Any]) -> None:
        now = blueprint["bases"]["views"]["Projects.base"]["Now"]
        now["sort"] = ["priority_high_to_low", "focus_rank_asc", "target_date_asc_nulls_last", "file_name_asc"]
        now["filters"]["status_in"] = ["active"]

    codes, locators = _semantic_error_codes_and_locators(
        _mutation_root(tmp_path, change_sort_and_status)
    )

    assert "SEMANTIC_BASE_QUERY" in codes
    assert "/bases/views/Projects.base/Now" in locators


def test_dashboard_source_view_mutation_is_a_semantic_error(tmp_path: Path) -> None:
    def change_dashboard_view(blueprint: dict[str, Any]) -> None:
        sections = blueprint["dashboards"]["home"]["sections"]
        projects = next(section for section in sections if section["name"] == "projects")
        projects["view"] = "Blocked"

    codes, locators = _semantic_error_codes_and_locators(
        _mutation_root(tmp_path, change_dashboard_view)
    )

    assert "SEMANTIC_DASHBOARD_SOURCE_VIEW" in codes
    assert "/dashboards/home/sections/projects" in locators


def test_home_capture_visibility_mutation_is_a_semantic_error(tmp_path: Path) -> None:
    def expose_home_capture_contract(blueprint: dict[str, Any]) -> None:
        blueprint["dashboards"]["home"]["capture_contract"]["visible_on_home"] = True

    codes, locators = _semantic_error_codes_and_locators(
        _mutation_root(tmp_path, expose_home_capture_contract)
    )

    assert "SEMANTIC_HOME_CAPTURE_CONTRACT" in codes
    assert "/dashboards/home/capture_contract" in locators


def test_projection_hash_domain_mutation_is_a_semantic_error(tmp_path: Path) -> None:
    def change_hash_domain(blueprint: dict[str, Any]) -> None:
        blueprint["projection"]["record_contracts"]["note"]["content_hash_domain"] = "sha256_of_body"

    codes, locators = _semantic_error_codes_and_locators(
        _mutation_root(tmp_path, change_hash_domain)
    )

    assert "SEMANTIC_PROJECTION_HASH_DOMAIN" in codes
    assert "/projection/record_contracts/note/content_hash_domain" in locators


def test_projection_ordering_mutation_is_a_semantic_error(tmp_path: Path) -> None:
    def change_record_order(blueprint: dict[str, Any]) -> None:
        blueprint["projection"]["serialization"]["record_sort"]["notes"] = [
            "id",
            "path_nfc_utf8_bytes",
        ]

    codes, locators = _semantic_error_codes_and_locators(
        _mutation_root(tmp_path, change_record_order)
    )

    assert "SEMANTIC_PROJECTION_ORDERING" in codes
    assert "/projection/serialization" in locators
    assert "/projection/serialization/record_sort" in locators


def test_project_bundle_cardinality_mutation_is_a_semantic_error(tmp_path: Path) -> None:
    def change_cardinality(blueprint: dict[str, Any]) -> None:
        blueprint["note_types"]["project_note"]["field_constraints"]["projects"]["max_items"] = 2

    codes, locators = _semantic_error_codes_and_locators(
        _mutation_root(tmp_path, change_cardinality)
    )

    assert "SEMANTIC_PROJECT_BUNDLE_CARDINALITY" in codes
    assert "/note_types/project_note/field_constraints/projects" in locators


def test_capture_finalize_transaction_reorder_is_a_semantic_error(tmp_path: Path) -> None:
    def reorder_transaction(blueprint: dict[str, Any]) -> None:
        transaction = blueprint["llm"]["triage"]["capture_finalize"]["transaction"]
        transaction[4], transaction[5] = transaction[5], transaction[4]

    codes, locators = _semantic_error_codes_and_locators(
        _mutation_root(tmp_path, reorder_transaction)
    )

    assert "SEMANTIC_CAPTURE_FINALIZE_TRANSACTION" in codes
    assert "/llm/triage/capture_finalize/transaction" in locators


def test_diagnostic_serialization_is_byte_deterministic(tmp_path: Path) -> None:
    root = _mutation_root(tmp_path, lambda blueprint: blueprint.update({"contract_id": "wrong"}))

    first = validate_blueprint(root).as_json()
    second = validate_blueprint(root).as_json()

    assert first == second


def test_invalid_blueprint_does_not_create_vault_or_runtime_bytes(tmp_path: Path) -> None:
    root = _mutation_root(tmp_path, lambda blueprint: blueprint.pop("contract_id"))
    before = {
        relative: (root / relative).read_bytes()
        for relative in ("blueprint/blueprint.yaml", "blueprint/blueprint.schema.json")
    }

    result = validate_blueprint(root)

    assert not result.passed
    assert not (root / "KnowledgeHub").exists()
    assert not (root / "runtime").exists()
    assert {
        relative: (root / relative).read_bytes()
        for relative in before
    } == before
