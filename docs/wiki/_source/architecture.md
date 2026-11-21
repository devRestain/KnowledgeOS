# 실행 구조: 요청에서 파일이 만들어지기까지

KnowledgeOS는 버튼마다 별도 에이전트 앱을 띄우는 구조가 아닙니다. **하나의 Owner 프로세스가 요청을 받고, 오래 걸리는 작업은 Temporal에 맡기며, 모델이 필요한 단계에서만 해당 Hermes Profile을 호출합니다.** 이 페이지는 기능 설명에 공통으로 등장하는 실행 장치를 연결합니다.

## 요청이 들어왔다고 모두 Graph가 도는 것은 아닙니다

사이드바의 “관련 노트 찾기”와 “정리 요청”은 서로 다른 길을 갑니다. 앞의 요청은 검색 서비스가 후보를 골라 바로 돌려줍니다. 뒤의 요청은 입력 노트를 고정하고 Work를 만든 뒤, 팀의 Graph 실행과 평가를 거쳐 제안 파일을 만듭니다.

Owner의 접수 코드는 기능 카탈로그에서 요청 이름과 허용 매개변수를 찾고 해당 처리기로 넘깁니다. 처리기가 즉시 결과를 돌려줄 수도 있고, **WorkDraft라는 실행 초안**을 돌려줄 수도 있습니다. 후자의 경우에만 Owner가 Work 기록을 만들고 실행 엔진에 맡깁니다. 따라서 Director라는 접수 책임을 지난다는 말이 `kos-director` 모델을 매번 부른다는 뜻은 아닙니다.

자유 요청은 한 단계가 더 있습니다. System One이 요청 문장만 보고 명백히 기능 밖인 요청을 거를 수 있고, 나머지는 `kos-director`가 기존 명령 하나로 번역합니다. 그 명령은 다시 같은 정형 접수 코드를 지납니다. Director 모델이 새 기능·평가 기준·예산을 만드는 경로는 없습니다([[c11]]).

## 한 Work가 실제로 실행되는 순서

정리 요청을 예로 들면 다음과 같습니다. 각 기능에서 달라지는 부분은 [[c1]], [[c2]], [[c4]] 등의 설명으로 이어집니다.

1. **접수와 입력 고정.** Owner가 요청 ID와 영수증을 남기고, 대상과 기준을 담은 WorkSpec 및 입력 스냅숏을 State에 기록합니다. 사용자가 실행 도중 노트를 고쳐도 모델이 읽은 판본과 현재 파일을 구별할 수 있게 됩니다.
2. **Temporal 시작.** `knowledgeos.work.v1` Workflow를 Work에 연결합니다. Workflow ID는 Operation ID와 Work ID를 합친 것이므로 같은 Work를 다시 붙잡을 때 새 작업을 임의로 만들지 않습니다.
3. **실행 순서 선택.** Team이면 Manager가 팀원 목록과 순서를 제출합니다. Officer이면 이미 정해진 정적 처리기 하나를 선택합니다.
4. **Graph 구간 시작.** Owner가 검증한 순서에 맞는 LangGraph를 고르고 구간 기록을 엽니다. 팀원들이 순서대로 중간 결과를 남깁니다.
5. **평가.** 정적 검사, 필요한 경우 모델 의미 평가, 최종 판정 저장을 따로 수행합니다. 어떤 기준이 부족한지도 이때 기록합니다.
6. **반영 또는 후속 구간.** 모두 만족하면 기능별 마무리 처리기가 제안·보고서 등을 씁니다. 부족하면 Team만 예산 안에서 후속 Graph를 만들 수 있습니다. 평가 미충족을 성공 파일로 포장하지 않습니다.
7. **종료.** Team은 성공적인 반영 뒤 Manager의 TeamReview를 남깁니다. 마지막으로 Work와 요청 영수증에 결과를 연결해 클라이언트가 읽게 합니다.

규격화 미리보기처럼 이 실행 결과가 `awaiting_confirmation`인 경우도 있습니다. 계산과 평가가 끝났어도 사용자가 확정하기 전에는 원본 반영이 끝난 것이 아닙니다.

## Manager가 만드는 Graph는 얼마나 자유로운가

현재 구현의 Graph는 **허용된 팀원을 한 번씩 배치하는 직렬 목록**입니다. Manager가 임의의 Python 코드, 병렬 분기, 순환, 새로운 역할을 작성하는 방식이 아닙니다. 제출물에는 팀원 순서와 “마지막에 평가”라는 표시만 들어갑니다.

정리는 `scout → classifier → linker` 같은 순서를, 강화는 `retriever → connector → challenger → synthesizer` 같은 순서를 사용할 수 있습니다. 이것들은 가능한 사례이지 모든 요청의 고정 파이프라인은 아닙니다. Manager는 팀원 풀과 기능별 제약 안에서 필요한 구성을 고릅니다.

Owner는 다음을 확인한 뒤에만 구간을 엽니다.

- 목록이 비어 있지 않고, 그 팀의 허용된 역할만 들어 있는가.
- 같은 팀원을 한 구간에서 두 번 부르지 않는가.
- 기능이 마지막 생산자를 지정했다면 그 역할로 끝나는가.
- 남은 생산자 호출 예산 안에 있는가.
- 후속 구간이면 각 팀원이 실제로 남은 기준을 다루는가.

평가 노드는 Owner가 끝에 붙입니다. Manager가 평가자의 입력·지시·모델·위치를 바꾸려 하면 거절됩니다. 이미 시작한 Graph를 고치는 대신, 미충족 기준을 담은 RemainingWork에 따라 **다음 구간**을 새로 만듭니다. 현재 Team 기본 한도는 총 2구간·생산자 6회이며 정적 팀원도 이 호출 수를 씁니다. 같은 부족이 후속 구간에도 남으면 종료합니다.

Officer도 LangGraph를 사용합니다. 다만 Manager는 없고 **Officer 처리기 하나 → 평가**로 고정됩니다. “코드로 실행한다”와 “Temporal·Graph를 쓰지 않는다”는 같은 말이 아닙니다.

## Temporal과 LangGraph가 만나는 정확한 지점

Owner는 Temporal 서버의 작업을 받는 Worker도 함께 실행합니다. 기본 접속은 `127.0.0.1:7233`, 네임스페이스는 `knowledgeos`이고, 작업 큐는 State 경로에서 구한 식별값으로 구분합니다. 서버가 모델을 실행하는 것이 아니라, **Owner 쪽 Worker가 모델과 처리기를 실행하고 서버가 이력을 보존**합니다.

Work Workflow는 `begin`, `compose`, `open` 단계를 거쳐 승인된 LangGraph를 호출합니다. Graph의 각 생산자 노드는 `produce` Activity를 실행합니다. 평가 노드 안에는 `evaluate_static → evaluate_model → evaluate_commit`이라는 세 Activity가 있습니다. Graph가 끝나면 Workflow가 `commit`, 필요한 `review`, `settle`을 이어 갑니다.

Activity는 외부 작업을 할 수 있는 실행 단위입니다. 한 모델 팀원의 도구 호출과 형식 오류 교정 턴까지 하나의 생산자 Activity 안에서 진행됩니다. **도구 호출 하나마다 별도 Workflow를 만드는 구조는 아닙니다.** 의미 평가가 필요 없을 때 `evaluate_model` 단계는 지나가되 Hermes는 호출하지 않습니다.

실행 가능한 역할 순서들은 Worker 시작 때 LangGraph로 등록됩니다. Manager가 제출한 목록을 검증한 뒤 정확히 일치하는 등록 Graph를 사용합니다. 구간에는 선택된 노드·간선·Graph 식별값도 남습니다.

미리보기 확정처럼 대기 중 Work에 붙는 사용자 결정은 별도 `knowledgeos.action.v1` Workflow가 처리합니다. 모든 클릭이 이 Workflow인 것은 아니며, 각 기능의 접수 처리기가 어느 경로인지 결정합니다([[c6]], [[c11]]).

### 재시작과 시간 초과는 어떻게 보이는가

각 Activity는 시작 의도를 먼저 State에 기록하고 끝나면 결과를 저장합니다. 완료 기록이 있으면 그것을 재사용합니다. 시작 기록만 남아 실제 실행 결과를 알 수 없다면 자동으로 다시 호출하지 않고 `unknown`으로 남깁니다. 최대 시도는 1회로, Activity를 자동 재시도하지 않습니다.

따라서 Temporal이 있다고 모델 호출·알림·파일 쓰기를 무조건 재실행하는 것은 아닙니다. Workflow 이력을 이어 가는 것과 결과를 모르는 외부 작업을 다시 보내는 것은 구별됩니다.

Activity 기본 시간은 120초이며 실제 호스트 설정은 600초로 기록되어 있습니다. Worker는 자신의 단조 시계로 남은 시간을 확인합니다. Mac이 잠든 뒤 Temporal 서버의 시계가 늦어져도 서버 시각과 호스트 시각을 비교해 정상 작업을 차단하지 않습니다. 실행 코드나 역할 선언의 고정값이 달라지면 진행 중 Work를 새 구현으로 조용히 바꿔 이어 가지 않습니다.

## 실제 Hermes Profile 목록

모델 역할은 다음 아홉 Profile입니다. 모델 공급자는 모두 `openai-codex`이며 추론 노력은 `high`입니다. 이 표는 현재 소스 설정을 설명하며, 각 설정을 실제로 실행해 본 범위는 [[status]]에서 구별합니다.

| Profile | 언제 호출되는가 | 모델 | 받는 정보와 제출물 |
|---|---|---|---|
| `kos-director` | 자유 요청을 정형 명령으로 바꿀 때 | `gpt-6.1-sol` | 기능 목록·허용 대상 → 명령 초안 또는 거절 |
| `kos-curation-manager` | 정리의 구성·후속 구성·TeamReview | `gpt-6.1-sol` | 팀과 기준·진행 요약 → 팀원 순서 또는 팀 회고 |
| `kos-thinking-manager` | 강화의 구성·후속 구성·TeamReview | `gpt-6.1-sol` | 팀과 기준·진행 요약 → 팀원 순서 또는 팀 회고 |
| `kos-classifier` | 정리의 분류 단계 | `gpt-6.1-terra` | 읽은 노트와 앞선 결과 → 분류 handoff |
| `kos-linker` | 정리의 연결 단계 | `gpt-6.1-terra` | 읽은 후보·분류 결과 → 링크 handoff |
| `kos-connector` | 강화의 연결 단계 | `gpt-6.1-terra` | 읽은 근거 → 연결·긴장 관계 handoff |
| `kos-challenger` | 강화의 반론 단계 | `gpt-6.1-sol` | 읽은 근거와 앞선 결과 → 빈틈·반론 handoff |
| `kos-synthesizer` | 강화의 보고서 단계 | `gpt-6.1-sol` | 근거와 중간 결과 → 인용이 있는 보고서 제출 |
| `kos-eval` | 의미 기준의 독립 평가 | `gpt-6.1-sol` | 기준·정적 검사·산출물과 인용 근거 → 기준별 판정 |

`scout`, `retriever`, 각 Officer는 Python 처리기입니다. 이 이름의 모델 Profile은 없습니다. 별도로 **`kos-ops`**가 있지만 모델이 아니라 Owner가 완성한 메시지를 Discord로 보내는 발송용 Profile입니다.

### Profile은 상시 팀원이 아니라 단계별 실행 환경입니다

Owner는 모델 단계가 필요할 때 `hermes -p <profile> acp` 하위 프로세스를 열고 세션을 만듭니다. 프로세스에는 그 단계 전용 `kos` MCP 도구 연결을 제공합니다. 도구 요청은 세션에 묶인 토큰으로 Owner의 해당 Work·역할에 연결됩니다. 모델이 Work ID를 써서 다른 작업을 선택하는 구조가 아닙니다.

모델은 허용된 도구로 문맥을 읽고 **제출 도구**를 호출해야 합니다. 채팅의 마지막 문장만으로는 결과가 접수되지 않습니다. 형식·인용 검증에 실패하면 같은 세션에서 진단을 받아 한 번 교정하고, 그 뒤 세션을 닫습니다. 다음 역할은 자신의 Profile과 새 세션으로 실행됩니다.

Profile 설치물은 공통 지침과 역할별 SOUL을 합친 `SOUL.md`, 모델·도구·학습 설정이 든 `config.yaml`입니다. 기본 파일·셸·브라우저 도구는 막고 역할별 Owner 도구와 허용된 학습 도구를 사용합니다. 모델·Profile을 사용할 수 없으면 실패가 드러나며 대체 모델로 몰래 바꾸지 않습니다.

## Hermes를 거치지 않는 두 모델 서비스

**System One**은 Owner가 OpenAI Decisions API에 직접 보내는 선택형 판단입니다. 기본 모델은 `gpt-6-luna`이고 Hermes Profile은 없습니다. 규격화의 제한된 속성 매핑, 관련 노트 거르기, 문서 관리 상태 제안, 자유 요청 사전 분류에 쓰입니다. 다섯 모델 팀원도 `system_one` 도구로 읽은 노트에 대해 질문할 수 있지만 Director·Manager·EvalOfficer에는 이 도구가 없습니다.

**임베딩**은 Owner가 로컬 Ollama에 직접 요청합니다. 기본 모델은 `qwen3-embedding:8b-q4_K_M`이며, 결과는 State의 의미 색인에 저장됩니다. Hermes 역할의 대화 모델과 별개입니다. 무엇을 임베딩하고 언제 다시 만드는지는 [[c3]]에서 설명합니다.

## 파일과 기록은 어디에 남는가

사용자가 읽을 제안·보고서·데일리는 Vault에 남습니다. 요청 영수증, WorkSpec, 입력 스냅숏, 단계별 handoff, 평가·효과·알림 기록과 벡터 색인은 State에 남습니다. Python 설치물·생성 Profile 배포본·로그는 Runtime에 있습니다. Hermes Profile의 세션·스킬·자격 증명 참조는 별도의 호스트 Profile 홈에 있습니다.

모델은 파일을 직접 수정하지 않습니다. 제출한 구조화 결과를 Owner가 검사하고 기능별 형식으로 렌더링합니다. 정리의 최종 반영은 **제안 파일 생성**이고, 그 제안을 사용자가 적용해 원본을 바꾸는 일은 별도 요청입니다. 강화는 보고서 파일을 만듭니다. 규격화와 데일리는 각자의 정해진 범위에서 기존 파일도 바꿉니다.

쓰기가 필요한 순간에는 현재 파일의 해시가 기대한 판본과 같은지 확인합니다. 달라졌다면 덮어쓰거나 자동 병합하지 않습니다. 노트의 `ai_policy`도 현재 값을 다시 확인합니다. `approve`만 대상이며 빠졌거나 비면 기본 `approve`, 그 외 알 수 없는 값은 `deny`입니다. `confidential` 표시는 이 이진 허용 여부를 바꾸지 않습니다.

## 구현 근거

- [요청 접수와 자유 요청 번역](../../src/knowledgeos/owner.py), [공통 실행 단계·Graph 검증·평가](../../src/knowledgeos/engine.py)
- [Work·Action Workflow와 실제 직렬 Graph](../../src/knowledgeos/temporal_workflow.py), [Worker·Graph 등록·실행 바인딩](../../src/knowledgeos/temporal_runtime.py)
- [역할별 Profile·모델·도구](../../config/roles.json), [세션별 Hermes 호출](../../src/knowledgeos/roles.py), [Profile 생성·발송 어댑터](../../src/knowledgeos/hermes.py)
- [시스템 계약](../../OBSIDIAN_VAULT_BLUEPRINT.md), [[status]], [[glossary]]
