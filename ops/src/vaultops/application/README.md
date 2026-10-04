# KnowledgeOS Application

이 패키지는 KnowledgeOS가 요청을 받아도 되는지 판단하고, 정책·workflow·수락 평가·사람의 결정·복구를 연결한다. `knowledge.py`의 `KnowledgeApplication`은 CLI와 MCP의 공통 owner 경계이며, `gateway.py`는 별도의 InterOps admission을 담당한다.

identity, Core pins, policy, journal, handler, evaluator는 trusted factory에서 구성한다. 조합만으로 파일을 생성하거나 프로세스를 시작하지 않는다. 결정·intent·receipt는 Operation State의 하나의 OwnerJournal에서 확정한다.

승인은 target digest에 묶인 결정과 pending apply intent를 기록한다. canonical apply는 별도로 요청해야 한다. `recover`는 실제 artifact와 frozen evidence를 비교하고, 불확실한 효과를 자동 재실행하지 않는다. `status`는 실행, 수락, 승인, freshness와 completeness를 구분한다.

공개 도메인 요청은 이 경계로 전달한다. 변경 검증은 루트의 `make core-readiness-check`와 최종 `make acceptance`를 사용한다.
