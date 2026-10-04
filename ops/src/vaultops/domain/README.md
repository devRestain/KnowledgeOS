# KnowledgeOS Domain

이 패키지는 Blueprint로 정의한 note·property·relation의 의미와 정규화 계산을 맡는다. 기존 retrieval, note validation, proposal 알고리즘을 재사용하며, `normalization.py`는 source digest를 확인하고 Pending proposal의 정확한 bytes를 계산한다.

도메인 정의의 authoritative source는 기존 Blueprint registry다. `Ontology/`는 이 원본을 참조한다. 이 계층은 caller 권한이나 승인 결정을 만들지 않는다.

선택한 정규화 계산은 `KnowledgeApplication`에서 호출한다. 파일 생성과 canonical apply는 application이 의도를 기록한 뒤 adapter에 맡긴다. 변경 후에는 루트의 `make core-readiness-check`로 의미·본문 보존과 동일 입력의 결정성을 확인한다.
