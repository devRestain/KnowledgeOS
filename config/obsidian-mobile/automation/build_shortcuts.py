#!/usr/bin/env python3
"""Build the five KnowledgeOS iOS Shortcuts as unsigned legacy plists.

The outputs are unsigned ``.shortcut`` property lists.  They are deliberately
generated with the Python standard library so the definitions can be reviewed
and signed by Apple's ``shortcuts sign --mode anyone`` on macOS.

Design constraints enforced here:

* capture content is never evaluated by QuickAdd or Templater;
* the capture body is inserted as one text-token attachment, so no delimiter
  inside the body can become YAML, a filename or a shortcut parameter;
* the UUID is generated at runtime from 30 random hex digits plus an
  independent RFC 4122 variant choice (122 random bits);
* every URL query value is encoded as one component before it is assembled;
* the Working Copy error string is checked after pull, commit and push;
* no action claims that Obsidian saved, or that Working Copy finished a push.
"""

from __future__ import annotations

import argparse
import plistlib
import uuid
from pathlib import Path

APP_BUNDLE = "com.appliedphasor.working-copy"
APP_NAME = "Working Copy"

CAPTURE = "KOS 캡처"
VOICE = "KOS 음성"
TODAY = "KOS 오늘"
PULL = "KOS 받기"
SEND = "KOS 보내기"

SHORTCUT_NAMES = (CAPTURE, VOICE, TODAY, PULL, SEND)

VAULT_DEFAULT = "KnowledgeHub"
REPO_DEFAULT = "KnowledgeHub"

# The share-sheet input types KnowledgeOS accepts at capture time.
CAPTURE_INPUT_CLASSES = [
    "WFStringContentItem",
    "WFURLContentItem",
    "WFSafariWebPageContentItem",
    "WFArticleContentItem",
]

# RFC 4122 version 4 layout: 8-4-4-4-12, with a fixed version nibble and a
# variant nibble chosen from 8/9/a/b.  The remaining 30 hex digits are random.
UUID_SEGMENTS = (8, 4, 4, 4, 12)
UUID_VERSION = "4"
UUID_VARIANTS = ("8", "9", "a", "b")
HEX_DIGITS = tuple("0123456789abcdef")

# Action identifiers and parameter keys used below.  They are kept together so
# a future Shortcuts schema change has one review surface.
TEXT = "is.workflow.actions.gettext"
URL = "is.workflow.actions.url"
OPEN_URL = "is.workflow.actions.openurl"
ASK = "is.workflow.actions.ask"
ALERT = "is.workflow.actions.alert"
SHOW_RESULT = "is.workflow.actions.showresult"
EXIT = "is.workflow.actions.exit"
DICTATE = "is.workflow.actions.dictatetext"
DEVICE_DETAILS = "is.workflow.actions.getdevicedetails"
DATE = "is.workflow.actions.date"
FORMAT_DATE = "is.workflow.actions.format.date"
URL_ENCODE = "is.workflow.actions.urlencode"
REPLACE_TEXT = "is.workflow.actions.text.replace"
SPLIT_TEXT = "is.workflow.actions.text.split"
GET_ITEM = "is.workflow.actions.getitemfromlist"
GET_NAME = "is.workflow.actions.getitemname"
SET_VARIABLE = "is.workflow.actions.setvariable"
APPEND_VARIABLE = "is.workflow.actions.appendvariable"
COMBINE_TEXT = "is.workflow.actions.text.combine"
CONDITIONAL = "is.workflow.actions.conditional"
REPEAT_COUNT = "is.workflow.actions.repeat.count"

NO_INPUT_CONTINUE = {"Name": "WFWorkflowNoInputBehaviorContinue", "Parameters": {}}

VAR_VAULT = "VaultName"
VAR_REPO = "RepoName"
VAR_CAPTURE = "CaptureText"
VAR_UUID_HEX = "KOSUUIDHex"


def _new_uuid() -> str:
    return str(uuid.uuid4()).upper()


def attachment(output_uuid: str, output_name: str) -> dict:
    """An action output reference used where a picker or bare value is expected."""
    return {
        "WFSerializationType": "WFTextTokenAttachment",
        "Value": {
            "Type": "ActionOutput",
            "OutputUUID": output_uuid,
            "OutputName": output_name,
        },
    }


def variable(name: str) -> dict:
    return {
        "WFSerializationType": "WFTextTokenAttachment",
        "Value": {"Type": "Variable", "VariableName": name},
    }


def shortcut_input() -> dict:
    return {
        "WFSerializationType": "WFTextTokenAttachment",
        "Value": {"Type": "ExtensionInput"},
    }


def subject(reference: dict) -> dict:
    """The legacy If subject wrapper, which is different from an input picker."""
    if reference.get("Type") == "Variable" and "Variable" in reference:
        return reference
    return {"Type": "Variable", "Variable": reference}


def token_string(*parts: object) -> dict:
    """A WFTextTokenString with U+FFFC placeholders and UTF-16 NSRange offsets.

    ``parts`` may contain plain strings or attachment wrappers.  Inline
    attachments store the raw token value; the outer
    ``WFTextTokenAttachment`` wrapper is only for picker parameters.
    """
    text = ""
    ranges: dict[str, dict] = {}
    for part in parts:
        if isinstance(part, str):
            text += part
            continue
        if not isinstance(part, dict):
            raise TypeError(f"text part must be str or attachment, got {type(part).__name__}")
        if part.get("WFSerializationType") == "WFTextTokenAttachment":
            value = part["Value"]
        elif "Type" in part:
            value = part
        else:
            raise ValueError(f"unrecognised text attachment: {part!r}")
        # Shortcuts uses NSRange, whose offsets count UTF-16 code units.  Korean
        # text is one unit per character; emoji are two.
        offset = len(text.encode("utf-16-le")) // 2
        ranges[f"{{{offset}, 1}}"] = value
        text += "\ufffc"
    return {
        "WFSerializationType": "WFTextTokenString",
        "Value": {"string": text, "attachmentsByRange": ranges},
    }


def frontmatter_token(uuid_ref: dict, created_ref: dict, body_ref: dict, capture_kind: str) -> dict:
    """The complete note, with only the runtime values as attachments.

    The untrusted capture is one attachment.  It cannot contribute a line to
    the YAML block, a title, or a shortcut parameter.
    """
    if capture_kind not in ("text", "voice"):
        raise ValueError(f"unsupported capture kind: {capture_kind}")
    return token_string(
        "---\n"
        "id: ",
        uuid_ref,
        "\n"
        "type: capture\n"
        "status: unprocessed\n"
        "created: ",
        created_ref,
        "\n"
        "aliases: []\n"
        "tags: []\n"
        "ai_policy: approve\n"
        "sensitivity: personal\n"
        "captured_from: ios_shortcut\n"
        f"capture_kind: {capture_kind}\n"
        "---\n"
        "## 원문\n",
        body_ref,
        "\n## 맥락\n",
    )


class _Builder:
    def __init__(
        self,
        name: str,
        *,
        no_input: dict,
        types: list[str] | None = None,
        input_classes: list[str] | None = None,
    ) -> None:
        self.name = name
        self.no_input = no_input
        self.types = list(types or [])
        self.input_classes = list(input_classes or [])
        self.actions: list[dict] = []
        self.questions: list[dict] = []
        self.has_shortcut_input = False

    def add(self, identifier: str, **params: object) -> dict:
        action = {
            "WFWorkflowActionIdentifier": identifier,
            "WFWorkflowActionParameters": {"UUID": _new_uuid(), **params},
        }
        self.actions.append(action)
        return action

    @staticmethod
    def output(action: dict, name: str) -> dict:
        return attachment(action["WFWorkflowActionParameters"]["UUID"], name)

    def import_question(self, prompt: str, default: str) -> dict:
        action = self.add(TEXT, WFTextActionText=default)
        self.questions.append(
            {
                "ActionIndex": len(self.actions) - 1,
                "Category": "Parameter",
                "DefaultValue": default,
                "ParameterKey": "WFTextActionText",
                "Text": prompt,
            }
        )
        return action

    def set_variable(self, name: str, value: dict) -> dict:
        return self.add(SET_VARIABLE, WFInput=value, WFVariableName=name)

    def use_shortcut_input(self) -> dict:
        self.has_shortcut_input = True
        return shortcut_input()

    def alert(self, title: str, message: object, *, cancel: bool = True) -> dict:
        return self.add(
            ALERT,
            WFAlertActionTitle=title,
            WFAlertActionMessage=message,
            WFAlertActionCancelButtonShown=cancel,
        )

    def stop(self) -> dict:
        return self.add(EXIT)

    def if_(self, value: dict, condition: int, comparison: str | None = None) -> str:
        group = _new_uuid()
        params: dict[str, object] = {
            "GroupingIdentifier": group,
            "WFControlFlowMode": 0,
            "WFInput": subject(value),
            "WFCondition": condition,
        }
        if comparison is not None:
            params["WFConditionalActionString"] = comparison
        self.add(CONDITIONAL, **params)
        return group

    def otherwise(self, group: str) -> dict:
        return self.add(CONDITIONAL, GroupingIdentifier=group, WFControlFlowMode=1)

    def end_if(self, group: str) -> dict:
        return self.add(CONDITIONAL, GroupingIdentifier=group, WFControlFlowMode=2)

    def repeat_start(self, count: int) -> str:
        group = _new_uuid()
        self.add(
            REPEAT_COUNT,
            GroupingIdentifier=group,
            WFControlFlowMode=0,
            WFRepeatCount=count,
        )
        return group

    def repeat_end(self, group: str) -> dict:
        return self.add(REPEAT_COUNT, GroupingIdentifier=group, WFControlFlowMode=2)

    def device_gate(self) -> None:
        """Stop before any capture or repository effect on non-iOS devices."""
        detail = self.add(DEVICE_DETAILS, WFDeviceDetail="Device Model")
        model = self.output(detail, "Device Details")

        iphone = self.if_(model, 8, "iPhone")  # begins with
        self.otherwise(iphone)
        ipad = self.if_(model, 8, "iPad")
        self.otherwise(ipad)
        self.alert(
            "지원하지 않는 기기",
            "이 단축어는 iPhone 또는 iPad에서만 실행할 수 있습니다.",
            cancel=False,
        )
        self.stop()
        self.end_if(ipad)
        self.end_if(iphone)

    def uuid_v4(self) -> dict:
        """Generate a full 122-bit RFC 4122 version 4 UUID natively.

        Thirty random hex digits are collected with a native Repeat; the
        variant is a separate random choice from 8/9/a/b.  A single regex
        replacement inserts the version nibble and the dash layout.  The
        replacement groups are literal ``$1``...``$5``; the variant is an
        inline token, not shell-expanded text.
        """
        alphabet = self.add(TEXT, WFTextActionText="".join(HEX_DIGITS))
        alphabet_split = self.add(
            SPLIT_TEXT,
            text=self.output(alphabet, "Text"),
            WFTextSeparator="Every Character",
        )
        alphabet_items = self.output(alphabet_split, "Split Text")

        loop = self.repeat_start(30)
        digit = self.add(
            GET_ITEM,
            WFItemSpecifier="Random Item",
            WFInput=alphabet_items,
        )
        self.add(
            APPEND_VARIABLE,
            WFInput=self.output(digit, "Item from List"),
            WFVariableName=VAR_UUID_HEX,
        )
        self.repeat_end(loop)
        digits = self.add(
            COMBINE_TEXT,
            text=variable(VAR_UUID_HEX),
            WFTextSeparator="Custom",
            WFTextCustomSeparator="",
        )

        variants = self.add(TEXT, WFTextActionText="\n".join(UUID_VARIANTS))
        variants_split = self.add(
            SPLIT_TEXT,
            text=self.output(variants, "Text"),
            WFTextSeparator="New Lines",
        )
        variant = self.add(
            GET_ITEM,
            WFItemSpecifier="Random Item",
            WFInput=self.output(variants_split, "Split Text"),
        )

        pattern = (
            rf"^([0-9a-f]{{{UUID_SEGMENTS[0]}}})"
            rf"([0-9a-f]{{{UUID_SEGMENTS[1]}}})"
            r"([0-9a-f]{3})([0-9a-f]{3})"
            rf"([0-9a-f]{{{UUID_SEGMENTS[4]}}})$"
        )
        replacement = token_string(
            f"$1-$2-{UUID_VERSION}$3-",
            self.output(variant, "Item from List"),
            "$4-$5",
        )
        formatted = self.add(
            REPLACE_TEXT,
            WFInput=self.output(digits, "Combined Text"),
            WFReplaceTextFind=pattern,
            WFReplaceTextReplace=replacement,
            WFReplaceTextCaseSensitive=True,
            WFReplaceTextRegularExpression=True,
        )
        return self.output(formatted, "Updated Text")

    def encode_component(self, source: dict) -> dict:
        """URL-encode one component with the native action, exactly once."""
        encoded = self.add(URL_ENCODE, WFEncodeMode="Encode", WFInput=source)
        return self.output(encoded, "URL Encoded Text")

    def app_intent(self, intent: str, **params: object) -> dict:
        return self.add(
            f"{APP_BUNDLE}.{intent}",
            AppIntentDescriptor={
                "BundleIdentifier": APP_BUNDLE,
                "Name": APP_NAME,
                "TeamIdentifier": "4U9NSKBV63",
                "AppIntentIdentifier": intent,
                "ActionRequiresAppInstallation": True,
            },
            **params,
        )

    def open_working_copy_status(self, repo: dict) -> dict:
        return self.app_intent(
            "OpenIntent",
            repo=repo,
            path="/",
            mode="status",
            clone=False,
        )


def _workflow(builder: _Builder) -> dict:
    return {
        "WFWorkflowClientVersion": "4018.0.4",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": 463140863,
            "WFWorkflowIconGlyphNumber": 61440,
        },
        "WFWorkflowActions": builder.actions,
        "WFWorkflowTypes": builder.types,
        "WFWorkflowInputContentItemClasses": builder.input_classes,
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowImportQuestions": builder.questions,
        "WFWorkflowNoInputBehavior": builder.no_input,
        "WFWorkflowHasShortcutInputVariables": builder.has_shortcut_input,
        "WFWorkflowHasOutputFallback": False,
        "WFQuickActionSurfaces": [],
        "WFWorkflowName": builder.name,
    }


def _capture(name: str, capture_kind: str) -> dict:
    text_capture = capture_kind == "text"
    builder = _Builder(
        name,
        no_input=NO_INPUT_CONTINUE,
        types=["ActionExtension"] if text_capture else [],
        input_classes=CAPTURE_INPUT_CLASSES if text_capture else [],
    )

    vault_question = builder.import_question(
        "KnowledgeOS Obsidian vault 이름 (기본 KnowledgeHub)",
        VAULT_DEFAULT,
    )
    builder.set_variable(VAR_VAULT, builder.output(vault_question, "Text"))
    builder.device_gate()

    if text_capture:
        input_ref = builder.use_shortcut_input()
        has_input = builder.if_(input_ref, 100)  # has any value
        combined = builder.add(
            COMBINE_TEXT,
            text=input_ref,
            WFTextSeparator="New Lines",
        )
        builder.set_variable(VAR_CAPTURE, builder.output(combined, "Combined Text"))
        builder.otherwise(has_input)
        asked = builder.add(
            ASK,
            WFInputType="Text",
            WFAskActionPrompt="캡처할 내용을 입력하세요.",
            WFAllowsMultilineText=True,
        )
        builder.set_variable(VAR_CAPTURE, builder.output(asked, "Provided Input"))
        builder.end_if(has_input)
    else:
        dictated = builder.add(DICTATE, WFDictateTextStopListening="After Pause")
        builder.set_variable(VAR_CAPTURE, builder.output(dictated, "Dictated Text"))

    trimmed = builder.add(
        REPLACE_TEXT,
        WFInput=variable(VAR_CAPTURE),
        WFReplaceTextFind=r"^\s+|\s+$",
        WFReplaceTextReplace="",
        WFReplaceTextCaseSensitive=True,
        WFReplaceTextRegularExpression=True,
    )
    empty = builder.if_(builder.output(trimmed, "Updated Text"), 101)  # has no value
    builder.alert("내용 없음", "캡처할 내용이 비어 있습니다.", cancel=False)
    builder.stop()
    builder.end_if(empty)

    uuid_ref = builder.uuid_v4()
    current_date = builder.add(DATE, WFDateActionMode="Current Date")
    created = builder.add(
        FORMAT_DATE,
        WFDate=builder.output(current_date, "Date"),
        WFDateFormatStyle="ISO 8601",
        WFISO8601IncludeTime=True,
    )
    created_ref = builder.output(created, "Formatted Date")

    content = builder.add(
        TEXT,
        WFTextActionText=frontmatter_token(uuid_ref, created_ref, variable(VAR_CAPTURE), capture_kind),
    )
    path = builder.add(
        TEXT,
        WFTextActionText=token_string("00_Inbox/Captures/", uuid_ref, ".md"),
    )

    vault_encoded = builder.encode_component(variable(VAR_VAULT))
    path_encoded = builder.encode_component(builder.output(path, "Text"))
    content_encoded = builder.encode_component(builder.output(content, "Text"))
    url = builder.add(
        URL,
        WFURLActionURL=token_string(
            "obsidian://new?vault=",
            vault_encoded,
            "&file=",
            path_encoded,
            "&content=",
            content_encoded,
        ),
    )
    builder.add(OPEN_URL, WFInput=builder.output(url, "URL"))
    return _workflow(builder)


def _today() -> dict:
    builder = _Builder(TODAY, no_input=NO_INPUT_CONTINUE)
    vault_question = builder.import_question(
        "KnowledgeOS Obsidian vault 이름 (기본 KnowledgeHub)",
        VAULT_DEFAULT,
    )
    builder.set_variable(VAR_VAULT, builder.output(vault_question, "Text"))
    builder.device_gate()
    vault_encoded = builder.encode_component(variable(VAR_VAULT))
    url = builder.add(
        URL,
        WFURLActionURL=token_string("obsidian://quickadd?vault=", vault_encoded, "&choice=Today"),
    )
    builder.add(OPEN_URL, WFInput=builder.output(url, "URL"))
    return _workflow(builder)


def _repo_builder(name: str) -> tuple[_Builder, dict]:
    builder = _Builder(name, no_input=NO_INPUT_CONTINUE)
    repo_question = builder.import_question(
        "Working Copy 저장소 이름 (기본 KnowledgeHub)",
        REPO_DEFAULT,
    )
    builder.set_variable(VAR_REPO, builder.output(repo_question, "Text"))
    builder.device_gate()
    return builder, token_string(variable(VAR_REPO))


def _pull() -> dict:
    builder, repo = _repo_builder(PULL)
    builder.alert(
        "저장소 가져오기",
        "Working Copy의 기존 저장소에서 원격 변경을 가져옵니다.\n계속할까요?",
        cancel=True,
    )
    pulled = builder.app_intent("PullRepositoryIntent", repo=repo, remote="")
    failed = builder.if_(builder.output(pulled, "Error"), 100)
    builder.alert(
        "가져오기 실패",
        token_string("Working Copy 오류: ", builder.output(pulled, "Error")),
        cancel=False,
    )
    builder.stop()
    builder.end_if(failed)
    builder.open_working_copy_status(repo)
    return _workflow(builder)


def _send() -> dict:
    builder, repo = _repo_builder(SEND)
    files = builder.app_intent(
        "GetFilesIntent",
        repo=repo,
        path="/",
        status="staged",
        archive=False,
        flatten="none",
    )
    names = builder.add(
        GET_NAME,
        WFInput=builder.output(files, "Files"),
        GetWebPageTitle=False,
    )
    builder.add(
        SHOW_RESULT,
        Text=token_string("STAGED 파일:\n", builder.output(names, "Name")),
    )
    message = builder.add(
        ASK,
        WFInputType="Text",
        WFAskActionPrompt="커밋 메시지를 입력하세요.",
        WFAllowsMultilineText=False,
    )
    trimmed_message = builder.add(
        REPLACE_TEXT,
        WFInput=builder.output(message, "Provided Input"),
        WFReplaceTextFind=r"^\s+|\s+$",
        WFReplaceTextReplace="",
        WFReplaceTextCaseSensitive=True,
        WFReplaceTextRegularExpression=True,
    )
    message_empty = builder.if_(builder.output(trimmed_message, "Updated Text"), 101)
    builder.alert("메시지 없음", "커밋 메시지가 비어 있습니다.", cancel=False)
    builder.stop()
    builder.end_if(message_empty)
    builder.alert(
        "커밋 및 푸시",
        "STAGED 변경을 커밋하고, 이미 로컬에 있는 커밋을 푸시합니다.\n계속할까요?",
        cancel=True,
    )

    committed = builder.app_intent(
        "CommitRepositoryIntent",
        repo=repo,
        message=token_string(builder.output(message, "Provided Input")),
        target="staged",
        failNothing=False,
        sign=False,
    )
    commit_failed = builder.if_(builder.output(committed, "Error"), 100)
    builder.alert(
        "커밋 실패",
        token_string("Working Copy 오류: ", builder.output(committed, "Error")),
        cancel=False,
    )
    builder.stop()
    builder.end_if(commit_failed)

    pushed = builder.app_intent("PushRepositoryIntent", repo=repo, remote="")
    push_failed = builder.if_(builder.output(pushed, "Error Message"), 100)
    builder.alert(
        "푸시 실패",
        token_string("Working Copy 오류: ", builder.output(pushed, "Error Message")),
        cancel=False,
    )
    builder.stop()
    builder.end_if(push_failed)

    builder.open_working_copy_status(repo)
    return _workflow(builder)


def build_workflow(name: str) -> dict:
    """Return one unsigned shortcut plist dictionary by its fixed name."""
    if name == CAPTURE:
        return _capture(CAPTURE, "text")
    if name == VOICE:
        return _capture(VOICE, "voice")
    if name == TODAY:
        return _today()
    if name == PULL:
        return _pull()
    if name == SEND:
        return _send()
    raise ValueError(f"unknown shortcut name: {name!r}; expected one of {SHORTCUT_NAMES}")


def write_shortcuts(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in SHORTCUT_NAMES:
        path = output_dir / f"{name}.shortcut"
        with path.open("wb") as handle:
            plistlib.dump(build_workflow(name), handle, fmt=plistlib.FMT_XML, sort_keys=False)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="directory for the unsigned .shortcut files",
    )
    args = parser.parse_args(argv)
    for path in write_shortcuts(args.output):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
