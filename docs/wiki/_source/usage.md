# 사용 가이드

이 페이지는 KnowledgeOS를 **Obsidian에서 실제로 누르고, 클릭 뒤에 어떤 일이 벌어지는지 확인하며, 콘솔·CLI·예약 작업까지 운영하는 순서**를 안내합니다. 각 기능의 상세한 내부 판단과 규칙은 해당 기능 페이지([[c1]]~[[c12]])에서 이어서 읽을 수 있습니다.

한 가지만 먼저 잡으면 헷갈리지 않습니다. **문서 작성·검토는 Obsidian 안에서** 하고, **상태 확인·작업 취소·예약 일시정지·결과 불명 기록은 브라우저 관리 콘솔과 CLI에서** 합니다. Obsidian을 꺼도 Mac의 온디맨드 서비스(LaunchAgent)로 동작하는 **Owner**가 켜져 있어야 요청이 처리되고 예약 작업도 돕니다([[architecture]]).

## 하고 싶은 일 → 누를 것과 뒤에서 벌어지는 일

| 하고 싶은 일 | Obsidian에서 누를 것 | 누른 뒤 일어나는 일 (실행 경로 · 결과) | 자세히 |
|---|---|---|---|
| Owner 켜기·끄기 | 사이드바 하단 **전원 버튼** (명령: `Owner 시작` / `Owner 중지`) | `launchctl` 신호 뒤 `/api/health`가 준비될 때까지 확인. Work·문서 변경 없음 | [[c11]] |
| 빠진 속성 채우기·정돈 | **규격화 → 미리보기** | `StandardizationOfficer` Work가 diff 계산 후 대기(`awaiting_confirmation`). 필요 시 규칙 9만 System One(`gpt-6-luna`) 질의 | [[c2]] |
| 미리보기 반영·취소 | 인라인 미리보기 아래 **적용** / **취소** | `ActionWorkflow`가 대기 Work를 재개해 preimage 확인 후 노트 수정 또는 취소. 다른 탭/노트로 옮기면 자동 취소 | [[c2]], [[c11]] |
| 분류·제목·태그·연결 제안받기 | **정리 → 정리 요청** (폴더·다중 선택은 파일 메뉴) | Curation Team(`kos-curation-manager`, `scout`, `kos-classifier`, `kos-linker`)이 `WorkWorkflow` + 동적 Graph로 `01_AI_Review/Proposals/`에 제안 작성. 원본 불변 | [[c1]] |
| 제안 반영·거절 | 제안 노트에서 **적용** / **거절** (사이드바·Note Toolbar·명령) | Work 없이 Owner가 즉시 처리. 체크한 항목만 원본에 반영하고 제안 파일 삭제 | [[c6]] |
| 자동 규격화 되돌리기 | **더보기 → 되돌리기** (명령: `자동 변경 되돌리기`) | Work 없이 Owner가 즉시 직전 규격화 결과와 일치할 때만 복원. 그사이 고쳤다면 거절 | [[c6]] |
| 아이디어 발전·질문·프로젝트 회고 | **강화** → **발전** / **묻기** / **돌아보기** → 요청 버튼 | Thinking Team(`kos-thinking-manager`, `retriever`, `kos-connector`, `kos-challenger`, `kos-synthesizer`)이 `WorkWorkflow` + 동적 Graph로 `01_AI_Review/Reports/`에 보고서 생성 | [[c4]] |
| 관련 노트·의미로 찾기 | **찾기** → **관련 노트** / **의미 검색** → 검색 버튼 | Work 없이 Owner가 즉시 검색 응답. `의미 검색`은 로컬 Ollama로 질의 임베딩, `관련 노트`는 설정 시 System One 거르기 | [[c3]] |
| 말로 자유롭게 시키기 | **더보기 → 자유 요청** 입력 → **요청** | System One(`gpt-6-luna`)이 명령 밖 요청을 먼저 거른 뒤 `kos-director`(`gpt-6.1-sol`)가 표준 명령 1개로 번역해 파생 요청 실행 | [[c11]] |
| 오늘 데일리·할 일 인계 | `T10_Daily.md` 생성 또는 매일 07:00 자동 실행 | `DailyOfficer` Work가 이전 날 열린 `[ ]`/`[/]`를 오늘 노트로 옮기고 원본만 `[>]`로 표시. `#task`는 Discord 알림 | [[c12]] |
| 작업 이력·예약·불명 관리 | **더보기 → 관리 콘솔** (또는 CLI) | `http://127.0.0.1:8765/` 로컬 콘솔 열기. 작업 취소·예약 일시정지·불명 확인 기록 수행 | [[c11]], [[c7]] |

## 1. Obsidian에서 쓰기

### 1.1 사이드바 설치와 기본 조작

`thin-client/`의 `main.js`, `manifest.json`, `styles.css`를 Vault 설정 폴더 안의 `plugins/knowledgeos-thin-client/`에 함께 두고 플러그인을 다시 불러옵니다(기존 `data.json`이 있다면 그대로 둡니다). 플러그인 설정에는 **Owner 주소**(기본 `http://127.0.0.1:8765`)와 **Owner launchd 라벨**(기본 `com.agentfabric.knowledgeos.owner`)만 있습니다.

리본의 KnowledgeOS 아이콘이나 명령 팔레트 **KnowledgeOS: 사이드바 열기**를 누르면 오른쪽 탭 하나가 열립니다. 다시 열어도 탭은 하나만 재사용됩니다.

- **맨 위 대상 노트**: 마지막으로 열어 둔 Markdown 노트가 표시됩니다. 사이드바 안을 클릭해도 이 대상이 유지되며, 다른 노트로 이동하면 바뀌고, 대상 노트를 삭제하면 비워집니다. 열린 노트가 없을 때는 노트 전용 버튼들이 비활성화되고 **묻기**, **의미 검색**, **자유 요청**만 쓸 수 있습니다([[SUR-8]]).
- **이동 버튼(`규격화 · 정리 · 강화 · 찾기`)**: 누르면 그 아래 **인라인 기능 영역**만 전환될 뿐 아무 요청도 보내지 않습니다. 질문·날짜·검색어·자유 요청 입력창과 결과 링크, 규격화 diff와 적용·취소 버튼도 모두 사이드바 안에서 처리되며 팝업 메뉴나 모달을 띄우지 않습니다. 입력 중이던 글은 메모리에만 남아 다른 기능으로 잠깐 옮겼다가 돌아와도 이어서 쓸 수 있습니다([[SUR-9]]).
- **제안 전용 막대**: 현재 대상 노트의 frontmatter가 `type: proposal`일 때만 기능 영역 아래에 **적용 · 거절** 버튼이 나타납니다.
- **더보기(`…`)와 전원 버튼**: **더보기**는 인라인 자유 요청 입력란·**되돌리기**·**관리 콘솔**을 엽니다. **전원 버튼**은 관찰된 연결 상태(`연결됨` / `연결 끊김`)에 따라 Owner 중지 또는 시작을 부르며, 사이드바를 여는 것만으로는 Owner가 켜지거나 꺼지지 않습니다.
- **명령 팔레트·문맥 메뉴·Note Toolbar**: 여러 파일 선택이나 폴더 단위 작업은 파일 메뉴에서 부릅니다. 명령 팔레트나 문맥 메뉴에서 부르면 작은 입력 창·미리보기 창·결과 목록 모달이 뜨지만, Owner로 보내는 요청과 확인 규칙은 사이드바와 똑같습니다.

> [!note] 함께 쓰는 Obsidian 기본·커뮤니티 플러그인
> 새 노트를 처음부터 규격에 맞게 만들 때는 **QuickAdd**·**Templater**(`99_System/Templates/`)·**Daily Notes**를, 키워드 검색은 **Obsidian 기본 검색**을, 검토 대기·포착함·프로젝트 목록은 **Bases**(`99_System/Bases/`)를 씁니다. 제안 노트의 상·하단 버튼은 **Note Toolbar**가, 할 일·홈 대시보드는 **Tasks**·**Homepage**·**Breadcrumbs**가 맡으며, Git 이력은 **Obsidian Git**으로 봅니다. KnowledgeOS는 `git` 명령을 실행하지 않습니다.

### 1.2 버튼을 누른 뒤 공통으로 일어나는 일

문서 처리 명령을 제출하면 Thin Client는 **첨부 노트의 본문은 빼고** 경로와 현재 파일의 SHA-256 해시를 보냅니다. 폼에 입력한 질문·기간·자유 요청 문장은 함께 전송합니다. Owner가 State에 영수증을 먼저 기록하고 요청 ID를 돌려주면, 사이드바는 `진행 N`을 띄우고 영수증을 주기적으로 조회합니다. 연결이 끊겨도 원래 요청 ID로 결과부터 확인하므로 같은 작업을 섣불리 두 번 보내지 않습니다([[REV-4]]). 기능 탭 이동과 Owner 전원 조작은 이 문서 실행 요청과 다릅니다.

### 1.3 기능별 조작과 클릭 후 흐름

#### 규격화 (`규격화 → 미리보기`)

1. 노트를 열고 **규격화 → 미리보기**를 누릅니다(폴더나 여러 파일은 파일 메뉴의 **규격화 미리보기**).
2. Owner가 정적 `StandardizationOfficer` Work(`WorkWorkflow`)를 돌려 빠진 `id`·`type`·`created`·`status`·`ai_policy`, 본문 끝 잘못 놓인 `tags:` 이동, 그리고 폴더로 알 수 없는 `type`·잘못된 `status`·`confidence`·`capture_kind`(규칙 9, 설정 시 System One 질의)를 계산합니다. 이 단계에서는 파일을 고치지 않고 Work를 확인 대기(`awaiting_confirmation`)로 멈춰 둡니다.
3. 사이드바 안에 바뀔 diff와 System One 선택 결과(예: `System One: status → seed (93%)`)가 표시됩니다. 바뀔 것이 없으면 `"바꿀 내용이 없습니다"`를 알리고 끝납니다.
4. **적용**을 누르면 `ActionWorkflow`가 실행되어 방금 본 preimage 해시와 현재 파일이 여전히 같을 때만 노트를 고칩니다. 미리보기 사이에 노트를 고쳤다면 쓰지 않고 다시 미리보기를 띄우라고 알립니다([[STD-3]]). **취소**를 누르거나 확정 전에 다른 기능·노트로 옮기면 해당 미리보기 ID가 자동으로 취소되고 아무것도 쓰이지 않습니다([[STD-8]], [[SUR-9]]).

- **제외·주의**: 기존 `ai_policy`·`sensitivity`, 이미 유효한 `type`, 제목 같은 자유 값은 바꾸지 않으며 데일리 노트에는 `status`를 넣지 않습니다. 빠진 `ai_policy`의 기본값 채우기와 잘못 놓인 `tags:` 이동은 예외적으로 정해진 규칙입니다. `deny`·충돌·깨진 YAML·중복 ID 노트는 건너뜁니다. 자동 규격화는 최근 10분 안에 편집한 노트를 기다리지만([[STD-4]]), 수동 미리보기는 방금 저장한 노트도 처리합니다.
- **되돌리기·대량 실행·템플릿**: 잘못 바뀐 규격화는 **더보기 → 되돌리기**로 복원합니다(그사이 직접 고쳤다면 거절, [[REV-3]]). 많은 노트는 CLI `knowledgeos standardize PATH --dry-run` / `--apply`를 쓰고, 새 노트를 처음부터 규격에 맞게 만들 때는 QuickAdd·Templater(`99_System/Templates/`)를 씁니다([[STD-7]]). 상세 규칙은 [[c2]]를 보세요.

#### 정리 요청 (`정리 → 정리 요청`)

1. 분류·제목·태그·관련 링크·폴더 이동 제안을 받고 싶은 노트를 열고 **정리 → 정리 요청**을 누릅니다(폴더·다중 선택은 한 번에 최대 10개). `type: daily`, `ai_policy: deny`, 충돌 표시, 중복 ID 노트는 거절됩니다([[ORG-8]]).
2. 요청이 접수되면 Temporal `WorkWorkflow`가 시작되고 `kos-curation-manager` Profile(`gpt-6.1-sol`)이 대상에 맞춰 팀원 순서를 동적으로 짭니다. 정적 `scout`이 대상과 후보·태그 어휘를 모은 뒤 `kos-classifier`(`gpt-6.1-terra`)와 필요 시 `kos-linker`(`gpt-6.1-terra`)가 인계물(handoff)을 만들고, 정적 검사와 `kos-eval`(`gpt-6.1-sol`) 평가를 통과하면 Owner가 `01_AI_Review/Proposals/`에 제안 노트 하나를 만듭니다. 부족하면 예산 안에서 후속 구간을 한 번 더 돕니다([[c1]]).
3. **원본 노트는 전혀 바뀌지 않습니다**([[ORG-1]]). 사이드바 하단에 생성된 제안 노트 링크가 뜨며, 같은 대상에 이미 열린 제안이 있으면 중복 생성하지 않고 덮어쓰기 규칙에 따라 갱신합니다(사용자가 직접 고친 제안은 건드리지 않음). 매일 06:00에는 미처리 포착과 종류 없는 `approve` 노트를 최대 10개씩 자동으로 정리합니다.

#### 제안 검토 (`적용` · `거절`)

1. `01_AI_Review/Proposals/`의 제안 노트를 열면 사이드바 아래에 **적용 · 거절** 막대가 나타납니다(Note Toolbar의 제안 전용 도구 모음과 명령 팔레트에도 같은 버튼이 있습니다).
2. 제안 안의 체크리스트에서 반영하기 싫은 항목의 체크를 해제합니다. 해제한 항목은 건너뛰고 체크된 변경만 반영됩니다([[ORG-4]]).
3. **적용** 또는 **거절**을 누르면 Work나 Temporal을 새로 열지 않고 Owner가 접수 단계에서 즉시 처리합니다. 적용 시에는 대상 노트의 SHA-256 해시가 제안을 만들 때와 같은지 확인한 뒤 변경(또는 폴더 이동)을 쓰고 제안 파일을 삭제합니다([[REV-1]]). 그사이 대상 노트를 수정했다면 원본은 그대로 두고 제안을 `stale`(낡음, 7일 뒤 자동 정리)로 표시하며 다시 정리 요청하도록 안내합니다([[REV-2]]). 거절 시에는 결정을 기록하고 제안 파일만 지웁니다. 상세는 [[c6]]을 보세요.

#### 강화 (`강화 → 발전 / 묻기 / 돌아보기`)

| 모드 | 언제 고르는가 | 입력할 것 | 만들어지는 보고서(`01_AI_Review/Reports/`) |
|---|---|---|---|
| **발전** (`develop`) | 지금 연 아이디어·지식·질문 노트를 깊게 풀고 싶을 때 | 현재 노트 (버튼 한 번) | 내 말로 본 핵심, 이미 쓴 관련 생각, 연결과 긴장, 빈틈·열린 질문, 다음 걸음 |
| **묻기** (`ask`) | Vault 전체를 상대로 질문을 던지고 답을 모을 때 | 인라인 질문 입력 (노트 없어도 가능) | 답, 뒷받침하는 노트, 상충하는 노트, Vault가 다루지 않는 것 |
| **돌아보기** (`review`) | 프로젝트 노트의 일정 기간 진척을 회고할 때 | 프로젝트 노트 + 시작일·종료일(`YYYY-MM-DD`) | 움직인 것, 멈춘 것, 열린 결정, 다음 행동 |

모드 선택만으로는 요청하지 않습니다. 요청 버튼을 누르면 접수 검사 뒤 Thinking Team이 실행됩니다. `kos-thinking-manager`가 근거 수집과 연결·반론·보고서 역할의 순서를 고르고, 정적 검사와 `kos-eval` 평가를 통과한 보고서를 Owner가 씁니다. 단, 노트 없이 **묻기**를 했는데 일치하는 노트가 전혀 없으면 Team을 시작하지 않고 “Vault가 다루지 않음”으로 끝납니다. 보고서에는 **인용 / 추론 / 찾지 못함**이 구분되며, 읽은 입력의 정책을 물려받습니다. 생성 뒤에는 자유롭게 수정·이동할 수 있고, `type`을 바꾸기 전에는 역할의 근거로 다시 쓰지 않습니다. 역할별 Profile과 구간 구성은 [[c4]]에서 설명합니다.

#### 찾기 (`찾기 → 의미 검색 / 관련 노트`)

- **의미 검색**: 질문을 적고 **검색**을 누르면 Work나 영수증을 만들지 않고 Owner가 `POST /api/search`로 즉시 답합니다. Owner가 로컬 Ollama로 질문을 임베딩해 `<State>/semantic/`에서 가장 가까운 조각(제목·경로·발췌·줄 위치·점수)을 사이드바 링크로 띄우며, 누르면 그 노트의 해당 줄로 바로 열립니다. 색인이 아직 없거나 Ollama가 꺼져 있으면 `"unavailable"`이라고 알릴 뿐 키워드 검색으로 몰래 대체하지 않습니다([[SRC-3]]).
- **관련 노트**: **관련 노트 찾기**를 누르면 현재 노트의 링크·백링크·공통 태그·의미 이웃을 이유 표시와 함께 인라인 링크로 보여 줍니다. System One이 설정되어 있으면 의미 이웃 후보가 정말 관련 있는지 확률로 걸러 표시합니다([[SRC-9]]). `deny` 노트와 제안 노트는 어떤 검색에도 나오지 않습니다([[c3]]).

#### 자유 요청 (`더보기 → 자유 요청`)

정확히 어떤 명령을 눌러야 할지 애매할 때 문장으로 적고 **요청**을 누릅니다. Owner는 먼저 요청 문장만 System One(`gpt-6-luna`)에 보내 KnowledgeOS와 무관한 요청(`none_of_these`, 확신도 `0.7` 이상)을 즉시 거절하고([[SUR-10]]), 통과한 요청만 `kos-director`(`gpt-6.1-sol`)가 허용된 명령(`standardize.preview`, `organize`, `enhance`) 중 하나로 번역해 파생 요청을 실행합니다. 작업 관리나 제안 적용·거절로는 연결되지 않으며, 매핑할 수 없으면 Work를 만들지 않고 이유를 보여 줍니다([[c11]]).

#### 데일리 노트 (`T10_Daily.md` · 매일 07:00)

Obsidian에서 오늘 데일리 노트(`10_Journal/Daily/YYYY-MM-DD.md`)를 **메모 / 할 일 / 일정 / 하루 정리** 네 구역으로 씁니다. 이어갈 항목은 최상위 `- [ ]`나 `- [/]`로 적고, 아침 Discord 알림으로 받을 할 일에는 `#task`를 붙입니다. 매일 07:00에 Owner 안의 `DailyOfficer` Work가 이전 날짜의 열린 항목을 오늘 노트로 옮기고 원본 체크박스만 `[>]`로 바꾸며, `#task`가 있으면 할 일 이름만 추려 하루 한 번 알림을 보냅니다. `type: daily` 노트는 정리 제안 대상이 아닙니다([[c12]], [[ORG-8]]).

## 2. 브라우저 관리 콘솔과 CLI

### 브라우저 관리 콘솔

사이드바 **더보기 → 관리 콘솔**을 누르면 Owner가 loopback(`http://127.0.0.1:8765/`)에서 직접 서빙하는 로컬 콘솔이 열립니다. 조회만으로는 상태나 알림이 변하지 않으며, 문서 편집 기능은 없습니다.

- **볼 수 있는 것**: **작업** 목록과 상세(`WorkSpec`, `Graph 세그먼트`, `역할 호출`의 Profile·모델 경로·skill 해시, `독립 평가`, `CompletionAssessment`, `RemainingWork`, `TeamReview`), **예약** 실행 기록, **결과 불명**, **주의**(Bases로 보기 어려운 중복 ID·충돌·깨진 YAML·낡은 제안·실패 작업), **알림**, **Experience** outbox, **역할** 지도와 학습 기록, 현재 탭 **요청** 추적.
- **할 수 있는 것**: 실행 중 **작업 취소**, 예약 작업 **일시정지 · 재개**, **결과 불명 확인 기록**(`적용됨`/`적용 안 됨` — 과거 기록은 고쳐 쓰지 않고 관찰만 남김), `TeamReview` 개선 제안 **검토함 표시**. 모두 Thin Client와 같은 ExOps 접수 경로를 지납니다([[c11]]).

### CLI와 서비스 설치

운영자 유지보수·설치용 `knowledgeos` CLI는 `--config owner.json` 또는 `--endpoint URL`로 Owner에 붙습니다. `owner.json`에서 `vault`, `state`, `runtime`은 서로 겹치거나 포함되지 않게 따로 지정해야 합니다.

| 명령 | 언제 쓰는가 |
|---|---|
| `knowledgeos install service --config owner.json [--dry-run]` | macOS LaunchAgent 등록(`RunAtLoad`·`KeepAlive` 꺼짐, 로그는 Runtime `logs/`). `--dry-run`은 plist와 `launchctl` 인자만 출력([[SUR-7]]) |
| `knowledgeos install profiles --config owner.json [--dry-run]` | `config/roles.json`·`roles/` 기준으로 9개 역할 Profile과 `kos-ops` 설치([[c9]]) |
| `knowledgeos serve --config owner.json` | Owner, Temporal Worker, 스케줄러, ExOps와 관리 콘솔 실행 |
| `knowledgeos health` / `vault-check` | Owner·의존성(`search_semantic`, `system_one` 등) 상태 확인 / 깨진 YAML·충돌·중복 ID 점검 |
| `knowledgeos index rebuild \| verify \| embed` | 키워드 색인 재구축, 의미 색인 준비 상태 점검, 남은 노트가 없을 때까지 `index.embed` 실행([[c3]]) |
| `knowledgeos standardize PATH --dry-run \| --apply` | 폴더 또는 노트 대량 규격화 계획 출력 및 적용([[SUR-5]]) |
| `knowledgeos job run standardize\|embed\|curate\|daily\|digest` | 예약 작업을 즉시 실행하고 자식 요청의 최종 영수증까지 함께 보고 |
| `knowledgeos reconcile UNKNOWN_ID --resolution applied\|not_applied --yes` | 결과 불명 항목의 실제 관찰 결과를 기록(재실행 아님) |
| `knowledgeos work export WORK_ID` | Work 기록 하나를 JSON으로 출력 |

## 3. 자동으로 도는 작업과 외부 서비스 한눈에 보기

- **예약 작업(Owner가 켜져 있는 동안만 실행)**: 매시 정각 `standardize`(규격화), 매시 30분 `embed`(의미 색인), 매일 06:00 `curate`(정리, 최대 10개), 매일 07:00 `daily`(데일리 인계·할 일 알림), 매일 08:00 `digest`(문서 관리 + 검토할 것이 있을 때만 Discord 요약). Owner가 꺼져 있었다면 다시 켠 뒤 시작 지연(기본 3분) 후 놓친 작업을 작업당 1번씩만 따라잡습니다. 일시정지된 작업은 건너뛰며, 실패 시 자동 재시도하지 않습니다. 자세한 동작은 [[c7]]을 보세요.
- **의미 검색과 로컬 Ollama**: Owner가 loopback HTTP(`http://127.0.0.1:11434`, 기본 모델 `qwen3-embedding:8b-q4_K_M`)로 직접 임베딩해 State `<State>/semantic/`에 저장합니다. 처음 설정할 때는 `knowledgeos index embed`로 전체를 채운 뒤 `index verify`로 확인하세요. 새로 쓰거나 고친 노트는 다음 `embed` 실행 전까지 검색에서 빠집니다([[c3]]).
- **System One (`gpt-6-luna`)**: Owner가 OpenAI Decisions API에 직접 보내는 선택형 판단 서비스입니다(규격화 규칙 9, `system_one` 팀원 도구, 관련 노트 거르기, 문서 관리 상태 제안, 자유 요청 사전 거름). `api_key_file`(Git·Vault 밖 `0600` 파일)이 없거나 실패해도 모든 기능은 System One 없이 동작하던 대로 이어집니다. `GET /api/health`의 `system_one`이 `ready`인지로 확인할 수 있습니다.
- **Hermes 모델 경로**: 현재 `config/roles.json`은 `classifier`·`linker`·`connector`에 `openai-codex/gpt-6.1-terra`, Director·Manager·EvalOfficer·`challenger`·`synthesizer`에 `openai-codex/gpt-6.1-sol`을 지정하며 추론 노력은 모두 `high`입니다. 한 모델 단계는 하나의 Temporal Activity 안에서 끝나야 하며 기본 시간 제한은 120초(실제 호스트 `owner.json`은 600초)입니다([[architecture]]).
- **Discord 알림 (`kos-ops`)**: 매일 07:00 할 일 알림(이름만), 매일 08:00 검토 요약(볼 것이 있을 때만), 즉시 알림(Vault 충돌과 해결 안 된 효과·액션 결과 불명만). Discord는 발송 전용이라 답장·반응으로 승인이나 명령을 받지 않습니다([[c8]]).
- **모바일**: 현재 모바일 전용 KnowledgeOS는 없습니다. iPhone·iPad에서는 Obsidian과 Working Copy로 GitHub에 동기화해 쓰면 Mac의 Owner가 받아서 규격화·정리하며, Discord의 `obsidian://` 링크는 모바일에서도 열립니다. 단축어·모바일 요청 후속(M1~M3)은 [[roadmap]]을 보세요.

## 4. 문제가 생겼을 때

| 증상 | 원인과 할 일 |
|---|---|
| 버튼을 눌러도 연결 안 됨 | Owner가 꺼져 있을 수 있습니다. 사이드바 하단 전원 버튼(**Owner 시작**)으로 켜고 `연결됨`이 될 때까지 기다립니다 |
| `"의미 검색을 사용할 수 없습니다 (unavailable)"` | 색인이 아직 없거나 모델 변경 직후, 또는 Ollama가 꺼진 상태입니다. `knowledgeos index verify`와 Ollama 상태를 확인한 뒤 `knowledgeos index embed`를 실행합니다 |
| 방금 저장한 노트가 의미 검색에 안 나옴 | 정상입니다. 매시 30분 `embed` 또는 `knowledgeos index embed` 뒤에 반영됩니다 |
| 제안 적용 시 `"적용 안 됨, 낡음"` | 제안을 만든 뒤 대상 노트가 바뀌었습니다. 원본은 그대로이고 제안은 `stale`로 표시되므로 다시 **정리 요청**을 보냅니다 |
| 되돌리기가 거절됨 | 마지막 규격화 뒤에 노트를 직접 수정했거나 이미 되돌렸습니다. Git 이력으로 복원합니다 |
| 정리·강화가 모델 불가 오류로 실패 | 해당 `kos-*` Profile이나 자격 증명이 없는 것입니다. 다른 모델로 몰래 바꾸지 않으므로 Profile과 인증을 점검합니다 |
| 콘솔에 결과 불명(`unknown`)이 뜸 | 타임아웃 등으로 결과를 관찰하지 못한 항목입니다. 다시 실행하지 말고 실제 파일 상태를 확인한 뒤 콘솔 **결과 불명** 탭이나 `knowledgeos reconcile`로 확인 기록을 남깁니다 |
| 데일리 노트 정리 요청이 거절됨 | 정상입니다. `type: daily`는 `approve`여도 AI 제안 대상이 아니며 데일리 인계(`daily`)만 동작합니다 |
| 노트가 규격화·정리에서 빠짐 | `ai_policy: deny`, 충돌 표시, 깨진 YAML, 중복 ID, `99_System/`·`01_AI_Review/` 경로는 건너뜁니다. 콘솔 **주의** 탭이나 `knowledgeos vault-check`로 확인하세요 |
| 자동 규격화가 방금 쓴 노트를 건너뜀 | 자동 `standardize`는 최근 10분 안에 편집한 노트를 건너뜁니다. 바로 고치려면 사이드바 **규격화 → 미리보기**를 쓰세요 |

## 참고 자료

- **실제 호스트·Obsidian 확인 범위와 남은 일**: [[status]], [[roadmap]]
- **소스·설정**: [Thin Client](../../thin-client/main.js), [CLI](../../src/knowledgeos/cli.py), [콘솔 화면](../../frontend/app.js), [역할·모델 설정](../../config/roles.json), [예약 작업 설정](../../config/jobs.json), [CAPABILITIES](../CAPABILITIES.md), [USER_FEATURE_SPEC](../../USER_FEATURE_SPEC.md)
