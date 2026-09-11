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
- 결정: control root와 `KnowledgeHub/`는 독립 repository다. `KnowledgeHub/`를 기본 submodule로 만들지 않는다.
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
- 영향: S02 command와 fixture는 `KnowledgeHub/`·`runtime/`을 읽거나 쓰지 않는다. S03A–S03B는 이 결과를 입력으로 재사용하고 cross-document 오류를 별도 reason code로 추가한다. `make blueprint-check`가 canonical container evidence surface다.

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

### D-012 — S03C explicit artifact ownership과 profile-scoped zero-diff

- 상태: accepted
- 날짜: 2026-09-09
- 결정: generated artifact ownership을 `ops/config/generated-artifacts.yaml`의 explicit allowlist로 고정한다. `contract_validated` profile은 Blueprint가 완전히 결정하는 `properties`, `paths`, `relations`, `privacy`, `retrieval` policy, trusted `blueprint.schema.json` copy, `ops/expected/Property_Dictionary.md`만 소유한다. `KnowledgeHub/` 배포본과 note/bridge/job/proposal/projection schema, prompt, action registry는 owner session 전까지 `NOT_APPLICABLE_FOR_PROFILE`로 보고한다.
- 이유: 하나의 wildcard policy directory나 Whitepaper 예시를 기계 원본으로 오인하면 뒤 단계의 소유권과 생성 입력이 섞인다. explicit path별 source selector와 generator를 고정하면 한 바이트 drift를 재현 가능한 check에서 잡으면서도 아직 승인·구현하지 않은 Vault mutation과 provider/bridge artifact를 만들지 않을 수 있다.
- 영향: `vaultctl schema export`는 Blueprint/semantic validation 후 owned control artifact만 atomic write하고, `vaultctl schema export --check`는 byte-for-byte zero-diff와 future profile의 명시적 N/A 상태를 출력한다. `make blueprint-check`는 Blueprint semantic, `make schema-check`는 generated artifact gate, `make verify`는 bootstrap integrity를 각각 담당한다. S04는 이 policy와 trusted schema copy를 입력으로 note schema와 production Property Dictionary를 소유한다.

### D-013 — S04 portable_core strict note boundary와 제한된 Vault 배포

- 상태: accepted
- 날짜: 2026-09-09
- 결정: `portable_core` profile은 S03C registry를 단일 입력으로 사용해 per-note-type strict schema, duplicate-key-safe frontmatter parser/writer, path/relation validator를 제공한다. generator가 소유하는 production Vault 파일은 `KnowledgeHub/99_System/Schemas/Property_Dictionary.md` 하나로 제한한다.
- 이유: proposal과 projection이 서로 다른 property 해석을 갖지 않게 하면서도, 실제 사용자 note·template·dashboard를 한 번에 생성해 migration 경계를 넘지 않기 위해서다. dictionary와 machine schema는 같은 bytes contract에서 파생되어야 하며, 사용자 변경이 있는 deployed copy는 자동 overwrite하지 않는다.
- 영향: `vaultctl note validate`는 read-only note contract 진단 surface이고, `make schema-export`는 명시된 generated artifact만 생성한다. S05가 template/bootstrap/project create를 추가하기 전까지 실제 note corpus와 sentinel은 생성하지 않는다. 관계는 `canonical_property` provenance와 explicit approval이 없는 proposal 상태로는 정본에 들어갈 수 없다.

### D-014 — S05 template source와 create-only Vault workflow 경계

- 상태: accepted
- 날짜: 2026-09-09
- 결정: 16개 template은 `KnowledgeHub/99_System/Templates`의 사람이 읽을 수 있는 Markdown source로 배포한다. vaultops는 Templater JavaScript를 실행하지 않고 bounded context renderer만 사용한다. `bootstrap`은 전체 target preflight 뒤 누락 directory/template만 additive create하며 기존 파일을 overwrite하지 않는다. `project create`는 schema 검증된 Project root note와 `Working`/`Artifacts` sibling을 create-only bundle로 만든다. Daily/Weekly/Monthly는 Asia/Seoul에서 계산하는 vaultctl renderer가 소유한다.
- 이유: template UX는 Obsidian/QuickAdd를 사용할 때도 이식 가능해야 하고, terminal 경로는 실행 코드나 raw placeholder를 해석해 보안 경계를 넓히면 안 된다. project bundle은 root note와 sibling의 부분 상태를 남기지 않아야 하며, period 생성은 locale/플러그인 동작과 분리된 ISO·calendar 계산이 필요하다.
- 영향: S05는 실제 사용자 note, sentinel, Base/dashboard, plugin, remote, device, LLM을 활성화하지 않는다. `bootstrap --dry-run`이 live Vault mutation 전의 검토 surface이며, 충돌·재실행은 no-overwrite/no-op으로 보고한다.

### D-015 — S06 Base/dashboard compiler와 plugin-free fallback 경계

- 상태: accepted
- 날짜: 2026-09-09
- 결정: 8개 Base와 14개 view의 bytes는 Blueprint `bases` registry에서 `vaultops.base_dashboard`가 결정론적으로 컴파일하고, frozen fixture evaluator와 exact-byte test가 그 결과를 검증한다. Home/Mobile/Tasks/Weekly Review는 Base embed를 사용하되 plain Markdown/wikilink fallback을 함께 제공하며, S06 artifact는 기존 `schema-export` ownership profile과 별도 gate로 유지한다.
- 이유: Base query와 dashboard consumer가 Blueprint와 drift하지 않게 하면서도 Base/plugin 동작이 제한된 환경에서 핵심 navigation, capture, defer 정보를 잃지 않아야 한다. schema-export의 기존 소유 범위를 넓히지 않고 새로운 artifact family의 owner와 acceptance를 명시해야 generated status를 과장하지 않는다.
- 영향: S06은 8개 static Base, dashboard surface, CSS, fixture/evaluator와 additive bootstrap 경계를 소유한다. freshness는 frontmatter `modified`가 아닌 filesystem `file.mtime`을 사용한다. sentinel, `.obsidian-*`, community plugin, remote, 실제 Obsidian smoke는 S07 이후의 별도 opt-in/acceptance로 남긴다.

### D-016 — S07 fixed portable fixture와 앱 smoke 분리

- 상태: accepted with blocked app gate
- 날짜: 2026-09-09
- 결정: S07은 `guestbook-horror`의 fixed input/expected bytes, SHA-256/mtime manifest, NoteEngine·Base evaluator 기반의 read-only integration gate와 negative fixture를 소유한다. archive·capture-finalize·asset import의 실제 mutation writer는 후속 S13C command slice가 소유하고, S07 expected Vault는 그 writer의 golden output contract만 고정한다.
- 이유: Phase 1 exit gate가 note/path/property/link/query와 lifecycle provenance의 concrete evidence를 가져야 하지만, 후속 command 소유권을 앞당겨 구현하면 mutation transaction과 runtime receipt 경계가 섞인다. 고정된 bytes와 mtime을 먼저 검증하면 later writer를 안전하게 비교할 수 있다.
- 영향: `portable_fixture`는 control repository의 synthetic fixture만 읽고 production `KnowledgeHub/`와 `runtime/`을 쓰지 않는다. `materialize_phase1_smoke_vault()`는 `/private/tmp` 같은 disposable 경로에서만 S06 core surface와 fixture를 합성하며 disposable output에는 `.obsidian-*`, `.vault-bridge`, sentinel, plugin을 만들지 않는다. workspace `KnowledgeHub/.obsidian`의 Codex-generated baseline은 이 disposable 경계와 별도로 보존한다. Obsidian restricted-mode smoke는 실제 앱 권한이 허용된 별도 evidence이고, 그 gate가 통과하기 전에는 S07 전체를 complete로 부르지 않는다.

### D-017 — Host numeric UID/GID를 Compose fallback보다 우선

- 상태: accepted
- 날짜: 2026-09-09
- 결정: bind-mounted control/Vault/runtime와 Colima 내부 disposable cache는 현재 호스트의 `id -u`/`id -g`를 `KNOWLEDGEOS_UID`/`KNOWLEDGEOS_GID`로 사용한다. `Makefile`은 이 값을 자동 export하고, Compose는 값이 없으면 `1000:1000`으로 추정하지 않고 fail closed한다. Dockerfile도 UID/GID를 기본값으로 갖지 않으며 숫자형 non-root 값을 요구한다.
- 이유: 실제 macOS 실행 계정 `501:20`과 기존 Compose `1000:1000` fallback이 달라 bind mount/cache permission 오류가 발생했다. 다른 사용자 환경에 특정 숫자를 하드코딩하는 것도 이식 가능한 해결이 아니다.
- 영향: 후속 세션은 `make` wrapper를 canonical 경로로 사용하거나 직접 Compose에 현재 숫자형 UID/GID를 명시해야 한다. cache 삭제·재생성이나 production Vault 권한 변경은 이 결정에 포함되지 않는다.

### D-018 — Codex 실행 세션의 외부 애플리케이션 접근 경계

- 상태: accepted
- 날짜: 2026-09-09
- 결정: Codex가 실행 세션에서 직접 접근할 수 있는 기본 surface는 Codex 앱, 이 workspace, Colima/Docker CLI·Compose·container로 제한한다. Obsidian, 브라우저, Finder, Mail, Calendar, Slack, Teams, Working Copy, Shortcuts와 기타 native/외부 애플리케이션 및 connector는 사용자가 승인한 정확한 앱·대상·효과 범위에서만 접근한다.
- 이유: 현재 Codex 작업 환경을 사용자가 지정한 개발 경계 밖으로 넓히지 않고, 실제 앱/device 확인이 필요한 작업은 사용자 판단과 별도 승인을 거치게 하기 위함이다.
- 영향: 외부 앱 작업이 남으면 필요한 앱·행동·범위를 기록하고 사용자에게 가능한지 확인한다. 사용자 확인 전에는 CUA, 앱 CLI, AppleScript, URI, connector, plugin을 호출하지 않는다. S07은 `/private/tmp/knowledgeos-s07-guestbook-horror` disposable Vault에 대한 사용자 승인 예외로 기록되었다.

### D-019 — Codex-generated Obsidian baseline과 app smoke evidence 분리

- 상태: accepted
- 날짜: 2026-09-11
- 결정: 결정 당시 `KnowledgeHub/.obsidian/app.json`, `appearance.json`, `core-plugins.json`, `workspace.json`은 Codex 작업으로 생성된 ignored workspace baseline으로 보존했다. `KnowledgeHub/.obsidian-mac`, `.obsidian-phone`, `.obsidian-tablet`는 profile 파일 없이 namespace만 유지하고, `.vault-bridge/{protocol,requests,responses}`는 S08A 전까지 empty namespace로 유지했다.
- 이유: 실제 구현에는 Obsidian baseline configuration이 존재하지만, 설정 파일의 존재만으로 Home/Bases/Daily restricted-mode rendering이나 plugin-free fallback이 검증되었다고 볼 수 없다. config artifact와 runtime/app acceptance를 분리해야 상태를 과장하지 않고 다음 agent가 기존 baseline을 덮어쓰지 않는다.
- 영향: 결정 당시 S07 app smoke는 blocked 상태였으며, 다음 smoke는 `/private/tmp/knowledgeos-s07-guestbook-horror` disposable Vault에서만 수행하도록 범위를 고정했다. 현재 smoke 결과와 완료 판정은 D-020에서 갱신한다.

### D-020 — S07 disposable Obsidian smoke 통과와 production 경계 유지

- 상태: accepted
- 날짜: 2026-09-11
- 결정: 사용자 승인으로 `/private/tmp/knowledgeos-s07-guestbook-horror`만 Obsidian v1.9.14에서 열어 Home, 8개 Base, Daily, Mobile 및 fallback surface를 확인한다. workspace `KnowledgeHub/.obsidian` baseline과 profile namespace는 건드리지 않고, disposable Vault의 일반 `.obsidian` 설정과 실제 앱 렌더링 증거를 분리 기록한다.
- 이유: S07의 portable local Vault acceptance는 실제 앱에서 핵심 surface가 열리고 읽히는 것까지 필요하지만, 이 증거가 mobile sync, bridge, plugin, sentinel, remote 활성화를 뜻하지 않기 때문이다.
- 영향: S07은 `portable local Vault`로 complete이며 S08A 진입을 허용한다. 실제 device/sync와 production topology는 별도 사용자 입력 및 deployment overlay로 남긴다.

### D-021 — S08A bridge contract의 control-side ownership과 fixture-only write boundary

- 상태: accepted
- 날짜: 2026-09-11
- 결정: S08A는 Blueprint `/bridge`와 root-sentinel contract에서 strict request/response/root-sentinel schema를 생성하고, trusted control schema와 동일한 protocol schema copy만 `KnowledgeHub/.vault-bridge/protocol/`에 배포한다. fixture renderer는 명시적 fixture root 안에서만 create-only write를 허용하며 production `.vault-bridge/requests`·`responses`와 sentinel에는 쓰지 않는다.
- 이유: schema와 상태 전이를 remote/device 없이 먼저 고정해야 하고, protocol copy를 importer의 authority로 오인하지 않으면서 response publishing과 Git history 검증을 후속 S14 범위에 둘 수 있기 때문이다.
- 영향: `portable_core` schema-export profile에 S08A first-capability artifact ownership을 추가하고, trusted/protocol digest equality, 17-state transition, remote canonicalization/hash, create-only/quarantine, deterministic fixture evidence를 별도 테스트한다. S08B의 실제 remote·branch 확인 전에는 `.knowledgeos-root.json`을 만들지 않는다. canonical Vault name `KnowledgeHub`와 UUID `411602c1-5278-4a8b-8b96-9183fb6ef8c2`는 S08B 전제값으로 별도 확정했다.

### D-022 — Vault root rename과 선행 UUID 선택

- 상태: accepted
- 날짜: 2026-09-11
- 결정: physical Vault Git root 디렉터리 이름을 `vault`에서 `KnowledgeHub`로 변경하고, `KnowledgeHub/.git`와 기존 Vault content를 보존한다. Blueprint의 Vault root, canonical 표시 이름, mobile path, Obsidian URI, Compose mount, ignore rule, foundation verifier, generated artifact 경로를 모두 `KnowledgeHub`에 맞춘다. project/workspace 이름 `KnowledgeOS`와 logical repository role key `vault`는 유지한다. 새 Vault UUID v4는 `411602c1-5278-4a8b-8b96-9183fb6ef8c2`로 선택하지만 remote/expected branch가 없으므로 `.knowledgeos-root.json`에는 아직 쓰지 않는다.
- 이유: 물리 디렉터리 이름과 Obsidian/모바일에서 사용하는 canonical Vault 표시 이름이 다르면 URI와 Working Copy external worktree가 다른 대상을 가리킬 수 있다. root rename을 generator와 검증기까지 함께 반영해야 수동 rename으로 인한 경로 drift를 막을 수 있다.
- 영향: 두 Git root는 계속 독립이며 remote, commit, push, device, plugin, request/response event는 활성화하지 않는다. `KnowledgeHub` rename 후 `make schema-export`, `make schema-check`, foundation/source/Blueprint/container/test/lint gate로 새 경계를 재검증한다.

### D-023 — S08B 확인된 notes identity와 create-only production sentinel

- 상태: accepted
- 날짜: 2026-09-11
- 결정: 사용자가 `KnowledgeHub` notes remote를 `https://github.com/devRestain/KnowledgeHub.git`, expected branch를 `main`으로 확인했고, 민감자료 경계도 전체 Vault 동기화 허용으로 확인했다. canonical identity는 `github.com/devrestain/knowledgehub.git\n`, SHA-256은 `a8c9310a232c9d41110f113aedb0bcdd6483571db43235109d092bdaa4ba3146`이다. local `main`, `origin/main` tracking ref와 GitHub `main` remote ref가 같은 commit `440829fa8d1c01aaaced54d9469dd692910ab10d`임을 read-only preflight했다. 이 값으로 `KnowledgeHub/.knowledgeos-root.json`을 6개 allowlisted field만 가진 canonical JSON으로 create-only 기록했다.
- 이유: Working Copy와 이후 bridge가 control workspace나 다른 GitHub repository를 notes repository로 오인하지 않도록 remote identity, branch, canonical name, UUID를 같은 tracked sentinel bytes에 고정해야 한다. 민감자료 경계는 sentinel schema에 넣지 않고 사용자 확인 evidence로만 남겨 secret·정책 문자열의 transport를 늘리지 않는다.
- 영향: `git_identity_configured` overlay는 verified다. sentinel은 working tree에 추가되었지만 commit/push는 수행하지 않았고, 기존 sentinel의 다른 bytes는 configure가 덮어쓰지 않는다. Working Copy, device credential/sync, Shortcut, plugin, live bridge round-trip은 후속 단계다.

## 열려 있는 결정

| ID | 결정할 내용 | 필요한 시점 | 보수적 기본값 |
|---|---|---|---|
| O-001 | notes repository remote와 expected branch | S08B 완료 | `KnowledgeHub.git`, `main`, canonical identity hash `a8c9310a232c9d41110f113aedb0bcdd6483571db43235109d092bdaa4ba3146` 검증 완료 |
| O-002 | Vault 표시 이름 | URI와 mobile Shortcut 생성 전 | `KnowledgeHub`로 확정 |
| O-003 | 상시 실행 Mac 사용 | LaunchAgent·round-trip 설계 전 | 비활성 |
| O-004 | mobile immediate API relay | API/credential 설계 전 | 비활성 |
| O-005 | 의료·직장·기관 제한 자료의 별도 Vault | 실제 자료 import 전 | 이 Vault에 저장하지 않음 |
| O-006 | vector/RRF 도입 | lexical 평가 이후 | lexical + typed-link만 사용 |
| O-007 | 큰 binary를 위한 Git LFS 도입 | 실제 asset 크기와 remote 정책 확인 후 | 도입하지 않음 |
| O-008 | tracked root sentinel의 Vault UUID | S08B 완료 | `411602c1-5278-4a8b-8b96-9183fb6ef8c2`를 `KnowledgeHub/.knowledgeos-root.json`에 기록 완료 |

열린 결정을 추측해 config나 sentinel에 먼저 기록하지 않는다.
