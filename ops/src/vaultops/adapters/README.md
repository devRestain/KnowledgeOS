# KnowledgeOS Adapters

이 패키지는 trusted root binding, 정적 Core 입력, owner persistence와 filesystem effect를 구현한다. `paths.py`는 config v3의 다섯 roots를 독립적으로 해석하고, `core.py`는 frozen release와 semantic digest를 확인한다. `owner_journal.py`는 State의 기록·writer 조정·generation fence를, `artifacts.py`는 admitted Pending artifact 생성을 맡는다.

물리 경로는 private startup 설정에서만 정한다. public ResourceReference는 owner·kind·논리 ID·revision·digest로 자원을 가리킨다. 이전 config나 호환되지 않는 State를 자동 변환하지 않는다.

writer는 정확한 이전 bytes 비교, atomic replacement와 fsync를 사용한다. State의 의도·승인·receipt·unresolved 기록과 Runtime의 재구성 가능한 index·cache·log를 분리한다. 실제 State 이동은 별도 C12 범위다.

adapter 변경은 프로젝트 이미지의 hermetic Make 검증으로 확인한다. 실제 host binding, 데이터 전환이나 외부 연결은 `planning/c12-preparation/C12_HANDOFF.md`에서 정확한 대상과 효과를 선택한 뒤 진행한다.
