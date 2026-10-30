# KnowledgeOS Interfaces

이 패키지는 단일 `vaultmcp` v3, 운영용 `vaultctl`, owner Web 요청의 입력과 결과 경계다. 업무 규칙과 권한 판단은 `KnowledgeApplication` 및 owner 계약에 둔다.

`vaultmcp`는 일반 연결에도 24개 도구를 표시한다. 매 호출에 현재 owner 정책과 자료 범위를 다시 적용하며, Work 도구는 신뢰된 Runner 시작 문맥의 WorkRun·executor·GraphRun이 없으면 거부한다. 모델 입력으로 actor, root 또는 binding을 고를 수 없다. 일반 자료는 `ask`를 구성된 로컬 MCP에 허용하며 `deny`·confidential은 제외한다. `local_only` 본문은 신뢰된 로컬 실행 문맥에서만 반환한다. 제안은 Pending에 기록하고 사람의 승인·거절·정본 적용은 MCP에 제공하지 않는다.

`vaultctl`에는 초기 설정, 계약·문서 검사, 색인 구축·검증·내보내기, owner 상태·영수증·복구 및 storage 유지보수만 남긴다. 사용자의 작성·탐색·검토는 Obsidian에서 한다.

`exops_web.py`는 인증된 owner actor와 CSRF token을 매번 검사한다. 자유형 `work/intake`와 별도로 `work/intake-template`은 신뢰된 Director callback이 고정한 Team·방법·원본 참조만 받아들인다. 메타데이터 요청은 두 WorkRun을 하나의 owner 영수증에 기록한다. 같은 요청의 재시도는 현재 Work 전달 상태를 반영한 기존 영수증을 돌려주며, 미해결 전달이 있으면 `reconciliation_required`를 표시한다. 같은 원본·템플릿의 새 요청은 owner 화해 전 거부한다. `work/list`·`work/result`는 문서별 상태와 독립 평가가 끝난 산출물을 표시한다. 두 Team의 Worker 결과가 기록되면 owner가 독립 평가와 새 값마다 필요한 정확한 원문 인용을 검사해 원본 해시의 Pending `update_note`를 자동 준비한다. `proposal/read`는 Pending 바이트를 owner 영수증에 다시 묶고 값별 인용과 정본 diff를 반환한다. 실패와 무변경 상태는 Work 목록에 남기고 `work/prepare-metadata`는 중단된 준비를 같은 검증 조건으로 재시도한다. 이후 `proposal/list`·`read`·`decide`·`apply`에서 검토, 결정, 정본 적용을 각각 분리한다.

`search/query`는 Team Work를 만들지 않는 읽기 전용 owner 검색이다. Lexical이 기본이고 Vector·Hybrid·Ontology는 요청한 모드만 실행한다. 후보를 선택한 세대와 반환 직전 세대, 정확한 원본 참조와 문서 정책을 다시 확인한 뒤 제목·경로·발췌·일치 이유를 붙인다. 준비되지 않은 Vector 모델이나 색인은 오류를 반환한다. 문서 강화 결과의 보강점마다 유효한 인용 번호가 있어야 하며, owner는 현행 원본·색인·인용 청크를 재검증해 근거 발췌와 불확실성·빠진 자료를 반환한다. owner가 생성한 비정본 Work 산출물은 원문 검색 세대를 바꾸지 않지만, 사용자 AI Review 문서의 명시적 검색은 유지한다. `loopback_owner.py`는 owner HTTP 어댑터와 메모리 세션 토큰을 제공한다. 기본 주소는 프로세스의 loopback이고 내부망 전용 컨테이너에서만 `owner` 서비스 주소에 묶을 수 있다. `owner_relay.py`는 호스트의 loopback 게시와 내부망 owner 사이에서 크기가 제한된 JSON 요청을 전달하며 문서·State·토큰을 저장하지 않는다. 일회용 컨테이너에서는 `127.0.0.1:17901`을 통해 실제 owner HTTP 어댑터의 Lexical 검색, 두 메타데이터 Work 접수와 상태 조회, 잘못된 토큰의 거절을 합성 자료에서 확인했다. 실제 Director, Temporal, Hermes Runner, 모델 서비스 구성과 `127.0.0.1:17900` listener 시작은 별도의 운영 단계다.

`read_only_owner.py`는 Director·Team·Worker·모델·Temporal 없이 실행하는 별도 읽기 전용 시작점이다. config v3의 다섯 독립 루트와 기존 owner journal, 현재 Core/State 핀, 원본과 일치하는 Lexical 세대를 시작 전에 검사한다. `search/query`의 Lexical 검색과 `owner/status`, `work/list`·`read`·`result`만 허용하며, 다른 경로는 요청 내용을 처리하거나 효과를 내기 전에 거부한다. 검색 결과에는 정확한 원본 참조와 `chunk_locator`·`chunk_hash`가 포함된다. actor는 신뢰된 시작 인수로만 정하고 토큰 값은 인수·환경·로그에 넣지 않는다. 소유자 전용 0700 디렉터리의 0600 `token` 파일을 읽어 메모리에 유지하며 relay에는 이 파일을 마운트하지 않는다. `ops/compose.owner-read-only.yaml`은 다섯 루트를 모두 읽기 전용으로 마운트하고 relay만 호스트 loopback에 게시한다. 실행·일회용 HTTP 검증 절차는 [RUNTIME 계약](../../../../docs/RUNTIME.md#read-only-owner-entrypoint)을 따른다. 이 구성은 기존 쓰기 허용 어댑터나 네이티브 Obsidian·모델 서비스의 활성화를 대신하지 않는다.

MCP 입력·결과 계약은 `ops/schemas/mcp/`와 capability manifest에 있으며, 변경 후 프로젝트 컨테이너의 `make schema-check`, `make test`, `make lint`를 실행한다.
