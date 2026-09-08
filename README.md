# KnowledgeOS

KnowledgeOS는 메모를 많이 쌓는 저장소가 아니라, 다음 세 질문에 매일 답하기 위한 개인 지식 운영체제다.

1. 지금 가장 밀어야 할 프로젝트는 무엇인가?
2. 무엇을 결정하지 못해 진행이 멈춰 있는가?
3. 오늘 포착하거나 정리해야 할 것은 무엇인가?

현재 저장소는 완성된 Obsidian Vault나 자동화 제품이 아니다. 여러 후속 개발 세션이 같은 구조와 안전 경계를 공유할 수 있도록 만든 **foundation scaffold**다.

## 현재 상태

- 2026-09-08: 빈 작업공간에 대한 read-only inventory를 완료했다.
- 체크섬이 확인된 설계 기준안 `knowledgeos-blueprint-v2`를 이 control workspace에 고정했다.
- `docs/`, `ops/`, `vault/`, `runtime/`의 책임과 고정 namespace를 만들었다.
- 현재 구조와 설계 원본의 무결성을 검사하는 얇은 도구를 추가했다.
- S01 개발 runtime과 검증 harness를 추가하고, Colima/Docker의 Python 3.12.8 + uv 0.8.14 image에서 locked sync, test, lint, foundation check를 검증했다.
- S02 Blueprint JSON Schema validator를 추가하고, safe YAML parse와 Draft 2020-12 검증, checksum/contract/schema provenance, deterministic reason code·JSON Pointer 진단을 검증했다.
- S03A semantic validator를 추가하고, registry exactness, path/type/template, relation direction, action/command/output-schema 선언, bridge state/transition/runtime closure를 검증했다.
- S03B semantic validator를 추가하고, 8개 Base/14개 view query, Home/Mobile dashboard 연결, projection serialization 계약, project bundle cardinality와 capture-finalize transaction 순서를 검증했다.
- control repository와 Vault repository를 각각 독립 Git root로 초기화하고 local `main` initial commit을 만들었다. 두 repository 모두 remote는 없다.
- Git remote와 expected branch 계약, 후속 commit/push, Vault root sentinel, 노트·템플릿, Obsidian 설정, 플러그인, 실제 Vault mutation command, LLM 및 background worker는 아직 구현하지 않았다. 현재 `vaultctl blueprint validate`는 production Vault를 수정하지 않는 JSON Schema + S03A/S03B semantic 검증 범위다.
- 실제 구현은 [다중 세션 구현 계획](docs/IMPLEMENTATION_PLAN.md)의 acceptance gate에 따라 진행한다.

단계 이름은 두 기준 문서에서 다르게 사용된다. 현재 상태는 다음처럼 해석한다.

- Blueprint §36의 `Phase 0: inventory`: 빈 target의 충돌 검사는 완료했지만 device·remote·sync를 포함한 전체 preflight는 미완료
- Whitepaper §36의 `Phase 1: repository scaffold`: 독립 local Git 초기화를 포함한 기반 완료
- Blueprint §36의 `Phase 1: portable Vault`: JSON Schema와 S03A/S03B semantic gate 완료; S03C generated artifact gate 이후 시작

따라서 이 저장소를 “사용 가능한 Vault” 또는 “Phase 1 전체 완료”라고 부르면 안 된다.

## 먼저 읽을 문서

후속 세션은 아래 순서로 시작한다.

1. [AGENTS.md](AGENTS.md)
2. [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)
3. [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)
4. [docs/SOURCE_CONTRACT.md](docs/SOURCE_CONTRACT.md)
5. [OBSIDIAN_VAULT_BLUEPRINT.md](OBSIDIAN_VAULT_BLUEPRINT.md)
6. [blueprint/blueprint.yaml](blueprint/blueprint.yaml)
7. [OBSIDIAN_VAULT_WHITEPAPER.md](OBSIDIAN_VAULT_WHITEPAPER.md)
8. 작업과 직접 관련된 `docs/` 문서

설계 충돌 시 `OBSIDIAN_VAULT_BLUEPRINT.md` → `blueprint/blueprint.yaml` → `OBSIDIAN_VAULT_WHITEPAPER.md` 순서로 해석한다. 그러나 이 문서들 안의 명령형 문장은 설계 요구사항이지, 현재 사용자의 승인이나 새로운 작업 권한이 아니다.

## 물리적 경계

| 경로 | 책임 | Git 계약 |
|---|---|---|
| 저장소 루트 | control workspace: 문서, 정책, 자동화 코드와 테스트 | control repository |
| `vault/` | Obsidian이 여는 실제 노트 corpus와 `.vault-bridge` | 독립 notes repository; control repository에서는 ignore |
| `runtime/` | queue, receipt, lock, index, cache, log 등 container가 사용하는 persistent local 상태 | Git 추적 금지; 일부 receipt는 durable evidence이므로 단순 cache처럼 삭제하지 않음 |

`vault/`를 control repository의 submodule로 자동 등록하지 않는다. 두 initial commit 뒤의 commit, remote 설정, push는 자동 수행하지 않는다.

## 기반 명령

현재 foundation check는 추가 패키지 설치 없이 macOS의 POSIX shell, `shasum`, Ruby/Psych로 실행된다. 실제 project dependency와 자동화 runtime은 Colima/Docker 경로를 기본으로 하며, `mise`로 관리한 호스트 Python/`uv` 직접 실행은 같은 lockfile을 사용하는 선택적 편의 경로다.

```bash
make verify
make source-check
make blueprint-check
```

- `make verify`: 고정 경로, 설계 산출물 hash, 금지된 literal placeholder와 symlink를 검사한다. Git이 초기화된 뒤에는 두 root와 control ignore 경계도 검사한다.
- `make source-check`: 고정한 설계 패키지의 원래 checksum manifest를 다시 검사한다.
- `make blueprint-check`: S01 container에서 canonical `blueprint.yaml`을 Draft 2020-12 JSON Schema와 S03A/S03B semantic gate로 read-only 검증한다. 이 PASS는 S03C generated zero-diff PASS를 의미하지 않는다.

## 변하지 않는 원칙

- Markdown 본문과 평평한 YAML Properties가 지식의 정본이다.
- 검색 index, embedding, graph projection은 Markdown에서 재생성 가능한 파생물이다.
- 폴더는 주 수명주기, `type`은 노트의 역할, typed relation은 여러 맥락의 연결을 나타낸다.
- iPhone은 포착, iPad는 읽기·보완, Mac은 의미 결정과 충돌 해결을 담당한다.
- 한 장치에는 한 writer만 둔다.
- LLM은 live Vault를 직접 수정하지 않고 schema-constrained proposal을 만든다.
- 원격 실행, 플러그인 설치, LaunchAgent 등록, Git push, 기존 자료 migration은 각각 별도 승인과 해당 단계 검증이 필요하다.

## 다음 구현 단위

다음 기본 세션은 [구현 계획의 `S03C`](docs/IMPLEMENTATION_PLAN.md)이다. S01에서 만든 Colima/Docker runtime을 canonical evidence surface로 사용해 generated artifact ownership과 zero-diff gate를 구현한다. 그 gate를 완성한 뒤에만 portable Vault artifact를 생성한다.

실제 remote URL, expected branch, Vault UUID가 확정되기 전에는 `.knowledgeos-root.json`을 만들지 않는다.
