# Obsidian 네이티브 문서 작업 가이드

## 역할과 증거의 경계

KnowledgeHub의 문서 작업은 Obsidian 안에서 합니다. KnowledgeOS Thin Client는 오른쪽 사이드바의 네이티브 CommonJS `ItemView`이며, 브라우저 프레임·WebViewer·iframe·외부 브라우저 실행기나 터미널 문서 도구가 아닙니다. Markdown 본문과 YAML이 정본입니다. Work 접수, 독립 평가, 제안 결정, 일일 검토와 정본 변경의 권한은 Owner에만 있으며, 클라이언트는 표시와 제한된 요청을 담당합니다.

이 가이드는 구현 설계, 기존 기능과의 대응, 플러그인 통합 이유를 설명합니다. 진행 상황이나 실행 중인 플러그인의 채택 증거는 기록하지 않습니다. 소스·설치·런타임·기기별 증거는 [PROJECT_STATE.md](../PROJECT_STATE.md), 별도 승인된 GUI 검증 절차는 [Computer Use 프롬프트](OBSIDIAN_COMPUTER_USE_PROMPT.md)를 확인하세요. 문서 작성만으로 GUI 검증이 수행되지는 않습니다.

현재 유지 소스의 대상은 **0.6.0**입니다. 이전 **0.5.1**의 설치·로드와 부분 GUI 관측은 과거 근거로 구분해 재사용합니다. 변경된 문서 처리·검토 동작은 그 과거 관측만으로 검증됐다고 하지 않습니다. 설치·런타임·GUI의 현재 상태와 정확한 근거는 `PROJECT_STATE.md`를 따릅니다.

## 화면 구성·탐색·안전 규칙

- **검색 / 작업 / Pending / 일일 검토 / 노트 도구**의 다섯 패널은 글자 없이 아이콘으로 전환합니다. 버튼은 아래 표의 tooltip·접근 가능한 이름(`aria-label`)으로 찾고, 선택 패널은 `aria-pressed`로 구분합니다. 메타데이터·문서 개선은 별도 패널이 아니라 명령으로 준비하는 **작업 패널의 요청 양식**입니다.
- 상단 연결 동작도 아이콘만 표시합니다. `Owner 새로 조회`, `로컬 백엔드 시작`, `연결 해제`는 tooltip·접근 가능한 이름이며 항상 보이는 긴 버튼 문구가 아닙니다. 중요한 제출·결정·적용 동작은 아이콘과 짧은 글자를 함께 표시해 구분합니다.
- Obsidian 네이티브 `setIcon`의 Lucide 아이콘과 테마·인터페이스 글꼴·글자 크기·색상·테두리·포커스 규칙을 상속합니다. 별도 앱 틀, 강제 밝은/어두운 색상, 원격 글꼴, 고정 데스크톱 너비를 사용하지 않습니다. 키보드 포커스, 선택/비활성 상태, 좁은 사이드바의 줄바꿈·스크롤을 유지합니다.
- 노트·검색 결과·근거는 Obsidian의 네이티브 탐색으로 엽니다. 파일 통합은 `getLeaf("tab")`으로 루트 작업공간 탭을 열며, 사이드바 안에 문서 창을 중첩하지 않습니다. 기존 작성·포착·기간별 기록 흐름을 재사용하고 편집기를 복제하지 않습니다.
- 자체 명령은 선택 플러그인과 리본 표시 여부에 의존하지 않습니다. 선택 통합은 공개 command ID와 플러그인 사용 가능 여부를 확인합니다. 노트 도구는 그룹별 **아이콘+짧은 이름의 간결한 그리드**로 표시하며 설명·사용 불가 이유는 tooltip과 펼침 상세에서 확인합니다. 긴 도움말을 버튼마다 반복하지 않으며, 사용할 수 없으면 이유와 Obsidian 기본 탐색 대안을 확인할 수 있어야 합니다. 플러그인 내부 렌더러·스크립트 주입을 사용하지 않습니다.
- Owner 연결·mode 상태는 간결하게 표시하고 원래의 generation/revision/`observed_at`/개별 `bindings`/`runtime_observation`은 기본 접힘 **연결 상세**에서 확인합니다. binding 존재는 Worker 정상 동작 증거가 아니며 `runtime_observation: unavailable`은 관측 불가 그대로 유지합니다. 초기화·연결 해제 시 비공개 결과, 선택 참조와 실행 가능한 이전 관측을 무효화합니다.
- method/Work ID, receipt와 원시 응답 등 기술 상세는 기본 접힘으로 두되 펼쳐 읽거나 필요한 ID를 입력할 수 있습니다. 긴 ID·digest·근거는 줄바꿈되거나 해당 영역 안에서 스크롤되어야 합니다. 접힘은 정책 거부·오래됨·결과 불명이나 승인/적용 구분을 숨기는 수단이 아닙니다.
- 노트 대상 요청은 활성 Markdown 경로와 정확한 SHA-256에 묶습니다. 대상·질의·패널·연결·요청 ID가 바뀌면 이전 비동기 응답을 취소하거나 무시합니다. 노트 없이도 전체 자료 검색은 가능하지만, 노트 범위 요청 제출은 불가능합니다.
- 검토에 60초가 지났다는 이유만으로 버튼을 잠그지 않습니다. Owner는 동작 시 해당 제안·문서·제어 대상의 실제 digest와 revision을 확인합니다. 원문 적용 시 충돌 방지는 선택 문서에만 적용되며 무관 문서 편집은 작업을 막지 않습니다.
- 제출·결정·실행은 명시적으로 합니다. 메타데이터/문서 개선 명령은 양식만 준비합니다. 승인은 결정 기록이며 **적용은 별도의 확인을 거치는 Owner 동작**입니다. 일일 보강 거절은 정확한 추가분 복원 요청이지 일반 실행 취소가 아닙니다.
- 변경 요청의 응답이 유실되면 **해당 문서의 요청만** 보류합니다. 다른 문서는 계속 편집·처리할 수 있습니다. `요청 응답 대조`는 원래 요청을 `mutation/receipt`로 읽기만 하고, 확인된 Owner 영수증에 한해 통신 불명 잠금을 해제합니다. 자동 재전송은 없습니다. Owner의 실행 결과 자체가 `unknown`인 경우는 별개로 유지합니다.
- 요청은 구성된 신뢰 경계인 `127.0.0.1` loopback Owner로만 보냅니다. 로그인/세션 토큰, 클라우드·provider 호출, 외부 Gateway 전송, 자동 Git, 클라이언트의 직접 정본 쓰기를 추가하지 않습니다.
- 예전 세션 토큰 필드는 런타임 설정과 요청에서 사용하지 않습니다. 승인된 소스 설치 시 기존 설정 파일의 바이트는 보존하므로 사용하지 않는 토큰이 남아 있을 수 있습니다. 이는 인증이나 토큰 표시 권한을 뜻하지 않으며 유지 코드는 이를 무시합니다.

## 문서별 Projection과 자유로운 작성

- 노트의 생성·편집·이동에 사전 등록, 필수 frontmatter 작성, 수동 Projection 갱신을 요구하지 않습니다. 문서가 처음 사용되면 현재 파일로 읽기용 정보를 만들고, 이후에는 변경된 문서만 다시 파싱합니다. 없는 메타데이터는 내부 읽기용 기본값이며 사용자 문서에 자동으로 쓰지 않습니다.
- 새 노트나 다른 노트의 변경·이동·잘못된 YAML이 선택 문서의 작업을 막지 않습니다. 선택 문서 자체가 바뀌면 최신 본문으로 처리합니다. 읽을 수 없는 문서는 해당 문서에만 오류가 적용됩니다.
- Citation은 해당 문서·chunk의 정확한 버전에 묶습니다. 오래된 검색 근거에서도 현재 노트를 열 수 있지만 원문 변경을 알립니다. 과거 평가된 Work 결과는 원문이 바뀌거나 이동해도 ID로 열어 볼 수 있으며, 최신 근거나 바로 적용 가능한 제안으로 취급하지 않습니다.
- 저장소 전체의 source snapshot은 동결된 유지보수 export의 출처 정보일 뿐, 일반 문서 작업의 실행 허가가 아닙니다. Vector/Hybrid도 이미 구성된 신뢰된 로컬 embedding port에서 새롭거나 바뀐 chunk만 처리하며 모델 설치나 원격 대체는 하지 않습니다.


### 아이콘과 정확한 동작 이름

아이콘만 있는 버튼은 포인터 tooltip과 `aria-label`이 아래 이름과 일치해야 합니다. 키보드만으로도 이동·포커스 확인·실행이 가능해야 하며, 아래 이름으로 동작을 식별합니다.

| 위치 | Lucide 아이콘 | 정확한 동작 이름 | 표시 방식 |
|---|---|---|---|
| 상단 | `refresh-cw` | Owner 새로 조회 | 아이콘만 |
| 상단 | `power` | 로컬 백엔드 시작 | 아이콘만 |
| 상단 | `unplug` | 연결 해제 | 아이콘만 |
| 패널 전환 | `search` | 검색 | 아이콘만 |
| 패널 전환 | `workflow` | 작업 | 아이콘만 |
| 패널 전환 | `inbox` | Pending | 아이콘만 |
| 패널 전환 | `calendar-check` | 일일 검토 | 아이콘만 |
| 패널 전환 | `panels-top-left` | 노트 도구 | 아이콘만 |
| 주요 요청 | `tags` | 메타데이터 | 아이콘+짧은 글자 |
| 주요 요청 | `file-pen-line` | 문서 개선 | 아이콘+짧은 글자 |
| 질문 제출 | `send` | 질문 | 아이콘+짧은 글자 |
| Pending 결정 | `check` | 승인 | 아이콘+짧은 글자 |
| Pending 결정 | `x` | 거절 | 아이콘+짧은 글자 |
| Pending 변경 | `check-check` | 적용 | 아이콘+짧은 글자, 별도 확인 필수 |

항상 펼쳐진 긴 설명 대신 필요한 tooltip·상세를 확인합니다. 접힘 상세와 비활성 도구의 사용 불가 이유도 키보드로 접근 가능해야 합니다. 실제 좁은 사이드바, 사용자 테마·글꼴, tooltip·접근 가능한 이름·포커스·상세 열기/닫기의 네이티브 검증은 별도 Computer Use 과제입니다. 격리된 browser shim의 소스 렌더나 Node 검사 결과는 실제 Obsidian에서 로드·표시됐다는 증거가 아닙니다.

## 로컬 백엔드를 필요할 때 시작하기

macOS 데스크톱에서는 사이드바 상단의 `power` 아이콘을 tooltip·접근 가능한 이름 **로컬 백엔드 시작**으로 찾아 명시적으로 누릅니다. 사이드바 열기와 `refresh-cw` 아이콘의 **Owner 새로 조회**는 조회만 하며 백엔드를 자동으로 시작하지 않습니다. 이미 실행 중이면 현재 Owner를 재사용하고, 시작 중인 다른 창이 있으면 중복 실행하지 않습니다.

- 별도로 설치한 고정 도우미 `~/.local/bin/knowledgeos-local`의 `status --json`과 `start --json`만 shell 없이 호출합니다. 사용자가 Obsidian 설정에 프로그램·명령·권한·root 경로를 입력하지 않습니다.
- 시작 전에 도우미의 Owner 주소와 현재 설정 주소가 같은지 확인합니다. disposable Owner 등 다른 주소를 선택했다면 개인 백엔드를 켜거나 설정을 자동 변경하지 않습니다.
- 도우미는 기존 private host binding과 설치된 Python driver·이미지·Temporal 구성을 사용합니다. Docker가 꺼져 있거나 최초 설치가 없으면 이유를 표시하며, 모델·이미지 설치나 State 초기화·권한 확장을 자동으로 하지 않습니다.
- 시작 대기는 전체 60초로 제한합니다. UI 도우미 호출 상한은 65초이며 시간 초과 후에도 이미 시작된 OS job을 죽이지 않습니다. 시작 실패·대기·Owner 연결과 Worker/모델 사용 가능 여부는 서로 다릅니다.
- LaunchAgent는 host Runtime의 plist를 필요할 때 등록하고 실행합니다. `RunAtLoad=false`, `KeepAlive=false`이며 로그인 자동 시작 위치에 설치하지 않습니다. 로그인 후 다시 사용하려면 시작 버튼을 누릅니다.
- **연결 해제·사이드바 닫기·Obsidian 종료는 백엔드 종료나 Work 취소가 아닙니다.** 시작한 driver·Owner는 독립적으로 유지됩니다. 앱 종료 시 자동 종료·강제 재시작 버튼은 제공하지 않습니다.
- 시작·새로 조회 자체는 통신 불명을 해소하지 않습니다. 원래 요청 ID는 plugin 수명 안에서 해당 문서에만 유지하며 `요청 응답 대조`로 실제 영수증을 확인합니다. 앱 전체 재시작을 durable reconciliation으로 간주하지 않습니다. 승인과 원본 적용도 별도입니다.

도우미 미설치, Docker/Temporal 불가, 주소 불일치, 다른 프로세스의 port 점유, Owner 거부, 시작 시간 초과는 구체적인 오류로 안내합니다. 반복 클릭·다른 UUID 제출·권한 변경으로 오류를 우회하지 마세요. 세부 host 계약은 [RUNTIME.md](RUNTIME.md), 실제 버튼과 앱 종료 동작의 후속 검증은 [Computer Use 프롬프트](OBSIDIAN_COMPUTER_USE_PROMPT.md)의 N37–N44를 사용합니다.

## 자체 명령과 진입점

아래 ID는 Obsidian 플러그인 접두사를 포함한 정확한 값입니다. Note Toolbar·Homepage 등 선택 통합이 없어도 명령 팔레트와 네이티브 메뉴를 사용할 수 있습니다. 리본은 필수가 아니며 조사된 mac 프로필에서는 숨김 상태입니다.

| 정확한 command ID | 이동 위치와 효과 |
|---|---|
| `knowledgeos-thin-client:open-work-review` | KnowledgeOS 네이티브 사이드바를 엽니다. Work를 제출하지 않습니다. |
| `knowledgeos-thin-client:open-search` | 검색 패널을 엽니다. 활성 노트가 없어도 됩니다. |
| `knowledgeos-thin-client:open-note-work` | 현재 Markdown 노트의 작업을 엽니다. 대상이 없으면 안내합니다. |
| `knowledgeos-thin-client:open-pending` | Owner Pending 목록과 검토 화면을 엽니다. |
| `knowledgeos-thin-client:open-daily-review` | 정확한 일일 보강 추가분의 검토 화면을 엽니다. |
| `knowledgeos-thin-client:request-metadata` | 현재 노트의 메타데이터·관계 요청 양식을 작업 패널에 준비합니다. 별도 제출이 필요합니다. |
| `knowledgeos-thin-client:request-enhancement` | 현재 노트의 문서 개선 양식을 작업 패널에 준비합니다. 별도 제출이 필요합니다. |
| `knowledgeos-thin-client:open-library` | 노트 도구 패널에서 네이티브 문서 탐색과 사용 가능성을 확인한 선택 통합을 보여줍니다. |

Markdown 파일의 네이티브 문맥 메뉴에는 현재 노트 작업, Pending, 메타데이터 준비, 문서 개선 준비가 있습니다. 노트 도구는 QuickAdd 다섯 선택지, core 활성 여부를 확인한 `daily-notes`, Notebook Navigator 주간/월간 이동, Tasks 검색·편집·완료 전환, Breadcrumbs 트리/행렬을 실행합니다. 아래 목록의 나머지 공개 명령은 기존 플러그인의 선택 기능이며 모두 자체 버튼으로 제공된다는 뜻은 아닙니다. 선택 명령이 없으면 기능을 재구현하지 않고 이유를 안내합니다. 기본 편집과 탐색은 Obsidian에 남습니다.

Note Toolbar에는 기존 항목을 지우거나 스크립트를 켜지 않고 아래 자체 명령을 앞에 추가합니다.

| 기존 문맥별 도구 모음 | 자체 명령 접미사: 각 값 앞에 `knowledgeos-thin-client:` 사용 |
|---|---|
| Home | `open-search`, `open-work-review`, `open-library` |
| Inbox, Knowledge, Project | `open-note-work`, `request-metadata`, `request-enhancement` |
| Review | `open-pending`, `open-daily-review`, `open-work-review` |
| Daily | `open-note-work`, `open-daily-review`, `open-pending` |

노트 도구는 존재하는 로컬 Home, 여덟 Base(`Inbox`, `Knowledge`, `Projects`, `Sources`, `Decisions`, `Compass`, `Journal`, `Review`), Tasks/Today Focus/Weekly Review 대시보드만 엽니다. Base 이동은 core Bases 활성화가 필요합니다. 파일이 없으면 알리며 탐색의 부수 효과로 생성하지 않습니다. Mobile은 별도 전용 버튼을 가정하지 않고 일반 네이티브 노트 링크로 접근합니다.

## 기존 사용자 기능 전체 대응표

첫 열의 ID는 후속 네이티브 검증 기준과 같습니다. HTTP 경로는 `POST /owner/` 기준입니다. 클라이언트는 JSON 요청을 보내지만 actor/root/binding을 선택하지 않습니다. 경로나 binding이 없으면 이유를 표시하며 가짜 결과나 다른 producer 실행으로 대체하지 않습니다.

| ID | 사용자 기능 | 네이티브 화면 / Owner 경로 | 관찰 가능한 결과와 한계 |
|---|---|---|---|
| N01 | 일반 노트·포착 작성/편집 | Obsidian 편집기·새 노트, 노트 도구의 기존 QuickAdd 선택 | Markdown/YAML을 기본 편집기로 작성합니다. Capture/Question/Idea/Project/Knowledge의 기존 템플릿·경로를 유지합니다. 노트 생성은 Owner Work 접수가 아닙니다. |
| N02 | 일간·주간·월간 기록과 템플릿 | 일간은 Core Daily Notes, 주간/월간은 Notebook Navigator, 제한된 Templater 렌더링 | 기간·제목·폴더·frontmatter가 맞고 미처리 템플릿 토큰이 없어야 합니다. 전역 새 파일 trigger·shell은 끕니다. 기존 기록을 열 때 재생성·덮어쓰기하지 않습니다. |
| N03 | Home·Mobile·Base·Compass 탐색 | 네이티브 노트 링크, Homepage, Obsidian Bases | 원문을 다시 쓰지 않고 로컬 문서/뷰를 엽니다. Base·Compass는 별도 정본 저장소가 아닌 투영입니다. |
| N04 | 링크·백링크·언급·타입 관계 | 네이티브 링크/Backlinks, 선택 Breadcrumbs 트리·행렬·이웃 선택 | 실제 대상 노트로 이동합니다. 미연결 언급과 추론된 edge는 후보이며 승인 관계가 아닙니다. 자동으로 메타데이터에 확정하지 않습니다. |
| N05 | Tasks와 Properties | 기본 체크박스/Properties, Tasks 대시보드·명령, 선택 Meta Bind 입력 | 현재 `#task` 필터를 반영합니다. 노트 도구의 task 편집/전환은 Markdown 소스/라이브 미리보기의 `#task` 체크박스 줄에 커서가 있어야 하며 다른 줄은 거부합니다. 의도한 task/property 편집은 일반 문서 변경이지 Work·Pending 결정이 아닙니다. |
| N06 | Owner 연결·기능 확인 | `owner/status`, 아이콘으로 갱신/해제, 연결 상세 | actor 권한에 따른 mode를 간결하게 표시하고 generation/revision/관측 시각, 개별 binding과 runtime 원시 관측은 기본 접힘 연결 상세에서 확인합니다. 불가 동작은 비활성화합니다. 초기화/해제하면 비공개 상세와 오래된 실행 대상을 지웁니다. |
| N07 | Lexical 전체 자료 검색 | 검색, `search/query`, `mode: lexical` | 활성 노트 없이 제한된 결과·경로·발췌·이유·원문/chunk 식별자·generation을 보거나 명시적인 빈 결과/오류를 확인합니다. |
| N08 | Vector 검색 | 검색, `mode: vector` | 현재 구성된 vector generation을 사용하거나 불가/오래됨을 알립니다. Lexical 결과를 vector로 바꿔 표시하지 않습니다. |
| N09 | Hybrid RRF 검색 | 검색, `mode: hybrid` | 실제 mode/generation과 근거 또는 거부 이유를 표시합니다. 통과를 위해 모델 설치·provider 실행을 시작하지 않습니다. |
| N10 | 승인된 ontology 검색 | 검색, `mode: ontology` | 정책에 맞는 승인 관계 결과 또는 실제 빈 결과/불가 상태를 표시합니다. 언급 후보를 ontology 사실로 취급하지 않습니다. |
| N11 | 검색/결과의 정확한 근거 열기 | 결과·근거에서 네이티브 노트 이동 | 반환된 경로를 Obsidian에서 열고 제공된 locator/hash·잘림 여부를 표시합니다. 근거 일치는 주장 진실성 평가가 아닙니다. 임의 chunk 페이지 조회·인용 검증용 새 사용자 HTTP 경로는 없습니다. |
| N12 | 활성 노트 대상·비동기 응답 관리 | 기본 file/leaf 이벤트, 경로+content digest, 요청 ID | 사이드바 포커스를 새 노트로 오인하지 않고 활성 Markdown 변경을 따릅니다. 이전 응답을 무시하고 적격 노트가 없으면 범위를 무효화합니다. |
| N13 | 단일 원문 자유 질문 Research | 작업 양식, `work/intake` | 명시적 질문과 정확한 선택 원문으로 접수 receipt를 받습니다. 즉시 실행/성공을 뜻하지 않습니다. 유지 런타임은 trim 후 1–512자 질문과 Research method를 지원합니다. |
| N14 | 정형 메타데이터·관계 요청 | 작업 패널의 준비 양식, `work/intake-template`, `template_id: note_metadata` | 동일 request ID/원문 아래 독립 평가되는 두 Team을 접수합니다. 기존 채워진 값을 보존하며 통합 Pending 적용 전에 근거를 검토합니다. 명령으로 양식을 여는 것만으로 제출하지 않습니다. |
| N15 | 정형 문서 개선 요청 | 작업 패널의 준비 양식, 같은 경로, `template_id: document_enhancement` | Research 기반 작업을 접수하고 인용된 개선점·불확실성·부족 자료를 봅니다. 정본 재작성이나 초안이 아닌 보고서입니다. |
| N16 | 그 밖의 구성된 선택 method | `work/methods`, `work/intake-selected` | 각 catalog method의 현재 `available`/`reason`을 표시합니다. Owner에 등록된 실행 가능한 단일 원문 method만 고정 매개변수로 제출합니다. 불확실하면 UUID/원문 ID를 유지합니다. 사용자 임의 매개변수·Officer·Graph 선택은 없습니다. |
| N17 | 노트별 작업 목록·임의 Work ID | 경로별 `work/list`, 입력 ID의 `work/read` | 노트 범위 이력과 입력한 유효 ID의 작업을 봅니다. 전역 작업함으로 오해하지 않으며 receipt·dispatch·application/run·독립 평가를 나눠 표시합니다. |
| N18 | 전체 Work·기준·계보·미충족 항목 | `work/read`, `work/inspection` | 정확한 spec/run, 기준, 배정/segment/Profile, 원문 범위, 계보, 평가, RemainingWork/미충족 항목과 반환된 continuation을 봅니다. 전체 원시 반환값도 확인하며 runtime 관측 불가를 숨기지 않습니다. |
| N19 | 독립 평가된 결과와 근거 | `work/result` | 독립 평가된 결과/text/report, 정확한 근거·인용·제안 참조·잘림·truth/apply flag를 봅니다. 대기/실패/오래된 결과를 평가 통과나 정본 변경으로 표시하지 않습니다. |
| N20 | 통합 메타데이터 제안 준비 재시도 | 원래 `request_id`의 `work/prepare-metadata` | 적격한 독립 member 평가 뒤 Owner의 통합/조정을 명시적으로 요청합니다. 같은 그룹을 재사용하며 producer 재실행이나 정확한 원문/근거 검사 우회를 하지 않습니다. |
| N21 | Pending 목록 | `proposal/list` | 반환된 완료 Pending artifact·동작·적용 가능 여부를 봅니다. 빈 목록을 예시로 채우지 않습니다. |
| N22 | Pending 원문·내용·diff·이유 | `proposal/read` | 원문 텍스트, 묶인/current digest, 원문·잘림·오래됨, 제안 내용·정확한 diff·인용/값 근거·대상·기존 결정을 봅니다. 사용자 결정 이유를 입력합니다. |
| N23 | Pending 승인/거절 | `proposal/decide` | 최신 상세에서 이유와 결정을 보내 Owner receipt를 기록합니다. 승인만으로 정본 바이트가 바뀌지 않습니다. 기결정/오래된 제안은 Owner가 제한합니다. |
| N24 | 승인된 제안 별도 적용 | `proposal/apply` | 최신의 승인된 비오래된 적격 상세와 별도 확인이 필요하며 Owner만 변경합니다. 적용 후 원문 범위를 갱신/무효화합니다. 결정 전용 MCP 계획은 `apply_allowed: false`입니다. |
| N25 | 일일 잠정 추가분 목록·상세 | `daily/list`, `daily/read` | 정확한 original/added/current 필드, before/current digest, 근거·본문 발췌·검토/충돌 상태를 봅니다. 이미 Owner가 잠정 보강한 변경이며 Pending 초안 승인이 아닙니다. |
| N26 | 정확한 일일 추가분 확인/거절 | `daily/decide` | 확인은 추가분을 유지하고, 거절은 그 추가분만 복원하도록 Owner에 요청합니다. 충돌 시 파괴적 선택을 막으며 다른 편집을 덮어쓰지 않습니다. |
| N27 | 제한된 checkpoint·제어 대상 | `work/control/target` | `run_control`이 구성됐을 때만 정확한 checkpoint와 적격 상태를 봅니다. 조사된 쓰기 런타임에는 구성되지 않았으므로 현재 사용 불가로 표시합니다. |
| N28 | 일시정지/재개/취소·수신/적용 | `work/control/{pause,resume,cancel}`, `work/control/command` | 최신의 정확한 대상·고정 command ID·만료와 확인을 사용하며 수신과 적용을 구분합니다. 제한된 Curation continuation 제어이지 일반 process/Activity 종료가 아닙니다. 한 번의 제한된 pause/resume 쌍과 적법한 checkpoint가 필요합니다. |
| N29 | 제한된 사람 질문·steering 조회 | `work/steering/view` | steering binding이 있을 때만 실제 질문·대상·허용 종류·매개변수 범위·이전 receipt·successor 문맥을 봅니다. 조사된 쓰기 런타임에는 없어 사용 불가입니다. |
| N30 | 지시 artifact 등록·steering 요청 | `work/steering/artifact`, `work/steering/request` | artifact 등록은 steering 접수/적용이 아닙니다. 실제 경계에서 허용한 `answer`, `parameter`, `cutover`, `amend_work`, `resume`만 정확한 대상/참조/ID로 제출합니다. 임의 Officer 재배정은 없습니다. |
| N31 | 평가된 steering 결과 내보내기 | `work/steering/export` 또는 평가된 `work/result` | 정확한 artifact/참조·독립 평가를 표시합니다. 미평가 부분 보고서를 완료로 취급하지 않으며 반환 텍스트를 안전하게 표시하고 실행하지 않습니다. |
| N32 | 긴 검토·선택 원문 변경 | 시간 잠금 없이 Owner의 대상별 재검증 | 60초가 지나도 변경되지 않은 제안을 결정할 수 있습니다. 실제 원문/대상 충돌은 Owner가 적용 시 거부하고 무관 문서 변경은 차단하지 않습니다. |
| N33 | 불확실한 변경·응답 대조 | 문서별 원래 ID, `mutation/receipt` 읽기 | 해당 문서의 불명을 표시하되 다른 문서는 계속 사용합니다. 정확한 영수증을 읽어 통신 불명을 해소하며 요청 재전송·producer 재실행·낙관적 적용은 하지 않습니다. |
| N34 | 아이콘·키보드·포커스·좁은 화면·테마 | 네이티브 아이콘 컨트롤, 다섯 패널, 접힘 상세, 명령 팔레트/메뉴 | tooltip·접근 가능한 이름과 선택 `aria-pressed`를 확인하고 키보드로 이동/실행/상세 열기·닫기를 합니다. 보이는 포커스·대비·상속된 외관을 유지하며, 좁은 너비의 그리드·긴 ID/근거도 확인합니다. 긴 상시 도움말이나 별도 브라우저는 필요하지 않습니다. |
| N35 | 선택 도구 모음·기본 대안 | Note Toolbar 명령 항목, 네이티브 명령/탐색 | 자동 제출 없이 같은 자체 화면으로 이동합니다. 선택 명령 부재는 안내하되 기본 편집/탐색·자체 명령 접근을 없애지 않습니다. |
| N36 | 미지원 접수·복구·관리의 경계 | 명시적 안내, 별도 운영자 유지보수 | 일반 다중 원문 Director 미리보기/접수, 임의 Officer/Team binding, 범용 사람 작업함, 실패 Curation 복구를 구현된 사용자 기능으로 제공하지 않습니다. 기존 recovery/repair CLI는 운영자 유지보수이며 문서 작업 흐름이 아닙니다. |

### 구성된 선택 method 연결

`POST work/methods {}`는 `{methods: [{method_id, purpose, available, reason, parameters}], observed_at}`를 반환합니다. `parameters`는 비밀을 제외한 고정 Owner 선택 값이며 클라이언트가 binding·actor·Graph·root·비공개 경로를 편집할 수 없습니다. `owner/status`의 `bindings.selected_methods`가 연결 구성을 나타냅니다.

`POST work/intake-selected`의 입력은 `{method_id, scope: {path, content_sha256}, request_id: <UUID>}`뿐입니다. Owner는 현재 정책·executor·설정과 정확한 단일 원문 Director 권한을 다시 확인하고, 결정적 receipt 재전송으로 중복 dispatch를 막습니다. 목록 조회는 접수나 Director 미리보기가 아닙니다. 직전 조회에서 가능했던 method도 이후 정책에 따라 거부될 수 있습니다.

`ops/config/work-methods.json`에는 아래 열한 method가 있습니다. 표나 catalog 존재가 아니라 Owner가 반환한 현재 가능 여부를 사용합니다. 조사된 로컬 쓰기 런타임의 binding은 메타데이터·관계/ontology·Research였으며 모든 항목의 실행을 확인한 것은 아닙니다.

| 정확한 method ID | 사용자 목적과 제한 |
|---|---|
| `knowledgeos.capture_normalization_team` | Capture 하나를 Pending 정규화로 변환합니다. 정확한 executor/접수가 등록되지 않으면 불가입니다. |
| `knowledgeos.capture_normalization_continuation_team` | 독립 평가 후 기준에 연결된 불변 continuation입니다. 일반 재시도가 아닙니다. |
| `knowledgeos.cited_draft_team` | 단일 원문에서 인용된 Pending 초안을 만듭니다. 접수 때 고정한 대상/제목/type을 쓰며 임의 대상 선택은 없습니다. |
| `knowledgeos.cited_research_team` | 정본이 아닌 인용 답변입니다. 기존 자유 질문/문서 개선 또는 구성된 선택 접수를 사용합니다. |
| `knowledgeos.relation_ontology_team` | 근거에 묶인 관계 후보입니다. 메타데이터 member 또는 구성된 선택 method이며 관계 진실성 확정은 아닙니다. |
| `knowledgeos.project_review_team` | 원문 범위의 프로젝트 검토입니다. Owner가 `apply_allowed: false`로 반환한 계획은 결정만 가능합니다. |
| `knowledgeos.knowledge_exchange_team` | 이미 Gateway가 수락한 근거를 읽고 로컬 적응을 제안합니다. 외부 전송을 허가하지 않습니다. |
| `knowledgeos.note_metadata_team` | 누락 property/tag 후보입니다. 정형 메타데이터 member 또는 구성된 선택 method입니다. |
| `knowledgeos.daily_corpus_stewardship_team` | 기존 Owner 잠정 변경 경계가 소비하는 빈 필드용 일일 후보입니다. scheduler 활성화가 아닙니다. |
| `knowledgeos.capture_steering_team` | 제한된 질문/부분 cutover/label 보고서입니다. catalog 선택으로 없는 steering binding을 만들 수 없습니다. |
| `knowledgeos.vault_maintenance_team` | 제한된 진단·복구 계획 제안입니다. 실제 복구는 운영자가 합니다. |

steering은 실제 확인된 경계에 한정됩니다. 질문 경계는 `answer`, 부분 경계는 `parameter`/`cutover`, 종료 경계는 구현된 `amend_work`/`resume`이며 이후 revision에서는 resume만 가능할 수 있습니다. 구현된 유일한 매개변수는 `report.order`의 `source_order`/`alphabetical`입니다. 답변과 구조 변경 이유는 256자, steering 만료는 1시간, control 만료는 24시간 이내입니다. 현재 권한·quiescence·대기 명령 배제·Director 수정 접수도 필요합니다. 범용 에이전트 대화·프로세스 제어·복구 시스템이 아닙니다.

## 설치된 플러그인과 통합/비통합 이유

아래는 업데이트 전 조사 증거이며 경로 기준은 `Vaults/KnowledgeHub/.obsidian-mac/`입니다. 열한 ID 모두 당시 활성 설정에 포함됐지만 현재 로드를 뜻하지 않습니다. 유지 소스 **0.6.0**, 과거 설치·부분 GUI 관측 **0.5.1**, 현재 로드 상태는 구분합니다. 제3자 코드는 조사 근거이지 수정하거나 의존할 내부 API 계약이 아닙니다.

| 플러그인 ID / 설치 버전 | 정확한 증거와 기존 공개 command ID | 통합 또는 비통합 이유 |
|---|---|---|
| `knowledgeos-thin-client` **0.3.1** | `plugins/knowledgeos-thin-client/manifest.json`; 업데이트 전 `main.js:20–29,84–100,438–440`; `contract.json` schema 5 대 개편 전 유지 소스 schema 6 | 자체 네이티브 Owner 화면입니다. 승인된 소스만 교체하고 설정은 보존합니다. 토큰을 요구하는 설치 소스 불일치는 업데이트 대상이며 manifest/복사만으로 로드를 입증하지 않습니다. 위의 자체 명령 여덟 개를 사용합니다. |
| `quickadd` **2.9.3** | `plugins/quickadd/manifest.json`; `data.json:10–200`; `main.js:71`; 아래 다섯 choice ID | 작성 마법사를 중복 구현하지 않고 기존 로컬 포착/생성을 재사용합니다. 온라인 기능은 꺼져 있고 providers는 비어 있습니다. 제한된 로컬 helper를 유지하되 내부 Templater 연결을 새 클라이언트 의존성으로 삼지 않습니다. |
| `templater-obsidian` **2.18.1** | `plugins/templater-obsidian/manifest.json`; `data.json:1–33`; `main.js:22`; `templater-obsidian:create-new-note-from-template` | 기존 포착/기간 템플릿용입니다. 전역 생성 trigger·system command는 꺼져 있고 startup template·user script는 없습니다. 공개 명령은 선택 대안이며 내부 renderer 호출 권한이 아닙니다. |
| `notebook-navigator` **3.4.1** | `plugins/notebook-navigator/manifest.json`; `data.json:66–127,131–132,181–192,308,333–356`; `main.js:688,726`; `notebook-navigator:open-daily-note`, `notebook-navigator:open-weekly-note`, `notebook-navigator:open-monthly-note` | 선택 달력/기간 탐색입니다. 노트 도구는 주간/월간만 직접 실행하며 일간은 core `daily-notes`를 사용합니다. Navigator의 일간 명령은 기존 기능 목록에만 포함합니다. 활성 프로필은 GUI 확인 대상입니다. 생성 확인과 외부 이미지/시작 시 update 다운로드 차단을 유지하고 내부 Templater 연결을 직접 호출하지 않습니다. |
| `breadcrumbs` **4.21.12** | `plugins/breadcrumbs/manifest.json`; `data.json:45–50,64–115,188–190,205–232`; `main.js:589`; `breadcrumbs:open-tree-view`, `breadcrumbs:open-matrix-view`, `breadcrumbs:jump-to-neighbour-picker` | 기존 Project 도구 모음의 트리 등 타입 관계 탐색용입니다. 추론 edge 자동 확정/기록은 하지 않습니다. save/layout 시 graph 재구축과 edge suggester는 끈 상태를 유지합니다. |
| `note-toolbar` **1.25.23** | `plugins/note-toolbar/manifest.json`; `data.json:10–42,194–201,310–316,1105–1112`; `main.js:212` 공개 명령 실행 | 문맥별 자체 명령의 선택 진입점입니다. Review Pending/일일 검토, 현재 노트 작업/메타데이터/개선/검색을 연결합니다. scripting은 끄고 무관한 항목은 보존합니다. 없어도 팔레트/네이티브 메뉴로 접근합니다. |
| `homepage` **3.6.0** | `plugins/homepage/manifest.json`; `data.json:1–23`; `main.js:2`; `homepage:open-homepage` | 기존 Home 탐색만 재사용합니다. 별도 Owner 연동이나 문서 변경은 필요 없습니다. |
| `obsidian-tasks-plugin` **8.4.0** | `plugins/obsidian-tasks-plugin/manifest.json`; `data.json:1–34,75–111`; `main.js:452`; `obsidian-tasks-plugin:toggle-done`, `obsidian-tasks-plugin:edit-task`, `obsidian-tasks-plugin:quick-search`; `99_System/Dashboards/Tasks.md:23–51` | 대시보드와 기본 명령을 재사용합니다. 현재 전역 필터는 `#task`입니다. task는 WorkRun/제안 승인이 아니며 태그 없는 task가 이 뷰에 나온다고 약속하지 않습니다. |
| `obsidian-meta-bind-plugin` **1.5.1** | `plugins/obsidian-meta-bind-plugin/manifest.json`; `data.json:1–30`; `main.js:159`; `obsidian-meta-bind-plugin:open-button-builder`, `obsidian-meta-bind-plugin:copy-command-id` | 기존 status/priority/next-action/today-focus property 입력용입니다. 입력은 의도한 metadata 변경이지만 Owner 결정/적용 버튼·JS·새 button template 의존성은 추가하지 않습니다. builder 명령은 목록 증거이지 필수 작업 단계가 아닙니다. |
| `obsidian-linter` **1.31.0** | `plugins/obsidian-linter/manifest.json`; `data.json`의 `lintOnSave`, `lintOnFileChange`, 제외 경로 `99_System`; `main.js:424`; `obsidian-linter:lint-file` | 사용자가 선택한 수동 정리만 허용합니다. 정확한 digest를 무효화하거나 정본을 바꿀 수 있어 검토 흐름에 자동/lint-all을 연결하지 않습니다. |
| `obsidian-git` **2.39.0** | `plugins/obsidian-git/manifest.json`; `data.json`의 자동 interval/backup/pull/push 차단; `main.js:295`; `obsidian-git:open-git-view` | 선택한 Git 조회 화면만 사용합니다. 문서 작업에서 commit/pull/push/원격/자동 backup을 연결하거나 실행하지 않습니다. |

### 기존 생성 명령의 정확한 ID

| 기존 QuickAdd 선택 | 정확한 command ID |
|---|---|
| Capture(포착) | `quickadd:choice:f29c95dc-1f39-4ff0-81bd-2ef12181bdcf` |
| Question(질문) | `quickadd:choice:5eddd4fc-24b8-4189-b926-c91f34b20ce1` |
| Idea(아이디어) | `quickadd:choice:9bba3ca7-d9c7-4df3-8ab4-425ddd7da0a6` |
| Project(프로젝트) | `quickadd:choice:dc798d84-85be-465e-b461-c4cc13d198aa` |
| Knowledge(지식) | `quickadd:choice:c44007fc-f3e5-47f0-b295-d9c99cc5aa9b` |

기존 단축키는 `.obsidian-mac/hotkeys.json`과 유지 설정 `ops/config/vault-profile.json:64–153`에 있습니다. core 공개 진입점은 `daily-notes`, `backlink:open-backlinks`, `app:toggle-left-sidebar`, `app:toggle-right-sidebar`입니다. KnowledgeOS URI handler는 이 계약에 없으므로 전용 URI를 안내하지 않습니다.

유지 설정의 원본은 `ops/config/vault-profile.json`이며 `vault_projection.py`가 예상 프로필 사본을 만들고 `vault_readiness.py`가 비교합니다. 승인된 통합 변경도 무관한 설정·capture ID·개인정보 보호·사용자 외관 선택을 보존해야 합니다. 조사에서는 실제 테마가 `moonstone`, 유지 설정은 `system`이었습니다. 생성 기준과 맞추기 위해 사용자 선호를 덮어쓰지 마세요. 기존 Review 도구 모음의 Home/Review Base/Weekly Review 이동은 Owner Pending이 아니므로, 스크립트 없는 자체 command 항목으로 그 진입점을 제공합니다.

## 소스 참조와 의도적으로 제외한 연결

- 사용자·에이전트·운영자 역할 구분: [USER_FEATURE_SPEC.md](../USER_FEATURE_SPEC.md), [SOURCE_CONTRACT.md](SOURCE_CONTRACT.md), `ops/config/vault-profile.json`.
- 자체 구현·계약: `ops/clients/obsidian-thin-client/{main.js,styles.css,manifest.json,contract.json}`. 설치 증거는 소스와 별도로 확인합니다.
- 네이티브 설치 패키지는 `manifest.json`, `contract.json`, `main.js`, `styles.css` 네 파일입니다. Obsidian은 진입점을 외부 모듈용 CommonJS evaluator로 로드하므로 로컬 통합 함수는 `main.js` 안에 포함하며 `require("./integrations")`와 별도 `integrations.js`를 사용하지 않습니다.
- 사용자 HTTP 전체 경계: `ops/src/vaultops/interfaces/{loopback_owner.py,exops_web.py}`. 단일 원문 접수 런타임은 `ops/src/vaultops/application/owner_runtime.py`, method 정의는 `ops/config/work-methods.json`입니다.
- binding이 필요한 control/steering: `ops/src/vaultops/application/{curation_control.py,curation_steering.py}`. 소스 존재만으로 현재 런타임에 구성됐다고 보지 않습니다.
- 에이전트의 원문/Base/링크/task/인용 도구: `ops/src/vaultops/application/read_tools.py`, `ops/schemas/mcp/capabilities.json`. MCP 탐색, Graph 제안, artifact/assessment 제출, model-provider 연결은 범위가 제한된 에이전트 기능이며 추가 사용자 버튼이나 권한이 아닙니다.
- `work/submit`은 정확히 보존된 Director 권한을 요구하는 저수준 신뢰 경계이지 일반 사용자 양식이 아닙니다. 여기서 WorkSpec·임의 binding·다중 원문 미리보기를 만들어내지 않습니다.
- 실패한 Curation의 조정/복구와 기존 `vaultctl` 유지보수는 운영자 경계에 남습니다. 없는 네이티브 사용자 계약을 producer 재실행·자동 복구·터미널 문서 작업으로 대체하지 않습니다.
