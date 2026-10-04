# KnowledgeOS 온톨로지 작성 공간

KnowledgeOS가 자기 도메인의 용어·관계와 필요한 의미 정렬을 작성하는 공간이다. 기존 note type·property·relation 정의는 Blueprint에 유지한다. 이곳에서는 원본을 찾아 사용하고, 새 정의를 작성할 수 있다. 문서를 작성했다고 해서 도메인 패키지가 발행되거나 Core가 KnowledgeOS에 운영 채택된 것은 아니다.

## 기존 정의의 원본

설계 충돌은 [Blueprint](../OBSIDIAN_VAULT_BLUEPRINT.md), [기계 registry](../blueprint/blueprint.yaml), [Whitepaper](../OBSIDIAN_VAULT_WHITEPAPER.md) 순서로 해소한다.

| 원본 또는 파생 파일 | 정확한 범위와 역할 |
|---|---|
| `blueprint/blueprint.yaml#/note_types` | 기존 note 종류와 각 종류의 계약 |
| `blueprint/blueprint.yaml#/property_registry` | 속성 이름·값·참조 대상의 registry |
| `blueprint/blueprint.yaml#/relation_registry` | canonical·context 관계, 방향과 provenance 규칙 |
| `ops/policies/properties.yaml` | Blueprint에서 생성한 속성 정책 |
| `ops/policies/relations.yaml` | Blueprint에서 생성한 관계 정책 |

기존 정의는 이 폴더에 복제하지 않는다. 새 문서에서 필요한 원본 selector를 가리키고, 실행 계약을 바꾸어야 할 때 해당 원본과 그 검증·생성 경로를 함께 수정한다.

## 작성할 문서

- [TERMS.md](TERMS.md): 용어의 안정적인 식별자, 뜻, 소유자, 원본을 작성한다.
- [RELATIONS.md](RELATIONS.md): 관계의 뜻, 방향, 허용 주체와 대상, 근거를 작성한다.
- [ALIGNMENTS.md](ALIGNMENTS.md): 실제 의미 불일치가 있을 때 목적과 방향을 정한 정렬을 제안한다. 이 작성 공간에 채택된 도메인 정렬은 없다.

새 도메인 용어의 작성 관례는 `knowledgeos.domain`이다. 기존 Blueprint의 type·property 식별자는 그대로 사용한다. 이름이 비슷한 `derived_from`과 Core의 `derivedFrom`도 각각의 원본 정의를 확인하며, 자동으로 동일한 관계로 치환하지 않는다.

## 공통 의미와 사용 흐름

공통 프로토콜 의미는 [Core 온톨로지](../../../Core/ontology/README.md)와 [catalog](../../../Core/contracts/catalog.json)를 참조한다. 공통 정의를 읽는 것과 domain 패키지를 실행에 채택하는 것은 별도 단계다. 현재 Gateway가 허용하지 않는 domain term을 문서 작성으로 자동 등록하지 않는다.

용어·관계는 **기존 원본 확인 → 작성 → KnowledgeOS 소유자 검토 → 기존 계약 반영 또는 후속 패키지 채택 준비**로 관리한다. 정렬은 **실제 불일치 확인 → 목적·방향을 정한 제안 → 참여자 검토 → 필요한 변환 검증 → 버전 고정과 수신자 채택**으로 구체화한다. 역방향 사용과 정렬 합성은 별도 검토가 필요하다.

유지하는 정의는 KnowledgeOS 내부에, 지속적인 주장·검토·채택 기록은 KnowledgeOS 소유 State에, 도메인 증거와 지식은 기존 KnowledgeHub Vault에 둔다. 현재 State·Runtime·Vault 경로는 이 공간을 마련하기 위해 이동하지 않는다. 진행 상태와 수행한 검증은 [Operation PROJECT_STATE](../PROJECT_STATE.md)에 기록한다.
