# KnowledgeOS 아키텍처

## 제품 의도

KnowledgeOS는 자료 창고가 아니라 행동, 판단, 장기 지식을 연결하는 개인 운영체제다. 서로 다른 지식관리 접근은 하나의 폴더 규칙으로 섞지 않고 서로 다른 질문을 담당한다.

| 계층 | 담당 질문 | 구현 방향 |
|---|---|---|
| PARA-lite | 이 정보는 어떤 행동 맥락에 속하는가? | Projects, Areas, Knowledge, Archive |
| Evergreen/Zettelkasten | 장기 보존할 생각은 무엇인가? | 한 노트의 중심 주장, 안정 ID, 근거·반례·적용 |
| 가벼운 지식 그래프 | 노트끼리 어떤 의미 관계가 있는가? | typed relation Properties와 wikilink |
| Hybrid RAG | 어떤 근거를 찾아 답할 것인가? | lexical → typed-link → 검증 후 vector/RRF |

기본 흐름은 `포착 → 구분 → 연결 → 실행/숙성 → 검토 → 보존`이다. 자동화는 후보를 만들고, 사람이 의미와 정본을 결정한다.

## 세 개의 물리적 경계

```text
KnowledgeOS/                  control workspace와 repository
├── docs/                     사람용 계약·운영·결정 기록
├── blueprint/                고정한 기계 판독 설계 패키지
├── ops/                      validator, vaultctl, policy, test와 container 정의
├── KnowledgeHub/                별도 Git repository이자 Obsidian Vault
└── runtime/                  Git 밖의 container bind-mounted persistent 상태
```

control repository는 `KnowledgeHub/`와 `runtime/`을 ignore한다. `KnowledgeHub/`는 독립 notes repository이며 기본값은 submodule이 아니다. runtime과 note corpus를 섞지 않음으로써 Obsidian 검색에 코드·queue·log가 들어가는 것을 막고, 모바일은 notes repository만 Working Copy로 연결할 수 있다. 프로젝트의 Python/`uv`/CLI/worker는 Colima VM 안 container에서 실행하며, `mise`로 관리한 host Python/`uv` 직접 실행은 같은 lockfile을 사용하는 선택적 convenience path로만 허용한다.

## Container 실행 경계

```text
macOS host
├── Colima + Docker CLI/Compose (Docker Desktop daemon 불필요)
├── make, shasum, Ruby/Psych (현재 foundation check)
└── Obsidian / launchctl / plutil / 실제 device smoke
        │
        ▼
Colima VM
└── knowledgeos-dev / worker container
    ├── Python, uv, project dependencies, vaultctl
    ├── /workspace/control  ← control root
    ├── /workspace/KnowledgeHub  ← 독립 Vault Git root
    └── /workspace/runtime  ← durable queue/receipt/index/log
```

Compose wrapper를 사용하면 host dependency를 추가하지 않고 동일 image로 현재 구현된 test·CLI·validator와 향후 worker·projection·retrieval을 재현할 수 있다. Docker socket, host secret/keychain, SSH agent와 provider network는 기본적으로 container에 노출하지 않는다. 현재 Codex 세션의 직접 접근 surface는 Codex 앱, workspace, Colima/Docker 개발 환경으로 한정하며, Obsidian·Working Copy·LaunchAgent·브라우저 등 외부 앱은 사용자 확인 전 직접 접근하지 않는다. 향후 LaunchAgent가 추가되더라도 실제 KnowledgeOS worker는 container에서 실행되어야 한다. Compose는 현재 호스트 숫자형 UID/GID를 요구하며 `1000:1000` fallback을 사용하지 않는다.

## Vault의 고정 namespace

아래는 실제로 유지할 정적 디렉터리다. 연도·월·프로젝트·job 하위 경로는 데이터가 생길 때 concrete 값으로 만든다.

```text
KnowledgeHub/
├── .knowledgeos-root.json       검증된 Vault identity sentinel (S08B)
├── .vault-bridge/{protocol,requests,responses}
├── 00_Inbox/{Captures,Imports}
├── 01_AI_Review/{Pending,Resolved,Rejected,Expired,Conflict}
├── 10_Journal/{Daily,Weekly,Monthly}
├── 20_Projects
├── 30_Areas
├── 40_Knowledge/{Notes,Ideas,Questions,Sources,People}
├── 50_Maps
├── 60_Meetings
├── 80_Assets/{Inbox,Images,Documents,Audio}
├── 90_Archive/{Projects,Captures,Other}
├── 99_System/{Templates,Bases,Dashboards,Schemas,Scripts/QuickAdd,CSS}
├── .obsidian-mac
├── .obsidian-phone
└── .obsidian-tablet
```

`YYYY`, `MM`, `GGGG`, `PROJECT_NAME`, `JOB_ID`는 문서 표기용 변수이며 literal directory가 아니다.

Git은 빈 디렉토리를 저장하지 않으므로, 현재 비어 있는 일반 canonical namespace에는
`.knowledgeos-directory` 한 줄 구조 표식을 둔다. 표식은 정확한 allowlist의 경로에서만
허용하며 `.gitkeep`, 사용자 note를 가장한 Markdown, `.obsidian-*` profile,
`.vault-bridge` transport, `runtime`에는 사용하지 않는다. 실제 파일이 생기면 그 파일과
함께 디렉토리를 계속 보존하고, 표식은 구조 보조 파일로만 취급한다.

S08B에서 `KnowledgeHub/.knowledgeos-root.json`은 canonical Vault name `KnowledgeHub`, UUID, expected branch `main`, 그리고 credential이 없는 canonical notes remote identity hash를 고정하는 6개 필드 sentinel로 create-only 생성되었다. 민감자료 경계 확인은 sentinel bytes에 저장하지 않고 configure evidence로만 남긴다. 이 sentinel의 존재는 Working Copy/device sync, plugin, bridge round-trip, commit 또는 push 완료를 뜻하지 않는다.

S09의 mobile capture 경로는 control workspace의 `runtime/`이나 production Vault가 아니라 장치 로컬 `On My iPhone/KnowledgeHub-Recovery/Outbox/JOB_ID/` 또는 iPad 대응 경로에 놓이는 recovery outbox를 계약으로만 정의한다. `input.json`은 durable payload hash에 묶이고 event는 append-only이며, remote 관찰 또는 local-only 이관 receipt가 생기기 전에는 삭제하지 않는다. 실제 장치 outbox와 Working Copy transport는 S10 범위이므로 현재 checkout에는 생성하지 않았다.

## 폴더 책임

| 경로 | 책임 | 장기 보관 금지 또는 종료 조건 |
|---|---|---|
| `00_Inbox` | 분류 전 원문과 import | 정식 지식의 영구 보관 금지 |
| `01_AI_Review` | 모델 제안과 변경 preview | 정본으로 인용 금지 |
| `10_Journal` | 시간에 묶인 사건, 로그, 회고 | 장기 지식의 유일한 사본 금지 |
| `20_Projects` | 종료 조건이 있는 결과 중심 작업 | 완료·취소 뒤 bundle archive |
| `30_Areas` | 종료일 없는 책임과 기준 | 책임 소멸 시 retired |
| `40_Knowledge` | 채택한 idea, question, source, claim, person context | raw dump와 무차별 link 금지 |
| `50_Maps` | 사람이 큐레이션한 탐색 경로 | 자동 링크 덤프 금지 |
| `60_Meetings` | 의제, 결정, 후속 작업 | 원격 AI 기본 deny |
| `80_Assets` | 검증한 원본 binary | 모델 직접 수정 금지 |
| `90_Archive` | 끝난 맥락 보존 | 자동 삭제 금지 |
| `99_System` | template, Base, schema, dashboard | LLM write 금지 |

## 노트 데이터 계약

Markdown 본문과 평평한 YAML Properties가 유일한 정본이다. 모든 정식 노트는 다음 공통 키를 가진다.

```text
schema_version, id, type, title, status, created, modified,
aliases, tags, sensitivity, ai_policy, ai_status
```

핵심 불변식은 다음과 같다.

- `id`는 UUID v4 또는 승인된 deterministic system ID이며 불변이다.
- `title`은 파일 basename과 같다.
- datetime은 timezone offset을 포함한다.
- freshness 정렬은 frontmatter `modified`가 아니라 file mtime을 사용한다.
- YAML의 wikilink는 quoted list item이다.
- 관계 역방향을 모든 파일에 복제하지 않고 backlinks와 index에서 계산한다.

일급 note type은 다음과 같다.

```text
capture, proposal, daily, weekly, monthly,
project, project_note, artifact, area,
idea, question, knowledge, source, person, moc, meeting,
home, system
```

정확한 status enum, 필수 추가 property, 허용 path glob은 `blueprint/blueprint.yaml`의 `note_types`가 정본이다.

관계는 두 종류다.

- 맥락: `projects`, `areas`, `topics`, `sources`, `people`, `related`
- 의미: `supports`, `contradicts`, `explains`, `applies_to`, `derived_from`, `implements`, `raises`

승인 전 AI 관계는 proposal에만 존재한다. canonical note와 `edges.jsonl`에는 승인된 정방향 관계만 들어간다.

## Project bundle 계약

```text
20_Projects/<실제 이름>/
├── <실제 이름>.md             type: project
├── Working/                   type: project_note
└── Artifacts/                 type: artifact
```

- 폴더명과 project root basename은 같다.
- project-local note와 artifact는 정확히 하나의 parent project를 가리킨다.
- 재사용할 지식은 project bundle 밖의 `40_Knowledge`에 별도 생성한다.
- 종료 시 bundle 전체를 `90_Archive/Projects/<실제 연도>/`로 옮기되 type과 ID를 유지한다.

## 장치·writer 계약

| 장치 | 역할 | canonical apply | Git 충돌 해결 |
|---|---|---|---|
| iPhone | capture, consult, defer | 금지 | 금지 |
| iPad | read, annotate, light clarify | 금지 | 금지 |
| Mac | decide, connect, execute | interactive only | 담당 |

한 장치에서 여러 background writer를 겹치지 않는다. 모바일의 unique capture는 overwrite하지 않으며, 기존 note 편집은 clean fast-forward sync와 exact-file commit 경계가 필요하다.

## 자동화 경계

- LLM은 source와 candidate set을 읽고 schema-constrained proposal만 만든다.
- 승인 시 source hash, target hash, policy, schema, proposal digest를 다시 묶어 확인한다.
- live Vault 직접 쓰기와 provider silent fallback은 금지한다.
- `.vault-bridge`는 Git으로 이동하는 immutable message transport다.
- worker는 암묵적으로 pull, push 같은 network Git command를 실행하지 않는다.
- `notes.jsonl`과 `edges.jsonl` projection은 같은 Markdown 입력에서 byte-identical하게 재생성한다. lexical/vector index는 삭제 후 재생성 가능해야 하지만 byte-identical 범위로 일반화하지 않는다.

## Privacy 경계

- 일반 note의 보수적 기본값은 `sensitivity: personal`, `ai_policy: ask`다.
- person과 meeting은 기본적으로 `ai_policy: deny`다.
- `confidential + remote_ok` 조합은 금지한다.
- `ask`는 대화형 hash-bound 승인 뒤에만 remote 전송할 수 있다.
- `local_only`와 `deny`는 remote candidate set에서 제외한다.
- `ai_policy`는 모델 전송 정책이며 GitHub 저장 허가가 아니다.
- 이 notes repository profile에는 confidential 자료와 기관·규제상 외부 저장이 제한된 자료를 넣지 않는다. 필요하면 별도 Vault 경계를 먼저 결정한다.
