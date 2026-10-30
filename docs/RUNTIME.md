# Agent Runtime Contract

- Bind Operation State and host Runtime through separate config v3 roots without aliases or implicit nested layout.
- Keep durable owner control, active generation pins and frozen restart evidence in State.
- Keep reconstructable index bodies, caches, logs and host process artifacts in Runtime.
- Keep both private roots outside Git and every sync root.
- Keep Python, uv, `vaultctl`, `vaultmcp`, and dependencies in the project container image or container filesystem.
- Keep the bind-mounted runtime and disposable uv cache separate.
- Match container UID and GID to numeric host identity and fail closed on missing values.
- Keep State/Runtime directories at mode `0700`, private payload files at mode `0600`, and process umask at `077`.
- Keep Docker socket, host secrets, keychains, SSH agents, and provider networks out of the runtime container.
- Keep current owner Work, method, Graph, proposal, and effect intents bound to immutable references and current State pins.
- Replay identical effect IDs and digests idempotently; reject changed digest, malformed input, traversal, wrong owner, and unknown action.
- Fail apply when source, target, policy, or schema hashes change after approval.
- Reconcile crash state through journals and receipts without silently changing canonical Markdown.
- Preserve receipt, authorization, and recovery evidence; reset only with an explicit backup and retention gate.
- Finalize owner decisions, exact effect intents, receipts, outbox and unresolved outcomes in State owner-control.json through OwnerJournal.
- Keep owner journal, linked transaction journals, and receipts in Operation State. Fsync immutable intent before an allowed canonical effect and reconcile incomplete or uncertain outcomes against exact preimage and postcondition evidence.
- Keep repair plans digest-bound and create-only under State; require a fresh observation match before maintenance apply. MCP `proposal_recovery_plan` records a Pending plan and cannot execute repair.

## Readiness and predeployment archive retirement

The exact `archives/legacy-runtime/de85d43091639ae8f9b3d7c2` State payload was retired on 2026-10-08 after read-only reference and hash preview and a disposable rehearsal. The owner writer lock rechecked checkpoint `sha256:13a2a0723c50e1d171c5ebe9432725a88d2039749e5f66877bfbd5ce9a297bde` before removing that payload and its obsolete `storage-transition.json`; the current `storage-retirement.json` records the completed, authority-free retirement. This transition grants no Runner, model, Web or device authority. Read `PROJECT_STATE.md` for separately observed native adoption.

## Pinned runtime source review, 2026-10-09

This is a source review of the selected upstream tags, not an installed-build or model measurement. The [Hermes `v2026.9.14` release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.14) identifies its Docker image tag. Its [tagged profile guide](https://github.com/NousResearch/hermes-agent/blob/v2026.9.14/website/docs/user-guide/profiles.md) and [profile resolver](https://github.com/NousResearch/hermes-agent/blob/v2026.9.14/hermes_cli/profiles.py) place named profile homes under the default Hermes root's `profiles/<name>/`, with separate config, memory, sessions, and state. A custom `HERMES_HOME` changes the root. A profile supplies state separation, not filesystem isolation; the owner container and tool boundary must enforce access limits.

The [tagged tool configuration](https://github.com/NousResearch/hermes-agent/blob/v2026.9.14/hermes_cli/tools_config.py) resolves platform toolsets and applies `agent.disabled_toolsets` as a final suppression. The [tagged agent constructor](https://github.com/NousResearch/hermes-agent/blob/v2026.9.14/run_agent.py) accepts enabled and disabled toolsets, iteration and time budgets, and callbacks. This identifies candidate controls; it does not prove that the planned Director, three Team Managers, and EvalOfficer have owner-scoped tools or that their Runner callbacks behave correctly. The [llama.cpp `v0.6.0` release](https://github.com/ggml-org/llama.cpp/releases/tag/v0.6.0) confirms the selected upstream tag, without establishing a local binary, model compatibility, or memory fit.

The development `ops/compose.yaml` container remains network-isolated; process-loopback listeners there are not host listeners. The maintained read-only Owner topology below separately publishes only host loopback through a data-free relay. This does not compose or activate the mutable Director/Team/Temporal/model runtime. Both Qwen downloads remain separately deferred; model ports, vector generation, and native Thin Client adoption are not implied by read-only Owner startup.

A [disposable Colima probe](../../../Tmp/knowledgeos-owner-loopback-2026-10-09/REPORT.md) found that an internal-only Docker network did not publish a host loopback port, while a dedicated bridge did. A dual-network relay then forwarded synthetic authenticated JSON from host `127.0.0.1:17901` to an internal-only synthetic owner and preserved a 403 denial for a wrong token. `interfaces/owner_relay.py` implements that data-free forwarding boundary. The fixture containers and networks were removed. No real owner process, host port `17900`, Vault mount, Temporal dispatch, or model service was started by this probe. Production composition must still bind the owner to the internal network, keep the relay free of Vault and State mounts, and confirm the exact native service routes before activation.

The second disposable fixture bound the actual `loopback_owner.py` adapter to the internal Docker service hostname `owner` and reached its authenticated Lexical search, metadata intake, and Work list routes through the relay. It used synthetic domain roots and synthetic trusted Director and Team bindings. The adapter rejects an unrestricted bind host, and its default remains process loopback. The temporary owner and relay were removed; the real owner, host port `17900`, and native services remain unstarted.

The initial [read-only preflight of the current five roots](../../../Tmp/knowledgeos-local-rollout-2026-10-09/REPORT.md) opened the owner journal at revision 7 but rejected the selected Lexical generation because its stored Blueprint digest differed from the current maintained Blueprint. No Vector pointer or selected Qwen candidate file was present. That old generation was not reused as a compatibility fallback. The next paragraph records its replacement.

A second [disposable Lexical rebuild rehearsal](../../../Tmp/knowledgeos-local-rollout-2026-10-09/REPORT.md) mounted the current Core, control, and Vault read-only and published only to container tmpfs State and Runtime. The new generation passed exact current-source verification for 6 notes and 0 relations. A subsequent owner-locked maintenance container required the exact old live pointer hash, owner revision 7, and rehearsed generation ID before updating only the selected State pointer and Runtime generation. The new pointer SHA-256 is `4947f6fc81906e33641a126d8a77e424ba84c28b9cdb3d676d79c66e32d13352`. A read-only five-root preflight then passed both Lexical index checks but remained `NO_GO` for full rollout because Vector and both deferred model files are absent. No Owner listener or native search result was observed.

- Reject v1/v2 config and incompatible State before any new boundary writes; perform no automatic conversion.
- Use exact previous bytes, atomic replacement, fsync and generation/coordination fences for owner writers.
- Retain the latest 100 contracted detailed history records, expire unstarted work after 12 hours and preserve minimum dedupe receipts for 12 hours; keep unresolved identity/digest/fence records and required active evidence.
- Recover by observing journal and actual artifacts without automatic dispatch or unknown-effect retry.
- Remove withdrawn predeployment payloads instead of retaining them as current KnowledgeOS evidence. First produce an exact path and hash inventory, then check owner journal references, current index and selection pointers, transition records, active Work, receipts and recovery code. A transition record that still names an archive must be retired or replaced by a current-state checkpoint before deleting that archive; deleting only its payload would break replay and status checks.
- Prove the retirement on a disposable copy with the same reference graph. Then take the owner writer lock, verify the inventory has not changed, write the retirement intent and current-state checkpoint, remove only the selected archived payload and its obsolete metadata, fsync the parent, and run owner status, recovery and index verification. If any reference or in-flight effect remains, stop with an explicit blocker. Never import an archived approval, token, job or generation into the current owner State.
- Use `make source-check`, `make verify`, `make blueprint-check`, `make schema-check`, `make core-readiness-check`, and `make acceptance` with the proven project image and disposable roots. Distinguish the performed owner State retirement and sidebar layout check from unrun Hermes Runner, owner-backed Thin Client behavior, external Gateway, and model adoption.

## Read-only Owner entrypoint

`python -m vaultops.interfaces.read_only_owner --actor-id <trusted-login> --token-file <private-file>` composes existing config-v3 resolution, `KnowledgeApplication`, and `KnowledgeExOps(read_only=True)`. Direct invocation defaults to process `127.0.0.1:17900`. Startup requires five existing independent roots, a compatible initialized owner journal, current Core/State pins and a source-verified Lexical generation. It never creates directories, initializes State, repairs pins, rebuilds an index, or binds a Director, Team, Worker, model or Temporal client. A stale generation requires separately authorized maintenance, not a startup fallback.

Authenticated POST JSON routes are exactly `search/query` (Lexical only), `owner/status`, `work/list`, `work/read`, and `work/result`. Search returns source references, exact chunk locators and chunk hashes alongside excerpts. Work routes only inspect existing records; an empty State legitimately has no Work. Every other route, including guessed submission, approval, preparation, apply, worker and effect names, is denied before parsing its payload or calling an admission callback. Unknown future routes stay denied by the allowlist. Explicit mutable callers retain the existing adapter behavior. Trusted actor identity is selected at startup; request bodies cannot supply or replace it.

`ops/compose.owner-read-only.yaml` attaches Owner only to an internal network, publishes no Owner port, and mounts Core/control/Vault/State/Runtime read-only. The relay has no domain or session mounts and publishes only `127.0.0.1:${KNOWLEDGEOS_OWNER_PORT:-17900}` through a separate bridge. Neither service has a Docker socket, provider binding or model/Temporal dependency. Use a unique `COMPOSE_PROJECT_NAME` for disposable runs; do not expose the relay on a public interface.

### Image and private session input

`make image-build` is the explicit network-enabled image restoration step: it pulls the digest-pinned Python 3.12.8 base and builds the locked uv 0.8.14 dependency environment into `agentfabric-knowledgeos-dev:py3.12.8-uv0.8.14`. The package includes static Core adoption pins and the MCP capability manifest and schemas, not private roots or owner State. Build then runs `make image-smoke`: installed `vaultctl` and read-only Owner help execute in read-only, network-free containers without source mounts. The installed MCP smoke uses checked-in fixture inputs, disposable roots and an isolated-Python subprocess to exercise retrieval, exact citation and unbound Work denial through the installed `vaultmcp` entrypoint; mounted candidate source cannot supply that subprocess's implementation or schemas. This catches package inputs that source-bound tests cannot prove. `make -s image-id` resolves an existing local image to its exact ID and fails if absent. Runtime Compose uses `pull_policy: never`; startup never downloads models, images or dependencies.

Create a fresh random `secrets.token_urlsafe(32)` token in a separate owner-owned mode-0700 session directory, with the file named `token` and mode 0600. The entrypoint rejects symlinks, nonregular files, wrong ownership and broad permissions; its session token is read once into memory. Mount only that private directory into Owner read-only. Never put the token value in shell arguments, environment, Compose YAML, logs, URLs, or copied curl commands. Clients read the same private file into memory and send `X-KnowledgeOS-CSRF`; the relay does not retain it. Protect the local operator account and Docker daemon as trusted startup authorities. Rotate by stopping Owner, replacing the private file, then restarting; changing the file alone does not change a running session. Remove disposable session files after stopping containers.

`make owner-read-only-up` requires exact five-root source selections, `KNOWLEDGEOS_OWNER_SESSION_SOURCE`, and `KNOWLEDGEOS_OWNER_ACTOR_ID`; `owner-read-only-down` stops that composition without building or requiring a present image. No existing user config or plugin configuration needs modification.

### Standalone disposable HTTP smoke

These are explicit runtime checks, not pytest. Run from the KnowledgeOS control checkout with the local image already restored. Fixture preparation copies maintained Core/control inputs and the checked-in C09 Vault fixture only; it never mounts the real Vault, State or Runtime. The one writable preparation mount is a fresh disposable directory.

```sh
export KNOWLEDGEOS_UID="$(id -u)" KNOWLEDGEOS_GID="$(id -g)"
export KNOWLEDGEOS_DEV_IMAGE="$(make -s image-id)"
export FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/knowledgeos-owner-readonly.XXXXXX")"
export CONTROL="$(pwd)"
export CORE="$(cd ../../Core && pwd)"
export COMPOSE_PROJECT_NAME="knowledgeos-readonly-smoke-$$"
docker run --rm --pull=never --network none \
  --user "$KNOWLEDGEOS_UID:$KNOWLEDGEOS_GID" \
  --mount "type=bind,src=$CORE,dst=/workspace/Core,readonly" \
  --mount "type=bind,src=$CONTROL,dst=/workspace/control,readonly" \
  --mount "type=bind,src=$FIXTURE,dst=/fixtures" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTHONPATH=/workspace/control/ops/src:/workspace/control/ops/tests \
  "$KNOWLEDGEOS_DEV_IMAGE" \
  python /workspace/control/ops/tests/support/read_only_owner_smoke.py prepare /fixtures

export KNOWLEDGEOS_CORE_SOURCE="$FIXTURE/core"
export KNOWLEDGEOS_CONTROL_SOURCE="$FIXTURE/control"
export KNOWLEDGEOS_VAULT_SOURCE="$FIXTURE/vault"
export KNOWLEDGEOS_STATE_SOURCE="$FIXTURE/state"
export KNOWLEDGEOS_RUNTIME_SOURCE="$FIXTURE/runtime"
export KNOWLEDGEOS_OWNER_SESSION_SOURCE="$FIXTURE/session"
export KNOWLEDGEOS_OWNER_ACTOR_ID=owner.fixture
export KNOWLEDGEOS_OWNER_PORT=17901
make owner-read-only-up
python3 ops/tests/support/read_only_owner_smoke.py check "$FIXTURE" \
  --url http://127.0.0.1:17901
make owner-read-only-down
```

The smoke client verifies the fixture's exact source reference, chunk locator/hash and excerpt through the actual relay; wrong-token denial; all current effect route families plus guessed unknown mutations; payload identity rejection; read-only Work/status; and unchanged Vault/State/Runtime file sets and SHA-256 bytes. It deliberately changes one disposable source, requires stale-search refusal without an Owner write, then restores it and verifies the original snapshot. It prints no token. Always run `make owner-read-only-down` after failure before inspecting or removing the exact disposable directory. Keep runtime success/failure evidence in `PROJECT_STATE.md`, not this guide; these commands are instructions, not a claim that a check ran.

Focused regression command: `make test PYTEST_ARGS="tests/test_read_only_owner.py tests/test_obsidian_sidebar_owner.py tests/test_exops_temporal.py tests/test_owner_relay.py"`, then the normal `make lint`/acceptance sequence only when assigned to verify.

## Canonical executable paths

`vaultctl` and `vaultmcp` resolve directly to `vaultops.interfaces.cli:main` and `vaultops.interfaces.mcp_server:main`. Python module launchers are `python -m vaultops` for maintenance and `python -m vaultops.interfaces.mcp_server --control-root <absolute-control-root>` for MCP. The application owner is `KnowledgeApplication`; no historical `KnowledgeServices` or module-forwarding layer is required.

The withdrawn `scheduled-worker` Make target has no supported host launcher. Daily Stewardship is an owner API, not an installed scheduler: native adoption must bind due checks, admission, scoped execution, independent assessment and candidate recording. Do not restore the old `ai worker` path as a substitute.

## Disposable native Officer execution

`application/normalization_work.py` composes one bounded NormalizationOfficer WorkSpec and its exact source-bound executor. The caller supplies the owner-approved Officer/EvalOfficer binding and separately admits that WorkSpec and Capture through ExOps. The executor admits one immutable one-task Graph, invokes the existing deterministic normalization proposal effect, and registers only its exact Pending artifact for result export. A separate deterministic EvalOfficer reopens the source, proposal, effect receipt and human gate before assessing `criterion.proposal`. An unsupported criterion or changed evidence cannot be reported complete.

The default Officer path does not call a model or use a Team Manager. The explicit model-bound mode below requires a separately pinned host Runner; it is never a fallback for a failed deterministic run. Neither path approves Pending, applies canonical Markdown, or implements a native LangGraph or Team runtime. The Graph contract describes the bounded orchestration, not deterministic model output. Native Temporal provides the execution path; owner State retains admission, proposal effects and the independent assessment.

The optional `temporal` dependency pins the selected SDK without adding it to read-only MCP installations. `make officer-image-build` builds a distinct Officer image with `KNOWLEDGEOS_RUNTIME_EXTRA=temporal`; the default image stays SDK-free. After pulling the exact Temporal Server/Admin Tools 1.31.3 and PostgreSQL 16.14 images pinned in `ops/compose.officer-smoke.yaml`, run `make officer-smoke`. Set `KNOWLEDGEOS_OFFICER_EVIDENCE` to a new JSON path when a specific evidence destination is needed; existing evidence must not be overwritten.

The smoke uses maintained control/Core inputs and synthetic Capture data, a unique internal-only Temporal/PostgreSQL stack, container-temporary State/Runtime, and actual authenticated loopback Owner HTTP result reads. It checks native start history and the completed Activity, one independent assessment, exactly one Pending artifact, unchanged existing Vault files, rejected authentication/actor injection, and teardown. No host port is published. It must not mount the real Vault, owner State, Runtime, provider credentials or an Obsidian profile. Its pass covers this one bounded backend path, not service restart, backup/restore, uncertain-effect recovery or production adoption.

Computer Use and native Obsidian checks are a user-run boundary. A disposable backend pass does not establish the running plugin version, note-switch behavior, authenticated sidebar results, human decision/apply behavior, Vector/Hybrid quality or production Team execution. Report those checks separately; do not open or operate the app as part of this smoke.

## Exact native model Officer binding

The model mode pins the native runtime kind/version/revision, provider/model and actual host adapter digest in the Officer binding and admitted WorkSpec. After owner Graph admission, the executor issues one exact Work/Profile/Graph/generation/fence/source request with source chunk locators but no source text. Only the trusted host transport selects the container, control root and installed Hermes interpreter. Model arguments cannot replace that context.

`ops/clients/hermes/native_officer.py` runs a fresh native Hermes process with only `work_context_read`, `knowledge_read` and `proposal_create` from the bound `vaultmcp`. Every call still crosses the owner session's scope checks. The native runtime uses a disposable Hermes home, disables general host tools/context/memory/background behavior, and reads the user's selected provider authentication without copying credentials into Owner or changing profile configuration. Missing or expired authentication, a changed route or adapter, and unavailable runtime inputs fail closed; no provider fallback or automatic effect retry is authorized.

Model output is execution evidence, not completion or effect authority. The owner requires matching request/binding/route and successful scoped tool evidence, then reopens its actual completed proposal receipt and current source. The separate deterministic EvalOfficer assesses the exact Pending bytes and human gate; the producing model cannot certify its own completion. An external-provider binding must not export `local_only`, denied or confidential source text.

Build the selected Officer image with `make officer-image-build`, then use `make model-officer-smoke`. The host selections are `KNOWLEDGEOS_HERMES_SOURCE`, `KNOWLEDGEOS_HERMES_PROFILE_HOME` and `KNOWLEDGEOS_HERMES_PYTHON`; defaults select the installed local build, and a different installation requires explicit matching selections. `KNOWLEDGEOS_MODEL_EVIDENCE` optionally selects a new JSON destination. The smoke keeps the same internal-only disposable Temporal stack and synthetic roots; a narrow stdio rendezvous lets host Hermes call scoped MCP through `docker exec` without mounting a Docker socket, credentials or provider networking into Owner. It checks a source-only random code, wrong-source/tool denials, native execution, independent assessment and exact Owner HTTP export before teardown.

This command makes real calls to the already configured provider using synthetic text. It does not activate a production listener, Team, Cron, embedding service or Obsidian profile, and it does not perform Computer Use. Keep provider, runtime, owner effect, assessment, HTTP, preservation and device evidence separate.
