# 사용 가이드

이 페이지는 KnowledgeOS를 **실제로 쓰는 순서**를 Obsidian, 브라우저 콘솔, CLI, 자동으로 도는 작업 순으로 안내합니다. 기능이 왜 그렇게 동작하는지는 각 기능 페이지([[c1]]~[[c11]])에 있습니다.

> [!note]
> 이 가이드는 명세와 `README.md`, `PROJECT_STATE.md`에 적힌 것만 근거로 합니다. 실제 환경에서 확인된 것에는 근거(증거 ID)를 달았고, 증거 기록이 없는 것은 "확인 기록 없음"으로 적었습니다. 현재 확인된 범위는 [[status]]에서 볼 수 있습니다.

## 먼저 알아둘 것

- **문서 작업은 Obsidian에서만** 합니다. 브라우저 콘솔과 CLI는 관리·유지보수용입니다.
- KnowledgeOS의 **Owner**(서비스)가 켜져 있어야 요청이 처리되고 예약 작업이 돕니다. Owner는 Mac에서 필요할 때 켜지는 사용자 서비스(LaunchAgent)라서 Obsidian을 종료해도 계속 실행됩니다. 켜고 끄는 것은 사용자가 **Owner 시작·중지**를 직접 누를 때뿐입니다.
- 요청에는 **노트 경로와 마지막으로 본 내용의 해시만** 들어가고, 노트 본문은 보내지 않습니다([[SUR-1]]).
- 노트를 직접 바꾸는 것은 **규격화**(작은 frontmatter 변경)뿐입니다. 정리는 **제안 노트**를, 강화는 **보고서 노트**를 새로 만들 뿐 원본을 바꾸지 않습니다. 제안은 사용자가 **적용**해야 반영됩니다.
- 요청의 응답을 잃어도 **클라이언트가 기록된 결과를 찾아 보여 줍니다.** 같은 요청 ID는 한 번만 처리되고, "기록된 적 없음"이 확인된 경우에만 같은 요청을 다시 보냅니다. 결과 불명이 보여도 재전송은 허락이 아닙니다([[REV-4]]).

## 하고 싶은 일 → 누를 것

| 하고 싶은 일 | Obsidian에서 | 결과가 나타나는 곳 | 기능 |
|---|---|---|---|
| 빠진 `id`·`type`·`created`·`status`·`ai_policy` 채우기 | **규격화 → 미리보기** (명령: 규격화 미리보기) | 인라인 diff를 먼저 보여 주고, 적용을 누르면 노트에 반영 | [[c2]] |
| 노트의 종류·제목·태그·관련 노트·위치 제안받기 | **정리 → 정리 요청** | `01_AI_Review/Proposals/`의 제안 노트 | [[c1]] |
| 아이디어를 발전시키거나, Vault에 묻거나, 프로젝트를 돌아보기 | **강화** → 발전 / 묻기 / 돌아보기 → 요청 버튼 | `01_AI_Review/Reports/`의 보고서 노트 | [[c4]] |
| 이 노트와 관련된 노트 보기 | **찾기 → 관련 노트 → 관련 노트 찾기** | 인라인 결과 링크 → 해당 줄로 이동 | [[c3]] |
| 의미로 검색하기 | **찾기 → 의미 검색** → 질문 입력 → **검색** | 인라인 결과 링크 → 해당 줄로 이동 | [[c3]] |
| 제안을 반영하거나 버리기 | 제안 노트에서 **적용** / **거절** | 대상 노트 변경 / 제안 파일 삭제 | [[c6]] |
| 자동 변경 되돌리기 | **더보기 → 되돌리기** (명령: 자동 변경 되돌리기) | 마지막 자동·수동 규격화 변경 복원 | [[c6]] |
| 자유롭게 말로 요청하기 | **더보기** → 자유 요청 입력 → **요청** | Director가 정리·강화·규격화 미리보기 중 하나로 매핑 | [[c11]] |
| 관리 화면 열기 | 더보기 → 관리 콘솔 | 브라우저 콘솔 | [[c11]] |
| Owner 켜기·끄기 | 전원 버튼 (명령: Owner 시작 / Owner 중지) | 사이드바 상태·결과 표시 | [[c11]] |

## 1. Obsidian

### 1.1 설치와 사이드바 열기

Thin Client 플러그인의 `main.js`·`manifest.json`·`styles.css`를 Vault 설정 폴더의 `plugins/knowledgeos-thin-client/`에 **함께** 두고 플러그인을 다시 불러옵니다. 기존 `data.json`은 그대로 둡니다(README). 플러그인 설정에는 Owner 주소(엔드포인트)와 launchd 라벨(기본 `com.agentfabric.knowledgeos.owner`)만 있습니다.

그다음 리본의 KnowledgeOS 아이콘을 누르거나 명령 팔레트에서 **KnowledgeOS: 사이드바 열기**를 실행하면 오른쪽에 탭 하나가 열립니다. 이미 열려 있으면 새 탭을 만들지 않고 그 탭을 다시 보여 줍니다.

사이드바 구성:

- 맨 위에 **마지막으로 활성이었던 Markdown 노트의 이름**이 표시됩니다. 이 노트가 요청의 대상입니다.
- 이동 단추 네 개: **규격화**, **정리**, **강화**, **찾기**. 누르면 아래 인라인 기능 영역만 바뀌며, 이동만으로 요청을 보내지 않습니다. 강화의 발전 / 묻기 / 돌아보기 선택과 찾기의 관련 노트 / 의미 검색 선택도 이 영역 안에 있습니다.
- **적용**·**거절**은 `type: proposal` 노트가 대상일 때만 나타납니다.
- **더보기**: 인라인 자유 요청 입력, 되돌리기, 관리 콘솔.
- **전원 버튼**: 지금 관찰된 연결 상태에 따라 Owner 시작 또는 중지를 호출합니다. 사이드바를 여는 것만으로 Owner가 켜지거나 꺼지지 않습니다.

대상 노트의 동작:

- 사이드바로 초점을 옮겨도 표시된 노트가 대상으로 유지됩니다. 다른 파일을 열면 대상이 바뀌고, 대상이 지워지면 비워집니다([[SUR-8]]).
- Markdown 대상이 없으면 노트가 필요한 단추는 비활성입니다. **묻기**, **의미 검색**, **자유 요청**은 노트 없이도 쓸 수 있습니다.
- 사이드바는 작업 목록·설정·관리 패널을 늘리지 않습니다. 연결 상태와 진행 중 개수를 표시하고, 사이드바 요청의 진행·오류·결과 링크는 **사이드바 안에** 보여 줍니다. 입력·검색 결과·규격화 diff와 적용/취소도 인라인입니다. 명령 팔레트·문맥 메뉴에서는 기존 입력/미리보기 창과 결과 알림을 사용하며, 오류 알림은 닫을 때까지 남습니다.

> [!status]
> 실제 Obsidian에 1.2.0을 설치하고, 네 인라인 기능 영역과 질문·날짜 입력, 실제 의미 검색 결과 7개, 밝은·어두운 테마와 242px 레이아웃에서 팝업 메뉴·모달 없이 동작함을 확인했습니다([[status#E_SIDEBAR_INLINE_INSTALL|E_SIDEBAR_INLINE_INSTALL]], [[status#E_SIDEBAR_INLINE_GUI|E_SIDEBAR_INLINE_GUI]]). Vault Markdown 20개와 플러그인 설정 상태도 보존했습니다([[status#E_SIDEBAR_INLINE_PRESERVATION|E_SIDEBAR_INLINE_PRESERVATION]]). 앞선 1.1.0의 팝업 메뉴 미확인([[status#E_SIDEBAR_NATIVE_MENUS|E_SIDEBAR_NATIVE_MENUS]], blocked)은 역사적 기록이며, 현재 UI의 후속 과제가 아닙니다.

### 1.2 명령 팔레트·메뉴

사이드바 말고도 같은 명령을 명령 팔레트, 파일·편집기 메뉴, Note Toolbar에서 쓸 수 있습니다. 명령은 다음과 같습니다([[c11]]).

| 명령 | 대상 | 기능 |
|---|---|---|
| 규격화 미리보기 | 노트·선택·폴더 | [[c2]] 수동 경로 |
| 정리 요청 | 노트·선택·폴더 | [[c1]] |
| 강화: 발전 / 묻기 / 돌아보기 | 노트, 질문 입력, 날짜 범위 | [[c4]] |
| 자유 요청 | 자유 문장 | System One 사전 거름 뒤 Director가 분류 |
| 관련 노트 · 의미 검색 | 노트, 질문 입력 | [[c3]] |
| 적용 · 거절 | 제안 노트 | [[c6]] |
| 자동 변경 되돌리기 | 노트 | [[c6]] |
| 관리 콘솔 열기 | 어디서나 | 브라우저 콘솔 |
| Owner 시작 · 중지 | 어디서나(데스크톱 전용) | Owner 수명 |

입력이 필요한 명령(질문, 날짜 범위, 미리보기 확인)에서만 작은 입력창이 뜹니다.

### 1.3 규격화하기 ([[c2]])

1. 규격화할 노트를 열고 사이드바의 **규격화 → 미리보기**를 누릅니다. 여러 파일 선택이나 폴더는 파일 메뉴의 **규격화 미리보기**를 사용합니다.
2. KnowledgeOS가 **바뀔 diff를 먼저 보여 줍니다.** 사이드바에서는 인라인으로, 명령 팔레트·문맥 메뉴에서는 미리보기 창으로 표시하며, 아직 Vault 노트를 바꾸지 않습니다.
3. **적용**을 누르면 반영하고, **취소**하면 노트를 바꾸지 않으며 변경 기록도 남지 않습니다([[STD-8]]). 사이드바의 기능이나 대상 노트를 바꿔도 확정 전 미리보기는 취소되며, 늦게 도착한 응답이 자동 적용되지는 않습니다([[SUR-9]]).

알아둘 점:

- **빠졌거나 잘못된 값만** 채웁니다. 본문과 다른 속성, 파일 위치는 바꾸지 않습니다. 이미 규격에 맞는 노트는 아무 일도 일어나지 않습니다([[STD-5]]).
- 폴더로 `type`을 유추합니다. 예: `40_Knowledge/Ideas/`의 frontmatter 없는 노트는 `id`(새 UUIDv4)·`type: idea`·`created`·`status: seed`·`ai_policy: ask`가 추가되고 본문은 한 바이트도 바뀌지 않습니다([[STD-1]]). 폴더로 정할 수 없는 `type`(예: Vault 맨 위의 노트)이나 올바르지 않은 `status`·`confidence`, 빠졌거나 잘못된 `capture_kind`는 System One이 선택지 안에서 고르고, 미리보기에는 고른 값과 확신도가 diff 위에 나옵니다([[STD-9]]). 자동(매시간) 경로는 `remote_ok` 노트의 값을 확신도 0.7 이상일 때만 적용합니다([[STD-10]]). System One이 꺼져 있으면 그 필드는 그대로 두고 다음 실행에서 다시 묻습니다([[STD-11]]). `ai_policy`·`sensitivity`, 사용자가 쓴 `daily` 같은 기존 종류, 제목·태그 같은 자유 값은 묻지 않으며 정리([[c1]])의 몫입니다.
- 본문 끝에 잘못 들어간 `tags: [a, b]` 같은 줄은 frontmatter의 `tags`로 합쳐지고(중복 없이) 그 줄만 지워집니다([[STD-2]]).
- 미리보기 뒤 확인하기 전에 노트가 바뀌었다면 **쓰지 않고** 바뀌었다고 알려 줍니다([[STD-3]]).
- 방금 편집한 노트도 수동으로는 가능합니다. 자동(매시간) 경로만 최근 10분 안에 편집한 노트를 건너뜁니다([[STD-4]]).
- 새 노트를 처음부터 규격에 맞게 만들려면 QuickAdd·Templater 템플릿(`99_System/Templates/`)을 씁니다. 이 경우 KnowledgeOS 호출이 필요 없습니다([[STD-7]]).
- 많은 노트는 CLI의 `standardize --dry-run` / `--apply`가 편합니다(아래 3절).
- 잘못 바뀌었다면 **자동 변경 되돌리기**(1.6절).

### 1.4 정리 요청 ([[c1]])

1. 노트를 열고 사이드바의 **정리 → 정리 요청**을 누릅니다. 여러 파일 선택이나 폴더는 파일 메뉴의 **정리 요청**을 사용합니다(한 번에 최대 10개).
2. Owner가 곧바로 요청 ID로 응답하고, 정리 팀이 작업합니다. 사이드바 요청의 진행·결과 링크는 사이드바 안에 표시됩니다. 명령 팔레트·문맥 메뉴 요청은 결과 알림을 사용합니다.
3. `01_AI_Review/Proposals/`에 **대상마다 제안 노트 하나**가 생깁니다. 원래 노트는 바뀌지 않습니다([[ORG-1]]).

제안에는 종류와 상태(한 묶음), 제목, 태그(기존 어휘에서), 링크, 폴더 이동 같은 변경이 **체크리스트**로 들어 있습니다. 포착(capture)이면 종류가 있는 노트로 바꾸거나, 기존 노트에 `related` 링크만 붙이는 제안이 될 수 있습니다.

정리가 거절되는 경우: `ai_policy: deny` 노트, 충돌 표시가 있는 노트, ID가 중복된 노트. 이미 열린(또는 낡은) 제안이 있는 대상에는 두 번째 제안을 만들지 않습니다.

매일 06:00에는 정리가 자동으로 돌지만, 이때 모델은 `remote_ok` 노트만 볼 수 있습니다([[ORG-3]]).

### 1.5 제안 검토: 적용·거절 ([[c6]])

1. `01_AI_Review/Proposals/`의 제안 노트를 엽니다(검토 대기 목록은 Bases 보기에서 볼 수 있습니다). 변경 체크리스트와 diff가 보입니다.
2. 원하지 않는 항목의 체크를 해제합니다. 해제한 변경은 적용되지 않고, 체크한 것만 반영됩니다([[ORG-4]]).
3. **적용** 또는 **거절**을 누릅니다. 위치는 사이드바(제안 노트일 때만 보임), 명령 팔레트, 그리고 Note Toolbar의 제안 전용 도구 모음입니다.

결과:

- **적용**: Owner가 결정을 먼저 기록하고, 대상 노트가 제안을 만들 때와 같을 때만 체크한 변경을 적용한 뒤 제안 파일을 지웁니다([[REV-1]]).
- 그사이 **대상이 바뀌었으면** 노트는 그대로 두고, 제안은 낡음(stale)으로 표시되며 결정은 "수락했지만 적용 안 됨"으로 기록됩니다. 알림이 다시 요청하도록 안내합니다([[REV-2]]). 낡은 제안은 7일 뒤 지워집니다.
- **거절**: 결정을 기록하고 제안 파일을 지웁니다. 기록은 Git에 남습니다.
- 제안을 직접 고쳤다면(체크리스트 포함) KnowledgeOS는 그 제안을 덮어쓰거나 중복 생성하지 않습니다.
- 클라이언트가 시간 초과된 뒤 다시 연결되면 그 요청 ID의 기록된 결과를 보여 줍니다. 두 번 적용되지 않습니다([[REV-4]]).

> [!status]
> 실제 Vault에서 사용자가 스모크 제안을 적용해 체크한 변경 7개를 반영하고 제안이 삭제된 것이 확인되었습니다([[status#E_USER_PROPOSAL_APPLY|E_USER_PROPOSAL_APPLY]], device pass). 제안 전용 Note Toolbar(적용·거절)도 실제 Vault 설정에 만들어졌습니다([[status#E_VAULT_CONFIG_CLEANUP|E_VAULT_CONFIG_CLEANUP]]).

**자동 변경 되돌리기**: 노트에서 실행하면 마지막 자동(또는 수동) 규격화 이전 내용을 복원합니다. 단, 노트가 그 변경의 결과와 **아직 같을 때만** 합니다. 그사이 고쳤다면 아무것도 쓰지 않고 Git 이력으로 되돌리라고 안내합니다([[REV-3]]).

### 1.6 강화하기 ([[c4]])

| 모드 | 언제 | 입력 | 보고서에 담기는 것 |
|---|---|---|---|
| **발전**(develop) | 아이디어·지식·질문 노트를 발전시키고 싶을 때 | 현재 노트 | 내 말로 다시 쓴 핵심(인용), 이미 쓴 관련 내용, 연결과 긴장, 빈틈과 열린 질문, 다음 걸음 |
| **묻기**(ask) | Vault에 질문하고 싶을 때 | 질문 입력 | 답, 뒷받침하는 노트, 충돌하는 노트, Vault가 다루지 않는 것 |
| **돌아보기**(review) | 프로젝트를 돌아볼 때 | 프로젝트 노트 + 기간(날짜) | 움직인 것, 멈춘 것, 열린 결정, 다음 행동 |

1. 사이드바의 **강화**에서 모드를 고릅니다. 발전은 **발전 요청**을 누르고, 묻기와 돌아보기는 인라인으로 질문·날짜 범위를 입력한 뒤 **요청**을 누릅니다. 모드 선택만으로는 작업을 시작하지 않습니다.
2. 끝나면 `01_AI_Review/Reports/`에 보고서가 생기고 사이드바에 결과 링크가 표시됩니다. 원래 노트는 바뀌지 않습니다([[ENH-1]]). 명령 팔레트·문맥 메뉴에서는 기존 입력창과 결과 알림을 사용합니다.

보고서 읽는 법:

- 모든 주장에 **인용됨 / 추론 / 근거 없음** 표시가 붙습니다. 인용은 위키링크와 줄 위치, 짧은 내용 해시로 적힙니다.
- 보고서의 `ai_policy`는 읽은 모든 노트 중 가장 엄격한 값을 따릅니다([[ENH-4]]).
- 작업 중 노트를 고쳤다면 보고서는 **접수 때의 내용**을 인용하고 바뀌었다고 밝힙니다([[ENH-3]]).
- 보고서는 만들어진 뒤 **사용자의 문서**입니다. 고치고 옮기고 지워도 됩니다. `type`을 바꾸면 내 지식으로 편입되며, 그전까지는 KnowledgeOS의 역할이 근거로 쓰지 않습니다([[ENH-5]]).
- 질문과 관련된 읽을 만한 노트가 없으면 보고서 없이 "다루지 않음"으로 끝납니다.

### 1.7 찾기 ([[c3]])

- **키워드 검색**은 Obsidian 기본 검색이 합니다(KnowledgeOS가 필요 없습니다).
- **관련 노트**: 링크, 백링크, 공통 태그, 의미 이웃을 **이유 표시와 함께** 보여 줍니다. 항목을 고르면 해당 줄에서 노트가 열립니다.
- **의미 검색**: 질문을 입력하면 유사도 순으로 결과(제목, 경로, 종류, 상태, 발췌, 줄 위치, 점수)를 보여 줍니다. 색인이 준비되지 않았으면 **"unavailable"** 이라고만 알리고 결과를 내지 않습니다. 키워드 결과로 몰래 대체하지 않습니다([[SRC-3]]).
- 사이드바에서는 **찾기** 아래에서 검색 종류를 고른 뒤 **관련 노트 찾기** 또는 질문 입력 후 **검색**을 누릅니다. 결과는 사이드바 안의 링크로 표시합니다. 명령 팔레트·문맥 메뉴에서는 기존 선택 목록을 사용합니다.
- `deny` 노트와 제안은 어떤 검색에도 나오지 않습니다([[SRC-2]]). 보고서는 사람에게는 보이고 역할에게는 보이지 않습니다([[ENH-5]]).

### 1.8 자유 요청 ([[c11]])

**더보기**의 인라인 **자유 요청** 입력란에 문장을 적고 **요청**을 누르면, System One이 설정되어 있을 때 먼저 요청 문장**만** System One에게 보내 KnowledgeOS 명령과 무관한지 가립니다. 무관하다고 충분히 확신하면 Work 없이 바로 거절하고, 그렇지 않으면 `kos-director` 모델이 읽고 표준 요청(규격화 미리보기·정리·강화) 중 하나로 매핑합니다([[SUR-10]]).

- 모델은 내가 이름 붙인 노트의 **경로 참조**(정책으로 걸러진)만 보고 본문은 보지 못합니다. 경로와 digest는 Owner가 채웁니다.
- 작업·검토·예약 관리는 자유 요청으로 할 수 없습니다.
- 예: "오늘 서울 날씨 알려줘"는 System One이 `none_of_these`로 거절해 Director도 Work도 만들어지지 않았습니다(실제 Owner 관찰, [[status#E_SYSTEM_ONE_LIVE|E_SYSTEM_ONE_LIVE]]).
- 매핑할 수 없으면 작업을 만들지 않고 **이유를 알려 줍니다**([[SUR-3]]).

### 1.9 Owner 시작·중지

- **Owner 시작**: launchd 라벨을 kickstart하고 `/api/health`가 준비될 때까지 기다린 뒤 "시작됨"이라고 알립니다.
- **Owner 중지**: SIGTERM을 보내고 health가 더는 응답하지 않을 때 "중지됨"이라고 알립니다. 아니면 "아직 실행 중" 또는 "확인 불가"라고 알립니다.
- 둘 다 **눌렀을 때만** 동작하고, health 조회 외에는 아무것도 보내지 않습니다([[SUR-6]]). 데스크톱 전용입니다.

> [!status]
> 실제 Obsidian에서 Owner 중지(launchd 종료 코드 0)와 시작(health 정상)이 관찰되었습니다([[status#E_OBSIDIAN_OWNER_LIFECYCLE|E_OBSIDIAN_OWNER_LIFECYCLE]], device pass).

### 1.10 함께 쓰는 플러그인

{{include:USER#Obsidian 안에서 쓰는 방법}}

## 2. 브라우저 관리 콘솔

콘솔은 KnowledgeOS를 **살펴보고 관리**하는 로컬 전용 페이지입니다. Owner(ExOps)가 loopback에서 직접 서빙하며, 문서 작업은 하지 않습니다. Thin Client의 **관리 콘솔 열기**(사이드바 더보기)로 엽니다.

화면의 탭(현재 `frontend/index.html` 기준):

| 탭 | 보여 주는 것 |
|---|---|
| 작업 | Work 목록과 상세: WorkSpec, Graph 구간, 역할 호출(profile·모델 경로·skill digest), 평가, RemainingWork, TeamReview |
| 예약 | 예약 작업 실행 기록, 일시정지·재개 |
| 결과 불명 | 결과를 관찰하지 못한 항목, 확인 기록 |
| 주의 | Bases로 표현할 수 없는 주의 항목(중복 ID, 충돌 표시, 깨진 YAML, 느슨한 `confidential`, 낡은 제안, 실패·막힌 작업 등) |
| 알림 | Discord 알림 기록 |
| Experience | Experience outbox |
| 역할 | 역할·profile 지도와 학습 기록 |
| 요청 | 관리 요청 결과 |

관리 동작은 다른 클라이언트와 **같은 접수 경로**를 거칩니다.

- **작업 취소**: 관찰한 결과를 "받음 → 적용됨" 또는 확인하지 못했다면 "불명"으로 보여 줍니다([[SUR-4]]).
- **예약 작업 일시정지·재개**.
- **결과 불명 확인 기록**: 확인 후 "적용됨/적용 안 됨"을 기록합니다. 과거 Work·영수증·불명 증거는 고치지 않습니다.
- **TeamReview 제안을 "검토함"으로 표시**.

화면을 열어 보는 것(GET)은 작업을 시작하거나 알림을 보내거나 결과 불명을 정리하지 않습니다.

> [!status]
> 일회용 Owner(합성 Vault·State·Runtime, 로컬 Temporal 개발 서버)를 띄워 브라우저에서 상태와 `job.pause`·`job.resume` 영수증을 관찰했습니다([[status#E_CONSOLE_BROWSER|E_CONSOLE_BROWSER]], runtime pass). **실제 Owner의 콘솔을 사용해 본 기록은 PROJECT_STATE에 없습니다.**

## 3. CLI

CLI는 유지보수와 설치용입니다. 변경을 일으키는 명령도 다른 화면과 **같은 Owner 접수와 효과 규칙**을 거칩니다. 설치된 `knowledgeos` 또는 `python -m knowledgeos.cli`로 실행하며, Owner에 연결하는 명령은 `--config owner.json` 또는 `--endpoint URL`로 Owner를 가리킵니다(CLI 도움말 기준).

| 명령 | 하는 일 |
|---|---|
| `knowledgeos serve --config owner.json` | Owner와 ExOps 백엔드, 관리 콘솔을 실행. SIGTERM에 깨끗이 종료([[SUR-6]]) |
| `knowledgeos health` | Owner 상태 |
| `knowledgeos vault-check` | 깨진 YAML, 충돌 표시, 중복 ID 점검 |
| `knowledgeos index rebuild`, `index verify`, `index embed` | 키워드 색인 재구축, 색인·의미 검색 준비 상태 확인, 의미 벡터 만들기(남은 노트가 없을 때까지 반복) |
| `knowledgeos standardize PATH --dry-run` | 대량 규격화 **계획만** 출력, 아무것도 쓰지 않음([[SUR-5]]). `PATH`는 Vault 안의 노트·폴더(전체는 `.`) |
| `knowledgeos standardize PATH --apply` | 같은 계획을 실제로 적용 |
| `knowledgeos job run standardize` (또는 `embed`·`curate`·`digest`) | 선언된 작업을 지금 실행하고, 접수된 요청의 실행 영수증까지 따라가 결과를 보고 |
| `knowledgeos reconcile UNKNOWN_ID --resolution applied --yes` (또는 `not_applied`) | 결과 불명 항목에 사람의 관찰을 기록. 효과를 다시 실행하지 않음 |
| `knowledgeos install profiles --config owner.json [--dry-run]` | Hermes 역할 profile과 발송 전용 `kos-ops` profile 설치. `settings.hermes.binary` 등 Hermes 호스트 설정이 필요하며, `--dry-run`은 Runtime에 배포물을 생성하되 Hermes 설치 명령은 실행하지 않음 |
| `knowledgeos install service --config owner.json [--dry-run]` | Owner LaunchAgent 등록. `--dry-run`은 plist와 `launchctl` 인자만 출력 |
| `knowledgeos work export WORK_ID` | Work 기록 하나를 JSON으로 출력(Blueprint §3.3의 "Work export", CLI 도움말 기준) |

설정 파일(`owner.json`) 예시. Vault·State·Runtime은 따로 지정하며 서로 겹치거나 포함할 수 없습니다.

```json
{
  "vault": "/path/to/KnowledgeHub",
  "state": "/path/to/States/Operations/knowledgeos",
  "runtime": "/path/to/Runtimes/knowledgeos",
  "settings": {"runtime": {"address": "127.0.0.1:7233", "namespace": "default"}}
}
```

개발·검증용 `make` 명령은 모두 프로젝트 컨테이너에서 돕니다.

| 명령 | 용도 |
|---|---|
| `make image-build` / `make image-id` | 개발 이미지를 만들고 ID를 확인 |
| `make trace-check` | 추적표가 인수 ID와 정확히 일치하는지 검사 |
| `make test TESTS="<노드 ID들>"` | 지정한 테스트만 실행(네트워크 차단, 읽기 전용 소스) |
| `make state-check` | `PROJECT_STATE.md` 검증 |
| `make wiki` / `make wiki-check` | 이 위키를 생성 / 검사([[philosophy]]) |

### Owner를 서비스로 등록하기

```sh
knowledgeos install service --config owner.json --dry-run   # 먼저 plist와 launchctl 인자만 확인
knowledgeos install service --config owner.json             # plist 작성 후 적재(실행은 안 함)
```

- plist는 `~/Library/LaunchAgents/com.agentfabric.knowledgeos.owner.plist`(라벨·경로는 `--label`, `--plist`로 변경)에 쓰이고, `RunAtLoad`와 `KeepAlive`가 꺼져 있으며 로그는 Runtime의 `logs/`에 남습니다. 이미 적재된 같은 라벨은 새 정의로 교체됩니다.
- 등록만으로는 Owner가 **켜지지 않습니다.** Obsidian의 **Owner 시작**으로 켭니다([[SUR-7]]).
- 이 호스트의 사실(PROJECT_STATE): 공유 Temporal 서버는 **호스트 loopback 127.0.0.1:7233에만** 열려 있고 Owner가 거기에 연결합니다([[status#E_TEMPORAL_RUNNING|E_TEMPORAL_RUNNING]], [[status#D_TEMPORAL_LOOPBACK|D_TEMPORAL_LOOPBACK]]). Owner는 사용자가 Thin Client에서 켜고 끄는 온디맨드 LaunchAgent입니다([[status#D_OWNER_LAUNCHAGENT|D_OWNER_LAUNCHAGENT]]).

## 4. 자동으로 일어나는 일 (예약 작업)

Owner가 **켜져 있는 동안** Owner 안의 스케줄러가 아래 작업을 시작합니다. 호스트 스케줄러도, Hermes cron도, 분마다 도는 프로세스도 없습니다(Hermes의 `kos-ops` cron 세 개와 생성 스크립트는 제거되었습니다: [[status#E_HERMES_CRON_REMOVED|E_HERMES_CRON_REMOVED]]).

| 작업 | 기본 주기(Asia/Seoul) | 하는 일 |
|---|---|---|
| `standardize` | 매시 정각 | 바뀐 노트를 결정적으로 규격화(최근 10분 안 편집은 건너뜀) |
| `embed` | 매시 30분 | 새로 생기거나 바뀐 노트의 의미 검색 벡터를 만듦 |
| `curate` | 매일 06:00 | 새 포착과 종류 없는 `remote_ok` 노트에 정리 제안(한 번에 최대 10개) |
| `digest` | 매일 08:00 | 문서 관리 계산과 Discord 요약 |

{{svg:scheduler-timeline}}

- **Owner가 꺼져 있으면 아무것도 실행되지 않습니다.** 잃는 것은 없습니다. 다시 켜고 **시작 지연**(`startup_delay_minutes`, 기본 3분)이 지나면 놓친 작업을 **작업당 한 번만** 실행하고, 그 뒤로는 정해진 시각에 실행합니다([[JOB-4]]).
- 같은 시각(슬롯)은 재시작해도 두 번 실행되지 않습니다([[JOB-5]]).
- 변경이 없으면 아무것도 쓰지 않고 요약 외의 알림도 보내지 않습니다([[JOB-1]]).
- 모델 경로를 쓸 수 없으면 `curate`는 **눈에 보이게 실패**하고 제안을 만들지 않으며 자동 재시도하지 않습니다([[JOB-3]]).
- **일시정지·재개**는 콘솔의 예약 탭에서 합니다. 멈춘 동안의 슬롯은 그냥 지나가고, 재개해도 몰아서 실행하지 않습니다([[JOB-6]]). 멈춘 작업을 수동으로 실행하는 것도 거절됩니다.
- **수동 실행**: `knowledgeos job run <작업>`. `scheduler_enabled`를 끄면 자동 실행은 없고 수동 실행만 됩니다.
- 설정은 `settings.C7`의 `scheduler_enabled`(기본 true), `startup_delay_minutes`(기본 3), `tick_seconds`(기본 30)입니다([[JOB-7]]).
- 작업 실패나 슬롯이 두 번 지나도록 실행되지 않은 경우는 **주의 항목**이 됩니다.

> [!status]
> 임시 `index.embed` 작업(20:40 슬롯)이 시작 지연 3분 뒤 20:40:41에 한 번 실행된 것이 확인되었습니다(요청 `schedule-schedulercheck-20261119T2040`). 이후 임시 작업은 제거하고 Owner를 다시 시작했습니다([[status#E_REAL_SCHEDULER_SLOT|E_REAL_SCHEDULER_SLOT]], runtime pass). **선언된 실제 작업의 첫 자연 실행(매시 정각의 규격화, 30분의 임베딩)은 아직 관찰하지 못했고 다음 세션의 인계 사항입니다**([[roadmap]]).

{{include:USER#자동으로 일어나는 일}}

## 5. 의미 검색과 임베딩

의미 검색은 **로컬 Ollama**가 만드는 임베딩과 State의 벡터 색인(`<State>/semantic/`)을 씁니다. Owner가 loopback HTTP로 Ollama를 직접 부르며, 게이트웨이나 원격 모델은 쓰지 않습니다. loopback이 아닌 주소는 설정 오류입니다.

{{svg:semantic-pipeline}}

기본 설정(`settings.C3.semantic`):

```json
"settings": {"C3": {"semantic": {"ollama_url": "http://127.0.0.1:11434", "model": "qwen3-embedding:8b-q4_K_M"}}}
```

그 밖에 `batch_size`, `timeout_seconds`, `max_notes`, `related_neighbors`(기본 5), `related_min_score`(기본 0.5), `related_min_probability`(기본 0.5, System One의 이웃 거르기 기준)를 조정할 수 있습니다. `ollama_url`이나 `model`을 비우면 의미 검색을 쓰지 않습니다.

사용 순서:

1. Ollama가 설정한 임베딩 모델을 제공할 수 있어야 합니다.
2. 처음에는 `knowledgeos index embed`를 돌려 남은 노트가 없을 때까지 임베딩합니다. 이후에는 예약 작업 `embed`(매시 30분)가 새 노트·바뀐 노트만 이어서 처리합니다. 두 길 모두 같은 `index.embed` 요청입니다.
3. 준비됐는지는 `knowledgeos index verify`(대상·임베딩·오래됨·누락·더는 대상 아님의 개수, 경로는 알려 주지 않음)와 `GET /api/health`의 `search_semantic`으로 확인합니다.
4. Obsidian의 **찾기 → 의미 검색**을 씁니다.

이럴 때는 이렇게 됩니다.

| 상황 | 동작 |
|---|---|
| 새로 쓰거나 고친 노트 | 다음 임베딩 때까지 의미 검색·관련 노트에서 빠집니다([[SRC-5]]) |
| 모델을 바꿈 | 첫 임베딩 때 다른 모델의 벡터를 모두 버리고 새로 만듭니다. 그때까지 의미 검색은 `unavailable`입니다 |
| Ollama가 꺼져 있음 | 작업이 눈에 보이게 실패하고(재시도 없음) 기존 벡터는 유지되어 이웃 계산에는 계속 쓰입니다. 의미 검색 질문은 임베딩할 수 없어 `unavailable`입니다([[SRC-8]]) |
| `deny`·제안·`99_System/`·충돌 표시 노트 | 임베딩되지 않고, 이미 있던 벡터도 다음 실행에서 지워집니다([[SRC-6]]) |
| `local_only`·`confidential`·보고서 노트 | 로컬 처리이므로 임베딩됩니다 |

> [!status]
> 실제 호스트에서 대상 노트 7개를 조각 21개로 임베딩하는 데 27초 걸렸고(`qwen3-embedding` 8b q4_K_M), Obsidian Thin Client에서 줄 위치가 달린 의미 검색 결과가 나왔습니다([[status#E_REAL_SEMANTIC|E_REAL_SEMANTIC]], external_service pass). `related_min_score`(기본 0.5)는 **시작값**이고 실제 노트로 조정하는 일이 남아 있습니다([[roadmap]]).

### System One(빠른 선택형 판단) 설정

System One은 Owner가 OpenAI Decisions API(`gpt-6-luna`)에 직접 묻는 서비스입니다([[glossary]]). 규격화 규칙 9([[c2]]), 정리 힌트([[c1]]), 관련 노트 거르기([[c3]]), 문서 관리 상태 제안([[c5]]), 자유 요청 사전 거름([[c11]])이 씁니다. 쓰지도 글을 짓지도 않고 `ai_policy`·`sensitivity`는 정하지 않습니다. Hermes 모델 역할과는 별개이며 profile을 설치할 필요가 없습니다.

- **설정**(`settings.system_one`): `api_key_file`(Git과 Vault 밖의 0600 비공개 파일의 절대 경로, 없으면 미설정), `url`(기본 OpenAI Decisions 엔드포인트, 평문 `http`는 loopback만), `model`(기본 `gpt-6-luna`), `timeout_seconds`(기본 20), `min_confidence`(기본 0.7), `max_input_chars`(기본 8000, 노트마다 보내는 본문 길이).
- **확인**: `GET /api/health`의 `system_one`이 `ready`, `unavailable`(쓸 수 없는 키 파일), `unconfigured`로 알려 줍니다. 이 확인은 요청을 보내지 않습니다.
- **보내는 노트**: 직접 요청한 작업과 검색은 `remote_ok`·`ask` 노트, 예약·Gateway 작업은 `remote_ok` 노트만 보냅니다. `local_only`·`confidential`·`deny`는 보내지 않습니다.
- **실패해도 괜찮습니다**: 설정이 없거나 실패하면 모든 기능이 System One 없이 하던 대로 동작하고, 한 요청 안에서 다시 시도하지 않습니다.
- **기록**: 호출마다 질문 이름·입력 해시·모델·토큰 수만 State `system_one`에 남고 내용은 남지 않습니다. 검색 같은 읽기는 State를 쓰지 않고 로그만 남깁니다.

> [!status]
> 실제 Owner를 `system_one`을 설정해 다시 시작하자 health가 `ready`를 보고했습니다([[status#E_SYSTEM_ONE_LIVE|E_SYSTEM_ONE_LIVE]], external_service pass). Thin Client 1.3.0은 활성 플러그인 폴더에 설치되어 사이드바 규격화 미리보기에 "System One: 항목 → 값 (NN%)" 줄이 나오는 것을 관찰했습니다([[status#E_SYSTEM_ONE_CLIENT_GUI|E_SYSTEM_ONE_CLIENT_GUI]], device pass, 화면 잠금으로 사이드바 영역의 CDP 렌더). 확신도 기준 `min_confidence`·`related_min_probability`는 **시작값**입니다([[roadmap]]).

## 6. Discord 요약과 알림

Discord는 **알림만** 합니다. 메시지·반응·답장·링크 방문으로는 아무것도 승인되지 않고 명령도 받지 않습니다.

- **요약**(매일 08:00 예약): 새 자동 변경 개수와 제목, 검토 대기 중인 제안·보고서, 낡은 제안, 결정이 필요한 주의 항목, 다시 볼 아이디어·열린 질문. 이전 요약 이후의 새 자동 변경만 담고, 아직 남아 있는 것은 다음 요약에 다시 나옵니다.
- **즉시 알림**: 충돌, 결과 불명, 작업 실패는 항목 ID로 중복 없이 바로 보냅니다. 같은 충돌이 이틀째 해결되지 않아도 알림은 한 번이고, 요약에는 둘 다 나옵니다([[NTF-2]]).
- **내용**: 제목과 개수, 그리고 `obsidian://` 링크(노트)와 콘솔 링크(관리 항목)뿐입니다. **`remote_ok`·`ask` 노트의 제목과 링크만** 쓸 수 있고, `deny`·`local_only`·`confidential` 노트는 제목도 식별 정보도 나오지 않습니다([[NTF-1]]).
- **발송**: Owner가 알림 의도를 먼저 기록하고 모델 없는 `kos-ops` profile로 보냅니다. 결과를 알 수 없는 발송은 **다시 보내지 않습니다**([[NTF-3]]).
- 모바일의 Obsidian에서도 알림의 `obsidian://` 링크가 열립니다.

설정: KnowledgeOS 전용 봇을 서버에 초대하고 설정된 채널에서 **View Channel**과 **Send Messages**를 허용해야 합니다. 처음에는 봇이 서버에 들어오기 전이라 Discord API가 403 Missing Access(코드 50001)를 돌려주었고 알림이 `not_delivered`로 기록되었습니다([[status#E_DISCORD_MISSING_ACCESS|E_DISCORD_MISSING_ACCESS]]). 이후 봇이 서버에 들어가 `knowledgeos` 채널로 요약 하나가 전달되었습니다([[status#E_DISCORD_DELIVERED|E_DISCORD_DELIVERED]], external_service pass; 막힘 [[roadmap#B_DISCORD_TOKEN|B_DISCORD_TOKEN]]은 해결됨). 즉시 알림(충돌·결과 불명·실패)이 실제 Discord로 나간 기록은 PROJECT_STATE에 **없습니다.**

## 7. 모바일

지금은 모바일용 KnowledgeOS가 없습니다.

- iPhone·iPad에서는 Obsidian으로 쓰고 Working Copy로 GitHub와 동기화합니다.
- Mac이 변경을 받아오면 Mac의 KnowledgeOS가 예약 작업으로 규격화·정리합니다.
- 모바일에서 템플릿으로 노트를 만들거나 요청을 보내는 흐름(M1~M3)은 아직 시작 전입니다. [[roadmap]]을 보세요.

## 문제가 생겼을 때

| 증상 | 원인과 할 일 |
|---|---|
| 요청해도 반응이 없다 / 연결 안 됨 | Owner가 꺼져 있을 수 있습니다. 사이드바의 전원 버튼(또는 Owner 시작)으로 켭니다. 시작은 health가 응답할 때만 "시작됨"으로 알립니다 |
| "의미 검색이 unavailable" | 색인이 준비되지 않았거나(설정 없음, 모델 변경 직후, 벡터 없음) 질문을 임베딩할 수 없습니다. `knowledgeos index verify`로 준비 상태를 보고 `knowledgeos index embed`를 돌립니다. Ollama가 켜져 있는지 확인합니다 |
| 방금 쓴 노트가 의미 검색에 안 나온다 | 정상입니다. 다음 임베딩(매시 30분 또는 `index embed`)까지 빠집니다 |
| 적용했더니 "적용 안 됨, 낡음" | 제안을 만든 뒤 대상 노트가 바뀐 것입니다. 노트는 그대로이고 제안은 낡음으로 남습니다. 다시 정리를 요청합니다 |
| 되돌리기가 거절됨 | 자동 변경 뒤에 노트를 고쳤기 때문입니다. Git 이력으로 되돌립니다 |
| 정리·강화가 "모델을 쓸 수 없다"로 실패 | 해당 역할의 모델 경로·자격 증명이 없는 것입니다. 몰래 다른 모델로 바꾸지 않으므로 profile을 고쳐야 합니다. 예약 `curate`는 재시도하지 않고 다음 슬롯을 기다립니다 |
| 결과 불명 항목이 보인다 | 콘솔의 "결과 불명" 탭이나 `knowledgeos reconcile`로 사람이 확인해 기록합니다. 요청을 다시 보내지 않습니다 |
| 노트가 규격화·정리에서 빠진다 | `deny`, 충돌 표시, 깨진 YAML, 중복 ID, `99_System/`·`01_AI_Review/` 안의 노트는 건너뜁니다. 주의 탭이나 `knowledgeos vault-check`로 확인합니다 |
| 규격화가 방금 쓴 노트를 건너뛴다 | 자동 경로는 최근 10분 안에 편집한 노트를 건너뜁니다. 수동 **규격화**는 바로 됩니다 |

## 원문 참고

### README의 "사용"

{{include:README#사용}}

### 사용자 기능 명세의 "관리: 브라우저와 CLI"

{{include:USER#관리: 브라우저와 CLI}}
