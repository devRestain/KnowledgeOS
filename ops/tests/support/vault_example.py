"""Hermetic sample content, excluded from real Vault projection."""
from vaultops.note_engine import render_frontmatter

EXAMPLE_PATH = "40_Knowledge/Ideas/Core readiness example.md"

def example_source() -> str:
    properties = {
        "schema_version": 1, "id": "bbd369ca-7580-43f3-88fb-0566d06d8f55", "type": "idea", "title": "Core readiness example",
        "status": "seed", "created": "2026-10-04T00:00:00+09:00", "modified": "2026-10-04T00:00:00+09:00",
        "aliases": [], "tags": ["knowledgeos/example"], "sensitivity": "personal", "ai_policy": "ask", "ai_status": "idle",
        "possibility": "Provider-free retrieval and a deterministic normalization proposal can preserve the source meaning while owner control retains decisions.",
    }
    return render_frontmatter(dict(reversed(list(properties.items()))), """# Core readiness example

## 가능성

선택된 Core 계약의 graphless KnowledgeOS에서 retrieve → normalize proposal → review 경로를 사용한다. 본문과 note 의미는 Blueprint registries에 따른다.

## 검증할 가정

조회한 source digest를 다시 검사한다. review는 source와 proposal bytes를 변경하지 않으며, 승인은 canonical apply와 별도로 기록한다. Runtime을 재구성해도 durable owner State는 유지한다.

## 연결

[[99_System/Guides/KnowledgeOS|KnowledgeOS 계약과 사용 경계]]
""")

