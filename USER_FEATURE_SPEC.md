# KnowledgeOS 사용자 기능 명세

KnowledgeOS는 사용자가 KnowledgeHub Vault에 자유롭게 쓴 아이디어와 발상을 **다시 쓸 수 있는 형태로 정리·축적**하고, 축적된 생각을 이용해 **지금의 사고를 강화**하는 가벼운 Operation입니다.

이 문서는 사용자의 의도를 정합니다.

- 시스템 계약: [Blueprint](OBSIDIAN_VAULT_BLUEPRINT.md)
- 기능별 동작과 인수 시나리오: [기능 명세](docs/CAPABILITIES.md)

사용자의 결정에 따라 [기능 명세](docs/CAPABILITIES.md)가 동작과 인수 기준의 최상위 계약입니다. 이 문서는 의도를, Blueprint는 시스템 제약을 보충하며 기능이나 인수 범위를 줄이지 않습니다. 충돌은 함께 바로잡고, 이 우선순위로 Core 계약이나 스키마를 바꾸지 않습니다.

처음 사용하는 분도 코드를 읽지 않고 기능을 이해하도록 필수 한국어 기술 위키를 제공합니다. 설명 원고와 모든 HTML 표면은 단일 Sites 모노레포의 `knowledgeos/`에 두고, KnowledgeOS가 내용의 정확성을 책임집니다. 목적에서 실제 처리 흐름과 선별된 코드 근거까지 이어 읽으며 구현·계획·실행 관찰을 구별합니다. Sites의 독립 worktree에서 수정해 GitHub PR로 병합하고, 문서는 ExOps 정적 내보내기와 비공개 Tailscale Serve로 읽습니다. 이 설명 사이트가 Owner 실행이나 콘솔 활성화를 뜻하지는 않습니다.

## 지키는 약속

1. **Vault는 사용자의 것입니다.**
   - 맥·iPhone·iPad 어디서든 어떤 노트든 자유롭게 쓰고, 고치고, 옮기고, 지울 수 있습니다.
   - frontmatter·폴더·등록 없이 쓴 Markdown도 그대로 유효합니다.
   - 테마·CSS 스니펫·커뮤니티 플러그인도 기기마다 자유롭게 설치·삭제·업데이트하고 설정합니다. KnowledgeOS의 승인·등록·허용 목록은 필요하지 않습니다.
   - 외형·플러그인 목록·설정은 사용자 소유입니다. 제공 프리셋은 출발점일 뿐이며, 유지보수 때 명시적으로 요청하지 않은 개인 설정을 덮어쓰거나 초기화하지 않습니다.
2. **문서 작업은 Obsidian 안에서 합니다.**
   - 쓰기·검색·보기는 Obsidian core와 community plugin이 맡습니다.
   - KnowledgeOS 기능 호출은 최소한의 Thin Client 명령과 사이드바 탭이 맡습니다.
   - 관리는 브라우저 콘솔과 CLI가 맡고, 문서 작업은 거기서 하지 않습니다.
3. **유지보수는 가능한 한 오픈소스에 맡깁니다.** 화면·보기·템플릿·버튼은 plugin을 쓰고, Thin Client는 커지지 않게 유지합니다.
4. **자동으로 바꾸는 것은 작고 기계적인 것뿐입니다.**
   - 빠진 frontmatter를 채우는 규격화와, 데일리 노트의 미완료 항목 인계만 자동입니다. 정해진 선택지(enum) 안에서 고르는 값은 System One이 충분히 확신할 때만 고릅니다.
   - 그 밖에 판단이 필요한 변경은 제안으로 만들고, 적용은 사용자가 결정합니다.
5. **Git이 백업이자 이력입니다.**
   - Vault는 GitHub private repo와 동기화됩니다. 맥은 사용자의 동기화 방식으로, iOS는 Working Copy로 합니다.
   - KnowledgeOS는 git 명령을 실행하지 않고, diff가 작고 읽기 쉽게 씁니다.
6. **잃으면 안 되는 것은 Vault에 둡니다.** KnowledgeOS 내부 상태와 자격증명은 Vault 밖에 있어 GitHub로 올라가지 않으며, 잃어도 지식은 남습니다.
7. **근거를 밝힙니다.** 결과는 이미 쓴 노트를 정확히 인용하고, 근거·추론·근거 없음을 구분합니다.

## 여섯 가지 기능

| 기능 | 사용자가 얻는 것 | 요청 방법 | 결과가 나타나는 곳 |
|---|---|---|---|
| **정리** | 노트의 종류·제목·태그·관련 노트·위치 제안. 포착을 아이디어·지식·질문·자료로 바꾸거나 기존 노트에 연결 | "정리 요청"(노트·선택·폴더), 매일 자동 | `01_AI_Review/Proposals/`의 제안 노트 |
| **규격화** | `id`·`type`·`created`·`status`·`ai_policy`가 채워지고 값 모양이 정돈된 노트. 본문 끝에 잘못 들어간 `tags:` 같은 줄은 frontmatter로 이동 | 세 경로(아래) | 노트 자체(작은 frontmatter 변경) |
| **검색** | 키워드 검색(Obsidian 검색), 관련 노트, 준비된 경우 의미 검색 | Obsidian 검색, "관련 노트"·"의미 검색" | 검색 결과에서 해당 줄로 이동 |
| **강화** | 아이디어 발전(`develop`), Vault에 묻기(`ask`), 프로젝트 돌아보기(`review`). 내가 이미 쓴 생각, 연결과 긴장, 빈틈과 반론, 다음 걸음을 인용과 함께 정리 | "강화: 발전 / 묻기 / 돌아보기" | `01_AI_Review/Reports/`의 보고서 노트 |
| **문서 관리** | 처리 안 된 포착, 오래 안 본 아이디어, 열린 질문, 검토 대기, 충돌·오류 노트, 보관 제안 | Bases 화면, 관리 콘솔, Discord 요약 | Bases·콘솔·Discord, 제안 노트 |
| **데일리 노트** | 하루의 메모·할 일·일정·하루 정리. 이전 날짜의 미완료 항목을 오늘로 인계하고 Discord로 할 일을 알림 | 매일 07:00 인계와 할 일 알림, `daily` 작업 직접 실행 | `10_Journal/Daily/YYYY-MM-DD.md`, Discord |

정리·강화는 원본 노트를 직접 바꾸지 않습니다. 보고서는 만들어진 뒤 사용자의 문서입니다. 자유롭게 고치고 옮기며, `type`을 바꾸면 자기 지식으로 편입됩니다.

### 데일리 노트

- 본문은 **메모 / 할 일 / 일정 / 하루 정리** 네 구역입니다. 할 일·일정에는 주석 예시를, 하루 정리에는 잘한 일·아쉬웠던 점과 이유·새로 알게 된 것·고마웠던 순간·내일 먼저 할 일을 돌아보는 질문을 둡니다. `type: daily`이며 `status`는 없습니다. 템플릿으로 직접 만들거나, 오늘 노트가 없으면 Owner가 같은 레이아웃으로 정확한 날짜 경로에 만듭니다.
- 이전 날짜 노트에서 들여쓰기 없이 시작하는 미완료 체크박스(`[ ]`, `[/]`) 항목과 바로 이어진 들여쓰기 줄을 날짜·문서 순서대로 오늘로 옮깁니다. 일정 아래의 항목은 일정으로, 나머지는 할 일로 인계합니다. 완료·취소·이미 인계된 항목, 코드 블록·HTML 주석 속 항목, 일반 목록은 옮기지 않습니다.
- 오늘 노트의 기존 줄은 순서대로 모두 보존하고 해당 구역 끝에 항목만 더합니다. 같은 첫 줄이 이미 있으면 다시 넣지 않으며, 원본의 인계된 항목은 체크박스만 `[>]`로 바꿉니다. 그사이 바뀐 파일은 덮어쓰지 않고 다음 실행에서 다시 살핍니다.
- **데일리 노트는 AI 제안의 대상이 되지 않습니다.** 직접 정리를 요청해도 거절하며 폴더 정리·자동 정리에서도 제외합니다.
- 아침 알림은 오늘 노트의 열린 최상위 `#task` 항목과 다른 일반 노트의 기한 지난·오늘 마감 `#task` 항목만 알려줍니다. `config/capabilities/C12.json`의 편집 가능한 `settings.reminder`로 따뜻한 한국어 인사와 마무리를 구성하고, 할 일 이름에 `(기한이 지났어요)`·`(오늘까지)`를 붙입니다. `#task`와 Tasks 이모지 필드는 빼고 링크는 보내지 않습니다. `#task` 항목이 없으면 보내지 않고 같은 날 두 번 보내지 않습니다.

### 규격화의 세 경로

| 경로 | 방법 | 특징 |
|---|---|---|
| 작성할 때 | QuickAdd·Templater 템플릿으로 노트 생성 | 처음부터 규격에 맞는 frontmatter. KnowledgeOS 호출 없음 |
| 수동 | Thin Client "규격화 미리보기"(노트·선택·폴더) | 바뀔 diff를 먼저 보여주고 확인하면 적용. 방금 편집한 노트도 가능. 대량 정리는 CLI `--dry-run`/`--apply` |
| 자동 | 매시간 예약 작업 | 최근 10분 안에 편집한 노트는 건너뜀 |

규칙 1-8은 결정적입니다. 폴더로 정할 수 없는 `type`, 잘못된 `status`·`confidence`, 빠지거나 잘못된 `capture_kind`처럼 **선택지가 고정된 값**은 System One(아래)이 고릅니다.

- 수동 미리보기에서는 System One이 고른 값과 확신도를 diff 위에 보여주고, 확인해야 적용합니다. 자동 경로는 확신도가 기준(기본 0.7) 이상인 값만 적용하고 나머지는 그대로 둡니다.
- 본문의 다른 부분, 관련 없는 기존 속성, 파일 위치는 바꾸지 않습니다. `ai_policy`·`sensitivity`, 이미 유효한 종류, 알 수 없는 기존 종류, 제목·태그 같은 자유 값은 System One에게 묻지 않습니다. 데일리 노트에는 `status`를 추가하지 않습니다.
- `deny` 노트는 읽거나 언급하지 않습니다. 충돌 표시가 있거나 YAML이 깨진 허용 노트는 건드리지 않고 주의 항목으로 알려줍니다.
- 바뀐 것은 "자동 변경 되돌리기"로 되돌립니다. 그 뒤 노트를 고쳤다면 Git 이력으로 되돌립니다.
- Vault 맨 위나 임의의 폴더에 썼다는 이유만으로 일반 Markdown을 건너뛰지 않습니다.

## Obsidian 안에서 쓰는 방법

**plugin이 맡는 일:**

| 하는 일 | plugin |
|---|---|
| 노트 생성(포착·아이디어·질문·지식·자료·프로젝트·데일리) | QuickAdd, Templater, Daily Notes |
| 키워드 검색 | Obsidian Search |
| 검토 대기·포착함·아이디어·프로젝트 목록 | Bases |
| 제안 노트의 "적용·거절" 버튼, 노트별 KnowledgeOS 버튼 | Note Toolbar(필요하면 Meta Bind) |
| 링크·계층·할 일·홈 | Backlinks, Breadcrumbs, Tasks, Homepage, Notebook Navigator |
| 서식 정리 | Linter(직접 실행할 때만) |
| Git 이력 보기 | Obsidian Git(보기만) |

이 목록은 연동 예시이지 설치 제한이 아닙니다. 플러그인을 끄면 그 플러그인의 화면 기능은 없어질 수 있지만 Vault 자체는 유효합니다. 외부 플러그인은 KnowledgeOS의 `ai_policy` 보호 대상이 아니므로, 해당 플러그인의 읽기·쓰기·네트워크 동작은 별도로 확인합니다.

**Thin Client가 맡는 일:** 기존 KnowledgeOS 명령을 작은 사이드바 탭에서도 호출합니다.

- 명령: 규격화 미리보기, 정리 요청, 강화, 자유 요청, 관련 노트·의미 검색, 적용·거절, 자동 변경 되돌리기, 관리 콘솔 열기, Owner 시작·중지(데스크톱).
- 리본 아이콘이나 `사이드바 열기` 명령으로 오른쪽 탭을 엽니다. 기존 명령 팔레트·파일·편집기 메뉴·Note Toolbar도 유지합니다.
- **규격화·정리·강화·찾기**를 누르면 네 버튼 아래에 해당 기능 창이 열립니다. 강화의 세부 선택·질문·기간, 검색창·결과, 규격화 diff·적용·취소를 모두 사이드바 안에서 처리하며 팝업은 띄우지 않습니다. 메뉴 선택만으로 작업을 실행하지는 않습니다.
- 제안 노트에서만 적용·거절을 표시합니다. 더보기 역시 사이드바 안에 자유 요청·되돌리기·관리 콘솔을 열고, Owner 시작·중지는 전원 버튼 하나로 모읍니다. 입력 중인 내용은 메모리에만 유지합니다.
- 탭으로 초점을 옮겨도 표시된 노트를 대상으로 유지하며, 다른 파일을 열면 대상도 바뀝니다. 확정하지 않은 규격화 미리보기는 기능이나 노트를 바꾸면 취소됩니다. 진행·오류·결과 링크도 사이드바 안에 표시하며 작업 이력이나 관리 화면은 추가하지 않습니다. 기존 명령 팔레트·문맥 메뉴의 입력창은 유지합니다.
- 요청에는 노트 경로와 마지막으로 본 내용의 해시만 보내고 본문은 보내지 않습니다.
- Owner는 Mac에서 필요할 때 켜지는 사용자 서비스(LaunchAgent)입니다. **Owner 시작·중지** 명령이 직접 누를 때만 켜고 끄며, Obsidian을 종료해도 Owner는 계속 실행됩니다. 켜졌는지·꺼졌는지는 health가 실제로 응답하는지 보고 알려 주고, 확인하지 못하면 확인하지 못했다고 알립니다.

**제안 검토:**

- 제안 노트에는 변경 체크리스트와 diff가 있습니다.
- 원하지 않는 항목의 체크를 해제하고 **적용**하면 체크한 변경만 반영됩니다.
- 그사이 대상 노트가 바뀌었으면 적용하지 않고 다시 요청하도록 안내합니다.
- 같은 대상에는 열린 제안을 하나만 둡니다. 다시 만들 때도 KnowledgeOS가 쓴 그대로 남아 있는 제안만 교체하며, 사용자가 고친 제안과 내부 기록이 없는 제안은 보존하고 중복 생성하지 않습니다.
- **거절**하면 제안 파일이 지워집니다. 기록은 Git에 남습니다.

## 관리: 브라우저와 CLI

- **브라우저 관리 콘솔(로컬 전용):**
  - 볼 수 있는 것: 작업 이력(WorkSpec, Graph, 역할별 호출·모델, 평가, 남은 작업, TeamReview), 예약 작업 실행 기록, 결과 불명, Bases로 볼 수 없는 주의 항목, 역할별 학습 기록, 상태.
  - 할 수 있는 것: 작업 취소, 예약 작업 일시정지·재개, 결과 불명 확인 기록.
- **CLI:** 상태 점검, 색인 재구축, Vault 점검, 대량 규격화, 역할 profile·예약 작업·Owner 서비스 설치, 복구.

## KnowledgeOS의 조직

각 객체는 서로 겹치지 않는 책임 하나만 온전히 소유합니다.

| 객체 | 책임 |
|---|---|
| Director | 요청을 작업 명세(WorkSpec)로 접수. 정형 요청은 코드가, 자유 요청만 모델이 해석 |
| CurationManager와 teammate(`scout`·`classifier`·`linker`) | **정리** Team. Manager는 필요한 teammate와 순서를 정하고, teammate는 각자 수집·분류·연결만 함 |
| ThinkingManager와 teammate(`retriever`·`connector`·`challenger`·`synthesizer`) | **강화** Team. 근거 수집, 연결·긴장, 빈틈·반론, 보고서 작성을 나눠 맡음 |
| StandardizationOfficer | **규격화**(결정적 코드, 선택지가 고정된 값은 System One에게 물음) |
| StewardshipOfficer | **문서 관리**: 주의 항목, 보관 제안, 요약 내용(결정적 코드). System One이 판단한 상태 변경(답한 질문·끝난 프로젝트·버린 아이디어)도 제안으로 만듦 |
| DailyOfficer | **데일리 노트**: 오늘 노트 생성, 미완료 일정·할 일 인계, 아침 할 일 알림(결정적 코드) |
| System One | Owner가 OpenAI Decisions API로 묻는 빠른 선택형 판단. 선택지·확률만 답하고 쓰지 않음. 정리·강화의 모델 teammate가 `system_one` 도구로 읽은 노트에 대해 참고 질문을 하고, 관련 노트 거르기·자유 요청 사전 분류에도 씀 |
| EvalOfficer | 모든 작업 결과를 독립적으로 평가. 만들거나 고치지 않음 |
| Owner | 권한·정책, 파일 쓰기, 기록. 내용을 판단하지 않음 |

- **Team의 진행:** 결과가 기준에 못 미치면 같은 Manager가 남은 부분만 다시 구성합니다. 예산 안에서만 합니다.
- **TeamReview:** 완료 후 Manager가 teammate 성과와 개선 제안을 남깁니다. 제안은 TeamReview·역할 피드백·콘솔과 보관 기한이 있는 로컬 관찰 기록에만 남고, 검증된 성과로 간주하거나 HermesTrace 전송 후보로 자동 등록하지 않습니다. 역할 변경은 사용자가 명세를 고쳐서만 합니다.
- **Hermes profile:** 모델을 쓰는 역할마다 별도 profile을 둡니다. Discord 봇은 모델 없는 `kos-ops` profile이 맡으며 알림 발송에만 쓰입니다.
- **현재 모델 설정:** `classifier`·`linker`·`connector`는 `openai-codex/gpt-6.1-terra`, Director·Manager·EvalOfficer·`challenger`·`synthesizer`는 `openai-codex/gpt-6.1-sol`을 쓰며, 모든 모델 역할의 추론 노력은 `high`입니다. `classifier`에는 로컬 서버 설정이나 대체 모델을 두지 않습니다.

## 자동으로 일어나는 일

Owner가 켜져 있는 동안 Owner 안의 스케줄러가 다음 작업을 시작합니다.

| 작업 | 기본 주기 | 하는 일 |
|---|---|---|
| 규격화 | 매시간 | 바뀐 `approve` 노트를 규격화. 고정 선택지 값은 System One이 확신할 때만 채움 |
| 임베딩 | 매시간 30분 | 새로 생기거나 바뀐 노트의 의미 검색용 벡터를 만듦 |
| 정리 | 매일 06:00 | 새 포착과 종류 없는 `approve` 노트에 정리 제안. 한 번에 최대 10개 |
| 데일리 노트 | 매일 07:00 | 미완료 일정·할 일 인계와 할 일 알림 |
| 요약 | 매일 08:00 | 문서 관리 후 확인할 것이 있을 때만 Discord 검토 요약 발송 |

- **Discord 요약:** 검토할 제안·오래된 제안, 새 보고서, 3일 넘은 포착, 확인 대기 작업, 확인이 필요한 충돌·오류·결과 불명, 다시 볼 노트를 묶습니다. 제안·새 보고서·오래된 포착·확인 대기·주의 항목 중 하나가 있을 때만 보내며, 다시 볼 노트만 있으면 보내지 않습니다. 자동 규격화 내역은 콘솔에서만 봅니다. 실패 작업은 다른 확인거리가 있어 요약을 보낼 때만 작업 이름을 한 줄로 모아 덧붙이고, 실패만으로는 보내지 않습니다.
- **즉시 알림:** 충돌·해결되지 않은 효과의 결과 불명만 바로 알립니다. 알림 자체의 전송 결과 불명은 새 알림을 만들지 않습니다. 실패·차단·놓친 작업은 즉시 알리지 않습니다.
- **알림 내용:** 요약과 즉시 알림은 `approve` 노트의 제목·건수·링크만 보냅니다. 데일리 알림은 현재 유효 정책이 `deny`가 아닌 노트의 할 일 이름만 보냅니다. `deny` 노트는 읽거나 언급하지 않으며 건수로도 드러내지 않습니다. 오늘 노트 순서대로 먼저, 다른 노트의 마감 할 일을 그다음에 최대 20줄까지 쓰고 남은 할 일 수를 덧붙입니다. 노트 제목·링크·일반 일정은 넣지 않습니다.
- **Discord는 알림만 합니다.** 메시지·반응·답장으로는 아무것도 승인되지 않습니다.
- **Owner가 꺼져 있으면** 작업이 실행되지 않습니다. 잃는 것은 없습니다. 다시 켜고 `startup_delay_minutes`(기본 3분)가 지나면 놓친 작업을 한 번씩만 실행하고, 이후에는 정해진 시각에 실행합니다. 일시 중지한 작업은 건너뛰며, 재개해도 놓친 실행을 몰아서 하지 않습니다.

## 개인정보: `ai_policy`

| 값 | 의미 |
|---|---|
| `approve` (기본) | 모든 자동화·결정적 처리·모델(로컬 Ollama와 System One 포함)·예약 작업에 사용 가능. 보고서가 아닌 노트는 허가된 수신자에게 Gateway 조회로 제공 가능 |
| `deny` | KnowledgeOS가 읽지도, 색인하지도, 규격화·전송·내보내기·알림에 쓰지도 않음 |

- 빠졌거나 빈 `ai_policy`는 `approve`입니다. 퇴역한 `remote_ok`·`ask`·`local_only`를 포함한 알 수 없는 값은 모두 `deny`로 닫힙니다.
- `sensitivity: confidential`은 개인정보 허용 여부를 바꾸지 않습니다.
- `ai_policy` 결정은 사용자의 몫입니다. KnowledgeOS는 규격화 규칙 6으로 빠진 값에 `approve`를 넣거나 새 노트에 기본·상속 정책을 쓸 뿐, 기존 결정을 바꾸지 않습니다.
- 결과물은 읽은 모든 입력 중 가장 엄격한 정책을 물려받습니다. 작업 도중 노트가 `deny`로 바뀌면 허용하지 않습니다.
- `ai_policy`는 모델·자동화의 사용 범위만 정합니다. GitHub 동기화 여부는 Vault의 `.gitignore`가 정합니다.
- System One에도 요청 출처와 관계없이 현재 `approve`인 노트만 보내며, 보내기 직전에 다시 확인합니다.

## 모바일

**지금:**

- iPhone·iPad에서는 Obsidian으로 쓰고 Working Copy로 GitHub와 동기화합니다.
- 모바일용 KnowledgeOS는 만들지 않습니다.
- 맥이 변경을 받아오면 맥의 KnowledgeOS가 예약 작업으로 규격화·정리합니다.
- Discord 알림의 Obsidian 링크는 모바일에서도 열립니다.
- iPhone 설정은 사용자 소유의 `Vaults/KnowledgeHub/.obsidian-phone/`에 두고 MacBook이나 폰에서 자유롭게 바꿉니다. `config/obsidian-mobile/profile/`은 참조 프리셋이며 현재 개인 설정과 같아야 하는 기준본이 아닙니다. 자동 업데이터나 초기화는 없습니다. 폰 폴더 전체를 기존 GitHub–Working Copy Pro로 동기화하며 공통 `.DS_Store`·임시 파일 제외만 유지하므로 workspace·캐시도 생성되면 동기화될 수 있습니다. Mac과 폰의 설정 폴더는 달라서 Vault를 동기화해도 Mac의 테마·플러그인 설정이 폰 프로필로 자동 복사되지는 않습니다.
- QuickAdd 명령은 **Today · 입력 · 아이디어 · 질문 · 지식 · 자료 · 프로젝트**입니다. 입력은 코어 고유 노트 생성으로 Inbox 노트를 열고 기본 편집기에서 씁니다. 나머지 다섯 유형은 제목 한 번만 입력하면 기존 템플릿과 표준 폴더로 새 노트를 만들며 별칭은 빈 목록으로 시작하고 기존 노트는 덮어쓰지 않습니다. 제목의 실행 구문은 거부하며 본문을 QuickAdd Capture로 재해석하지 않습니다.
- Note Toolbar는 기존 `type`을 읽어 노트를 옮겨도 종류별로 전환합니다. 폰·태블릿 하단은 기존 네 버튼의 위치와 선택을 유지하고 다섯 번째 **동기화** 메뉴를 덧붙입니다. 메뉴는 **받기·보내기** 두 항목만 제공하며, 동기화 툴바 자체는 별도 행으로 표시하지 않습니다. 명시적 Working Copy 메타데이터와 새 세 Shortcut 자산이 모두 준비될 때까지 기존 두 URI를 유지하고 새 이름으로 바꾸지 않습니다. 이 항목들은 새 native 구현이 아닙니다. 문맥에 따라 포착함·할 일·관련 종류의 새 노트·검토함으로 연결됩니다. 알 수 없는 종류도 검토 폴더 또는 공통 툴바를 사용하며 Mac에는 표시하지 않습니다. 모바일에서 AI 실행·제안 적용·거절·Owner 기동은 추가하지 않습니다.
- 제공하는 폰 전용 `mobile-toolbar` CSS 스니펫은 터치 영역을 최소 44×44px로 늘립니다. 좁은 화면은 라벨을 숨기지 않고 두 줄로 배치될 수 있습니다. 사용자는 스니펫과 테마를 자유롭게 바꾸거나 끌 수 있습니다.
- `Home.md`는 할 일·대기 제안·Inbox, 전체 폭 진행 프로젝트, 열린 질문·읽는 중인 자료, 30일 방치 아이디어의 일곱 구획을 유지합니다. `Mobile.md`는 설명 없이 할 일·Inbox·읽기 전용 AI 검토(원래 Queue대로 제안과 보고서)·진행 중 프로젝트·전체 목록 순서로 시작합니다. Tasks는 완료·기한/데일리 범위를 고르고 정렬한 뒤 Home 6·Mobile 5로 제한하며 count·backlink·tag를 숨깁니다. 추가 compact Base view는 작은 목록 limit(Home 3, Mobile 5, ideas 2; 원래 Projects Now 5)을 맡고, 각 구획 링크는 바꾸지 않은 원래 full view나 dashboard를 엽니다.
- 제공 스타일의 원본은 `99_System/CSS/dashboard.css`이며 `knowledgeos-home` 사본을 Mac·폰 스니펫·참조 프리셋에 둡니다. Home/Mobile의 16px 본문과 범위형 44×44 동작, 노트 폭 기반 배치를 맡습니다. 참조 프리셋은 `[mobile-toolbar, knowledgeos-home]`을 켜지만 사용자 설정을 강제하지 않으며, 유지보수 시 별도로 꾸민 사본을 임의로 되돌리지 않습니다. 프로필 준비와 Mac 렌더링은 실제 iPhone 런타임·원격 전달과 별개입니다.
- QuickAdd·Note Toolbar·Templater·Tasks·Homepage는 제공하는 기본 워크플로 플러그인이지 다섯 개 제한이 아닙니다. Style Settings·Notebook Navigator 등도 자유롭게 추가합니다. 일곱 QuickAdd 명령(Today·입력·아이디어·질문·지식·자료·프로젝트)은 계속 실제 작성 진입점입니다. 기본 **Today**는 기존 Daily를 수정 없이 열거나 `T10_Daily.md`를 명시적으로 렌더링합니다. Templater 새 파일 자동 실행은 폰과 Mac 모두 끄고, Mac의 기존 Daily Note 버튼과 `Mod+Shift+E` 단축키는 폰과 같은 QuickAdd **Today**를 호출합니다. Core Daily Notes 직접 생성은 Templater를 자동 실행하지 않으며, Core Daily Notes를 직접 호출하는 외부 호출자는 이 전환 밖입니다. 모바일 코어 Daily Notes도 끄고, Tasks의 `#task`·이월 `[>]` 구분과 `Mobile.md` 시작 화면을 사용합니다. 기존 Mac sync push와 Working Copy Pro pull 후 Obsidian **Settings → Files and links → Override config folder**를 `.obsidian-phone`으로 지정하고 다시 불러옵니다. 새 파이프라인·디렉터리 연결은 만들지 않으며 원격 전송·iPhone 실제 적용과 별개입니다.

**후속 과제**(구현 공간 확보, 상세는 [기능 명세](docs/CAPABILITIES.md)의 Follow-up). M1은 이번에 명세되었고 M2·M3는 예약 상태입니다:

| 과제 | 내용 |
|---|---|
| M1 단축어 (부분 소스; 3종 배포 미준비) | 목표는 `KOS 캡처`·`KOS Pull`·`KOS Push` 세 개입니다. 별도 음성·Today 단축어는 두지 않습니다. 캡처는 메모를 먼저 받고, 그다음 클립보드에서 native `detect.link`로 URL을 추출해 HTTP(S) 전체 줄만 남긴 뒤 실제 URL을 보여 주고 포함 여부를 묻습니다. URL은 필수가 아니며 명시적으로 확인한 경우에만 저장합니다. 캡처 호스트 receiver는 `KOS Capture` Macro의 `body`·`source_url`을 받아 `00_Inbox/Captures/<uuid>.md`를 만들고 raw memo를 평가하지 않습니다. Pull/Push는 Obsidian 안에서 native Working Copy 동작을 호출하는 목표이며 Working Copy 열기·상태 리다이렉트는 없습니다. Push는 실제 변경을 한 번 확인한 뒤 현재 provider가 허용하는 범위에서 수정·신규·삭제 파일을 dated 기본 메시지로 자동 commit하고 Pull 없이 push합니다. staging UI·수동 메시지 입력은 없고 비동기 결과를 완료로 가장하지 않습니다. Shortcut이 잠시 foreground될 수 있으며 zero-app-switch를 보장하지 않고 Working Copy를 자동으로 열지 않습니다. 캡처 host receiver는 `test_capture_note_contract_and_c2_std7`로 구현·검증되었고, unsigned `KOS 캡처` 생성 소스(`config/obsidian-mobile/automation/shortcuts_author.py`, `python3 config/obsidian-mobile/automation/shortcuts_author.py --out-dir <dir>`)는 macOS 26.6.2 `WorkflowKit`/`ActionKit` 읽기 전용 admission 점검(25개 구체적 native class, discontinued·알 수 없는 키·nil parameter state·선행 UUID 누락 없음)을 통과했습니다. 이는 정적 생성·Mac admission 확인일 뿐 전체 분기·값 실행, iPhone 가져오기, 실제 기기 UI, 서명, 배포가 아니며 live Vault에 서명된 Shortcut을 설치하지 않았습니다. `KOS Pull`·`KOS Push`는 명시적 Working Copy `RepoEntity`·수정 파일 필터·Commit all-modified/메시지 메타데이터가 없어 차단 중이며(현재 4개 `EntityAppIntent` export 확보, 두 번째 복호화 파일은 바이트 동일·미설정, Commit Repository 펼친 화면 스크린샷 요청), 이전 5개 생성 자산과 generator는 실제 iPhone 실패 후 사용 중지·live Vault 제거(archive 사본만 유지)되었습니다. 기존 Obsidian Today·Mac Templater false·7개 QuickAdd·`.gitignore`·Owner 중지 정책·AppleScript note opener는 유지합니다. |
| M2 Vault를 통한 요청 | 노트에 `kos_request` 표시나 `00_Inbox/Requests/` 요청 노트를 두면, 맥 KnowledgeOS가 동기화 후 처리하고 결과가 다시 동기화됨 |
| M3 GitHub Actions | push 시 YAML·충돌 표시·중복 ID 같은 결정적 점검 |

현재 3개 클라이언트 배포는 아직 준비되지 않았습니다. `KOS Capture` host receiver와 공개 C2 계약, 그리고 unsigned `KOS 캡처` 생성 소스 및 Mac `WorkflowKit`/`ActionKit` 정적 admission 점검은 완료되었으나, 전체 분기·값 실행과 iPhone 가져오기·실제 기기 UI·서명·배포는 검증되지 않았습니다. `KOS Pull`·`KOS Push`는 현재 4개 `EntityAppIntent` export는 확보했으나 두 번째 복호화 파일도 바이트가 같고 명시적 `RepoEntity`·커밋 옵션이 비어 있어 소유자 Commit Repository 스크린샷을 기다리며 차단되어 있습니다. 실제 iPhone 사용자 보고에서 실패한 이전 5개 흐름과 생성기는 사용 중지되어 live Vault에서 제거되었고 archive 사본만 남아 있습니다.

GitHub Actions에는 제약이 있습니다.

- 모델을 호출하지 않고 KnowledgeOS 자격증명을 쓰지 않습니다.
- commit은 Git 작성자 규칙을 바꾸는 결정 후에만 합니다.

`kos_request` 속성과 `00_Inbox/Requests/` 폴더는 이 용도로 예약되어 있습니다.

## Hermes와 함께 성장하기

- **스스로 배웁니다.**
  - 각 역할 profile은 Hermes native skill·memory로 자기 책임 안에서 스스로 익힙니다.
  - 역할끼리 기억을 공유하지 않으며, 학습은 권한을 넓히지 못합니다.
- **사용자의 반응에서 배웁니다.** 제안의 적용·체크 해제·거절, 되돌리기, 보고서 수정·삭제가 해당 역할의 피드백이 됩니다.
- **다른 Operation과 함께 성장합니다.**
  - 실제 실패, 사용자의 수정·거절·되돌리기, 정책 거부처럼 Core의 기존 분류에 맞는 근거가 있는 사건만 본문 없이 HermesTrace 후보로 전달합니다.
  - TeamReview의 일반 개선 제안은 아직 평가하지 않은 로컬 관찰입니다. 후보나 전송 대기열로 자동 넘어가지 않으며, 나중에 자동 승격된다는 약속도 없습니다. 이후 실제 사용자 수정 같은 사건이 생기면 그 사건 자체의 근거로 새로 기록합니다.
  - HermesTrace가 승격한 능력은 명시적으로 버전을 고정해 채택할 때만 반영됩니다. Core 계약과 스키마는 그대로 둡니다.
- **다른 Operation과 정보를 주고받습니다.**
  - 서명 인증을 거친 다른 Operation이 보낸 정보는 포착 노트로 들어와 같은 규격화·정리를 거칩니다.
  - ExecutiveOffice를 포함해 발신 권한(`INTEROP_REQUEST`), 서명 인증, KnowledgeOS 수신 허가를 모두 갖춘 Operation의 조회에는 보고서가 아닌 `approve` 노트만 응답합니다. 기반 역할 이름만으로 자동 요청 권한이 생기지는 않습니다.

## 하지 않는 일

- 브라우저·CLI에서 문서 작업하기. 둘은 관리용입니다.
- Thin Client에 명령 호출 탭을 넘어 문서·작업 목록이나 설정을 늘리기. plugin으로 할 수 있으면 plugin을 씁니다.
- 사용자 노트 삭제, 자동 병합, 판단이 필요한 변경의 자동 적용.
- git commit·pull·push.
- 다른 모델로 몰래 바꾸기, 의미 검색이 안 될 때 키워드 결과로 몰래 대체하기.
- Discord로 명령 받기, 원격·다중 사용자 접속, 모바일 전용 KnowledgeOS.

## 명세 운용

명세는 단계별 산출물이 아니라 코드와 함께 움직이는 현재 계약입니다.

- 이력·증거·설치 상태를 담지 않습니다. 이력은 Git에, 진행과 검증은 [PROJECT_STATE.md](PROJECT_STATE.md)에 둡니다.
- 동작을 바꾸는 변경은 같은 변경 안에서 명세도 고칩니다. 낡은 문장은 표시하지 않고 지웁니다.
- 기능 명세의 인수 시나리오가 곧 실행 가능한 테스트입니다.
- 기능 하나를 끝까지 얇게 만들어 써 보고, 배운 것으로 명세를 고칩니다.
- 정하지 않은 것은 Blueprint의 열린 질문에 두고, 필요한 시점에 결정합니다.
- 기존 구현 중 이 방향과 맞는 부분은 [참고 자료 목록](../../Tmp/active/knowledgeos-spec-references-2026-11-19/REFERENCES.md)에 있습니다. 명세가 우선합니다.
