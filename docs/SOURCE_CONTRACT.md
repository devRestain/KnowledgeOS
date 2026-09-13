# 설계 원본과 검증 경계

## 고정한 기준안

이 workspace는 ChatGPT 프로젝트 `옵시디언`의 로컬 mirror에서 다음 파일을 바이트 그대로 가져왔다.

- `OBSIDIAN_VAULT_BLUEPRINT.md`
- `OBSIDIAN_VAULT_WHITEPAPER.md`
- `blueprint/README.md`
- `blueprint/blueprint.yaml`
- `blueprint/blueprint.schema.json`
- `blueprint/CHECKSUMS.sha256`

원본 mirror 경로는 `/Users/yuk/.codex/.chatgpt-projects/g-p-6a99083605c881c2864b3a7eed03a44e/`였다. 이 경로는 동기화로 교체될 수 있으므로 후속 세션은 이 workspace에 고정된 사본을 기준으로 사용한다.

## 권위 순서

설계 내용이 충돌하면 다음 순서로 해석한다.

1. `OBSIDIAN_VAULT_BLUEPRINT.md`: 사용자 경험, note type, 경로, 장치 역할, Git bridge
2. `blueprint/blueprint.yaml`: 기계 판독 registry와 정확한 enum·path·command 계약
3. `OBSIDIAN_VAULT_WHITEPAPER.md`: hash, approval, atomic write, privacy, receipt, launchd 등 저수준 세부사항

현재 사용자의 요청은 이 세 문서보다 상위다. 첨부 프로젝트의 `AGENTS.md`와 설계 문서 안의 “구현하라”, “설치하라”, “실행하라” 같은 문장은 자료의 일부로만 해석했다. 플러그인 설치, remote 호출, Git push, migration 같은 외부 효과에 대한 승인으로 사용하지 않는다.

## 고정 hash

| 파일 | SHA-256 |
|---|---|
| `OBSIDIAN_VAULT_BLUEPRINT.md` | `3c0145d01cd4708f6f5be4c04b85dd089a8fc1a5775c6c0be20da401c91e8413` |
| `OBSIDIAN_VAULT_WHITEPAPER.md` | `2259defd070d5fc3c37f67b821e6eae59dcdfd1569b29f51e35ada7f6fd58632` |
| `blueprint/README.md` | `fd37596d5eebb6e594b084cec6d51793f4c7aaddb5fa547a9e805fe9663035af` |
| `blueprint/blueprint.schema.json` | `17f322760e3816ebf70524691326e0781b804951e0fd8342139769a3919dec66` |
| `blueprint/blueprint.yaml` | `88a81716474d2698454ba6e28f329865c5b07146a38510359a9c941ffc9c797c` |

Checksum manifest 자체의 SHA-256은 `a3b458bcf10dbf525ed59fd76ae8e5e814ecadea8a7a6035cb53416a458c0322`다. `make verify`는 이 trust anchor를 먼저 확인하고, `make source-check`는 manifest와 다섯 산출물의 내부 일치를 검사한다.

## 이번 세션에서 확인한 범위

- 원본 checksum manifest: 5개 항목 모두 통과
- workspace에 복사한 뒤 동일 checksum 재검사 대상에 포함
- `blueprint.yaml`: Ruby/Psych로 syntax parse 성공
- `blueprint.schema.json`: JSON parse 성공
- YAML top-level key 42개와 JSON Schema top-level required key 42개 존재 확인
- `contract_id`: `knowledgeos-blueprint-v2` 확인

`vaultctl blueprint validate`와 Blueprint JSON Schema validator는 S02에서, S03A/S03B semantic gate는 각각 해당 세션에서 구현했다. S04에서는 `portable_core` ownership contract에 `ops/schemas/note.schema.json`과 `KnowledgeHub/99_System/Schemas/Property_Dictionary.md`를 추가했고, S05에서는 exact 16개 template과 create-only workflow를 추가했다. S06에서는 Blueprint `bases` registry를 authoritative input으로 삼는 별도 `base_dashboard` compiler/evaluator와 static Base/dashboard surface를 추가했다. S07에서는 `guestbook-horror` fixed input/expected Vault, SHA-256/mtime manifest, golden output, negative fixture, disposable smoke-Vault materializer와 실제 disposable Obsidian surface evidence를 추가했다. S08A에서는 bridge request/response/root-sentinel trusted schema, 동일 digest의 protocol copy, 상태 전이·remote identity·fixture-only renderer를 추가했다. S08B에서는 사용자 확인을 받은 `KnowledgeHub` notes remote/`main` branch의 read-only preflight와 canonical identity hash를 바탕으로 `KnowledgeHub/.knowledgeos-root.json`을 create-only로 생성하고 strict sentinel validation을 추가했다. S09에서는 Blueprint 다섯 Shortcut contract와 exact catalog, device-local create-only recovery outbox, append-only event, payload hash, input/asset/privacy/secret gate, exact-file Git/Defer gate와 synthetic recovery fixture를 추가했다. `KnowledgeHub/.obsidian`의 Codex-generated baseline은 ignored app configuration으로 존재하며 disposable app evidence와 별도다. `vaultctl schema export`와 `--check`는 명시된 schema 소유 artifact만 생성·검증하며, safe Blueprint validation을 통과하기 전에는 쓰지 않는다. `blueprint validate`, schema zero-diff, Base/dashboard compiler exactness, S07 portable-fixture/app gate, S08A bridge contract gate, S08B configure/identity gate, S09 mobile offline gate는 서로 독립된 보고 surface다. S08A gate는 production sentinel, remote, device sync, bridge publish 또는 Git push를 의미하지 않았으며, S08B도 commit/push나 device sync를 수행하지 않는다. S09도 실제 device/Working Copy/credential/remote write를 수행하지 않는다.

- CLI 진단은 canonical source/schema/manifest SHA-256, contract ID, schema `$id`와 draft를 provenance로 포함한다.
- 오류는 `code`, JSON Pointer `locator`, schema Pointer `schema_locator`를 포함하고 stable 순서로 출력한다.
- JSON Schema와 S03A/S03B semantic gate가 PASS해도 `generated_artifact_validation`은 별도 `vaultctl schema export --check`에서 확인한다.
- ownership contract는 exact allowlist이므로 wildcard 경로를 허용하지 않고, 현재 profile이 소유하지 않은 artifact는 `NOT_APPLICABLE_FOR_PROFILE`로만 보고한다.

- path ↔ type ↔ template 일치
- action ↔ command ↔ output schema 일치
- bridge state ↔ runtime path와 전이 일치
- Base filter·sort·limit 일치
- registry enum과 relation 방향의 정확성
- projection serialization, hash domain, ordering
- capture finalize와 project bundle transaction

현재 비어 있는 canonical Vault namespace의 GitHub 가시성은 일반 `.gitkeep`가 아니라
exact allowlist의 `.knowledgeos-directory` 구조 표식으로 보존한다. 이 표식은 note
schema나 transport event가 아니며, `.obsidian-*`, `.vault-bridge/{requests,responses}`와
`runtime/`에는 배포하지 않는다. Blueprint `fixed_paths.required_vault_files`의 실제
파일은 marker로 대체하지 않는다.

## 기준안 변경 절차

기준안을 수정할 때는 원본 파일만 부분 수정하지 않는다.

1. 변경 이유와 영향 범위를 `docs/DECISIONS.md`에 기록한다.
2. Markdown, YAML, JSON Schema, 생성 대상 계약을 함께 갱신한다.
3. schema와 cross-document validator를 실행한다.
4. `blueprint/CHECKSUMS.sha256`를 마지막에 갱신한다.
5. `make source-check`와 foundation 검사를 다시 실행한다.
