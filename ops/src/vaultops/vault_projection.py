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
PREPARE_INPUTS_PATH = "99_System/Scripts/QuickAdd/PrepareInputs.js"
CLIENT_DIRECTORY = ".obsidian-mac/plugins/knowledgeos-thin-client"
CLIENT_FILES = ("manifest.json", "contract.json", "main.js", "styles.css")
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
        "workflow": ["owner_work_admission", "bounded_mcp_evidence", "pending_proposal_or_artifact", "independent_assessment", "human_review", "separate_apply"],
        "control": {"owner": "KnowledgeApplication", "journal": "OwnerJournal", "approval": "digest_bound_decision_and_pending_intent", "canonical_apply": "separate_explicit_dispatch", "recovery": "observe_and_reconcile_without_dispatch"},
        "interfaces": {"mcp": capabilities, "maintenance_commands": ["operation check", "operation status", "operation recover", "index build", "index verify", "note validate"], "native_execution": "unconfigured", "resource_authority": "trusted_startup_with_Core_ResourceReference"},
        "observations": ["admission_ack", "execution_outcome", "acceptance_state", "human_approval", "freshness", "completeness"],
        "transport": "unconfigured",
        "thin_client": "staged_disabled_pending_owner_web_and_runner_adoption",
        "machine_state_owner": "Operations/KnowledgeOS/PROJECT_STATE.md",
    }


def _system_note(identifier: str, title: str, purpose: str, body: str) -> str:
    return render_frontmatter({
        "schema_version": 1, "id": identifier, "type": "system", "title": title,
        "status": "active", "created": "2026-10-04T00:00:00+09:00", "modified": "2026-10-04T00:00:00+09:00",
        "aliases": [], "tags": [], "sensitivity": "personal", "ai_policy": "deny", "ai_status": "idle", "purpose": purpose,
    }, body)


def guide_source() -> str:
    return _system_note("system-knowledgeos-guide", "KnowledgeOS", "Core 계약과 Vault 사용 경계", f"""# KnowledgeOS

[[Home]] · [[Mobile]] · [[99_System/Bases/Review.base|제안 보기]]

이 Vault는 KnowledgeOS가 관리하는 문서·템플릿·조회 화면이다. 실행 제어와 승인 기록은 Operation State의 OwnerJournal에 둔다. 이 문서와 note property는 실행 권한을 만들지 않는다.

## 계약과 설정

Core/schema {PINS["core_version"]}, Capsule {PINS["capsule_version"]}, semantic {PINS["semantic_version"]}, ResourceBinding/HostBinding API {PINS["resource_binding_api"]}을 사용한다. 정확한 digest와 공개 인터페이스는 `99_System/Schemas/KnowledgeOS_Contract.json`에 투영된다. Core는 읽기 전용이며 Blueprint registries가 note·property·relation 의미를 정의한다.

private config v3에서 Core, control, Vault, State, Runtime을 각각 선언한다. v1/v2 설정과 호환되지 않는 State는 쓰기 전에 거부한다. 자동 변환과 State/Runtime 별칭은 없다.

## 조회와 제안

- 사용자는 Obsidian Search, Bases, Backlinks, Tasks로 문서를 탐색하고 QuickAdd와 템플릿으로 작성한다.
- 에이전트는 단일 `vaultmcp` v3에서 owner가 허용한 근거를 읽고 Pending 제안 또는 비정본 Work 산출물을 제출한다. 정확한 ResourceReference와 digest를 사용하며 actor, host root 또는 Work binding을 도구 입력으로 고를 수 없다.
- Thin Client의 Work 요청은 owner Director admission에 접수되고 진행 상태를 표시한다. Pending의 원문·출처·변경안을 GUI에서 확인한 뒤 승인 또는 거절한다. 정본 적용은 별도 owner 요청이다.
- `vaultctl operation check/status/recover`, `index build/verify`, `note validate`는 운영 검사와 복구에 사용한다. `recover`는 불확실한 효과를 자동 재실행하지 않는다.
- `ai_policy: ask`는 구성된 로컬 MCP에 허용된다. `deny`와 confidential은 제외하고 `local_only` 본문은 신뢰된 로컬 실행 문맥에서만 반환한다.

## 화면과 기록의 의미

Review Base의 status는 문서 분류다. admission ACK, 실행 완료, domain acceptance, human approval과 서로 대체할 수 없다. 실행 여부를 확인할 근거가 없으면 unknown으로 유지한다. Operation 간 교환은 owner Gateway가 수락한 참조로만 조회한다.

현재 first-party Thin Client 파일은 설치 가능한 정적 자료로만 배치하고 profile에서 비활성으로 둔다. owner Web listener, Hermes Runner와 외부 전송은 구성되지 않았다. 자동 Git, shell과 background writer도 실행하지 않는다.

## 검증과 후속 실행

사용자 문서가 없어도 정상 Vault다. 예시는 hermetic test fixture에만 둔다. 운영 CLI의 검사는 admission이나 실행 권한을 만들지 않는다. 철회된 Runtime 자료는 현재 owner 참조를 확인한 뒤 별도 retirement 절차로 제거한다. Temporal Worker, Web, Discord와 Team 실행은 구성되지 않았다.
""")




def expected_projection(control: str | Path) -> dict[str, bytes]:
    """Render the declared generated Vault copy without reading its target."""
    from .vault_artifacts import expected_system_artifacts

    root = Path(control)
    result = expected_system_artifacts(root)
    result.update({path: payload.encode() for path, payload in dashboard_sources().items()})
    result.update({
        CONTRACT_PATH: json_bytes(presentation_contract(root)), GUIDE_PATH: guide_source().encode(),
        PREPARE_INPUTS_PATH: (root / "ops/expected/PrepareInputs.js").read_bytes(),
        ".obsidian-mac/snippets/knowledgeos-home.css": (root / "ops/expected/knowledgeos-home.css").read_bytes(),
    })
    for name in CLIENT_FILES:
        result[f"{CLIENT_DIRECTORY}/{name}"] = (root / "ops/clients/obsidian-thin-client" / name).read_bytes()
    for path, value in json.loads((root / "ops/config/vault-profile.json").read_bytes()).items():
        if not path.startswith((".obsidian-mac/", ".obsidian/")) or ".." in Path(path).parts or "\\" in path:
            raise ValueError("profile projection path is outside the owned profile namespace")
        result[path] = json_bytes(value)
    return result
