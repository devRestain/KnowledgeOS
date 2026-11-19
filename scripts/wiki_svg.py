"""Inline SVG diagrams for the KnowledgeOS wiki.

``render(name, data)`` returns one deterministic ``<svg>`` string. Styling is class-only
(``d-box*``, ``d-edge*``, ``d-label*``, ``d-lane``); colors live in wiki.css. Fixed
diagrams describe the specification in Korean; ``capability-map`` and ``status-overview``
are driven by ``data`` (capability summary rows, acceptance IDs, PROJECT_STATE records).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from xml.sax.saxutils import escape, quoteattr

LINE = 18
SIZE = {"d-label": 14, "d-label-strong": 14, "d-label-small": 13}

CAP_KO = {
    "C1": ("정리", "분류·링크 제안"),
    "C2": ("규격화", "결정적 정리"),
    "C3": ("검색", "키워드·의미"),
    "C4": ("강화", "근거 보고서"),
    "C5": ("문서 관리", "수명주기 제안"),
    "C6": ("검토", "적용·거절"),
    "C7": ("예약 작업", "스케줄러"),
    "C8": ("알림", "Discord 알림"),
    "C9": ("학습·경험", "역할 피드백"),
    "C10": ("게이트웨이", "외부 제출·질의"),
    "C11": ("호출·관리 표면", "명령 · 사이드바 · 콘솔 · CLI"),
}


def _n(v: float) -> str:
    v = round(v, 1)
    return str(int(v)) if v == int(v) else f"{v:.1f}"


def _tw(text: str, cls: str = "d-label") -> float:
    """Estimated rendered width in viewBox units."""
    size = SIZE[cls]
    total = 0.0
    for ch in text:
        wide = unicodedata.east_asian_width(ch) in ("W", "F")
        total += size * (1.0 if wide else 0.62)
    return total * (1.06 if cls == "d-label-strong" else 1.0)


def _fit(text: str, width: float, cls: str = "d-label") -> str:
    """Truncate data-driven text so it fits ``width``."""
    if _tw(text, cls) <= width:
        return text
    while text and _tw(text + "…", cls) > width:
        text = text[:-1]
    return text + "…"


class _Svg:
    def __init__(self, name: str, width: int, height: int, title: str):
        self.name = name
        self.w = width
        self.h = height
        self.title = title
        self.parts: list[str] = []
        self.marker = f"d-arrow-{name}"

    # -- primitives ---------------------------------------------------------
    def lane(self, x, y, w, h, label: str | None = None) -> None:
        self.parts.append(
            f'<rect class="d-lane" x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" rx="10"/>'
        )
        if label:
            self.parts.append(
                f'<text class="d-label-small" x="{_n(x + 12)}" y="{_n(y + 18)}" '
                f'text-anchor="start" font-size="13">{escape(label)}</text>'
            )

    def text(self, x, y, lines: list[tuple[str, str]], anchor="middle", lh=LINE) -> str:
        """Centered-on-y block of (class, text) lines."""
        first = y - (len(lines) - 1) * lh / 2 + 5
        spans = []
        for i, (cls, s) in enumerate(lines):
            dy = "0" if i == 0 else _n(lh)
            spans.append(
                f'<tspan class="{cls}" x="{_n(x)}" dy="{dy}" font-size="{SIZE[cls]}">{escape(s)}</tspan>'
            )
        return (
            f'<text class="d-label" x="{_n(x)}" y="{_n(first)}" text-anchor="{anchor}" '
            f'font-size="14">{"".join(spans)}</text>'
        )

    def box(
        self,
        x,
        y,
        w,
        h,
        lines: list[str],
        cls="d-box",
        href: str | None = None,
        tip: str | None = None,
        strong=True,
        fit=False,
    ) -> None:
        rows: list[tuple[str, str]] = []
        for i, s in enumerate(lines):
            c = "d-label-strong" if (i == 0 and strong) else ("d-label" if (i == 0) else "d-label-small")
            if fit:
                s = _fit(s, w - 12, c)
            elif _tw(s, c) > w - 10:
                raise ValueError(f"{self.name}: text overflows box ({w}): {s!r} ~{_tw(s, c):.0f}")
            rows.append((c, s))
        if len(rows) * LINE > h:
            raise ValueError(f"{self.name}: box too short for {lines!r}")
        body = (
            f'<rect class="{cls}" x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" rx="8"/>'
            + self.text(x + w / 2, y + h / 2, rows)
        )
        if tip:
            body = f"<title>{escape(tip)}</title>" + body
        if href:
            body = f"<a href={quoteattr(href)}>{body}</a>"
        self.parts.append(body)

    def edge(self, pts: list[tuple[float, float]], dashed=False, arrow=True) -> None:
        d = "M" + " L".join(f"{_n(px)},{_n(py)}" for px, py in pts)
        cls = "d-edge d-edge-dashed" if dashed else "d-edge"
        marker = f' marker-end="url(#{self.marker})"' if arrow else ""
        self.parts.append(f'<path class="{cls}" d="{d}" fill="none"{marker}/>')

    def label(self, x, y, s: str, anchor="middle", cls="d-label-small") -> None:
        if cls not in SIZE:
            raise ValueError(cls)
        self.parts.append(
            f'<text class="{cls}" x="{_n(x)}" y="{_n(y)}" text-anchor="{anchor}" '
            f'font-size="{SIZE[cls]}">{escape(s)}</text>'
        )

    def render(self) -> str:
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg" role="img" viewBox="0 0 {self.w} {self.h}" '
            f'width="100%" preserveAspectRatio="xMidYMid meet">'
            f"<title>{escape(self.title)}</title>"
            f'<defs><marker id="{self.marker}" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto">'
            f'<path class="d-edge" d="M1,1 L9,5 L1,9" fill="none"/></marker></defs>'
        )
        return head + "".join(self.parts) + "</svg>"


# ---------------------------------------------------------------------------
# system-map
# ---------------------------------------------------------------------------
def _system_map(data: dict) -> str:
    s = _Svg("system-map", 960, 450, "KnowledgeOS 시스템 구성도: 표면, Owner, 실행 엔진, 저장소, 외부 서비스")
    s.lane(10, 8, 940, 104, "표면 · 요청은 모두 Owner로 들어간다")
    surfaces = [
        (30, ["Thin Client", "Obsidian 명령 · 사이드바"], "d-box-accent"),
        (270, ["브라우저 콘솔", "관찰 · 관리"], "d-box"),
        (510, ["CLI", "유지보수 · 설치"], "d-box"),
        (750, ["다른 Operation", "Gateway 제출 · 질의"], "d-box-muted"),
    ]
    centers = []
    for x, lines, cls in surfaces:
        s.box(x, 38, 190, 58, lines, cls)
        centers.append(x + 95)
    for cx in centers:
        s.edge([(cx, 96), (cx, 126)], arrow=False)
    s.edge([(centers[0], 126), (centers[-1], 126)], arrow=False)
    s.edge([(480, 126), (480, 150)])

    s.box(
        290,
        150,
        380,
        270,
        [
            "Owner / ExOps",
            "Director 승인 (정형=정적, 자유 텍스트=모델)",
            "정책 확인 · 입력 스냅샷 · 제출 검증",
            "Vault 쓰기 · 영수증 · 스케줄러",
            "단일 권한자: 역할은 파일·셸·네트워크 없음",
        ],
        "d-box-accent",
        fit=False,
    )
    # left column: storage
    left = [
        (150, ["Vault", "사용자 정본 · Owner만 기록"], "d-box-ok"),
        (225, ["State", "요청·Work 기록 · 벡터 색인"], "d-box"),
        (300, ["HermesTrace", "Experience 후보 (InterOps)"], "d-box-muted"),
    ]
    for y, lines, cls in left:
        s.box(30, y, 220, 56, lines, cls)
    s.edge([(290, 178), (250, 178)], arrow=True)
    s.edge([(290, 253), (250, 253)], arrow=True)
    s.edge([(290, 328), (250, 328)], dashed=True, arrow=True)
    # right column: execution and services
    right = [
        (150, ["Temporal + LangGraph", "Work 실행 · 그래프"], "d-box"),
        (225, ["Hermes 역할 프로필", "모델 역할마다 1개"], "d-box"),
        (300, ["Ollama", "로컬 임베딩 (loopback)"], "d-box-muted"),
        (375, ["kos-ops → Discord", "송신 전용 알림"], "d-box-muted"),
    ]
    for y, lines, cls in right:
        s.box(710, y, 220, 56, lines, cls)
        s.edge([(670, y + 28), (710, y + 28)], arrow=True)
    s.label(480, 442, "실선: 호출 · 기록   점선: 후보·알림 같은 단방향 전달", cls="d-label-small")
    return s.render()


# ---------------------------------------------------------------------------
# capability-map
# ---------------------------------------------------------------------------
def _clip(cx, cy, w, h, tx, ty, gap=3.0):
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    t = min((w / 2) / abs(dx) if dx else float("inf"), (h / 2) / abs(dy) if dy else float("inf"))
    length = (dx * dx + dy * dy) ** 0.5
    k = t + gap / length
    return cx + dx * k, cy + dy * k


def _cap_sort(cid: str) -> int:
    m = re.fullmatch(r"C(\d+)", cid)
    return int(m.group(1)) if m else 10**6


def _capability_map(data: dict) -> str:
    caps = {c["id"]: c for c in data.get("caps", []) if c.get("id")}
    s = _Svg("capability-map", 960, 350, "KnowledgeOS 기능 C1–C11과 의존 관계")
    NW, NH = 124, 60
    row_b = {"C2": 95, "C1": 249, "C3": 403, "C4": 557, "C5": 711, "C6": 865}
    row_c = {"C10": 95, "C7": 326, "C9": 557, "C8": 711}
    pos: dict[str, tuple[float, float]] = {}
    for cid, cx in row_b.items():
        pos[cid] = (cx, 134)
    for cid, cx in row_c.items():
        pos[cid] = (cx, 280)
    present = {cid for cid in pos if cid in caps}

    if "C11" in caps:
        s.lane(10, 6, 940, 54)
    # edges first
    trig: list[str] = []
    if "C7" in caps:
        trig = [t for t in re.findall(r"C\d+", str(caps["C7"].get("target", ""))) if t != "C7"]
    solid = [("C7", t) for t in trig]
    solid += [("C11", t) for t in ("C1", "C2", "C3", "C4", "C5", "C6")]
    dashed = [
        ("C1", "C6"),
        ("C5", "C6"),
        ("C5", "C8"),
        ("C1", "C9"),
        ("C4", "C9"),
        ("C10", "C1"),
        ("C10", "C2"),
        ("C3", "C1"),
        ("C3", "C4"),
    ]
    for src, dst in sorted(solid):
        if dst not in present:
            continue
        if src == "C11":
            if "C11" in caps:
                cx, cy = pos[dst]
                s.edge([(cx, 60), (cx, cy - NH / 2 - 3)])
            continue
        if src not in present:
            continue
        (sx, sy), (tx, ty) = pos[src], pos[dst]
        a = _clip(sx, sy, NW, NH, tx, ty)
        b = _clip(tx, ty, NW, NH, sx, sy)
        s.edge([a, b])
    for src, dst in dashed:
        if src not in present or dst not in present:
            continue
        (sx, sy), (tx, ty) = pos[src], pos[dst]
        if (src, dst) == ("C1", "C6"):
            s.edge([(sx, sy + NH / 2), (sx, 200), (tx, 200), (tx, ty + NH / 2 + 3)], dashed=True)
            continue
        a = _clip(sx, sy, NW, NH, tx, ty)
        b = _clip(tx, ty, NW, NH, sx, sy)
        s.edge([a, b], dashed=True)

    if "C11" in caps:
        ko, sub = CAP_KO["C11"]
        c = caps["C11"]
        s.box(
            20,
            14,
            920,
            38,
            [f"C11 {ko} — {sub}"],
            "d-box-muted",
            href="c11.html",
            tip=_tip(c),
        )
    cls_for = {
        "C7": "d-box-accent",
        "C10": "d-box-muted",
        "C6": "d-box-accent",
        "C8": "d-box-muted",
        "C9": "d-box-muted",
    }
    for cid in sorted(present, key=_cap_sort):
        cx, cy = pos[cid]
        ko, sub = CAP_KO.get(cid, (caps[cid].get("name", cid), ""))
        lines = [f"{cid} {ko}"] + ([sub] if sub else [])
        s.box(
            cx - NW / 2,
            cy - NH / 2,
            NW,
            NH,
            lines,
            cls_for.get(cid, "d-box"),
            href=f"{cid.lower()}.html",
            tip=_tip(caps[cid]),
            fit=True,
        )
    # legend
    s.edge([(30, 328), (70, 328)])
    s.label(78, 332, "호출 · 트리거", anchor="start")
    s.edge([(190, 328), (230, 328)], dashed=True)
    s.label(238, 332, "산출물 · 정보 흐름", anchor="start")
    return s.render()


def _tip(cap: dict) -> str:
    parts = [f"{cap.get('id', '')} {cap.get('name', '')}".strip()]
    for key in ("target", "roles", "writes"):
        if cap.get(key):
            parts.append(f"{key}: {cap[key]}")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# work-flow
# ---------------------------------------------------------------------------
def _work_flow(data: dict) -> str:
    s = _Svg("work-flow", 960, 400, "Work 흐름: 요청에서 Director 승인, 실행, 평가, Owner 효과, TeamReview까지")
    W, H = 150, 56
    cols = {"A": 20, "B": 210, "C": 400, "D": 590, "E": 780}
    rows = {1: 60, 2: 150, 3: 240}

    def b(col, row, lines, cls="d-box"):
        s.box(cols[col], rows[row] - H / 2, W, H, lines, cls)

    # edges first
    s.edge([(170, 150), (210, 150)])
    s.edge([(360, 150), (380, 150), (380, 60), (400, 60)])
    s.edge([(380, 150), (380, 240), (400, 240)])
    s.label(372, 104, "Officer 대상", anchor="end")
    s.label(372, 200, "Team 대상", anchor="end")
    s.edge([(550, 240), (590, 240)])
    s.edge([(665, 212), (665, 178)])
    s.edge([(550, 60), (635, 60), (635, 122)])
    s.edge([(740, 150), (780, 150)])
    s.label(760, 140, "충족")
    s.edge([(855, 178), (855, 212)])
    s.edge([(590, 150), (475, 150), (475, 212)], dashed=True)
    s.label(530, 142, "미충족 · 예산 내", cls="d-label-small")
    s.edge([(710, 122), (710, 60), (780, 60)], dashed=True)
    s.label(745, 48, "예산 소진")

    b("A", 2, ["요청", "표면·작업·Gateway"], "d-box-muted")
    b("B", 2, ["Director 승인", "WorkSpec 확정"], "d-box-accent")
    b("C", 1, ["Officer 단계", "정적 · 단독 결과"])
    b("C", 3, ["Manager", "그래프·후속 구간"])
    b("D", 3, ["팀원 단계", "handoff 산출"])
    b("D", 2, ["EvalOfficer", "독립 평가"], "d-box-accent")
    b("E", 2, ["Owner 효과", "기록 · 영수증"], "d-box-ok")
    b("E", 1, ["사용자에게 표시", "새 요청이 필요"], "d-box-warn")
    b("E", 3, ["TeamReview", "팀원 평가·제안"], "d-box-muted")

    s.lane(20, 290, 920, 96)
    s.parts.append(
        s.text(
            480,
            338,
            [
                ("d-label-strong", "Owner가 검증하고 기록한다 · 모델은 판단만 담당한다"),
                ("d-label-small", "정적 검사 먼저, EvalOfficer 모델은 의미 기준만 판단 · 기본 예산: Team 2구간·생산 6회, Officer 1구간"),
                ("d-label-small", "같은 미충족이 연속 두 체크포인트에 남으면 Work는 미충족으로 종료 · 대상이 바뀌면 새 Director 승인"),
            ],
        )
    )
    return s.render()


# ---------------------------------------------------------------------------
# review-flow
# ---------------------------------------------------------------------------
def _review_flow(data: dict) -> str:
    s = _Svg("review-flow", 960, 410, "검토 흐름: 제안의 적용·거절, preimage SHA 확인, applied·stale·unknown 결과")
    H = 58
    # edges
    s.edge([(150, 200), (180, 200)])
    s.edge([(310, 200), (340, 200)])
    s.label(325, 190, "적용")
    s.edge([(245, 171), (245, 55), (340, 55)])
    s.label(252, 112, "거절", anchor="start")
    s.edge([(490, 55), (520, 55)])
    s.edge([(490, 200), (520, 200)])
    s.edge([(595, 171), (595, 110), (720, 110)])
    s.label(603, 140, "일치", anchor="start")
    s.edge([(670, 200), (720, 200)])
    s.label(695, 190, "불일치")
    s.edge([(415, 229), (415, 300), (720, 300)], dashed=True)
    s.label(560, 292, "응답 유실 · 결과 미확정")

    s.box(10, 171, 140, H, ["제안 노트", "type: proposal"], "d-box-muted")
    s.box(180, 171, 130, H, ["사용자 결정", "적용 · 거절"], "d-box-accent")
    s.box(340, 26, 150, 58, ["결정 기록", "거절"], "d-box")
    s.box(520, 26, 150, 58, ["제안 파일 삭제", "대상 노트 불변"], "d-box-muted")
    s.box(340, 171, 150, H, ["결정 먼저 기록", "받음 → 적용 시도"], "d-box")
    s.box(520, 171, 150, H, ["SHA-256 확인", "대상 = preimage?"], "d-box-accent")
    s.box(720, 75, 220, 70, ["applied", "체크한 변경만 적용", "제안 파일 삭제"], "d-box-ok")
    s.box(720, 165, 220, 70, ["accepted-not-applied", "대상 변경 없음 · 제안 stale", "재요청 안내 · 7일 후 삭제"], "d-box-warn")
    s.box(720, 270, 220, 60, ["unknown", "요청 ID의 기록된 결과 조회"], "d-box-muted")
    s.label(830, 348, "재전송하지 않는다", cls="d-label-small")

    s.lane(10, 360, 940, 44)
    s.parts.append(
        s.text(
            480,
            382,
            [("d-label-small", "되돌리기: 대상이 마지막 자동 변경 결과와 같을 때만 preimage 복원 · 아니면 거절하고 Git 이력 안내")],
        )
    )
    return s.render()


# ---------------------------------------------------------------------------
# policy-matrix
# ---------------------------------------------------------------------------
def _policy_matrix(data: dict) -> str:
    s = _Svg("policy-matrix", 960, 470, "ai_policy 값별 공개 범위: 원격 모델, 로컬 역할, 임베딩, Gateway, 알림")
    heads = [
        ("원격 모델", "사용자 요청"),
        ("원격 모델", "예약 작업"),
        ("로컬 모델", "역할"),
        ("임베딩", "Ollama 로컬"),
        ("Gateway", "내보내기"),
        ("Discord 알림", "제목·링크"),
    ]
    LX, LW, CW, GAP = 10, 150, 124, 6
    ok = ("d-box-ok", "허용")
    no = ("d-box-bad", "금지")
    rows = [
        (
            ["remote_ok", "원격 허용"],
            [ok, ok, ok, ok, ("d-box-ok", "허용", "승인된 수신자"), ("d-box-ok", "허용", "제목·링크만")],
        ),
        (
            ["ask", "기본값"],
            [ok, no, ("d-box-warn", "요청만", "예약 작업 제외"), ok, no, ("d-box-ok", "허용", "제목·링크만")],
        ),
        (
            ["local_only", "로컬 전용"],
            [no, no, ("d-box-warn", "로컬 경로만", ""), ok, no, ("d-box-bad", "금지", "알림에 안 나옴")],
        ),
        (
            ["confidential", "local_only로 취급"],
            [no, no, ("d-box-warn", "로컬 경로만", ""), ok, no, ("d-box-bad", "금지", "알림에 안 나옴")],
        ),
        (
            ["deny", "모두 금지"],
            [no, no, no, ("d-box-bad", "금지", "색인 제외"), no, ("d-box-bad", "금지", "언급 금지")],
        ),
    ]
    y = 14
    for i, (a, b) in enumerate(heads):
        s.box(LX + LW + GAP + i * (CW + GAP), y, CW, 44, [a, b], "d-box-muted")
    s.box(LX, y, LW, 44, ["ai_policy", "값"], "d-box-muted")
    y = 70
    for label, cells in rows:
        s.box(LX, y, LW, 54, label, "d-box")
        for i, cell in enumerate(cells):
            cls, main = cell[0], cell[1]
            sub = cell[2] if len(cell) > 2 else ""
            s.box(LX + LW + GAP + i * (CW + GAP), y, CW, 54, [main] + ([sub] if sub else []), cls)
        y += 60
    notes = [
        "· 결정적 로컬 처리(규격화 등)는 deny를 뺀 모든 값에 허용된다. confidential은 값이 느슨해도 local_only로 처리하고 attention으로 알린다.",
        "· 예약 C1은 로컬 경로여도 remote_ok 노트만 모델에 준다. Gateway 질의는 remote_ok 비보고서 노트만 내보낸다.",
        "· 출력은 읽은 모든 노트 중 가장 엄격한 정책을 이어받는다. 정책은 공개 직전 현재 내용으로 다시 확인한다.",
        "· 알림은 본문 없이 제목·횟수·링크만 쓰고, 임베딩은 제안·99_System/·충돌 마커 노트도 제외한다.",
    ]
    for i, n in enumerate(notes):
        s.label(LX, y + 18 + i * 19, n, anchor="start")
    return s.render()


# ---------------------------------------------------------------------------
# scheduler-timeline
# ---------------------------------------------------------------------------
def _scheduler_timeline(data: dict) -> str:
    s = _Svg("scheduler-timeline", 960, 440, "스케줄러 시간선: Owner 시작, 3분 지연, 밀린 job 1회 catch-up, 이후 슬롯 실행")
    s.lane(30, 24, 250, 150, "Owner 중지됨 · 실행 없음")
    s.lane(280, 24, 650, 150, "Owner 실행 중")
    # axis
    s.edge([(30, 120), (930, 120)])
    s.label(930, 142, "시간", anchor="end")
    # missed slots
    for x in (80, 150, 220):
        s.edge([(x, 100), (x, 140)], dashed=True, arrow=False)
    s.label(150, 160, "지나간 슬롯: 실행되지 않는다")
    # start
    s.edge([(280, 54), (280, 120)], arrow=False)
    s.label(288, 140, "Owner 시작", anchor="start", cls="d-label-small")
    # startup delay
    s.box(285, 68, 125, 44, ["시작 지연 3분", "트리거 없음"], "d-box-warn")
    s.edge([(410, 90), (436, 90)])
    s.box(436, 68, 170, 44, ["catch-up", "밀린 job마다 1회"], "d-box-accent")
    # live slots
    for x in (660, 740, 820, 900):
        s.edge([(x, 100), (x, 140)], arrow=False)
    s.label(780, 160, "이후 슬롯마다 실행")

    # info boxes
    s.box(
        30,
        200,
        280,
        150,
        ["job 슬롯 (Asia/Seoul)", "standardize · 매시 :00", "embed · 매시 :30", "curate · 매일 06:00", "digest · 매일 08:00"],
        "d-box",
    )
    s.box(
        330,
        200,
        250,
        150,
        ["설정 기본값", "scheduler_enabled: true", "startup_delay_minutes: 3", "tick_seconds: 30", "타이머는 시계만 비교한다"],
        "d-box",
    )
    s.box(
        600,
        200,
        330,
        150,
        [
            "규칙",
            "슬롯 ID: schedule-<job>-<날짜시각>",
            "같은 슬롯은 한 번만 실행된다",
            "이전 실행이 진행 중이면 거절·소비",
            "일시정지 job은 재개해도 소급하지 않음",
        ],
        "d-box",
    )
    s.lane(30, 370, 900, 56)
    s.parts.append(
        s.text(
            480,
            398,
            [
                ("d-label-small", "scheduler_enabled false면 자동 트리거가 없고, knowledgeos job run 수동 실행은 계속 가능하다"),
                ("d-label-small", "트리거 영수증은 요청을 냈다는 뜻이며, 실행 결과는 별도 Work 영수증이 가진다"),
            ],
        )
    )
    return s.render()


# ---------------------------------------------------------------------------
# semantic-pipeline
# ---------------------------------------------------------------------------
def _semantic_pipeline(data: dict) -> str:
    s = _Svg("semantic-pipeline", 960, 350, "의미 색인 파이프라인: 대상 노트, 청크, Ollama 임베딩, State 벡터, 검색과 관련 노트")
    s.lane(10, 10, 740, 38)
    s.parts.append(
        s.text(380, 29, [("d-label-small", "트리거: embed 작업(매시 :30) 또는 knowledgeos index embed → 정적 EmbeddingOfficer Work")])
    )
    s.edge([(90, 48), (90, 70)])
    y, h = 70, 100
    cols = [
        (20, 160, ["자격 있는 노트", "remote_ok · ask", "local_only · 보고서", "전송 직전 재확인"], "d-box-accent"),
        (210, 160, ["청크 분할", "제목+본문 약 1,500자", "헤딩·문단 경계", "frontmatter 제외"], "d-box"),
        (400, 160, ["Ollama 임베딩", "loopback HTTP만", "qwen3-embedding", "원격 모델 없음"], "d-box-muted"),
        (590, 160, ["State 벡터 색인", "State/semantic/", "파생 데이터", "Vault·Git에 없음"], "d-box"),
    ]
    for x, w, lines, cls in cols:
        s.box(x, y, w, h, lines, cls)
    for x1, x2 in ((180, 210), (370, 400), (560, 590)):
        s.edge([(x1, 120), (x2, 120)])
    s.edge([(750, 120), (780, 89)])
    s.edge([(750, 120), (780, 151)])
    s.box(780, 62, 170, 54, ["관련 노트", "이웃 5개 · 점수 0.5↑"], "d-box-ok")
    s.box(780, 124, 170, 54, ["의미 검색", "질문만 임베딩"], "d-box-ok")

    s.box(
        20,
        215,
        300,
        100,
        ["임베딩 제외", "deny · 99_System/", "제안(proposal) · 충돌 마커 노트", "제외된 노트는 벡터도 삭제"],
        "d-box-bad",
    )
    s.edge([(170, 215), (170, 170)], dashed=True)
    s.box(
        340,
        215,
        280,
        100,
        ["증분 갱신", "내용 SHA-256이 다르거나", "벡터가 없는 노트만 처리", "삭제·이름 변경도 정리"],
        "d-box",
    )
    s.edge([(480, 215), (480, 170)], dashed=True)
    s.box(
        640,
        215,
        310,
        100,
        ["실패와 준비 상태", "Ollama 불가: Work 실패", "기존 벡터는 유지", "벡터 없으면 의미 검색 unavailable"],
        "d-box-warn",
    )
    s.edge([(795, 215), (795, 178)], dashed=True)
    s.label(480, 338, "키워드 검색은 Obsidian 핵심 검색이 맡고 인덱스 없이 동작한다")
    return s.render()


# ---------------------------------------------------------------------------
# status-overview
# ---------------------------------------------------------------------------
_STATE_ORDER = ["pass", "fail", "not_run", "blocked", "unknown", "unconfigured"]
_STATE_CLS = {
    "pass": "d-box-ok",
    "fail": "d-box-bad",
    "blocked": "d-box-warn",
    "unknown": "d-box-warn",
    "not_run": "d-box-muted",
    "unconfigured": "d-box-muted",
}
_CLOSED = {"resolved", "cancelled", "closed", "complete"}


def _counts(values: list[str]) -> list[tuple[str, int]]:
    tally: dict[str, int] = {}
    for v in values:
        tally[v] = tally.get(v, 0) + 1
    known = [(k, tally[k]) for k in _STATE_ORDER if k in tally]
    rest = sorted((k, v) for k, v in tally.items() if k not in _STATE_ORDER)
    return known + rest


def _status_overview(data: dict) -> str:
    acceptance = data.get("acceptance", {}) or {}
    records = data.get("records", []) or []
    accept_rows = [(cid, len(ids)) for cid, ids in sorted(acceptance.items(), key=lambda kv: _cap_sort(kv[0]))]
    total_acc = sum(n for _, n in accept_rows)
    accepts = _counts([str(r.get("state") or "unknown") for r in records if r.get("kind") == "accept"])
    evidence = _counts([str(r.get("result") or "unknown") for r in records if r.get("kind") == "evidence"])
    blockers = [r for r in records if r.get("kind") == "blocker"]
    open_blockers = [r for r in blockers if str(r.get("state") or "open") not in _CLOSED]

    ROW = 26
    top = 14
    left_h = 40 + max(len(accept_rows), 1) * ROW + 12
    acc_h = 40 + max(len(accepts), 1) * ROW + 12
    evi_h = 40 + max(len(evidence), 1) * ROW + 12
    blk_h = 40 + 24 + 22 * min(len(open_blockers), 4) + 12
    right_h = acc_h + 10 + evi_h + 10 + blk_h
    H = top + max(left_h, right_h) + 14
    s = _Svg("status-overview", 960, H, "현황 요약: 기능별 인수 기준 수, accept 상태, evidence 결과, 열린 blocker")

    def bars(x, y, w, label_w, bar_max, rows, cls_of: Callable[[str], str], label_of: Callable[[str], str]):
        peak = max((n for _, n in rows), default=1) or 1
        for i, (key, n) in enumerate(rows):
            ry = y + i * ROW
            s.label(x, ry + 17, _fit(label_of(key), label_w - 8, "d-label-small"), anchor="start")
            bw = max(4.0, bar_max * n / peak) if n else 2.0
            s.parts.append(
                f'<rect class="{cls_of(key)}" x="{_n(x + label_w)}" y="{_n(ry + 4)}" '
                f'width="{_n(bw)}" height="16" rx="4"/>'
            )
            s.label(x + label_w + bw + 8, ry + 17, str(n), anchor="start", cls="d-label-strong")

    # left panel
    lx, lw = 10, 450
    s.lane(lx, top, lw, left_h, f"기능별 인수 기준 · 합계 {total_acc}")
    if accept_rows:
        bars(
            lx + 14,
            top + 34,
            lw,
            160,
            240,
            accept_rows,
            lambda k: "d-box-accent",
            lambda k: f"{k} {CAP_KO[k][0]}" if k in CAP_KO else k,
        )
    else:
        s.label(lx + 14, top + 56, "기록 없음", anchor="start")
    # right panels
    rx, rw = 480, 470
    y = top
    s.lane(rx, y, rw, acc_h, f"accept 기록 · 합계 {sum(n for _, n in accepts)}")
    if accepts:
        bars(rx + 14, y + 34, rw, 110, 280, accepts, lambda k: _STATE_CLS.get(k, "d-box"), lambda k: k)
    else:
        s.label(rx + 14, y + 56, "기록 없음", anchor="start")
    y += acc_h + 10
    s.lane(rx, y, rw, evi_h, f"evidence 결과 · 합계 {sum(n for _, n in evidence)}")
    if evidence:
        bars(rx + 14, y + 34, rw, 110, 280, evidence, lambda k: _STATE_CLS.get(k, "d-box"), lambda k: k)
    else:
        s.label(rx + 14, y + 56, "기록 없음", anchor="start")
    y += evi_h + 10
    s.lane(rx, y, rw, blk_h, f"열린 blocker · 전체 {len(blockers)}")
    cls = "d-box-warn" if open_blockers else "d-box-ok"
    s.box(rx + 14, y + 30, 120, 40, [f"열림 {len(open_blockers)}"], cls)
    if not open_blockers:
        s.label(rx + 150, y + 55, "열린 blocker 없음", anchor="start")
    for i, r in enumerate(open_blockers[:4]):
        s.label(rx + 150, y + 46 + i * 22, _fit(str(r.get("id") or "-"), rw - 170), anchor="start", cls="d-label")
    return s.render()


# ---------------------------------------------------------------------------
_DIAGRAMS: dict[str, Callable[[dict], str]] = {
    "system-map": _system_map,
    "capability-map": _capability_map,
    "work-flow": _work_flow,
    "review-flow": _review_flow,
    "policy-matrix": _policy_matrix,
    "scheduler-timeline": _scheduler_timeline,
    "semantic-pipeline": _semantic_pipeline,
    "status-overview": _status_overview,
}


def render(name: str, data: dict) -> str:
    """Return the inline ``<svg>`` for ``name``; ``KeyError`` for an unknown diagram."""
    return _DIAGRAMS[name](data)
