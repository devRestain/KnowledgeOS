# KnowledgeHub bridge 파일

이 폴더는 KnowledgeOS의 bridge request·response schema와 수동 교환 데이터를 위한 공간이다. `protocol/request.schema.json`과 `response.schema.json`은 control의 동일 schema를 복제한다. 파일을 만들거나 읽는 것만으로 Core admission, 실행, domain acceptance 또는 승인이 성립하지 않는다.

직접 사용자 요청은 KnowledgeApplication의 owner control로 들어가고, InterOps 요청은 별도 Gateway에서 authenticated caller, eligibility, policy, semantic pins, expiry와 correlation을 검사한다. durable admission receipt 이후의 ACK와 실제 실행 결과는 구분한다.

지속적인 queue, approval, receipt, frozen evidence, unresolved outcome과 resume 정보는 Operation State에 둔다. 재구성 가능한 index·cache·log는 Runtime에 둔다. 이 Vault는 owner journal을 보관하거나 actor authority와 host roots를 공급하지 않는다.

현재 mobile shortcut, remote Git 교환, provider와 외부 transport는 구성되지 않았다. 실제 writer cutover와 운영 채택은 별도 C12 범위다. 계약과 사용 방법은 [[99_System/Guides/KnowledgeOS]]에서 확인한다.
