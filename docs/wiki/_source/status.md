# 현재 확인된 범위

기능 페이지는 현재 소스가 어떻게 동작하는지를 설명합니다. 이 페이지는 **그 동작을 실제로 어디까지 실행해 보았는지**를 따로 보여 줍니다. 근거는 [PROJECT_STATE.md](../../PROJECT_STATE.md)에 기록된 관찰입니다. 이번 위키 집필에서는 KnowledgeOS 작업이나 Vault 변경을 새로 실행하지 않았습니다.

## 실제 환경에서 확인한 핵심 경로

**정리와 강화**는 실제 Obsidian 요청에서 Hermes 모델 역할을 거쳐 제안과 보고서를 만든 기록이 있습니다. 사용자가 제안을 적용한 기록도 있습니다([[E_REAL_ROLE_SMOKE]], [[E_USER_PROPOSAL_APPLY]]). 이 초기 스모크가 이후의 모든 역할 설정 변경까지 증명하지는 않습니다.

현재 `kos-classifier`의 Terra high 경로는 별도 설치와 실제 단독 단계 호출까지 확인되어 있습니다. 합성 아이디어 하나를 `idea`·`seed`로 제출했고 실제 Vault 24개 Markdown 파일은 보존했습니다([[E_CLASSIFIER_TERRA_INSTALL]], [[E_CLASSIFIER_TERRA_LIVE]]). 반면 공유 System One 도구를 새로 받은 `linker`·`connector`·`challenger`·`synthesizer`의 새 실제 모델 호출은 기록상 미실행입니다([[E_SHARED_SYSTEM_ONE_MODELS]]).

**규격화**는 실제 Owner에서 System One이 `status: draft`를 `seed`로 매핑한 미리보기와 취소, 그리고 자동 경로가 값을 쓰고 되돌릴 수 있는 변경 기록을 남긴 관찰이 있습니다([[E_SYSTEM_ONE_LIVE]], [[E_BINARY_POLICY_AUTO_STANDARDIZE]]). 따라서 “자동 쓰기는 아직 한 번도 확인하지 않았다”는 설명은 현재 기록과 맞지 않습니다.

**검색**은 로컬 Ollama 임베딩과 실제 Obsidian의 의미 검색·관련 노트 결과까지 확인했습니다([[E_REAL_SEMANTIC]], [[E_SYSTEM_ONE_LIVE]]). 적절한 유사도·확률 문턱은 실제 노트를 더 보며 조정할 대상이지, 스모크 통과만으로 품질이 확정된 값은 아닙니다.

**예약·데일리·알림**은 Owner 내부 스케줄러의 실제 슬롯 실행, 서버 시계 지연 수정 뒤 수동 규격화 완료, 실제 데일리 생성·인계·Discord 전달 기록이 있습니다([[E_REAL_SCHEDULER_SLOT]], [[E_JOB_WINDOW_LIVE]], [[E_DAILY_V2_LIVE]]). 마지막 데일리 관찰은 오늘 노트에 네 구역과 이월 항목 4개를 만들고 과거 항목을 표시한 실행입니다. 다섯 기본 작업이 모두 자연 예약 시각에 지속적으로 실행된다는 장기 관찰과는 다릅니다.

**사이드바**는 실제 Obsidian에서 인라인 기능·검색 결과·좁은 폭·테마를 확인했고, 1.3.0의 System One 결정 줄도 확인했습니다. 후자의 시각 근거는 화면 잠금 상태에서 얻은 사이드바 CDP 렌더라는 한계가 있습니다([[E_SIDEBAR_INLINE_GUI]], [[E_SYSTEM_ONE_CLIENT_GUI]]). Home·Tasks의 최신 데일리 표시 질의는 실제 Obsidian 화면에서 아직 확인하지 않았습니다([[E_HOME_TASKS_DEVICE]]).

**Gateway와 Experience**는 기능 테스트의 범위와 실제 다른 Operation의 수신·응답을 구별해야 합니다. 현재 기록에는 실제 Gateway 왕복이나 HermesTrace 수신기까지의 연동 완료 근거가 없습니다. 다음 확인 대상은 [[roadmap]]에 모았습니다.

## 구현을 대조하며 발견한 연결 한계

공유 `system_one` 도구는 내부 처리기에서 선택지 목록을 받도록 만들어져 있지만, **현재 Hermes와 Owner를 잇는 HTTP 입력 검사가 그 목록을 받지 못합니다.** 도구 선언은 `choices`를 문자열 배열로 내보내는 반면 ExOps 검사는 문자열·객체·정수만 처리하므로, 배열을 실어 보내면 도구 본체에 도달하기 전에 `invalid argument choices`로 거절되는 구조입니다.

이는 [도구 선언](../../src/knowledgeos/roles.py)과 [HTTP 검사](../../src/knowledgeos/exops.py)를 대조한 정적 확인입니다. 이번에 실제 모델 호출로 재현한 결과는 아닙니다. 선택지 없는 예/아니오 질문이나 분류기의 대상 힌트 호출과는 다른 경로입니다. 기존 인프로세스 집중 테스트가 이 HTTP 연결까지 확인한 것으로 읽으면 안 됩니다.

이 집필 작업은 런타임을 수정하지 않습니다. 필요한 수정과 HTTP 경계 검증을 [[roadmap]]에 남겼으며, 기능 페이지에서도 해당 경로를 완전히 검증된 실제 동작으로 단정하지 않습니다.

## 검증 기록을 읽을 때의 구별

| 근거 | 확인하는 것 | 이것만으로 알 수 없는 것 |
|---|---|---|
| 소스·추적 검사(`static`) | 설정·구현·인수 ID가 연결되어 있음 | 실제 모델·장치 동작 |
| 테스트·실행(`runtime`) | 기록에 지정된 환경에서 시나리오가 실행됨 | 실제 호스트인지 여부는 환경 기록을 봐야 함 |
| 산출물(`artifact`) | 생성 파일·해시·이미지 등의 결과 | 그 설치물이 현재 실행 중인지 |
| 설치(`deployment`) | 특정 구성이나 파일이 호스트에 반영됨 | 모든 경로를 실제로 호출했는지 |
| 실제 서비스(`external_service`) | 모델·Ollama·Discord 등의 실제 응답 | 장기간 품질과 신뢰성 |
| 장치(`device`) | 명시한 화면과 조작의 관찰 | 다른 화면·버전·장치의 동작 |
| 의미 대조(`semantic`) | 특정 해석이나 충돌의 해소 | 배포·실행 성공 |

기능 테스트는 실제 Temporal 개발 서버와 LangGraph를 사용하지만, 모델은 스크립트 응답을 사용합니다. Hermes·launchctl·시계 대역을 쓰는 테스트도 있으므로 통과 숫자를 실제 모델 호출 횟수처럼 읽지 않습니다. `not_run`은 미실행, `blocked`는 확인이 막힘, `unknown`은 결과를 확정하지 못함이며 모두 단순 실패와 다릅니다.

아래는 빠짐없는 원기록을 찾아보기 위한 부록입니다. 기능의 처리 순서는 각 기능 본문에서 읽을 수 있습니다.

## 목표 기록

{{state:goals}}

## 인수 기록

{{state:accepts}}

## 실행·설치·검사 근거

{{state:evidence}}

## 결정 기록

{{state:decisions}}
