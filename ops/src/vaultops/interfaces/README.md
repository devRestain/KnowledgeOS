# KnowledgeOS Interfaces

이 패키지는 `vaultctl`과 `vaultmcp`의 입력 검증과 결과 표현을 맡는다. 기존 실행 이름은 shim을 통해 유지하고, 요청은 KnowledgeApplication 경계로 전달한다. CLI의 익숙한 note 선택은 owner 경계에서 논리 참조로 변환한다.

MCP v2는 `knowledge_search`, `knowledge_retrieve`, `proposal_create`, `proposal_inspect` 네 도구를 제공한다. source·proposal 대상은 Core ResourceReference로 받고, caller가 actor 권한이나 host root를 지정할 수 없다. 승인·적용·provider 활성화는 MCP 범위에 없다.

결과는 실행 outcome, 수락 state와 Pending/canonical effect를 분리한다. stdio stdout에는 protocol frames만 쓰고 진단은 제한된 stderr로 보낸다. owner 관측과 복구는 CLI의 `operation check`, `status`, `recover`를 사용한다.

입출력 계약을 바꾸면 `ops/schemas/mcp/`와 capability manifest를 함께 갱신하고, 루트 `make core-readiness-check`로 schema·protocol·concurrency·cancellation 책임을 검증한다.
