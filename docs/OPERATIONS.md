# 운영 및 단계 계약

## 현재 foundation 작업

현재 단계는 구조와 경계를 재현할 수 있게 만드는 데까지만 책임진다.

```bash
make source-check   # 현재 foundation: host built-in으로 고정한 설계 패키지 checksum
make verify         # 현재 foundation: host built-in foundation acceptance
```

`make verify`는 현재 고정 namespace와 설계 원본의 bootstrap 무결성을 확인하고 generated validator가 사용 가능함을 보고한다. JSON Schema와 S03A/S03B semantic gate는 `make blueprint-check`가, S04/S08A `portable_core` ownership/zero-diff는 `make schema-check`가 수행한다. S06 Base/dashboard는 `vaultops.base_dashboard` compiler와 frozen evaluator test가 소유하며 schema-export ownership profile에는 포함하지 않는다. `make schema-export`는 명시된 schema/Property Dictionary와 S08A protocol schema copy를 생성하며, sentinel·request·response event·runtime은 생성하지 않는다.

`S01` 이후 일상적인 canonical 경로는 `make container-source-check`, `make container-verify`, `make test`, `make vaultctl`로 전환한다. 기존 `make source-check`/`make verify`는 추가 설치가 필요 없는 bootstrap 진단으로 계속 보존한다.

## 개발·실행 환경

기본 project runtime은 Colima VM 위 Docker container다. 호스트 Python/`uv`는 사용자가 원할 때 직접 실행할 수 있지만, 기본값은 프로젝트 dependency를 host 전용 `.venv`로 따로 관리하지 않는 것이다. 직접 실행을 명시적으로 선택하면 동일 `uv.lock`에서 분리 환경을 만들 수 있으나 non-canonical로 기록한다.

- 호스트: Colima와 Docker CLI/Compose(Docker Desktop daemon 불필요), 선택적 `mise`/Python/`uv`, `make`와 현재 foundation check에 필요한 macOS built-in
- container: Python/`uv`, locked dependency, `vaultctl`, worker, test/lint, projection, retrieval, synthetic Git
- bind mount: control root, 독립 `KnowledgeHub/`, durable `runtime/`
- named volume: uv/build cache만 Colima 내부에 보관; receipt·queue·journal은 bind mount에 보존
- 기본 실행: `make test`, `make vaultctl`, `make worker`, `make blueprint-check`, `make schema-export`, `make schema-check`가 Compose wrapper를 호출
- 기본 격리: Docker socket, host secret/keychain, SSH agent, provider network는 비활성
- `mise`: host Python/`uv`/필요 시 Node의 버전 shim을 관리하는 선택적 도구. `mise.toml`과 image version parity를 확인하고 global `pip`/`npm` 설치는 사용하지 않는다.

호스트에서 직접 실행한 결과도 진단용으로는 허용하지만, 세션의 canonical acceptance evidence에는 image digest, container command, mount profile을 남긴다. 현재 Codex 실행 세션의 직접 접근 허용 범위는 Codex 앱, 이 workspace, Colima/Docker 개발 환경으로 한정한다. Obsidian GUI, 브라우저, Finder, Mail, Calendar, Slack, Teams, Working Copy, Shortcuts, `plutil`, `launchctl`과 같은 외부 앱·connector는 사용자가 명시적으로 허용한 정확한 앱·대상·효과 범위에서만 접근한다. 해당 smoke가 필요하면 필요한 앱·행동·범위를 사용자에게 확인할 수 있도록 남기고, 사용자 확인 전에는 CUA·앱 CLI·AppleScript·connector를 호출하지 않는다.

### Host UID/GID와 bind mount

`Makefile`은 실행 시점의 `id -u`와 `id -g`를 `KNOWLEDGEOS_UID`/`KNOWLEDGEOS_GID`로 export한다. `ops/compose.yaml`은 이 두 값이 없으면 `1000:1000`으로 fallback하지 않고 즉시 실패하며, disposable uv cache의 tmpfs mount option과 container `user`에도 동일한 값을 사용한다. `ops/Dockerfile`도 UID/GID 기본값을 갖지 않고 숫자형 non-root UID를 요구한다.

2026-09-09 실제 실행에서 macOS 계정은 `501:20`이었지만 직접 Compose 경로의 `1000:1000` fallback이 남아 있어 bind mount/cache permission 오류가 드러났다. cache volume은 삭제·재생성하지 않았고, 이후 canonical Compose 검증은 `KNOWLEDGEOS_UID=501 KNOWLEDGEOS_GID=20`을 명시해 통과했다. 후속 세션은 `make`를 우선 사용하고, 직접 Compose를 호출할 때도 현재 호스트 숫자형 UID/GID를 명시한다.

## 단계별 gate

| 단계 | 산출물 | 진입 조건 | 현재 |
|---|---|---|---|
| Inventory | 기존 Vault, Git, sync, property, plugin 조사 | target read-only 접근 | 빈 target 확인 완료; device·sync 전체 preflight 미완료 |
| Repository foundation | docs, namespace, 독립 Git boundary | inventory에 충돌 없음 | 사용자가 초기화한 두 독립 local `main` repository; control은 `KnowledgeOS.git`, notes는 `KnowledgeHub.git`을 추적하며 S09 update set이 working tree에 있음 |
| Blueprint contract | JSON Schema, S03A/S03B semantic gate, generated ownership/zero-diff | `make blueprint-check`, `make schema-check`, negative mutation fixture | S03C 완료 |
| Portable Vault | strict note schema, Property Dictionary, 16 templates, create-only project/period workflow, 8 Bases/Home/Mobile, S07 fixed integration fixture, disposable Obsidian smoke, Codex-generated `.obsidian` baseline | S04-S07 offline contract와 containerized/app validation | S07 `portable local Vault` complete |
| Git/mobile baseline | root sentinel, Working Copy, Shortcuts, bridge protocol | remote·branch·device 확인 | S08B Git identity/sentinel과 S09 control-side Shortcut/outbox contract complete; Working Copy 연동은 사용자 확인, GitHub visibility slice `a44c70c` 완료; 장치별 acceptance는 진행 중 |
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
notes:   KnowledgeOS/KnowledgeHub/.git
```

- control은 `KnowledgeHub/`와 `runtime/`을 추적하지 않는다.
- notes repository는 Vault 내용만 추적한다.
- 두 repository 모두 local `main` initial commit이 있다. control은 `https://github.com/devRestain/KnowledgeOS.git`, notes는 `https://github.com/devRestain/KnowledgeHub.git`을 추적한다.
- S08B read-only preflight에서 notes `main` remote ref가 존재하고 local `HEAD` 및 `origin/main`과 `440829fa8d1c01aaaced54d9469dd692910ab10d`로 일치함을 확인했다. 이후 GitHub directory visibility exact set을 `a44c70c0ce4c41491195e0e721bfbb718ce17c4c`로 별도 commit/push했다.
- submodule 등록은 수행하지 않았다. S08B sentinel은 create-only로 추가된 뒤 이번 visibility commit에 포함되어 원격 `main`에 반영됐다.
- notes canonical remote identity는 `github.com/devrestain/knowledgehub.git\n`이며 SHA-256은 `a8c9310a232c9d41110f113aedb0bcdd6483571db43235109d092bdaa4ba3146`이다.
- 두 repository를 commit할 때는 exact staged set을 각각 보여 주고 별도로 승인받는다.
- 기존 Git history가 들어오면 재초기화하거나 force push하지 않고 additive migration plan을 먼저 작성한다.

## Portable Vault를 구현할 때의 순서

1. 고정 blueprint를 JSON Schema와 cross-validator로 검증한다.
2. common Properties와 type registry를 기계 판독 schema로 생성한다.
3. 16개 template sample을 만든 뒤 모두 note schema로 검사한다.
4. `bootstrap --dry-run` exact path를 검토한 뒤 누락 template/directory만 additive 생성한다.
5. project root와 `Working`/`Artifacts` sibling을 create-only transaction으로 만들고, Daily/Weekly/Monthly period target을 결정론적으로 생성한다.
6. 8개 Base와 Home, Mobile, Tasks, Weekly Review를 생성한다.
7. S07 fixed input/expected Vault와 hash/mtime manifest, archive/capture-finalize/asset-provenance golden bytes를 read-only로 검증한다.
8. project bundle create/archive와 capture finalize의 실제 mutation writer는 후속 S13C에서 구현하고, S07은 expected bytes만 고정한다.
9. plugin-free restricted mode에서 Markdown과 핵심 link가 읽히는지 확인한다.
10. 실제 Obsidian 설치본이 있고 UI 권한이 허용될 때만 Base, Home, Daily와 config 형식을 smoke test한다.

## S06 Base/dashboard gate

S06 정적 `.base`와 dashboard Markdown은 Blueprint 선언에서 파생되지만 S04 `schema-export` artifact가 아니다. 따라서 다음 두 검증을 함께 기록한다.

- `make test`: `test_s06_dashboard.py`가 8개 Base의 compiler exact match, frozen fixture limit/order, `file.mtime` freshness, dashboard source bytes, bootstrap no-overwrite를 확인한다.
- `make blueprint-check`와 `make schema-check`: Blueprint semantic contract와 기존 `portable_core` generated artifact zero-diff를 각각 확인한다. `schema-check`의 PASS 또는 `generated_artifact_validation` 상태를 S06 Base/dashboard 배포 완료로 확대 해석하지 않는다.

빈 template, 빈 `.base`, 추측한 plugin JSON을 “구현됨” 표시용으로 먼저 만들지 않는다.

## S07 fixed fixture gate

`ops/tests/fixtures/s07_portable_vault/guestbook-horror/`는 S06 surface 위에서 portable local Vault의 offline evidence를 고정한다. `portable_fixture`는 다음 순서로 동작한다.

1. manifest의 exact file set과 SHA-256을 확인한다.
2. materialized copy에서만 fixed mtime을 적용하고, checked-in source의 checkout mtime을 신뢰하지 않는다.
3. NoteEngine으로 note type/path/property/relation을 검증하고, fixture 전체 ID 중복과 wikilink heading/block locator를 확인한다.
4. S06 Base evaluator로 `Home` 소비자에 해당하는 canonical view 결과가 expected path와 같은지 확인한다.
5. expected archive/finalize/provenance bytes를 별도 tree로 검사한다. 이 tree는 mutation command나 receipt가 아니다.

negative fixture는 invalid relation, duplicate ID, missing locator를 각각 fail closed한다. actual app smoke는 disposable Vault에서만 시도했고 S07은 complete다. 현재 workspace `KnowledgeHub/.obsidian/{app.json,appearance.json,core-plugins.json,workspace.json}`은 Codex 작업으로 생성된 ignored baseline이며 disposable app evidence와 분리한다. `.obsidian-mac`/`.obsidian-phone`/`.obsidian-tablet`에는 profile 파일이 없고 `.vault-bridge/protocol`에는 S08A schema copy만 있으며 `requests`/`responses`는 비어 있다. smoke 이후에도 sentinel, plugin, request/response event를 만들지 않는다.

## S08A offline bridge contract gate

S08A는 실제 remote·Git history·device를 읽지 않는 control-side slice다. `bridge_contract`가 Blueprint에서 trusted request/response/root-sentinel schema를 생성하고, protocol copy는 trusted bytes와 동일한 digest를 가져야 한다. 상태 전이와 runtime/public mapping은 exact allowlist로 유지한다.

- `canonicalize_github_remote()`는 credential/userinfo·query·fragment·비-GitHub host를 거부하고 canonical GitHub identity와 SHA-256만 반환한다.
- request/response history는 add 한 번만 허용하며 update/delete/re-add는 quarantine 진단으로 닫는다.
- fixture renderer는 caller가 지정한 `ops/tests/fixtures/` 계열 root 안에서만 O_EXCL create를 수행하고 production `.vault-bridge/responses/`에는 쓸 수 없다.
- S08A PASS는 `.knowledgeos-root.json` 생성이나 실제 bridge publish/commit/push를 포함하지 않는다. 해당 작업은 S08B 이후의 별도 사용자 입력 및 acceptance다.

## 별도 사용자 입력이 필요한 값

- S08B notes repository remote URL, canonical fingerprint, expected branch와 민감자료 경계는 사용자 확인 및 sentinel 생성으로 완료됨
- canonical Vault 표시 이름은 `KnowledgeHub`로 확정됨
- tracked sentinel에 넣을 Vault UUID는 `411602c1-5278-4a8b-8b96-9183fb6ef8c2`로 생성됨
- 상시 실행 Mac 사용 여부
- mobile relay 또는 immediate API 사용 여부
- 의료·직장·기관 제한 자료를 별도 Vault로 분리할지 여부

S08B 이후에도 Working Copy/device credential, mobile round-trip, remote automation, LLM/provider와 background worker는 별도 사용자 입력 및 acceptance로 남는다.

## S08B Git identity와 production sentinel

S08B는 사용자가 확인한 `KnowledgeHub` notes remote와 branch를 control-side configure 구현에 바인딩했다.

- configured origin: `https://github.com/devRestain/KnowledgeHub.git`
- canonical remote: `github.com/devrestain/knowledgehub.git\n`
- `remote_identity_sha256`: `a8c9310a232c9d41110f113aedb0bcdd6483571db43235109d092bdaa4ba3146`
- current/expected branch: `main`
- pre-visibility remote `main` commit: `440829fa8d1c01aaaced54d9469dd692910ab10d`
- current remote `main` commit: `a44c70c0ce4c41491195e0e721bfbb718ce17c4c`
- sentinel: `KnowledgeHub/.knowledgeos-root.json`
- sentinel write: create-only, no overwrite; visibility fix commit `a44c70c` pushed separately

`vaultctl configure --interactive`는 control/Vault 독립 Git root, configured `origin`, canonical remote identity, current branch, local `origin/main` tracking ref, UUID, canonical name과 민감자료 경계 확인을 모두 통과해야 sentinel을 쓴다. 이미 다른 bytes의 sentinel이 있으면 conflict로 종료한다.

## S09 offline Shortcut/outbox gate

S09의 canonical 범위는 실제 장치 adapter가 없는 control-side synthetic contract다. `ops/config/shortcuts.yaml`은 Blueprint의 다섯 Shortcut ID와 필드를 exact하게 복제하고, `ops/config/mobile.yaml`은 device-local recovery outbox와 입력·asset·privacy·Git gate를 기록한다. 실제 Shortcuts, Working Copy, device profile, credential, remote 또는 production Vault에는 쓰지 않는다.

- payload는 deterministic target path, UUID/job ID, RFC3339 timestamp, input kind, sensitivity choice, UTF-8 byte count와 SHA-256을 포함한다.
- outbox는 `input.json`을 O_EXCL/create-only로 저장하고 event를 append-only로 기록한다. 같은 ID·같은 digest retry는 `NO_OP`, 다른 digest는 conflict다.
- `501:20`을 명시한 `docker compose -f ops/compose.yaml run --rm dev`는 프로젝트 로컬 `.codex/rules/knowledgeos.rules`에 exact allow로 등록되어 있다. 이 규칙은 disposable `dev` 실행만 허용하고 Compose lifecycle이나 다른 identity는 허용하지 않는다.
- dirty/detached/diverged/conflict/pre-existing staged path 또는 exact staged set 미확인에서는 자동 Pull/Commit/Push를 금지한다. `Defer`도 committed blob bytes와 hash를 확인하지 못하면 request를 만들지 않는다.
- remote 관찰 또는 local-only 이관 receipt와 최소 7일이 없으면 interactive confirmation이 있어도 cleanup하지 않는다. 30일 초과 pending item은 별도 stale warning으로 표시한다.

S09의 synthetic recovery fixture는 offline, auth failure, push rejection, Shortcut cancellation, reboot과 changed retry를 다룬다. S10에서 실제 iPhone/iPad Working Copy 연동은 사용자가 확인했으며, 장치별 profile/capture/edit, visibility와 local result renderer의 잔여 acceptance는 별도 사용자 참여와 승인 아래 진행한다.

## GitHub canonical directory visibility 보강

2026-09-13 실제 iPhone/iPad Working Copy 연동 확인에서 원격 `KnowledgeHub` tree에
파일이 있는 `99_System`만 보이고, 아직 비어 있는 canonical namespace와
`99_System/Scripts/QuickAdd`가 누락되는 문제가 확인되었다. 원인은
`KnowledgeHub/.gitignore`가 일반 namespace를 제외해서가 아니라 Git이 빈 디렉토리를
저장하지 않기 때문이다.

- 현재 비어 있는 일반 namespace에는 exact allowlist의 `.knowledgeos-directory`만 둔다.
- `.gitkeep`, 빈 template, 빈 Markdown, device/profile/bridge/runtime marker는 만들지 않는다.
- Blueprint required Vault files인 `.vault-bridge/README.md`와
  `99_System/Scripts/QuickAdd/PrepareTitle.js`는 실제 계약 파일로 둔다.
- `vaultops foundation`은 required Vault file, marker 경로, marker bytes를 모두 검사하며
  허용되지 않은 marker·symlink·변조 bytes는 fail closed한다.
- marker 추가 후에도 `KnowledgeHub/.knowledgeos-root.json`, request/response event,
  device profile, runtime state는 이 보강의 대상이 아니다.
- 검토된 exact set은 `a44c70c0ce4c41491195e0e721bfbb718ce17c4c`로 `origin/main`에 push했고,
  post-push `git ls-remote`와 `git ls-tree`로 원격 ref·tracked tree를 확인했다.

## 검증 순서

각 후속 세션은 다음 순서로 증거를 남긴다.

1. `make source-check`
2. `make verify`
3. `S01` 이후에는 `make container-source-check`, `make container-verify`를 canonical container surface에서 실행
4. S02 이후에는 `make blueprint-check`와 변경한 unit/negative fixture를 실행
5. S03C 이후에는 `make schema-check`를 실행하고, artifact 소유가 바뀐 세션은 먼저 `make schema-export`를 실행
6. 두 repository의 `git status --short --branch`
7. 해당 단계에서만 필요한 실제 앱·device smoke는 사용자에게 가능 여부와 정확한 범위를 먼저 확인한 뒤 별도 사용자 작업으로 남김

Obsidian/Working Copy/plugin/Codex CLI 세부 동작은 변할 수 있으므로 해당 단계에서 공식 문서와 실제 설치본을 다시 확인한다. 외부 애플리케이션은 사용자의 정확한 승인이 있는 경우에만 해당 범위로 직접 열거나 조작하며, 그 밖의 device·remote·plugin·credential 작업은 사용자가 별도 수행 가능 여부를 확인한 뒤 다음 작업으로 분리한다. 이번 S07 smoke는 사용자가 승인한 `/private/tmp/knowledgeos-s07-guestbook-horror` disposable Vault에만 한정했다.
