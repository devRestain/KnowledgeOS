# 모바일 계약

## 역할 분리

> iPhone은 포착하고, iPad는 읽고 보완하며, Mac은 의미를 결정한다.

모바일에서 허용할 작업은 30초에서 10분 안에 끝나고 Vault 전체 구조를 이해하지 않아도 안전한 작업이다.

| 작업 | iPhone | iPad | Mac |
|---|---|---|---|
| 생각·음성·URL 포착 | 주 역할 | 가능 | 가능 |
| 오늘과 현재 프로젝트 확인 | 주 역할 | 주 역할 | 주 역할 |
| 짧은 본문 보완 | 제한적 | 적합 | 적합 |
| `triage_hint` 표시 | 가능 | 주 역할 | 가능 |
| 최종 분류·이동·관계 적용 | 금지 | 금지 | 주 역할 |
| LLM canonical apply | 금지 | 금지 | interactive only |
| Git conflict 해결 | 금지 | 금지 | 주 역할 |

## 안전한 모바일 작업

- unique capture 생성
- 오늘 Daily 열기
- search/read
- 짧은 body edit
- `triage_hint` 설정
- immutable bridge request 게시
- proposal 결과 확인

모바일 capture와 bridge request/response는 모두 create-only이며 기존 경로를 덮어쓰지 않는다.

다음 작업은 모바일 핵심 흐름에 넣지 않는다.

- bulk move/rename/delete
- graph 정비
- plugin 설치와 설정
- Git conflict 해결
- live note에 대한 LLM overwrite
- 실시간 LLM stream

## 향후 설치 gate

2026-09-13 사용자는 Working Copy Pro를 유일한 mobile Git writer로 사용한 iPhone/iPad Obsidian `KnowledgeHub` 연동 성공을 확인했다. Codex는 외부 앱이나 장치에 직접 접근하지 않았으므로, 아래 목록에서 장치별 exact evidence가 필요한 항목은 후속 사용자 참여 gate로 남긴다.

1. Working Copy의 Push와 linked external repository 기능 사용 가능 여부
2. `On My iPhone` 또는 `On My iPad/Obsidian/KnowledgeHub`의 local Vault
3. linked external worktree root와 Obsidian Vault root의 동일성
4. remote fingerprint, branch, `.knowledgeos-root.json` bytes의 일치
5. `.obsidian-phone` 또는 `.obsidian-tablet` config override와 앱 재실행
6. 작은 fixture의 mobile → Mac → mobile 왕복

iCloud Drive, Obsidian Sync, Dropbox, OneDrive 같은 두 번째 live sync transport를 같은 Vault에 겹치지 않는다.

## S10 사용자 보고 연동과 GitHub tree 보강

사용자 보고로 Working Copy Pro 기반 iPhone/iPad 연결은 동작했지만, Git은 빈 디렉토리를 저장하지 않으므로 GitHub tree에 실제 파일이 있던 `99_System`만 보이는 문제가 있었다. 원인은 `KnowledgeHub/.gitignore`가 일반 canonical namespace를 제외한 것이 아니었다.

현재 비어 있던 일반 canonical namespace 26곳에는 exact allowlist의 `.knowledgeos-directory` 구조 표식을 두었고, Blueprint required Vault files인 `.vault-bridge/README.md`와 `99_System/Scripts/QuickAdd/PrepareTitle.js`는 실제 파일로 추가했다. `.knowledgeos-root.json`도 함께 tracked해 Vault identity가 원격에 존재하도록 했다. `.gitkeep`, 사용자 note 더미, `.obsidian-*` profile, `.vault-bridge/{requests,responses}`, `runtime/`에는 표식을 만들지 않았다.

이 exact set은 `KnowledgeHub` commit `a44c70c0ce4c41491195e0e721bfbb718ce17c4c`로 `origin/main`에 push했고, post-push `git ls-remote`와 `git ls-tree`로 원격 ref와 tracked tree를 확인했다. 남은 S10 acceptance는 장치별 profile/capture/edit, Mac visibility와 offline/auth/conflict/revoke drill이다.

## 향후 UX 기준

`Mobile.md`는 desktop Home의 축소판이 아니라 단일 열 현장 화면이다.

1. Capture: 생각 또는 음성을 새 capture로 저장
2. Consult: 오늘, 현재 프로젝트, 자주 보는 지식을 열기
3. Clarify: 보류·긴급·Mac 검토 필요만 표시
4. Defer: 구조화·LLM 심층 작업을 Git bridge로 넘기기

Community plugin이 없어도 열려야 하며, query가 실패하면 plain wikilink가 남아야 한다. 정적 문서에 오래된 sync 성공 상태를 저장하지 않고 “마지막 sync snapshot”임을 명시한다.

## S09 control-side offline contract

S09는 실제 장치나 외부 앱을 건드리지 않고, 다섯 Shortcut과 durable recovery outbox가 실패 상황에서도 원문을 보존해야 한다는 계약을 control workspace에 고정했다.

- `ops/config/shortcuts.yaml`: Blueprint와 exact하게 대응하는 `KO · Capture`, `Save Source`, `Today`, `Defer to Mac`, `Sync`의 exportable definition
- `ops/config/mobile.yaml`: device-local outbox, payload limits, asset/privacy/secret policy, exact-file Git/Defer gate와 device boundary
- `ops/src/vaultops/mobile_offline.py`: strict payload hash, create-only `input.json`, append-only event, idempotent retry/conflict, cleanup/stale warning과 fail-closed transaction gate
- `ops/tests/fixtures/s09_mobile_offline/recovery-cases.yaml`: offline, auth failure, push rejection, cancellation, reboot과 changed retry의 synthetic cases

outbox의 canonical 장치 경로는 `On My iPhone/KnowledgeHub-Recovery/Outbox/JOB_ID/input.json` 또는 iPad 대응 경로다. payload와 event는 durable payload hash에 묶이고, remote 관찰 또는 local-only 이관 receipt와 최소 7일이 없으면 삭제하지 않는다. 이 세션에서는 `/private/tmp` 계열 synthetic temporary root만 사용했다.

## S09에서 하지 않은 것

- 실제 Shortcut 생성·설치 또는 Apple Shortcuts export/import
- Working Copy 연결, linked external worktree 설정, device credential 발급
- `.obsidian-phone`, `.obsidian-tablet` profile 파일 생성·추측
- 실제 iPhone/iPad outbox, mobile sync/bridge round-trip, remote write, commit/push

이 파일의 S09 계약은 S10 실제 device transport를 위한 전제이며, live mobile 완료를 의미하지 않는다.
