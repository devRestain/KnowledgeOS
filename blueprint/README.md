# KnowledgeOS blueprint pack

이 디렉터리는 실제 Vault가 아니라 구현 입력 패키지다.

## 읽는 순서

1. `../OBSIDIAN_VAULT_BLUEPRINT.md`
2. `blueprint.yaml`
3. `../OBSIDIAN_VAULT_WHITEPAPER.md`

첫 문서는 사용자의 정보 구조·Home/Mobile·장치 역할·자동화 lane을 정한다. YAML은 path, type, template, command, bridge, acceptance 계약을 기계 판독 가능한 형태로 반복한다. White paper는 atomic write, hash, approval, receipt, plugin audit, Codex isolation, launchd의 저수준 세부사항을 제공한다.

충돌 시 위 순서가 우선한다. 구현자는 세 문서 중 편한 하나만 골라 읽으면 안 된다.

## 계약 검증

- `blueprint.schema.json`은 YAML의 top-level과 핵심 중첩 구조를 fail-closed로 검사한다. 정확한 type/status 집합, relation 방향, Base filter·sort·limit, action↔schema↔command, bridge 전이, JSONL 필드·hash domain 같은 의미 규칙은 **필수** cross-validator와 생성된 action별 schema가 함께 검사한다. 둘 중 하나만 통과해서는 유효한 blueprint가 아니다.
- `CHECKSUMS.sha256`은 이 패키지의 다섯 산출물(두 Markdown 명세, YAML 계약, JSON Schema, 이 README)의 현재 bytes를 고정한다. 문서를 고친 뒤 검증을 다시 실행하고 manifest를 갱신한다.
- 실제 구현에서는 `vaultctl blueprint validate`가 JSON Schema뿐 아니라 경로↔type↔template, action↔output schema↔command, bridge state↔runtime directory, Markdown 예시↔생성 schema의 교차 규칙까지 검사해야 한다.
- `vaultctl schema export --check`는 `blueprint.yaml`과 version-controlled action/policy에서 생성한 schema·Property Dictionary·bridge protocol 사본의 diff가 0인지 확인한다.

## 이 패키지가 결정한 것

- PARA-lite + Evergreen/Zettelkasten + RDF-lite + Hybrid RAG의 역할 분리
- Home은 desktop 조종석, Mobile은 포착·조회·보류 화면
- Idea, Question, Knowledge, Project, Artifact의 일급 type
- Mac control repository와 Working Copy용 Vault notes repository 분리
- Git-tracked `.vault-bridge`와 Mac-local `runtime/queue` 분리
- Codex/API/local provider 사이의 silent fallback 금지
- preview-first LLM과 digest-bound apply
- deterministic `notes.jsonl`/`edges.jsonl` projection
- lexical/typed-link를 먼저 활성화하고 평가 통과 뒤 vector/RRF를 더하는 Hybrid RAG, GraphRAG는 보류

## 구현 전 반드시 조사할 것

- 현재 Vault path와 Git root
- 현재 Obsidian Git 설정과 dirty/conflict 상태
- Working Copy가 연결한 repository와 branch
- 기존 Properties의 vault-wide type
- 기존 template, plugin, config folder
- GitHub에 저장할 수 없는 민감 자료의 존재

기존 사용자 파일이 있으면 새 구조를 덮어씌우지 말고 additive migration plan부터 만든다.
