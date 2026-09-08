# 결정 기록

이 문서는 구조를 바꾸는 결정과 아직 열려 있는 결정을 한 곳에 기록한다. 구현 상세가 달라져도 안정적으로 유지할 계약만 남긴다.

## 확정 결정

### D-001 — 세 개의 물리적 경계

- 상태: accepted
- 날짜: 2026-09-08
- 결정: control workspace, independent Vault repository, device-local runtime을 분리한다.
- 이유: Obsidian index, mobile sync, 자동화 상태의 책임을 섞지 않고 각각 복구할 수 있어야 한다.

### D-002 — 두 개의 독립 Git repository

- 상태: accepted
- 날짜: 2026-09-08
- 결정: control root와 `vault/`는 독립 repository다. `vault/`를 기본 submodule로 만들지 않는다.
- 이유: automation code version과 note corpus version을 독립적으로 복구하고, 모바일은 notes repository만 clone한다.

### D-003 — foundation-only bootstrap

- 상태: accepted
- 날짜: 2026-09-08
- 결정: 이번 세션은 namespace, 계약 문서, 원본 lock, 현재 checkout 구조 검사 도구까지만 구현한다.
- 이유: 빈 placeholder가 실제 schema·template·plugin 설정으로 오인되는 것을 막고 후속 세션이 작은 acceptance 단위로 작업하게 한다.

### D-004 — 설계 패키지 고정

- 상태: accepted
- 날짜: 2026-09-08
- 결정: `knowledgeos-blueprint-v2`의 checksum 확인 bytes를 control workspace에 보존한다.
- 이유: 외부 ChatGPT project mirror가 교체되어도 후속 세션이 같은 계약을 읽을 수 있어야 한다.

### D-005 — Markdown 정본과 proposal-only AI

- 상태: accepted
- 날짜: 2026-09-08
- 결정: Markdown + flat YAML Properties가 정본이며 LLM은 live Vault를 직접 쓰지 않는다.
- 이유: 사람이 검토할 수 있는 기록, hash-bound 승인, index 재생성을 유지하기 위함이다.

## 열려 있는 결정

| ID | 결정할 내용 | 필요한 시점 | 보수적 기본값 |
|---|---|---|---|
| O-001 | notes repository remote와 expected branch | root sentinel / mobile 연결 전 | remote 없음, sentinel 생성 안 함 |
| O-002 | Vault 표시 이름 | URI와 mobile Shortcut 생성 전 | `KnowledgeOS` |
| O-003 | 상시 실행 Mac 사용 | LaunchAgent·round-trip 설계 전 | 비활성 |
| O-004 | mobile immediate API relay | API/credential 설계 전 | 비활성 |
| O-005 | 의료·직장·기관 제한 자료의 별도 Vault | 실제 자료 import 전 | 이 Vault에 저장하지 않음 |
| O-006 | vector/RRF 도입 | lexical 평가 이후 | lexical + typed-link만 사용 |
| O-007 | 큰 binary를 위한 Git LFS 도입 | 실제 asset 크기와 remote 정책 확인 후 | 도입하지 않음 |

열린 결정을 추측해 config나 sentinel에 먼저 기록하지 않는다.
