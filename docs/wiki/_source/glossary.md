# 용어 사전

KnowledgeOS 문서에는 영어 용어가 그대로 쓰입니다. 코드·명세와 같은 단어를 쓰려고 번역하지 않은 것이므로, 여기서 한 번씩 풀어 둡니다. 정의는 원본 문서에서 가져왔고, 원본에 정의가 없어 풀어 쓴 것은 **[해석]** 으로 표시했습니다. 모르는 말이 나오면 이 페이지로 돌아오세요.

## 명세가 공식으로 정의한 용어

아래는 `docs/CAPABILITIES.md`의 "Shared terms" 원문입니다.

{{include:CAPABILITIES#Shared terms}}

## 요청과 작업

| 용어 | 뜻 | 더 보기 |
|---|---|---|
| **Request(요청)** | Thin Client 명령, 콘솔 동작, CLI 명령, 예약 작업 실행, Gateway 제출 중 하나. 안정적인 요청 ID가 붙습니다 | [[c11]] |
| **Admission(접수)** | Director가 요청을 받아들여 Work로 만드는 일. 정형 요청은 코드가, 자유 요청만 모델이 초안을 만듭니다 | [[architecture]] |
| **Work(작업)** | Director가 접수한 요청 하나. 실행한 주체, 대상, 입력 스냅숏, 구간, 역할 호출, 효과, 평가를 기록합니다 | [[architecture]] |
| **WorkSpec** | 접수된 작업 명세: 대상(Officer 또는 Team), 카탈로그에서 온 기준, 예산, 입력 범위. 모델은 기준을 쓰지 못합니다 | [[architecture]] |
| **Step(단계)** | 구간 안의 역할 호출 하나. `static`(Owner 코드) 또는 `model`(Hermes 역할 profile) | [[architecture]] |
| **Graph / Segment(구간)** | Manager가 정한 "어느 팀원이 어떤 순서로 + 평가 위치". 승인된 구간은 바뀌지 않고, 부족하면 후속 구간이 더해집니다 | [[architecture]] |
| **Handoff(인계물)** | 팀원이 남기는 중간 산출물. 뒤의 팀원이 같은 작업 안에서 읽을 수 있습니다 | [[c1]], [[c4]] |
| **Budget(예산)** | 한 작업이 쓸 수 있는 양. 기본값은 Team 2구간·생산자 호출 6회, Officer 1구간입니다 | [[architecture]] |
| **RemainingWork** | 평가에서 기준에 못 미친 "남은 일". 같은 Manager가 이것만 다루는 후속 구간을 짭니다 | [[ENH-6]] |
| **CompletionAssessment** | 평가 결과(만족·미충족 등)의 기록. Owner가 EvalOfficer의 판정과 static 검사를 모아 만듭니다 | [[architecture]] |
| **Corrective turn(교정 턴)** | 모델 제출이 형식에 어긋날 때 진단과 함께 한 번 더 주는 기회. 두 번째 실패는 단계 실패입니다 | [[ENH-2]] |
| **Authority violation(권한 위반)** | 모델이 자기 권한 밖(기준·예산·경로 정하기, EvalOfficer에게 지시하기 등)을 시도하는 것. 교정 턴 없이 즉시 거절됩니다 | [[ORG-5]] |
| **TeamReview** | 작업이 끝난 뒤 Manager가 팀원 성과를 평가하고 개선 제안(최대 3개)을 남기는 기록. 조언일 뿐 역할을 바꾸지 못합니다 | [[ENH-7]] |

## 역할

| 용어 | 뜻 | 더 보기 |
|---|---|---|
| **Owner** | 권한과 효과의 주인. 정책 판단, State, 파일 쓰기, 기록, 알림을 맡고 내용은 판단하지 않습니다. 호스트에서 필요할 때 켜지는 사용자 서비스입니다 | [[architecture]] |
| **Director** | 접수 담당. 요청을 WorkSpec으로 바꿉니다 | [[architecture]] |
| **Manager** | 팀의 Graph를 짜고 후속 구간과 TeamReview를 맡습니다. CurationManager(정리 팀)와 ThinkingManager(강화 팀)가 있습니다 | [[c1]], [[c4]] |
| **Teammate(팀원)** | 한 종류의 중간 산출물만 만드는 역할. 정리 팀 `scout`·`classifier`·`linker`, 강화 팀 `retriever`·`connector`·`challenger`·`synthesizer` | [[c1]], [[c4]] |
| **Team(팀)** | Manager 한 명과 팀원들. 정리 Team([[c1]])과 강화(Thinking) Team([[c4]])이 있습니다 | [[architecture]] |
| **Officer** | 팀 없이 혼자 결과 하나를 책임지는 static 역할. StandardizationOfficer, StewardshipOfficer, EmbeddingOfficer | [[c2]], [[c3]], [[c5]] |
| **EvalOfficer** | 독립 평가자. 모든 작업의 기준을 평가하고, 결과를 만들거나 고치지 않습니다. static 검사는 항상, 의미 기준만 모델이 판단합니다 | [[architecture]] |
| **Scheduler** | 선언된 예약 작업을 트리거하는 Owner 안의 서비스 | [[c7]] |
| **Notifier** | 만들어진 알림을 전달하는 호스트 서비스. 내용은 만들지 않습니다 | [[c8]] |

## 저장소와 실행

| 용어 | 뜻 | 더 보기 |
|---|---|---|
| **Operation** | AgentFabric 안의 운영 단위. KnowledgeOS도 Operation의 하나이고, 다른 Operation이 정보를 보내거나 조회할 수 있는 기초 Operation입니다 | [[c10]] |
| **Core** | AgentFabric의 공통 계약과 스키마. KnowledgeOS는 수정하지 않고 `config/core-adoption.json`으로 고정해 따릅니다 | [[architecture]] |
| **Vault** | 사용자의 KnowledgeHub 노트 저장소. 사용자의 것이며 GitHub private repo로 백업됩니다 | [[architecture]] |
| **State** | 요청 영수증, 작업 기록, 입력 스냅숏, 자동 변경 이력, 알림·Experience outbox 같은 운영 기록(`States/Operations/knowledgeos/`). 잃어도 지식은 남습니다. 의미 검색 벡터도 여기(`<State>/semantic/`)에 있습니다 | [[architecture]] |
| **Runtime** | 호스트의 Python 실행 환경, Owner 바인딩 설정, LaunchAgent 로그, 생성한 Hermes 배포물. 다시 만들 수 있습니다. 키워드 색인은 메모리에, 의미 검색 벡터는 State에 둡니다 | [[architecture]] |
| **ExOps** | Owner의 loopback HTTP 접수 경로. Thin Client·콘솔·CLI가 공유합니다 | [[architecture]] |
| **Temporal** | 작업의 내구성 있는 수명을 맡는 실행 서버. 호스트 loopback에만 공개됩니다 | [[architecture]] |
| **LangGraph** | 승인된 Graph 구간을 실행하는 엔진 | [[architecture]] |
| **Hermes** | 모델 역할을 실행하는 에이전트 런타임 [해석: 원본이 profile·route·skill·memory로 다루는 대상] | [[architecture]] |
| **Hermes profile** | 모델을 쓰는 역할마다 따로 둔 설정·기억·기술 묶음(`kos-director`, `kos-classifier` 등). 모델 경로는 고정이고 서로 기억을 공유하지 않습니다 | [[architecture]], [[c9]] |
| **`kos-ops`** | 모델도 스케줄러도 없는 발송 전용 profile. Discord 알림만 보냅니다 | [[c8]] |
| **HermesTrace** | 근거 있는 사건(Experience 후보)을 받는 다른 Operation | [[c9]] |
| **InterOps** | Operation 사이의 정보 교환 통로. Gateway 접수·조회와 Experience 전달이 이를 거칩니다 | [[c10]] |
| **Ollama** | 의미 검색용 임베딩을 만드는 로컬 서버. Owner가 loopback으로 직접 부릅니다 | [[c3]] |
| **LaunchAgent / launchd** | macOS 사용자 서비스 등록 방식. Owner는 `RunAtLoad`·`KeepAlive`를 끈 채 등록되어 사용자가 시작할 때만 켜집니다 | [[c11]] |
| **loopback** | `127.0.0.1`. 같은 컴퓨터 안에서만 닿는 주소 | [[architecture]] |
| **health** | Owner가 `GET /api/health`로 알려 주는 상태. Owner 시작·중지는 이것이 실제로 응답하는지로 결과를 판정합니다 | [[c11]] |

## 개인정보와 안전

| 용어 | 뜻 | 더 보기 |
|---|---|---|
| **`ai_policy`** | 노트마다 모델·자동화의 사용 범위를 정하는 속성. `remote_ok`·`ask`(기본)·`local_only`·`deny` | [[architecture]] |
| **`remote_ok`** | 예약 작업을 포함해 모델이 사용할 수 있음. Gateway 조회에도 나갈 수 있음 | [[architecture]] |
| **`ask`** | 내가 직접 요청한 작업에서만 모델이 사용 | [[architecture]] |
| **`local_only`** | 로컬 처리와 로컬 모델만 | [[architecture]] |
| **`deny`** | 읽지도, 색인하지도, 알리지도 않음 | [[ORG-3]] |
| **`sensitivity`** | `public`·`personal`(기본)·`confidential`. `confidential` 노트는 `ai_policy`가 느슨해도 `local_only`로 다룹니다. 어긋남은 주의 항목으로 알리되 사용자의 값을 고쳐 쓰지는 않습니다 [해석: 원본이 `confidential`을 이 속성값으로 가리킨다고 읽음] | [[architecture]] |
| **Preimage** | 바꾸기 직전 파일의 SHA-256. 쓰기 직전 현재 값과 같을 때만 씁니다 | [[STD-3]] |
| **Stale(낡음)** | 제안을 만든 뒤 대상 노트가 바뀌어 더는 적용할 수 없는 상태. 7일 뒤 지워집니다 | [[REV-2]] |
| **Receipt(영수증)** | 요청이 기록됐음을 확정한 기록. 응답을 잃으면 클라이언트가 이것을 읽어 결과를 확인합니다 | [[REV-4]] |
| **Unknown(결과 불명)** | 효과의 결과를 관찰하지 못한 상태. 자동으로 다시 보내거나 돌리지 않습니다 | [[NTF-3]] |
| **Reconcile(확인 기록)** | 사람이 결과 불명 항목을 직접 확인해 "적용됨/적용 안 됨"을 기록하는 일. 과거 기록은 고치지 않습니다 | [[SUR-4]] |
| **Snapshot(스냅숏)** | 접수 때 State에 잡아 둔 입력 노트의 정확한 바이트 | [[ENH-3]] |
| **Fail closed** | 확인할 수 없으면 허용하지 않고 막는 방식 [해석: 원본의 "fails closed"] | [[architecture]] |
| **Autofix** | 결정적 규격화가 노트에 직접 쓴 작은 변경 | [[c2]] |
| **Quiet period** | 최근 편집한 노트를 자동 규격화가 건너뛰는 기간(기본 10분) | [[STD-4]] |
| **Conflict marker** | Git 병합 충돌 표시. 있는 노트는 모든 자동화가 건너뛰고 주의 항목이 됩니다 | [[DOC-3]] |
| **Attention item(주의 항목)** | 사용자가 봐야 할 것. 노트로 저장하지 않고 Bases 보기나 콘솔·요약에서 계산합니다 | [[c5]] |
| **HumanActionResponse** | 적용·거절·되돌리기·콘솔 동작 같은 사람의 결정을 기록하는 Core 기록. 받음·적용됨·적용 안 됨·불명을 구분합니다 | [[c6]] |

## 노트와 파일

| 용어 | 뜻 | 더 보기 |
|---|---|---|
| **frontmatter** | 노트 맨 위의 YAML 속성 블록 | [[c2]] |
| **`type`** | KnowledgeOS가 믿는 유일한 분류. `capture`·`idea`·`knowledge`·`question`·`project`·`source`. 폴더는 `type`을 유추하는 관례일 뿐입니다 | [[c2]] |
| **`status`** | 종류별 상태값. 예: `idea`는 `seed`·`developing`·`mature`·`dropped`, `capture`는 `unprocessed`·`processed`. 모든 종류가 `archived`를 받습니다 | [[c2]] |
| **`id`** | 노트를 이동·이름 변경에도 식별하는 UUIDv4 | [[c2]] |
| **Capture(포착)** | 빠르게 쓴 원시 입력. `00_Inbox/`에 둡니다. 상태는 `unprocessed`·`processed` | [[c1]] |
| **Report(보고서)** | `01_AI_Review/Reports/`에 한 번 만들어지는 노트. 만든 뒤에는 사용자의 문서입니다 | [[c4]] |
| **Proposal(제안)** | `01_AI_Review/Proposals/`의 노트. 대상 노트 하나에 대한 체크리스트와 diff. 사용자만 적용합니다 | [[c1]], [[c6]] |
| **Bases** | Obsidian 코어의 표 보기. 처리 안 된 포착·열린 질문·검토 대기 등을 보여 줍니다(`99_System/Bases/`) | [[c5]] |
| **QuickAdd / Templater** | 템플릿으로 규격에 맞는 노트를 만드는 Obsidian 플러그인 | [[c2]] |
| **Note Toolbar** | 제안 노트에 "적용·거절" 버튼을 붙이는 Obsidian 플러그인 | [[c6]] |
| **`kos_request` / `00_Inbox/Requests/`** | 모바일 요청용으로 예약된 속성과 폴더. 아직 비활성이며 다른 용도로 쓰지 않습니다 | [[roadmap]] |

## 검색

| 용어 | 뜻 | 더 보기 |
|---|---|---|
| **Keyword mode(키워드)** | 한국어·영어 부분 단어 일치. 색인은 메모리에 있습니다 | [[SRC-1]] |
| **Semantic mode(의미)** | 질문과 각 노트의 가장 가까운 조각(chunk)의 임베딩 유사도(코사인)로 순위를 매기는 검색. 색인이 준비되고 질문을 임베딩할 수 있을 때만 쓰며, 안 되면 "unavailable"이고 키워드로 몰래 대체하지 않습니다 | [[SRC-3]] |
| **Embedding(임베딩)** | 글을 숫자 벡터로 바꾼 것. 의미가 비슷한 글은 벡터가 가깝습니다 [해석] | [[c3]] |
| **Chunk(조각)** | 제목·문단 경계로 자른 약 1,500자 창과 그 줄 범위 | [[SRC-7]] |
| **Neighbor(이웃)** | 한 노트의 평균 벡터와 충분히 가까운(`related_min_score` 이상) 노트. 최대 `related_neighbors`개 | [[c3]] |
| **Index ready(색인 준비)** | 설정한 모델의 벡터가 노트 하나 이상에 있는 상태 | [[SRC-3]] |
| **Citation(인용)** | `{resource_reference, chunk_locator, chunk_hash}`. 어떤 바이트를 읽었는지 증명하며, 주장이 참임을 증명하지는 않습니다 | [[c4]] |
| **ResourceReference** | 어느 시점의 노트를 가리키는 Core 참조: 노트 ID 또는 경로와, 읽은 정확한 바이트의 SHA-256 | [[architecture]] |
| **Evidence handle(근거 핸들)** | 읽기 도구가 돌려주는 작업별 짧은 핸들(`e7` 등). 최대 40줄의 정확한 바이트와 위치를 가리킵니다 | [[architecture]] |

## 예약 작업

| 용어 | 뜻 | 더 보기 |
|---|---|---|
| **Job(예약 작업)** | `config/jobs.json`에 선언된 작업. `standardize`·`embed`·`curate`·`digest` | [[c7]] |
| **Slot(슬롯)** | 작업의 예정 시각. 슬롯마다 요청 ID `schedule-<job>-<YYYYMMDDTHHMM>`(서울 시간)이 정해집니다 | [[JOB-5]] |
| **Startup delay(시작 지연)** | Owner가 켜진 뒤 아무것도 트리거하지 않는 기간(`startup_delay_minutes`, 기본 3분) | [[JOB-4]] |
| **Catch-up(따라잡기)** | 꺼져 있는 동안 놓친 작업을 시작 지연 뒤 **한 번만** 실행하는 것 | [[JOB-4]] |
| **Pause / resume** | 콘솔에서 작업을 멈추고 재개함. 멈춘 동안의 슬롯은 지나가고 재개해도 몰아서 하지 않습니다 | [[JOB-6]] |
| **Trigger receipt** | "선언된 요청을 트리거했다"는 기록. 실행 성공을 뜻하지 않으며, 결과는 접수된 요청의 영수증이 가집니다 | [[c7]] |

## 학습과 알림

| 용어 | 뜻 | 더 보기 |
|---|---|---|
| **`feedback` view** | 역할이 자기 이전 출력이 어떻게 처리됐는지(적용·거절·수정 등) 보는 화면 | [[LRN-1]] |
| **Skill / memory** | Hermes profile이 스스로 배우며 쌓는 것. profile끼리 공유하지 않고, 권한을 넓히지 못합니다 | [[LRN-2]] |
| **ExperienceSourceEvent / Candidate** | 근거 있는 사건을 기록한 것(SourceEvent)과 HermesTrace로 보낼 후보(Candidate). 본문 없이 참조·분류·digest만 담습니다 | [[c9]] |
| **`routine_progress`** | TeamReview 제안이 남기는 로컬 관찰의 분류. Candidate나 전송 대기열이 되지 않습니다 | [[c9]] |
| **Digest(요약)** | 하루 한 번 Discord로 가는 요약. 제목과 개수만 담습니다 | [[c8]] |
| **Alert(즉시 알림)** | 충돌·결과 불명·작업 실패가 생기면 항목 ID로 중복 없이 바로 보내는 알림 | [[NTF-2]] |
| **Gateway** | 다른 Operation이 포착을 보내거나(intake) `remote_ok` 노트를 조회(query)하는 문 | [[c10]] |

## 화면

| 용어 | 뜻 | 더 보기 |
|---|---|---|
| **Thin Client** | Obsidian 플러그인. KnowledgeOS 명령과 사이드바 탭을 제공하며 설정은 엔드포인트와 launchd 라벨뿐입니다 | [[c11]] |
| **Sidebar(사이드바)** | 오른쪽 탭 하나에 규격화·정리·강화·찾기, 상황에 따라 적용·거절, 더보기, 전원 버튼을 모은 것 | [[SUR-8]] |
| **관리 콘솔** | 브라우저 페이지(loopback). 작업·예약·결과 불명·주의·알림·Experience·역할을 보고 취소·일시정지·확인 기록을 합니다 | [[c11]] |
| **CLI** | `knowledgeos` 명령. 유지보수와 설치용이며 문서 작업은 하지 않습니다 | [[c11]] |

## 진행 상태를 읽는 말

| 용어 | 뜻 | 더 보기 |
|---|---|---|
| **evidence class** | 증거의 종류(static, runtime, external_service, device, deployment 등). "pass"가 무엇을 증명하는지가 종류마다 다릅니다 | [[status]] |
| **blocker** | 진행을 막는 것의 기록. 해결되면 `resolved`가 됩니다 | [[roadmap]] |
| **handoff** | 다음 세션이 이어받을 일 | [[roadmap]] |
