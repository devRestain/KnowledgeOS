"""Evidence-backed native Apple Shortcuts authoring for KnowledgeOS mobile flows.

Provenance boundaries:
- Built-in action identifiers, concrete native ActionClass resolution,
  parameter keys, and WFConditions (WFContentPredicateTableTemplate) structure:
  verified read-only against macOS 26.6.2 WorkflowKit/ActionKit
  (WFActionRegistry).
- Workflow envelope (WFWorkflowClientVersion 5114.0.8.3, minimum version 900),
  Working Copy EntityAppIntent / FileDetailsIntent templates, `repo` parameter
  keys across all four EntityAppIntents, `pathInRepo` property aggrandizement,
  and literal `KnowledgeHub` AppEntity value: loaded from private non-executed
  owner iPhone reference `.wflow` plists supplied via CLI arguments (never
  hardcoding opaque image URIs or private reference blobs in source).
- Native iPhone import, actual iPhone execution, and device-level Working Copy
  coverage of untracked/deleted files or no-change push semantics remain unrun
  until parent signing and owner device observation.
"""

from __future__ import annotations

import argparse
import copy
import plistlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OBJECT_REPLACEMENT = "\ufffc"
UUID_NAMESPACE = uuid.UUID("7f3b8f7a-2a1d-4d4a-9b92-0a4a4c1d9e01")

# Observed in owner's iPhone export (native-actions-sanitized.json).
CLIENT_VERSION = "5114.0.8.3"
MIN_CLIENT_VERSION = 900
MIN_CLIENT_VERSION_STRING = "900"

WC_BUNDLE_ID = "com.appliedphasor.working-copy"
WC_TEAM_ID = "4U9NSKBV63"
WC_GET_FILES_ID = f"{WC_BUNDLE_ID}.GetFilesEntityAppIntent"
WC_FILE_DETAILS_ID = f"{WC_BUNDLE_ID}.FileDetailsIntent"
WC_PULL_ID = f"{WC_BUNDLE_ID}.PullRepositoryEntityAppIntent"
WC_COMMIT_ID = f"{WC_BUNDLE_ID}.CommitRepositoryEntityAppIntent"
WC_PUSH_ID = f"{WC_BUNDLE_ID}.PushRepositoryEntityAppIntent"
WC_ENTITY_INTENT_IDS = frozenset(
    {WC_GET_FILES_ID, WC_PULL_ID, WC_COMMIT_ID, WC_PUSH_ID}
)
REPO_IDENTIFIER = "KnowledgeHub"

QUICKADD_CAPTURE_BASE = (
    "obsidian://quickadd?vault=KnowledgeHub&choice=KOS%20Capture"
)
OBSIDIAN_RETURN_URI = "obsidian://open?vault=KnowledgeHub"
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


@dataclass(frozen=True)
class WorkingCopyTemplates:
    """Cloned native Working Copy action templates from private reference plists."""

    repo_entity: dict[str, Any]
    get_files: dict[str, Any]
    file_details: dict[str, Any]
    path_in_repo_text: dict[str, Any]
    pull_repo: dict[str, Any]
    commit_repo: dict[str, Any]
    push_repo: dict[str, Any]


def _load_wflow_actions(path: Path) -> list[dict[str, Any]]:
    data = plistlib.loads(path.read_bytes())
    actions = data.get("WFWorkflowActions")
    if not isinstance(actions, list) or not actions:
        raise ValueError(f"Reference plist has no WFWorkflowActions: {path}")
    return actions


def _find_action(
    actions: list[dict[str, Any]],
    identifier: str,
    source: Path,
) -> dict[str, Any]:
    for act in actions:
        if act.get("WFWorkflowActionIdentifier") == identifier:
            return copy.deepcopy(act)
    raise ValueError(f"Missing action {identifier!r} in reference plist: {source}")


def _validate_wc_descriptor(action: dict[str, Any], expected_intent: str) -> None:
    params = action.get("WFWorkflowActionParameters", {})
    desc = params.get("AppIntentDescriptor")
    if not isinstance(desc, dict):
        raise ValueError(f"Missing AppIntentDescriptor on {expected_intent}")
    if (
        desc.get("BundleIdentifier") != WC_BUNDLE_ID
        or desc.get("TeamIdentifier") != WC_TEAM_ID
        or desc.get("AppIntentIdentifier") != expected_intent
    ):
        raise ValueError(f"Unexpected AppIntentDescriptor on {expected_intent}: {desc}")


def _find_path_in_repo_text(
    actions: list[dict[str, Any]],
    source: Path,
) -> dict[str, Any]:
    for act in actions:
        if act.get("WFWorkflowActionIdentifier") != "is.workflow.actions.gettext":
            continue
        text_param = act.get("WFWorkflowActionParameters", {}).get("WFTextActionText")
        if not isinstance(text_param, dict):
            continue
        attachments = text_param.get("Value", {}).get("attachmentsByRange", {})
        for att in attachments.values():
            aggs = att.get("Aggrandizements", [])
            if any(
                a.get("Type") == "WFPropertyVariableAggrandizement"
                and a.get("PropertyName") == "pathInRepo"
                for a in aggs
            ):
                return copy.deepcopy(act)
    raise ValueError(f"Missing pathInRepo gettext template in reference plist: {source}")


def load_wc_templates(
    repo_entity_ref: Path,
    wc_actions_ref: Path,
) -> WorkingCopyTemplates:
    """Load and validate native Working Copy templates from private reference plists."""
    repo_actions = _load_wflow_actions(repo_entity_ref)
    commit_with_repo = _find_action(repo_actions, WC_COMMIT_ID, repo_entity_ref)
    _validate_wc_descriptor(commit_with_repo, "CommitRepositoryEntityAppIntent")
    repo_entity = copy.deepcopy(
        commit_with_repo.get("WFWorkflowActionParameters", {}).get("repo")
    )
    if (
        not isinstance(repo_entity, dict)
        or repo_entity.get("identifier") != REPO_IDENTIFIER
        or repo_entity.get("title", {}).get("key") != REPO_IDENTIFIER
    ):
        raise ValueError(
            f"Expected literal {REPO_IDENTIFIER!r} AppEntity dict in {repo_entity_ref}"
        )
    # Strip optional display-only presentation image (opaque URI/cache) while
    # preserving exact repository identity (identifier, title, subtitle).
    repo_entity.pop("image", None)

    ref_actions = _load_wflow_actions(wc_actions_ref)
    get_files = _find_action(ref_actions, WC_GET_FILES_ID, wc_actions_ref)
    file_details = _find_action(ref_actions, WC_FILE_DETAILS_ID, wc_actions_ref)
    path_text = _find_path_in_repo_text(ref_actions, wc_actions_ref)
    pull_repo = _find_action(ref_actions, WC_PULL_ID, wc_actions_ref)
    commit_repo = _find_action(ref_actions, WC_COMMIT_ID, wc_actions_ref)
    push_repo = _find_action(ref_actions, WC_PUSH_ID, wc_actions_ref)

    for action, intent_name in (
        (get_files, "GetFilesEntityAppIntent"),
        (pull_repo, "PullRepositoryEntityAppIntent"),
        (commit_repo, "CommitRepositoryEntityAppIntent"),
        (push_repo, "PushRepositoryEntityAppIntent"),
    ):
        _validate_wc_descriptor(action, intent_name)
        if "repo" not in action.get("WFWorkflowActionParameters", {}):
            raise ValueError(
                f"Expected proven 'repo' key on {intent_name} in {wc_actions_ref}"
            )

    gf_params = get_files["WFWorkflowActionParameters"]
    if gf_params.get("status") != "modified" or gf_params.get("path") != "/*":
        raise ValueError(
            f"Expected GetFiles status='modified' and path='/*' in {wc_actions_ref}"
        )
    if "AppIntentDescriptor" in file_details.get("WFWorkflowActionParameters", {}):
        raise ValueError(
            f"Unexpected AppIntentDescriptor on FileDetailsIntent in {wc_actions_ref}"
        )
    if "files" not in file_details.get("WFWorkflowActionParameters", {}):
        raise ValueError(f"Missing 'files' key on FileDetailsIntent in {wc_actions_ref}")
    if commit_repo["WFWorkflowActionParameters"].get("target") != "modified":
        raise ValueError(f"Expected Commit target='modified' in {wc_actions_ref}")

    return WorkingCopyTemplates(
        repo_entity=repo_entity,
        get_files=get_files,
        file_details=file_details,
        path_in_repo_text=path_text,
        pull_repo=pull_repo,
        commit_repo=commit_repo,
        push_repo=push_repo,
    )


def _obsidian_return_actions(prefix: str) -> list[dict[str, Any]]:
    u_url = step_uuid(f"{prefix}.obsidian_url")
    u_open = step_uuid(f"{prefix}.obsidian_open")
    return [
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.url",
            "WFWorkflowActionParameters": {
                "UUID": u_url,
                "WFURLActionURL": OBSIDIAN_RETURN_URI,
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.openurl",
            "WFWorkflowActionParameters": {
                "UUID": u_open,
                "WFInput": token_attachment(u_url, "URL"),
            },
        },
    ]


def build_kos_pull(templates: WorkingCopyTemplates) -> dict[str, Any]:
    """Build unsigned `KOS Pull` shortcut definition.

    Flow:
    1. Native Working Copy Pull for fixed `KnowledgeHub` AppEntity only.
    2. On success, return to existing `KnowledgeHub` Vault in Obsidian.
       Provider errors abort the shortcut without opening Working Copy UI.
    """
    pull_action = copy.deepcopy(templates.pull_repo)
    pull_action["WFWorkflowActionParameters"]["UUID"] = step_uuid("pull.wc_pull")
    pull_action["WFWorkflowActionParameters"]["repo"] = copy.deepcopy(
        templates.repo_entity
    )
    actions: list[dict[str, Any]] = [
        pull_action,
        *_obsidian_return_actions("pull"),
    ]
    return workflow_envelope(actions)


def build_kos_push(templates: WorkingCopyTemplates) -> dict[str, Any]:
    """Build unsigned `KOS Push` shortcut definition.

    Flow:
    1. Native Working Copy Get Repository Files (`status='modified'`, `path='/*'`,
       pinned `KnowledgeHub` AppEntity).
    2. If modified files exist:
       a. Project body-free repo-relative paths via `FileDetailsIntent` +
          cloned `pathInRepo` property aggrandizement (never coercing raw files).
       b. Reject empty/missing projected paths with an alert + exit rather than
          blindly committing.
       c. Show ONE review and confirmation alert listing the repo-relative paths
          (Cancel aborts with zero commit/push effect).
       d. Format current date (`yyyy-MM-dd HH:mm`) into a dated default message
          and run native `CommitRepositoryEntityAppIntent` (`target='modified'`).
    3. Else (no modified files returned):
       a. Show ONE no-new-changes confirmation to push existing local commits
          without creating an empty commit (Cancel aborts).
    4. Run native `PushRepositoryEntityAppIntent` for `KnowledgeHub` (no auto
       pull, preserving real provider errors and never faking async success).
    5. On success, return to existing `KnowledgeHub` Vault in Obsidian.
    """
    u_get_files = step_uuid("push.wc_get_files")
    g_has_changes = step_uuid("push.group_has_changes")
    u_details = step_uuid("push.wc_file_details")
    u_paths_raw = step_uuid("push.paths_raw")
    u_paths_trimmed = step_uuid("push.paths_trimmed")
    g_missing_paths = step_uuid("push.group_missing_paths")
    u_missing_alert = step_uuid("push.missing_paths_alert")
    u_missing_exit = step_uuid("push.missing_paths_exit")
    u_missing_end = step_uuid("push.missing_paths_end")
    u_confirm_commit = step_uuid("push.confirm_commit")
    u_now = step_uuid("push.current_date")
    u_fmt_date = step_uuid("push.format_date")
    u_commit = step_uuid("push.wc_commit")
    u_confirm_push_only = step_uuid("push.confirm_push_only")
    u_changes_end = step_uuid("push.changes_end")
    u_push = step_uuid("push.wc_push")

    get_files_action = copy.deepcopy(templates.get_files)
    get_files_action["WFWorkflowActionParameters"]["UUID"] = u_get_files
    get_files_action["WFWorkflowActionParameters"]["repo"] = copy.deepcopy(
        templates.repo_entity
    )

    details_action = copy.deepcopy(templates.file_details)
    details_action["WFWorkflowActionParameters"]["UUID"] = u_details
    details_action["WFWorkflowActionParameters"]["files"] = token_attachment(
        u_get_files, "Get Repository Files"
    )

    paths_action = copy.deepcopy(templates.path_in_repo_text)
    paths_action["WFWorkflowActionParameters"]["UUID"] = u_paths_raw
    path_attachments = paths_action["WFWorkflowActionParameters"]["WFTextActionText"][
        "Value"
    ]["attachmentsByRange"]
    for att in path_attachments.values():
        att["OutputUUID"] = u_details
        att["OutputName"] = "Details"
        att["Type"] = "ActionOutput"

    commit_action = copy.deepcopy(templates.commit_repo)
    commit_action["WFWorkflowActionParameters"]["UUID"] = u_commit
    commit_action["WFWorkflowActionParameters"]["repo"] = copy.deepcopy(
        templates.repo_entity
    )
    commit_action["WFWorkflowActionParameters"]["target"] = "modified"
    commit_action["WFWorkflowActionParameters"]["message"] = token_string(
        ["Mobile sync ", action_output(u_fmt_date, "Formatted Date")]
    )

    push_action = copy.deepcopy(templates.push_repo)
    push_action["WFWorkflowActionParameters"]["UUID"] = u_push
    push_action["WFWorkflowActionParameters"]["repo"] = copy.deepcopy(
        templates.repo_entity
    )

    actions: list[dict[str, Any]] = [
        get_files_action,
        conditional_start(
            g_has_changes,
            [condition_row(u_get_files, "Get Repository Files", 100)],
            match_all=True,
        ),
        details_action,
        paths_action,
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.text.replace",
            "WFWorkflowActionParameters": {
                "UUID": u_paths_trimmed,
                "WFInput": token_string([action_output(u_paths_raw, "Text")]),
                "WFReplaceTextCaseSensitive": False,
                "WFReplaceTextFind": r"^\s+|\s+$",
                "WFReplaceTextRegularExpression": True,
                "WFReplaceTextReplace": "",
            },
        },
        conditional_start(
            g_missing_paths,
            [condition_row(u_paths_trimmed, "Updated Text", 101)],
            match_all=True,
        ),
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.alert",
            "WFWorkflowActionParameters": {
                "UUID": u_missing_alert,
                "WFAlertActionCancelButtonShown": False,
                "WFAlertActionMessage": "변경 파일 경로를 확인하지 못해 커밋을 중단했습니다.",
                "WFAlertActionTitle": "KOS Push",
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.exit",
            "WFWorkflowActionParameters": {
                "UUID": u_missing_exit,
            },
        },
        conditional_end(g_missing_paths, u_missing_end),
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.alert",
            "WFWorkflowActionParameters": {
                "UUID": u_confirm_commit,
                "WFAlertActionCancelButtonShown": True,
                "WFAlertActionMessage": token_string(
                    [
                        "아래 변경 파일을 커밋하고 푸시할까요?\n\n",
                        action_output(u_paths_raw, "Text"),
                    ]
                ),
                "WFAlertActionTitle": "KOS Push",
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.date",
            "WFWorkflowActionParameters": {
                "UUID": u_now,
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.format.date",
            "WFWorkflowActionParameters": {
                "UUID": u_fmt_date,
                # WFDateFieldParameter -> WFDateFieldParameterState ->
                # WFVariableStringParameterState requires WFTextTokenString
                # (single U+FFFC attachment resolves directly to .variable).
                "WFDate": token_string([action_output(u_now, "Date")]),
                "WFDateFormat": "yyyy-MM-dd HH:mm",
                "WFDateFormatStyle": "Custom",
            },
        },
        commit_action,
        conditional_otherwise(g_has_changes),
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.alert",
            "WFWorkflowActionParameters": {
                "UUID": u_confirm_push_only,
                "WFAlertActionCancelButtonShown": True,
                "WFAlertActionMessage": (
                    "새로 수정된 파일이 없습니다. 기존 로컬 커밋을 푸시할까요?"
                ),
                "WFAlertActionTitle": "KOS Push",
            },
        },
        conditional_end(g_has_changes, u_changes_end),
        push_action,
        *_obsidian_return_actions("push"),
    ]
    return workflow_envelope(actions)


def validate_workflow_references(workflow: dict[str, Any], name: str) -> None:
    """Validate internal UUID ordering, control-flow balance, and repo pinning."""
    seen_uuids: set[str] = set()
    group_stack: list[tuple[str, str]] = []

    def _walk_refs(obj: Any, idx: int) -> None:
        if isinstance(obj, dict):
            out_uuid = obj.get("OutputUUID")
            if isinstance(out_uuid, str) and out_uuid not in seen_uuids:
                raise ValueError(
                    f"{name}[{idx}]: OutputUUID {out_uuid} not produced by preceding action"
                )
            for value in obj.values():
                _walk_refs(value, idx)
        elif isinstance(obj, list):
            for item in obj:
                _walk_refs(item, idx)

    for idx, act in enumerate(workflow.get("WFWorkflowActions", [])):
        aid = act.get("WFWorkflowActionIdentifier", "")
        params = act.get("WFWorkflowActionParameters", {})
        if "SyncRepository" in aid or "OpenRepository" in aid:
            raise ValueError(f"{name}[{idx}]: disallowed Working Copy action {aid!r}")
        _walk_refs(params, idx)

        if aid in WC_ENTITY_INTENT_IDS:
            repo = params.get("repo")
            if (
                not isinstance(repo, dict)
                or repo.get("identifier") != REPO_IDENTIFIER
                or "image" in repo
            ):
                raise ValueError(
                    f"{name}[{idx}] {aid}: repo must be pinned {REPO_IDENTIFIER!r} AppEntity without opaque image"
                )

        if "WFControlFlowMode" in params:
            mode = params["WFControlFlowMode"]
            gid = params.get("GroupingIdentifier", "")
            if mode == 0:
                group_stack.append((aid, gid))
            elif mode == 1:
                if not group_stack or group_stack[-1] != (aid, gid):
                    raise ValueError(f"{name}[{idx}]: mismatched control-flow branch {gid}")
            elif mode == 2:
                if not group_stack or group_stack[-1] != (aid, gid):
                    raise ValueError(f"{name}[{idx}]: mismatched control-flow end {gid}")
                group_stack.pop()

        action_uuid = params.get("UUID")
        if isinstance(action_uuid, str):
            seen_uuids.add(action_uuid)

    if group_stack:
        raise ValueError(f"{name}: unclosed control-flow groups {group_stack}")


def write_shortcut(workflow: dict[str, Any], path: Path) -> Path:
    validate_workflow_references(workflow, path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(workflow, fmt=plistlib.FMT_BINARY))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Write unsigned KnowledgeOS iOS Shortcut plist definitions "
            "(KOS 캡처, KOS Pull, KOS Push) from private Working Copy reference plists."
        )
    )
    parser.add_argument(
        "--repo-entity-ref",
        type=Path,
        required=True,
        help="Private extracted Shortcut.wflow containing literal KnowledgeHub repo AppEntity.",
    )
    parser.add_argument(
        "--wc-actions-ref",
        type=Path,
        required=True,
        help=(
            "Private extracted Shortcut.wflow containing current Working Copy "
            "GetFiles/FileDetails/pathInRepo/Pull/Commit/Push templates."
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Explicit output directory for unsigned .shortcut files.",
    )
    args = parser.parse_args()
    templates = load_wc_templates(args.repo_entity_ref, args.wc_actions_ref)
    outputs = (
        ("KOS 캡처.shortcut", build_kos_capture()),
        ("KOS Pull.shortcut", build_kos_pull(templates)),
        ("KOS Push.shortcut", build_kos_push(templates)),
    )
    for filename, workflow in outputs:
        out_file = write_shortcut(workflow, args.out_dir / filename)
        print(f"Wrote {out_file} ({out_file.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
