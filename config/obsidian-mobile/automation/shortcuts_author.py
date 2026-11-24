"""Evidence-backed native Apple Shortcuts authoring for KnowledgeOS mobile flows.

Provenance boundaries:
- Built-in action identifiers, concrete native ActionClass resolution,
  parameter keys, and WFConditions (WFContentPredicateTableTemplate) structure:
  verified read-only against macOS 26.6.2 WorkflowKit/ActionKit
  (WFActionRegistry).
- Workflow envelope (WFWorkflowClientVersion 5114.0.8.3, minimum version 900)
  and WFTextTokenAttachment ActionOutput shape: observed from the owner's
  iPhone export (Working Copy Reference.shortcut, which contains only four
  Working Copy actions and no built-in actions).
- Native iPhone import and actual iPhone execution are unrun until parent
  signing and owner device observation.
"""

from __future__ import annotations

import argparse
import plistlib
import uuid
from pathlib import Path
from typing import Any

OBJECT_REPLACEMENT = "\ufffc"
UUID_NAMESPACE = uuid.UUID("7f3b8f7a-2a1d-4d4a-9b92-0a4a4c1d9e01")

# Observed in owner's iPhone export (native-actions-sanitized.json).
CLIENT_VERSION = "5114.0.8.3"
MIN_CLIENT_VERSION = 900
MIN_CLIENT_VERSION_STRING = "900"

QUICKADD_CAPTURE_BASE = (
    "obsidian://quickadd?vault=KnowledgeHub&choice=KOS%20Capture"
)
# Applied only to newline-joined URLs already extracted by native detect.link,
# so entire detected URL lines are filtered for HTTP(S) without matching
# embedded substrings inside non-HTTP app URLs.
HTTP_URL_LINE_PATTERN = r"(?m)^https?://\S+$"


def step_uuid(name: str) -> str:
    return str(uuid.uuid5(UUID_NAMESPACE, name)).upper()


def action_output(output_uuid: str, output_name: str) -> dict[str, Any]:
    return {
        "OutputName": output_name,
        "OutputUUID": output_uuid,
        "Type": "ActionOutput",
    }


def token_attachment(output_uuid: str, output_name: str) -> dict[str, Any]:
    return {
        "Value": action_output(output_uuid, output_name),
        "WFSerializationType": "WFTextTokenAttachment",
    }


def token_string(parts: list[str | dict[str, Any]]) -> dict[str, Any]:
    """Build a WFTextTokenString with exact UTF-16 code unit attachment offsets."""
    chunks: list[str] = []
    attachments: dict[str, dict[str, Any]] = {}
    utf16_offset = 0
    for part in parts:
        if isinstance(part, str):
            chunks.append(part)
            utf16_offset += len(part.encode("utf-16-le")) // 2
        else:
            attachments[f"{{{utf16_offset}, 1}}"] = part
            chunks.append(OBJECT_REPLACEMENT)
            utf16_offset += 1
    return {
        "Value": {
            "attachmentsByRange": attachments,
            "string": "".join(chunks),
        },
        "WFSerializationType": "WFTextTokenString",
    }


def condition_row(output_uuid: str, output_name: str, condition_code: int) -> dict[str, Any]:
    """Build one WFConditions row (100 = has any value, 101 = does not have any value)."""
    return {
        "WFCondition": condition_code,
        "WFInput": {
            "Type": "Variable",
            "Variable": token_attachment(output_uuid, output_name),
        },
    }


def conditional_start(
    group_id: str,
    rows: list[dict[str, Any]],
    *,
    match_all: bool = True,
) -> dict[str, Any]:
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_id,
            "WFControlFlowMode": 0,
            "WFConditions": {
                "Value": {
                    "WFActionParameterFilterPrefix": 1 if match_all else 0,
                    "WFActionParameterFilterTemplates": rows,
                    "WFContentPredicateBoundedDate": False,
                },
                "WFSerializationType": "WFContentPredicateTableTemplate",
            },
        },
    }


def conditional_otherwise(group_id: str) -> dict[str, Any]:
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_id,
            "WFControlFlowMode": 1,
        },
    }


def conditional_end(group_id: str, end_uuid: str) -> dict[str, Any]:
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_id,
            "UUID": end_uuid,
            "WFControlFlowMode": 2,
        },
    }


def workflow_envelope(
    actions: list[dict[str, Any]],
    *,
    icon_color: int = -2873601,
    icon_glyph: int = 61440,
) -> dict[str, Any]:
    return {
        "WFQuickActionSurfaces": [],
        "WFWorkflowActions": actions,
        "WFWorkflowClientVersion": CLIENT_VERSION,
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowHasShortcutInputVariables": False,
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowIcon": {
            "WFWorkflowIconGlyphNumber": icon_glyph,
            "WFWorkflowIconStartColor": icon_color,
        },
        "WFWorkflowMinimumClientVersion": MIN_CLIENT_VERSION,
        "WFWorkflowMinimumClientVersionString": MIN_CLIENT_VERSION_STRING,
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowTypes": ["WFWorkflowTypeShowInSearch"],
    }


def build_kos_capture() -> dict[str, Any]:
    """Build unsigned `KOS 캡처` shortcut definition.

    Flow:
    1. Ask for multiline memo text first (supports iOS keyboard dictation;
       Cancel aborts without creating a note).
    2. Read clipboard, extract valid URLs locally via native `detect.link`,
       combine detected URLs by newline, and filter entire lines for HTTP(S).
    3. Only when an HTTP(S) URL is found, show the actual URL in a menu asking
       whether to include it (`포함` / `포함 안 함`).
    4. Abort locally with an alert if both trimmed memo and approved URL are
       empty.
    5. URL-encode raw memo and approved URL once each and open the existing
       `KOS Capture` QuickAdd receiver URI.
    """
    u_memo = step_uuid("capture.ask_memo")
    u_clip = step_uuid("capture.get_clipboard")
    u_detect_urls = step_uuid("capture.detect_urls")
    u_combine_urls = step_uuid("capture.combine_urls")
    u_match = step_uuid("capture.match_url")
    g_has_url = step_uuid("capture.group_has_url")
    u_first_url = step_uuid("capture.first_url")
    g_url_menu = step_uuid("capture.group_url_menu")
    u_url_yes = step_uuid("capture.url_yes")
    u_url_no = step_uuid("capture.url_no")
    u_menu_end = step_uuid("capture.menu_end")
    u_url_none = step_uuid("capture.url_none")
    u_url_decision = step_uuid("capture.url_decision")
    u_trimmed_memo = step_uuid("capture.trimmed_memo")
    g_empty = step_uuid("capture.group_empty")
    u_empty_alert = step_uuid("capture.empty_alert")
    u_empty_exit = step_uuid("capture.empty_exit")
    u_empty_end = step_uuid("capture.empty_end")
    u_enc_body = step_uuid("capture.encode_body")
    u_enc_url = step_uuid("capture.encode_url")
    u_uri = step_uuid("capture.build_uri")
    u_open = step_uuid("capture.open_uri")

    actions: list[dict[str, Any]] = [
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.ask",
            "WFWorkflowActionParameters": {
                "UUID": u_memo,
                "WFAllowsMultilineText": True,
                "WFAskActionPrompt": "메모 입력",
                "WFInputType": "Text",
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.getclipboard",
            "WFWorkflowActionParameters": {
                "UUID": u_clip,
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.detect.link",
            "WFWorkflowActionParameters": {
                "UUID": u_detect_urls,
                "WFInput": token_string([action_output(u_clip, "Clipboard")]),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.text.combine",
            "WFWorkflowActionParameters": {
                "UUID": u_combine_urls,
                "WFTextSeparator": "New Lines",
                "text": token_attachment(u_detect_urls, "URLs"),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.text.match",
            "WFWorkflowActionParameters": {
                "UUID": u_match,
                "WFMatchTextCaseSensitive": False,
                "WFMatchTextPattern": HTTP_URL_LINE_PATTERN,
                "text": token_string([action_output(u_combine_urls, "Combined Text")]),
            },
        },
        conditional_start(
            g_has_url,
            [condition_row(u_match, "Matches", 100)],
            match_all=True,
        ),
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.getitemfromlist",
            "WFWorkflowActionParameters": {
                "UUID": u_first_url,
                "WFInput": token_attachment(u_match, "Matches"),
                "WFItemSpecifier": "First Item",
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": g_url_menu,
                "WFControlFlowMode": 0,
                "WFMenuItems": ["포함", "포함 안 함"],
                "WFMenuPrompt": token_string(
                    [
                        "출처 URL을 포함할까요?\n",
                        action_output(u_first_url, "Item from List"),
                    ]
                ),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": g_url_menu,
                "WFControlFlowMode": 1,
                "WFMenuItemTitle": "포함",
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
            "WFWorkflowActionParameters": {
                "UUID": u_url_yes,
                "WFTextActionText": token_string(
                    [action_output(u_first_url, "Item from List")]
                ),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": g_url_menu,
                "WFControlFlowMode": 1,
                "WFMenuItemTitle": "포함 안 함",
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
            "WFWorkflowActionParameters": {
                "UUID": u_url_no,
                "WFTextActionText": "",
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": g_url_menu,
                "UUID": u_menu_end,
                "WFControlFlowMode": 2,
            },
        },
        conditional_otherwise(g_has_url),
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
            "WFWorkflowActionParameters": {
                "UUID": u_url_none,
                "WFTextActionText": "",
            },
        },
        conditional_end(g_has_url, u_url_decision),
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.text.replace",
            "WFWorkflowActionParameters": {
                "UUID": u_trimmed_memo,
                "WFInput": token_string([action_output(u_memo, "Provided Input")]),
                "WFReplaceTextCaseSensitive": False,
                "WFReplaceTextFind": r"^\s+|\s+$",
                "WFReplaceTextRegularExpression": True,
                "WFReplaceTextReplace": "",
            },
        },
        conditional_start(
            g_empty,
            [
                condition_row(u_trimmed_memo, "Updated Text", 101),
                condition_row(u_url_decision, "If Result", 101),
            ],
            match_all=True,
        ),
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.alert",
            "WFWorkflowActionParameters": {
                "UUID": u_empty_alert,
                "WFAlertActionCancelButtonShown": False,
                "WFAlertActionMessage": "저장할 메모나 출처 URL이 없습니다.",
                "WFAlertActionTitle": "KOS 캡처",
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.exit",
            "WFWorkflowActionParameters": {
                "UUID": u_empty_exit,
            },
        },
        conditional_end(g_empty, u_empty_end),
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.urlencode",
            "WFWorkflowActionParameters": {
                "UUID": u_enc_body,
                "WFEncodeMode": "Encode",
                "WFInput": token_string([action_output(u_memo, "Provided Input")]),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.urlencode",
            "WFWorkflowActionParameters": {
                "UUID": u_enc_url,
                "WFEncodeMode": "Encode",
                "WFInput": token_string([action_output(u_url_decision, "If Result")]),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.url",
            "WFWorkflowActionParameters": {
                "UUID": u_uri,
                "WFURLActionURL": token_string(
                    [
                        f"{QUICKADD_CAPTURE_BASE}&value-body=",
                        action_output(u_enc_body, "URL Encoded Text"),
                        "&value-source_url=",
                        action_output(u_enc_url, "URL Encoded Text"),
                    ]
                ),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.openurl",
            "WFWorkflowActionParameters": {
                "UUID": u_open,
                "WFInput": token_attachment(u_uri, "URL"),
            },
        },
    ]
    return workflow_envelope(actions)


def write_shortcut(workflow: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(workflow, fmt=plistlib.FMT_BINARY))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write unsigned KnowledgeOS iOS Shortcut plist definitions."
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Explicit output directory for unsigned .shortcut files.",
    )
    args = parser.parse_args()
    out_file = write_shortcut(build_kos_capture(), args.out_dir / "KOS 캡처.shortcut")
    print(f"Wrote {out_file} ({out_file.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
