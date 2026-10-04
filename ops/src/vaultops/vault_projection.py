"""Deterministic Vault presentation inputs; never owner State or execution authority."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .adapters.core import PINS
from .base_dashboard import dashboard_sources
from .note_engine import render_frontmatter

CONTRACT_PATH = "99_System/Schemas/KnowledgeOS_Contract.json"
GUIDE_PATH = "99_System/Guides/KnowledgeOS.md"
EXAMPLE_PATH = "40_Knowledge/Ideas/Core readiness example.md"
PREPARE_INPUTS_PATH = "99_System/Scripts/QuickAdd/PrepareInputs.js"
CLIENT_DIRECTORY = ".obsidian-mac/plugins/knowledgeos-thin-client"
CLIENT_FILES = ("manifest.json", "contract.json", "main.js", "styles.css")
RETIRED_FIXTURES = (
    "01_AI_Review/Resolved/E05 Frozen Triage e0511111-111.md",
    "40_Knowledge/Ideas/Live provider smoke.md",
    "10_Journal/Weekly/2026/2026-W39.md",
    "10_Journal/Monthly/2026/2026-09.md",
)


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def presentation_contract(control: Path) -> dict[str, Any]:
    capabilities = json.loads((control / "ops/schemas/mcp/capabilities.json").read_bytes())
    return {
        "schema_version": 1,
        "contract_id": "knowledgeos-vault-presentation-v1",
        "operation_id": "knowledgeos",
        "graph_profile": "graphless",
        "pins": dict(PINS),
        "private_config_schema_version": 3,
        "purpose": "descriptive_static_projection",
        "authority": "none",
        "ownership": {"vault": "durable_domain_content", "state": "owner_control_and_restart_evidence_outside_vault", "runtime": "reconstructable_host_artifacts_outside_vault"},
        "workflow": ["retrieve", "normalize_proposal", "read_only_review"],
        "control": {"owner": "KnowledgeApplication", "journal": "OwnerJournal", "approval": "digest_bound_decision_and_pending_intent", "canonical_apply": "separate_explicit_dispatch", "recovery": "observe_and_reconcile_without_dispatch"},
        "interfaces": {"mcp": capabilities, "operation_commands": ["check", "status", "recover"], "resource_authority": "trusted_startup_with_Core_ResourceReference"},
        "observations": ["admission_ack", "execution_outcome", "acceptance_state", "human_approval", "freshness", "completeness"],
        "transport": "unconfigured",
        "thin_client": "staged_disabled_pending_separate_broker_adoption",
        "machine_state_owner": "Operations/KnowledgeOS/PROJECT_STATE.md",
    }


def _system_note(identifier: str, title: str, purpose: str, body: str) -> str:
    return render_frontmatter({
        "schema_version": 1, "id": identifier, "type": "system", "title": title,
        "status": "active", "created": "2026-10-04T00:00:00+09:00", "modified": "2026-10-04T00:00:00+09:00",
        "aliases": [], "tags": [], "sensitivity": "personal", "ai_policy": "deny", "ai_status": "idle", "purpose": purpose,
    }, body)


def guide_source() -> str:
    return _system_note("system-knowledgeos-guide", "KnowledgeOS", "Core 계약과 Vault 사용 경계", """# KnowledgeOS

[[Home]] · [[Mobile]] · [[99_System/Bases/Review.base|제안 보기]]

이 Vault는 KnowledgeOS가 관리하는 문서·템플릿·조회 화면이다. 실행 제어와 승인 기록은 Operation State의 OwnerJournal에 둔다. 이 문서와 note property는 실행 권한을 만들지 않는다.

## 계약과 설정

Core/schema 0.16.1, Capsule 2.0.0, semantic 0.6.0, ResourceBinding/HostBinding API 0.3.0을 사용한다. 정확한 digest와 공개 인터페이스는 `99_System/Schemas/KnowledgeOS_Contract.json`에 투영된다. Core는 읽기 전용이며 Blueprint registries가 note·property·relation 의미를 정의한다.

private config v3에서 Core, control, Vault, State, Runtime을 각각 선언한다. v1/v2 설정과 호환되지 않는 State는 쓰기 전에 거부한다. 자동 변환과 State/Runtime 별칭은 없다.

## 조회와 제안

- `vaultctl operation check`로 선택한 bindings와 manifest를 확인한다.
- `vaultctl operation status`에서 실행 결과, 평가 수락, 승인, freshness와 completeness를 각각 확인한다.
- `vaultctl ai normalize --source <Vault-relative note> --expected-sha256 <source digest>`는 Pending artifact를 만든다. provider가 필요하지 않다.
- `vaultctl ai review --proposal <Vault-relative proposal>`은 읽기 전용이다.
- `vaultctl ai approve` 또는 `reject`는 trusted local identity와 정확한 proposal digest를 사용한다. source·proposal·policy·schema·semantic 변경은 기존 응답을 무효화한다.
- 승인은 decision과 pending intent를 기록한다. `vaultctl ai apply`는 별도로 명시적으로 실행한다.
- `vaultctl operation recover`는 관찰과 조정만 수행한다. 불확실한 효과를 자동 재실행하지 않는다.

MCP는 `knowledge_search`, `knowledge_retrieve`, `proposal_create`, `proposal_inspect` 네 도구만 제공한다. source와 proposal은 Core ResourceReference이며 caller가 host path나 actor authority를 공급할 수 없다. MCP에는 approve·apply·provider activation이 없다.

## 화면과 기록의 의미

Review Base의 status는 문서 분류다. admission ACK, 실행 완료, domain acceptance, human approval과 서로 대체할 수 없다. 실행 여부를 확인할 근거가 없으면 unknown으로 유지한다. 외부 bridge 파일은 수동 교환 데이터이며 Core Gateway admission이나 human approval을 대신하지 않는다.

현재 first-party thin client 파일은 설치 가능한 정적 자료로만 배치하고 profile에서 비활성으로 둔다. broker와 provider transport, mobile shortcut, 외부 전송은 구성되지 않았다. 자동 Git, shell과 background writer도 실행하지 않는다.

## 예시와 운영 채택

`40_Knowledge/Ideas/Core readiness example.md`는 생성된 seed에 포함되는 재현 가능한 구현 입력이다. 기존 provider-smoke와 기간별 구현 fixture는 workspace Tmp에 보존한다. 준비 검증은 실제 State 전환, 운영 writer cutover, 서비스 활성화 또는 device 검증을 완료하지 않는다.
""")


def example_source() -> str:
    properties = {
        "schema_version": 1, "id": "bbd369ca-7580-43f3-88fb-0566d06d8f55", "type": "idea", "title": "Core readiness example",
        "status": "seed", "created": "2026-10-04T00:00:00+09:00", "modified": "2026-10-04T00:00:00+09:00",
        "aliases": [], "tags": ["knowledgeos/example"], "sensitivity": "personal", "ai_policy": "ask", "ai_status": "idle",
        "possibility": "Provider-free retrieval and a deterministic normalization proposal can preserve the source meaning while owner control retains decisions.",
    }
    return render_frontmatter(dict(reversed(list(properties.items()))), """# Core readiness example

## 가능성

Core 0.16.1의 graphless KnowledgeOS에서 retrieve → normalize proposal → review 경로를 사용한다. 본문과 note 의미는 Blueprint registries에 따른다.

## 검증할 가정

조회한 source digest를 다시 검사한다. review는 source와 proposal bytes를 변경하지 않으며, 승인은 canonical apply와 별도로 기록한다. Runtime을 재구성해도 durable owner State는 유지한다.

## 연결

[[99_System/Guides/KnowledgeOS|KnowledgeOS 계약과 사용 경계]]
""")


def expected_projection(control: str | Path) -> dict[str, bytes]:
    """Render the declared generated Vault copy without reading its target."""
    from .vault_artifacts import expected_system_artifacts

    root = Path(control)
    result = expected_system_artifacts(root)
    result.update({path: payload.encode() for path, payload in dashboard_sources().items()})
    result.update({
        CONTRACT_PATH: json_bytes(presentation_contract(root)), GUIDE_PATH: guide_source().encode(),
        EXAMPLE_PATH: example_source().encode(),
        PREPARE_INPUTS_PATH: (root / "ops/expected/PrepareInputs.js").read_bytes(),
        ".vault-bridge/protocol/request.schema.json": (root / "ops/schemas/bridge-request.schema.json").read_bytes(),
        ".vault-bridge/protocol/response.schema.json": (root / "ops/schemas/bridge-response.schema.json").read_bytes(),
        ".vault-bridge/README.md": (root / "ops/expected/VaultBridge.md").read_bytes(),
        ".obsidian-mac/snippets/knowledgeos-home.css": (root / "ops/expected/knowledgeos-home.css").read_bytes(),
    })
    for name in CLIENT_FILES:
        result[f"{CLIENT_DIRECTORY}/{name}"] = (root / "ops/clients/obsidian-thin-client" / name).read_bytes()
    for path, value in json.loads((root / "ops/config/vault-profile.json").read_bytes()).items():
        if not path.startswith((".obsidian-mac/", ".obsidian/")) or ".." in Path(path).parts or "\\" in path:
            raise ValueError("profile projection path is outside the owned profile namespace")
        result[path] = json_bytes(value)
    return result
