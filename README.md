# KnowledgeOS

KnowledgeOS는 개인 지식을 안전하게 포착하고, 검토하고, 연결하고, 다시 찾아보기 위한 개인용 Obsidian 작업공간입니다. 단순한 메모 저장소가 아니라, 포착한 생각을 검토 가능한 지식과 실행 가능한 프로젝트로 바꾸는 작은 운영체제를 목표로 합니다.

## 이 프로젝트가 하는 일

자료와 생각은 다음 흐름을 거칩니다.

1. 휴대폰이나 Mac에서 메모·음성·URL·첨부 자료를 포착합니다.
2. 포착물은 원본을 보존한 채 Inbox에 쌓이고, 필요하면 AI 검토 대기열로 보냅니다.
3. 사람이 분류·연결·수정 제안을 확인하고 승인합니다.
4. 승인된 내용만 정식 Markdown 노트와 YAML 속성으로 확정됩니다.
5. 확정된 노트에서 Home 화면, Bases, 검색 색인 같은 파생 화면을 다시 만듭니다.
6. 나중에 검색하거나 질문할 때는 원본 노트와 근거 링크를 따라 답을 확인합니다.

AI는 정식 노트를 대신 결정하지 않습니다. AI가 만든 분류·요약·링크·수정은 제안으로만 남고, 사람의 승인이 있어야 정본에 반영됩니다.

## 현재 위치

S13A(provider-free 진단), S13B(create-only 로컬 명령), S13C(asset import, capture finalize, project archive transaction)가 완료되었습니다. 다음 개발 단위는 S13D(fsynced transaction recovery journal)입니다. 그 뒤에 bridge/Git publish, MacBook Obsidian·plugin 통합, 모바일 왕복 검증을 순서대로 진행합니다.

Blueprint 검증, generated artifact zero-diff, strict note engine, 16개 template, portable Vault fixture, Base/dashboard, offline bridge·recovery outbox, remote identity sentinel, GitHub directory visibility 기반은 이미 마련되어 있습니다. 무엇이 실제로 완료되었는지와 어떤 검증이 미실행인지에 대한 최신 기록은 [`PROJECT_STATE.md`](PROJECT_STATE.md)에서 확인합니다.

## Vault를 이해하는 방법

`KnowledgeHub/`가 Obsidian에서 여는 실제 Vault입니다. 폴더는 정보의 수명과 관리 맥락을 나타내고, 노트의 `type`과 링크는 그 노트가 하는 일과 다른 노트와의 관계를 나타냅니다.

- `00_Inbox/Captures/`: 아직 정리하지 않은 포착물
- `01_AI_Review/`: AI 또는 사람이 검토 중인 제안과 보류 항목
- `20_Projects/`: 끝내야 할 결과를 가진 프로젝트. 각 프로젝트는 관련 파일을 함께 갖는 묶음입니다.
- `30_Areas/`: 계속 관리해야 하는 생활·업무 영역
- `40_Knowledge/`: 장기 보존할 주장과 설명
- `50_Maps/`: 사람이 큐레이션한 탐색 경로와 지도
- `60_Meetings/`: 회의의 의제·결정·후속 작업
- `80_Assets/`: 검증된 원본 파일과 관련 자료
- `90_Archive/`: 끝난 맥락을 보존하는 장소
- `99_System/`: 템플릿, 속성 사전, Bases, 대시보드 등 시스템 파일

Home과 Mobile은 오늘의 핵심·다음 행동·빠른 이동을 보여주는 화면입니다. 검색 색인과 대시보드는 언제든 Markdown에서 다시 만들 수 있는 보조 산출물이지, 원본을 대신하지 않습니다.

## 기기별 역할

| 기기 | 가장 잘하는 일 |
| --- | --- |
| iPhone | 생각·음성·URL을 빠르게 포착하고 오늘의 내용을 확인하기 |
| iPad | 읽기, 주석, 짧은 보완, 가벼운 검토 |
| Mac | 최종 분류·승인·구조 변경·Git 충돌 해결·자동화 실행 |

휴대기기에서 짧게 끝낼 수 없는 구조 변경이나 충돌 해결은 Mac 검토로 넘깁니다. 모바일 Git 사용은 Working Copy Pro, 별도 worktree, Vault 식별 확인, 왕복 검증을 모두 통과한 뒤에만 허용합니다.

## 안전 원칙

- Markdown 본문과 평평한 YAML 속성이 지식의 정본입니다.
- 한 파일을 동시에 쓰는 주체는 하나뿐이며, 기존 파일 수정은 해시 확인과 명시적 승인에 묶입니다.
- AI·plugin·외부 서비스는 선택 사항이며, 정본을 몰래 바꾸거나 비밀을 저장하지 않습니다.
- 사람·회의·기밀 자료는 AI 기본 거부 또는 별도 Vault 경계를 적용합니다.
- iCloud, Obsidian Sync, Dropbox, OneDrive를 canonical 모바일 경로와 병렬 writer로 사용하지 않습니다.
- Control 저장소와 Vault 저장소의 Git 기록은 독립적으로 보존합니다.

## 저장소 경계

| 위치 | 용도 |
| --- | --- |
| 프로젝트 루트 | 정책, 설계 계약, 실행 코드, 테스트를 보관하는 control repository |
| `KnowledgeHub/` | Obsidian 노트와 bridge transport를 보관하는 독립 Vault repository |
| `runtime/` | queue, lock, receipt, journal, index, cache, log를 보관하는 Git 비추적 로컬 상태 |

세 영역은 서로 자동으로 합쳐지지 않습니다. 원격 저장소, plugin, provider, 백그라운드 worker, 기존 자료 이관은 별도의 검토와 승인이 필요한 선택 기능입니다.

## 문서 안내

- [`OBSIDIAN_VAULT_BLUEPRINT.md`](OBSIDIAN_VAULT_BLUEPRINT.md): 에이전트가 읽는 상위 제품·구조·파이프라인 계약 색인
- [`OBSIDIAN_VAULT_WHITEPAPER.md`](OBSIDIAN_VAULT_WHITEPAPER.md): 에이전트가 읽는 저수준 실행·트랜잭션·검증 부속서 색인
- [`AGENTS.md`](AGENTS.md): 이 프로젝트에서 작업할 때 지켜야 할 짧은 실행 계약
- [`docs/`](docs/): architecture, runtime, operations, mobile, decisions, source contract의 간결한 참조 문서
- [`PROJECT_STATE.md`](PROJECT_STATE.md): 현재 단계, 실제 검증 결과, blocker, 다음 작업을 기록하는 에이전트용 상태 문서

Blueprint와 Whitepaper는 사람을 위한 긴 설명서가 아니라, 구조화된 계약을 빠르게 찾기 위한 인덱스입니다. 사용자에게 필요한 배경과 사용법은 이 README에 남깁니다.

## 확인 명령

프로젝트의 기본 구조와 계약을 확인할 때는 다음 명령을 사용합니다.

```bash
make source-check
make verify
make blueprint-check
make schema-check
make test
make lint
```

각 명령은 서로 다른 범위를 검사합니다. 구조·의미·생성 산출물·실행·장치·배포 결과를 하나의 성공으로 합치지 않으며, 실제 실행 여부는 `PROJECT_STATE.md`에 따로 기록합니다.

## 다음에 작업을 시작할 때

먼저 `PROJECT_STATE.md`에서 현재 목표와 증거를 확인하고, 한 번에 하나의 작은 작업 단위를 끝까지 수행합니다. 설계 문서에 적힌 미래 기능을 이미 구현된 것으로 간주하지 말고, Vault 파일·소스·테스트·실행 결과를 함께 확인합니다.
