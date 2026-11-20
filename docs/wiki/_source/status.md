# 현재 상태: 무엇이 어떤 근거로 확인되었나

이 페이지는 `PROJECT_STATE.md`(진행과 검증 증거의 유일한 기계 판독 원본)를 읽기 쉽게 보여 줍니다. 핵심 질문은 두 가지입니다. **무엇이 되어 있는가**, 그리고 **그것을 어떤 종류의 근거로 알 수 있는가.** 아직 안 된 것과 다음에 할 일은 [[roadmap]]에 있습니다.

## 한눈에 보기

단계로 보면 이렇습니다. 앞의 두 단계(명세 재정립, 이전 구현 퇴역)와 세 번째 단계(모든 인수 시나리오를 공개 인터페이스 테스트로 구현)는 **완료**로 기록되어 있고, 지금 **진행 중인 목표는 실제 호스트 도입**(`KNOWLEDGEOS_REAL_ADOPTION`)입니다. 즉 "명세대로 동작하는 코드와 테스트"는 갖춰졌고, 이제 실제 Temporal·Hermes 역할 profile·Vault·Obsidian 위에서 하나씩 확인해 가는 중입니다.

{{svg:status-overview}}

> [!note]
> 아래 숫자와 표는 매번 `PROJECT_STATE.md`와 명세에서 다시 만들어집니다. 이 페이지의 문장은 사람이 쓴 해설이라 표보다 늦을 수 있으니, 어긋나 보이면 **표가 맞습니다.**

## 근거의 종류 (evidence class)

증거 기록마다 `class`가 붙어 있습니다. 같은 "pass"라도 종류에 따라 증명하는 범위가 다릅니다.

> [!note]
> 원본에는 종류의 정의가 한곳에 적혀 있지 않습니다(저장소 규칙은 "static, runtime, deployment, device 증거를 따로 기록하고 돌리지 않은 것은 정확히 표시하라"고만 합니다). 아래 정의는 기록들의 쓰임에서 읽어 낸 **해석**입니다.

| class | 어떤 근거인가 | 기록 예 |
|---|---|---|
| `static` | 소스·문서·표를 **읽고 대조**한 결과. 시스템을 실제로 돌리지는 않습니다 | 추적표가 인수 ID와 일치(`E_CAPABILITY_TRACE`), 퇴역 자산 삭제(`E_RETIREMENT_DELETE`) |
| `runtime` | 코드를 **실제로 실행**하거나 실행 중인 서비스·프로세스를 관찰한 결과. 일회용 환경인지 실제 호스트인지는 각 기록의 `source`와 `observed`로 구분합니다 | 테스트 전체 통과(`E_TRACED_TESTS`), 합성 데이터의 콘솔 관찰(`E_CONSOLE_BROWSER`), 실제 호스트의 예약 슬롯 관찰(`E_REAL_SCHEDULER_SLOT`) |
| `external_service` | **실제 외부 서비스**(모델, Discord, Ollama)를 호출해 얻은 결과 | 실제 모델 역할 스모크(`E_REAL_ROLE_SMOKE`), Discord 전달(`E_DISCORD_DELIVERED`), 실제 임베딩(`E_REAL_SEMANTIC`) |
| `device` | **사용자의 실제 장치·Vault·Obsidian**에서 관찰한 결과 | Obsidian에서 Owner 시작·중지(`E_OBSIDIAN_OWNER_LIFECYCLE`), 사용자가 제안 적용(`E_USER_PROPOSAL_APPLY`) |
| `deployment` | 설치·구성이 **실제 호스트에 적용**되었음 | 사이드바 플러그인 설치(`E_SIDEBAR_INSTALL`), Hermes cron 제거(`E_HERMES_CRON_REMOVED`) |
| `artifact` | 만들어진 **산출물**(이미지, 아카이브, 해시)의 확인 | 개발 이미지 빌드(`E_DEV_IMAGE_BUILD`), 아카이브 기록(`E_RETIREMENT_ARCHIVE`) |
| `semantic` | 명세 해석·결정을 정리한 **의미적 해소** | TeamReview 제안의 분류 충돌 해소(`E_C9_CATEGORY_CONFLICT`) |

`result`는 `pass`, `fail`, `blocked` 중 하나입니다. **`fail`과 `blocked` 기록은 나중에 해결되어도 지우지 않고 남깁니다**(예: [[E_DISCORD_MISSING_ACCESS]]는 이후 [[E_DISCORD_DELIVERED]]가 생겼지만 그대로 있습니다).

## "pass"가 증명하는 것과 증명하지 않는 것

| 확인된 것 | 증명하는 것 | 증명하지 **않는** 것 |
|---|---|---|
| 테스트 통과 (`runtime`) | 명세의 시나리오가 **공개 인터페이스를 통해** 기대대로 동작함. 실제 Temporal 개발 서버와 LangGraph 위에서 돌았음 | 실제 모델의 응답 품질, 실제 Hermes profile 설치, 예약 작업의 실제 시각 실행, launchd 등록과 Owner 시작·중지, Discord 전송, 실제 HermesTrace 수신기, Obsidian GUI. 테스트에서 모델 역할은 스크립트 대역이고 Hermes와 `launchctl`은 가짜 실행 파일이며 시계는 가짜입니다 |
| 추적표 일치 (`static`) | 모든 인수 ID에 구현 위치와 테스트가 **연결되어 있음** | 테스트가 통과했다는 것, 배포되었다는 것 |
| 일회용 환경 관찰 (`runtime`) | 합성 데이터로 띄운 Owner가 콘솔에서 보이고 동작함 | 실제 Owner·실제 Vault에서도 같다는 것 |
| 실제 외부 서비스 한 번의 성공 (`external_service`) | 그 구성에서 **조립이 된다**는 것(모델 경로, 전달 경로, 임베딩 경로) | 품질이나 꾸준한 신뢰성. 예: 모델 역할 스모크는 라벨이 붙은 스모크 노트 세 개로 한 번씩 돌린 것입니다 |
| 실제 장치에서의 관찰 (`device`) | 그 장치·그 시점·그 버전에서 그것이 보이고 동작함 | 다른 버전이나 관찰하지 않은 부분. 예: 1.2.0의 인라인 GUI 관찰([[E_SIDEBAR_INLINE_GUI]])이 앞선 1.1.0 팝업 메뉴의 미확인 이력([[E_SIDEBAR_NATIVE_MENUS]])을 바꾸지는 않습니다 |
| 브라우저 대역 렌더링 | 사이드바 소스가 DOM 대역에서 그려지고 넘침이 없음([[E_SIDEBAR_PREVIEW]]) | 실제 Obsidian 플러그인 설치와 GUI 관찰은 **별개**입니다(README) |
| System One 실제 호출 (`external_service`) | 실제 Owner가 OpenAI Decisions API에 닿아 `system_one: ready`를 보고하고, 실제 노트의 규격화 미리보기·자유 요청 거름·관련 노트 거르기가 한 번씩 동작함([[E_SYSTEM_ONE_LIVE]]) | 자동 `standardize`가 실제로 값을 쓰는 것, 문서 관리 상태 제안, 확신도 기준이 실제 노트에 맞는지. 테스트(`E_SYSTEM_ONE_TESTS`)의 System One 응답은 loopback 가짜 Decisions API입니다 |
| 1.3.0 사이드바의 System One 결정 줄 (`deployment` + `device`) | 소스와 일치하는 1.3.0이 활성 플러그인 폴더에 설치·재로드되었고([[E_SYSTEM_ONE_CLIENT_INSTALL]]), 실제 Obsidian 사이드바에서 "System One: status → seed (93%)"가 적용/취소와 함께 모달·메뉴 없이 보였으며 취소가 아무것도 쓰지 않음([[E_SYSTEM_ONE_CLIENT_GUI]]) | macOS 화면이 잠겨 있어 시각 확인은 사이드바 영역의 CDP 렌더뿐이고, 정리 완료는 Owner State에서 읽은 것입니다. 관련 노트·의미 검색 화면은 1.3.0에서 따로 관찰하지 않았습니다 |

그래서 [[roadmap]]의 "아직 확인하지 못한 것"은 "실패했다"가 아니라 "이 종류의 증거가 아직 없다"는 뜻입니다.

> [!warning]
> 추적표 문서의 머리말에는 "네이티브 Hermes/모델 호출, 실제 Discord, 실제 Owner 서비스 배포, Obsidian/장치 검증은 돌리지 않았다"고 적혀 있습니다. 이 문장은 **추적표가 가리키는 테스트 묶음**의 범위를 말한 것이고, 그 뒤 실제 환경 스모크는 `PROJECT_STATE.md`에 따로 기록되어 있습니다(아래 증거 표). 둘을 섞어 읽지 마세요.

`PROJECT_STATE.md` 맨 위의 `/checkpoint` 줄에는 기록 시점의 revision과 dirty 여부가 있습니다. revision이 `UNKNOWN`이거나 `dirty=true`이면 그 기록이 특정 커밋에 고정되지 않았다는 뜻입니다 [해석].

## 기능별 현황 요약

> [!status]
> 이 표는 아래 증거 표를 기능 기준으로 다시 묶은 **해설**입니다. "기록 없음"은 PROJECT_STATE에 해당 확인 기록이 없다는 뜻이며, 확인했는데 실패했다는 뜻이 아닙니다. 인수 시나리오의 구현·테스트 연결은 각 기능 페이지의 인수 표에 있습니다.

| 기능 | 실제 환경에서 확인된 것 | 기록 없음·남은 것 |
|---|---|---|
| [[c1]] 정리 | Obsidian에서 정리 요청 → 제안 노트 하나 생성([[E_REAL_ROLE_SMOKE]]). 사용자가 제안을 적용(체크한 변경 7개, [[E_USER_PROPOSAL_APPLY]]). 사이드바 정리가 System One `scout` 힌트(type knowledge 1.0, status developing 0.74)를 `kos-classifier`에 전달해 열린 제안 하나를 남김([[E_SYSTEM_ONE_CLIENT_GUI]]) | 예약 `curate`(06:00)의 자연 실행. 남은 열린 제안의 사용자 결정(인계) |
| [[c2]] 규격화 | 실제 Vault의 `vault-check`가 깨진 YAML 없음을 보도록 `T10_Daily`를 규격에 맞춤([[E_VAULT_CONFIG_CLEANUP]]). 실제 Owner(`system_one: ready`)의 수동 미리보기가 실제 노트의 `status: draft`를 `seed`(0.93)로 매핑하고 취소해 Vault 22개 해시 보존([[E_SYSTEM_ONE_LIVE]], [[E_SYSTEM_ONE_PRESERVATION]]). 설치된 1.3.0 사이드바가 같은 값을 diff 위에 표시([[E_SYSTEM_ONE_CLIENT_GUI]]) | 매시 `standardize`의 자연 실행(인계 사항), 자동 경로의 System One 적용 실관찰 없음 |
| [[c3]] 검색 | 로컬 Ollama로 노트 7개를 조각 21개로 임베딩하고 Obsidian에서 줄 위치가 달린 의미 검색 결과 확인([[E_REAL_SEMANTIC]]). 실제 관련 노트 검색이 후보를 System One으로 0.3초에 거름([[E_SYSTEM_ONE_LIVE]]) | `related_min_score`·`related_min_probability` 조정, 매시 30분 `embed`의 자연 실행 |
| [[c4]] 강화 | Obsidian에서 강화 요청 → 보고서 하나 생성([[E_REAL_ROLE_SMOKE]]) | — |
| [[c5]] 문서 관리 | 요약 알림 하나가 전달됨([[E_DISCORD_DELIVERED]]) | Bases 보기·주의 항목의 실제 사용 기록 없음. System One 상태 제안(DOC-5)의 실제 관찰 기록 없음 |
| [[c6]] 검토 | 사용자가 제안을 적용([[E_USER_PROPOSAL_APPLY]]), 제안 전용 도구 모음 구성([[E_VAULT_CONFIG_CLEANUP]]) | 거절·되돌리기의 실제 사용 기록 없음 |
| [[c7]] 예약 작업 | 임시 작업이 시작 지연 뒤 슬롯에서 한 번 실행([[E_REAL_SCHEDULER_SLOT]]), Hermes cron 제거([[E_HERMES_CRON_REMOVED]]) | 선언된 네 작업의 자연 실행 |
| [[c8]] 알림 | 요약 하나가 `kos-ops`를 통해 Discord 채널에 전달([[E_DISCORD_DELIVERED]]). 처음의 403 실패는 기록으로 남음([[E_DISCORD_MISSING_ACCESS]]) | 즉시 알림(충돌·결과 불명·실패)의 실제 전달 기록 없음 |
| [[c9]] 학습·Experience | TeamReview 제안은 로컬 관찰로 두기로 결정·정리([[E_C9_CATEGORY_CONFLICT]]) | 실제 HermesTrace 수신기 확인은 README가 "별도 확인 필요"로 둔 항목이며 기록 없음 |
| [[c10]] Gateway | — | 실제 환경 기록 없음(테스트만) |
| [[c11]] 호출·관리 화면 | Obsidian에서 Owner 시작·중지([[E_OBSIDIAN_OWNER_LIFECYCLE]]), 사이드바 1.2.0 설치·인라인 입력·실제 의미 검색 결과·밝은/어두운 테마·242px 레이아웃 관찰([[E_SIDEBAR_INLINE_INSTALL]], [[E_SIDEBAR_INLINE_GUI]]), Vault·설정 보존([[E_SIDEBAR_INLINE_PRESERVATION]]), 일회용 Owner 콘솔([[E_CONSOLE_BROWSER]]). 1.3.0 설치와 System One 결정 줄 GUI 관찰([[E_SYSTEM_ONE_CLIENT_INSTALL]], [[E_SYSTEM_ONE_CLIENT_GUI]]). 실제 Owner가 자유 요청 "오늘 서울 날씨 알려줘"를 System One `none_of_these`(0.95)로 Work 없이 거절([[E_SYSTEM_ONE_LIVE]]) | 1.3.0 GUI 관찰은 화면 잠금으로 사이드바 영역의 CDP 렌더뿐. 실제 Owner 콘솔 사용 기록 없음. 1.1.0의 팝업 메뉴 미확인은 역사적 기록이며 현재 UI의 후속 과제가 아님 |

## 목표

`phase=history`는 끝난 단계, `phase=current`는 지금 하는 단계입니다.

{{state:goals}}

## 인수 기록

목표를 이루는 조건(accept)마다의 결과입니다. 연결된 증거는 아래 증거 표에서 찾습니다.

{{state:accepts}}

## 증거

시간순 기록입니다. 읽을 때는 class와 result를 먼저 보세요.

{{state:evidence}}

## 결정

사용자가 확정한 결정입니다. 명세·구성이 왜 이런지 궁금할 때 이 기록이 근거가 됩니다.

{{state:decisions}}

## 검증 범위 (README)

테스트가 확인하는 범위를 README가 직접 밝힌 부분입니다.

{{include:README#검증 범위}}

## 이전 구현의 퇴역

이전 KnowledgeOS 구현(소스, 내부 상태, Runtime, 역할 profile, 합성 Vault 노트)은 복구용 아카이브에 보관한 뒤 삭제했고, 호환이나 재생 의무는 없습니다. 이 위키가 설명하는 것은 퇴역 뒤 명세에 맞춰 새로 만든 구현입니다([[E_RETIREMENT_ARCHIVE]], [[E_RETIREMENT_DELETE]]).

{{include:README#퇴역 자산}}
