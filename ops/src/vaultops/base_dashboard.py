"""Deterministic C08 Base compiler and frozen-fixture query evaluator.

The Blueprint owns the logical query contract.  This module compiles that
contract to the current Obsidian Bases YAML shape and provides a small,
read-only evaluator for acceptance fixtures.  It deliberately does not try to
interpret arbitrary Bases expressions: the evaluator implements only the
allowlisted C08 query vocabulary, so an unrecognised contract fails closed.
"""

from __future__ import annotations

import json
import textwrap
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from .note_engine import FrontmatterError, parse_frontmatter, resolve_vault_relative_path
from .yaml_safe import load_yaml_file

BASE_DIRECTORY = "99_System/Bases"
BASE_NAMES = (
    "Inbox.base",
    "Projects.base",
    "Decisions.base",
    "Knowledge.base",
    "Sources.base",
    "Review.base",
    "Journal.base",
    "Compass.base",
)
BASE_PATHS = tuple(f"{BASE_DIRECTORY}/{name}" for name in BASE_NAMES)
DASHBOARD_PATHS = (
    "Home.md",
    "Mobile.md",
    "99_System/Dashboards/Tasks.md",
    "99_System/Dashboards/Today_Focus.md",
    "99_System/Dashboards/Weekly_Review.md",
    "99_System/CSS/dashboard.css",
)
CANONICAL_TIMEZONE = ZoneInfo("Asia/Seoul")


class BaseContractError(ValueError):
    """Raised when a Base or frozen query contract is not C08-safe."""


def _dashboard_source(value: str) -> str:
    return textwrap.dedent(value).lstrip("\n")


def dashboard_sources() -> dict[str, str]:
    """Return the plugin-free C08 navigation and dashboard source bytes."""

    return {
        "Home.md": _dashboard_source(
            r"""
            ---
            schema_version: 1
            id: home
            type: home
            title: Home
            status: active
            created: 2026-09-07T00:00:00+09:00
            modified: 2026-09-26T00:00:00+09:00
            aliases: []
            tags: []
            cssclasses:
              - knowledgeos-home
            purpose: "행동·판단·연결을 한 화면에서 조망하는 KnowledgeHub 사령탑"
            audience: desktop
            sensitivity: personal
            ai_policy: deny
            ai_status: idle
            ---
            # Home

            > [!ko-home-grid]
            > > [!ko-home-tasks] Task
            > > ```tasks
            > > not done
            > > due before tomorrow
            > > tags include #task
            > > description regex matches /\S/
            > > sort by due
            > > sort by priority
            > > limit 4
            > > short mode
            > > hide edit button
            > > hide postpone button
            > > hide recurrence rule
            > > hide toolbar
            > > ```
            > > - Core-only fallback: Core Search와 원문 Markdown checkbox를 확인한다.
            >
            > > [!ko-home-inbox] Inbox
            > > ![[99_System/Bases/Inbox.base#Unprocessed]]
            > >
            > > [!ko-home-ai-review] AI 검토 대기·충돌
            > > ![[99_System/Bases/Review.base#PendingOrConflict]]
            >
            > > [!ko-home-projects] 진행 중인 프로젝트
            > > ![[99_System/Bases/Projects.base#Now]]
            >
            > > [!ko-home-decisions] 내가 결정할 것
            > > ![[99_System/Bases/Decisions.base#Open]]
            >
            > > [!ko-home-review] 검토 리듬
            > > #### 읽는 중
            > > ![[99_System/Bases/Sources.base#Reading queue]]
            > >
            > > #### 열린 회고
            > > ![[99_System/Bases/Journal.base#Open Reviews]]
            >
            > > [!ko-home-compass] Compass 신호
            > > #### 신호
            > > ![[99_System/Bases/Compass.base#Signals]]
            > >
            > > #### 긴장
            > > ![[99_System/Bases/Compass.base#Tensions]]
            """
        ),
        "Mobile.md": _dashboard_source(
            r"""
            ---
            schema_version: 1
            id: mobile
            type: home
            title: Mobile
            status: active
            created: 2026-09-07T00:00:00+09:00
            modified: 2026-09-07T00:00:00+09:00
            aliases: []
            tags: []
            purpose: "포착·조회·보류를 위한 현장 화면"
            audience: mobile
            sensitivity: personal
            ai_policy: deny
            ai_status: idle
            ---
            # Mobile

            > [!info] 동기화 상태
            > 이 화면은 이 기기의 마지막 Working Copy 동기화 snapshot 기준입니다. 대량 변경을 받으려면
            > Obsidian을 닫고 `KO · Sync`를 실행하세요. 이 문서에는 성공 여부를 저장하지 않습니다.

            ## 빠른 입력

            - [＋ 생각 포착](shortcuts://run-shortcut?name=KO%20%C2%B7%20Capture)
            - [＋ 자료 저장](shortcuts://run-shortcut?name=KO%20%C2%B7%20Save%20Source)
            - [＋ AI 작업 요청](shortcuts://run-shortcut?name=KO%20%C2%B7%20Defer%20to%20Mac)
            - [↻ 안전 동기화](shortcuts://run-shortcut?name=KO%20%C2%B7%20Sync)

            ## 오늘

            - [오늘 Daily 열기](obsidian://daily?vault=KnowledgeHub)

            ![[99_System/Bases/Projects.base#Mobile]]

            - [[99_System/Bases/Projects.base#Mobile|프로젝트 전체 보기]]

            ## 확인

            ![[99_System/Bases/Inbox.base#Mobile Review]]

            - [[99_System/Bases/Inbox.base#Mobile Review|Mac 검토 Inbox 전체 보기]]

            ![[99_System/Bases/Review.base#Mobile Results]]

            - [[99_System/Bases/Review.base#Mobile Results|AI 결과 전체 보기]]

            ## Core 탐색

            - [[99_System/Bases/Projects.base|프로젝트 전체]]
            - [[99_System/Bases/Inbox.base|Inbox 전체]]
            - [[99_System/Bases/Review.base|AI Review 전체]]
            - [MOC 검색](obsidian://search?vault=KnowledgeHub&query=path%3A50_Maps)
            """
        ),
        "99_System/Dashboards/Tasks.md": _dashboard_source(
            r"""
            ---
            schema_version: 1
            id: system-tasks
            type: system
            title: Tasks
            status: active
            created: 2026-09-07T00:00:00+09:00
            modified: 2026-09-07T00:00:00+09:00
            aliases: []
            tags: []
            sensitivity: personal
            ai_policy: deny
            ai_status: idle
            purpose: "Vault 전체 실행 task"
            related: []
            ---
            # Tasks

            이 dashboard는 Markdown checkbox가 정본이며, Tasks plugin은 선택적 query enhancement다.

            ## Overdue

            ```tasks
            not done
            due before today
            tags include #task
            limit 100
            sort by due
            ```

            - Core-only fallback: Search에서 `#task`와 기한을 확인하고 원문을 직접 연다.

            ## Today

            ```tasks
            not done
            due today
            tags include #task
            limit 100
            sort by priority
            ```

            - Core-only fallback: 오늘 Daily와 각 Project의 `next_action`을 확인한다.

            ## Waiting

            ```tasks
            not done
            tags include #task
            tags include #waiting
            limit 100
            ```

            - Core-only fallback: `#task`와 `#waiting`을 Core Search로 검색한다.
            """
        ),
        "99_System/Dashboards/Today_Focus.md": _dashboard_source(
            r"""
            ---
            schema_version: 1
            id: system-today-focus
            type: system
            title: Today_Focus
            status: active
            created: 2026-09-26T00:00:00+09:00
            modified: 2026-09-26T00:00:00+09:00
            aliases:
              - Today Focus
            tags: []
            sensitivity: personal
            ai_policy: deny
            ai_status: idle
            purpose: "오늘의 초점과 Daily 기준을 독립적으로 확인하는 시스템 화면"
            related: []
            ---
            # Today Focus

            ![[99_System/Bases/Journal.base#Today Focus]]

            - [[99_System/Bases/Journal.base#Today Focus|Today Focus 전체 보기]]
            """
        ),
        "99_System/Dashboards/Weekly_Review.md": _dashboard_source(
            r"""
            ---
            schema_version: 1
            id: system-weekly-review
            type: system
            title: Weekly_Review
            status: active
            created: 2026-09-07T00:00:00+09:00
            modified: 2026-09-07T00:00:00+09:00
            aliases:
              - Weekly Review
            tags: []
            sensitivity: personal
            ai_policy: deny
            ai_status: idle
            purpose: "주간 운영 review 절차"
            related: []
            ---
            # Weekly Review

            이 문서는 완료 상태를 저장하지 않는 고정 절차다. 완료 기록은 해당 주의 T11 Weekly note에 남긴다.

            ## 1. Inbox 비우기

            ![[99_System/Bases/Inbox.base#Unprocessed]]

            - [[99_System/Bases/Inbox.base#Unprocessed|Inbox 전체 보기]]
            - 각 capture를 버리기, task, project, area, knowledge, source 중 하나로 결정한다.

            ## 2. 기한과 대기 확인

            ```tasks
            not done
            tags include #task
            sort by due
            ```

            - Core-only fallback: Tasks dashboard와 Core Search에서 기한·대기 항목을 확인한다.

            ## 3. Project 확인

            ![[99_System/Bases/Projects.base#Now]]

            - [[99_System/Bases/Projects.base#Now|Projects 전체 보기]]
            - 각 active project에 다음 행동이 하나 이상 있는지 확인한다.
            - blocked의 대기 상대·날짜를 확인한다.

            ## 4. Area와 Review

            `vaultctl reconcile --scope areas`의 due-area report를 확인한다.

            - 기준과 실제 상태의 차이를 기록한다.
            - `next_review`를 갱신한다.

            ## 5. Sources와 Knowledge

            ![[99_System/Bases/Sources.base#Reading queue]]

            ![[99_System/Bases/Knowledge.base#Radar]]

            - [[99_System/Bases/Sources.base#Reading queue|Sources 전체 보기]]
            - [[99_System/Bases/Knowledge.base#Radar|Knowledge 전체 보기]]
            - 처리한 source에서 파생할 knowledge note를 확인한다.

            ## 6. AI Review

            ![[99_System/Bases/Review.base#PendingOrConflict]]

            - [[99_System/Bases/Review.base#PendingOrConflict|AI Review 전체 보기]]
            - terminal에서 diff와 hash를 보고 approve 또는 reject한다.

            ## 7. 주 닫기

            - current weekly note에 결과·미완료·대기·다음 주 첫 행동을 기록한다.
            - `vaultctl reconcile` 결과와 backup/Sync 오류를 확인한다.
            """
        ),
        "99_System/CSS/dashboard.css": _dashboard_source(
            r"""
            /* KnowledgeOS C08: scoped Home layout. Markdown/Base content remains authoritative. */
            .knowledgeos-home {
              --g-field: #f1f5f9;
              --g-surface: #ffffff;
              --g-navy: #1e3a5f;
              --g-slate: #334155;
              --g-ink: #0f172a;
              --g-muted: #64748b;
              --g-secure: #059669;
              --g-attention: #b45309;
              --g-conflict: #b91c1c;
              --g-line: #cbd5e1;
              font-family: "Fira Sans", system-ui, sans-serif;
              color: var(--g-ink);
            }

            /* Keep Home's body heading as its one document title. */
            .knowledgeos-home .inline-title {
              display: none;
            }

            .knowledgeos-home .callout[data-callout="ko-home-grid"] {
              border: 0;
              background: transparent;
              margin: 0.75rem 0;
              padding: 0;
            }

            .knowledgeos-home .callout[data-callout="ko-home-grid"] > .callout-title {
              display: none;
            }

            .knowledgeos-home .callout[data-callout="ko-home-grid"] > .callout-content {
              display: grid;
              grid-template-columns: repeat(12, minmax(0, 1fr));
              gap: 0.7rem;
              min-width: 0;
            }

            .knowledgeos-home .callout[data-callout="ko-home-grid"] > .callout-content > .callout {
              min-width: 0;
              margin: 0;
              border: 1px solid var(--g-line);
              border-top: 2px solid var(--g-navy);
              border-radius: 6px;
              background: var(--g-surface);
              padding: 0.65rem 0.75rem;
              overflow-wrap: anywhere;
            }

            /* MacBook hierarchy: Task, Inbox, and AI review first, then projects/decisions, then Review Pulse and Compass. */
            .knowledgeos-home .callout[data-callout="ko-home-tasks"],
            .knowledgeos-home .callout[data-callout="ko-home-inbox"],
            .knowledgeos-home .callout[data-callout="ko-home-ai-review"] {
              grid-column: span 12;
            }

            .knowledgeos-home .callout[data-callout="ko-home-projects"],
            .knowledgeos-home .callout[data-callout="ko-home-decisions"] {
              grid-column: span 6;
            }

            .knowledgeos-home .callout[data-callout="ko-home-review"],
            .knowledgeos-home .callout[data-callout="ko-home-compass"] {
              grid-column: span 6;
            }

            .knowledgeos-home .callout[data-callout^="ko-home-"] .callout-title {
              color: var(--g-slate);
              font-family: "Fira Sans", system-ui, sans-serif;
              font-weight: 600;
            }

            .knowledgeos-home .callout[data-callout^="ko-home-"] .callout-content {
              min-width: 0;
            }

            .knowledgeos-home table {
              width: 100%;
              table-layout: auto;
              font-size: 0.88em;
            }

            .knowledgeos-home th,
            .knowledgeos-home td {
              min-width: 0;
              overflow-wrap: anywhere;
            }

            .knowledgeos-home code,
            .knowledgeos-home .tasks-count {
              font-family: "Fira Code", ui-monospace, monospace;
              font-variant-numeric: tabular-nums;
            }

            .knowledgeos-home a:focus-visible,
            .knowledgeos-home button:focus-visible,
            .knowledgeos-home input:focus-visible {
              outline: 2px solid var(--g-attention);
              outline-offset: 2px;
            }

            @media (max-width: 1199px) {
              .knowledgeos-home .callout[data-callout="ko-home-grid"] > .callout-content {
                grid-template-columns: repeat(2, minmax(0, 1fr));
              }

              .knowledgeos-home .callout[data-callout^="ko-home-"] {
                grid-column: span 1;
              }

              .knowledgeos-home .callout[data-callout="ko-home-tasks"],
              .knowledgeos-home .callout[data-callout="ko-home-inbox"],
              .knowledgeos-home .callout[data-callout="ko-home-ai-review"],
              .knowledgeos-home .callout[data-callout="ko-home-projects"],
              .knowledgeos-home .callout[data-callout="ko-home-decisions"],
              .knowledgeos-home .callout[data-callout="ko-home-review"],
              .knowledgeos-home .callout[data-callout="ko-home-compass"] {
                grid-column: span 1;
              }
            }

            @media (max-width: 899px) {
              .knowledgeos-home .callout[data-callout="ko-home-grid"] > .callout-content {
                grid-template-columns: minmax(0, 1fr);
              }

              .knowledgeos-home .callout[data-callout^="ko-home-"] {
                grid-column: span 1;
              }
            }

            @media (prefers-reduced-motion: reduce) {
              .knowledgeos-home *,
              .knowledgeos-home *::before,
              .knowledgeos-home *::after {
                scroll-behavior: auto !important;
                transition-duration: 0.001ms !important;
                animation-duration: 0.001ms !important;
                animation-iteration-count: 1 !important;
              }
            }
            """
        ),
    }


@dataclass(frozen=True)
class FrozenNote:
    """One deterministic fixture row, including the file mtime used by Bases."""

    path: str
    properties: Mapping[str, Any]
    mtime: float


def _path_prefix(base_name: str) -> tuple[str, ...]:
    prefixes = {
        "Journal.base": ("10_Journal/", "30_Areas/"),
        "Projects.base": ("20_Projects/",),
        "Decisions.base": ("40_Knowledge/Questions/",),
        "Knowledge.base": ("40_Knowledge/", "50_Maps/"),
        "Sources.base": ("40_Knowledge/Sources/",),
        "Inbox.base": ("00_Inbox/",),
        "Review.base": ("01_AI_Review/",),
        "Compass.base": (
            "00_Inbox/",
            "01_AI_Review/",
            "10_Journal/",
            "20_Projects/",
            "30_Areas/",
            "40_Knowledge/",
            "50_Maps/",
        ),
    }
    try:
        return prefixes[base_name]
    except KeyError as error:
        raise BaseContractError(f"unknown Base: {base_name}") from error


def _global_filters(base_name: str) -> list[Any]:
    filters: dict[str, list[Any]] = {
        "Journal.base": [
            'file.ext == "md"',
            {"or": ['file.inFolder("10_Journal")', 'file.inFolder("30_Areas")']},
        ],
        "Projects.base": [
            'file.ext == "md"',
            'file.inFolder("20_Projects")',
            'type == "project"',
        ],
        "Decisions.base": [
            'file.ext == "md"',
            'file.inFolder("40_Knowledge/Questions")',
            'type == "question"',
        ],
        "Knowledge.base": [
            'file.ext == "md"',
            {"or": ['file.inFolder("40_Knowledge")', 'file.inFolder("50_Maps")']},
            {"or": ['type == "knowledge"', 'type == "idea"', 'type == "moc"']},
        ],
        "Sources.base": [
            'file.ext == "md"',
            'file.inFolder("40_Knowledge/Sources")',
            'type == "source"',
        ],
        "Inbox.base": ['file.ext == "md"', 'file.inFolder("00_Inbox")'],
        "Review.base": [
            'file.ext == "md"',
            'file.inFolder("01_AI_Review")',
            'type == "proposal"',
        ],
        "Compass.base": [
            'file.ext == "md"',
            {
                "or": [
                    'file.inFolder("00_Inbox")',
                    'file.inFolder("01_AI_Review")',
                    'file.inFolder("10_Journal")',
                    'file.inFolder("20_Projects")',
                    'file.inFolder("30_Areas")',
                    'file.inFolder("40_Knowledge")',
                    'file.inFolder("50_Maps")',
                ]
            },
        ],
    }
    try:
        return filters[base_name]
    except KeyError as error:
        raise BaseContractError(f"unknown Base: {base_name}") from error


def _filter_expression(filter_contract: Mapping[str, Any]) -> Any:
    statements: list[Any] = []
    for name, value in filter_contract.items():
        if name in {"and", "or"}:
            if not isinstance(value, list) or not value:
                raise BaseContractError(f"{name} must be a non-empty list")
            nested: list[Any] = []
            for item in value:
                if isinstance(item, str):
                    nested.append(item)
                elif isinstance(item, Mapping):
                    nested.append(_filter_expression(item))
                else:
                    raise BaseContractError(f"{name} entries must be mappings or expressions")
            statements.append({name: nested})
        elif name in {"type", "status", "question_kind"}:
            statements.append(f'{name} == "{value}"')
        elif name in {"type_in", "status_in", "question_kind_in", "confidence_in"}:
            values = value
            if not isinstance(values, list) or not values:
                raise BaseContractError(f"{name} must be a non-empty list")
            statements.append({"or": [f'{name.removesuffix("_in")} == "{value}"' for value in values]})
        elif name == "decision_nonempty" and value:
            statements.append('decision != ""')
        elif name == "next_action_nonempty" and value:
            statements.append('next_action != ""')
        elif name == "needs_desktop_review" and value:
            statements.append("needs_desktop_review == true")
        elif name == "period_start":
            if value != "today":
                raise BaseContractError("only the canonical period_start=today filter is supported")
            statements.append("period_start == today()")
        elif name == "next_review_on_or_before_today" and value:
            statements.append("next_review <= today()")
        elif name in {
            "contradicts_nonempty",
            "supports_nonempty",
            "applies_to_nonempty",
            "implements_nonempty",
            "related_nonempty",
            "raises_nonempty",
        } and value:
            property_name = name.removesuffix("_nonempty")
            statements.append(f"!list({property_name}).isEmpty()")
        elif name in {"projects_empty", "related_empty", "raises_empty", "implements_empty"} and value:
            property_name = name.removesuffix("_empty")
            statements.append(f"list({property_name}).isEmpty()")
        elif name not in {
            "decision_nonempty",
            "next_action_nonempty",
            "needs_desktop_review",
            "next_review_on_or_before_today",
            "contradicts_nonempty",
            "supports_nonempty",
            "applies_to_nonempty",
            "implements_nonempty",
            "related_nonempty",
            "raises_nonempty",
            "projects_empty",
            "related_empty",
            "raises_empty",
            "implements_empty",
        }:
            raise BaseContractError(f"unsupported Base filter: {name}")
    if not statements:
        return None
    return statements[0] if len(statements) == 1 else {"and": statements}


def _view_filter(query: Mapping[str, Any]) -> Any:
    filter_contract = query.get("filters", query)
    if not isinstance(filter_contract, Mapping):
        raise BaseContractError("Base view filters must be a mapping")
    return _filter_expression(filter_contract)


def _column_property(column: str) -> str:
    if column == "file.link":
        # file.name is the stable built-in Bases column and renders as a
        # clickable note link in the table view.
        return "file.name"
    if column == "file.mtime":
        return "file.mtime"
    if column.startswith("file.") or column in {
        "status",
        "next_action",
        "focus_rank",
        "priority",
        "target_date",
        "decision",
        "decision_by",
        "projects",
        "type",
        "confidence",
        "created",
        "period_end",
        "today_focus",
        "capture_kind",
        "triage_hint",
        "needs_desktop_review",
        "capture_label",
        "source_kind",
        "authors",
        "published_date",
        "source_url",
        "proposal_id",
        "possibility",
        "question_kind",
        "contradicts",
        "related",
        "raises",
        "review_cadence",
        "next_review",
        "scope",
        "supports",
        "applies_to",
        "implements",
    }:
        return column
    raise BaseContractError(f"unsupported Base column: {column}")


def _sort_property(token: str) -> tuple[str, str, str | None]:
    mapping = {
        "file_name_desc": ("file.name", "DESC", None),
        "file_name_asc": ("file.name", "ASC", None),
        "focus_rank_asc": ("focus_rank", "ASC", None),
        "priority_high_to_low": ("formula.priority_rank", "ASC", "priority_rank"),
        "target_date_asc_nulls_last": ("formula.target_date_sort", "ASC", "target_date_sort"),
        "decision_by_asc_nulls_last": ("formula.decision_by_sort", "ASC", "decision_by_sort"),
        "file_mtime_desc": ("file.mtime", "DESC", None),
        "created_asc": ("created", "ASC", None),
        "created_desc": ("created", "DESC", None),
        "published_date_desc_nulls_last": (
            "formula.published_date_sort",
            "DESC",
            "published_date_sort",
        ),
        "period_start_desc": ("period_start", "DESC", None),
        "next_review_asc": ("next_review", "ASC", None),
        "file_mtime_asc": ("file.mtime", "ASC", None),
    }
    try:
        return mapping[token]
    except KeyError as error:
        raise BaseContractError(f"unsupported canonical sort token: {token}") from error


def _formula_sources(sort_tokens: Iterable[str]) -> dict[str, str]:
    formulas: dict[str, str] = {}
    for token in sort_tokens:
        _, _, formula = _sort_property(token)
        if formula == "priority_rank":
            formulas[formula] = 'if(priority == "high", 1, if(priority == "medium", 2, 3))'
        elif formula == "target_date_sort":
            formulas[formula] = 'if(target_date, target_date, date("9999-12-31"))'
        elif formula == "decision_by_sort":
            formulas[formula] = 'if(decision_by, decision_by, date("9999-12-31"))'
        elif formula == "published_date_sort":
            formulas[formula] = 'if(published_date, published_date, date("0001-01-01"))'
    return formulas


_DISPLAY_NAMES = {
    "file.name": "파일",
    "file.mtime": "실제 수정",
    "status": "상태",
    "next_action": "다음 행동",
    "focus_rank": "집중 순서",
    "priority": "우선순위",
    "target_date": "목표일",
    "decision": "결정",
    "decision_by": "결정 기한",
    "projects": "프로젝트",
    "type": "유형",
    "confidence": "신뢰도",
    "created": "생성",
    "period_end": "종료일",
    "today_focus": "오늘의 방향",
    "capture_kind": "종류",
    "triage_hint": "분류 힌트",
    "needs_desktop_review": "Mac 검토",
    "capture_label": "표시 라벨",
    "source_kind": "종류",
    "authors": "저자",
    "published_date": "발행일",
    "source_url": "URL",
    "proposal_id": "제안 ID",
    "possibility": "가능성",
    "question_kind": "질문 종류",
    "contradicts": "모순",
    "related": "관련 노트",
    "raises": "확장 질문",
    "supports": "지지",
    "applies_to": "적용 대상",
    "implements": "구현 대상",
    "review_cadence": "검토 주기",
    "next_review": "다음 검토",
    "scope": "범위",
}

_BASE_FILE_DISPLAY_NAMES = {
    "Journal.base": "파일",
    "Projects.base": "프로젝트",
    "Decisions.base": "질문",
    "Knowledge.base": "노트",
    "Sources.base": "출처",
    "Inbox.base": "제목",
    "Review.base": "항목",
    "Compass.base": "신호",
}


def _display_name(base_name: str, property_name: str) -> str:
    if property_name == "file.name":
        return _BASE_FILE_DISPLAY_NAMES[base_name]
    if base_name == "Knowledge.base" and property_name == "status":
        return "성숙도"
    return _DISPLAY_NAMES[property_name]


def render_base_documents(blueprint: Mapping[str, Any]) -> dict[str, str]:
    """Compile every Blueprint Base into deterministic Obsidian YAML."""

    bases = blueprint.get("bases")
    if not isinstance(bases, Mapping) or tuple(bases.get("required", ())) != BASE_NAMES:
        raise BaseContractError("Blueprint Base registry does not match C08 exact set")
    views = bases.get("views")
    if not isinstance(views, Mapping) or set(views) != set(BASE_NAMES):
        raise BaseContractError("Blueprint Base view registry does not match C08 exact set")

    documents: dict[str, str] = {}
    for base_name in BASE_NAMES:
        base_views = views[base_name]
        if not isinstance(base_views, Mapping):
            raise BaseContractError(f"Base views must be a mapping: {base_name}")
        all_columns: list[str] = []
        all_sort_tokens: list[str] = []
        rendered_views: list[dict[str, Any]] = []
        for view_name, query in base_views.items():
            if not isinstance(query, Mapping):
                raise BaseContractError(f"Base view query must be a mapping: {base_name}#{view_name}")
            columns = query.get("columns")
            sort_tokens = query.get("sort")
            limit = query.get("limit")
            if not isinstance(columns, list) or not isinstance(sort_tokens, list) or not isinstance(limit, int):
                raise BaseContractError(f"Base view is missing columns/sort/limit: {base_name}#{view_name}")
            all_columns.extend(str(column) for column in columns)
            all_sort_tokens.extend(str(token) for token in sort_tokens)
            view: dict[str, Any] = {
                "type": "table",
                "name": str(view_name),
                "limit": limit,
            }
            filter_value = _view_filter(query)
            if filter_value is not None:
                view["filters"] = filter_value
            view["sort"] = [
                {"property": property_name, "direction": direction}
                for property_name, direction, _ in (_sort_property(str(token)) for token in sort_tokens)
            ]
            view["order"] = [_column_property(str(column)) for column in columns]
            rendered_views.append(view)
            properties: dict[str, dict[str, str]] = {}
            for column in dict.fromkeys(all_columns):
                property_name = _column_property(column)
                properties[property_name] = {"displayName": _display_name(base_name, property_name)}
        payload: dict[str, Any] = {
            "filters": {"and": _global_filters(base_name)},
            "properties": properties,
        }
        formulas = _formula_sources(all_sort_tokens)
        if formulas:
            payload["formulas"] = formulas
        payload["views"] = rendered_views
        documents[f"{BASE_DIRECTORY}/{base_name}"] = yaml.safe_dump(
            payload,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            width=120,
        )
    return documents


def _today_value(today: date | str | None) -> date:
    if today is None:
        return datetime.now(CANONICAL_TIMEZONE).date()
    if isinstance(today, date):
        return today
    return date.fromisoformat(today)


def load_frozen_fixture(path: str | Path) -> tuple[date, tuple[FrozenNote, ...]]:
    """Load a YAML fixture with explicit ``today`` and file mtimes."""

    value = load_yaml_file(path)
    if not isinstance(value, Mapping) or not isinstance(value.get("today"), str):
        raise BaseContractError("C08 fixture must define an ISO today value")
    notes = value.get("notes")
    if not isinstance(notes, list):
        raise BaseContractError("C08 fixture notes must be a list")
    parsed: list[FrozenNote] = []
    for item in notes:
        if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
            raise BaseContractError("C08 fixture note path is required")
        if not isinstance(item.get("properties"), Mapping) or not isinstance(item.get("mtime"), (int, float)):
            raise BaseContractError(f"C08 fixture note is incomplete: {item.get('path')}")
        parsed.append(FrozenNote(item["path"], dict(item["properties"]), float(item["mtime"])))
    return date.fromisoformat(value["today"]), tuple(parsed)


def _notes_from_vault(vault_root: Path) -> tuple[FrozenNote, ...]:
    if vault_root.is_symlink() or not vault_root.is_dir():
        raise BaseContractError("Vault root is missing or is a symlink")
    result: list[FrozenNote] = []
    for path in sorted(vault_root.rglob("*.md")):
        relative = path.relative_to(vault_root).as_posix()
        if any(part.startswith(".") for part in PurePosixPath(relative).parts):
            continue
        try:
            document = parse_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, FrontmatterError):
            # A non-contract Markdown file cannot satisfy any typed Base
            # filter.  Ignore it like the app does for missing properties.
            continue
        result.append(FrozenNote(relative, document.properties, path.stat().st_mtime_ns / 1_000_000_000))
    return tuple(result)


def _date_value(value: Any) -> date | datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None


def _base_scope_matches(base_name: str, note: FrozenNote) -> bool:
    return any(note.path.startswith(prefix) for prefix in _path_prefix(base_name)) and note.path.endswith(".md")


def _as_date(value: Any) -> date | None:
    parsed = _date_value(value)
    if isinstance(parsed, datetime):
        return parsed.date()
    return parsed


def _matches_filter(filter_contract: Mapping[str, Any], properties: Mapping[str, Any], today: date) -> bool:
    for name, value in filter_contract.items():
        if name == "and":
            if not isinstance(value, list) or not value:
                raise BaseContractError("and must be a non-empty list")
            if not all(
                isinstance(item, Mapping) and _matches_filter(item, properties, today)
                for item in value
            ):
                return False
            continue
        if name == "or":
            if not isinstance(value, list) or not value:
                raise BaseContractError("or must be a non-empty list")
            if not any(
                isinstance(item, Mapping) and _matches_filter(item, properties, today)
                for item in value
            ):
                return False
            continue
        if name in {"type", "status", "question_kind"}:
            if properties.get(name) != value:
                return False
        elif name in {"type_in", "status_in", "question_kind_in", "confidence_in"}:
            if properties.get(name.removesuffix("_in")) not in value:
                return False
        elif name == "decision_nonempty" and value:
            if not properties.get("decision"):
                return False
        elif name == "next_action_nonempty" and value:
            if not properties.get("next_action"):
                return False
        elif name == "needs_desktop_review" and value:
            if properties.get("needs_desktop_review") is not True:
                return False
        elif name == "period_start":
            if value == "today" and properties.get("period_start") != today.isoformat():
                return False
            if value != "today":
                raise BaseContractError("only the canonical period_start=today filter is supported")
        elif name == "next_review_on_or_before_today" and value:
            next_review = _as_date(properties.get("next_review"))
            if next_review is None or next_review > today:
                return False
        elif name.endswith("_nonempty") and name.removesuffix("_nonempty") in {
            "contradicts",
            "supports",
            "applies_to",
            "implements",
            "related",
            "raises",
        } and value:
            if not properties.get(name.removesuffix("_nonempty")):
                return False
        elif name.endswith("_empty") and name.removesuffix("_empty") in {
            "projects",
            "related",
            "raises",
            "implements",
        } and value:
            if properties.get(name.removesuffix("_empty")):
                return False
        elif value is False and name in {
            "decision_nonempty",
            "next_action_nonempty",
            "needs_desktop_review",
            "next_review_on_or_before_today",
        }:
            continue
        else:
            raise BaseContractError(f"unsupported Base filter: {name}")
    return True


def _matches(base_name: str, query: Mapping[str, Any], note: FrozenNote, today: date) -> bool:
    if not _base_scope_matches(base_name, note):
        return False
    filter_contract = query.get("filters", query)
    if not isinstance(filter_contract, Mapping):
        raise BaseContractError("Base view filters must be a mapping")
    return _matches_filter(filter_contract, note.properties, today)


def _sort_value(token: str, note: FrozenNote) -> Any:
    properties = note.properties
    if token.startswith("file_name"):
        return PurePosixPath(note.path).stem
    if token == "focus_rank_asc":
        return properties.get("focus_rank")
    if token == "priority_high_to_low":
        return {"high": 0, "medium": 1, "low": 2}.get(properties.get("priority"))
    if token == "file_mtime_desc":
        return note.mtime
    if token == "file_mtime_asc":
        return note.mtime
    property_name = {
        "target_date_asc_nulls_last": "target_date",
        "decision_by_asc_nulls_last": "decision_by",
        "created_asc": "created",
        "created_desc": "created",
        "published_date_desc_nulls_last": "published_date",
        "period_start_desc": "period_start",
        "next_review_asc": "next_review",
    }.get(token)
    if property_name is not None:
        return _date_value(properties.get(property_name))
    raise BaseContractError(f"unsupported canonical sort token: {token}")


_DESCENDING_SORTS = frozenset(
    {"file_name_desc", "file_mtime_desc", "created_desc", "published_date_desc_nulls_last", "period_start_desc"}
)


def _sorted_notes(notes: Iterable[FrozenNote], sort_tokens: Iterable[str]) -> list[FrozenNote]:
    result = list(notes)
    for token in reversed(tuple(sort_tokens)):
        _sort_property(token)  # Validate the token even for an empty result.
        present = [note for note in result if _sort_value(token, note) is not None]
        missing = [note for note in result if _sort_value(token, note) is None]
        present.sort(key=lambda note: _sort_value(token, note), reverse=token in _DESCENDING_SORTS)
        result = present + missing
    return result


def evaluate_records(
    blueprint: Mapping[str, Any],
    base_name: str,
    view_name: str,
    records: Iterable[FrozenNote],
    *,
    today: date | str | None = None,
) -> list[dict[str, Any]]:
    """Evaluate one canonical view over frozen records without writing files."""

    if base_name not in BASE_NAMES:
        raise BaseContractError(f"unknown Base: {base_name}")
    blueprint_views = blueprint.get("bases", {}).get("views", {})
    query = blueprint_views.get(base_name, {}).get(view_name)
    if not isinstance(query, Mapping):
        raise BaseContractError(f"unknown canonical Base view: {base_name}#{view_name}")
    limit = query.get("limit")
    sort_tokens = query.get("sort")
    columns = query.get("columns")
    if not isinstance(limit, int) or not isinstance(sort_tokens, list) or not isinstance(columns, list):
        raise BaseContractError(f"invalid canonical Base view: {base_name}#{view_name}")
    selected = [note for note in records if _matches(base_name, query, note, _today_value(today))]
    ordered = _sorted_notes(selected, (str(token) for token in sort_tokens))[:limit]
    rows: list[dict[str, Any]] = []
    for note in ordered:
        values: dict[str, Any] = {}
        for column in columns:
            column_name = str(column)
            if column_name == "file.link":
                values[column_name] = f"[[{PurePosixPath(note.path).stem}]]"
            elif column_name == "file.mtime":
                values[column_name] = note.mtime
            else:
                values[column_name] = note.properties.get(column_name)
        rows.append({"path": note.path, "columns": values})
    return rows


def evaluate_base_view(
    root: str | Path,
    base_name: str,
    view_name: str,
    *,
    today: date | str | None = None,
) -> list[dict[str, Any]]:
    """Read one Vault and evaluate its canonical view; never mutate the Vault."""

    workspace = Path(root).resolve()
    blueprint = load_yaml_file(workspace / "blueprint/blueprint.yaml")
    expected = render_base_documents(blueprint)
    base_path = resolve_vault_relative_path(workspace / "KnowledgeHub", f"{BASE_DIRECTORY}/{base_name}")
    if not base_path.is_file() or base_path.is_symlink():
        raise BaseContractError(f"Base file is missing or unsafe: {BASE_DIRECTORY}/{base_name}")
    actual = base_path.read_text(encoding="utf-8")
    if yaml.safe_load(actual) != yaml.safe_load(expected[f"{BASE_DIRECTORY}/{base_name}"]):
        raise BaseContractError(f"Base file differs from canonical C08 compiler: {base_name}")
    return evaluate_records(blueprint, base_name, view_name, _notes_from_vault(workspace / "KnowledgeHub"), today=today)


def evaluator_report(rows: Iterable[Mapping[str, Any]]) -> str:
    """Provide deterministic JSON for tests or a future read-only CLI surface."""

    return json.dumps(list(rows), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
