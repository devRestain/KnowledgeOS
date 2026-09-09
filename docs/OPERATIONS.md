# 운영 및 단계 계약

## 현재 foundation 작업

현재 단계는 구조와 경계를 재현할 수 있게 만드는 데까지만 책임진다.

```bash
make source-check   # 현재 foundation: host built-in으로 고정한 설계 패키지 checksum
make verify         # 현재 foundation: host built-in foundation acceptance
```

`make verify`는 현재 고정 namespace와 설계 원본의 bootstrap 무결성을 확인하고 generated validator가 사용 가능함을 보고한다. JSON Schema와 S03A/S03B semantic gate는 `make blueprint-check`가, S03C ownership/zero-diff는 `make schema-check`가 수행한다. 두 검사는 모두 production Vault를 수정하지 않는다.

`S01` 이후 일상적인 canonical 경로는 `make container-source-check`, `make container-verify`, `make test`, `make vaultctl`로 전환한다. 기존 `make source-check`/`make verify`는 추가 설치가 필요 없는 bootstrap 진단으로 계속 보존한다.

## 개발·실행 환경

기본 project runtime은 Colima VM 위 Docker container다. 호스트 Python/`uv`는 사용자가 원할 때 직접 실행할 수 있지만, 기본값은 프로젝트 dependency를 host 전용 `.venv`로 따로 관리하지 않는 것이다. 직접 실행을 명시적으로 선택하면 동일 `uv.lock`에서 분리 환경을 만들 수 있으나 non-canonical로 기록한다.

- 호스트: Colima와 Docker CLI/Compose(Docker Desktop daemon 불필요), 선택적 `mise`/Python/`uv`, `make`와 현재 foundation check에 필요한 macOS built-in
- container: Python/`uv`, locked dependency, `vaultctl`, worker, test/lint, projection, retrieval, synthetic Git
- bind mount: control root, 독립 `vault/`, durable `runtime/`
- named volume: uv/build cache만 Colima 내부에 보관; receipt·queue·journal은 bind mount에 보존
- 기본 실행: `make test`, `make vaultctl`, `make worker`, `make blueprint-check`, `make schema-check`가 Compose wrapper를 호출
- 기본 격리: Docker socket, host secret/keychain, SSH agent, provider network는 비활성
- `mise`: host Python/`uv`/필요 시 Node의 버전 shim을 관리하는 선택적 도구. `mise.toml`과 image version parity를 확인하고 global `pip`/`npm` 설치는 사용하지 않는다.

호스트에서 직접 실행한 결과도 진단용으로는 허용하지만, 세션의 canonical acceptance evidence에는 image digest, container command, mount profile을 남긴다. Obsidian GUI, device/Working Copy, `plutil`, `launchctl`은 container로 대체할 수 없는 별도 macOS/device smoke 예외다. LaunchAgent도 host wrapper만 실행하고 실제 worker는 container에서 실행한다.

## 단계별 gate

| 단계 | 산출물 | 진입 조건 | 현재 |
|---|---|---|---|
| Inventory | 기존 Vault, Git, sync, property, plugin 조사 | target read-only 접근 | 빈 target 확인 완료; device·remote·sync 전체 preflight 미완료 |
| Repository foundation | docs, namespace, 독립 Git boundary | inventory에 충돌 없음 | 두 독립 local `main` repository와 initial commit까지 완료; remote 없음 |
| Blueprint contract | JSON Schema, S03A/S03B semantic gate, generated ownership/zero-diff | `make blueprint-check`, `make schema-check`, negative mutation fixture | S03C 완료 |
| Portable Vault | schema, 16 templates, 8 Bases, Home/Mobile, fixture | containerized blueprint full validation 도구 | 미착수 |
| Git/mobile baseline | root sentinel, Working Copy, Shortcuts, bridge protocol | remote·branch·device 확인 | 미착수 |
| Mac plugin profile | Core + 최소 community plugins | 실제 Obsidian smoke 가능 | 미착수 |
| `vaultctl` non-LLM | doctor, create, period, ingest, reconcile | container image와 path/schema/transaction tests | 미착수 |
| Read-only LLM | triage proposal, review/apply receipts | privacy·hash·candidate tests | 미착수 |
| Retrieval | lexical + typed-link, cited answer | deterministic projection | 미착수 |
| Background/remote | LaunchAgent, optional API/relay | 별도 opt-in과 recovery drill | 미착수 |

세션별 상세 순서와 acceptance는 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)를 따른다.

## Git 운영 경계

이 workspace에는 다음 두 개의 독립 local repository가 있다.

```text
control: KnowledgeOS/.git
notes:   KnowledgeOS/vault/.git
```

- control은 `vault/`와 `runtime/`을 추적하지 않는다.
- notes repository는 Vault 내용만 추적한다.
- 두 repository 모두 local `main` initial commit이 있다. 현재 working tree 상태는 `docs/IMPLEMENTATION_STATUS.md`와 각 세션의 종료 증거에서 별도로 기록한다.
- 두 repository 모두 remote/upstream이 없다. local `main`은 notes `expected_branch` 계약을 자동 확정하지 않는다.
- 후속 commit, remote 추가, push, submodule 등록은 아직 승인되거나 수행되지 않았다.
- notes remote와 expected branch를 확인하기 전에는 임의 branch를 계약으로 고정하지 않는다.
- 두 repository를 commit할 때는 exact staged set을 각각 보여 주고 별도로 승인받는다.
- 기존 Git history가 들어오면 재초기화하거나 force push하지 않고 additive migration plan을 먼저 작성한다.

## Portable Vault를 구현할 때의 순서

1. 고정 blueprint를 JSON Schema와 cross-validator로 검증한다.
2. common Properties와 type registry를 기계 판독 schema로 생성한다.
3. 16개 template sample을 만든 뒤 모두 note schema로 검사한다.
4. 8개 Base와 Home, Mobile, Tasks, Weekly Review를 생성한다.
5. project bundle create/archive와 capture finalize를 transaction fixture로 검증한다.
6. plugin-free restricted mode에서 Markdown과 핵심 link가 읽히는지 확인한다.
7. 실제 Obsidian 설치본이 있을 때만 Base, CLI, config 형식을 smoke test한다.

빈 template, 빈 `.base`, 추측한 plugin JSON을 “구현됨” 표시용으로 먼저 만들지 않는다.

## 별도 사용자 입력이 필요한 값

- notes repository remote URL과 canonical fingerprint
- expected branch
- tracked sentinel에 넣을 Vault UUID
- canonical Vault 표시 이름을 `KnowledgeOS`로 유지할지 여부
- 상시 실행 Mac 사용 여부
- mobile relay 또는 immediate API 사용 여부
- 의료·직장·기관 제한 자료를 별도 Vault로 분리할지 여부

이 값이 정해지기 전에도 local schema와 fixture 개발은 가능하지만 root sentinel, mobile round-trip, remote automation 완료를 주장할 수 없다.

## 검증 순서

각 후속 세션은 다음 순서로 증거를 남긴다.

1. `make source-check`
2. `make verify`
3. `S01` 이후에는 `make container-source-check`, `make container-verify`를 canonical container surface에서 실행
4. S02 이후에는 `make blueprint-check`와 변경한 unit/negative fixture를 실행
5. S03C 이후에는 `make schema-check`를 실행
6. 두 repository의 `git status --short --branch`
7. 해당 단계에서만 필요한 실제 앱·device smoke

Obsidian/Working Copy/plugin/Codex CLI 세부 동작은 변할 수 있으므로 해당 단계에서 공식 문서와 실제 설치본을 다시 확인한다.
