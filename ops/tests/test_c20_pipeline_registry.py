from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest
import yaml

from vaultops.cli import main
from vaultops.pipeline_registry import (
    PIPELINE_FILENAMES,
    PROMPT_FILENAMES,
    USER_ACTION_ROUTES,
    dispatch_user_action,
    validate_c20_registry,
)
from vaultops.schema_export import OWNED_ARTIFACTS
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def _fresh_control_copy(tmp_path: Path) -> Path:
    root = tmp_path / "control"
    shutil.copytree(
        CONTROL_ROOT,
        root,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", ".ruff_cache", "KnowledgeHub", "runtime"),
    )
    fixture = CONTROL_ROOT / "ops/tests/fixtures/c09_portable_vault/guestbook-horror/input"
    shutil.copytree(fixture, root / "KnowledgeHub")
    return root


def _write_blueprint(root: Path, mutate) -> None:
    blueprint = copy.deepcopy(load_yaml_file(root / "blueprint/blueprint.yaml"))
    mutate(blueprint)
    (root / "blueprint/blueprint.yaml").write_text(
        yaml.safe_dump(blueprint, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def test_c20_registry_contains_every_action_prompt_route_and_traceability_record(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)

    report, code = validate_c20_registry(root)

    assert code == 0, report
    assert report["status"] == "PASS"
    assert set(report["pipelines"]) == set(PIPELINE_FILENAMES)
    assert report["routes"] == {route: list(pipelines) for route, pipelines in USER_ACTION_ROUTES.items()}
    assert report["traceability"]["path"] == "ops/config/prd-traceability.yaml"
    for pipeline, filename in PIPELINE_FILENAMES.items():
        assert (root / "ops/actions" / filename).is_file()
        assert (root / "ops/prompts" / PROMPT_FILENAMES[pipeline]).is_file()


def test_c20_facade_dispatch_is_ready_and_read_only(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }

    for route, expected_pipelines in USER_ACTION_ROUTES.items():
        report, code = dispatch_user_action(root, route)
        assert code == 0, report
        assert report["status"] == "READY"
        assert report["route"] == route
        assert report["pipelines"] == list(expected_pipelines)
        assert report["provider_called"] is False
        assert report["mutation_performed"] is False

    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_c20_cli_exposes_each_facade_route_without_execution(tmp_path: Path, capsys) -> None:
    root = _fresh_control_copy(tmp_path)

    for route in USER_ACTION_ROUTES:
        assert main(["ai", route, "--root", str(root)]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "READY"
        assert report["operation"] == f"ai {route}"
        assert report["mutation_performed"] is False


def test_c20_unknown_route_and_duplicate_mapping_fail_closed(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)

    unknown, unknown_code = dispatch_user_action(root, "unknown")
    assert unknown_code == 30
    assert unknown["errors"][0]["code"] == "C20_ROUTE_UNKNOWN"

    def duplicate_pipeline(blueprint: dict[str, object]) -> None:
        actions = blueprint["actions"]
        assert isinstance(actions, dict)
        routes = actions["user_action_routes"]
        assert isinstance(routes, dict)
        organize = routes["organize"]
        assert isinstance(organize, dict)
        organize["pipelines"] = ["triage", "triage", "normalize"]

    _write_blueprint(root, duplicate_pipeline)
    invalid, invalid_code = validate_c20_registry(root)
    assert invalid_code == 30
    assert invalid["errors"][0]["code"] == "C20_REGISTRY_INVALID"
    assert "duplicate pipeline" in invalid["errors"][0]["message"]


@pytest.mark.parametrize("relative", ["ops/actions/answer.json", "ops/prompts/normalize.md"])
def test_c20_missing_registry_artifact_fails_closed(tmp_path: Path, relative: str) -> None:
    root = _fresh_control_copy(tmp_path)
    (root / relative).unlink()

    report, code = validate_c20_registry(root)

    assert code == 30
    assert report["status"] == "FAIL"
    assert report["provider_called"] is False
    assert report["mutation_performed"] is False


def test_c20_artifact_ownership_promotes_all_c20_outputs(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    ownership = load_yaml_file(root / "ops/config/generated-artifacts.yaml")
    assert isinstance(ownership, dict)
    artifacts = {item["path"]: item for item in ownership["artifacts"]}
    c20_paths = {
        spec.path for spec in OWNED_ARTIFACTS if spec.owner == "C20"
    }

    assert c20_paths
    assert all(artifacts[path]["status"] == "OWNED" for path in c20_paths)
    assert all(artifacts[path]["first_capability"] == "C20" for path in c20_paths)
