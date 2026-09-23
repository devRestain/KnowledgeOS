"""Deterministic, non-executing renderers for the C07 note templates.

The files under ``KnowledgeHub/99_System/Templates`` are deliberately ordinary
Markdown templates.  Templater may render the small user-facing subset later,
but vaultops never evaluates JavaScript or arbitrary template expressions.  A
terminal renderer uses the typed functions in this module instead.
"""

from __future__ import annotations

import calendar
import re
import textwrap
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import yaml

from .note_engine import render_frontmatter

CANONICAL_TIMEZONE = "Asia/Seoul"
TEMPLATE_DIRECTORY = "99_System/Templates"


class TemplateRenderError(ValueError):
    """Raised when a template context is incomplete or unsafe."""


@dataclass(frozen=True)
class TemplateSpec:
    filename: str
    note_type: str

    @property
    def relative_path(self) -> str:
        return f"{TEMPLATE_DIRECTORY}/{self.filename}"


TEMPLATE_SPECS = (
    TemplateSpec("T00_Capture.md", "capture"),
    TemplateSpec("T01_AI_Proposal.md", "proposal"),
    TemplateSpec("T10_Daily.md", "daily"),
    TemplateSpec("T11_Weekly.md", "weekly"),
    TemplateSpec("T12_Monthly.md", "monthly"),
    TemplateSpec("T20_Project.md", "project"),
    TemplateSpec("T21_Idea.md", "idea"),
    TemplateSpec("T22_Question.md", "question"),
    TemplateSpec("T23_Artifact.md", "artifact"),
    TemplateSpec("T24_Project_Note.md", "project_note"),
    TemplateSpec("T30_Area.md", "area"),
    TemplateSpec("T40_Knowledge.md", "knowledge"),
    TemplateSpec("T41_Source.md", "source"),
    TemplateSpec("T42_Person.md", "person"),
    TemplateSpec("T50_MOC.md", "moc"),
    TemplateSpec("T60_Meeting.md", "meeting"),
)

TEMPLATE_BY_NAME = {spec.filename: spec for spec in TEMPLATE_SPECS}


def _source(value: str) -> str:
    return textwrap.dedent(value).lstrip("\n")


# These are the checked-in template bytes.  Keeping the source in the runtime
# makes ``bootstrap`` able to provision a fresh temporary Vault without
# reading a possibly user-modified template from the target Vault.
TEMPLATE_SOURCES: dict[str, str] = {
    "T00_Capture.md": _source(
        r"""
        <%*
        const now = tp.date.now("YYYY-MM-DDTHH:mm:ssZ");
        const id = crypto.randomUUID();
        const title = tp.file.title;
        const yamlTitle = title.replace(/'/g, "''");
        -%>
        ---
        schema_version: 1
        id: "<% id %>"
        type: capture
        title: '<% yamlTitle %>'
        status: unprocessed
        created: <% now %>
        modified: <% now %>
        aliases: {{VALUE:collision_aliases_yaml}}
        tags:
          - inbox
        sensitivity: personal
        ai_policy: ask
        ai_status: idle
        captured_from: mac_quickadd
        capture_device: mac
        capture_kind: thought
        triage_hint: none
        needs_desktop_review: false
        ---
        # <% title %>

        ## 원문


        ## 맥락

        - 왜 지금 기록했는가:
        - 연결될 프로젝트·영역:

        ## Triage

        - [ ] 버리기 / 행동으로 전환 / 정식 노트로 승격 중 하나를 결정한다.
        """
    ),
    "T01_AI_Proposal.md": _source(
        r"""
        ---
        schema_version: 1
        id: "proposal-{{JOB_ID}}"
        type: proposal
        title: "{{PROPOSAL_FILENAME_STEM}}"
        status: pending
        created: {{CREATED_AT}}
        modified: {{CREATED_AT}}
        aliases: []
        tags:
          - ai/review
        sensitivity: {{SENSITIVITY}}
        ai_policy: deny
        ai_status: proposed
        proposal_id: "{{JOB_ID}}"
        source_hashes: {{SOURCE_HASH_LIST_YAML}}
        ---
        # AI 제안 — {{PROPOSAL_DISPLAY_TITLE}}

        > 이 노트는 정본이 아니다. 승인·거절은 terminal의 vaultctl 명령으로 수행한다.

        ## 제안 요약

        {{SUMMARY_PLAINTEXT}}

        ## 출처와 고정 hash

        {{SOURCE_TABLE}}

        ## 변경 미리보기

        {{DIFF_FENCE}}

        ## 경고

        {{WARNINGS_PLAINTEXT}}

        ## 검토 체크

        - [ ] 출처의 의미가 왜곡되지 않았다.
        - [ ] 민감정보와 ai_policy를 다시 확인했다.
        - [ ] 링크와 target path가 맞다.
        - [ ] 덮어쓰기라면 현재 파일 hash가 기준과 같다.
        """
    ),
    "T10_Daily.md": _source(
        r"""
        ---
        schema_version: 1
        id: "daily-{{date:YYYY-MM-DD}}"
        type: daily
        title: "{{date:YYYY-MM-DD}}"
        status: open
        created: {{date:YYYY-MM-DD}}T00:00:00+09:00
        modified: {{date:YYYY-MM-DD}}T00:00:00+09:00
        aliases: []
        tags:
          - journal/daily
        sensitivity: personal
        ai_policy: ask
        ai_status: idle
        period_start: {{date:YYYY-MM-DD}}
        period_end: {{date:YYYY-MM-DD}}
        today_focus: ""
        ---
        # {{date:YYYY-MM-DD dddd}}

        ## 오늘의 3가지 결과

        1.
        2.
        3.

        ## 일정과 약속


        ## 작업

        - [ ] #task

        ## 로그

        - {{time:HH:mm}}

        ## 메모와 발견


        ## 하루 닫기

        - 잘된 점:
        - 막힌 점:
        - 내일 첫 행동:

        <!-- vaultops:daily-summary:begin -->
        <!-- 승인된 자동 요약만 이 구간을 교체할 수 있다. -->
        <!-- vaultops:daily-summary:end -->
        """
    ),
    "T11_Weekly.md": _source(
        r"""
        <%*
        const period = moment(tp.file.title, "GGGG-[W]WW", true);
        if (!period.isValid()) throw new Error("weekly filename must be YYYY-Www");
        const periodStart = period.clone().startOf("isoWeek");
        const periodEnd = periodStart.clone().add(6, "days");
        const periodLabel = periodStart.format("GGGG-[W]WW");
        const periodId = periodLabel.toLowerCase();
        -%>
        ---
        schema_version: 1
        id: "weekly-<% periodId %>"
        type: weekly
        title: "<% periodLabel %>"
        status: open
        created: <% periodStart.format("YYYY-MM-DD") %>T00:00:00+09:00
        modified: <% periodStart.format("YYYY-MM-DD") %>T00:00:00+09:00
        aliases: []
        tags:
          - journal/weekly
        sensitivity: personal
        ai_policy: ask
        ai_status: idle
        period_start: <% periodStart.format("YYYY-MM-DD") %>
        period_end: <% periodEnd.format("YYYY-MM-DD") %>
        ---
        # <% periodStart.format("GGGG [W]WW") %>

        ## 이번 주 결과

        1.
        2.
        3.

        ## 일간 기록

        - [[<% periodStart.format("YYYY-MM-DD") %>]]
        - [[<% periodStart.clone().add(1, "days").format("YYYY-MM-DD") %>]]
        - [[<% periodStart.clone().add(2, "days").format("YYYY-MM-DD") %>]]
        - [[<% periodStart.clone().add(3, "days").format("YYYY-MM-DD") %>]]
        - [[<% periodStart.clone().add(4, "days").format("YYYY-MM-DD") %>]]
        - [[<% periodStart.clone().add(5, "days").format("YYYY-MM-DD") %>]]
        - [[<% periodStart.clone().add(6, "days").format("YYYY-MM-DD") %>]]

        ## 프로젝트 점검


        ## 완료·미완료·대기


        ## 다음 주로 넘길 것

        - [ ] #task

        <!-- vaultops:weekly-summary:begin -->
        <!-- 승인된 자동 요약만 이 구간을 교체할 수 있다. -->
        <!-- vaultops:weekly-summary:end -->
        """
    ),
    "T12_Monthly.md": _source(
        r"""
        <%*
        const period = moment(tp.file.title, "YYYY-MM", true);
        if (!period.isValid()) throw new Error("monthly filename must be YYYY-MM");
        const monthStart = period.clone().startOf("month");
        const monthEnd = monthStart.clone().endOf("month");
        const monthId = monthStart.format("YYYY-MM");
        -%>
        ---
        schema_version: 1
        id: "monthly-<% monthId %>"
        type: monthly
        title: "<% monthId %>"
        status: open
        created: <% monthStart.format("YYYY-MM-DD") %>T00:00:00+09:00
        modified: <% monthStart.format("YYYY-MM-DD") %>T00:00:00+09:00
        aliases: []
        tags:
          - journal/monthly
        sensitivity: personal
        ai_policy: ask
        ai_status: idle
        period_start: <% monthStart.format("YYYY-MM-DD") %>
        period_end: <% monthEnd.format("YYYY-MM-DD") %>
        ---
        # <% monthId %>

        ## 이달의 방향


        ## 결과와 증거


        ## 프로젝트·영역 review


        ## 배운 것


        ## 다음 달에 중단·시작·지속할 것

        - 중단:
        - 시작:
        - 지속:
        """
    ),
    "T20_Project.md": _source(
        r"""
        <%*
        const now = tp.date.now("YYYY-MM-DDTHH:mm:ssZ");
        const id = crypto.randomUUID();
        const title = tp.file.title;
        const yamlTitle = title.replace(/'/g, "''");
        -%>
        ---
        schema_version: 1
        id: "<% id %>"
        type: project
        title: '<% yamlTitle %>'
        status: planned
        created: <% now %>
        modified: <% now %>
        aliases: {{VALUE:collision_aliases_yaml}}
        tags: []
        sensitivity: personal
        ai_policy: ask
        ai_status: idle
        outcome: '<% yamlTitle %>의 완료 조건을 정의한다.'
        priority: medium
        ---
        # <% title %>

        ## 원하는 결과


        ## 완료 조건

        - [ ]

        ## 현재 상태


        ## 다음 행동

        - [ ] #task

        ## 마일스톤


        ## 결정

        | 날짜 | 결정 | 근거 |
        |---|---|---|

        ## 자료와 노트


        ## 회고

        <!-- vaultops:project-brief:begin -->
        <!-- 승인된 자동 요약만 이 구간을 교체할 수 있다. -->
        <!-- vaultops:project-brief:end -->
        """
    ),
    "T21_Idea.md": _source(
        r"""
        <%*
        const now = tp.date.now("YYYY-MM-DDTHH:mm:ssZ");
        const id = crypto.randomUUID();
        const title = tp.file.title;
        const yamlTitle = title.replace(/'/g, "''");
        -%>
        ---
        schema_version: 1
        id: "<% id %>"
        type: idea
        title: '<% yamlTitle %>'
        status: seed
        created: <% now %>
        modified: <% now %>
        aliases: {{VALUE:collision_aliases_yaml}}
        tags: []
        sensitivity: personal
        ai_policy: ask
        ai_status: idle
        possibility: '<% yamlTitle %>'
        ---
        # <% title %>

        ## 가능성

        ## 왜 흥미로운가

        ## 검증할 가정

        ## 다음 실험

        ## 연결
        """
    ),
    "T22_Question.md": _source(
        r"""
        <%*
        const now = tp.date.now("YYYY-MM-DDTHH:mm:ssZ");
        const id = crypto.randomUUID();
        const title = tp.file.title;
        const yamlTitle = title.replace(/'/g, "''");
        -%>
        ---
        schema_version: 1
        id: "<% id %>"
        type: question
        title: '<% yamlTitle %>'
        status: open
        created: <% now %>
        modified: <% now %>
        aliases: {{VALUE:collision_aliases_yaml}}
        tags: []
        sensitivity: personal
        ai_policy: ask
        ai_status: idle
        question_kind: research
        ---
        # <% title %>

        ## 질문 또는 결정

        ## 왜 지금 중요한가

        ## 선택지 또는 가설

        ## 판단 기준

        ## 근거

        ## 결정과 이유

        ## 후속 행동
        """
    ),
    "T23_Artifact.md": _source(
        r"""
        <%*
        const now = tp.date.now("YYYY-MM-DDTHH:mm:ssZ");
        const id = crypto.randomUUID();
        const title = tp.file.title;
        const yamlTitle = title.replace(/'/g, "''");
        -%>
        ---
        schema_version: 1
        id: "<% id %>"
        type: artifact
        title: '<% yamlTitle %>'
        status: draft
        created: <% now %>
        modified: <% now %>
        aliases: {{VALUE:collision_aliases_yaml}}
        tags: []
        sensitivity: personal
        ai_policy: ask
        ai_status: idle
        artifact_kind: other
        projects: {{VALUE:project_links_yaml}}
        ---
        # <% title %>

        ## 목적과 독자

        ## 산출물 또는 위치

        ## 검토 기준

        ## 결정 기록

        ## 변경 이력
        """
    ),
    "T24_Project_Note.md": _source(
        r"""
        ---
        schema_version: 1
        id: "{{UUID_V4}}"
        type: project_note
        title: '{{YAML_TITLE}}'
        status: active
        created: {{CREATED_AT}}
        modified: {{CREATED_AT}}
        aliases: []
        tags: []
        sensitivity: personal
        ai_policy: ask
        ai_status: idle
        projects: {{PROJECT_LINKS_YAML}}
        note_kind: exploration
        ---
        # {{TITLE}}

        ## 목적

        ## 현재 초안

        ## 열린 쟁점

        ## 정본으로 승격할 후보
        """
    ),
    "T30_Area.md": _source(
        r"""
        <%*
        const now = tp.date.now("YYYY-MM-DDTHH:mm:ssZ");
        const id = crypto.randomUUID();
        const title = tp.file.title;
        const yamlTitle = title.replace(/'/g, "''");
        -%>
        ---
        schema_version: 1
        id: "<% id %>"
        type: area
        title: '<% yamlTitle %>'
        status: active
        created: <% now %>
        modified: <% now %>
        aliases: {{VALUE:collision_aliases_yaml}}
        tags: []
        sensitivity: personal
        ai_policy: ask
        ai_status: idle
        review_cadence: monthly
        next_review: <% tp.date.now("YYYY-MM-DD", 30) %>
        standard: '<% yamlTitle %>에서 유지할 기준을 정의한다.'
        ---
        # <% title %>

        ## 책임 범위


        ## 유지할 기준


        ## 현재 프로젝트


        ## 루틴과 점검표


        ## 참고 자료


        ## Review 기록
        """
    ),
    "T40_Knowledge.md": _source(
        r"""
        <%*
        const now = tp.date.now("YYYY-MM-DDTHH:mm:ssZ");
        const id = crypto.randomUUID();
        const title = tp.file.title;
        const yamlTitle = title.replace(/'/g, "''");
        -%>
        ---
        schema_version: 1
        id: "<% id %>"
        type: knowledge
        title: '<% yamlTitle %>'
        status: seed
        created: <% now %>
        modified: <% now %>
        aliases: {{VALUE:collision_aliases_yaml}}
        tags: []
        sensitivity: personal
        ai_policy: ask
        ai_status: idle
        claim: '<% yamlTitle %>'
        confidence: unknown
        ---
        # <% title %>

        ## 핵심 주장

        ## 설명

        ## 근거

        ## 한계와 반례

        ## 적용

        ## 연결

        <!-- vaultops:knowledge-summary:begin -->
        <!-- 승인된 자동 요약만 이 구간을 교체할 수 있다. -->
        <!-- vaultops:knowledge-summary:end -->

        <!-- vaultops:link-suggestions:begin -->
        <!-- LLM이 제안한 링크는 검토 전까지 이 구간에만 둔다. -->
        <!-- vaultops:link-suggestions:end -->
        """
    ),
    "T41_Source.md": _source(
        r"""
        <%*
        const now = tp.date.now("YYYY-MM-DDTHH:mm:ssZ");
        const id = crypto.randomUUID();
        const title = tp.file.title;
        const yamlTitle = title.replace(/'/g, "''");
        -%>
        ---
        schema_version: 1
        id: "<% id %>"
        type: source
        title: '<% yamlTitle %>'
        status: queued
        created: <% now %>
        modified: <% now %>
        aliases: {{VALUE:collision_aliases_yaml}}
        tags: []
        sensitivity: personal
        ai_policy: ask
        ai_status: idle
        source_kind: web
        ---
        # <% title %>

        ## 서지정보


        ## 한 문단 요약


        ## 핵심 주장과 근거

        | 주장 | 근거 | 위치·페이지 |
        |---|---|---|

        ## 인용

        > 원문 인용은 정확한 위치와 함께 기록한다.

        ## 내 해석


        ## 파생 지식 노트

        <!-- vaultops:source-summary:begin -->
        <!-- LLM은 인용문을 창작하거나 locator를 추측해서는 안 된다. -->
        <!-- vaultops:source-summary:end -->
        """
    ),
    "T42_Person.md": _source(
        r"""
        <%*
        const now = tp.date.now("YYYY-MM-DDTHH:mm:ssZ");
        const id = crypto.randomUUID();
        const title = tp.file.title;
        const yamlTitle = title.replace(/'/g, "''");
        -%>
        ---
        schema_version: 1
        id: "<% id %>"
        type: person
        title: '<% yamlTitle %>'
        status: active
        created: <% now %>
        modified: <% now %>
        aliases: {{VALUE:collision_aliases_yaml}}
        tags: []
        sensitivity: personal
        ai_policy: deny
        ai_status: idle
        ---
        # <% title %>

        ## 맥락


        ## 함께 하는 일


        ## 최근 대화


        ## 다음 연락

        - [ ] #task

        > 비밀번호, 주민번호, 민감한 건강·금융정보는 기록하지 않는다.
        """
    ),
    "T50_MOC.md": _source(
        r"""
        <%*
        const now = tp.date.now("YYYY-MM-DDTHH:mm:ssZ");
        const id = crypto.randomUUID();
        const title = tp.file.title;
        const yamlTitle = title.replace(/'/g, "''");
        -%>
        ---
        schema_version: 1
        id: "<% id %>"
        type: moc
        title: '<% yamlTitle %>'
        status: active
        created: <% now %>
        modified: <% now %>
        aliases: {{VALUE:collision_aliases_yaml}}
        tags: []
        sensitivity: personal
        ai_policy: ask
        ai_status: idle
        scope: '<% yamlTitle %>'
        ---
        # <% title %>

        ## 이 지도가 답하는 질문


        ## 시작점


        ## 핵심 노트


        ## 논쟁과 대안


        ## 아직 비어 있는 부분

        <!-- vaultops:link-suggestions:begin -->
        <!-- LLM이 제안한 링크는 검토 전까지 이 구간에만 둔다. -->
        <!-- vaultops:link-suggestions:end -->
        """
    ),
    "T60_Meeting.md": _source(
        r"""
        <%*
        const now = tp.date.now("YYYY-MM-DDTHH:mm:ssZ");
        const id = crypto.randomUUID();
        const title = tp.file.title;
        const yamlTitle = title.replace(/'/g, "''");
        -%>
        ---
        schema_version: 1
        id: "<% id %>"
        type: meeting
        title: '<% yamlTitle %>'
        status: scheduled
        created: <% now %>
        modified: <% now %>
        aliases: {{VALUE:collision_aliases_yaml}}
        tags: []
        sensitivity: personal
        ai_policy: deny
        ai_status: idle
        meeting_at: <% now %>
        attendees: []
        ---
        # <% title %>

        ## 목적


        ## 의제

        1.

        ## 메모


        ## 결정

        | 결정 | 책임자 | 근거 |
        |---|---|---|

        ## 후속 작업

        - [ ] #task

        <!-- vaultops:meeting-summary:begin -->
        <!-- 참석자 동의와 ai_policy를 확인한 뒤 생성한다. -->
        <!-- vaultops:meeting-summary:end -->
        """
    ),
}


@dataclass(frozen=True)
class RenderedTemplate:
    template: str
    note_type: str
    properties: dict[str, object]
    body: str
    markdown: str


def template_source(filename: str) -> str:
    """Return one canonical static template source by exact filename."""

    if filename not in TEMPLATE_BY_NAME:
        raise TemplateRenderError(f"unknown template: {filename}")
    return TEMPLATE_SOURCES[filename]


def template_paths() -> tuple[str, ...]:
    return tuple(spec.relative_path for spec in TEMPLATE_SPECS)


def _now(value: object | None = None) -> datetime:
    if value is None:
        return datetime.now(ZoneInfo(CANONICAL_TIMEZONE)).replace(microsecond=0)
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value)
        except ValueError as error:
            raise TemplateRenderError(f"invalid datetime context: {value}") from error
    else:
        raise TemplateRenderError("datetime context must be an ISO string or datetime")
    if result.tzinfo is None or result.utcoffset() is None:
        raise TemplateRenderError("datetime context must include a timezone")
    return result.replace(microsecond=0)


def _iso_date(value: object | None, *, default: date | None = None) -> date:
    if value is None:
        if default is not None:
            return default
        return _now().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise TemplateRenderError(f"invalid date context: {value}") from error
    raise TemplateRenderError("date context must be YYYY-MM-DD")


def _identifier(context: Mapping[str, object]) -> str:
    value = context.get("id", context.get("identifier"))
    return str(value) if value is not None else str(uuid.uuid4())


def _list(context: Mapping[str, object], name: str) -> list[str]:
    value = context.get(name, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TemplateRenderError(f"{name} must be a flat string list")
    return list(value)


def _optional_lists(
    properties: dict[str, object], context: Mapping[str, object], names: tuple[str, ...]
) -> None:
    """Add optional context lists only when they contain user data."""

    for name in names:
        values = _list(context, name)
        if values:
            properties[name] = values


def _common(context: Mapping[str, object], note_type: str, title: str) -> dict[str, object]:
    created = _now(context.get("created", context.get("now")))
    modified = _now(context.get("modified", created))
    defaults = {
        "schema_version": 1,
        "id": _identifier(context),
        "type": note_type,
        "title": title,
        "status": context.get("status", "open"),
        "created": created.isoformat(timespec="seconds"),
        "modified": modified.isoformat(timespec="seconds"),
        "aliases": _list(context, "aliases"),
        "tags": _list(context, "tags"),
        "sensitivity": context.get("sensitivity", "personal"),
        "ai_policy": context.get("ai_policy", "ask"),
        "ai_status": context.get("ai_status", "idle"),
    }
    return defaults


def _project_link(context: Mapping[str, object]) -> str:
    value = context.get("project_link")
    if not isinstance(value, str) or not re.fullmatch(r"\[\[[^\]\n]+\]\]", value):
        raise TemplateRenderError("project_link must be one quoted wikilink")
    return value


def _body(context: Mapping[str, object], default: str) -> str:
    value = context.get("body", default)
    if not isinstance(value, str):
        raise TemplateRenderError("body must be text")
    return value


def _period_values(kind: str, context: Mapping[str, object]) -> tuple[date, date, str, str]:
    selected = _iso_date(context.get("date"))
    if kind == "daily":
        return selected, selected, selected.isoformat(), selected.isoformat()
    if kind == "weekly":
        monday = selected - timedelta(days=selected.weekday())
        sunday = monday + timedelta(days=6)
        iso_year, iso_week, _ = selected.isocalendar()
        label = f"{iso_year:04d}-W{iso_week:02d}"
        return monday, sunday, label, f"{iso_year:04d}-w{iso_week:02d}"
    if kind == "monthly":
        start = selected.replace(day=1)
        end = selected.replace(day=calendar.monthrange(selected.year, selected.month)[1])
        label = start.strftime("%Y-%m")
        return start, end, label, label
    raise TemplateRenderError(f"unsupported period template: {kind}")


def render_note_template(filename: str, context: Mapping[str, object]) -> RenderedTemplate:
    """Render one template with typed values and no template code execution."""

    spec = TEMPLATE_BY_NAME.get(filename)
    if spec is None:
        raise TemplateRenderError(f"unknown template: {filename}")
    title_value = context.get("title")
    if not isinstance(title_value, str) or not title_value:
        raise TemplateRenderError("title is required")
    title = title_value
    note_type = spec.note_type
    properties = _common(context, note_type, title)

    if note_type == "capture":
        properties.update(
            {
                "status": context.get("status", "unprocessed"),
                "tags": _list(context, "tags") or ["inbox"],
                "captured_from": context.get("captured_from", "mac_quickadd"),
                "capture_device": context.get("capture_device", "mac"),
                "capture_kind": context.get("capture_kind", "thought"),
                "triage_hint": context.get("triage_hint", "none"),
                "needs_desktop_review": context.get("needs_desktop_review", False),
            }
        )
        body = _body(context, f"# {title}\n\n## 원문\n\n\n## 맥락\n\n- 왜 지금 기록했는가:\n- 연결될 프로젝트·영역:\n\n## Triage\n\n- [ ] 버리기 / 행동으로 전환 / 정식 노트로 승격 중 하나를 결정한다.\n")
    elif note_type == "proposal":
        proposal_id = str(context.get("proposal_id", properties["id"]))
        properties.update(
            {
                "id": f"proposal-{proposal_id}",
                "status": context.get("status", "pending"),
                "ai_policy": "deny",
                "ai_status": "proposed",
                "proposal_id": proposal_id,
                "source_hashes": context.get(
                    "source_hashes", ["00_Inbox/Captures/Example.md|sha256:" + "a" * 64]
                ),
            }
        )
        body = _body(context, f"# AI 제안 — {title}\n\n## 제안 요약\n\n검토할 제안입니다.\n")
    elif note_type in {"daily", "weekly", "monthly"}:
        start, end, label, deterministic = _period_values(note_type, context)
        properties.update(
            {
                "id": f"{note_type if note_type != 'weekly' else 'weekly'}-{deterministic}",
                "title": label,
                "status": context.get("status", "open"),
                "tags": [f"journal/{note_type}"],
                "created": f"{start.isoformat()}T00:00:00+09:00",
                "modified": f"{start.isoformat()}T00:00:00+09:00",
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
            }
        )
        if note_type == "daily":
            properties["today_focus"] = context.get("today_focus", "")
            body = _body(context, f"# {label}\n\n## 오늘의 3가지 결과\n\n1.\n2.\n3.\n\n## 작업\n\n- [ ] #task\n")
        elif note_type == "weekly":
            days = [start + timedelta(days=index) for index in range(7)]
            links = "\n".join(f"- [[{item.isoformat()}]]" for item in days)
            body = _body(
                context,
                f"# {label.replace('-W', ' [W]')}\n\n## 이번 주 결과\n\n1.\n2.\n3.\n\n## 일간 기록\n\n{links}\n\n## 프로젝트 점검\n\n## 완료·미완료·대기\n\n## 다음 주로 넘길 것\n\n- [ ] #task\n\n<!-- vaultops:weekly-summary:begin -->\n<!-- 승인된 자동 요약만 이 구간을 교체할 수 있다. -->\n<!-- vaultops:weekly-summary:end -->\n",
            )
        else:
            body = _body(
                context,
                f"# {label}\n\n## 이달의 방향\n\n\n## 결과와 증거\n\n\n## 프로젝트·영역 review\n\n\n## 배운 것\n\n\n## 다음 달에 중단·시작·지속할 것\n\n- 중단:\n- 시작:\n- 지속:\n",
            )
    elif note_type == "project":
        properties.update(
            {
                "status": context.get("status", "planned"),
                "outcome": context.get("outcome", f"{title}의 완료 조건을 정의한다."),
                "priority": context.get("priority", "medium"),
            }
        )
        _optional_lists(properties, context, ("areas", "topics", "people"))
        for key in ("focus_rank", "next_action", "target_date"):
            if key in context and context[key] not in (None, ""):
                properties[key] = context[key]
        body = _body(context, f"# {title}\n\n## 원하는 결과\n\n\n## 완료 조건\n\n- [ ]\n\n## 현재 상태\n\n\n## 다음 행동\n\n- [ ] #task\n")
    elif note_type == "project_note":
        properties.update({"status": context.get("status", "active"), "projects": [_project_link(context)], "note_kind": context.get("note_kind", "exploration")})
        body = _body(context, f"# {title}\n\n## 목적\n\n## 현재 초안\n\n## 열린 쟁점\n\n## 정본으로 승격할 후보\n")
    elif note_type == "idea":
        properties.update({"status": context.get("status", "seed"), "possibility": context.get("possibility", title)})
        _optional_lists(properties, context, ("projects", "areas", "topics", "related"))
        body = _body(context, f"# {title}\n\n## 가능성\n\n## 왜 흥미로운가\n\n## 검증할 가정\n\n## 다음 실험\n\n## 연결\n")
    elif note_type == "question":
        properties.update({"status": context.get("status", "open"), "question_kind": context.get("question_kind", "research")})
        _optional_lists(properties, context, ("projects", "areas", "sources", "related"))
        for key in ("decision", "decision_by", "priority"):
            if key in context and context[key] not in (None, ""):
                properties[key] = context[key]
        body = _body(context, f"# {title}\n\n## 질문 또는 결정\n\n## 왜 지금 중요한가\n\n## 선택지 또는 가설\n\n## 판단 기준\n\n## 근거\n\n## 결정과 이유\n\n## 후속 행동\n")
    elif note_type == "artifact":
        properties.update({"status": context.get("status", "draft"), "artifact_kind": context.get("artifact_kind", "other"), "projects": [_project_link(context)]})
        for key in ("artifact_uri", "artifact_hash", "artifact_repo", "asset"):
            if key in context and context[key] not in (None, ""):
                properties[key] = context[key]
        body = _body(context, f"# {title}\n\n## 목적과 독자\n\n## 산출물 또는 위치\n\n## 검토 기준\n\n## 결정 기록\n\n## 변경 이력\n")
    elif note_type == "area":
        properties.update({"status": context.get("status", "active"), "standard": context.get("standard", f"{title}에서 유지할 기준을 정의한다."), "review_cadence": context.get("review_cadence", "monthly"), "next_review": context.get("next_review", (_now(context.get("created")).date() + timedelta(days=30)).isoformat())})
        _optional_lists(properties, context, ("topics", "people"))
        body = _body(context, f"# {title}\n\n## 책임 범위\n\n## 유지할 기준\n\n## 현재 프로젝트\n\n## 루틴과 점검표\n\n## 참고 자료\n\n## Review 기록\n")
    elif note_type == "knowledge":
        properties.update({"status": context.get("status", "seed"), "claim": context.get("claim", title), "confidence": context.get("confidence", "unknown")})
        _optional_lists(properties, context, ("areas", "projects", "topics", "sources", "related"))
        if "last_reviewed" in context:
            properties["last_reviewed"] = context["last_reviewed"]
        body = _body(context, f"# {title}\n\n## 핵심 주장\n\n## 설명\n\n## 근거\n\n## 한계와 반례\n\n## 적용\n\n## 연결\n")
    elif note_type == "source":
        properties.update({"status": context.get("status", "queued"), "source_kind": context.get("source_kind", "web")})
        _optional_lists(properties, context, ("authors", "areas", "projects", "topics", "related"))
        for key in ("source_url", "published_date", "citation_key", "asset", "asset_hash", "extractor", "extractor_version", "page_locator_scheme", "derived_from"):
            if key in context and context[key] not in (None, ""):
                properties[key] = context[key]
        body = _body(context, f"# {title}\n\n## 서지정보\n\n## 한 문단 요약\n\n## 핵심 주장과 근거\n\n## 인용\n\n## 내 해석\n\n## 파생 지식 노트\n")
    elif note_type == "person":
        properties.update({"status": context.get("status", "active"), "ai_policy": "deny"})
        organization = context.get("organization")
        if organization not in (None, ""):
            properties["organization"] = organization
        _optional_lists(properties, context, ("areas", "projects", "related"))
        body = _body(context, f"# {title}\n\n## 맥락\n\n## 함께 하는 일\n\n## 최근 대화\n\n## 다음 연락\n\n- [ ] #task\n")
    elif note_type == "moc":
        properties.update({"status": context.get("status", "active"), "scope": context.get("scope", title)})
        _optional_lists(properties, context, ("related",))
        body = _body(context, f"# {title}\n\n## 이 지도가 답하는 질문\n\n## 시작점\n\n## 핵심 노트\n\n## 논쟁과 대안\n\n## 아직 비어 있는 부분\n")
    elif note_type == "meeting":
        properties.update({"status": context.get("status", "scheduled"), "ai_policy": "deny", "meeting_at": context.get("meeting_at", properties["created"]), "attendees": _list(context, "attendees")})
        _optional_lists(properties, context, ("projects", "areas", "related"))
        body = _body(context, f"# {title}\n\n## 목적\n\n## 의제\n\n1.\n\n## 메모\n\n## 결정\n\n| 결정 | 책임자 | 근거 |\n|---|---|---|\n\n## 후속 작업\n\n- [ ] #task\n")
    else:
        raise TemplateRenderError(f"renderer is missing note type: {note_type}")

    markdown = render_frontmatter(properties, body)
    return RenderedTemplate(filename, note_type, properties, body, markdown)


_RAW_CODE_BLOCK = re.compile(r"<%\*.*?-%>\s*", re.DOTALL)
_RAW_TOKEN = re.compile(r"<%\s*([^%]+?)\s*%>|\{\{([^{}]+)\}\}")


def render_template_source(filename: str, context: Mapping[str, object]) -> str:
    """Perform a bounded source-token render for fixture parity checks.

    This is intentionally a token replacer, never a JavaScript or expression
    evaluator.  Unknown tokens remain an error so a successful fixture cannot
    hide an unrendered placeholder.
    """

    source = template_source(filename)
    values = {str(key): str(value) for key, value in context.items()}
    aliases = context.get("aliases", [])
    if isinstance(aliases, list):
        values.setdefault("VALUE:collision_aliases_yaml", yaml.safe_dump(aliases, allow_unicode=True, default_flow_style=True).strip())
    project_link = context.get("project_link")
    if isinstance(project_link, str):
        values.setdefault("VALUE:project_links_yaml", yaml.safe_dump([project_link], allow_unicode=True, default_flow_style=True).strip())
        values.setdefault("PROJECT_LINKS_YAML", values["VALUE:project_links_yaml"])
    source_hashes = context.get("source_hashes", [])
    if isinstance(source_hashes, list):
        values.setdefault("SOURCE_HASH_LIST_YAML", yaml.safe_dump(source_hashes, allow_unicode=True, default_flow_style=True).strip())
    values.setdefault("YAML_TITLE", str(context.get("title", "")).replace("'", "''"))
    values.setdefault("TITLE", str(context.get("title", "")))
    values.setdefault("UUID_V4", str(context.get("id", context.get("identifier", ""))))
    values.setdefault("CREATED_AT", str(context.get("created", context.get("now", ""))))
    values.setdefault("date:GGGG-[W]WW-lower", str(context.get("week_id", "")))
    values.setdefault("date:GGGG-[W]WW", str(context.get("week_label", "")))
    values.setdefault("date:YYYY-MM-DD", str(context.get("date", "")))
    values.setdefault("date:YYYY-MM-DD dddd", str(context.get("date_label", context.get("date", ""))))
    values.setdefault("date:YYYY-MM", str(context.get("month_label", "")))
    values.setdefault("date:GGGG [W]WW", str(context.get("week_heading", context.get("week_label", ""))))
    values.setdefault("date:YYYY-MM-01", str(context.get("month_start", "")))
    values.setdefault("monday:YYYY-MM-DD", str(context.get("monday", "")))
    values.setdefault("sunday:YYYY-MM-DD", str(context.get("sunday", "")))
    values.setdefault("time:HH:mm", str(context.get("time", "")))
    values.setdefault("tp.file.title", str(context.get("title", "")))
    values.setdefault("tp.date.now(\"YYYY-MM-DDTHH:mm:ssZ\")", str(context.get("now", "")))
    values.setdefault("tp.date.now(\"YYYY-MM-DD\", 30)", str(context.get("next_review", "")))
    values.setdefault("id", str(context.get("id", context.get("identifier", ""))))
    values.setdefault("title", str(context.get("title", "")))
    values.setdefault("yamlTitle", values["YAML_TITLE"])
    values.setdefault("now", str(context.get("now", context.get("created", ""))))
    values.setdefault("SENSITIVITY", str(context.get("sensitivity", "personal")))
    values.setdefault("PROPOSAL_FILENAME_STEM", str(context.get("proposal_filename_stem", context.get("title", ""))))
    values.setdefault("PROPOSAL_DISPLAY_TITLE", str(context.get("proposal_display_title", context.get("title", ""))))
    values.setdefault("JOB_ID", str(context.get("proposal_id", context.get("id", ""))))
    values.setdefault("SUMMARY_PLAINTEXT", str(context.get("summary", "검토할 제안입니다.")))
    values.setdefault("SOURCE_TABLE", str(context.get("source_table", "없음")))
    values.setdefault("DIFF_FENCE", str(context.get("diff", "변경 없음")))
    values.setdefault("WARNINGS_PLAINTEXT", str(context.get("warnings", "없음")))
    values.setdefault("tuesday:YYYY-MM-DD", str(context.get("tuesday", "")))
    values.setdefault("wednesday:YYYY-MM-DD", str(context.get("wednesday", "")))
    values.setdefault("thursday:YYYY-MM-DD", str(context.get("thursday", "")))
    values.setdefault("friday:YYYY-MM-DD", str(context.get("friday", "")))
    values.setdefault("saturday:YYYY-MM-DD", str(context.get("saturday", "")))
    values.setdefault("monday:YYYY-MM-DD", str(context.get("monday", context.get("date", ""))))
    values.setdefault("sunday:YYYY-MM-DD", str(context.get("sunday", context.get("date", ""))))
    values.setdefault(
        "periodId",
        str(
            context.get(
                "week_label" if filename == "T11_Weekly.md" else "month_label",
                context.get("week_id", ""),
            )
        ).lower(),
    )
    values.setdefault(
        "periodLabel",
        str(
            context.get(
                "week_label" if filename == "T11_Weekly.md" else "month_label",
                context.get("title", ""),
            )
        ),
    )
    values.setdefault(
        'periodStart.format("YYYY-MM-DD")',
        str(context.get("monday", context.get("month_start", context.get("date", "")))),
    )
    values.setdefault(
        'periodEnd.format("YYYY-MM-DD")',
        str(context.get("sunday", context.get("month_end", context.get("date", "")))),
    )
    values.setdefault(
        'periodStart.format("GGGG [W]WW")',
        str(context.get("week_heading", context.get("week_label", ""))),
    )
    for index, name in enumerate(
        ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    ):
        values.setdefault(
            f'periodStart.clone().add({index}, "days").format("YYYY-MM-DD")',
            str(context.get(name, "")),
        )
    values.setdefault("monthId", str(context.get("month_label", "")))
    values.setdefault(
        'monthStart.format("YYYY-MM-DD")',
        str(context.get("month_start", context.get("date", ""))),
    )
    values.setdefault('monthEnd.format("YYYY-MM-DD")', str(context.get("month_end", "")))

    result = _RAW_CODE_BLOCK.sub("", source)

    def replace(match: re.Match[str]) -> str:
        key = (match.group(1) or match.group(2) or "").strip()
        if key in values:
            return values[key]
        raise TemplateRenderError(f"unresolved template token: {key}")

    result = _RAW_TOKEN.sub(replace, result)
    if "<%" in result or "{{" in result:
        raise TemplateRenderError("rendered template contains an unresolved token")
    return result


def template_target_path(note_type: str, title: str, *, date_value: date | None = None) -> str:
    """Return the canonical create-only target for a template note type."""

    if not title or any(character in title for character in "/\\\x00\r\n"):
        raise TemplateRenderError("title is not a safe single path component")
    if note_type == "capture":
        selected = date_value or _now().date()
        return f"00_Inbox/Captures/{selected:%Y}/{selected:%m}/{selected:%Y%m%d-%H%M%S} {title}.md"
    if note_type == "project":
        return f"20_Projects/{title}/{title}.md"
    paths = {
        "idea": f"40_Knowledge/Ideas/{title}.md",
        "question": f"40_Knowledge/Questions/{title}.md",
        "area": f"30_Areas/{title}.md",
        "knowledge": f"40_Knowledge/Notes/{title}.md",
        "source": f"40_Knowledge/Sources/{title}.md",
        "person": f"40_Knowledge/People/{title}.md",
        "moc": f"50_Maps/{title} MOC.md",
        "meeting": f"60_Meetings/{(date_value or _now().date()):%Y}/{(date_value or _now().date()):%Y-%m-%d} — {title}.md",
    }
    try:
        return paths[note_type]
    except KeyError as error:
        raise TemplateRenderError(f"no QuickAdd-style target for {note_type}") from error


def template_spec_for_type(note_type: str) -> TemplateSpec:
    for spec in TEMPLATE_SPECS:
        if spec.note_type == note_type:
            return spec
    raise TemplateRenderError(f"unknown note type: {note_type}")
