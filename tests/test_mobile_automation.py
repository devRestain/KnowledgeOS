"""Consumer-visible contracts for the KnowledgeOS iOS shortcut definitions."""

from __future__ import annotations

import importlib.util
import re
import uuid as uuidlib
from pathlib import Path

from knowledgeos.util import iso

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "config/obsidian-mobile/automation/build_shortcuts.py"
)
_SPEC = importlib.util.spec_from_file_location("knowledgeos_build_shortcuts", MODULE_PATH)
assert _SPEC and _SPEC.loader
shortcuts = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(shortcuts)


def _render(token: dict, values: dict[tuple[str, str], str]) -> str:
    """Render a WFTextTokenString by replacing its U+FFFC placeholders."""
    text = token["Value"]["string"]
    replacements: list[tuple[int, str]] = []
    for range_key, attachment in token["Value"]["attachmentsByRange"].items():
        match = re.fullmatch(r"\{(\d+), (\d+)\}", range_key)
        assert match, range_key
        start, length = int(match.group(1)), int(match.group(2))
        assert length == 1
        if attachment["Type"] == "ActionOutput":
            key = ("output", attachment["OutputUUID"])
        else:
            key = (attachment["Type"], attachment["VariableName"])
        replacements.append((start, values[key]))
    for start, replacement in sorted(replacements, reverse=True):
        byte_start = start * 2
        encoded = text.encode("utf-16-le")
        text = (
            encoded[:byte_start]
            + replacement.encode("utf-16-le")
            + encoded[byte_start + 2 :]
        ).decode("utf-16-le")
    return text


def test_text_attachments_use_utf16_offsets_for_korean_and_astral_text() -> None:
    prefix = "😀 한국\n"
    token = shortcuts.token_string(prefix, shortcuts.variable("Body"), "끝")
    offset = len(prefix.encode("utf-16-le")) // 2
    assert offset == 6
    assert token["Value"]["attachmentsByRange"] == {
        "{6, 1}": {"Type": "Variable", "VariableName": "Body"}
    }
    assert _render(token, {("Variable", "Body"): "본문"}) == "😀 한국\n본문끝"


def test_frontmatter_keeps_untrusted_body_after_metadata() -> None:
    body = (
        "+&#한글 😀\n"
        "<% templater %>\n"
        "{{ quickadd }}\n"
        "---\n"
        "key: value\n"
        "a=b&c"
    )
    uuid_attachment = shortcuts.attachment("11111111-1111-4111-8111-111111111111", "Combined Text")
    created_attachment = shortcuts.attachment("22222222-2222-4222-8222-222222222222", "Formatted Date")
    body_attachment = shortcuts.variable("CaptureText")
    token = shortcuts.frontmatter_token(
        uuid_attachment,
        created_attachment,
        body_attachment,
        "text",
    )
    content = _render(
        token,
        {
            ("output", "11111111-1111-4111-8111-111111111111"): "11111111-1111-4111-8111-111111111111",
            ("output", "22222222-2222-4222-8222-222222222222"): "2026-11-24T01:52:00+09:00",
            ("Variable", "CaptureText"): body,
        },
    )

    frontmatter, remainder = content.split("\n---\n", 1)
    assert frontmatter.startswith("---\nid: 11111111-1111-4111-8111-111111111111\n")
    assert "capture_kind: text" in frontmatter
    assert "key: value" not in frontmatter
    assert remainder.startswith("## 원문\n")
    assert body in remainder


def test_actual_capture_frontmatter_is_std7_unchanged(kos, vault, clock) -> None:
    runtime_id = "11111111-1111-4111-8111-111111111111"
    created = iso(clock.now())
    body = "현장 메모 +&#\n<% templater %>\n{{ quickadd }}"
    content = _render(
        shortcuts.frontmatter_token(
            shortcuts.attachment(runtime_id, "Updated Text"),
            shortcuts.attachment("22222222-2222-4222-8222-222222222222", "Formatted Date"),
            shortcuts.variable("CaptureText"),
            "text",
        ),
        {
            ("output", runtime_id): runtime_id,
            ("output", "22222222-2222-4222-8222-222222222222"): created,
            ("Variable", "CaptureText"): body,
        },
    )
    path = f"00_Inbox/Captures/{runtime_id}.md"
    vault.write(path, content)
    before = vault.path(path).read_bytes()

    receipt = kos.run(
        "standardize.preview",
        {"paths": [path], "digests": {path: kos.vault.sha(path)}},
    )
    assert receipt["status"] == "completed", receipt
    outcome = receipt["outcome"]
    assert outcome["notes"] == []
    assert outcome["skipped"] == []
    assert outcome["unchanged"] == 1
    assert vault.path(path).read_bytes() == before


def test_emitted_uuid_format_preserves_random_digits_and_variant() -> None:
    workflow = shortcuts.build_workflow(shortcuts.CAPTURE)
    format_action = next(
        action
        for action in workflow["WFWorkflowActions"]
        if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.text.replace"
        and isinstance(
            action["WFWorkflowActionParameters"].get("WFReplaceTextReplace"),
            dict,
        )
    )
    params = format_action["WFWorkflowActionParameters"]
    pattern = params["WFReplaceTextFind"]
    replacement_token = params["WFReplaceTextReplace"]

    sample = "0123456789abcdef0123456789abcd"  # 30 random hex digits
    for variant in ("8", "9", "a", "b"):
        values: dict[tuple[str, str], str] = {}
        for attachment in replacement_token["Value"]["attachmentsByRange"].values():
            if attachment["Type"] == "ActionOutput":
                values[("output", attachment["OutputUUID"])] = variant
            else:
                values[(attachment["Type"], attachment["VariableName"])] = variant
        replacement = _render(replacement_token, values)
        # Shortcuts writes $1 backreferences; Python's regex oracle uses \1.
        python_replacement = re.sub(r"\$(\d+)", lambda match: "\\" + match.group(1), replacement)
        formatted = re.sub(pattern, python_replacement, sample)

        parsed = uuidlib.UUID(formatted)
        assert parsed.version == 4
        assert parsed.variant == uuidlib.RFC_4122
        assert formatted[14] == "4"
        assert formatted[19] == variant
        assert formatted == (
            f"{sample[:8]}-{sample[8:12]}-4{sample[12:15]}-"
            f"{variant}{sample[15:18]}-{sample[18:30]}"
        )
