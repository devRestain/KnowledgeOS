# 용어 사전

KnowledgeOS 위키와 화면에서는 코드·명세와 같은 단어를 쓰기 위해 일부 영어 용어를 그대로 유지합니다. 이 사전은 각 개념이 실제 구현에서 맡은 역할과 한 가지 구체적 예시를 묶어 정리합니다. 원문 용어 정의는 [CAPABILITIES Shared terms](../CAPABILITIES.md)에서 볼 수 있습니다.

## 핵심 실행 개념과 예시

| 용어 | 실제 구현에서 하는 일 | 예시 | 더 보기 |
|---|---|---|---|
| **Request (요청)** | Thin Client·콘솔·CLI·예약 작업·Gateway가 `POST /api/requests`로 보내는 실행 단위. 안정적인 `request_id`로 중복 실행을 막습니다 | 사이드바 **정리 요청**을 누르면 노트 경로·해시·UUID가 요청으로 전송됨 | [[c11]] |
| **Receipt (영수증)** | Owner가 요청을 받자마자 실행 전에 State `requests/<id>.json`에 먼저 남기는 접수·종결 기록. 응답을 잃으면 클라이언트가 이 영수증을 조회합니다 | 응답이 끊긴 뒤에도 같은 `request_id` 영수증을 폴링해 생성된 제안 경로를 받음 | [[c11]] |
| **Admission (접수)** | Owner의 `Director`가 요청을 검사해 즉시 처리하거나 `WorkSpec`으로 받아들이는 관문. 정형 요청은 정적 코드가, 자유 요청만 `kos-director`가 초안을 만듭니다 | **적용**(`review.apply`)은 접수 안에서 바로 끝나고, **정리 요청**은 Work로 이어짐 | [[architecture]] |
| **Work (`work_id`)** | 접수된 실행 요청 하나의 전체 기록(`State/works/<id>.json`). 입력 스냅숏, `Graph 세그먼트`, 역할 호출, 평가, 효과, `TeamReview`를 담습니다. 검색·제안 적용처럼 Work를 만들지 않는 요청도 있습니다 | 노트 하나를 정리하면 `work-...`가 열려 분류·링크·평가·제안 생성까지 한 기록에 남음 | [[architecture]] |
| **WorkSpec** | Work 시작 시 Owner가 고정하는 작업 명세. 대상(Officer 또는 Team), 카탈로그 완료 기준, 예산, 입력 스냅숏, 가장 엄격한 입력 `ai_policy`를 담습니다. 모델은 기준·예산을 쓸 수 없습니다 | `강화: 묻기`를 누르면 `ThinkingTeam`, 5개 기준, 2구간·생산자 6회 예산이 `WorkSpec`에 고정됨 | [[c4]] |
| **Graph segment (구간)** | Work 안에서 한 번 실행하는 직렬 생산자 목록과 끝의 평가 노드 묶음. Team은 Manager가 남은 일과 예산에 맞춰 고르고, Officer는 고정된 처리기 하나를 씁니다. 시작한 구간은 바꾸지 않습니다 | 정리의 첫 구간 뒤 분류 근거만 부족하면 다음 구간에 `classifier`만 다시 배치함 | [[architecture]] |
| **LangGraph** | 승인된 `Graph 세그먼트`의 노드 순서를 Temporal 안에서 실제로 실행하는 엔진(`StateGraph`). Worker 시작 시 등록된 토폴로지 중 선택된 순서와 일치하는 그래프를 꺼내 생산자 노드와 평가 노드를 차례로 돌립니다 | `["retriever", "connector", "synthesizer"]`가 선택되면 `START → retriever → connector → synthesizer → evaluation → END`로 실행됨 | [[architecture]] |
| **Temporal Activity (`knowledgeos.phase`)** | Workflow 안에서 외부 호출·파일 처리·모델 실행을 담당하는 단위. 한 모델 팀원의 도구 호출과 교정 턴은 하나의 생산자 Activity 안에서 끝납니다. 최대 시도는 1회, 즉 자동 재시도는 없습니다 | 분류 단계가 시간 제한을 넘겨 결과를 알 수 없으면 다시 호출하지 않고 `unknown`으로 남김 | [[architecture]] |
| **Hermes Profile (`kos-*`)** | 모델 역할 9개와 발송 전용 `kos-ops`마다 따로 설치하는 격리 실행 환경(`SOUL.md`·`config.yaml`·자체 기억). `config/roles.json` 기준으로 `classifier`·`linker`·`connector`는 `gpt-6.1-terra`, 나머지 6개 모델 역할은 `gpt-6.1-sol`(모두 `high`)을 씁니다 | `kos-linker`는 `gpt-6.1-terra`와 읽기·검색·제출용 작업 도구만 받고 셸·파일 도구 없이 1단계만 실행함 | [[architecture]] |
| **Handoff (인계물)** | Team 작업의 팀원이 `submit_handoff` 등으로 제출해 State blob에 남기는 구조화된 중간 결과물. 같은 Work의 뒤쪽 팀원이 `work_context`로 앞선 인계물을 읽어 이어 갑니다 | `scout`이 모은 대상 핸들과 태그 어휘를 `kos-classifier`가 읽고, `classifier`의 분류 인계물을 `kos-linker`가 읽음 | [[c1]], [[c4]] |
| **Handle (`e1`, `e2`...)** | 모델에게 파일 경로·ID·해시 대신 주는 작업별 짧은 근거 참조. 노트 한 조각과 정확한 내용 판본을 가리킵니다. 아직 읽지 않은 검색 후보는 `read`로 열어야 인용할 수 있습니다 | 검색의 `e3`을 실제로 읽은 뒤 보고서에서 인용하면 Owner가 줄 위치와 해시로 펼쳐 줌 | [[c4]] |
| **Effect (`effects`)** | Owner가 Vault 파일을 생성·수정·이동·삭제하는 쓰기와 그 의도·결과 기록(`State/effects.jsonl`). 쓰기 전에 의도와 직전 파일 해시(`preimage`)를 남기고, 파일이 달라졌으면 쓰지 않습니다 | `review.apply`가 체크된 속성을 대상 노트에 쓰고 제안 파일을 삭제할 때 `replace`·`delete` 효과가 순서대로 남음 | [[c6]] |
| **Unknown (`unknowns/<id>.json`)** | Activity 타임아웃, 중단, 발송 불명처럼 실제 반영 여부를 확인할 수 없을 때 남기는 상태. 중복 적용·발송을 막기 위해 자동 재실행하지 않고 사람이 확인 뒤 `reconcile`로 관찰만 기록합니다 | 모델 단계나 `ActionWorkflow`가 시간 초과로 끊기면 콘솔 **결과 불명** 탭에 남아 사람의 확인을 기다림 | [[c11]], [[c6]] |
| **System One** | Owner가 OpenAI Decisions API(`gpt-6-luna`)에 직접 보내는 선택형(`choice`)·확률(`predicate`) 판단 서비스. Hermes Profile이 아니며 글을 짓거나 파일을 쓰지 않고 `ai_policy`·`sensitivity`도 정하지 않습니다. 현재 `approve` 노트만 보내며 실패해도 기능은 그대로 이어집니다 | 자유 요청 `"오늘 서울 날씨 알려줘"`는 `none_of_these`(0.95)로 거절해 `kos-director`를 띄우지 않고, 규격화에서는 `status: draft`를 `seed`로 고름 | [[c2]], [[c11]] |

## 실행·평가·조직 용어

| 용어 | 뜻 | 더 보기 |
|---|---|---|
| **Owner** | 권한·정책 확인·입력 스냅숏·Vault 쓰기·영수증·알림을 독점하는 호스트 프로세스. 내용은 직접 판단하지 않습니다 | [[architecture]] |
| **Director** | 요청을 받아들이는 접수 담당. 정형 요청은 정적 코드가, 자유 요청만 `kos-director` Profile이 표준 명령 1개 초안으로 번역해 접수합니다 | [[c11]] |
| **Manager** | 팀의 `Graph 세그먼트`를 짜고 미충족 시 후속 구간과 완료 후 `TeamReview`를 남기는 모델 역할(`kos-curation-manager`, `kos-thinking-manager`) | [[c1]], [[c4]] |
| **Teammate (팀원)** | 한 종류의 중간 산출물만 만드는 팀 구성원. 정적 코드(`scout`, `retriever`)와 Hermes Profile(`classifier`, `linker`, `connector`, `challenger`, `synthesizer`)로 나뉩니다 | [[c1]], [[c4]] |
| **Officer** | 팀 없이 혼자 결과 하나를 만드는 정적 코드 역할(`StandardizationOfficer`, `StewardshipOfficer`, `EmbeddingOfficer`, `DailyOfficer`) | [[c2]], [[c5]], [[c3]], [[c12]] |
| **EvalOfficer (`kos-eval`)** | 모든 구간 끝에서 정적 검사 결과와 인용 발췌만 보고 기준 충족 여부를 독립 평가합니다. 정적 기준은 Owner 코드가, 의미 기준만 `kos-eval`이 판단합니다 | [[architecture]] |
| **`WorkWorkflow` / `ActionWorkflow`** | Temporal의 두 Workflow. `knowledgeos.work.v1`은 일반 Work 전체 수명을, `knowledgeos.action.v1`은 대기 중인 규격화 미리보기(`awaiting_confirmation`)의 확정·취소를 맡습니다 | [[architecture]], [[c11]] |
| **Step (단계)** | 구간 안의 생산자 호출 1회(`static` 또는 `model`). Team 작업은 기본 2구간·생산자 6회, Officer는 1구간·1회 예산을 씁니다 | [[architecture]] |
| **RemainingWork** | 평가에서 `unmet`로 남은 기준과 대상·이유 목록. 같은 Manager가 이 부족분만 메우는 후속 구간을 짭니다 | [[ENH-6]] |
| **CompletionAssessment** | 구간 끝에서 Owner가 정적 검사와 `EvalOfficer` 판정을 합쳐 만드는 최종 만족·미충족 판정(정적 검사가 실패한 대상은 모델 판정이 `satisfied`여도 낮춤) | [[architecture]] |
| **Corrective turn (교정 턴)** | 모델 제출이 형식·인용 검증에 걸렸을 때 진단과 함께 같은 세션에서 1번 더 주는 수정 기회 | [[ENH-2]] |
| **Authority violation (권한 위반)** | 모델이 기준·예산·경로·EvalOfficer 위치 같은 권한 밖 필드를 정하려 드는 것. 교정 턴 없이 즉시 거절됩니다 | [[ORG-5]] |
| **TeamReview** | Team 작업 완료 뒤 Manager가 남기는 팀원 평점(1~5)과 개선 제안(최대 3개). 조언일 뿐 역할·카탈로그를 자동으로 바꾸지 않습니다 | [[ENH-7]] |
| **Scheduler / Notifier** | Owner 안에서 선언된 예약 작업을 실행하는 스케줄러와, 완성된 알림을 모델 없는 `kos-ops` Profile로 Discord에 보내는 발송기 | [[c7]], [[c8]] |
| **ExOps / InterOps / HermesTrace** | ExOps는 Thin Client·콘솔·CLI가 공유하는 loopback HTTP 서버(`127.0.0.1:8765`), InterOps는 다른 Operation과 정보를 주고받는 통로, HermesTrace는 근거 있는 Experience 후보를 받는 외부 Operation입니다 | [[c11]], [[c10]], [[c9]] |
| **Ollama** | 의미 검색용 임베딩 벡터(기본 `qwen3-embedding:8b-q4_K_M`)를 생성하는 로컬 서버. Owner가 loopback HTTP로 직접 호출합니다 | [[c3]] |
| **LaunchAgent / `/api/health`** | Owner를 온디맨드 macOS 사용자 서비스(`RunAtLoad`·`KeepAlive` 꺼짐)로 등록하는 방식과, Thin Client·콘솔이 Owner 준비 상태를 판정하는 조회 경로 | [[c11]] |

## 개인정보 · 저장소 · 노트 안전

| 용어 | 뜻 | 더 보기 |
|---|---|---|
| **Vault / State / Runtime** | **Vault**는 사용자의 Markdown 정본, **State**(`States/Operations/knowledgeos/`)는 잃어도 지식이 남는 운영 기록·영수증·벡터 색인, **Runtime**은 재설치 가능한 Python 환경·로그·배포물입니다 | [[architecture]] |
| **`ai_policy` (`approve` / `deny`)** | 노트별 자동화·모델 허용 정책. 빠졌거나 비면 `approve`, 그 외 퇴역값(`ask`·`remote_ok` 등)이나 알 수 없는 값은 모두 `deny`로 닫힙니다 | [[architecture]] |
| **`sensitivity`** | `public`·`personal`(기본)·`confidential` 분류 속성. `ai_policy`의 허용 여부를 바꾸지 않습니다 | [[architecture]] |
| **Preimage** | 쓰기 직전 기대하는 대상 파일의 SHA-256 해시. 현재 파일과 다르면 덮어쓰지 않고 거절합니다 | [[STD-3]] |
| **Snapshot (스냅숏)** | Work 접수 때 State에 잡아 두는 입력 노트의 정확한 바이트. 작업 중 사용자가 노트를 수정해도 Work는 접수 당시 스냅숏을 읽습니다 | [[ENH-3]] |
| **Stale (낡음)** | 제안 생성 뒤 대상 노트가 바뀌어 더는 적용할 수 없는 제안 상태. 7일 뒤 자동으로 정리됩니다 | [[REV-2]] |
| **Reconcile (확인 기록)** | 사람이 `unknown` 항목의 실제 결과를 직접 확인해 `applied` 또는 `not_applied`로 남기는 일. 과거 기록은 고치지 않습니다 | [[SUR-4]] |
| **HumanActionResponse** | 적용·거절 등 사람의 결정을 남기는 Core 형식. 이를 감싸는 KnowledgeOS 액션 기록은 별도로 접수됨·적용됨·적용 안 됨·결과 불명을 구별합니다 | [[c6]], [[c11]] |
| **Autofix / Quiet period** | **Autofix**는 C2 규칙 1~8과 확신도 기준 이상 규칙 9로 노트에 직접 쓰는 작은 규격화 변경이고, **Quiet period**는 최근 편집한 노트를 자동 규격화가 건너뛰는 시간(기본 10분)입니다 | [[c2]] |
| **Conflict marker** | Git 병합 충돌 표시. 있는 노트는 모든 자동화가 건너뛰고 주의 항목으로 뜹니다 | [[DOC-3]] |
| **Attention item (주의 항목)** | 사용자가 확인해야 할 항목. 노트로 쓰지 않고 Bases 보기나 콘솔·요약에서 계산합니다 | [[c5]] |

## 노트 · 검색 · 예약 · 화면

| 용어 | 뜻 | 더 보기 |
|---|---|---|
| **frontmatter / `id` / `type` / `status`** | 노트 맨 위의 YAML 블록과 UUIDv4 식별자(`id`), KnowledgeOS가 기준 삼는 종류(`capture`·`idea`·`knowledge`·`question`·`project`·`source`·`daily`), 종류별 상태값 | [[c2]], [[c12]] |
| **Proposal / Report / Capture** | **Proposal**은 `01_AI_Review/Proposals/`의 체크리스트 제안 노트(적용 후 삭제), **Report**는 `01_AI_Review/Reports/`의 1회 생성 보고서(이후 사용자 문서), **Capture**는 `00_Inbox/`의 원시 포착 노트입니다 | [[c1]], [[c4]], [[c6]] |
| **Daily note / `[>]`** | `10_Journal/Daily/YYYY-MM-DD.md`의 `type: daily` 노트(AI 제안 제외)와, 어제의 열린 항목을 오늘로 옮긴 뒤 원본에 남기는 인계 표시 | [[c12]] |
| **Bases / QuickAdd / Templater / Note Toolbar** | Obsidian 안에서 목록 보기(Bases), 템플릿 생성(QuickAdd·Templater), 제안 적용·거절 버튼(Note Toolbar)을 맡는 코어·커뮤니티 플러그인 | [[usage]] |
| **Keyword / Semantic / Chunk / Vetted** | **Keyword**는 메모리 부분 일치, **Semantic**은 약 1,500자 조각(`Chunk`) 임베딩의 코사인 유사도 검색(미준비 시 `unavailable`), **Vetted**는 관련 노트 후보가 System One 확률 기준(`related_min_probability`)을 통과한 상태입니다 | [[c3]] |
| **Job / Slot / Catch-up / Trigger receipt** | `config/jobs.json`의 다섯 예약 작업과 예정 시각 슬롯(`schedule-<job>-<YYYYMMDDTHHMM>`), Owner가 꺼져 있던 동안 놓친 작업을 시작 지연(기본 3분) 뒤 1회만 실행하는 따라잡기, 실행 요청을 접수했다는 트리거 영수증입니다 | [[c7]] |
| **Digest / Alert / Daily reminder** | 매일 08:00 볼 것이 있을 때만 보내는 검토 요약(`Digest`), 충돌·미해결 효과·액션 불명만 보내는 즉시 알림(`Alert`), 매일 07:00 `#task` 할 일 이름만 보내는 아침 알림(`Daily reminder`)입니다 | [[c8]], [[c12]] |
| **`feedback` / `SourceEvent` / `Candidate` / `routine_progress`** | 역할이 자기 과거 출력 결과를 보는 `feedback` 뷰, 근거 있는 사건(`SourceEvent`)과 본문 없이 HermesTrace로 보낼 후보(`Candidate`), 그리고 외부 전송 없이 로컬에만 남는 일반 `TeamReview` 관찰 분류(`routine_progress`)입니다 | [[c9]] |
| **Thin Client / 브라우저 콘솔 / CLI** | Obsidian 플러그인(명령·인라인 사이드바), 로컬 loopback 관리 페이지(`127.0.0.1:8765`), 운영자 유지보수 CLI(`knowledgeos`)입니다 | [[c11]], [[usage]] |
| **`kos_request` / `00_Inbox/Requests/`** | 추후 모바일 요청(M2)을 위해 비워 둔 예약 속성과 폴더입니다 | [[roadmap]] |
| **evidence class / blocker / handoff** | `PROJECT_STATE.md`에서 검증 근거 성격(`static`·`runtime`·`external_service`·`deployment`·`device`·`artifact`·`semantic`), 해결할 막힘, 다음 세션 인계 항목을 가리키는 상태 분류입니다 | [[status]], [[roadmap]] |
