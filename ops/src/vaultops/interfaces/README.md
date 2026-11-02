# KnowledgeOS Interfaces

이 패키지는 단일 `vaultmcp` v3, 운영용 `vaultctl`, owner Web 요청의 입력과 결과 경계다. 업무 규칙과 권한 판단은 `KnowledgeApplication` 및 owner 계약에 둔다.

`vaultmcp`는 일반 연결에도 24개 도구를 표시한다. 매 호출에 현재 owner 정책과 자료 범위를 다시 적용하며, Work 도구는 신뢰된 Runner 시작 문맥의 WorkRun·executor·GraphRun이 없으면 거부한다. 모델 입력으로 actor, root 또는 binding을 고를 수 없다. 일반 자료는 `ask`를 구성된 로컬 MCP에 허용하며 `deny`·confidential은 제외한다. `local_only` 본문은 신뢰된 로컬 실행 문맥에서만 반환한다. 제안은 Pending에 기록하고 사람의 승인·거절·정본 적용은 MCP에 제공하지 않는다.

`vaultctl`에는 초기 설정, 계약·문서 검사, 색인 구축·검증·내보내기, owner 상태·영수증·복구 및 storage 유지보수만 남긴다. 사용자의 작성·탐색·검토는 Obsidian에서 한다.

`exops_web.py`는 신뢰된 로컬 시작 actor를 사용하고 행동마다 현재 owner 권한을 확인한다. 로그인·세션·CSRF 토큰은 없다. 자유형 `work/intake`와 별도로 `work/intake-template`은 Director가 고정한 Team·방법·원본 참조만 받아들인다. 메타데이터 요청은 두 WorkRun을 한 owner 영수증에 기록한다. 재시도는 기존 영수증을 반환하며 미해결 전달은 `reconciliation_required`로 남긴다. 두 Team의 독립 평가와 값별 정확한 인용을 확인한 뒤 결합 Pending을 준비하고 `proposal/read`에서 원문·diff·근거를 표시한다. 검토, 승인·거절 기록, 정본 적용은 각각 별도 행동이다.

`search/query`는 Work를 만들지 않는 owner 검색이다. Lexical이 기본이며 Vector·Hybrid·Ontology는 선택한 모드와 실제 바인딩을 사용한다. 현재 세대·원본 참조·자료 정책·인용 청크를 다시 확인하고 준비되지 않은 모드는 오류를 반환한다. `loopback_owner.py`는 로컬 HTTP를 제공하며 기본값은 프로세스 loopback이다. 컨테이너의 내부망 주소는 외부에 공개하지 않고 mount-free `owner_relay.py`만 호스트 loopback에 게시한다. 두 경계의 Host·Origin 검사와 JSON 요청 제한은 외부 웹페이지의 요청을 거부하며 교차 출처를 허용하지 않는다. 같은 사용자 권한의 로컬 프로그램은 신뢰한다.

`read_only_owner.py`는 Director·Worker·모델·Temporal 없이 기존 다섯 루트·journal·Core/State 핀과 Lexical 원본을 검사한다. 읽기 전용 route allowlist 밖의 요청은 내용을 처리하거나 효과를 내기 전에 거부한다. actor는 시작 인수로 정하고 토큰 파일은 요구하지 않는다. Compose는 모든 루트를 읽기 전용으로 마운트하고 relay만 loopback에 게시한다. 명령과 일회용 검증은 [RUNTIME 계약](../../../../docs/RUNTIME.md#read-only-owner-entrypoint), 실제 설치·기기 검증 상태는 Operation 상태 문서를 따른다.

MCP 입력·결과 계약은 `ops/schemas/mcp/`와 capability manifest에 있으며, 변경 후 프로젝트 컨테이너의 `make schema-check`, `make test`, `make lint`를 실행한다.
