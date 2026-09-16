# KnowledgeOS

KnowledgeOS는 개인 지식을 안전하게 포착하고, 검토하고, 연결하고, 다시 찾아보기 위한 개인용 Obsidian 작업공간입니다. 단순한 메모 저장소가 아니라, 포착한 생각을 검토 가능한 지식과 실행 가능한 프로젝트로 바꾸는 작은 운영체제를 목표로 합니다.

## 이 프로젝트가 하는 일

자료와 생각은 다음 흐름을 거칩니다.

1. 휴대폰이나 Mac에서 메모·음성·URL·첨부 자료를 포착합니다.
2. 포착물은 원본을 보존한 채 Inbox에 쌓이고, 필요하면 AI 검토 대기열로 보냅니다.
3. 사람이 분류·연결·수정 제안을 확인하고 승인합니다.
4. 승인된 내용만 정식 Markdown 노트와 YAML 속성으로 확정됩니다.
5. 확정된 노트에서 Home 화면, Bases, 검색 색인 같은 파생 화면을 다시 만듭니다.
6. 나중에 검색하거나 질문할 때는 원본 노트와 근거 링크를 따라 답을 확인합니다.

AI는 정식 노트를 대신 결정하지 않습니다. AI가 만든 분류·요약·링크·수정은 제안으로만 남고, 사람의 승인이 있어야 정본에 반영됩니다.

## 현재 위치

핵심 capability lane은 `C01`부터 `C24`까지 완료되었습니다. 기존 경계에는 Colima/Docker 실행 계약과 Blueprint·schema·semantic gate(`C01`–`C06`), template·bootstrap·Base·dashboard·portable Vault fixture(`C07`–`C09`), offline bridge·mobile outbox·provider-free diagnostics·create-only local command(`C10`–`C13`), asset·capture finalize·project archive transaction(`C14`), journal·replay(`C15`), reconcile·repair·receipt 검증(`C16`), exact local bridge/Git publish(`C17`)가 포함됩니다. `C18`은 read-only deterministic triage proposal contract, `C19`는 provider-free proposal review·approval·rejection·apply closure, `C20`은 proposal action facade route와 PRD traceability를 닫았습니다. `C21`은 검증된 Vault 노트와 relation edge를 deterministic JSONL generation으로 투영하고 atomic current pointer를 발행하며, `C22`는 한 개의 검증된 C21 generation 위에서 read-only lexical retrieval과 bounded typed-link retrieval을 수행하고 frozen evaluation baseline을 검증합니다. `C23`은 같은 검증 generation 위에서 hash-bound capture 입력을 확인하고 deterministic cited answer를 반환하는 provider-free guestbook-horror 흐름을 닫았습니다. `C24`는 deterministic background artifact, one-shot worker, replay·recovery 관찰과 inactive LaunchAgent preview를 닫았습니다.

현재 1차 목표는 모바일 확장을 제외한 **MacBook 중심 Obsidian 사용 경험**입니다. 포착·제안·사람의 검토와 승인·정본 반영·Git 적용·검색·인용 답변까지의 provider-free 흐름과 `C24`의 선택적 core reliability가 구현·검증되었습니다. C24 worker는 명시적으로 실행할 수 있지만 provider 호출, Vault mutation, Git network I/O는 하지 않으며, LaunchAgent 설치·활성화는 `E03`로 분리되어 기본 비활성입니다. Mac 배치 오버레이는 Mac 프로필과 QuickAdd, Templater, Tasks, Linter, Obsidian Git을 포함하는 `D07`까지 완료로 승격했습니다. `D08`과 `D09`는 모바일 전용이므로 이번 목표에서 제외하고, `D10`은 Codex provider 연동을 별도로 원할 때만 검토합니다. `E01`은 C22 baseline에 대한 provider-free local vector/RRF 평가 overlay로 구현되었지만 기본 retrieval은 여전히 C22 lexical 경로입니다. `E02`부터 `E05`까지는 선택 확장이며 MacBook 기본 사용 경험에 필수인 과제는 없습니다.

현재 상태 기록에는 Mac 프로필의 Home workspace, Properties와 Workspaces core plugin, 다섯 개 Mac community plugin의 설치·audit·기능 확인이 pass로 남아 있습니다. 또한 `C22`의 retrieval policy, lexical·typed-link candidate, stale/policy-digest fail-closed, CLI, frozen evaluation 및 canonical gate, `C23`의 cited answer·hash-bound capture·guestbook-horror evaluation, `C24`의 synthetic wake·recovery observation 및 canonical gate가 pass로 기록되어 있습니다. 최신 단계와 실제 검증 결과는 [`PROJECT_STATE.md`](PROJECT_STATE.md)에서 확인합니다. E01 vector/RRF는 opt-in evaluation overlay로 사용할 수 있지만 기본 retrieval을 바꾸지 않으며, LaunchAgent activation·remote/unattended 실행·thin client·provider 연동은 기본 완료선과 별도의 승인 경계에 남아 있습니다.

Blueprint 검증, generated artifact zero-diff, strict note engine, 16개 template, portable Vault fixture, Base/dashboard, offline bridge·recovery outbox, remote identity sentinel, GitHub directory visibility 기반은 이미 마련되어 있습니다. 무엇이 실제로 완료되었는지와 어떤 검증이 미실행인지에 대한 최신 기록은 [`PROJECT_STATE.md`](PROJECT_STATE.md)에서 확인합니다.

## C01–C24 Cookbook

이 절은 현재 구현되어 있고 canonical container에서 검증된 기능만 설명합니다. 모든 명령은 프로젝트 root에서 실행합니다. Compose는 host UID/GID를 요구하므로, 임의의 `docker compose` 호출을 복사하기 전에 다음 runner를 한 번 정의합니다.

```bash
export KNOWLEDGEOS_UID="$(id -u)"
export KNOWLEDGEOS_GID="$(id -g)"

ko() {
  docker compose -f ops/compose.yaml run --rm dev vaultctl "$@"
}
```

컨테이너 내부의 canonical 경로는 control root `/workspace/control`, 독립 Vault `/workspace/KnowledgeHub`, runtime `/workspace/runtime`입니다. `ko`가 실행되는 작업 디렉터리는 `/workspace/control/ops`이므로 fixture 파일은 `/workspace/control/ops/tests/fixtures/...`로 지정합니다. Make target은 위 UID/GID를 자동으로 전달하므로 전체 검증에는 `make`를 우선 사용합니다.

### 1. 시작 전 점검과 계약 검증

다음 순서로 현재 상태와 계약을 점검합니다. `make test`와 `make lint`는 disposable uv cache 경쟁을 피하기 위해 순서대로 실행합니다.

```bash
# source trust anchor and repository foundation
make source-check
make verify

# Blueprint structural/semantic validation and generated artifact ownership
make blueprint-check
make schema-check
make contract-check

# same foundation checks inside the canonical container
make container-source-check
make container-verify

# regression and static quality
make test
make lint
```

읽기 전용 runtime·Git·Mac profile 진단은 다음과 같습니다.

```bash
ko doctor --root /workspace/control
ko git status --repo both --root /workspace/control
ko plugins audit --profile mac --root /workspace/control
ko foundation source-check --root /workspace/control
ko foundation check --root /workspace/control
ko blueprint status
ko blueprint validate --root /workspace/control
ko schema export --check --root /workspace/control
```

`doctor`, `git status`, `plugins audit`, `foundation`, `blueprint validate`, `schema export --check`는 정본 Vault를 변경하지 않는 진단입니다. 실패하면 결과의 `status`, `errors`, `provider_called`, `mutation_performed`를 먼저 확인하고 다음 명령으로 진행하지 않습니다.

### 2. additive bootstrap과 Vault identity

`bootstrap`은 없는 canonical directory와 C07 template을 추가하는 명령입니다. 기존 파일을 정리하거나 삭제하지 않습니다. 처음에는 dry-run으로 계획을 확인합니다.

```bash
ko bootstrap --dry-run --root /workspace/control
ko bootstrap --root /workspace/control
```

Vault identity와 root sentinel은 exact remote, branch, Vault UUID, canonical name을 확인한 뒤에만 생성합니다. 값이 확실하지 않으면 dry-run에 머뭅니다.

```bash
ko configure --dry-run --root /workspace/control

# 실제 값으로 대체한 뒤, 명시적으로 boundary를 확인할 때만 실행
ko configure \
  --remote https://github.com/OWNER/REPOSITORY.git \
  --branch main \
  --vault-uuid VAULT_UUID \
  --canonical-vault-name KnowledgeHub \
  --confirm-sensitive-data-boundary \
  --root /workspace/control
```

이 명령은 local sentinel과 identity binding을 다루는 것이며, remote fetch/pull/push를 대신하지 않습니다. 원격 저장소나 민감 자료 경계를 확인하지 않은 채 placeholder 값을 사용하지 않습니다.

### 3. capture, journal, project, note

#### Capture

Capture는 unique path에 create-only로 기록합니다. text 입력은 stdin으로 전달하고 `--device`를 반드시 지정합니다.

```bash
# 계획만 확인
printf '새로운 생각\n' | ko capture text \
  --stdin --device mac --title "새로운 생각" --dry-run \
  --root /workspace/control

# 계획을 확인한 뒤 실제 capture를 생성할 때 --dry-run을 제거
printf '새로운 생각\n' | ko capture text \
  --stdin --device mac --title "새로운 생각" \
  --root /workspace/control
```

URL capture는 URL을 파일로 전달할 수 있고, 기존 파일을 덮어쓰지 않습니다.

```bash
ko capture url \
  --url-file /workspace/control/path/to/url.txt \
  --comment-file /workspace/control/path/to/comment.txt \
  --dry-run --root /workspace/control
```

#### Period, project, typed note

```bash
ko period create --kind daily --date 2026-09-17 --dry-run --root /workspace/control
ko period create --kind weekly --date 2026-09-17 --dry-run --root /workspace/control
ko period create --kind monthly --date 2026-09-17 --dry-run --root /workspace/control

ko project create \
  --title "Guestbook Horror" \
  --status planned \
  --priority medium \
  --next-action "첫 번째 플레이 루프를 정리" \
  --dry-run --root /workspace/control

printf '아이디어 본문\n' | ko note create \
  --type idea --title "새 아이디어" --body-stdin \
  --dry-run --root /workspace/control

ko note validate 40_Knowledge/Ideas/새-아이디어.md --root /workspace/control
ko fmt --check --path 40_Knowledge/Ideas/새-아이디어.md --root /workspace/control
```

`note validate`는 strict note registry, frontmatter, path, title, type, link direction을 검사합니다. `fmt --check`는 검사만 하며, guarded formatting을 실제로 수행할 때만 `--check`를 제거합니다. 정본 변경은 항상 사람이 target path와 결과를 확인한 뒤 실행합니다.

### 4. asset와 hash-bound transaction

Asset은 regular file만 허용하며 destination directory와 source SHA-256을 명시적으로 확인할 수 있습니다. container가 볼 수 있는 source는 control root 또는 Vault mount 아래에 둡니다.

```bash
# host에서 먼저 실제 파일 digest를 확인
shasum -a 256 /absolute/path/to/source.pdf

# container path로 매핑해 계획 확인
ko asset import \
  --source /workspace/control/path/to/source.pdf \
  --target-directory Documents \
  --expected-sha256 LOWERCASE_64_HEX_SHA256 \
  --dry-run --root /workspace/control
```

Capture finalize와 project archive는 source hash가 현재 bytes와 일치할 때만 진행합니다. journal과 receipt는 runtime에 남고, 동일 job·digest replay는 NO_OP으로 수렴합니다.

```bash
# Vault-relative path와 host에서 계산한 digest를 사용
shasum -a 256 KnowledgeHub/00_Inbox/Captures/YYYY/MM/CAPTURE.md
ko capture finalize \
  --path 00_Inbox/Captures/YYYY/MM/CAPTURE.md \
  --expected-sha256 LOWERCASE_64_HEX_SHA256 \
  --outcome triaged \
  --dry-run --root /workspace/control

# complete project bundle의 모든 파일에 --hash PATH=SHA256를 반복
ko project archive \
  --project 20_Projects/Guestbook-Horror \
  --hash 20_Projects/Guestbook-Horror/Guestbook-Horror.md=PROJECT_MD_SHA256 \
  --archive-year 2026 \
  --dry-run --root /workspace/control
```

Crash나 interrupted transaction이 의심되면 먼저 read-only reconcile을 실행합니다. repair plan 생성은 runtime create-only이고, apply는 fresh digest match가 확인된 경우에만 별도로 실행합니다.

```bash
ko reconcile --root /workspace/control
ko repair plan --root /workspace/control
ko receipts verify --root /workspace/control

# 사람이 plan의 source/target/policy/schema digest를 검토한 뒤에만
ko repair apply --plan /workspace/runtime/repair/PLAN.json --root /workspace/control
```

### 5. offline bridge와 proposal review

Bridge transport는 committed request와 exact response path를 사용합니다. ingest와 status는 Vault를 바꾸지 않으며, publish는 지정한 response와 필요할 때 proposal만 exact path로 local commit합니다.

```bash
ko bridge status --root /workspace/control
ko bridge ingest --job-id JOB_UUID --root /workspace/control

# response file을 먼저 schema 검증한 뒤 exact path로 publish
ko bridge publish \
  --response-file /workspace/control/path/to/response.json \
  --proposal-file /workspace/control/path/to/proposal.json \
  --root /workspace/control
```

`C18`–`C20`의 AI 명령은 provider-free proposal boundary입니다. triage와 facade route는 제안·dispatch plan만 만들고 정본을 바꾸지 않습니다. approval은 digest-bound artifact를 만들며, 실제 canonical mutation은 사람이 승인한 `apply`에서만 수행합니다.

```bash
# source bytes의 SHA-256을 먼저 계산한 뒤 read-only triage
shasum -a 256 KnowledgeHub/00_Inbox/Captures/YYYY/MM/CAPTURE.md
ko ai triage \
  --source 00_Inbox/Captures/YYYY/MM/CAPTURE.md \
  --expected-sha256 LOWERCASE_64_HEX_SHA256 \
  --root /workspace/control

# pending proposal 확인 -> approval 생성 -> reject 또는 apply 선택
ko ai review --root /workspace/control
ko ai approve \
  --proposal /workspace/runtime/review/PROPOSAL.json \
  --expected-sha256 PROPOSAL_SHA256 \
  --root /workspace/control
ko ai reject \
  --proposal /workspace/runtime/review/PROPOSAL.json \
  --expected-sha256 PROPOSAL_SHA256 \
  --reason "정본 반영 보류" \
  --root /workspace/control
ko ai apply \
  --proposal /workspace/runtime/review/PROPOSAL.json \
  --approval /workspace/runtime/approved/APPROVAL.json \
  --root /workspace/control
```

다음 여섯 facade route는 현재 등록된 provider-free read-only dispatch plan입니다. 이 route 자체가 LLM 호출이나 Vault mutation을 의미하지 않습니다.

```bash
ko ai organize --root /workspace/control
ko ai summarize --root /workspace/control
ko ai relate --root /workspace/control
ko ai extract --root /workspace/control
ko ai inbox --root /workspace/control
ko ai project-summary --root /workspace/control
```

### 6. projection, index, lexical retrieval

`C21` projection은 Vault 원본을 immutable JSONL generation으로 만들고 runtime의 atomic current pointer를 갱신합니다. `C22`와 `C23`은 source freshness와 digest가 검증된 generation만 읽습니다.

```bash
ko export jsonl --root /workspace/control
ko index build --root /workspace/control
ko index verify --root /workspace/control
```

`search`는 lexical candidate만 반환하고, `retrieve`는 typed-link allowlist 안에서 최대 hop/node/edge/candidate cap으로 확장합니다. query body를 argv의 `--query`로 넘기는 방식은 지원하지 않습니다. stdin 또는 regular file을 사용합니다.

```bash
# lexical retrieval; default hops=0
printf '확정되지 않은 정보\n' | ko search \
  --query-stdin --root /workspace/control

# lexical retrieval plus bounded typed-link expansion
printf '플레이 루프\n' | ko retrieve \
  --query-stdin --scope project:guestbook-horror --hops 1 \
  --root /workspace/control

# checked-in C22 frozen baseline; fixture paths are container-absolute
ko retrieve \
  --evaluation-file /workspace/control/ops/tests/fixtures/c22_retrieval/evaluation.yaml \
  --root /workspace/control
```

검색 후보에는 query·policy·generation·content·chunk·retrieval-config digest와 graph path가 함께 기록됩니다. `search`, `retrieve`, `index verify`는 provider를 호출하지 않으며 C22 retrieval은 Vault와 runtime bytes를 query 실행 중 변경하지 않습니다.

### 7. cited answer와 optional vector/RRF

`C23 ask`는 질문 또는 hash-bound capture를 읽어 deterministic extractive answer와 citation digest를 반환합니다. capture를 source로 사용할 때는 source bytes의 lowercase SHA-256을 반드시 함께 전달합니다.

```bash
printf '이 프로젝트를 다시 방문할 이유는 무엇인가?\n' | ko ask \
  --question-stdin --scope project:guestbook-horror --hops 1 \
  --root /workspace/control

# answer one capture; CAPTURE_SHA256은 실제 capture bytes의 digest로 교체
ko ask \
  --source 00_Inbox/Captures/YYYY/MM/CAPTURE.md \
  --expected-sha256 CAPTURE_SHA256 \
  --scope project:guestbook-horror \
  --root /workspace/control

# checked-in C23 cited-answer baseline
ko ask \
  --evaluation-file /workspace/control/ops/tests/fixtures/c23_answers/evaluation.yaml \
  --root /workspace/control
```

`E01` vector/RRF는 명시적으로 opt-in할 때만 실행합니다. 기본 `search`/`retrieve`는 C22 lexical 경로이며 embedding provider나 remote service를 사용하지 않습니다.

```bash
# optional E01 frozen evaluation
ko vector evaluate \
  --evaluation-file /workspace/control/ops/tests/fixtures/e01_vectors/evaluation.yaml \
  --root /workspace/control

# optional vector/RRF retrieval
printf '플레이 루프\n' | ko vector retrieve \
  --query-stdin --scope project:guestbook-horror --hops 1 \
  --root /workspace/control
```

### 8. C24 worker와 LaunchAgent 경계

`C24` worker는 one-shot wake, committed bridge request ingest, runtime queue manifest, local transaction recovery observation을 실행할 수 있습니다. provider 호출, Vault mutation, Git network I/O는 하지 않습니다. 먼저 evaluation 또는 dry-run을 사용합니다.

```bash
# synthetic C24 baseline
ko ai worker \
  --evaluation-file /workspace/control/ops/tests/fixtures/c24_background/evaluation.yaml \
  --root /workspace/control

# request/recovery를 관찰하지만 queue manifest를 만들지 않음
ko ai worker --once --dry-run --root /workspace/control

# 명시적으로 one-shot worker를 실행; runtime만 변경될 수 있음
ko ai worker --once --root /workspace/control

# E03 경계의 inactive LaunchAgent installation preview
ko launchd install --dry-run --root /workspace/control
```

LaunchAgent는 `RunAtLoad=false`, 기본 inactive이며 `E03`의 별도 authorization 없이는 설치·활성화하지 않습니다. `ko launchd install`은 C24에서 activation을 허용하지 않으므로 preview 결과를 확인하는 용도로만 사용합니다.

### 9. C 단계별 검증 결과

2026-09-17 현재 canonical container의 `make test`는 206개 테스트를 모두 통과했고 `make lint`도 통과했습니다. 아래는 각 C 단계의 대표 구현 surface와 직접 대응하는 검증 파일입니다.

| 단계 | 완료된 capability | 대표 검증 |
| --- | --- | --- |
| C01 | Colima/Docker 실행·UID/GID·toolchain harness | `test_toolchain_contract.py`, `test_foundation.py` |
| C02 | Blueprint Draft 2020-12 JSON Schema | `test_blueprint.py`, `make blueprint-check` |
| C03 | registry/path/action/command/bridge semantic gate | `test_blueprint.py`, `make blueprint-check` |
| C04 | Base/dashboard/projection/transaction semantics | `test_blueprint.py`, `test_vault_structure.py` |
| C05 | generated-artifact ownership and zero-diff | `test_schema_export.py`, `make schema-check` |
| C06 | operational policy, strict note schema, note engine | `test_note_engine.py`, `test_schema_export.py` |
| C07 | additive templates/bootstrap/create-only project bundle | `test_c07_templates.py` |
| C08 | Bases, Home, Mobile, deterministic dashboards | `test_c08_dashboard.py` |
| C09 | portable Vault fixture and plugin-free fallback boundary | `test_c09_portable_fixture.py` |
| C10 | offline bridge request/response/root-sentinel schemas | `test_c10_bridge_contract.py` |
| C11 | offline shortcut, durable outbox, recovery contract | `test_c11_mobile_offline.py` |
| C12 | provider-free read-only diagnostics and CLI integration | `test_c12_diagnostics.py` |
| C13 | create-only local commands and guarded formatting | `test_c13_commands.py` |
| C14 | hash-bound asset, capture-finalize, project-archive transaction | `test_c14_transactions.py` |
| C15 | fsynced hash-chained journal and idempotent replay | `test_c15_recovery.py` |
| C16 | reconcile, digest-bound repair, apply, receipt verification | `test_c16_reconcile.py` |
| C17 | local bridge ingest, exact Git publish, crash recovery | `test_c17_bridge_publish.py` |
| C18 | deterministic read-only triage proposal contract | `test_c18_triage.py` |
| C19 | review, approval, rejection, guarded apply closure | `test_c19_proposals.py` |
| C20 | action registry, six facade routes, PRD traceability | `test_c20_pipeline_registry.py` |
| C21 | JSONL projection, immutable generations, atomic pointer | `test_c21_projection.py` |
| C22 | lexical and bounded typed-link retrieval, frozen baseline | `test_c22_retrieval.py` |
| C23 | cited answer, hash-bound capture, guestbook-horror flow | `test_c23_answer.py` |
| C24 | background artifacts, one-shot wake, replay/recovery observation | `test_c24_background.py` |

이 표의 “완료”는 단순히 파일이 존재한다는 뜻이 아니라, 해당 단계의 source·contract·test와 전체 canonical gate가 현재 checkout에서 함께 통과했다는 뜻입니다. 실제 외부 service, mobile device, remote provider, LaunchAgent activation은 별도 evidence이며 C lane 완료로 합산하지 않습니다.

## Vault를 이해하는 방법

`KnowledgeHub/`가 Obsidian에서 여는 실제 Vault입니다. 폴더는 정보의 수명과 관리 맥락을 나타내고, 노트의 `type`과 링크는 그 노트가 하는 일과 다른 노트와의 관계를 나타냅니다.

- `00_Inbox/Captures/`: 아직 정리하지 않은 포착물
- `01_AI_Review/`: AI 또는 사람이 검토 중인 제안과 보류 항목
- `20_Projects/`: 끝내야 할 결과를 가진 프로젝트. 각 프로젝트는 관련 파일을 함께 갖는 묶음입니다.
- `30_Areas/`: 계속 관리해야 하는 생활·업무 영역
- `40_Knowledge/`: 장기 보존할 주장과 설명
- `50_Maps/`: 사람이 큐레이션한 탐색 경로와 지도
- `60_Meetings/`: 회의의 의제·결정·후속 작업
- `80_Assets/`: 검증된 원본 파일과 관련 자료
- `90_Archive/`: 끝난 맥락을 보존하는 장소
- `99_System/`: 템플릿, 속성 사전, Bases, 대시보드 등 시스템 파일

Home과 Mobile은 오늘의 핵심·다음 행동·빠른 이동을 보여주는 화면입니다. 검색 색인과 대시보드는 언제든 Markdown에서 다시 만들 수 있는 보조 산출물이지, 원본을 대신하지 않습니다.

## 기기별 역할

| 기기 | 가장 잘하는 일 |
| --- | --- |
| iPhone | 생각·음성·URL을 빠르게 포착하고 오늘의 내용을 확인하기 |
| iPad | 읽기, 주석, 짧은 보완, 가벼운 검토 |
| Mac | 최종 분류·승인·구조 변경·Git 충돌 해결·자동화 실행 |

휴대기기에서 짧게 끝낼 수 없는 구조 변경이나 충돌 해결은 Mac 검토로 넘깁니다. 모바일 Git 사용은 Working Copy Pro, 별도 worktree, Vault 식별 확인, 왕복 검증을 모두 통과한 뒤에만 허용합니다.

## 안전 원칙

- Markdown 본문과 평평한 YAML 속성이 지식의 정본입니다.
- 한 파일을 동시에 쓰는 주체는 하나뿐이며, 기존 파일 수정은 해시 확인과 명시적 승인에 묶입니다.
- AI·plugin·외부 서비스는 선택 사항이며, 정본을 몰래 바꾸거나 비밀을 저장하지 않습니다.
- 사람·회의·기밀 자료는 AI 기본 거부 또는 별도 Vault 경계를 적용합니다.
- iCloud, Obsidian Sync, Dropbox, OneDrive를 canonical 모바일 경로와 병렬 writer로 사용하지 않습니다.
- Control 저장소와 Vault 저장소의 Git 기록은 독립적으로 보존합니다.

## 저장소 경계

| 위치 | 용도 |
| --- | --- |
| 프로젝트 루트 | 정책, 설계 계약, 실행 코드, 테스트를 보관하는 control repository |
| `KnowledgeHub/` | Obsidian 노트와 bridge transport를 보관하는 독립 Vault repository |
| `runtime/` | queue, lock, receipt, journal, index, cache, log를 보관하는 Git 비추적 로컬 상태 |

세 영역은 서로 자동으로 합쳐지지 않습니다. 원격 저장소, plugin, provider, 백그라운드 worker, 기존 자료 이관은 별도의 검토와 승인이 필요한 선택 기능입니다.

## 문서 안내

- [`OBSIDIAN_VAULT_BLUEPRINT.md`](OBSIDIAN_VAULT_BLUEPRINT.md): 에이전트가 읽는 상위 제품·구조·파이프라인 계약 색인
- [`OBSIDIAN_VAULT_WHITEPAPER.md`](OBSIDIAN_VAULT_WHITEPAPER.md): 에이전트가 읽는 저수준 실행·트랜잭션·검증 부속서 색인
- [`AGENTS.md`](AGENTS.md): 이 프로젝트에서 작업할 때 지켜야 할 짧은 실행 계약
- [`docs/`](docs/): architecture, runtime, operations, mobile, decisions, source contract의 간결한 참조 문서
- [`PROJECT_STATE.md`](PROJECT_STATE.md): 현재 단계, 실제 검증 결과, blocker, 다음 작업을 기록하는 에이전트용 상태 문서

Blueprint와 Whitepaper는 사람을 위한 긴 설명서가 아니라, 구조화된 계약을 빠르게 찾기 위한 인덱스입니다. 사용자에게 필요한 배경과 사용법은 이 README에 남깁니다.

## 확인 명령

프로젝트의 기본 구조와 계약을 확인할 때는 다음 명령을 사용합니다.

```bash
make source-check
make verify
make blueprint-check
make schema-check
make contract-check
make container-source-check
make container-verify
make test
make lint
```

각 명령은 서로 다른 범위를 검사합니다. 구조·의미·생성 산출물·실행·장치·배포 결과를 하나의 성공으로 합치지 않으며, 실제 실행 여부는 `PROJECT_STATE.md`에 따로 기록합니다.

## 다음에 작업을 시작할 때

먼저 `PROJECT_STATE.md`에서 현재 목표와 증거를 확인하고, 한 번에 하나의 작은 작업 단위를 끝까지 수행합니다. 설계 문서에 적힌 미래 기능을 이미 구현된 것으로 간주하지 말고, Vault 파일·소스·테스트·실행 결과를 함께 확인합니다.
