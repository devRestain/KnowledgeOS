"""C2 Standardize acceptance (docs/CAPABILITIES.md STD-1..STD-8) through the ExOps API."""

from __future__ import annotations

import os
import re
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import conforming_note

from knowledgeos.config import ROOT
from knowledgeos.notes import UUID4_RE, parse
from knowledgeos.util import iso

IDEAS = "40_Knowledge/Ideas/"
NOTES = "40_Knowledge/Notes/"
OLD = timedelta(hours=1)


def preview(kos, *paths):
    digests = {path: kos.vault.sha(path) for path in paths if kos.vault.path(path).is_file()}
    receipt = kos.run("standardize.preview", {"paths": list(paths), "digests": digests})
    assert receipt["status"] == "completed", receipt
    assert receipt["outcome"]["preview_id"] == receipt["work_id"]
    return receipt


def confirm(kos, receipt):
    return kos.run("standardize.confirm", {"preview_id": receipt["outcome"]["preview_id"]})


def automatic(kos):
    receipt = kos.run("standardize.auto", {}, actor="schedule/standardize")
    assert receipt["status"] in ("completed", "unmet"), receipt
    return receipt


def test_STD_1_frontmatterless_idea_note(kos, vault, clock):
    path = IDEAS + "Thought.md"
    body = "# 도구와 생각\n\n도구가 생각의 모양을 바꾼다.\n"
    vault.write(path, body)
    receipt = preview(kos, path)
    [planned] = receipt["outcome"]["notes"]
    assert {change["key"] for change in planned["changes"]} >= {"id", "type", "created", "status", "ai_policy"}
    assert vault.text(path) == body

    done = confirm(kos, receipt)
    assert done["status"] == "completed", done
    note = parse(path, vault.read(path))
    assert UUID4_RE.match(note.data["id"])
    assert note.data["type"] == "idea"
    assert note.data["created"] == iso(clock.now())
    assert note.data["status"] == "seed"
    assert note.data["ai_policy"] == "ask"
    assert note.body.encode("utf-8") == body.encode("utf-8")
    [change] = kos.changes()
    assert change["path"] == path and change["kind"] == "manual"
    assert change["preimage_sha"] != change["result_sha"] == vault.sha(path)


def test_STD_2_trailing_tags_line_moves_into_frontmatter(kos, vault):
    path = NOTES + "Claim.md"
    original = conforming_note("knowledge", "seed", body="A claim.\n\ntags: [a, b]\n", extra="tags: [b]\n")
    vault.write(path, original)
    receipt = preview(kos, path)
    [planned] = receipt["outcome"]["notes"]
    assert [(c["key"], c["after"]) for c in planned["changes"]] == [("tags", ["b", "a"])]

    assert confirm(kos, receipt)["status"] == "completed"
    expected = original.replace("tags: [b]\n", "tags: [b, a]\n", 1).replace("A claim.\n\ntags: [a, b]\n", "A claim.\n\n")
    assert vault.text(path) == expected


def test_STD_3_note_changed_after_preview_is_not_written(kos, vault):
    path = IDEAS + "Moving.md"
    vault.write(path, "first draft\n")
    receipt = preview(kos, path)
    edited = "first draft, edited on the phone\n"
    vault.write(path, edited, age=OLD)

    done = confirm(kos, receipt)
    assert done["status"] == "unmet", done
    assert done["outcome"]["applied"] == []
    assert [item["path"] for item in done["outcome"]["not_applied"]] == [path]
    assert path in done["notice"]
    assert vault.text(path) == edited
    assert kos.changes() == []

    # The automatic path works from file state, so the next run reconsiders the edited note.
    run = automatic(kos)
    assert run["outcome"]["applied"] == [path]
    note = parse(path, vault.read(path))
    assert note.data["type"] == "idea" and note.body == edited


def test_STD_4_quiet_period_skips_only_the_automatic_path(kos, vault):
    path = IDEAS + "Fresh.md"
    vault.write(path, "just written\n", age=timedelta(minutes=2))

    run = automatic(kos)
    assert {"path": path, "reason": "quiet_period"} in run["outcome"]["skipped"]
    assert run["outcome"]["applied"] == []
    assert vault.text(path) == "just written\n"

    receipt = preview(kos, path)
    assert confirm(kos, receipt)["status"] == "completed"
    note = parse(path, vault.read(path))
    assert note.data["type"] == "idea" and note.data["status"] == "seed"


def test_STD_5_conforming_note_is_never_written(kos, vault):
    path = NOTES + "Settled.md"
    vault.write(path, conforming_note("knowledge", "evergreen", extra="tags: [x]\naliases: []\n"), age=OLD)
    before = vault.snapshot()

    receipt = preview(kos, path)
    assert receipt["outcome"]["notes"] == [] and receipt["outcome"]["unchanged"] == 1
    assert confirm(kos, receipt)["status"] == "refused"  # nothing awaits confirmation
    batch = kos.run("standardize.batch", {"path": "40_Knowledge", "apply": True})
    assert batch["status"] == "completed" and batch["outcome"]["applied"] == []
    assert automatic(kos)["outcome"]["applied"] == []

    assert vault.snapshot() == before
    assert kos.changes() == []


def test_STD_6_custom_keys_and_quoted_values_stay_byte_identical(kos, vault):
    path = IDEAS + "Custom.md"
    front = (
        'title: "Plan: phase 2"\n'
        "custom_key: 'single quoted'\n"
        "type: idea\n"
        "rating:   5   \n"
        'nested: {"a": 1}\n'
        "list_prop:\n"
        "  - one\n"
        "  - 'two'\n"
    )
    vault.write(path, f"---\n{front}---\nBody stays.\n")
    assert confirm(kos, preview(kos, path))["status"] == "completed"

    text = vault.text(path)
    assert text.startswith("---\n" + front)
    appended, body = text[len("---\n" + front) :].split("---\n", 1)
    assert [line.split(":", 1)[0] for line in appended.splitlines()] == ["id", "created", "status", "ai_policy"]
    assert body == "Body stays.\n"


TEMPLATES = {
    "T00_Capture.md": "00_Inbox",
    "T21_Idea.md": "40_Knowledge/Ideas",
    "T22_Question.md": "40_Knowledge/Questions",
    "T40_Knowledge.md": "40_Knowledge/Notes",
    "T41_Source.md": "40_Knowledge/Sources",
    "T20_Project.md": "20_Projects/{title}",
}


def _templates() -> Path:
    mounted = os.environ.get("KNOWLEDGEOS_VAULT_SYSTEM")
    system = Path(mounted) if mounted else ROOT.parents[1] / "Vaults/KnowledgeHub/99_System"
    if not (system / "Templates").is_dir():
        pytest.skip("Vault templates are not mounted (KNOWLEDGEOS_VAULT_SYSTEM)")
    return system / "Templates"


def render_template(text: str, title: str, now: str) -> str:
    """What QuickAdd + Templater produce, rendered deterministically."""
    text = re.sub(r"<%\*.*?-%>\r?\n?", "", text, flags=re.DOTALL)
    for placeholder, value in {
        "<% id %>": str(uuid.uuid4()),
        "<% now %>": now,
        "<% yamlTitle %>": title,
        "{{VALUE:collision_aliases_yaml}}": "[]",
    }.items():
        text = text.replace(placeholder, value)
    assert not re.search(r"<%|\{\{VALUE:", text), f"unrendered template placeholder in {title}"
    return text


def test_STD_7_template_notes_need_no_changes(kos, vault, clock):
    templates = _templates()
    paths = []
    for name, folder in TEMPLATES.items():
        title = f"Template {name.split('_')[1].removesuffix('.md')}"
        path = f"{folder.format(title=title)}/{title}.md"
        vault.write(path, render_template((templates / name).read_text(encoding="utf-8"), title, iso(clock.now())))
        paths.append(path)

    receipt = preview(kos, *paths)
    assert receipt["outcome"]["skipped"] == []
    assert receipt["outcome"]["notes"] == [], [(n["path"], n["changes"]) for n in receipt["outcome"]["notes"]]
    assert receipt["outcome"]["unchanged"] == len(paths)


def test_STD_8_cancelled_preview_writes_nothing(kos, vault):
    path = IDEAS + "Cancel.md"
    vault.write(path, "keep me as is\n")
    before = vault.snapshot()
    receipt = preview(kos, path)
    assert receipt["outcome"]["notes"]

    cancelled = kos.run("standardize.cancel", {"preview_id": receipt["outcome"]["preview_id"]})
    assert cancelled["status"] == "completed", cancelled
    assert vault.snapshot() == before
    assert kos.changes() == []
    assert confirm(kos, receipt)["status"] == "refused"
    assert vault.snapshot() == before
    assert kos.get(f"/api/works/{receipt['work_id']}")[1]["status"] == "cancelled"


def test_skips_conflicts_invalid_yaml_duplicates_and_deny(kos, vault):
    shared = str(uuid.uuid4())
    vault.write(IDEAS + "Conflict.md", "<<<<<<< ours\na\n=======\nb\n>>>>>>> theirs\n")
    vault.write(IDEAS + "Broken.md", "---\ntitle: [unclosed\n---\nbody\n")
    vault.write(IDEAS + "Twin1.md", f"---\nid: {shared}\n---\none\n")
    vault.write(IDEAS + "Twin2.md", f"---\nid: {shared}\n---\ntwo\n")
    vault.write(IDEAS + "Private.md", "---\nai_policy: deny\n---\nprivate\n")
    vault.write(IDEAS + "Plain.md", "plain\n")
    before = vault.snapshot()

    receipt = kos.run("standardize.batch", {"path": IDEAS.rstrip("/"), "apply": True})
    assert receipt["status"] == "completed", receipt
    assert receipt["outcome"]["applied"] == [IDEAS + "Plain.md"]
    reasons = {item["path"]: item["reason"] for item in receipt["outcome"]["skipped"]}
    assert reasons == {
        IDEAS + "Conflict.md": "conflict",
        IDEAS + "Broken.md": "invalid_yaml",
        IDEAS + "Twin1.md": "duplicate_id",
        IDEAS + "Twin2.md": "duplicate_id",
        IDEAS + "Private.md": "deny",
    }
    after = vault.snapshot()
    assert {path: value for path, value in after.items() if not path.endswith("Plain.md")} == {
        path: value for path, value in before.items() if not path.endswith("Plain.md")
    }


def test_STD_1_ordinary_root_note_is_standardized_without_inventing_type(kos, vault, clock):
    path = "A free thought.md"
    body = "An ordinary root note, not navigation.\n"
    vault.write(path, body)
    receipt = preview(kos, path)
    [planned] = receipt["outcome"]["notes"]
    assert {change["key"] for change in planned["changes"]} == {"frontmatter", "id", "created", "ai_policy"}
    assert confirm(kos, receipt)["status"] == "completed"
    note = parse(path, vault.read(path))
    assert UUID4_RE.fullmatch(note.data["id"])
    assert note.data["created"] == iso(clock.now())
    assert note.data["ai_policy"] == "ask"
    assert "type" not in note.data and "status" not in note.data
    assert note.body == body


@pytest.mark.parametrize("note_type", ["home", "system"])
def test_STD_1_actual_root_navigation_is_unchanged(kos, vault, note_type):
    path = f"{note_type}.md"
    original = f"---\ntype: {note_type}\n---\nNavigation stays untouched.\n"
    vault.write(path, original)
    receipt = preview(kos, path)
    assert receipt["outcome"]["notes"] == []
    assert receipt["outcome"]["skipped"] == [{"path": path, "reason": "navigation"}]
    assert vault.text(path) == original


@pytest.mark.parametrize(
    "identifier",
    ["not-a-uuid", "00000000-0000-1000-8000-000000000000", "123", "[one, two]", '""', "null"],
)
def test_STD_6_malformed_id_is_replaced_without_rewriting_other_lines(kos, vault, identifier):
    path = "Unclassified.md"
    untouched = (
        "type: 'my own kind'\r\n"
        "status:   'my own state'   \r\n"
        "created: 2026-11-01T09:00:00+09:00\r\n"
        "ai_policy: ask\r\n"
        "custom: 'quoted: unchanged'\r\n"
    )
    original = f"---\r\nid: {identifier}\r\n{untouched}---\r\nBody stays byte-identical.\r\n"
    vault.write(path, original)
    receipt = preview(kos, path)
    [planned] = receipt["outcome"]["notes"]
    assert [(change["key"], change["rule"]) for change in planned["changes"]] == [("id", "id")]
    assert confirm(kos, receipt)["status"] == "completed"
    note = parse(path, vault.read(path))
    assert UUID4_RE.fullmatch(note.data["id"])
    assert vault.text(path) == f"---\r\nid: {note.data['id']}\r\n{untouched}---\r\nBody stays byte-identical.\r\n"
    assert note.data["type"] == "my own kind" and note.data["status"] == "my own state"


def test_STD_6_valid_uuid_and_quoted_property_lines_remain_identical(kos, vault):
    path = IDEAS + "Existing identity.md"
    identifier = str(uuid.uuid4()).upper()
    front = f'id: "{identifier}"\ncustom:   "leave this alone"   \ntype: idea\n'
    vault.write(path, f"---\n{front}---\nSame body.\n")
    receipt = preview(kos, path)
    [planned] = receipt["outcome"]["notes"]
    assert "id" not in {change["key"] for change in planned["changes"]}
    assert confirm(kos, receipt)["status"] == "completed"
    assert vault.text(path).startswith("---\n" + front)
    assert parse(path, vault.read(path)).note_id == identifier
