# KnowledgeOS

KnowledgeOS는 KnowledgeHub Vault에 자유롭게 쓴 아이디어와 발상을 다시 쓸 수 있는 형태로 정리·축적하고, 축적된 생각으로 사고를 강화하는 AgentFabric Operation입니다.

동작 기준은 [기능 명세](docs/CAPABILITIES.md)이며, 각 인수 시나리오가 어떤 구현과 테스트로 확인되는지는 [기능 추적표](docs/CAPABILITY_TRACE.md)에 있습니다. 처음 보는 분은 별도 Sites 모노레포의 [KnowledgeOS 위키](../../Sites/knowledgeos/wiki/index.html)를 먼저 읽어 보세요.

## 문서

| 문서 | 내용 |
|---|---|
| [KnowledgeOS 위키](../../Sites/knowledgeos/wiki/index.html) | 코드를 읽지 않고 실제 기능의 처리 흐름·Hermes Profile·Graph·Temporal 연결을 이해하는 필수 한국어 기술 위키 |
| [기능 명세](docs/CAPABILITIES.md) | 최상위 기준: 기능별 동작, 인수 시나리오, 구현 순서 |
| [기능 추적표](docs/CAPABILITY_TRACE.md) | 인수 ID마다 구현 위치와 pytest 노드 |
| [사용자 기능 명세](USER_FEATURE_SPEC.md) | 지키는 약속, 여섯 기능, Obsidian·관리·모바일 사용 흐름 |
| [Blueprint](OBSIDIAN_VAULT_BLUEPRINT.md) | 화면 분담, 역할 조직, Vault 계약, 효과 규칙, Core 준수 |
| [온톨로지 공간](Ontology/README.md) | 도메인 용어·관계 작성 공간 |
| [PROJECT_STATE.md](PROJECT_STATE.md) | 진행 상태와 검증 근거 |

## 구성

| 위치 | 내용 |
|---|---|
| `src/knowledgeos/` | Owner(접수·State·효과), ExOps HTTP, Vault 경계, 검색, 규격화, 제안, 역할 실행, Temporal·LangGraph 런타임, Hermes 어댑터, InterOps 송신, CLI |
| `src/knowledgeos/capabilities/` | C1–C12 기능 모듈 |
| `config/` | 기능 카탈로그, 역할·모델 경로(`roles.json`), 예약 작업(`jobs.json`), Core 채택 고정값(`core-adoption.json`) |
| `roles/` | 역할별 Hermes profile의 공통 지침과 SOUL |
| `frontend/` | 관리 콘솔(브라우저) |
| `thin-client/` | Obsidian Thin Client 플러그인 소스 |
| `tests/` | 공개 인터페이스 기준 계약 테스트 |
| `scripts/` | 추적표 검사(`trace_check.py`), 상태 검증(`validate_state.py`), Sites의 KnowledgeOS 범위 생성·검사 호출(`build_wiki.py`, `wiki_check.py`) |
| `../../Sites/knowledgeos/` | 소개·위키·선별 공개 근거 사본과 유일한 설명 원고(`_source/`); 별도 Git 모노레포 |

## 소유와 경계

- 이 디렉터리는 제어·소스 경계입니다. Vault, Owner State(`States/Operations/knowledgeos/`), Runtime은 설정에서 각각 따로 지정하며 서로 겹치거나 포함할 수 없습니다.
- State, Runtime, 자격 증명은 Git과 Vault 밖에 둡니다. ExOps는 KnowledgeOS 소유자가 선택한 로컬 정책에 따라 별도 로그인·토큰 없이 loopback(`127.0.0.1`)에만 바인딩하며, 문서 작업은 Obsidian 네이티브 경계에 두고 `frontend/` 브라우저 관리 콘솔은 선택 표면으로 유지합니다.
- `Core/`의 계약과 스키마는 수정하지 않고 `config/core-adoption.json`으로 고정해 따릅니다. Core `0.21.1`에 맞춘 소스 정렬과 `Runtimes/knowledgeos/.venv` 고정 설치·`States/Operations/knowledgeos/` 수동 호환성 채택은 기존 이력·미해결 기록·과거 고정값을 덮어쓰지 않으며, 기존 소유자 선택 온디맨드 중지 정책과 C7 예약 설정을 그대로 유지하고 이번 채택 범위에서는 Owner를 기동하지 않았습니다.
- 다른 Operation과의 InterOps 요청·수신은 기반 역할 이름만으로 자동 허용되지 않으며, 발신 측의 명시적 `INTEROP_REQUEST` 권한, 발신자별 `0600` 자격 증명 서명 인증, 수신 측의 Gateway 허용이 모두 있을 때만 처리합니다.
- 실행 기록과 결과는 Temporal 서버에 보존되지만, Work의 소유와 판단은 KnowledgeOS가 가집니다.
- 작업 조정 상태의 유일한 원본 원장은 `Core/docs/OPERATION_STATE.md` 기준의 [PROJECT_STATE.md](PROJECT_STATE.md)이며, 별도 상태 문서를 추가하지 않습니다.
- 테마·CSS 스니펫·커뮤니티 플러그인은 모든 기기에서 자유롭게 설치·삭제·업데이트하고 설정합니다. 연동 목록은 허용 목록이 아니며, KnowledgeOS와 에이전트 유지보수는 별도 요청 없이 개인 외형·플러그인 설정을 덮어쓰거나 프리셋으로 되돌리지 않습니다.

## 사용

개발 이미지와 검증은 모두 프로젝트 컨테이너에서 실행합니다.

```sh
make image-build                 # 개발 이미지(Python 3.12.8, uv 0.8.14, Node.js, 고정 Temporal CLI 1.5.1)
make trace-check                 # 추적표가 인수 ID와 정확히 일치하는지 검사하고 노드 목록 출력
make test TESTS="<노드 ID들>"     # 지정한 테스트만 실행(네트워크 차단, 읽기 전용 소스)
make state-check                 # PROJECT_STATE.md 검증
make wiki                        # Sites/knowledgeos/_source/로 Sites/knowledgeos/wiki/ 생성
make wiki-check                  # KnowledgeOS 범위의 신선도·인수/상태 연결·링크 검사
```

바뀐 기능에 맞춰 명세와 Sites의 `knowledgeos/_source/` 설명을 함께 갱신합니다. 설명 원고와 생성 결과는 하나의 Sites 저장소에만 두며, 예전 `docs/wiki/` HTML 별칭은 만들지 않습니다. Operation별 독립 Sites worktree에서는 `make wiki SITES_ROOT=/path/to/sites-worktree`와 대응하는 `make wiki-check`로 자신의 범위만 생성·검사합니다. 선택한 재사용 구현 worktree는 `OPERATIONKIT_ROOT=/path/to/OperationKit`로 지정할 수 있습니다. 공통 허브나 등록을 바꿀 때만 Sites에서 전체를 생성합니다.

위키는 [작성 철학](../../Sites/knowledgeos/wiki/philosophy.html)에 따라 실제 입력에서 결과까지의 구현 선택을 자연어로 설명합니다. 처리 순서와 기본값은 현재 코드·설정으로 확인하며, 구현 근거와 실제 실행 관찰을 구별합니다. Sites의 문서 변경은 독립 worktree의 GitHub PR로 리뷰·병합하며, 소유 Operation의 명세·코드 근거는 명시한 AgentFabric 소스 경로에서만 읽습니다.

### iPhone용 Obsidian 설정

`Vaults/KnowledgeHub/.obsidian-phone/`은 사용자 소유의 iPhone용 Obsidian 설정 위치이며 MacBook이나 폰에서 자유롭게 바꿉니다. `config/obsidian-mobile/profile/`은 참조 프리셋이지 현재 개인 설정과 같아야 하는 기준본이 아닙니다. 자동 업데이터나 초기화는 없습니다. `.obsidian-phone/` 트리 전체를 기존 GitHub–Working Copy Pro로 동기화하고 공통 `.DS_Store`·임시 파일 제외만 유지하므로 workspace·캐시도 생성되면 동기화될 수 있습니다. Mac은 별도 `.obsidian-mac/`을 쓰므로 같은 Vault를 동기화해도 두 프로필의 테마·플러그인 설정이 자동으로 같아지지는 않습니다.

`Home.md`와 `Mobile.md`는 Obsidian Reading View에서 Markdown·Core Bases·Tasks만으로 동작하는 대시보드입니다. Home은 할 일·대기 제안·Inbox, 전체 폭 프로젝트, 열린 질문·읽는 중인 자료, 30일 방치 아이디어의 일곱 구획을 유지하고, Mobile은 `# Mobile` 제목과 인라인 제목을 숨긴 채 할 일·Inbox·읽기 전용 AI 검토(제안과 보고서)·진행 중 프로젝트·전체 목록 순서로 시작합니다. `99_System/Dashboards/`의 `Tasks.md`·`Weekly_Review.md`도 인라인 제목·무설명 대시보드 원칙에 맞춰 `title:` frontmatter·`#` H1 제목·메타 설명·`Core-only fallback` 문구·안내 목록을 없앴으며, `Tasks.md`는 이월되지 않은 열린 할 일을 `기한 지남`·`오늘`(날짜 없는 데일리 할 일 포함)·`대기`·`예정 · 기타`로 나누고, `Weekly_Review.md`는 다섯 라이브 작업 구획(`Inbox`·`할 일`·`프로젝트`·`자료 · 지식`·`AI 검토`)과 전체 보기 직접 링크를 둡니다. Tasks 8.4는 "JavaScript in queries" 활성화를 기기별 local storage에 저장하므로, 이월 `>` 제외용 `filter by function`을 쓰는 `Home.md`·`Mobile.md`·`Tasks.md`·`Weekly_Review.md`의 Task 블록은 해당 설정이 꺼진 기기에서 오류가 표시되며 Tasks 설정에서 직접 켜야 합니다(KnowledgeOS가 바꾸지 않음). Base view는 기존 이름과 필터를 유지한 채 상태 배지와 type별 색상 커버 수식을 추가했으며, 전체 페이지 view(`Knowledge#Knowledge`, `Ideas untouched 30 days`, `Open questions`, `Sources#Read`, `Inbox#Unprocessed`, status별 그룹화된 `Projects#By status`, `Review#Reports untouched 14 days`)는 카드 형식을, `Review#Queue`는 type별 그룹화를 사용하고 대시보드 임베드(`Home.md`·`Mobile.md`)는 작은 표를 유지합니다. 제공 스타일은 16px 본문과 범위형 44×44 동작, 카드·그룹 스타일을 쓰며 작은 목록과 full view를 연결합니다. 스타일 원본 `99_System/CSS/dashboard.css`를 포함해 `.obsidian-mac/snippets/knowledgeos-home.css`, `.obsidian-phone/snippets/knowledgeos-home.css`, `config/obsidian-mobile/profile/snippets/knowledgeos-home.css` 네 곳에 동기화된 사본을 두고 `KnowledgeOS Dashboard` Style Settings 섹션(모바일 줄 높이, 기본 세로 간격 배율 0.8, 글자 크기, 44px 터치·행 크기, `ko-mobile-task-row-height`)을 제공해 `Mobile.md`의 Task 목록 행 간격을 이전의 약 절반으로 줄였습니다(소유자 선택; 할 일 체크박스 터치 영역은 44px보다 작지만 28px 이상이며 `ko-mobile-task-row-height`로 조절 가능하고 나머지 대상은 44px 이상 유지). 폰 전용 `mobile-toolbar`는 속성 아래 툴바용이며 `KnowledgeOS Toolbars` Style Settings 섹션(툴바 터치 크기 44px)을 포함합니다. 참조 프리셋은 두 스니펫을 켜지만 사용자가 바꾼 사본이나 활성화 선택을 강제하지 않습니다. 이번 변경은 정적·설정 변경과 데스크톱 모바일 에뮬레이션 렌더링만 확인한 것이며 실제 iPhone 및 Obsidian 모바일 실행은 미검증이고 원격 push는 하지 않았습니다.

1. 기존에 쓰던 모바일 설정이 있다면 전환 전에 백업합니다. `.obsidian-mac`은 대상이 아닙니다.
2. Mac에서 평소 쓰는 기존 Git 동기화 절차로 변경을 GitHub에 push한 뒤, iPhone Working Copy Pro에서 평소대로 pull합니다. 숨김 폴더(`.obsidian-phone`)가 iOS 파일 앱에 직접 보이지 않아도 정상이며, 새 디렉터리 연결을 만들지 않고 기존 Working Copy 워크플로를 그대로 씁니다.
3. iPhone Obsidian에서 **Settings → Files and links → Override config folder**를 `.obsidian-phone`으로 지정하고 앱을 완전히 강제 종료한 뒤 다시 엽니다(설치된 Note Toolbar 1.25.23 `main.js`에 `onExternalSettingsChange` 핸들러가 없어 Working Copy로 `.obsidian-phone/plugins/note-toolbar/data.json`을 pull해도 Obsidian이 열려 있거나 일시 중단 상태면 이전 설정을 메모리에 유지하므로, 앱을 완전히 강제 종료 후 다시 열거나 Note Toolbar를 껐다 켜야 갱신된 4개 항목 속성 아래 툴바가 나타납니다; 커뮤니티 플러그인 허용 필요 시 확인). 시작 화면은 `Mobile.md`이며, 속성 아래에 최대 4개 항목의 Note Toolbar가 표시됩니다.
4. **새노트**에서 QuickAdd의 **입력 · 아이디어 · 질문 · 지식 · 자료 · 프로젝트 · Today**를 고릅니다. 템플릿(`T00`·`T11`·`T12`·`T20`·`T21`·`T22`·`T40`·`T41`)에는 `title:` 속성과 H1 제목이 없고 인라인 제목(= 파일명)만 보이며, `T40`의 `claim`은 비어 있는 상태로 시작합니다(`T10_Daily.md`는 변경 없음). 캡처 파일명에는 시각 숫자를 넣지 않고 생성 시각은 `created` 속성에 저장하며, `T00`의 `captured_from`은 플랫폼에 따라 `phone_quickadd` 또는 `mac_quickadd`로 기록합니다. 폰 QuickAdd **입력**은 `PrepareInputs`로 제목을 묻고 `T00_Capture` 템플릿으로 `00_Inbox/Captures/{{VALUE:title}}`을 만드는 템플릿 선택이며(폰에서는 코어 고유 노트 `zk-prefixer`를 끄고 `showInlineTitle`을 켬), Mac QuickAdd 캡처도 `YYYYMMDD-HHmmss` 접두사 없이 입력한 제목만 파일명으로 쓰고 충돌 시 번호를 증가시킵니다. 다섯 유형은 제목 한 번만 묻고 기존 템플릿으로 만들며, 별칭은 빈 목록으로 시작합니다. 실행 구문이 든 제목은 거부하고 기존 노트는 덮어쓰지 않습니다.
5. Note Toolbar는 Mac(`.obsidian-mac`)과 폰(`.obsidian-phone` 및 참조 프로필) 모두 속성 아래(`props`)에 배치합니다.
   - **Mac (`.obsidian-mac`, 7개 툴바, 최대 5개 항목)**:
     - Home: `오늘 · 캡처 · 의미 검색 · 할 일 · 주간 점검`
     - Daily: `홈 · 캡처 · 새 노트 · 할 일 · 주간 점검`
     - Inbox: `정리 요청 · 규격화 · 강화: 발전 · 관련 노트 · 포착함`
     - Knowledge: `강화: 발전 · 관련 노트 · 의미 검색 · 백링크 · 지식 목록`
     - Project: `강화: 돌아보기 · 관련 노트 · 백링크 · 할 일 · 프로젝트 목록`
     - Review: `검토함 · 주간 점검 · 백링크 · 나가는 링크 · 홈`
     - Proposal: `적용 · 거절 · 나가는 링크 · 검토함 · 홈`
   - **폰 (`.obsidian-phone` 및 참조 프로필, 최대 4개 항목, `동기화` 드롭다운 메뉴·숨겨진 Sync 툴바 제거)**: `홈`은 `Mobile.md`를 엽니다. 이전 이름이 `Mobile`이던 툴바는 `home`으로 바꿔 `type: home` 노트(`Home.md`·`Mobile.md`)가 직접 연결되고(`*` 기본 규칙도 계속 사용) `"No matching toolbar: home"` 알림이 더 이상 뜨지 않습니다. `home` 툴바는 `오늘 · 새노트 · 받기 · 보내기`(`받기`·`보내기`는 고정 URI 버튼 `shortcuts://run-shortcut?name=KOS%20Pull` / `shortcuts://run-shortcut?name=KOS%20Push`)이며, 종류별 툴바는 아래와 같습니다.

| 현재 노트 | 툴바 항목 (최대 4개) |
|---|---|
| home (이전 `Mobile`, `type: home` · `*` 기본) | 오늘 · 새노트 · 받기 · 보내기 |
| capture | 홈 · 포착함 · 새노트 · 찾기 |
| daily | 홈 · 새노트 · 할일 · 찾기 |
| idea | 홈 · 질문 · 지식 · 찾기 |
| question | 홈 · 지식 · 자료 · 찾기 |
| knowledge | 홈 · 자료 · 새노트 · 찾기 |
| source | 홈 · 지식 · 새노트 · 찾기 |
| project | 홈 · 할일 · 새노트 · 찾기 |
| Review | 홈 · 검토함 · 오늘 · 찾기 |

**오늘 / Today**는 신뢰된 `T10_Daily.md`만 명시적으로 렌더링해 `10_Journal/Daily/YYYY-MM-DD.md`를 만들며, 이미 있으면 수정 없이 엽니다. 새 Daily에 원시 Templater 문법이 남지 않는지 확인합니다. Tasks는 기존 `#task`와 이월된 `[>]`의 `NON_TASK` 구분을 유지합니다.

폰 프로필의 `mobile-toolbar` CSS 스니펫은 최대 4개 항목 툴바 버튼의 최소 터치 영역을 44×44px로 늘립니다. 좁은 화면에서는 라벨을 숨기지 않고 두 줄로 배치될 수 있으며, Mac 프로필에는 폰 전용 `mobile-toolbar`를 추가하지 않고 공유 `knowledgeos-home` 사본만 적용합니다.

home(`type: home` · `*` 기본) 툴바의 고정 URI 버튼 **받기 · 보내기**는 실제 로컬 서명 자산이 준비된 `shortcuts://run-shortcut?name=KOS%20Pull`과 `shortcuts://run-shortcut?name=KOS%20Push`를 직접 실행합니다. 실행 중 iOS가 Shortcuts 앱을 잠시 전면으로 띄울 수 있고 Pull/Push 단축어는 끝에서 Obsidian을 다시 엽니다. 오류를 가리는 `x-error` 콜백은 두지 않으며 실제 iPhone 동작은 미검증입니다.

### 모바일 단축어: 목표와 현재 상태

M1의 세 단축어는 `KOS 캡처`, `KOS Pull`, `KOS Push`입니다. 별도 음성·Today 단축어는 두지 않습니다. 캡처는 메모를 먼저 받고, 그다음 클립보드에서 native `detect.link`로 URL을 추출해 HTTP(S) 전체 줄만 남긴 뒤 실제 URL을 보여 주고 포함 여부를 묻습니다. URL은 필수가 아니며 명시적으로 확인한 경우에만 저장합니다. 캡처 호스트(`CaptureNote.js`)는 `KOS Capture` Macro의 `body`·`source_url`을 받아 `00_Inbox/Captures/` 아래에 첫 메모 줄(정규화, 40자 이하), 없으면 출처 URL 호스트명, 그것도 없으면 `캡처`를 파일명으로 노트를 만듭니다(충돌 시 ` 2`, ` 3`…를 붙여 절대 덮어쓰지 않으며, 파일명에는 시각 숫자를 넣지 않고 `id` UUID와 `created`는 frontmatter에 유지; raw memo를 평가하지 않음). `KOS Pull`은 고정 `KnowledgeHub` 저장소에 native Pull만 실행하고 성공 시 Obsidian으로 돌아오며 Working Copy 열기·상태 리다이렉트는 없습니다. `KOS Push`는 native 수정 파일 조회 뒤 본문 없이 저장소 상대 경로(`pathInRepo`)를 보여 주고 한 번 확인을 받아 dated 기본 메시지(`Mobile sync yyyy-MM-dd HH:mm`)로 자동 commit한 뒤 Pull 없이 push하며, 새 수정 파일이 없을 때는 빈 커밋 없이 기존 로컬 커밋 푸시 여부만 한 번 확인합니다. staging UI·수동 메시지 입력은 없고 비동기 결과를 완료로 가장하지 않습니다. Shortcut이 잠시 foreground될 수 있고 zero-app-switch를 보장하지 않으며 Working Copy를 자동으로 열지 않습니다.

현재 세 단축어는 **로컬 서명 자산 준비 완료(GitHub 원격 push 없음)** 상태이며, 이번 작업은 **정적·설정 변경만** 포함하고 **실제 iPhone 기기 및 Obsidian 모바일 동작 검증은 미실행**입니다. 소유자가 제공한 비실행 Working Copy reference로 `repo` 키·`KnowledgeHub` AppEntity(선택적 표시용 `image` 제거)·`FileDetailsIntent`·`pathInRepo` 메타데이터를 확보해 `config/obsidian-mobile/automation/shortcuts_author.py`에서 세 단축어를 모두 생성합니다(`python3 config/obsidian-mobile/automation/shortcuts_author.py --repo-entity-ref <repo-entity.wflow> --wc-actions-ref <wc-actions.wflow> --out-dir <out-dir>`). 정적·참조 검증으로 `KOS Capture` host receiver(`CaptureNote.js`)와 공개 C2 계약, macOS 26.6.2 `WorkflowKit`/`ActionKit` 내장 동작 admission(캡처 25·Pull 2·Push 15개 모두 구체적 native class, 오류 0), 소유자 donor 템플릿 대조(Pull 1·Push 4개 Working Copy 동작, 오류 0), Apple Anyone 로컬 서명 및 복호화 액션 그래프 일치를 확인하고 서명된 3개 파일(`KOS 캡처.shortcut`, `KOS Pull.shortcut`, `KOS Push.shortcut`)을 로컬 `Vaults/KnowledgeHub/99_System/Shortcuts/`에만 배치했습니다. GitHub에는 push하지 않았으므로 iPhone에서 GitHub pull만으로는 새 파일이 전달되지 않습니다. 실제 폰 검증을 하려면 사용자가 기존 동기화 경로로 로컬 변경(3개 서명 `.shortcut`, `99_System/Scripts/QuickAdd/CaptureNote.js`, `.obsidian-phone/`의 QuickAdd `KOS Capture`·Note Toolbar `받기`/`보내기` URI)을 폰으로 옮기고 3개 단축어를 가져온 뒤(기존 `KOS 캡처` 등 이전 버전 교체), 실제 폰에서 캡처(메모/선택 URL/취소)·Pull·Push(신규/수정/삭제/변경 없음/기존 커밋 푸시, Working Copy UI 미전환)를 직접 확인해야 합니다. `GetFiles`/`Commit`의 신규·미추적·삭제 파일 포함 범위와 변경 없음 시 push 동작은 실제 기기에서 확인하기 전까지 보장하지 않으며, 이전 다섯 흐름과 generator는 퇴역 후 live Vault에서 제거되어 archive 사본만 남아 있습니다. 기존 7개 QuickAdd 원본, Obsidian Today, Mac Templater false, `.gitignore`, Owner 중지 정책, AppleScript note opener는 유지합니다. M2 모바일 요청과 M3 GitHub Actions는 예약 상태입니다.

원격 Git 조작, iPhone 설정 변경, Apple 단축어 설치와 Mac Owner 기동은 자동으로 수행하지 않습니다. 이는 사용자 선택 설정 유지보수이며 Owner 런타임의 플러그인 관리가 아닙니다. QuickAdd·Note Toolbar·Templater·Tasks·Homepage는 제공하는 기본 워크플로일 뿐 플러그인 수 제한이 아니며, 폰 프로필(`.obsidian-phone` 및 미러 `config/obsidian-mobile/profile`, 대상 iPhone 17 402×874 pt)은 소유자 요청에 따라 Mac 설치본과 바이트 단위로 동일한 Notebook Navigator 3.4.1과 Style Settings 1.0.9도 새 다운로드 없이 고정해 함께 켭니다(추가·변경 제한 없음). Navigator `data.json`은 26개 ID 전부에 `synced`를 지정한 `syncModes`를 선언합니다(없으면 3.4.1이 프로필·툴바·행 높이 설정을 기기 로컬로 취급해 `data.json`에서 제외하며, 실제 Obsidian 1.13.7 로드에서 확인). 저장 파일은 플러그인의 자체 정규화 출력으로 자동 생성된 빈 `default` 프로필도 함께 보관하며 활성 프로필은 `iPhone`입니다. 402×874 데스크톱 모바일 에뮬레이션에서는 `iPhone` 프로필, 6개 바로가기, 44pt 탐색 행이 확인되었습니다(`홈(Mobile.md)·Inbox·검토·일기·프로젝트·지식` 바로가기, `99_System`·`80_Assets` 숨김, `type`/`status` 속성 탐색, 하위 폴더 표시, 최신순·Daily 제목 내림차순, 2줄 미리보기·대표 이미지/외부 다운로드 끔, 하단 플로팅 툴바, `mobileScale` 1.1 약 44pt 행, 캘린더 끔·Today는 Note Toolbar 유지, Navigator 홈 없음·Homepage가 계속 `Mobile.md` 열기). 기본 파일 탐색기를 자동 대체하지 않으므로 폰에서 명령 팔레트로 `Notebook Navigator: Open`을 한 번 실행해 왼쪽 서랍 탭 선택을 유지해야 Obsidian이 기억합니다. 폰 Style Settings `data.json`은 행 반경 8·파일 반경 10·선택 파일 테두리 1px을 씁니다. 기본 생성 경로는 본문을 실행 구문으로 재해석하는 QuickAdd Capture와 Mac 캡처 매크로를 사용하지 않으며, Templater의 **새 파일 자동 실행**은 Mac과 폰 모두 끕니다. Mac의 기존 Daily Note 버튼과 `Mod+Shift+E` 단축키는 QuickAdd **Today**를 호출합니다. Core Daily Notes를 직접 호출하는 외부 호출자는 이 전환 밖이며, Core Daily Notes 직접 생성은 Templater를 자동 실행하지 않습니다. 모바일 코어 **Daily Notes**는 끕니다. 기존 Obsidian Today 기능은 그대로 두며 별도 Today 단축어는 추가하지 않습니다.

### 모바일에서 위키 읽기

위키는 휴대폰 브라우저에서도 읽을 수 있습니다. 좁은 화면에서는 검색창이 별도 줄을 쓰고 본문은 화면 폭에 맞춥니다. 넓은 표·코드는 영역 안에서 가로 스크롤하며, 그림은 **그림 크게 보기 / 그림 전체 보기**로 전환합니다.

접속은 Sites의 ExOps 정적 서버와 서버 측 **Tailscale Serve의 비공개 tailnet HTTPS**를 사용합니다. 정확한 컨테이너 실행·Host 설정·`tailscale serve --bg http://127.0.0.1:8766` 안내는 [Sites README](../../Sites/README.md)에 있습니다. 이 작업은 Serve나 네트워크를 활성화하지 않으며 Funnel·공개 인터넷·Owner API 노출을 포함하지 않습니다. 원문 근거는 Sites 안의 명시적 선별 사본으로 연결하므로 저장소 전체를 제공할 필요가 없습니다.

### 기능 검증과 Owner 실행

추적된 테스트 전체 실행:

```sh
make test TESTS="$(make -s trace-check | tail -n 1)"
```

호스트 Runtime(`Runtimes/knowledgeos/.venv`)을 Core 고정값과 소스 작업 트리에 맞춰 설치·갱신할 때는 `.pth` 우회 파일이 아니라 격리된 빌드 환경에서 고정 잠금(`--frozen --no-dev`)으로 설치합니다. 이 명령은 기존 서드파티 패키지를 바꾸지 않고 `knowledgeos`·`operationkit` 편집 가능 패키지만 설치하며, Owner 서비스를 시작하거나 State 기록을 고쳐 쓰지 않습니다.

```sh
UV_PROJECT_ENVIRONMENT=/path/to/Runtimes/knowledgeos/.venv \
  uv sync --project /path/to/Operations/KnowledgeOS --frozen --no-dev
```

일반적인 Owner 시작(예: Thin Client의 **Owner 시작** 또는 `launchctl kickstart`)은 `startup_delay_minutes` 경과 후 꺼져 있던 동안 놓친 예약 슬롯(C7)·복구·알림을 실행할 수 있으며, 기존 온디맨드·C7 예약 정책을 유지한 채 이번 비활성 설치·호환성 채택에서는 Owner 기동을 수행하지 않았습니다.

Owner 실행과 유지보수 CLI(`python -m knowledgeos.cli` 또는 설치된 `knowledgeos`):

```sh
knowledgeos serve --config owner.json          # Owner와 ExOps 백엔드, 관리 콘솔
knowledgeos health | vault-check | index rebuild|verify|embed
knowledgeos standardize PATH --dry-run|--apply
knowledgeos job run standardize|embed|curate|daily|digest  # 실행 결과 영수증까지 따라가 보고
knowledgeos reconcile UNKNOWN_ID --resolution applied|not_applied --yes
knowledgeos install profiles --config owner.json [--dry-run]  # Hermes 역할 profile과 발송 전용 kos-ops profile 설치
knowledgeos install service --config owner.json [--dry-run]   # Owner LaunchAgent 등록(--label, --plist 선택)
```

설정 파일 예시(경로는 설정 파일 기준 상대 경로 가능):

```json
{
  "vault": "/path/to/KnowledgeHub",
  "state": "/path/to/States/Operations/knowledgeos",
  "runtime": "/path/to/Runtimes/knowledgeos",
  "settings": {"runtime": {"address": "127.0.0.1:7233", "namespace": "default"}}
}
```

`ai_policy`는 `approve`(기본)와 `deny` 두 값입니다. 빠졌거나 비면 `approve`, 퇴역값을 포함한 알 수 없는 값은 `deny`입니다. `approve`만 모든 자동화·모델·예약 작업에 쓰며, 보고서가 아닌 허용 노트만 승인된 수신자에게 Gateway로 내보냅니다. `sensitivity: confidential`은 이 허용 여부를 바꾸지 않습니다. 정책 결정은 사용자만 하며, KnowledgeOS는 C2 규칙 6의 빠진 값 기본 `approve`와 새로 생성하는 노트의 기본·상속 정책만 씁니다.

역할의 모델 설정은 `config/roles.json`에서 관리합니다. `classifier`·`linker`·`connector`는 `openai-codex/gpt-6.1-terra`, Director·Manager·EvalOfficer·`challenger`·`synthesizer`는 `openai-codex/gpt-6.1-sol`을 씁니다. 모든 모델 역할의 `reasoning_effort`는 `high`이며, profile 생성 시 `agent` 아래에 씁니다. `classifier`에는 로컬 `base_url`·`ollama_num_ctx`나 대체 모델을 두지 않습니다.

모델 단계 하나는 Temporal Activity 하나 안에서 끝나야 합니다. 기본 Activity 시간(`settings.runtime.activity_timeout_seconds`)은 120초이며, 실제 호스트 `owner.json`은 이 값을 600초로 설정합니다.

예약 작업은 Owner가 켜져 있는 동안 Owner 안에서만 실행됩니다. Owner가 시작되고 `startup_delay_minutes`(기본 3분)가 지나면 꺼져 있는 동안 놓친 작업을 한 번씩 실행하고, 이후에는 정해진 시각에 실행합니다. `settings.C7`의 `scheduler_enabled`(기본 true)·`startup_delay_minutes`·`tick_seconds`(기본 30)로 조정합니다.

`daily`는 매일 07:00 이전 날짜의 허용된 미완료 일정·할 일을 오늘의 `10_Journal/Daily/YYYY-MM-DD.md`로 인계하고, 원본 항목의 체크박스만 `[>]`로 표시합니다. 오늘 노트의 기존 줄은 보존하며, 없는 날만 `ai_policy: approve`로 메모·할 일·일정·하루 정리 네 구역의 노트를 만듭니다. 데일리 노트에는 AI 제안을 만들지 않습니다. 알릴 할 일이 있으면 하루 한 번 Discord 할 일 알림을 보내며, 현재 `approve` 노트의 할 일 이름만 날짜·메타데이터·링크 없이 공개합니다.

Discord 즉시 알림은 충돌·해결되지 않은 효과의 결과 불명만 보냅니다. 실패·차단·놓친 작업은 즉시 알리지 않습니다. 08:00 `digest`는 제안·새 보고서·오래된 포착·확인 대기·주의 항목 등 확인할 것이 있을 때만 보내며, 자동 규격화 내역이나 다시 볼 노트·실패 작업만으로는 보내지 않습니다. 실패 작업 이름은 다른 확인거리가 있는 요약에만 한 줄로 덧붙입니다.

의미 검색은 로컬 Ollama 임베딩과 State의 벡터 색인(`<State>/semantic/`)을 씁니다. Owner가 loopback HTTP로 Ollama를 직접 호출하며(loopback이 아닌 주소는 설정 오류), 게이트웨이나 원격 모델은 쓰지 않습니다. 기본값은 아래와 같고 `settings.C3.semantic`으로 바꿉니다(그 밖에 `batch_size`, `timeout_seconds`, `max_notes`, `related_neighbors`, `related_min_score`, System One 관련 노트 거르기 기준 `related_min_probability`). `ollama_url`이나 `model`을 비우면 의미 검색을 쓰지 않습니다.

```json
"settings": {"C3": {"semantic": {"ollama_url": "http://127.0.0.1:11434", "model": "qwen3-embedding:8b-q4_K_M"}}}
```

- 예약 작업 `embed`(매시 30분)와 `knowledgeos index embed`(남은 노트가 없을 때까지 반복)가 같은 `index.embed` 요청을 처리합니다. 새 노트와 내용이 바뀐 `approve` 노트만 임베딩하고, 삭제·이름 변경으로 사라졌거나 정책 거부·제안(`type: proposal`)·`99_System/`·충돌 표시 때문에 대상이 아니게 된 노트의 벡터는 지웁니다. `approve`이면 `confidential`·보고서 노트도 임베딩합니다.
- 아직 임베딩되지 않았거나 수정된 노트는 다음 임베딩까지 의미 검색·관련 노트 결과에서 빠집니다. 모델을 바꾸면 다음 실행에서 모든 벡터를 새로 만들고, 그 전까지 의미 검색은 `unavailable`입니다. Ollama가 꺼져 있으면 작업이 실패로 남고(재시도 없음) 기존 벡터는 그대로 유지됩니다.
- `knowledgeos index verify`의 `semantic`과 `GET /api/health`의 `search_semantic`으로 준비 상태와 범위(대상·임베딩·오래됨·누락 수)를 확인합니다.

System One은 OpenAI Decisions API(`gpt-6-luna`)에 선택형 질문을 보내는 Owner 서비스입니다. 규격화 규칙 9(폴더로 못 정하는 `type`, 잘못된 `status`·`confidence`, 빠지거나 잘못된 `capture_kind`), 모델 teammate(`classifier`·`linker`·`connector`·`challenger`·`synthesizer`)의 `system_one(handle, question, choices?)` 도구, 관련 노트 거르기, 문서 관리 상태 제안, 자유 요청 사전 분류에 씁니다. 도구는 그 Work에서 읽은 노트에 대해 선택지 하나를 고르거나 예/아니오 확률을 묻고, step마다 10번까지 보냅니다. `classifier`가 `question` 없이 대상 핸들로 부르면 type/status 제안이며 대상마다 Work에 기록해 재사용합니다. Director·Manager·EvalOfficer에는 이 도구가 없습니다. 답은 조언일 뿐이고 Owner가 어디에도 쓰지 않습니다. `ai_policy`·`sensitivity`는 묻지 않고, 출처와 관계없이 현재 `approve` 노트만 보냅니다. API 키는 Git·Vault 밖의 0600 파일에 두고 경로만 설정합니다.

```json
"settings": {"system_one": {"api_key_file": "/path/to/States/System/OpenAI/knowledgeos-decisions.key"}}
```

- 그 밖의 설정: `model`(기본 `gpt-6-luna`), `min_confidence`(기본 0.7), `timeout_seconds`(기본 20), `max_input_chars`(기본 8000), `url`.
- `GET /api/health`의 `system_one`이 `ready`·`unavailable`·`unconfigured`를 보여 줍니다. 꺼져 있거나 실패해도 각 기능은 System One 없이 동작합니다. 작업에 묶인 호출은 내용 없이 State `system_one.jsonl`에 기록됩니다.

Owner를 Mac의 필요할 때 켜지는 사용자 서비스(LaunchAgent)로 등록하고 Obsidian에서 켜고 끕니다.

```sh
knowledgeos install service --config owner.json --dry-run   # plist와 launchctl argv만 출력, 아무것도 쓰지 않음
knowledgeos install service --config owner.json             # ~/Library/LaunchAgents/com.agentfabric.knowledgeos.owner.plist 작성 후 적재(실행은 안 함)
```

- plist는 `RunAtLoad`·`KeepAlive`를 끄고, 로그를 Runtime의 `logs/`에 남기며, `HERMES_HOME`과 Hermes 바이너리 경로가 든 `PATH`를 넣습니다. 이미 적재된 같은 label은 새 정의로 교체됩니다. 라벨과 plist 경로는 `--label`, `--plist`로 바꿀 수 있습니다.
- Thin Client 명령 **Owner 시작**은 `launchctl kickstart`로 켜고 `/api/health`가 준비될 때까지 기다려 결과를 알립니다. **Owner 중지**는 `launchctl kill SIGTERM`을 보낸 뒤 health 응답이 끊겼는지 확인해, 중지됨·아직 실행 중·확인 불가를 구분해 알립니다. 둘 다 데스크톱 전용이며 노트 본문은 보내지 않습니다. Thin Client 설정의 launchd 라벨 기본값은 `com.agentfabric.knowledgeos.owner`입니다.

### Obsidian 사이드바

`thin-client/`의 `main.js`·`manifest.json`·`styles.css`를 Vault 설정 폴더의 `plugins/knowledgeos-thin-client/`에 함께 배치하고 플러그인을 다시 불러옵니다. 기존 `data.json`은 유지합니다. 리본의 KnowledgeOS 아이콘이나 **KnowledgeOS: 사이드바 열기**로 오른쪽 탭을 엽니다.

- **규격화·정리·강화·찾기**를 한 줄로 배치했습니다. 누르면 아래 기능 창이 바뀌며 선택만으로 작업을 실행하지 않습니다.
- 강화의 발전·묻기·돌아보기, 질문·기간 입력, 의미 검색·관련 노트 결과는 모두 사이드바 안에서 처리합니다. 입력 중인 내용은 기능을 전환해도 메모리에만 유지합니다.
- 제안 노트에서만 **적용·거절**이 나타납니다. 더보기 역시 내부 화면에서 자유 요청·자동 변경 되돌리기·관리 콘솔을 제공하며, 전원 버튼은 Owner 시작·중지를 맡습니다.
- 위에 표시된 마지막 활성 노트를 대상으로 합니다. 사이드바로 초점을 옮겨도 유지되며, 다른 파일을 열면 바뀝니다. 노트가 없으면 노트 전용 동작만 비활성화됩니다.
- 규격화 diff와 적용·취소도 내부에 표시하며, 확정 전에 기능이나 노트를 바꾸면 해당 미리보기를 취소합니다. 진행·오류·결과 링크도 팝업 대신 내부에 표시합니다. 기존 명령 팔레트·문맥 메뉴의 입력창과 확인 절차는 유지합니다. 탭을 여는 것만으로 Owner를 켜거나 끄지 않습니다.

실제 Obsidian 인계 시에는 탭 재사용, 노트 대상 전환, 내부 기능 창·입력·검색 결과, 좁은 폭과 테마, 제안 전용 버튼 표시를 확인합니다. 브라우저 대역 렌더링과 실제 플러그인 설치·GUI 관찰은 별개입니다.

## 검증 범위

테스트는 실제 Temporal 개발 서버와 LangGraph를 사용하지만, 모델 역할은 스크립트 대역이고 Hermes와 `launchctl`은 가짜 실행 파일이며 시계는 조작 가능한 가짜입니다. 실제 모델 호출, Hermes profile 설치, 예약 작업의 실제 시각 실행, launchd 등록과 Owner 시작·중지, Discord 전송, 실제 HermesTrace 수신기, Obsidian GUI는 별도로 확인해야 하며, 그 결과는 `PROJECT_STATE.md`에 따로 기록합니다.

## 퇴역 자산

이전 구현의 소스, 내부 상태, Runtime, 역할 profile, 합성 Vault 노트는 복구용 아카이브(`Tmp/archive/knowledgeos-legacy-2026-11-19/`, 0600 권한)에 보관한 뒤 삭제했습니다.
