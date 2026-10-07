# KnowledgeOS Application

이 패키지는 KnowledgeOS owner의 정책, Work 수락, 제안·결정·적용, 효과 영수증과 복구를 소유한다. `KnowledgeApplication`은 MCP와 운영 CLI가 함께 사용하는 경계이며, `gateway.py`는 수락된 교환 증거와 소유 outbox를 처리한다. `experience.py`는 확정된 lifecycle 사건에서 적격 후보를 추출한다.

`read_tools.py`는 현재 projection과 정확한 ResourceReference를 대조해 제한된 근거 구간, Base view, 링크 방향·미연결 후보, checkbox, Registry와 Gateway 증거를 반환한다. MCP 자료 정책은 각 후보에 다시 적용한다. Work 문맥에서는 승인된 출처로 읽기 범위를 제한한다.

`mcp_effects.py`는 작업별 계획과 비정본 Work 산출물을 Pending에 기록한다. owner journal은 동일 효과의 재호출, 충돌, 불확실한 결과를 처리한다. `proposal_create`의 정규화와 `proposal_draft_note`는 출처와 현재 digest에 묶인다. 사람의 결정과 정본 적용은 분리된 owner 호출이다.

`team_catalog.py`와 `work_methods.py`는 여섯 Team의 전담 Manager, specialist, 독립 EvalOfficer 및 방법·도구 상한을 정의한다. `work_tools.py`는 WorkRun, GraphRun, Profile, method pin과 현재 효과 상태를 매 호출 확인한다. `exops.py`는 Director admission과 Work 상태의 owner Web 경계다. `temporal_work.py`의 지속 실행 계약은 별도이며, Hermes 네이티브 Runner와 실제 사용자 Work 실행은 아직 결합되지 않았다.

상태의 권위는 Operation State의 OwnerJournal에 있다. `recover`는 frozen 증거와 실제 artifact를 대조하며 불확실한 효과를 임의로 재실행하지 않는다.
