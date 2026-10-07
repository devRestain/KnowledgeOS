# KnowledgeOS Blueprint 계약

이 디렉터리는 현재 KnowledgeOS의 문서·속성·관계·Base·검색 projection·프라이버시·운영 명령 계약을 담습니다. 실제 KnowledgeHub Vault, Operation State 또는 Runtime은 이 디렉터리에 들어 있지 않습니다.

## 권위와 검증

1. [사용자와 제품 계약](../OBSIDIAN_VAULT_BLUEPRINT.md)
2. [기계 판독 계약](blueprint.yaml)
3. [기술 설명](../OBSIDIAN_VAULT_WHITEPAPER.md)

`blueprint.schema.json`은 현재 구조를 검사하고, KnowledgeOS 의미 검증기는 note type·template·관계 방향·Base view·projection·검색 범위·운영 명령을 검사합니다. `CHECKSUMS.sha256`은 이 세 문서, 스키마와 이 README의 바이트를 고정합니다. 변경한 뒤 `make source-check blueprint-check schema-check`를 실행합니다.

사용자는 Obsidian에서 문서를 작성하고 검색합니다. 에이전트는 owner가 허용한 단일 `vaultmcp` v3에서 근거를 읽고 산출물이나 Pending 제안을 제출합니다. `vaultctl`은 상태·검사·인덱스·복구를 담당합니다. 실행 메서드와 도구 권한은 Blueprint의 기기·provider 추정이 아니라 owner 정책, Team/Officer method catalog 및 MCP capability manifest에서 읽습니다.

생성된 정책과 스키마는 소스 계약의 사본입니다. `make schema-check`는 생성 결과와 checked-in 사본의 차이를 확인합니다. 소스 및 fixture 검증은 설치된 Obsidian GUI, Hermes Runner, 외부 Gateway나 실제 사용자 자료의 검증을 대체하지 않습니다.
