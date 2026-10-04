from __future__ import annotations

import json

import pytest
from support.control_factory import fixture_path, make_control_root

from vaultops.paths import RootResolutionError, resolve_beneath, resolve_paths


def fixture(tmp_path):
    return make_control_root(tmp_path, ("blueprint",))


def test_explicit_five_roots_resolve_from_unrelated_cwd_without_creation(tmp_path, monkeypatch):
    root = fixture(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    roots = resolve_paths(root, environment={})
    assert roots.control == root
    assert roots.state != roots.runtime
    for kind in ("core", "vault", "state", "runtime"):
        assert getattr(roots, kind) == fixture_path(root, kind)
        assert root not in getattr(roots, kind).parents


@pytest.mark.parametrize("version", [1, 2, 4])
def test_old_or_unknown_config_refused_before_write(tmp_path, version):
    root = fixture(tmp_path)
    config = root / "ops/vaultops.toml"
    config.write_text(config.read_text().replace("schema_version = 3", f"schema_version = {version}"))
    before = fixture_path(root, "state").joinpath("owner-control.json").read_bytes()
    with pytest.raises(RootResolutionError, match="only config v3"):
        resolve_paths(root, environment={})
    assert fixture_path(root, "state").joinpath("owner-control.json").read_bytes() == before


@pytest.mark.parametrize("content", ['schema_version = [\n', 'schema_version = 3\n', 'schema_version = "3"\n'])
def test_malformed_and_missing_schema_fields_fail_closed(tmp_path, content):
    root = fixture(tmp_path)
    (root / "ops/vaultops.toml").write_text(content)
    with pytest.raises(RootResolutionError):
        resolve_paths(root, environment={})


def test_missing_explicit_config_has_no_nested_layout_fallback(tmp_path):
    root = fixture(tmp_path)
    with pytest.raises(RootResolutionError, match="schema v3"):
        resolve_paths(root, environment={"KNOWLEDGEOS_CONFIG_FILE": str(tmp_path / "missing.toml")})


def test_unknown_keys_and_aliased_roots_are_rejected(tmp_path):
    root = fixture(tmp_path)
    config = root / "ops/vaultops.toml"
    text = config.read_text()
    config.write_text(text + 'actor_id = "administrator"\n')
    with pytest.raises(RootResolutionError):
        resolve_paths(root, environment={})
    config.write_text(text)
    with pytest.raises(RootResolutionError, match="independent"):
        resolve_paths(root, environment={"KNOWLEDGEOS_STATE_ROOT": str(fixture_path(root, "runtime"))})


def test_overrides_are_independent_and_invalid_explicit_root_never_falls_back(tmp_path):
    root = fixture(tmp_path)
    alternative = tmp_path / "alternate-state"
    alternative.mkdir(mode=0o700)
    roots = resolve_paths(root, environment={"KNOWLEDGEOS_STATE_ROOT": str(alternative)})
    assert roots.state == alternative and roots.runtime == fixture_path(root, "runtime")
    with pytest.raises(RootResolutionError):
        resolve_paths(tmp_path / "missing-control", environment={"KNOWLEDGEOS_CONTROL_ROOT": str(root)})


@pytest.mark.parametrize("kind", ["core", "control", "vault", "state", "runtime"])
def test_symlink_root_or_ancestor_is_rejected(tmp_path, kind):
    root = fixture(tmp_path)
    original = root if kind == "control" else fixture_path(root, kind)
    alias = tmp_path / "alias"
    alias.symlink_to(original, target_is_directory=True)
    with pytest.raises(RootResolutionError):
        resolve_paths(alias if kind == "control" else root, environment={f"KNOWLEDGEOS_{kind.upper()}_ROOT": str(alias)})


@pytest.mark.parametrize("kind", ["state", "runtime"])
def test_private_roots_reject_public_mode_and_git(tmp_path, kind):
    root = fixture(tmp_path)
    selected = fixture_path(root, kind)
    selected.chmod(0o755)
    with pytest.raises(RootResolutionError):
        resolve_paths(root, environment={})
    selected.chmod(0o700)
    (selected / ".git").mkdir()
    with pytest.raises(RootResolutionError):
        resolve_paths(root, environment={})


@pytest.mark.parametrize("relative", ["../outside", "/absolute", "a/../b", "a//b", "a\\b", "a/./b"])
def test_resource_paths_reject_traversal_and_noncanonical_components(tmp_path, relative):
    with pytest.raises(RootResolutionError):
        resolve_beneath(tmp_path, relative)


def test_resource_path_rejects_symlink_ancestor(tmp_path):
    directory = tmp_path / "root"
    directory.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (directory / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RootResolutionError):
        resolve_beneath(directory, "escape/private.md")


def test_relative_private_config_roots_use_config_parent(tmp_path, monkeypatch):
    root = fixture(tmp_path)
    config = root / "ops/vaultops.toml"
    text = config.read_text()
    for kind in ("core", "vault", "state", "runtime"):
        text = text.replace(json.dumps(str(fixture_path(root, kind))), json.dumps("../../" + kind))
    config.write_text(text)
    monkeypatch.chdir(tmp_path.parent)
    assert resolve_paths(root, environment={}).state == tmp_path / "state"
