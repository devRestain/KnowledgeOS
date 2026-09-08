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

### D-006 — Acceptance-gated 다중 세션 구현

- 상태: accepted
- 날짜: 2026-09-08
- 결정: 실제 구현은 `docs/IMPLEMENTATION_PLAN.md`의 작은 capability slice와 release gate로 진행한다. 먼저 production Vault를 수정하지 않는 contract validator/compiler를 만들고, 누적 code capability profile과 독립 deployment overlay를 따로 검증한다.
- 이유: Blueprint Phase 1의 create command와 Phase 4의 전체 `vaultctl` 사이 의존성을 해소하고, remote identity가 없을 때 sentinel을 추측하거나 뒤 단계 파일을 placeholder로 만드는 일을 막기 위함이다.
- 영향: 각 세션은 해당 acceptance와 상태 문서 갱신까지 끝내야 완료된다. offline 구현은 remote/device/provider 없이 진행할 수 있지만 live 사용 완료를 주장하지 않으며, plugin, remote, device, live bridge, Codex provider smoke, LaunchAgent 및 provider lane은 별도 opt-in/overlay로 남는다.

### D-007 — Container-first runtime과 mise 보조

- 상태: accepted
- 날짜: 2026-09-08
- 결정: KnowledgeOS의 canonical 개발·실행·검증은 Colima VM 위 Docker container에서 수행한다. `mise`와 host Python/`uv` 직접 실행은 선택적 convenience path로 허용하되, 프로젝트 dependency와 release evidence의 정본은 동일 `pyproject.toml`/`uv.lock`을 사용하는 container image로 둔다.
- 이유: 호스트에 자동화용 dependency를 반복 설치하지 않고, control/Vault/runtime bind mount와 재현 가능한 image를 유지하기 위함이다. 동시에 가벼운 read-only 작업과 사용자가 이미 가진 host toolchain을 불필요하게 금지하지 않는다.
- 영향: S01은 `mise.toml`, Dockerfile/Compose, version parity, non-root bind mount, cache 분리를 정의한다. Node는 실제 필요 시에만 추가한다. Obsidian/device/`plutil`/`launchctl`은 물리적 macOS 경계로 남지만, LaunchAgent가 실행하는 worker와 기본 CLI/test는 container에서 동작한다.

### D-008 — Container foundation check와 disposable cache

- 상태: accepted
- 날짜: 2026-09-08
- 결정: macOS의 `make verify`를 bootstrap surface로 보존하고, Linux container에서는 Python 기반 `vaultctl foundation check`를 사용해 동일한 path/hash/Git boundary 계약을 검증한다. uv cache는 runtime evidence와 분리된 Colima 내부 named tmpfs volume으로 두고 host UID/GID를 mount 옵션에 반영한다.
- 이유: 기존 foundation script는 macOS의 `shasum`, Ruby/Psych, BSD `stat`에 의존하므로 container에서 그대로 실행할 수 없다. cache는 재생성 가능한 파생 상태인 반면 runtime receipt/queue는 보존해야 하므로, cache의 root ownership과 host cache 누적을 함께 피한다.
- 영향: `make container-source-check`와 `make container-verify`는 portable checker를 canonical container evidence로 사용한다. cache volume 삭제·재생성은 runtime evidence나 Vault bytes를 건드리지 않지만, runtime bind mount는 container 수명보다 오래 보존한다.

### D-009 — S02 Blueprint JSON Schema validator의 read-only 진단 경계

- 상태: accepted
- 날짜: 2026-09-08
- 결정: `vaultctl blueprint validate`는 S01의 safe YAML loader와 Draft 2020-12 JSON Schema validator만 실행한다. 진단은 canonical source/schema/manifest의 SHA-256, contract ID, schema provenance, stable reason code와 RFC 6901 locator를 포함한 deterministic JSON으로 출력한다. JSON Schema PASS는 semantic 또는 generated-artifact PASS로 승격하지 않고 두 deferred 상태를 함께 출력한다.
- 이유: Blueprint를 artifact mutation 전에 검증하면서도 checksum과 진단 결과를 재현하고, 후속 S03A–S03C가 semantic/ownership 규칙을 별도 gate로 추가할 수 있어야 한다.
- 영향: S02 command와 fixture는 `vault/`·`runtime/`을 읽거나 쓰지 않는다. S03A–S03B는 이 결과를 입력으로 재사용하고 cross-document 오류를 별도 reason code로 추가한다. `make blueprint-check`가 canonical container evidence surface다.

### D-010 — S03A semantic gate의 exact registry와 generated-file 비의존 경계

- 상태: accepted
- 날짜: 2026-09-08
- 결정: S03A는 Blueprint 안에 선언된 64개 property, 18개 note type, relation, action, command, phase, acceptance, bridge state/transition/runtime mapping을 canonical exact set과 비교한다. JSON Schema가 허용하는 확장 여지를 semantic gate에서 닫되, 아직 S03C가 소유하지 않은 `ops/actions`, `ops/prompts`, `ops/schemas` 및 Vault artifact의 파일 존재를 성공 조건으로 요구하지 않는다.
- 이유: registry 이름 변경·삭제·추가와 cross-document 방향 오류를 artifact 생성 전에 잡으면서도 S04 이후 산출물을 빈 placeholder로 만들어 현재 단계의 acceptance를 가장하지 않기 위함이다.
- 영향: `vaultctl blueprint validate`는 JSON Schema PASS 후 S03A semantic gate를 실행하고 두 결과와 semantic reason code를 분리한다. S03B는 Base/dashboard/projection/transaction 규칙을 이 gate에 추가하고, S03C는 generated ownership과 zero-diff를 별도 gate로 닫는다.

### D-011 — S03B consumer contract와 atomic lifecycle semantic 경계

- 상태: accepted
- 날짜: 2026-09-08
- 결정: S03B는 Blueprint 안의 8개 Base/14개 view query, Home/Mobile의 10개 source/view/limit consumer 연결, projection record/serialization/publish contract, `NEW_PROJECT` bundle cardinality와 capture-finalize transaction 순서를 canonical expected contract와 exact 비교한다. 검증은 S03A와 같은 read-only semantic gate에 누적하며 generated artifact 파일 존재나 production Vault 변경을 요구하지 않는다.
- 이유: Base와 dashboard가 서로 다른 query를 소비하거나 projection/transaction 단계가 조용히 바뀌면 JSON Schema만으로는 감지할 수 없다. artifact 생성 전 선언된 소비자·수명주기 계약을 닫아야 다음 generation 세션이 안전하게 authoritative input을 가질 수 있다.
- 영향: `make blueprint-check`의 semantic PASS는 S03A/S03B 선언 계약까지를 의미하고, `generated_artifact_validation`은 S03C까지 deferred로 유지한다. S03B negative fixture는 JSON Schema PASS 후 Base/query, dashboard source/view, projection hash/order, bundle cardinality, transaction order drift를 각각 실패시킨다.

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
| O-008 | tracked root sentinel의 Vault UUID | sentinel 생성 전 | UUID를 추측하지 않고 sentinel 생성 안 함 |

열린 결정을 추측해 config나 sentinel에 먼저 기록하지 않는다.
