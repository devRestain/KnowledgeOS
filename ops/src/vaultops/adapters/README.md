# KnowledgeOS 어댑터

이 패키지는 Core 계약 확인, 독립 root 해석, owner State 저널, 정본 파일 효과와 제한된 Gateway 전송을 담당합니다. [Core 선택](../../../config/core-adoption.json)은 버전과 digest를 고정하고, `paths.py`는 Core·control·KnowledgeHub·State·Runtime을 별도로 바인딩합니다.

`owner_journal.py`는 intent, receipt, 재호출과 불확실한 효과의 조정을 맡습니다. 정본 변경은 정확한 preimage와 owner 결정을 확인한 뒤 별도 적용 단계에서 수행합니다. 일반 MCP 호출의 도구 노출이나 반환된 제안은 적용 권한이 아닙니다.

`storage.py`, `legacy_archive.py`, `core_cutover.py`는 정확한 State 전환을 위한 유지보수 코드입니다. 개발 단계 archive를 지우려면 현재 journal·transition·pointer·복구 참조를 먼저 제거할 수 있는 계약과 격리 fixture를 검증해야 합니다. 현재 소스 개정은 private State payload를 변경하지 않습니다.

`interops_socket.py`는 owner가 수락한 로컬 Gateway 프레임만 전달합니다. 외부 Operation의 Vault나 State를 직접 읽지 않습니다. 네이티브 Runner 및 외부 Gateway 연결은 별도 채택 증거가 필요합니다.
