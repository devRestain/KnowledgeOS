"""Vault confinement, Git-ignore semantics and read-only Owner observations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import conforming_note

from knowledgeos.notes import parse
from knowledgeos.util import iso, sha256
from knowledgeos.vault import Vault, VaultPathError

IDEAS = "40_Knowledge/Ideas/"


def files(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def test_STD_1_http_and_cli_refuse_absolute_and_traversal_targets(kos, vault, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "Private.md"
    target.write_text("outside-canary-never-disclosed\n")
    vault.write(IDEAS + "Visible.md", "Ordinary content.\n")
    before = files(outside)
    for path in (
        str(target), str(outside), "../outside/Private.md", "../outside", "/",
        "/40_Knowledge/Ideas/Visible.md", "40_Knowledge/../outside/Private.md",
        "C:\\outside\\Private.md", "\\\\server\\share\\Private.md",
    ):
        receipt = kos.run("standardize.preview", {"paths": [path]})
        assert receipt["status"] == "refused", receipt
        assert "outside-canary-never-disclosed" not in json.dumps(receipt)
    code, out, err = kos.cli("standardize", "../outside", "--apply")
    assert code != 0
    assert "outside-canary-never-disclosed" not in out + err
    assert files(outside) == before
    assert kos.search(mode="keyword", query="outside-canary-never-disclosed")["results"] == []


def test_SRC_2_external_symlink_files_and_selected_folders_are_never_followed(kos, vault, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Private.md").write_text("symlink-canary-never-disclosed\n")
    vault.path(IDEAS).mkdir(parents=True)
    vault.path(IDEAS + "Alias.md").symlink_to(outside / "Private.md")
    vault.path("External").symlink_to(outside, target_is_directory=True)
    before = files(outside)
    for path in (IDEAS + "Alias.md", "External", "External/Private.md"):
        receipt = kos.run("standardize.batch", {"path": path, "apply": True})
        assert receipt["status"] == "refused", receipt
        assert "symlink-canary-never-disclosed" not in json.dumps(receipt)
    assert kos.search(mode="keyword", query="symlink-canary-never-disclosed")["results"] == []
    status, check = kos.get("/api/vault-check")
    assert status == 200 and check["checked"] == 0
    assert files(outside) == before


@pytest.mark.parametrize("swap", ["file", "directory", "ignore"])
def test_STD_3_confirmation_rechecks_current_path_boundary(kos, vault, tmp_path, swap):
    path = IDEAS + "Draft.md"
    original = "Same bytes inside and outside.\n"
    vault.write(path, original)
    receipt = kos.run("standardize.preview", {"paths": [path], "digests": {path: vault.sha(path)}})
    assert receipt["status"] == "completed", receipt
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Draft.md").write_text(original)
    if swap == "file":
        vault.path(path).unlink()
        vault.path(path).symlink_to(outside / "Draft.md")
    elif swap == "directory":
        vault.path(path).unlink()
        vault.path(IDEAS).rmdir()
        vault.path(IDEAS).symlink_to(outside, target_is_directory=True)
    else:
        vault.write(".gitignore", f"/{path}\n")
    before = files(outside)
    done = kos.run("standardize.confirm", {"preview_id": receipt["outcome"]["preview_id"]})
    assert done["status"] != "completed", done
    assert files(outside) == before
    assert vault.read(path) == original.encode()
    assert kos.changes() == []


def test_SRC_2_gitignore_root_nested_negation_and_symlink_aliases(kos, vault):
    vault.write(
        ".gitignore",
        "/Private.md\n**/Secret*.md\n/Blocked/\n*.scratch.md\n!/Keep.scratch.md\n"
        "/40_Knowledge/Ideas/Nested/*.md\n!.obsidian-local/\n!80_Assets/\n",
    )
    vault.write(IDEAS + "Nested/.gitignore", "!Keep.md\n*.local.md\n")
    vault.write("Blocked/.gitignore", "!Keep.md\n")
    allowed = ["Keep.scratch.md", IDEAS + "Visible.md", IDEAS + "Nested/Keep.md"]
    excluded = [
        "Private.md", "Other/Secret notes.md", "Other/Keep.scratch.md", "Blocked/Keep.md",
        IDEAS + "Nested/Hidden.md", IDEAS + "Nested/Device.local.md",
        ".obsidian-local/Settings.md", "80_Assets/Attachment.md",
    ]
    for path in allowed + excluded:
        vault.write(path, "ignore-boundary-token\n")
    vault.path("Alias.md").symlink_to(vault.path("Private.md"))
    vault.path("Alias folder").symlink_to(vault.path("Blocked"), target_is_directory=True)
    excluded_bytes = {path: vault.read(path) for path in excluded}
    assert sorted(item["path"] for item in kos.search(mode="keyword", query="ignore-boundary-token")["results"]) == sorted(allowed)
    for path in [*excluded, "Blocked", "Alias.md", "Alias folder", "Alias folder/Keep.md"]:
        receipt = kos.run("standardize.preview", {"paths": [path]})
        assert receipt["status"] == "refused", receipt
    done = kos.run("standardize.batch", {"path": ".", "apply": True})
    assert done["status"] == "completed", done
    assert sorted(done["outcome"]["applied"]) == sorted(allowed)
    assert {path: vault.read(path) for path in excluded} == excluded_bytes
    assert all(parse(path, vault.read(path)).data.get("id") for path in allowed)


def test_SRC_4_gitignore_identity_changes_evict_and_reinclude_existing_documents(kos, vault):
    path = IDEAS + "Changed policy.md"
    vault.write(path, "ignore-cache-token\n")
    assert [item["path"] for item in kos.search(mode="keyword", query="ignore-cache-token")["results"]] == [path]
    vault.write(".gitignore", f"/{path}\n")
    assert kos.search(mode="keyword", query="ignore-cache-token")["results"] == []
    assert kos.run("standardize.batch", {"path": path, "apply": True})["status"] == "refused"
    vault.path(".gitignore").unlink()
    assert [item["path"] for item in kos.search(mode="keyword", query="ignore-cache-token")["results"]] == [path]
    vault.write(IDEAS + ".gitignore", "*.md\n")
    assert kos.search(mode="keyword", query="ignore-cache-token")["results"] == []
    vault.write(IDEAS + ".gitignore", "*.md\n!Changed policy.md\n")
    assert [item["path"] for item in kos.search(mode="keyword", query="ignore-cache-token")["results"]] == [path]


@pytest.mark.parametrize("boundary", ["symlink", "ignored", "device_local"])
def test_STD_1_public_output_and_rename_boundaries_do_not_write_excluded_destinations(kos, vault, tmp_path, boundary):
    target = "20_Projects/Done/Done.md"
    original = conforming_note("project", "done")
    vault.write(target, original)
    result = kos.run("digest.run", {}, actor="schedule/digest")
    assert result["status"] == "completed", result
    [proposal] = vault.root.glob("01_AI_Review/Proposals/*.md")
    proposal_path = proposal.relative_to(vault.root).as_posix()
    outside = tmp_path / "outside"
    outside.mkdir()
    if boundary == "symlink":
        vault.path("90_Archive").symlink_to(outside, target_is_directory=True)
    elif boundary == "ignored":
        vault.write(".gitignore", "/90_Archive/\n")
    else:
        # A user-edited proposal is still parsed, but its device-local move must be refused.
        vault.write(proposal_path, vault.text(proposal_path).replace("90_Archive", ".obsidian-local"))
    before = files(outside)
    done = kos.run("review.apply", {"path": proposal_path, "digest": vault.sha(proposal_path)})
    assert done["status"] != "completed", done
    assert vault.text(target) == original
    assert files(outside) == before
    assert not vault.path(".obsidian-local").exists()


@pytest.mark.parametrize("boundary", ["symlink", "ignored"])
def test_STD_1_public_output_creation_refuses_excluded_review_folders(kos, vault, tmp_path, boundary):
    target = "20_Projects/Done/Done.md"
    vault.write(target, conforming_note("project", "done"))
    outside = tmp_path / "outside"
    outside.mkdir()
    if boundary == "symlink":
        vault.path("01_AI_Review").symlink_to(outside, target_is_directory=True)
    else:
        vault.write(".gitignore", "/01_AI_Review/\n")
    before = files(outside)
    result = kos.run("digest.run", {}, actor="schedule/digest")
    assert result["status"] != "completed", result
    assert files(outside) == before
    assert not list(outside.rglob("*.md"))


def test_STD_1_output_name_collision_keeps_existing_bytes_and_uses_suffix(kos, vault):
    first = "20_Projects/One/Done.md"
    second = "20_Projects/Two/Done.md"
    vault.write(first, conforming_note("project", "done"))
    result = kos.run("digest.run", {}, actor="schedule/digest")
    assert result["status"] == "completed", result
    [original] = vault.root.glob("01_AI_Review/Proposals/*.md")
    before = original.read_bytes()
    vault.write(second, conforming_note("project", "done"))
    result = kos.run("digest.run", {}, actor="schedule/digest")
    assert result["status"] == "completed", result
    paths = sorted(vault.root.glob("01_AI_Review/Proposals/*.md"))
    assert len(paths) == 2
    assert original.read_bytes() == before
    assert original.with_name(original.stem + " 2.md") in paths


def test_SRC_4_get_views_and_search_do_not_write_state_and_c2_keeps_first_observation(kos, vault, clock):
    path = IDEAS + "Observed earlier.md"
    vault.write(path, "first-observed-token\n")
    state = kos.owner.config.state
    before = files(state)
    observed = iso(clock.now())
    for route in (
        "/api/health", "/api/roles", "/api/jobs", "/api/works", "/api/notices",
        "/api/unknowns", "/api/attention", "/api/experience", "/api/changes",
        "/api/index/verify", "/api/vault-check",
    ):
        status, payload = kos.get(route)
        assert status == 200, (route, payload)
    assert [item["path"] for item in kos.search(mode="keyword", query="first-observed-token")["results"]] == [path]
    assert kos.search(mode="related", path=path)["results"] == []
    assert kos.search(mode="semantic", query="first-observed-token")["status"] == "unavailable"
    assert kos.get("/api/requests/not-a-request")[0] == 404
    assert files(state) == before
    clock.advance(hours=2)
    done = kos.run("standardize.batch", {"path": path, "apply": True})
    assert done["status"] == "completed", done
    assert parse(path, vault.read(path)).data["created"] == observed
    rows = [json.loads(line) for line in (state / "first_seen.jsonl").read_text().splitlines()]
    assert [row for row in rows if row["path"] == path] == [{"path": path, "at": observed}]


def test_STD_1_all_vault_effects_reject_boundary_escapes_before_journaling(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "External.md").write_bytes(b"outside\n")
    (root / "Alias").symlink_to(outside, target_is_directory=True)
    (root / "Source.md").write_bytes(b"source\n")
    (root / "01_AI_Review/Proposals").mkdir(parents=True)
    (root / "01_AI_Review/Proposals/Alias.md").symlink_to(outside / "External.md")
    (root / ".gitignore").write_text("/Ignored/\n")
    vault = Vault(root)
    records = []

    def record(kind, payload):
        records.append((kind, payload))

    for target in (
        "../outside/External.md", str(outside / "External.md"), "Alias/External.md",
        "Ignored/New.md", ".obsidian/New.md", "01_AI_Review/Proposals/Alias.md",
    ):
        for operation in (
            lambda target=target: vault.abs(target),
            lambda target=target: vault.read(target),
            lambda target=target: vault.stat(target),
            lambda target=target: vault.exists(target),
            lambda target=target: vault.iter_paths(target),
            lambda target=target: vault.create(target, b"new\n", record),
            lambda target=target: vault.replace(target, sha256(b"outside\n"), b"new\n", record),
            lambda target=target: vault.move("Source.md", sha256(b"source\n"), target, b"new\n", record),
            lambda target=target: vault.move(target, sha256(b"outside\n"), "Destination.md", b"new\n", record),
            lambda target=target: vault.delete_own(target, sha256(b"outside\n"), record),
            lambda target=target: vault.observe_effect({"op": "replace", "path": target, "preimage": None, "result": "unused"}),
        ):
            with pytest.raises(VaultPathError):
                operation()
    assert records == []
    assert (outside / "External.md").read_bytes() == b"outside\n"
    assert (root / "Source.md").read_bytes() == b"source\n"


@pytest.mark.parametrize("operation", ["create", "replace", "move", "delete"])
def test_STD_3_writer_rechecks_symlink_components_after_intent_recording(tmp_path, operation):
    root = tmp_path / "vault"
    folder = root / "01_AI_Review/Proposals"
    folder.mkdir(parents=True)
    (folder / "Source.md").write_bytes(b"original\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Source.md").write_bytes(b"original\n")
    vault = Vault(root)
    intents = []

    def record(kind, payload):
        if kind == "intent":
            intents.append(payload)
            folder.rename(root / "saved")
            folder.symlink_to(outside, target_is_directory=True)

    source = "01_AI_Review/Proposals/Source.md"
    destination = "01_AI_Review/Proposals/New.md"
    before = files(outside)
    with pytest.raises(VaultPathError):
        if operation == "create":
            vault.create(destination, b"changed\n", record)
        elif operation == "replace":
            vault.replace(source, sha256(b"original\n"), b"changed\n", record)
        elif operation == "move":
            vault.move(source, sha256(b"original\n"), destination, b"changed\n", record)
        else:
            vault.delete_own(source, sha256(b"original\n"), record)
    assert len(intents) == 1
    assert files(outside) == before
    assert (root / "saved/Source.md").read_bytes() == b"original\n"
