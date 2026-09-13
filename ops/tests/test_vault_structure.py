from pathlib import Path

from vaultops.foundation import (
    STRUCTURAL_MARKER_DIRECTORIES,
    STRUCTURAL_MARKER_NAME,
    STRUCTURAL_MARKER_TEXT,
)
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]
VAULT_ROOT = CONTROL_ROOT / "KnowledgeHub"


def test_empty_canonical_namespaces_have_the_exact_structural_marker() -> None:
    for relative_directory in sorted(STRUCTURAL_MARKER_DIRECTORIES):
        directory = CONTROL_ROOT / relative_directory
        assert directory.is_dir(), relative_directory
        marker = directory / STRUCTURAL_MARKER_NAME
        children = [child for child in directory.iterdir() if child.name != STRUCTURAL_MARKER_NAME]
        if not children:
            assert marker.is_file(), relative_directory
            assert not marker.is_symlink(), relative_directory
            assert marker.read_text(encoding="utf-8") == STRUCTURAL_MARKER_TEXT


def test_blueprint_required_vault_files_are_real_files() -> None:
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")
    required_files = blueprint["fixed_paths"]["required_vault_files"]
    for relative in required_files:
        path = VAULT_ROOT / relative
        assert path.is_file(), relative
        assert not path.is_symlink(), relative


def test_retrospective_removed_placeholder_and_duplicate_surfaces() -> None:
    assert not (VAULT_ROOT / "00_Inbox/Imports").exists()
    assert not (VAULT_ROOT / "99_System/Bases/Ideas.base").exists()
    for profile in (".obsidian-mac", ".obsidian-phone", ".obsidian-tablet"):
        assert not (VAULT_ROOT / profile).exists()


def test_structural_markers_do_not_enter_device_bridge_or_runtime_namespaces() -> None:
    forbidden_roots = (
        VAULT_ROOT / ".obsidian-mac",
        VAULT_ROOT / ".obsidian-phone",
        VAULT_ROOT / ".obsidian-tablet",
        VAULT_ROOT / ".vault-bridge",
        CONTROL_ROOT / "runtime",
    )
    for root in forbidden_roots:
        assert not list(root.rglob(STRUCTURAL_MARKER_NAME)), root
