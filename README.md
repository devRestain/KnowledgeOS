# KnowledgeOS

KnowledgeOS는 메모를 많이 쌓는 저장소가 아니라, 다음 세 질문에 매일 답하기 위한 개인 지식 운영체제다.

1. 지금 가장 밀어야 할 프로젝트는 무엇인가?
2. 무엇을 결정하지 못해 진행이 멈춰 있는가?
3. 오늘 포착하거나 정리해야 할 것은 무엇인가?

현재 저장소는 완성된 Obsidian Vault나 자동화 제품이 아니다. 여러 후속 개발 세션이 같은 구조와 안전 경계를 공유할 수 있도록 만든 **contract-first portable-core scaffold**다.

## 현재 상태

- 2026-09-08: 빈 작업공간에 대한 read-only inventory를 완료했다.
- 체크섬이 확인된 설계 기준안 `knowledgeos-blueprint-v2`를 이 control workspace에 고정했다.
- `docs/`, `ops/`, `KnowledgeHub/`, `runtime/`의 책임과 고정 namespace를 만들었다.
- 현재 구조와 설계 원본의 무결성을 검사하는 얇은 도구를 추가했다.
- S01 개발 runtime과 검증 harness를 추가하고, Colima/Docker의 Python 3.12.8 + uv 0.8.14 image에서 locked sync, test, lint, foundation check를 검증했다.
- S02 Blueprint JSON Schema validator를 추가하고, safe YAML parse와 Draft 2020-12 검증, checksum/contract/schema provenance, deterministic reason code·JSON Pointer 진단을 검증했다.
- S03A semantic validator를 추가하고, registry exactness, path/type/template, relation direction, action/command/output-schema 선언, bridge state/transition/runtime closure를 검증했다.
- S03B semantic validator를 추가하고, 8개 Base/14개 view query, Home/Mobile dashboard 연결, projection serialization 계약, project bundle cardinality와 capture-finalize transaction 순서를 검증했다.
- S03C generated artifact ownership과 zero-diff gate를 추가하고, 당시 `contract_validated` profile이 소유한 5개 policy, trusted Blueprint schema copy, 임시 Property Dictionary를 결정론적으로 생성·검증했다. 뒤 세션 산출물은 명시적으로 `NOT_APPLICABLE_FOR_PROFILE`로 보고했다.
- S04 strict note engine을 추가하고, `portable_core` profile에서 duplicate-key-safe frontmatter, schema-aware writer, 18개 note type, path/property/relation invariant를 검증한다. 동일 generator가 `ops/schemas/note.schema.json`과 `KnowledgeHub/99_System/Schemas/Property_Dictionary.md`를 배포·zero-diff 검증한다.
- S05 exact 16개 template과 비실행 token renderer, additive/idempotent `bootstrap`, atomic create-only project bundle, ISO week·calendar month를 처리하는 Daily/Weekly/Monthly renderer를 추가했다.
- S05 canonical acceptance를 84개 test, lint, Blueprint JSON Schema+semantic, generated artifact zero-diff, container source/foundation gate로 최종 검증했다.
- S06 8개 Base와 canonical 14개 view, Home/Mobile 및 Tasks/Weekly Review navigation surface, CSS/plain-Markdown fallback, frozen fixture evaluator를 추가했다. canonical acceptance는 88개 test와 lint, Blueprint/schema/container gate로 검증했다.
- S07 `guestbook-horror` fixed input/expected Vault, SHA-256/mtime manifest, archive/capture-finalize/asset-provenance golden bytes와 read-only negative fixture gate를 추가했다. S07 전용 canonical acceptance는 당시 95개 test와 lint, Blueprint/schema/container gate로 검증되었고, 현재 누적 acceptance는 S08A를 포함해 101개 test다.
- S07 disposable Vault를 Obsidian v1.9.14에서 열어 Home, 8개 Base, Daily, Mobile과 plugin-free fallback을 확인했고 `portable local Vault` acceptance를 닫았다. S08A는 bridge request/response/root-sentinel schema, 17-state transition, GitHub remote canonicalization/hash, trusted/protocol digest equality와 fixture-only renderer를 추가했고, S08B는 확인된 notes remote/branch preflight와 create-only production sentinel을 추가했다. 현재 canonical acceptance는 105개 test와 lint, Blueprint/schema/container gate로 검증된다.
- 2026-09-09 실행에서 macOS host UID/GID `501:20`과 Compose의 `1000:1000` fallback 불일치로 bind mount/cache permission 오류가 드러났다. `Makefile`은 `id -u`/`id -g`를 자동 export하고 Compose/Dockerfile은 UID/GID 누락을 fail closed하도록 보강했다.
- Codex 실행 세션의 직접 접근 범위는 Codex 앱, 이 workspace, Colima/Docker 개발 환경으로 제한한다. Obsidian·브라우저·Finder·Mail·Calendar·Slack·Teams·Working Copy·Shortcuts 등 외부 애플리케이션은 사용자가 명시적으로 허용한 정확한 앱·대상·효과 범위에서만 접근하며, 그 밖의 작업은 사용자에게 가능 여부와 범위를 확인할 수 있도록 남긴다.
- 사용자가 control repository와 Vault repository를 각각 독립 Git root로 초기화했다. control은 local `main`의 `2c2fd80`에서 `https://github.com/devRestain/KnowledgeOS.git`을 추적하고, Vault는 local `main`의 `440829f`에서 `https://github.com/devRestain/KnowledgeHub.git`의 `origin/main`을 추적한다. S08B 코드·문서와 `KnowledgeHub/.knowledgeos-root.json`은 현재 working tree에 있으며 commit/push는 하지 않았다.
- Codex 작업으로 `KnowledgeHub/.obsidian/app.json`, `appearance.json`, `core-plugins.json`, `workspace.json` baseline이 생성되어 현재 Vault에 존재한다. 이 ignored app-config baseline은 disposable Obsidian smoke evidence와 분리하며, `.obsidian-mac`/`.obsidian-phone`/`.obsidian-tablet` profile 파일과 community plugin은 활성화하지 않았다. canonical Vault 이름은 `KnowledgeHub`, UUID는 `411602c1-5278-4a8b-8b96-9183fb6ef8c2`로 확정했고, S08A protocol schema copy와 S08B production sentinel은 `KnowledgeHub/.vault-bridge/protocol/` 및 `KnowledgeHub/.knowledgeos-root.json`에 있다. S08B에서 `main`의 원격 branch와 canonical identity hash `a8c9310a232c9d41110f113aedb0bcdd6483571db43235109d092bdaa4ba3146`을 확인했으며, request/response event, Working Copy/device config, LLM 및 background worker는 아직 활성화하지 않았다. `vaultctl blueprint validate`는 JSON Schema + S03A/S03B semantic을, `vaultctl schema export --check`는 `portable_core`와 S08A generated artifact zero-diff를, `vaultctl note validate`는 개별 Markdown note contract를, S06 compiler/evaluator와 S07 portable fixture test는 Base/dashboard 및 통합 fixture를, S08A bridge contract와 S08B configure test는 상태·schema·remote identity·sentinel 경계를 각각 검증한다.
- 실제 구현은 [다중 세션 구현 계획](docs/IMPLEMENTATION_PLAN.md)의 acceptance gate에 따라 진행한다.

단계 이름은 두 기준 문서에서 다르게 사용된다. 현재 상태는 다음처럼 해석한다.

- Blueprint §36의 `Phase 0: inventory`: control/notes remote와 expected `main` branch의 identity preflight는 완료했지만 device·sync preflight는 미완료
- Whitepaper §36의 `Phase 1: repository scaffold`: 독립 local Git 초기화를 포함한 기반 완료
- Blueprint §36의 `Phase 1: portable Vault`: S06 Base/dashboard, S07 fixed fixture/portable contract와 disposable app smoke까지 완료; S08A offline bridge contract와 S08B Git identity/sentinel도 완료했지만 Phase 1 전체 또는 live mobile 완료로 부르지 않음

따라서 이 저장소를 “완성된 자동화 제품” 또는 “Phase 1 전체 완료”라고 부르면 안 된다. S07의 `portable local Vault`, S08A의 오프라인 bridge contract, S08B의 Git identity/sentinel overlay만 완료되었다.

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
| `KnowledgeHub/` | Obsidian이 여는 실제 노트 corpus와 `.vault-bridge` | 독립 notes repository; control repository에서는 ignore |
| `runtime/` | queue, receipt, lock, index, cache, log 등 container가 사용하는 persistent local 상태 | Git 추적 금지; 일부 receipt는 durable evidence이므로 단순 cache처럼 삭제하지 않음 |

`KnowledgeHub/`를 control repository의 submodule로 자동 등록하지 않는다. 사용자가 초기화한 두 repository에 대한 후속 commit, remote 설정, push는 자동 수행하지 않는다.

## 기반 명령

현재 foundation check는 추가 패키지 설치 없이 macOS의 POSIX shell, `shasum`, Ruby/Psych로 실행된다. 실제 project dependency와 자동화 runtime은 Colima/Docker 경로를 기본으로 하며, `mise`로 관리한 호스트 Python/`uv` 직접 실행은 같은 lockfile을 사용하는 선택적 편의 경로다.

```bash
make verify
make source-check
make blueprint-check
make schema-export
make schema-check
```

- `make verify`: 고정 경로, 설계 산출물 hash, 금지된 literal placeholder와 symlink를 검사한다. Git이 초기화된 뒤에는 두 root와 control ignore 경계도 검사한다.
- `make source-check`: 고정한 설계 패키지의 원래 checksum manifest를 다시 검사한다.
- `make blueprint-check`: S01 container에서 canonical `blueprint.yaml`을 Draft 2020-12 JSON Schema와 S03A/S03B semantic gate로 read-only 검증한다.
- `make schema-export`: Blueprint 검증 뒤 현재 profile이 소유한 control artifact와 검증된 `KnowledgeHub/99_System/Schemas/Property_Dictionary.md`, S08A protocol schema copy를 생성한다. differing deployed copy는 덮어쓰지 않는다.
- `make schema-check`: 현재 `portable_core`가 소유한 S04/S08A artifact만 `vaultctl schema export --check`로 byte-for-byte 검증하고, 뒤 세션 산출물은 `NOT_APPLICABLE_FOR_PROFILE`로 보고한다. 이 명령은 sentinel, request/response event, `runtime/`을 생성하지 않는다.

## 변하지 않는 원칙

- Markdown 본문과 평평한 YAML Properties가 지식의 정본이다.
- 검색 index, embedding, graph projection은 Markdown에서 재생성 가능한 파생물이다.
- 폴더는 주 수명주기, `type`은 노트의 역할, typed relation은 여러 맥락의 연결을 나타낸다.
- iPhone은 포착, iPad는 읽기·보완, Mac은 의미 결정과 충돌 해결을 담당한다.
- 한 장치에는 한 writer만 둔다.
- LLM은 live Vault를 직접 수정하지 않고 schema-constrained proposal을 만든다.
- 원격 실행, 플러그인 설치, LaunchAgent 등록, Git push, 기존 자료 migration은 각각 별도 승인과 해당 단계 검증이 필요하다.

## 다음 구현 단위

다음 진입은 [구현 계획의 `S09`](docs/IMPLEMENTATION_PLAN.md) offline Shortcut과 durable outbox 작업이다. S07 fixed input/expected Vault와 Home/Bases/Daily/Mobile app smoke, S08A bridge contract와 protocol digest gate, S08B notes remote/branch preflight와 production sentinel은 닫혔다. canonical Vault 이름은 `KnowledgeHub`, UUID는 `411602c1-5278-4a8b-8b96-9183fb6ef8c2`이며 remote identity hash는 `a8c9310a232c9d41110f113aedb0bcdd6483571db43235109d092bdaa4ba3146`로 확정했다.

`.knowledgeos-root.json`은 `KnowledgeHub`에 create-only로 생성되어 있으며, 실제 commit/push는 수행하지 않았다. Working Copy/device transport와 mobile round-trip은 여전히 후속 단계다.
