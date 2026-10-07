# KnowledgeOS Interfaces

이 패키지는 단일 `vaultmcp` v3, 운영용 `vaultctl`, owner Web 요청의 입력과 결과 경계다. 업무 규칙과 권한 판단은 `KnowledgeApplication` 및 owner 계약에 둔다.

`vaultmcp`는 일반 연결에도 24개 도구를 표시한다. 매 호출에 현재 owner 정책과 자료 범위를 다시 적용하며, Work 도구는 신뢰된 Runner 시작 문맥의 WorkRun·executor·GraphRun이 없으면 거부한다. 모델 입력으로 actor, root 또는 binding을 고를 수 없다. 일반 자료는 `ask`를 구성된 로컬 MCP에 허용하며 `deny`·confidential은 제외한다. `local_only` 본문은 신뢰된 로컬 실행 문맥에서만 반환한다. 제안은 Pending에 기록하고 사람의 승인·거절·정본 적용은 MCP에 제공하지 않는다.

`vaultctl`에는 초기 설정, 계약·문서 검사, 색인 구축·검증·내보내기, owner 상태·영수증·복구 및 storage 유지보수만 남긴다. 사용자의 작성·탐색·검토는 Obsidian에서 한다.

`exops_web.py`는 인증된 owner actor와 CSRF token을 매번 검사한다. `work/intake`는 구성자가 주입한 Director admission callback으로 접수하며 `work/read`로 상태를 읽는다. `proposal/list`·`read`·`decide`·`apply`는 Pending 원문 검토, owner 결정, 별도 정본 적용을 제공한다. 현재 소스에는 listener와 Hermes Runner의 실제 결합이 없다.

MCP 입력·결과 계약은 `ops/schemas/mcp/`와 capability manifest에 있으며, 변경 후 프로젝트 컨테이너의 `make schema-check`, `make test`, `make lint`를 실행한다.
