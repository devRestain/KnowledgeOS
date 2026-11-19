# 로드맵: 막힘 · 시작 전 · 후속 과제

이 페이지는 **무엇이 막혀 있고, 무엇이 아직 시작 전이며, 다음에 무엇을 할 것인가**를 한곳에 모읍니다. 이미 되어 있는 것과 그 근거는 [[status]]에 있습니다.

| 구분 | 뜻 | 이 페이지의 절 |
|---|---|---|
| **막힘** | 진행을 막는 것으로 기록된 일 | 지금 막힌 것 |
| **인계** | 다음 세션이 이어받을 일 | 다음 세션이 이어받을 일 |
| **미확인** | 이 종류의 증거가 아직 없는 것 | 아직 확인하지 못한 것 |
| **열린 질문** | 명세가 아직 정하지 않은 것 | Blueprint §9의 열린 질문 |
| **시작 전** | 명세에 자리만 잡아 둔 모바일 후속 | 시작 전: 모바일 후속 과제 |
| **후속 구현 과제** | 위를 한 목록으로 모으고 출처를 단 것 | 후속 구현 과제 |

## 지금 막힌 것

`PROJECT_STATE.md`의 막힘(blocker) 기록입니다. `state`가 `resolved`이면 해결된 기록이며, 이력으로 남겨 둔 것입니다. **열린 막힘이 없다면 모든 행이 `resolved`입니다.**

{{state:blockers}}

**과거의 미확인 기록**도 남아 있습니다. 사이드바 1.1.0의 macOS 기본 팝업 메뉴는 접근성 자동화가 거부되고 렌더러 DOM에도 나타나지 않아 확인하지 못했습니다([[status#E_SIDEBAR_NATIVE_MENUS|E_SIDEBAR_NATIVE_MENUS]], blocked). 이 이력은 지우지 않습니다. 현재 1.2.0은 팝업 대신 인라인 입력·검색 결과·미리보기 결정을 사용하며([[status#D_SIDEBAR_INLINE|D_SIDEBAR_INLINE]]), 실제 Obsidian에서 인라인 기능과 의미 검색 결과 7개, 밝은/어두운 테마와 242px 레이아웃을 확인했습니다([[status#E_SIDEBAR_INLINE_GUI|E_SIDEBAR_INLINE_GUI]]). 따라서 과거 팝업의 검증을 현재 UI의 후속 과제로 두지 않습니다.

## 다음 세션이 이어받을 일

`PROJECT_STATE.md`의 인계(handoff) 기록입니다.

{{state:handoff}}

풀어 쓰면 두 가지입니다.

1. **첫 자연 실행 관찰**: 지금까지는 임시로 만든 작업이 한 번 도는 것만 확인되었습니다([[status#E_REAL_SCHEDULER_SLOT|E_REAL_SCHEDULER_SLOT]]). Owner가 켜져 있는 동안 선언된 `standardize`(매시 정각)와 `embed`(매시 30분)가 제 시각에 저절로 도는지 지켜봅니다. 설명은 [[c7]], 방법은 [[usage]]를 보세요.
2. **`related_min_score` 조정**: 관련 노트에 의미 이웃을 올리는 기준 점수(기본 0.5)는 "설정한 모델에 맞춰 조정할 시작값"이라고 명세에 적혀 있습니다. 실제 노트로 보고 알맞게 고칩니다([[c3]]).

## 아직 확인하지 못한 것

"실패했다"가 아니라 **"이 종류의 증거가 아직 `PROJECT_STATE.md`에 없다"**는 뜻입니다. 기록이 없다는 점은 사실이지만, 실제로 일어났는지는 [해석]으로 남습니다. 근거의 종류는 [[status]]의 "근거의 종류"를 보세요.

| 아직 확인하지 못한 것 | 지금 있는 기록 | 출처 |
|---|---|---|
| 선언된 예약 작업(`standardize` 매시 정각, `embed` 매시 30분)의 자연 실행 | 임시 작업의 슬롯 실행 1회 | 인계 기록, `E_REAL_SCHEDULER_SLOT` |
| 예약 `curate`(06:00)·`digest`(08:00)의 자연 실행 | 기록 없음 [해석: 임시 작업과 수동 요약 전달만 있음] | `PROJECT_STATE.md` |
| 실제 HermesTrace 수신기와의 연동 | README가 "별도로 확인해야 한다"고 한 항목이며 기록 없음 | README "검증 범위", [[c9]] |
| 즉시 알림(충돌·결과 불명·작업 실패)의 실제 Discord 전달 | 요약 알림 1건 전달만 있음 | `E_DISCORD_DELIVERED` |
| Gateway([[c10]])의 실제 다른 Operation과의 송수신 | 기록 없음(테스트만) | 추적표, `PROJECT_STATE.md` |
| 실제 Owner의 관리 콘솔 사용 | 일회용 합성 환경의 콘솔 관찰만 있음 | `E_CONSOLE_BROWSER` |
| 제안 거절·낡은 제안·자동 변경 되돌리기의 실제 사용 | 제안 적용 1건만 있음 | `E_USER_PROPOSAL_APPLY` |
| 모델 응답의 품질·꾸준함 | 모든 모델 역할을 스모크 노트 세 개로 한 번씩 돌린 기록 | `E_REAL_ROLE_SMOKE` |
| 모바일 흐름 M1~M3 | 시작 전 | 명세의 모바일 후속 |

> [!note]
> 추적표 문서의 머리말은 "네이티브 Hermes/모델 호출, 실제 Discord, 실제 Owner 서비스 배포, Obsidian/장치 검증은 돌리지 않았다"고 적습니다. 그 문장은 **추적표가 가리키는 테스트**에 대한 것이고, 그중 일부는 이후 실제 환경 스모크로 확인되었습니다([[status]]). 위 표는 **지금도** 기록이 없는 것만 남겼습니다.

## Blueprint §9의 열린 질문

명세가 아직 정하지 않은 것입니다. 실제 사용으로 답이 나면 같은 세션에서 §9에서 **지웁니다**([[philosophy]]).

{{include:BLUEPRINT#9. Open questions}}

각 질문이 무엇을 기다리는지 한 줄씩 풀면 [해석]:

- **Semantic ranking**: 위 인계 2번과 같은 일입니다. 실제 노트로 `related_min_score`와 이웃 수를 보고 정합니다.
- **Default `ai_policy`**: 예약 정리(`curate`)를 실제로 쓰기 시작한 뒤에야 `ask`가 맞는 기본값인지 판단할 수 있습니다. `ask` 노트는 예약 작업의 모델에게 보이지 않습니다([[ORG-3]]).
- **Schedules**: 선언된 주기가 실제 사용과 맞는지는 자연 실행을 지켜본 뒤 판단합니다([[c7]]).
- **Plugin choices**: Obsidian 코어 Bases만으로 문서 관리 목록이 충분한지, 다른 커뮤니티 플러그인이 필요한지([[c5]]).

## 시작 전: 모바일 후속 과제

모바일에서도 처리는 **언제나 Mac의 KnowledgeOS**가 합니다. 모바일은 만들고, 고치고, 표시하고, 동기화만 합니다. 아래 세 가지(M1~M3)는 **시작 전**이며 명세는 시작할 때 구체화합니다. 모바일 전용 KnowledgeOS 앱은 하지 않기로 한 범위 밖입니다([[philosophy]]).

{{include:CAPABILITIES#Follow-up: mobile}}

쉬운 말로 정리하면 이렇습니다.

| 과제 | 무엇 | 아직 정해야 할 것 (원본) |
|---|---|---|
| **M1 템플릿·동기화 단축어** | Apple 단축어로 포착·아이디어·질문 노트를 Vault 템플릿에서 만들고 Working Copy의 pull·commit·push를 구동 | 인수 기준을 써야 함: 단축어로 만든 노트가 [[STD-7]]을 통과해야 함 |
| **M2 Vault를 통한 요청** | 노트에 `kos_request: <기능[:모드]>`를 표시하거나 `00_Inbox/Requests/`에 요청 노트를 두면, Mac이 pull한 뒤 Director가 접수. 결과는 평소처럼 쓰이고 요청 표시는 규격화 쓰기 경로로 지움 | 기기 간 요청 신원, 중복 표시, 결과를 요약으로 알릴지 즉시 알림으로 알릴지 |
| **M3 GitHub Actions** | push 때의 결정적 점검(YAML 유효성, 충돌 표시, 중복 ID)을 점검 상태나 이슈로 보고 | 제약: 모델 호출 없음, KnowledgeOS 자격 증명 없음, Blueprint §5.7을 고쳐 두 번째 Git 작성자를 인정하기 전에는 commit 없음 |

`kos_request` 속성과 `00_Inbox/Requests/` 폴더는 이 용도로 **예약**되어 있어 비활성이며, 다른 용도로 쓰지 않습니다. 지금 모바일에서 하는 일(Obsidian으로 쓰고 Working Copy로 동기화하면 Mac이 예약 작업으로 규격화·정리)은 아래 원문의 "지금" 부분입니다.

{{include:USER#모바일}}

## 구현 순서(명세)

명세는 기능을 얇은 조각으로 나눠 차례로 끝까지 만들고, 한 조각을 끝낼 때마다 명세를 고치도록 정합니다.

{{include:CAPABILITIES#Delivery order}}

`PROJECT_STATE.md`의 목표 `KNOWLEDGEOS_CAPABILITY_IMPLEMENTATION`은 완료로 기록되어 있어 1~5번 조각은 모든 인수 시나리오가 테스트로 구현된 상태입니다. 6번(모바일)만 시작 전입니다. 지금의 목표 `KNOWLEDGEOS_REAL_ADOPTION`은 이 구현을 실제 호스트에서 하나씩 확인하는 일입니다([[status]]).

## 일부러 하지 않기로 한 것

기록에 **결정**으로 남은, 빠진 것이 아니라 의도한 것입니다. 후속 과제로 오해하지 마세요.

- **알림 기록에 전달 실패 이유를 남기지 않는다**: [[status#D_NOTICE_NO_FAILURE_REASON|D_NOTICE_NO_FAILURE_REASON]].
- **TeamReview의 일반 개선 제안은 후보로 자동 승격하지 않는다.** 로컬 관찰로 두고, 이후 실제 사용자 수정 같은 사건이 생기면 그 사건 자체의 근거로 새로 기록합니다: [[status#D_C9_PENDING_CLASSIFICATION|D_C9_PENDING_CLASSIFICATION]], [[c9]].
- **Temporal은 호스트 loopback에만 공개한다**: [[status#D_TEMPORAL_LOOPBACK|D_TEMPORAL_LOOPBACK]].
- **하지 않는 일**(브라우저·CLI에서 문서 작업, Discord로 명령 받기, 모바일 전용 KnowledgeOS, Git 명령 실행 등)은 [[philosophy]]에 있습니다.

## 후속 구현 과제

> [!warning]
> 이 목록은 새 요구를 만든 것이 아니라, 원본 문서가 이미 **후속·미확인·미정**으로 적어 둔 것을 한곳에 모으고 **출처를 단** 해설입니다. 출처에 명시되지 않은 연결은 [해석]으로 표시했습니다. 우선순위나 일정은 정해져 있지 않습니다.

### A. 이미 인계된 일 (PROJECT_STATE의 handoff)

| # | 과제 | 출처 |
|---|---|---|
| A1 | Owner가 켜져 있는 동안 매시 `standardize`·`embed`의 첫 자연 실행 관찰 | `PROJECT_STATE.md` handoff |
| A2 | `related_min_score`를 실제 노트로 조정 | `PROJECT_STATE.md` handoff, `docs/CAPABILITIES.md` C3("시작값"), Blueprint §9 Semantic ranking |

### B. 확인되지 않은 실제 환경 구성

| # | 과제 | 출처 · 비고 |
|---|---|---|
| B1 | 실제 HermesTrace 수신기 확인 | README "검증 범위", `docs/CAPABILITIES.md` C9, 기록 없음 |
| B2 | 예약 `curate`·`digest`의 자연 실행, 즉시 알림의 실제 Discord 전달 | 기록 없음 [해석]. Blueprint §9 Schedules·Default `ai_policy`가 답을 기다리는 자리이기도 합니다 |
| B3 | Gateway의 실제 Operation 간 송수신 | 기록 없음 [해석]. 명세는 [[c10]] |
| B4 | 실제 Owner에서 관리 콘솔 사용 확인 | `E_CONSOLE_BROWSER`는 일회용 환경만 다룸 [해석] |

### C. 정해야 할 것 (Blueprint §9)

| # | 질문 | 출처 |
|---|---|---|
| C1 | `ask`가 예약 정리를 쓰기 시작한 뒤에도 알맞은 기본 `ai_policy`인가 | Blueprint §9 |
| C2 | C7의 기본 작업 주기가 실제 사용과 맞는가 | Blueprint §9 |
| C3 | 문서 관리 화면에 코어 Bases로 충분한가, 다른 커뮤니티 플러그인이 필요한가 | Blueprint §9 |

(의미 검색 순위 기준 질문은 A2와 같은 일입니다.)

### D. 모바일 (명세의 Follow-up: mobile)

| # | 과제 | 출처 |
|---|---|---|
| D1 | M1 템플릿·동기화 단축어. 인수 기준 작성: 단축어로 만든 노트가 STD-7 통과 | `docs/CAPABILITIES.md` Follow-up, Blueprint §9 |
| D2 | M2 Vault를 통한 요청. 정할 것: 기기 간 요청 신원, 중복 표시, 결과 알림 방식 | 같음 |
| D3 | M3 GitHub Actions. 제약: 모델 호출 없음, 자격 증명 없음, §5.7 개정 전 commit 없음 | 같음 |

### E. 조건이 생기면 하는 일

| # | 과제 | 출처 |
|---|---|---|
| E1 | HermesTrace가 승격한 능력을 쓰려면 버전·digest를 고정한 구성 변경으로 명시적으로 채택. Core 계약과 스키마는 그대로 | `docs/CAPABILITIES.md` C9, Blueprint §4.5 |
| E2 | 역할 추가·제거는 사용자가 검토하는 명세 변경이며 대개 TeamReview 제안이 계기 | Blueprint §4.2 |
