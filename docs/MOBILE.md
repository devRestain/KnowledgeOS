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

모바일 연결은 아직 수행하지 않았다. 구현할 때 다음을 모두 실제 장치에서 확인한다.

1. Working Copy의 Push와 linked external repository 기능 사용 가능 여부
2. `On My iPhone` 또는 `On My iPad/Obsidian/KnowledgeOS`의 local Vault
3. linked external worktree root와 Obsidian Vault root의 동일성
4. remote fingerprint, branch, `.knowledgeos-root.json` bytes의 일치
5. `.obsidian-phone` 또는 `.obsidian-tablet` config override와 앱 재실행
6. 작은 fixture의 mobile → Mac → mobile 왕복

iCloud Drive, Obsidian Sync, Dropbox, OneDrive 같은 두 번째 live sync transport를 같은 Vault에 겹치지 않는다.

## 향후 UX 기준

`Mobile.md`는 desktop Home의 축소판이 아니라 단일 열 현장 화면이다.

1. Capture: 생각 또는 음성을 새 capture로 저장
2. Consult: 오늘, 현재 프로젝트, 자주 보는 지식을 열기
3. Clarify: 보류·긴급·Mac 검토 필요만 표시
4. Defer: 구조화·LLM 심층 작업을 Git bridge로 넘기기

Community plugin이 없어도 열려야 하며, query가 실패하면 plain wikilink가 남아야 한다. 정적 문서에 오래된 sync 성공 상태를 저장하지 않고 “마지막 sync snapshot”임을 명시한다.

## 이번 foundation에서 하지 않은 것

- `Mobile.md` 생성
- Shortcut 다섯 개 생성 또는 export
- Working Copy 설정
- device credential 발급
- `.obsidian-phone`, `.obsidian-tablet` 내부 설정 추측
- bridge request/response schema 생성

이 파일은 향후 구현 경계만 고정한다.
