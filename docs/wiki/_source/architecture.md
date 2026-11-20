# 구조: 누가 무엇을 맡는가

KnowledgeOS의 설계는 한 문장으로 줄일 수 있습니다. **"모든 객체는 서로 겹치지 않는 책임 하나만 맡는다."** 어떤 일이 두 객체에 걸쳐 보이면 그것은 설계 결함이고, 명세를 고쳐야 합니다(Blueprint §4.1). 이 페이지는 그 책임 지도를 위에서부터 풀어 줍니다. 용어가 낯설면 [[glossary]]를 함께 여세요.

## 한눈에 보기

{{svg:system-map}}

- **입구는 셋**: Obsidian Thin Client(명령·사이드바), 브라우저 콘솔, CLI. 셋 모두 **같은 접수 경로**(ExOps)로 Owner에 요청합니다.
- **Owner**가 권한 판단·파일 쓰기·기록을 모두 맡고, 작업의 수명은 **Temporal**(내구성 있는 실행)과 **LangGraph**(Graph 실행)가 이끕니다.
- **모델이 필요한 단계**는 역할마다 따로 둔 **Hermes profile**이 맡습니다. Discord로 나가는 알림은 모델 없는 `kos-ops` profile만 보냅니다.
- **빠른 선택형 판단**은 역할이 아니라 Owner 서비스인 **System One**이 맡습니다. Owner가 OpenAI Decisions API(`gpt-6-luna`)에 직접 묻고 정해진 선택지나 확률만 받으며, Hermes profile은 쓰지 않습니다.
- **의미 검색용 임베딩**은 Owner가 로컬 Ollama를 loopback HTTP로 직접 부릅니다.
- **데이터는 셋으로 갈립니다**: 사용자의 Vault, 운영 기록인 State, 다시 만들 수 있는 Runtime.

## 화면(표면)은 서로의 일을 하지 않는다

| 표면 | 맡는 일 | 하지 않는 일 | 더 보기 |
|---|---|---|---|
| Obsidian 본체와 플러그인 | 쓰기, 템플릿, 키워드 검색, 보기, 할 일, Git 보기 | KnowledgeOS 호출 | [[usage]] |
| Thin Client | 현재 노트에서 KnowledgeOS 호출(명령·사이드바), 결과 알림, Owner 시작·중지 | 문서 목록, 작업 브라우저, 설정 늘리기 | [[c11]] |
| 브라우저 콘솔 | KnowledgeOS **관찰과 관리** | 문서 작업 | [[c11]] |
| CLI | 유지보수와 설치 | 문서 작업 | [[c11]] |
| Discord | 나가는 알림 | 명령·승인 받기 | [[c8]] |

플러그인이 할 수 있는 일은 플러그인에 맡기고 Thin Client는 작게 둡니다. 플러그인은 설정일 뿐 필수 의존이 아니어서, 플러그인이 하나도 없어도 Vault는 일반 Markdown으로 쓸 수 있습니다.

## 세 개의 저장소: Vault · State · Runtime

핵심 규칙은 **"사용자가 잃으면 아쉬울 것은 전부 Vault에 있다"** 입니다. State와 Runtime을 잃어도 지식은 남고, 잃는 것은 진행 중이던 작업, 되돌리기 바로가기, 대기 중이던 알림 정도입니다. 이 셋은 설정에서 각각 따로 지정하며 서로 겹치거나 포함할 수 없고, State·Runtime·자격 증명은 Git과 Vault 밖에 둡니다.

{{include:BLUEPRINT#2. Ownership and storage}}

## 역할: 누가 무엇을 하나

실행 단계는 두 가지입니다. **static**은 Owner 코드가 하는 단계이고, **model**은 Hermes 역할 profile이 하는 단계입니다. Owner가 바이트 단위로 계산하거나 검증할 수 있는 일은 static이고, 모델은 판단에만 씁니다.

| 객체 | 쉽게 말하면 | 실행 방식 |
|---|---|---|
| **Owner** | 권한 판단·파일 쓰기·기록 담당. 내용은 판단하지 않음 | 코드 |
| **Director** | 접수 창구. 요청을 작업 명세(WorkSpec)로 받아들임 | 정형 요청은 static, 자유 요청만 model(`kos-director`) |
| **Manager** (CurationManager, ThinkingManager) | 팀장. 어느 팀원을 어떤 순서로 쓸지 정하고 다시 짜며, 끝나면 TeamReview를 남김. 직접 만들거나 평가하지 않음 | model |
| **Teammate** | 팀원. 중간 산출물(handoff) 한 종류만 만듦 | `scout`·`retriever`는 static, 나머지(`classifier`·`linker`·`connector`·`challenger`·`synthesizer`)는 model |
| **Officer** (StandardizationOfficer, StewardshipOfficer) | 팀 없이 혼자 한 결과를 책임짐 | static |
| **EvalOfficer** | 독립 평가자. 결과를 만들거나 고치지 않고, Manager의 설명이나 팀원의 자기평가도 보지 않음 | static 검사는 항상, 의미 기준만 model(`kos-eval`) |
| **Scheduler** | 정해진 작업을 때맞춰 트리거함 | Owner 안 서비스 |
| **Notifier** | 만들어진 알림을 전달함 | `kos-ops` profile |
| **System One** | 선택지 중 하나(`choice`)나 확률(`predicate`)만 답하는 빠른 판단. 쓰지도 글을 짓지도 않고 `ai_policy`·`sensitivity`는 정하지 않음 | Owner 서비스(OpenAI Decisions API 직접 호출, profile 없음) |

검색(C3)은 역할이 아니라 Owner가 사람과 역할에게 도구로 제공하는 **서비스**입니다. System One도 역할이 아니라 Owner의 **서비스**입니다. 규격화 규칙 9([[c2]]), 정리 `scout` 힌트([[c1]]), 관련 노트 이웃 거르기([[c3]]), 문서 관리 상태 제안([[c5]]), 자유 요청 사전 거름([[c11]])에서 Owner 코드가 질문하고, 답을 쓸지는 Owner 코드가 정합니다. 설정이 없거나 실패하면 이 기능들은 System One 없이 하던 대로 동작하며, 어떤 기능도 System One 때문에 실패하지 않습니다. 역할을 더하거나 빼는 것은 사용자가 검토하는 명세 변경이며, 대개 TeamReview 제안이 계기가 됩니다.

### 두 개의 팀

| 팀 | 기능 | Manager | 팀원 |
|---|---|---|---|
| Curation Team | [[c1]] 정리 | CurationManager | `scout`(수집), `classifier`(분류), `linker`(연결) |
| Thinking Team | [[c4]] 강화 | ThinkingManager | `retriever`(근거 수집), `connector`(연결·긴장), `challenger`(빈틈·반론), `synthesizer`(보고서) |

Officer로 처리되는 것은 [[c2]] 규격화(StandardizationOfficer), [[c5]] 문서 관리(StewardshipOfficer), [[c3]]의 임베딩(EmbeddingOfficer)입니다. 앞의 둘은 코드로 움직이면서 설정 시 System One에게 선택 질문을 합니다.

아래는 Blueprint의 원문입니다.

{{include:BLUEPRINT#4.1 Responsibility map}}

{{include:BLUEPRINT#4.2 Roster}}

## 일이 흘러가는 길 (Work flow)

{{svg:work-flow}}

모든 요청은 먼저 Director를 지납니다. 정형 요청(Thin Client 명령, 예약 작업, CLI 명령)은 코드가 접수하고, 자유 요청만 `kos-director` 모델이 읽어 초안을 만듭니다.

1. **요청**: Thin Client·CLI·예약 작업·Gateway에서 들어옵니다. 요청 ID가 있어 같은 요청을 두 번 보내도 한 번만 처리됩니다.
2. **접수(Director)**: 대상(Officer 또는 Team), 기준(criteria), 예산, 입력 범위를 담은 WorkSpec을 만듭니다. 기준은 기능 카탈로그에서 오며 **모델은 기준을 쓰지 못합니다**.
3. **Officer 대상이면** 한 단계로 결과를 만듭니다. **Team 대상이면** Manager가 Graph를 짭니다. Manager가 내는 것은 "승인된 팀원 중 누구를 어떤 순서로" 와 "평가 위치"뿐이고, Owner가 그것을 검증해 Core의 `GraphSpec` 등으로 만듭니다.
4. **팀원 단계**: 팀원이 차례로 handoff를 만들고, 뒤의 팀원은 앞의 handoff를 읽을 수 있습니다.
5. **평가(EvalOfficer)**: Owner가 증거 시점을 먼저 기록합니다. 그다음 static 검사가 돌고, 의미 기준만 EvalOfficer 모델이 판단합니다. 최종 판정(`CompletionAssessment`)과 남은 일(`RemainingWork`)은 Owner가 만듭니다.
6. **만족하면** Owner가 결과(제안 노트·보고서)를 렌더링해 씁니다. **기준에 못 미치고 예산이 남으면** 같은 Manager가 남은 부분만 다루는 후속 구간을 짭니다. **예산이 바닥나면** 사용자에게 보이고, 새 요청이 필요합니다.
7. **TeamReview**: 완료 후 Manager가 팀원마다 성과를 평가하고 개선 제안을 최대 3개 남깁니다. 제안은 조언일 뿐, 역할·카탈로그·평가를 바꾸지 못합니다. 바꾸는 것은 사용자가 명세와 카탈로그를 고쳐서만 합니다.

중요한 규칙은 다음과 같습니다.

- **예산**: Team은 기본 2구간·생산자 호출 6회, Officer는 1구간. 팀원 단계는 static이든 model이든 생산자 예산을 소모하고, 교정 턴은 소모하지 않습니다.
- **같은 미충족이 두 번 이어지면** 작업은 미충족으로 끝납니다. 대상이나 목표가 바뀌면 새 접수가 필요합니다.
- **모델 제출이 형식에 어긋나면** 진단과 함께 교정 턴을 한 번 줍니다. 두 번째에도 실패하면 그 단계는 실패이고 해당 기준은 미충족으로 남으며, 유효한 출력 없이 평가자에게 인증을 요청하지 않습니다. 예: 인용 없는 주장([[ENH-2]]), 읽지 않은 노트를 인용한 링크([[ORG-2]]).
- **권한 위반**은 **교정 턴 없이 즉시 거절**됩니다. Manager가 EvalOfficer에게 지시하는 Graph를 제출하면 구간은 시작되지 않고 이미 접수된 Work가 실패합니다([[ORG-5]]). Director 모델이 기준·예산·경로 같은 권한 필드를 정하려 하면 접수 단계에서 거절되어 **Work 자체가 만들어지지 않습니다**.
- 이미 승인된 구간은 바뀌지 않습니다. 기준 미충족이면 새 구간을 더할 뿐입니다([[ORG-6]], [[ENH-6]]).

**예**: Inbox의 노트에 "정리 요청"을 누르면, Director가 정리 작업을 접수하고, CurationManager가 `scout → classifier → linker` 같은 Graph를 짠 뒤, 평가를 거쳐, Owner가 `01_AI_Review/Proposals/`에 제안 노트 하나를 씁니다. 원래 노트는 그대로입니다([[ORG-1]]).

## Owner와 ExOps

**Owner**는 권한과 효과의 주인입니다. 정책 결정, State, 입력 스냅숏, 근거 핸들, 제출물의 스키마 검증, Vault 파일 렌더링과 쓰기, Core 기록 작성, 알림을 맡고, 내용을 고르거나 Graph를 짜거나 기준을 판단하지 않습니다. **Vault에 쓰는 것은 Owner뿐**이며, 어떤 역할도 파일·셸·네트워크 도구가 없습니다.

**ExOps**는 Owner의 HTTP 접수 경로입니다.

- Thin Client, 콘솔, CLI가 **하나의 접수 경로**를 공유합니다.
- loopback(`127.0.0.1`)에만 바인딩하고, Host와 Origin을 자동으로 검사하며, 변경은 JSON만 받고, 교차 출처 접근을 허용하지 않습니다. 로그인·계정·토큰이 없습니다. 이것은 **로컬 전용·단일 사용자** 전제입니다.
- **GET은 관찰만** 합니다. 작업을 시작하거나 결정을 저장하거나 알림을 보내거나 결과 불명을 정리하지 않습니다. 콘솔을 열어 보는 것만으로 알림이 나가거나, 알림이 나가기 위해 콘솔 방문이 필요하지는 않습니다([[c8]]).

## Temporal과 LangGraph

- **Temporal**은 Work의 **내구성 있는 수명**(재시작해도 이어지는 실행)을 맡습니다. 공유 Temporal 서버는 호스트 loopback `127.0.0.1:7233`에만 열려 있고, Owner는 `knowledgeos` 네임스페이스에 연결합니다(PROJECT_STATE의 [[status#E_TEMPORAL_RUNNING|E_TEMPORAL_RUNNING]]).
- **LangGraph**는 승인된 Graph 구간을 **실행**합니다.
- 실행 엔진은 하나입니다. Officer든 Team이든 같은 엔진이 돌리고, 역할 로직은 핸들러 또는 profile이지 별도 파이프라인이 아닙니다(Blueprint §8.4).
- 실행 기록과 결과는 Temporal 서버에 보존되지만, **Work의 소유와 판단은 KnowledgeOS**가 가집니다(README).
- 테스트는 실제 Temporal 개발 서버와 LangGraph를 쓰고, 모델 역할만 스크립트 대역입니다([[status]]).

## Hermes 역할 profile

{{include:BLUEPRINT#4.4 Hermes role profiles}}

쉬운 말로:

- 모델을 쓰는 역할마다 profile이 따로 있고, 모델 경로는 profile 설정에 **고정**됩니다. 모델이나 자격 증명이 없으면 그 단계가 **눈에 보이게 실패**하며, 몰래 다른 모델로 바꾸지 않습니다.
- 역할이 쓸 수 있는 도구는 **작업에 묶인(binding)** 것뿐입니다. 예컨대 Manager는 노트를 읽을 수 없고, EvalOfficer는 평가 보기와 평가 제출만 합니다. 터미널·파일·브라우저·웹·코드 실행 같은 도구는 모두 꺼져 있습니다.
- 모델은 경로·ID·digest처럼 Owner가 계산할 수 있는 값을 **직접 쓰지 않고**, 읽기 도구가 돌려준 짧은 핸들(`e7` 같은 것)을 인용합니다. Owner가 그것을 정확한 인용으로 펼칩니다.
- 설치는 `knowledgeos install profiles --config owner.json`으로 합니다. 설정에는 Hermes 호스트 설정(`settings.hermes.binary` 등)이 필요합니다. `--dry-run`은 Runtime에 배포물을 생성하되 Hermes 설치 명령은 실행하지 않습니다. 역할 정의(SOUL·공통 지침)는 `roles/`에, 모델 경로는 `config/roles.json`에 있습니다.
- 실환경 스모크에서 관찰된 모델은 Director·Manager·EvalOfficer가 `gpt-6.1-sol`, 팀원이 `gpt-6.1-terra`였습니다([[status#E_REAL_ROLE_SMOKE|E_REAL_ROLE_SMOKE]]). 이것은 **그 실행에서 관찰된 값**이고, 설정의 영구 약속이 아닙니다.

## 개인정보: `ai_policy`

노트마다 `ai_policy`가 있어 모델과 자동화가 그 노트로 무엇을 해도 되는지 정합니다. 이것은 **모델·자동화의 사용 범위**를 정할 뿐이며, GitHub 동기화 여부는 Vault의 `.gitignore`가 정합니다.

{{svg:policy-matrix}}

| 값 | 쉬운 말 |
|---|---|
| `remote_ok` | 예약 작업을 포함해 모델이 써도 됩니다 |
| `ask` (기본) | 내가 직접 요청한 작업에서만 모델이 씁니다. 예약 작업의 모델은 이 노트를 못 봅니다 |
| `local_only` | 로컬 처리와 로컬 모델만 |
| `deny` | KnowledgeOS가 읽지도, 색인하지도, 알리지도 않습니다 |

지켜지는 규칙은 이렇습니다.

- `confidential` 노트는 `ai_policy`가 느슨해도 `local_only`처럼 다룹니다. 어긋남은 "주의 항목"으로 알려 주되 **내 값을 고쳐 쓰지는 않습니다**.
- **결과물은 입력 중 가장 엄격한 정책을 물려받습니다.** 읽기만 했고 인용하지 않은 노트도 포함합니다([[ENH-4]]).
- 정책은 공개하기 **직전**의 현재 내용으로 다시 확인하며, 옮겨지거나 지워지거나 새로 제한된 노트는 닫힌 채로 실패합니다.
- 결정적 로컬 처리(규격화 규칙 1~8·임베딩)는 `deny`만 빼고 모두 할 수 있어서, `ask`·`local_only`·`confidential` 노트도 임베딩됩니다([[c3]]).
- 예약 정리([[c1]])는 모델에 `remote_ok` 노트만 줍니다([[ORG-3]]). Gateway 조회는 `remote_ok` 노트만 내보냅니다([[GW-2]]). 알림 제목·링크는 `remote_ok`·`ask`만 쓸 수 있습니다([[NTF-1]]).
- **System One은 원격 모델입니다.** 같은 정책 열을 따르고 로컬 경로가 없습니다. 내가 직접 요청한 작업과 검색은 `remote_ok`·`ask` 노트를, 예약 작업과 Gateway 작업은 `remote_ok` 노트만 보내며, `local_only`·`confidential`·`deny` 노트는 어느 경로에서도 보내지 않습니다. 정책은 보내기 직전의 현재 값으로 다시 확인하고, `ai_policy`·`sensitivity` 자체는 System One에게 묻지 않습니다. 자격 증명은 Git과 Vault 밖의 호스트 소유 0600 파일에 있고 Hermes 하위 프로세스에는 전달되지 않습니다. 호출 기록(State `system_one`)에는 질문 이름·입력 해시·모델·토큰 수만 남고 노트 내용은 남지 않습니다([[c2]]).

{{include:BLUEPRINT#5.4 Privacy}}

## 제안·보고서, 그리고 안전하게 쓰기

정리·강화의 결과 파일은 제안과 보고서입니다. 이 밖에 Gateway 접수([[c10]])는 `00_Inbox/`에 출처 정보가 붙은 포착(`capture`) 노트를 만듭니다. 기존 노트의 변경은 아래 효과 규칙을 따릅니다.

- **보고서**(`01_AI_Review/Reports/`): 한 번 만들어지면 이후로는 **사용자의 문서**입니다. `type`을 바꾸기 전까지는 근거로 쓰이지 않습니다.
- **제안**(`01_AI_Review/Proposals/`): 대상 노트 하나에 대한 변경 목록(체크리스트)입니다. 적용·거절·만료 뒤에 KnowledgeOS가 지웁니다. 기록은 Git에 남습니다.

{{svg:review-flow}}

대상 노트를 바꾸는 길은 규격화([[c2]]), 사용자가 적용한 제안, 사용자가 요청한 되돌리기, 이렇게 셋뿐입니다. 쓰기는 다음 규칙을 지킵니다.

- **정확한 preimage**: 바꾸기 직전에 파일의 현재 SHA-256이 기대한 값과 같은지 확인하고 원자적으로 교체합니다. 다르면 **쓰지 않습니다**. 병합·덮어쓰기·재시도 반복이 없습니다([[STD-3]], [[REV-2]]).
- **의도 먼저**: Owner는 효과를 하기 전에 의도를 기록합니다. Vault 쓰기는 파일을 읽어 판정합니다(기대한 결과면 적용됨, preimage면 적용 안 됨, 그 밖이면 충돌로 사용자에게 보임).
- **결과 불명은 재실행하지 않는다**: 관찰할 수 없는 역할 호출·알림 결과는 `unknown`으로 남고, 자동으로 다시 보내거나 다시 돌리지 않습니다([[NTF-3]]). 사람이 확인해 "적용됨/적용 안 됨"으로 **관찰 기록**을 남길 수는 있지만(콘솔·CLI의 reconcile), 과거 기록은 고치지 않습니다([[SUR-4]]).
- **영수증(receipt)**: 요청마다 요청 ID가 있고, Owner는 영수증을 **먼저 확정한 뒤** 답합니다. 응답을 잃으면 클라이언트는 그 영수증을 읽습니다. 재전송은 "기록된 적 없음"이 확인된 뒤에만 허용되고, 불명 상태는 재전송 허가가 아닙니다([[REV-4]]).
- **스냅숏**: 접수 때 선택한 입력의 바이트를 State에 잡아 두므로, 작업 중 사용자가 노트를 고쳐도 보고서는 그 스냅숏을 인용하고 "바뀌었음"을 밝힙니다([[ENH-3]]). 다만 적용은 항상 현재 preimage를 요구합니다.
- **문서 단위 신선도**: 노트 하나가 바뀌면 그 노트에 대한 결과만 무효가 되고 Vault 전체가 무효가 되지 않습니다.
- **Git은 건드리지 않는다**: KnowledgeOS는 commit·pull·push·checkout·reset을 하지 않고, 변경은 frontmatter 중심의 작은 diff로 씁니다. 어떤 변경이든 Git 이력으로 되돌릴 수 있습니다.

{{include:BLUEPRINT#5.6 Files KnowledgeOS writes}}

{{include:BLUEPRINT#6. Effect rules}}

## AgentFabric Core와의 관계

KnowledgeOS는 `Core/`의 계약과 스키마를 **수정하지 않고**, `config/core-adoption.json`에 고정한 핀으로 따릅니다. 아래 표는 Core가 요구하는 아홉 가지 기능(Capsule function)을 KnowledgeOS가 무엇으로 채우는지 보여 줍니다.

{{include:BLUEPRINT#7. AgentFabric Core conformance}}

## 개발자를 위한 제약

{{include:BLUEPRINT#8. Engineering constraints}}

## 코드는 어디에 있나

{{include:README#구성}}

{{include:README#소유와 경계}}
