# 운영 및 단계 계약

## 현재 foundation 작업

현재 단계는 구조와 경계를 재현할 수 있게 만드는 데까지만 책임진다.

```bash
make source-check   # 고정한 설계 패키지 checksum
make verify         # foundation acceptance
```

`make verify`는 현재 고정 namespace와 설계 원본의 무결성만 확인한다. JSON Schema 전체 검증이나 cross-document 의미 검증을 수행한다고 주장하지 않는다.

## 단계별 gate

| 단계 | 산출물 | 진입 조건 | 현재 |
|---|---|---|---|
| Inventory | 기존 Vault, Git, sync, property, plugin 조사 | target read-only 접근 | 빈 target 확인 완료; device·remote·sync 전체 preflight 미완료 |
| Repository foundation | docs, namespace, 독립 Git boundary | inventory에 충돌 없음 | 디렉터리·문서 완료, Git init 미착수 |
| Portable Vault | schema, 16 templates, 8 Bases, Home/Mobile, fixture | blueprint full validation 도구 | 미착수 |
| Git/mobile baseline | root sentinel, Working Copy, Shortcuts, bridge protocol | remote·branch·device 확인 | 미착수 |
| Mac plugin profile | Core + 최소 community plugins | 실제 Obsidian smoke 가능 | 미착수 |
| `vaultctl` non-LLM | doctor, create, period, ingest, reconcile | path/schema/transaction tests | 미착수 |
| Read-only LLM | triage proposal, review/apply receipts | privacy·hash·candidate tests | 미착수 |
| Retrieval | lexical + typed-link, cited answer | deterministic projection | 미착수 |
| Background/remote | LaunchAgent, optional API/relay | 별도 opt-in과 recovery drill | 미착수 |

## Git 운영 경계

이 workspace는 다음 두 개의 독립 local repository가 될 경계를 준비했다.

```text
control: KnowledgeOS/.git          # 아직 없음
notes:   KnowledgeOS/vault/.git    # 아직 없음
```

- control은 `vault/`와 `runtime/`을 추적하지 않는다.
- notes repository는 Vault 내용만 추적한다.
- `git init`, local branch 선택, remote 추가, commit, push, submodule 등록은 아직 수행하지 않았다.
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
3. 변경한 schema와 unit/negative fixture
4. 두 repository의 `git status --short --branch`
5. 해당 단계에서만 필요한 실제 앱·device smoke

Obsidian/Working Copy/plugin/Codex CLI 세부 동작은 변할 수 있으므로 해당 단계에서 공식 문서와 실제 설치본을 다시 확인한다.
