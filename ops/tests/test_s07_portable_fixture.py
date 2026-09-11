from __future__ import annotations

from pathlib import Path

import yaml

from vaultops.base_dashboard import (
    BASE_NAMES,
    DASHBOARD_PATHS,
    dashboard_sources,
    render_base_documents,
)
from vaultops.portable_fixture import (
    FIXTURE_NAME,
    fixture_root,
    load_fixture_manifest,
    materialize_guestbook_vault,
    materialize_phase1_smoke_vault,
    validate_guestbook_fixture,
    validate_note_collection,
    verify_manifest_tree,
)
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]
S07_ROOT = fixture_root(CONTROL_ROOT)


def _note_paths(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*.md"))


def test_guestbook_horror_input_is_exact_and_queries_are_reproducible() -> None:
    report = validate_guestbook_fixture(CONTROL_ROOT)
    assert report.passed, report.as_dict()
    assert report.fixture == FIXTURE_NAME
    assert len(report.query_results) == 8


def test_guestbook_horror_expected_contains_golden_archive_finalize_and_asset_bytes() -> None:
    report = validate_guestbook_fixture(CONTROL_ROOT, section_name="expected")
    assert report.passed, report.as_dict()
    manifest = load_fixture_manifest(CONTROL_ROOT)
    expected_files = {entry["path"] for entry in manifest["expected"]["files"]}
    assert "90_Archive/Projects/2026/guestbook-horror/guestbook-horror.md" in expected_files
    assert "90_Archive/Captures/2026/20260909-090000-mac-deadbeef.md" in expected_files
    assert "80_Assets/Documents/guestbook-horror-source.txt" in expected_files
    source = (S07_ROOT / "expected/40_Knowledge/Sources/Guestbook Horror Design Note.md").read_text(encoding="utf-8")
    assert "asset_hash: c1b1c3844dec875ccbedaa656bda2f0d084812ad8cf97dc8d5851f9b96f07b48" in source


def test_materialized_fixture_applies_fixed_mtime_and_keeps_note_validation_read_only(tmp_path: Path) -> None:
    manifest = load_fixture_manifest(CONTROL_ROOT)
    materialized = materialize_guestbook_vault(CONTROL_ROOT, tmp_path / "input")
    assert verify_manifest_tree(materialized, manifest["input"], check_mtime=True) == ()
    report = validate_note_collection(
        CONTROL_ROOT,
        materialized,
        manifest["input"]["note_paths"],
        section="materialized-input",
    )
    assert report.passed, report.as_dict()


def test_phase1_smoke_materializer_includes_core_surface_without_obsidian_config(tmp_path: Path) -> None:
    vault = materialize_phase1_smoke_vault(CONTROL_ROOT, tmp_path / "smoke-vault")
    for relative in ("Home.md", "Mobile.md", *DASHBOARD_PATHS):
        assert (vault / relative).is_file(), relative
    assert all((vault / "99_System/Bases" / name).is_file() for name in BASE_NAMES)
    assert (vault / "10_Journal/Daily/2026/2026-09-09.md").is_file()
    assert not any(path.name.startswith(".obsidian") for path in vault.iterdir())
    assert not any(path.name.startswith(".vault-bridge") for path in vault.iterdir())


def test_s06_surface_remains_blueprint_exact_inside_the_s07_gate() -> None:
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")
    expected_bases = render_base_documents(blueprint)
    for relative, expected in expected_bases.items():
        assert yaml.safe_load((CONTROL_ROOT / "KnowledgeHub" / relative).read_text(encoding="utf-8")) == yaml.safe_load(expected)
    sources = dashboard_sources()
    assert tuple(sorted(sources)) == tuple(sorted(DASHBOARD_PATHS))


def test_invalid_relation_duplicate_id_and_missing_locator_fail_closed() -> None:
    cases = {
        "invalid-relation": "RELATION_SUBJECT_TYPE",
        "duplicate-id": "FIXTURE_DUPLICATE_ID",
        "missing-locator": "FIXTURE_LOCATOR_UNRESOLVED",
    }
    for name, expected_code in cases.items():
        root = S07_ROOT / "negative" / name
        report = validate_note_collection(CONTROL_ROOT, root, _note_paths(root), section=name)
        assert expected_code in report.codes, {"case": name, "report": report.as_dict()}
