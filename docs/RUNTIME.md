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

## Source adoption and retained owner State

The maintained source selects Core/schema 0.19.0 through `ops/config/core-adoption.json`. The user-authorized local activation applied the exact retained v4 State transition to the same pins, preserving its 0.18.1 byte preimage and terminal records. Current live evidence and selected bindings are in `PROJECT_STATE.md`; source promotion alone still never performs migration, fallback, service activation or profile retargeting. Ordinary composition rejects any independently selected incompatible State.

Core 0.19.0 defines cancellation/aborted Experience only for a safely applied owner CANCEL whose existing human decision, control receipt and exact WorkRun evidence validate. Process death, native Activity cancellation and unknown effects are not that event. Historical Core 0.18.1 runtime proofs retain their original meanings; only explicitly identified Core 0.19.0 proofs below establish their bounded new-source cases, never a transition of retained live State.

`adapters/core_cutover.py:core_cutover` and `vaultctl storage core-cutover` support the exact retained state-version-4 Core 0.18.1 envelope as a source, not arbitrary historical or foreign State. Its frozen source Core/schema pins and unchanged non-Core pins, owner/Fabric/instance identity, private file mode/ownership, collection shapes and exact lowercase 64-hex SHA-256 preimage must match. Active/unresolved effects, retained WorkSpec/WorkRun envelopes, exchange/Experience state and a nonzero Experience cursor require separate review and are refused. Before preview or apply, `CoreContracts.verify()` validates the **selected target Core root** and adoption binding, including frozen manifest digest/input bytes, catalog versions, supported combination and semantic digest; source-envelope recognition alone is insufficient.

From the selected installed maintenance environment, bind `CONTROL_ROOT` to the exact control root and `OWNER_STATE_SHA256` to the lowercase 64-hex hash of the selected `owner-control.json` bytes. The read-only command shape is:

```sh
vaultctl storage core-cutover --root "$CONTROL_ROOT" \
  --expected-sha256 "$OWNER_STATE_SHA256"
```

Preview creates no writer lock or archive and does not initialize or modify State. Adding `--apply` is an explicit mutation requiring owner authorization. Apply takes the existing exclusive nonblocking owner writer lock, repeats source/target/preimage validation, preserves the exact prior bytes create-only at `State/archives/core-cutover/<source-sha256>` with private mode and fsync, then atomically replaces only the journal envelope using exact-byte comparison. The v4 envelope remains v4; pins become the selected target pins, observation time changes, and generation and revision each increment by one, while retained terminal records keep their content and historical meanings. Old generation-bound writer selections are fenced; an archive is preservation evidence, never imported runtime authority. This is not general active-Work migration, native-history recovery or permission to bypass a failed preview.

## Readiness and predeployment archive retirement

The exact `archives/legacy-runtime/de85d43091639ae8f9b3d7c2` State payload was retired on 2026-10-08 after read-only reference and hash preview and a disposable rehearsal. The owner writer lock rechecked checkpoint `sha256:13a2a0723c50e1d171c5ebe9432725a88d2039749e5f66877bfbd5ce9a297bde` before removing that payload and its obsolete `storage-transition.json`; the current `storage-retirement.json` records the completed, authority-free retirement. This transition grants no Runner, model, Web or device authority. Read `PROJECT_STATE.md` for separately observed native adoption.

## Pinned runtime source review, 2026-10-09

This is a source review of the selected upstream tags, not an installed-build or model measurement. The [Hermes `v2026.9.14` release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.14) identifies its Docker image tag. Its [tagged profile guide](https://github.com/NousResearch/hermes-agent/blob/v2026.9.14/website/docs/user-guide/profiles.md) and [profile resolver](https://github.com/NousResearch/hermes-agent/blob/v2026.9.14/hermes_cli/profiles.py) place named profile homes under the default Hermes root's `profiles/<name>/`, with separate config, memory, sessions, and state. A custom `HERMES_HOME` changes the root. A profile supplies state separation, not filesystem isolation; the owner container and tool boundary must enforce access limits.

The [tagged tool configuration](https://github.com/NousResearch/hermes-agent/blob/v2026.9.14/hermes_cli/tools_config.py) resolves platform toolsets and applies `agent.disabled_toolsets` as a final suppression. The [tagged agent constructor](https://github.com/NousResearch/hermes-agent/blob/v2026.9.14/run_agent.py) accepts enabled and disabled toolsets, iteration and time budgets, and callbacks. This upstream source review alone establishes no current owner-scoped Director, Manager, specialist or evaluator binding; select those from the maintained method/Team contracts and explicit owner composition. The [llama.cpp `v0.6.0` release](https://github.com/ggml-org/llama.cpp/releases/tag/v0.6.0) confirms the selected upstream tag, without establishing a local binary, model compatibility or memory fit.

The development `ops/compose.yaml` container remains network-isolated; process-loopback listeners there are not host listeners. The maintained read-only Owner topology below separately publishes only host loopback through a data-free relay. It does not activate the mutable Director/Team/Temporal/model runtime. The separate policy-selected writable composition below is an explicit source capability, not an implication of read-only startup. Model provisioning, a matching Vector generation and native Thin Client adoption require their own selected bindings and authorization.

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
- When assigned to verify, use `make source-check`, `make verify`, `make blueprint-check`, `make schema-check`, `make core-readiness-check` and `make acceptance` with the selected matched project image and disposable roots. Keep source checks, historical State retirement, bounded native execution, external-service activation and owner-backed device behavior as separate evidence classes; `PROJECT_STATE.md` owns their current results.

## Read-only Owner entrypoint

`python -m vaultops.interfaces.read_only_owner --actor-id <local-owner>` composes config-v3 resolution, `KnowledgeApplication`, and `KnowledgeExOps(read_only=True)`. Direct invocation defaults to `127.0.0.1:17900`. Startup requires five existing roots and compatible journal/Core pins. It materializes current documents in memory without requiring a previously published index, initializing State, repairing pins or binding a Director, Worker, model or Temporal client.

Local POST JSON routes permit Lexical `search/query` and existing Work/status inspection, including `owner/status`, `work/list`, `work/read`, `work/inspection` and `work/result`. The explicit read-only route allowlist rejects mutation before parsing its payload or invoking admission. Source references and chunk hashes remain exact; the local actor is selected at startup, never by request bodies. No login, session, password or HTTP token is required.

`ops/compose.owner-read-only.yaml` attaches Owner only to an internal network, publishes no Owner port, and mounts all five roots read-only. The mount-free relay publishes only `127.0.0.1:${KNOWLEDGEOS_OWNER_PORT:-17900}`. Host/Origin and browser fetch-site checks, JSON-only POST and no cross-origin permission quietly reject foreign web pages and DNS-rebinding hosts. Requests without Origin remain usable by trusted local programs. The local account and Docker daemon are trusted; this is not a multi-user or remotely exposed service.

### Capability-specific activation prerequisites

Treat source-mounted and installed execution as separate runtime artifacts. `ops/compose.owner-read-only.yaml` sets the Owner's `PYTHONPATH` to mounted control source, while its relay imports the installed package; `ops/compose.owner-writable.yaml` instead selects the installed Owner package and mounts control as configuration input. An image tag alone does not establish package identity. Before separately authorizing startup, select the exact image ID and compare the installed modules used by each process with intended source; restore or build a matched image only under separate authorization. Team and Officer extras are separate images, and neither implies a running Temporal service or Worker.

Read-only Lexical use requires the five roots, config v3, compatible journal/Core pins, a startup actor and the selected local topology. It does not require a token, manual Projection publication, Qwen, Director or Worker. Non-Lexical modes and mutation remain unavailable in this composition.

Vector/Hybrid require the selected trusted local embedding port and its exact model, retrieval policy, parser and indexer. Live readers materialize new or changed document chunks through that port and reuse unchanged vectors; neither a matching whole-Vault snapshot nor manual publication is an access gate. Process-local caches reconstruct after restart. Lexical does not depend on embeddings.

Writable Work needs a separately authorized owner composition with live actor authorization, exact Director/source admission, Team/Officer and independent evaluator registrations, current-binding callbacks, Worker identity and selected Temporal service/namespace/queue. `application/owner_runtime.py` supplies the private policy-selected composition for its concrete registered methods; the bounded Officer, Curation and steering compositions retain their own contracts. Neither is a generic executor for catalog entries. Native model work additionally requires the exact Runner version, revision, adapter digest, committed interpreter, provider/model route, private profile and credentials without exporting secrets. Source-disclosure policy remains authoritative; source implementation does not establish production activation.

Before any writable deployment, select a coordinated backup/restore procedure for Vault, owner State (journals, unresolved effects, receipts, pins and artifacts), selected Temporal/PostgreSQL history, reconstructable Runtime generations and exact package/runtime pins. Copying State alone cannot restore active runs. The quiescent native domain restore section below supplies a disposable retained-persistence restart and coordinated snapshot rehearsal, not an activated production backup. Preserve single-writer fencing, rebind only verified history, and preserve unknown outcomes rather than retrying effects. In-flight effects/control, completion-report loss, arbitrary hard-loss windows, disk/host loss and off-host durability still require separate evidence.

### Selected local Ollama embedding binding

`adapters/ollama_embedding.py` supplies `OllamaEmbeddingPort` and the library composition `compose_ollama_application`, injecting the port through `DomainHandlers` into existing `KnowledgeApplication` SearchModes. Maintained writable serving now selects the same port explicitly with `--local-embedding ollama-qwen3-stdio` on the host driver, which forwards that selection to the installed Owner. Without the selection, Vector/Hybrid remain unavailable; Lexical and authorized approved-edge Ontology do not depend on embeddings. The separate read-only entrypoint remains Lexical-only. Neither selection grants model installation, public CLI/MCP provider control or Work authority.

The selected already-installed model is `qwen3-embedding:8b-q4_K_M`, dimension 4096. The `ModelPin` artifact digest is the Ollama **manifest** SHA-256 `64b933495768fbd3b87c20583d379728a07471e0c66733a9df87cd1901b3c44b`; the separately checked GGUF blob digest reported by the service is `3fcd3febec8b3fd64435204db75bf0dd73b91e8d0661e0331acfe7e7c3120b85`. These are different identities, not interchangeable hashes; the adapter does not read or hash the model blob. Queries use the English `Instruct: Given a user query, retrieve relevant passages from the knowledge base that answer the query` followed by `Query:{text}`; documents use raw `{text}`, following the [Qwen model card](https://huggingface.co/Qwen/Qwen3-Embedding-8B/raw/main/README.md). Each `/api/embed` request sets `truncate: false`, so oversized input fails instead of silently truncating; the adapter bounds and checks finite output. Ollama advertises a 40960-token context, while the model card describes 32k: do not infer an independently verified effective context from either number.

The selected binding multiplexes allowlisted `/api/tags`, `/api/show` and `/api/embed` requests over the existing ordered, bounded stdio pipe. The host driver alone reaches fixed `http://127.0.0.1:11434`; container loopback is not treated as host loopback. It opens no embedding TCP listener, exposes no gateway alias or credentials, uses no host networking, proxy or redirects, and performs no download, service reconfiguration, generic model dispatch or fallback. The port checks exact model identity on pin access and before and after embedding; these observations do not provide an atomic defense against a malicious or transiently swapped trusted service. Queueing shares the native pipe and remains subject to its finite deadlines.

Normal reads materialize documents and eligible local vectors on demand without publishing State pointers or rewriting notes. Explicit host-driver `--publish-index` remains an optional maintenance export requiring its selected embedding binding and private `publish_index` action. It does not confer HTTP search or Work admission authority.

Maintenance publication creates immutable generation bodies and pointers for inspection. Its two-pointer protocol remains non-atomic and failures may leave derived Runtime artifacts; it never rolls back under revoked authority or retries a domain effect. Ordinary document reads do not require the published Lexical/Vector snapshots to match current Vault contents.

From the control checkout, this is the maintained command shape for an already authorized selected environment, not authorization to run against real roots:

```sh
python3 ops/src/vaultops/interfaces/native_owner_driver.py \
  --policy-file "$KNOWLEDGEOS_OWNER_POLICY_SOURCE/policy.json" \
  --compose-file ops/compose.owner-writable.yaml \
  --project "$OWNER_PROJECT" --container-name "$KNOWLEDGEOS_OWNER_CONTAINER" \
  --adapters-directory ops/clients/hermes \
  --hermes-python "$KNOWLEDGEOS_HERMES_PYTHON" \
  --hermes-root "$KNOWLEDGEOS_HERMES_SOURCE" \
  --profile-home "$KNOWLEDGEOS_HERMES_PROFILE_HOME" \
  --local-embedding ollama-qwen3-stdio --publish-index
```

Select the five roots, matched Team image, UID/GID, owner policy directory (`KNOWLEDGEOS_OWNER_POLICY_SOURCE`, mounted at `/run/owner-policy`), project/container identity, adapter and internal Temporal network. Publication starts no Temporal client, though Compose still requires that network. Replace `--publish-index` with `--admission-only` for retrieval without Workers, dispatch supervision or the daily loop; serving still connects selected Temporal clients. Normal writable execution remains separately selected. These examples do not authorize real-data Work.

The installed-path smoke publishes and reads Lexical, selected-model Vector/Hybrid and approved-edge Ontology over disposable roots, without Work or a Hermes/Codex invocation:

```sh
KNOWLEDGEOS_TEAM_IMAGE="$SELECTED_TEAM_IMAGE" \
  python3 ops/tests/support/owner_embedding_smoke.py /absolute/new-owner-evidence.json
```

It uses the existing host Qwen service and disposable Temporal services, exercising unbound/unpublished refusal, owner permission and pin/source/policy drift, private-file preservation and teardown. It mounts no real domain roots or profiles. The separate `ollama_embedding_smoke.py` remains a source-mounted measurement fixture. Historical measurements do not guarantee current latency, capacity, quality or model residency; current results belong in `PROJECT_STATE.md`.

### Image and local startup inputs

`make image-build` is the explicit network-enabled image restoration step: it pulls the digest-pinned Python 3.12.8 base and builds the locked uv 0.8.14 dependency environment into `agentfabric-knowledgeos-dev:py3.12.8-uv0.8.14`. The package includes static Core adoption pins and the MCP capability manifest and schemas, not private roots or owner State. Build then runs `make image-smoke`: installed `vaultctl` and read-only Owner help execute in read-only, network-free containers without source mounts. The installed MCP smoke uses checked-in fixture inputs, disposable roots and an isolated-Python subprocess to exercise retrieval, exact citation and unbound Work denial through the installed `vaultmcp` entrypoint; mounted candidate source cannot supply that subprocess's implementation or schemas. This catches package inputs that source-bound tests cannot prove. `make -s image-id` resolves an existing local image to its exact ID and fails if absent. Runtime Compose uses `pull_policy: never`; startup never downloads models, images or dependencies.

HTTP has no credential generation, token file, token header, session expiry or rotation procedure. Protect the trusted local account and Docker daemon. Writable owner policy is still a private startup input defining domain actions and exact execution bindings, not an HTTP login credential.

`make owner-read-only-up` requires the five roots and `KNOWLEDGEOS_OWNER_ACTOR_ID`, with no session mount. `owner-read-only-down` stops the composition without building. Writable serving additionally selects `KNOWLEDGEOS_OWNER_POLICY_SOURCE`; never expose either relay beyond host loopback.

### On-demand macOS local backend

`ops/host/local_backend.py` is a standard-library host adapter, not an Owner, domain State writer, service installer or generic process supervisor. Its fixed per-user installation is `~/.local/bin/knowledgeos-local`. The installed shim calls `main(binding_path=...)` with an existing private Runtime binding; the public CLI accepts only `start --json` and `status --json`. No caller-selected command, environment, roots, policy or binding path is accepted. Do not use synced plugin settings as execution configuration.

The desktop Thin Client explicitly invokes that executable with `execFile`, `shell:false`, a 65-second invocation bound and 64-KiB output bound. It first reads helper status and compares the returned `owner_endpoint` with its current endpoint before any start. Opening the sidebar, changing panels and refreshing Owner status never start services. A different disposable endpoint must not start the personal Owner.

The existing host Runtime owns `local-owner.plist`, a mode-0600 `local-backend.json` binding and its reconstructable `local-backend.lock`, under a private mode-0700 directory. The selected plist is outside login LaunchAgents directories with `RunAtLoad=false` and `KeepAlive=false`. The helper registers it in the current user's GUI launchd domain only when needed and starts a stopped job without `kickstart -k`. Neither the first-party installer nor the UI installs login/reboot registration. Configuration evidence is not a reboot/login observation.

The version-1 binding selects these exact existing installation inputs:

| Binding field | Meaning |
|---|---|
| `version`, `uid`, `label` | Version 1, current local UID and the selected KnowledgeOS launchd label |
| `plist`, `plist_sha256` | Private existing non-autostart plist and exact bytes |
| `compose_sha256` | Exact maintained writable Compose selected by the plist's driver arguments |
| `docker`, `project`, `owner_container`, `owner_image`, `port` | Existing Docker executable, logical Owner identity, verified image and loopback port |
| `relay: {name, image}` | Logical relay and verified image, not a physical container ID; the existing driver may legitimately recreate it |
| `temporal_network: {name, id}` | Existing selected private host network |
| `temporal: [{name, id, image, project, service, health}]` | Existing PostgreSQL, Temporal Server and SDK client, ordered by dependency; no implicit provisioning |

Start is serialized across helper processes. An existing selected healthy Owner is reused, not replaced; a running but inconsistent/unavailable job is reported rather than forcibly restarted. Physical Owner/relay containers are reconstructable under the selected driver's lifecycle; shared Temporal IDs are retained. The adapter verifies selected resource identity, not merely an open port. It may start only the selected existing stopped Temporal containers, then waits within one 60-second monotonic startup deadline. Missing services/configuration require separate provisioning. It does not call `Services/temporal/start.sh`, build/pull images, initialize DB schemas or State, publish indexes, invoke models or admit Work. Starting the Owner retains the existing driver's narrowly bound Compose/stdio behavior.

launchd can expose the selected driver's positive PID before its display state becomes `running`. Preserve exact job arguments/environment and container/image/mount checks, but use that PID to fence a second kickstart while independently observing Owner HTTP readiness. An interrupted connection before a status response is transport-unavailable within the same deadline, not an explicit Owner refusal. HTTP 403, malformed/non-JSON payloads, foreign identities and port-policy failures remain refusals; no domain mutation is retried.

`status` is read-only and never registers jobs, creates locks or starts services. Both commands return bounded version-1 JSON with `operation`, `state`, `owner_endpoint`, `owner_http`, `runtime_observation` and `error`. `owner_connected` means a real selected Owner status response, not live Worker/model readiness. `starting`, `unreachable`, `blocked` and `failed` retain their distinct meanings; a 403, relay-only 502, port conflict or deadline must not be relabeled as a successful start. Failed/unknown start acknowledgements require positive state observation, not a blind restart.

The helper returns independently of the driver, whose stdio bridge remains owned by launchd. Closing the sidebar, timing out a helper call or quitting Obsidian does not terminate the backend, cancel Work or stop shared services. There is no automatic idle shutdown or UI stop/restart/repair. Starting or refreshing never reconciles an uncertain mutation; preserve its original IDs and read the existing Owner receipts. Approval and canonical apply remain separate.

Installation changes require preserving the previous shim/plist/binding and selected plugin settings, confirming a quiescent Owner without unresolved effects, and replacing only approved first-party files. Preserve real note bytes, owner records and existing policy. Do not replace a busy writer, migrate pins, silently broaden permissions or write a shim into the Vault. Source/installed/headless/helper-HTTP evidence is distinct from the deferred native desktop checks in `docs/OBSIDIAN_COMPUTER_USE_PROMPT.md` N37–N44.


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
export KNOWLEDGEOS_OWNER_ACTOR_ID=owner.fixture
export KNOWLEDGEOS_OWNER_PORT=17901
make owner-read-only-up
python3 ops/tests/support/read_only_owner_smoke.py check "$FIXTURE" \
  --url http://127.0.0.1:17901
make owner-read-only-down
```

The smoke checks exact source/chunk identity through the actual relay without credentials, read-only mutation refusal, payload identity rejection and unchanged fixture file hashes. It changes one disposable source, requires stale-search refusal, then restores it. Always stop the exact disposable Compose project after failure. These commands are instructions; observed results belong in `PROJECT_STATE.md`.

Focused regression command: `make test PYTEST_ARGS="tests/test_read_only_owner.py tests/test_obsidian_sidebar_owner.py tests/test_exops_temporal.py tests/test_owner_relay.py"`, then the normal `make lint`/acceptance sequence only when assigned to verify.

## Policy-selected writable Owner composition

`application/owner_runtime.py:WritableOwnerRuntime` and `owner_authority.py` are the trusted writable composition, separate from `read_only_owner.py` and the callback-only HTTP adapter. The authoritative method/role inputs remain `ops/config/work-methods.json` and `ops/config/team-catalog.json`; the catalog's `definitions_only` mode is not runtime authority. `concrete_method()` selects existing metadata/daily, research, authoring and boundary executors and rejects unsupported methods instead of falling back to Curation. The `*_temporal.py` / `*_workflow.py` modules provide method-specific native routes; `native_team.py` / `native_team_temporal.py` share bounded Manager, two-stage and independent-assessment machinery without erasing method/source/effect checks.

`interfaces/writable_owner.py serve` composes installed local HTTP, selected Workers and dispatch supervision. `native_owner_driver.py` supplies the bounded stdio bridge to exact native adapters, interpreter/profile and private policy. The pipe may also carry explicitly selected embedding requests, never provider credentials. Unknown model/process outcomes are not permission to repeat a call. Unsupported catalog methods remain unavailable.

The private policy must be an existing locally owned regular mode-0600 file. It selects actor/actions, Director enablement, Worker identity, exact compatible method registrations (`team_binding`, `runner_binding`, `admission_scope`), templates, optional freeform research and daily enablement. Explicit index publication additionally requires its separate `publish_index` action; it does not broaden these Work grants. Startup requires five existing roots, a compatible initialized journal and source-verified Lexical projection; it neither initializes nor migrates retained State. Each selected registration must match the actual native Runner and implementation digest. Ordinary Core 0.19.0 owner startup still rejects untouched live Core 0.18.1 State; only the separately authorized maintenance cutover lane above can change that envelope.

Trusted admission retains the exact Director WorkSpec, initial WorkRun, source, actor, expiry, request and original authority registration. HTTP cannot replace those records or supply its own authority. Templates bind the two metadata Work admissions or document-enhancement research; optional freeform admission is bounded research only. Other selected methods use `WritableOwnerRuntime.admit_selected()` with owner-pinned scope/parameters, not arbitrary public Work/Graph input. Cross-process scoped tools and final proposal writers reread the retained policy selection through `validate_owner_authority()`, including current actor, method/source scope, Team/Runner and installed authority/native digests. A newly selected registration does not grandfather an older Work grant.

`DispatchSupervisor` drains bounded pending owner intents through their exact method-specific routes. Unknown starts are observation/reconciliation only; absence or mismatch cannot authorize a second start. Normal serving explicitly constructs the selected Worker and supervisor; `--admission-only` starts neither and installs no daily loop. `ops/compose.owner-writable.yaml` uses an installed verified Team image without pulling, internal Owner/Temporal networks and a mount-free relay publishing host loopback only. The host selects an existing internal network providing `server:7233`, namespace `knowledgeos`, queue `knowledgeos.work.v1`, currently without TLS. Select and assess that trust boundary rather than inferring it from a queue or image name.

The writable Owner binds `owner-http`, a network alias present **only** on `owner-private`; its relay explicitly selects `--upstream-host owner-http`. Do not bind the writable HTTP server through the shared `owner` service alias: Docker also registers that alias on the Temporal network, so the Owner can choose a different interface from its relay. The single-network read-only composition still uses `owner`. No unrestricted Owner listener or additional host port is needed. Rebuilding images is required after changing these installed entrypoints; changing mounted source alone does not replace them.

### Optional owner daily loop

`daily_scheduler.py:DailyAdmissionScheduler` supplies resumable bounded due admission and completed-result consumption through existing `DailyStewardship`, not a new Cron/provider authority. The writable entrypoint binds it only when the selected private policy enables the registered daily method and serving is not admission-only. The local-day loop preserves deterministic source/method/registry/policy identities across reconstruction; unknown admission, dispatch or consumption is held for exact positive owner evidence, not blindly repeated.

Before recording a consumption marker, the scheduler validates the completed source-bound Work artifact under its retained original Director grant. Both final `DailyStewardship.record_candidate()` and `record_no_evidence()` writers repeat that validation under the owner writer lock, in addition to current daily authorization and source/registry/policy checks. A fresh daily registration or broader current source scope cannot authorize an older retained Work under different original authority. Only validated empty fields may be provisionally written with retained review delta and `ai/pending`; ordinary Pending approval/apply and daily confirmation/rejection retain their separate owner contracts.

Activation selects matched installed code, Core/State pins, real-data policy, private model profiles, provider access, Temporal and backup/recovery boundaries. There is no HTTP session prerequisite. External Gateway/Discord/ingress, Cron and Obsidian device actions remain separately authorized; no optional loop activates them.

## Local Web work console

`interfaces/web_console/{index.html,app.js,style.css}` supplies the Korean ExOps console. `create_loopback_owner` returns only an unstarted server. Exact GET routes serve static assets; all data access uses JSON POST. Opening the console automatically reads local Owner status without token entry. Host/Origin/JSON checks protect the browser boundary; startup actor, current action policy, exact revisions and read-only restrictions protect domain actions.

`ops/compose.web-console-smoke.yaml` keeps the disposable Owner internal and publishes only its mount-free relay on host loopback. Start Owner with `run --use-aliases`, then relay in the same unique Compose project; no stdin token is needed. `KNOWLEDGEOS_WEB_PORT` defaults to `17902`. Wait for actual relay readiness.

The console uses owner-backed search to select an exact note path and SHA-256, Director intake or structured templates, Work criteria/Profiles/pins/lineage/gaps and independently evaluated result exports. Pending approval/rejection and canonical apply remain separate actions. Daily review and revision-bound control require their actual owner bindings; absent bindings and unobserved Worker/Workflow health stay visibly unavailable. Command receipt is not application acknowledgement. Old observations are marked stale, expired action views must be refreshed, and uncertain mutations are not silently retried.

Result evidence preserves its producer-defined shape: actual research citations and complete metadata/Exchange/Maintenance JSON, not invented citations. `truth_evaluated=false` stays false; proposal results do not gain apply authority. `verifyStructuredWorkResult` compares rendered evidence, artifact text and assessment with an independent local HTTP read of a completed result, without creating Work.

The exact `knowledgeos.capture_steering_team` method uses its currently bound `SafeCurationSteering.export` for journal-backed report bytes, formal result reference and independent assessment under fresh owner/source/binding/effect-receipt authorization. Common `work/result` preserves that JSON report and structured source/result/criterion evidence with `truth_evaluated=false` and `apply_allowed=false`; it does not fabricate MCP `submitted_artifacts` registration. Other methods retain their existing artifact exporters. This is a method-bound source dispatch contract, not general apply, truth or native Obsidian authority; exercised browser and regression results belong in `PROJECT_STATE.md`.

Clearing the view aborts pending reads and removes private DOM and selectors. Each Work loader retains its initial view epoch and checks it after awaited reads, so late responses cannot repopulate a cleared view or start a later result request. This is view lifecycle handling, not a login session.

Disposable browser evidence references, not production activation authority:

- [Director/Pending browser exercise](../../../Tmp/knowledgeos-web-console-2026-10-31/director-pending-browser.json): one policy-eligible synthetic knowledge note was found through real Owner search and selected by exact scope/digest. Director returned `accepted` / `pending` / `not_started`; criteria and Profile limits were displayed without claiming execution. The real Pending source/diff was reviewed, approval preserved source bytes, and a separate confirmed apply returned `canonical_target_changed=true`, changed the source digest and invalidated the prior scope.
- [In-flight disconnect exercise](../../../Tmp/knowledgeos-web-console-2026-10-31/disconnect-browser.json): `work/read` had rendered private data before a fixture-only 20-second hold of the actual inspection handler. The browser disconnected before release; the same inspection completed with HTTP `200`, with result-request count `0` before and after. A DOM observer found no transient private reappearance, all private fields/selectors/token input were cleared, and no result request appeared through loader completion and drained Owner stdout at shutdown. Token cleanup and zero remaining disposable containers, networks and volumes were recorded. This is after-fix evidence only; no before-fix runtime reproduction is claimed.

`ops/tests/support/web_console_smoke.py --inspection-delay-seconds <0..20>` enables that delay only in disposable fixture code; production routes have no delay or test control endpoint. `web_console_browser_smoke.js` exposes separate begin/disconnect/finish DOM stages. Pair them with actual `WEB_CONSOLE_INSPECTION_HELD` and matching `WEB_CONSOLE_INSPECTION_COMPLETED` stdout (`http_status=200`), and reconcile real result-request counters through loader completion. The DOM helper does not infer network evidence. Failed fetch interception, module-cache probes or timing predicates are instrumentation failures, not proof of application privacy behavior.

These browser exercises do not establish native Work dispatch/execution, every decision/control path, production session behavior, Tailscale ingress, real Vault adoption or Obsidian/device integration. Keep installed-package identity, source regression, actual native controller execution and external activation evidence separate; `PROJECT_STATE.md` remains the progress and adoption authority.

## Native lifecycle Experience sensors

`application/native_lifecycle.py:scan_native_lifecycle(application)` is the resumable owner scanner called by the existing authorized `KnowledgeApplication.recover()` boundary. An explicitly composed future owner supervisor can call the same function; importing it does not install a scheduler or start a Worker. It scans committed native records, not caller-supplied outcome labels or an ephemeral final-node callback, and passes source events through the existing `KnowledgeExperience.observe()` / `scan()` path.

The Curation Workflow retains an SDK-derived failure or timeout marker for the exact native Run/Activity and Work attempt. The scanner validates its committed Work/revision/evidence before deriving an event. A native Activity failure is not proof that its external effect failed: an `outcome_unknown` effect remains unknown. Historical identity-only failure markers and unknown receipts alone are insufficient. Recovery requires the actual owner-admitted, applied recovery intent and matching committed Work result/evidence, not a requested recovery or a successful-looking receipt in isolation. Stable logical identities and committed source-event dedupe make reconstruction a no-op without changing historical native evidence or retrying producers/effects.

Core 0.19.0 additionally permits `cancellation` / `aborted`, with `not_evaluated`, only after the scanner validates the existing human decision, safely applied control receipt, exact WorkRun/revisions and quiescent cancellation evidence. It retains an ordinary evidence `ArtifactReference`; there is no cancellation-only proof record or competing snapshot protocol. Core 0.18.1 has no cancellation category: that retained State cannot adopt this meaning without a separately authorized Core/schema/owner adoption transition. Cancellation must not be relabeled as failure or human correction.

Classification, redaction, evidence availability and the existing 100-source-event/outbox capacity limits remain those of `application/experience.py`. Native journal evidence is not a public artifact exporter: unavailable/private references are not turned into downloadable candidate evidence. A candidate is not an evaluated lesson, skill, truth claim or proof of HermesTrace delivery/review. Routine progress, ordinary completion and a passing assessment are not unusual success. These sensors do not invent human-correction, policy-denial or verified-unusual-success events when no committed source establishes those meanings.

Maintained disposable commands from the KnowledgeOS checkout:

```sh
make native-lifecycle-smoke NATIVE_LIFECYCLE_EVIDENCE=/absolute/new-lifecycle.json
make team-control-smoke KNOWLEDGEOS_TEAM_CONTROL_CASE=cancel KNOWLEDGEOS_TEAM_CONTROL_EVIDENCE=/absolute/new-cancel.json
```

The [Core 0.19.0 lifecycle proof](../../../Tmp/knowledgeos-native-lifecycle-2026-10-31/core019-run.json) uses actual native Activity failure and owner-admitted recovery through `KnowledgeApplication.recover()`: one matching Experience per committed source, reconstructed dedupe with no new events, one effect invocation, zero provider calls, unchanged historical evidence, 42-event fresh replay and complete teardown. The effect was unknown at failure; it was not mislabeled as confirmed effect failure. This is not a timeout-timing, real-model, generic hard-loss or all-method lifecycle proof.

The separate [Core 0.19.0 cancellation proof](../../../Tmp/knowledgeos-cancellation-core-2026-10-31/native-cancel-first.json) records one `cancellation` / `aborted` source and candidate, sourced dedupe after reconstruction, unchanged historical control evidence and unavailable journal export. Unknown effects were not relabeled. Its fresh SDK replay consumes 40 events with zero owner writes; canonical apply and provider execution are absent. It proves the bounded owner-applied CANCEL source, not arbitrary process/Activity cancellation, in-flight control, general human correction or full lifecycle adoption.

## Owner attention notices

`application/owner_attention.py:OwnerAttention` owns a separate durable notice journal, not Experience learning, Work truth or human decisions. `interfaces/attention_scanner.py:scan_committed_owner()` reconciles committed owner records; the maintained one-pass host entrypoint is `python -m vaultops.interfaces.attention_scanner --binding host_module:compose`. The trusted host factory receives the selected owner journal and must supply its current export/route authorization and `adapters/discord_attention.py:DiscordAttentionBinding`. That binding calls the host-owned `Services/clients/discord.py:DiscordWebhookClient.send_once`; credentials and webhook URLs stay in the host binding, not request fields or owner records. No Activity or final Work result depends on a running notice scanner.

The source derives bounded notice kinds for human wait, blocker, error/recovery, independently evaluated outcome and cancelled outcome from committed records, never caller labels or routine progress. Content is a fixed redacted summary and an admitted console deep link. Notice identity/dedupe, expiry and destination/audience generations are durable; current source/export and route authority are rechecked before dispatch and at the host send boundary. An attempt becomes `unknown` before crossing HTTP. Durable rate limits survive reconstruction, and unknown responses are not blindly resent: explicit authorized reconciliation must prove the exact attempt and delivery evidence. Transport outage, delivery acknowledgement, opening a link or reading a notice cannot complete Work, approve a human gate, authorize apply or produce Experience.

`make attention-smoke` exercises the real host transport against disposable loopback HTTP. The [Core 0.19.0 attention proof](../../../Tmp/knowledgeos-owner-attention-2026-10-31/core019/completion.json) records actual owner Work, two confirmed messages for human wait and evaluated outcome, three HTTP attempts, dedupe/reconstruction, current export revocation, destination/audience fencing, persisted rate limiting and unknown-response reconciliation without changing the Work outcome during outage. Its roots were removed. This is not Discord delivery, Tailscale/mobile reachability, human-read acknowledgement, every notice kind or production activation.

## Canonical executable paths

`vaultctl` and `vaultmcp` resolve directly to `vaultops.interfaces.cli:main` and `vaultops.interfaces.mcp_server:main`. Python module launchers are `python -m vaultops` for maintenance and `python -m vaultops.interfaces.mcp_server --control-root <absolute-control-root>` for MCP. The application owner is `KnowledgeApplication`; no historical `KnowledgeServices` or module-forwarding layer is required.

The withdrawn `scheduled-worker` Make target has no supported host launcher. Daily Stewardship is an owner API, not an installed scheduler: native adoption must bind due checks, admission, scoped execution, independent assessment and candidate recording. Do not restore the old `ai worker` path as a substitute.

## Disposable native Officer execution

`application/normalization_work.py` composes one bounded NormalizationOfficer WorkSpec and its exact source-bound executor. The caller supplies the owner-approved Officer/EvalOfficer binding and separately admits that WorkSpec and Capture through ExOps. The executor admits one immutable one-task Graph, invokes the existing deterministic normalization proposal effect, and registers only its exact Pending artifact for result export. A separate deterministic EvalOfficer reopens the source, proposal, effect receipt and human gate before assessing `criterion.proposal`. An unsupported criterion or changed evidence cannot be reported complete.

The default Officer path does not call a model or use a Team Manager. The explicit model-bound mode below requires a separately pinned host Runner; it is never a fallback for a failed deterministic run. Neither path approves Pending, applies canonical Markdown, or implements a native LangGraph or Team runtime. The Graph contract describes the bounded orchestration, not deterministic model output. Native Temporal provides the execution path; owner State retains admission, proposal effects and the independent assessment.

The optional `temporal` dependency pins the selected SDK without adding it to read-only MCP installations. `make officer-image-build` builds a distinct Officer image with `KNOWLEDGEOS_RUNTIME_EXTRA=temporal`; the default image stays SDK-free. After pulling the exact Temporal Server/Admin Tools 1.31.3 and PostgreSQL 16.14 images pinned in `ops/compose.officer-smoke.yaml`, run `make officer-smoke`. Set `KNOWLEDGEOS_OFFICER_EVIDENCE` to a new JSON path when a specific evidence destination is needed; existing evidence must not be overwritten.

The smoke uses synthetic Capture data, maintained control/Core inputs, a unique internal-only Temporal/PostgreSQL stack and temporary State/Runtime. Actual local Owner HTTP exports exact result bytes without credentials. It checks native start/completed Activity, independent assessment, one Pending artifact, unchanged existing Vault files, actor-injection refusal and teardown. It publishes no host port and mounts no real domain roots, credentials or Obsidian profile. This bounded path does not prove general restart, backup, uncertain-effect recovery or live-data adoption.

Computer Use and native Obsidian checks remain user-run. Backend proof does not establish installed plugin behavior, note switching, sidebar results, human decision/apply, retrieval quality or general Team execution.

## Exact native model Officer binding

The model mode pins the native runtime kind/version/revision, provider/model and actual host adapter digest in the Officer binding and admitted WorkSpec. After owner Graph admission, the executor issues one exact Work/Profile/Graph/generation/fence/source request with source chunk locators but no source text. Only the trusted host transport selects the container, control root and installed Hermes interpreter. Model arguments cannot replace that context.

`ops/clients/hermes/native_officer.py` runs a fresh native Hermes process with only `work_context_read`, `knowledge_read` and `proposal_create` from the bound `vaultmcp`. Every call still crosses the owner's scope checks. A disposable Hermes home disables general host tools/context/memory/background behavior. Existing selected credentials stay on the host; inference may use the normal Hermes refresh path when the token cannot cover its stage deadline. Diagnostics remain read-only. Missing or unrefreshable credentials, changed routes/adapters and unavailable runtime inputs still refuse execution; there is no provider fallback or automatic effect retry.

Model output is execution evidence, not completion or effect authority. The owner requires matching request/binding/route and successful scoped tool evidence, then reopens its actual completed proposal receipt and current source. The separate deterministic EvalOfficer assesses the exact Pending bytes and human gate; the producing model cannot certify its own completion. An external-provider binding must not export `local_only`, denied or confidential source text.

Build the selected Officer image with `make officer-image-build`, then use `make model-officer-smoke`. The host selections are `KNOWLEDGEOS_HERMES_SOURCE`, `KNOWLEDGEOS_HERMES_PROFILE_HOME` and `KNOWLEDGEOS_HERMES_PYTHON`; defaults select the installed local build, and a different installation requires explicit matching selections. `KNOWLEDGEOS_MODEL_EVIDENCE` optionally selects a new JSON destination. The smoke keeps the same internal-only disposable Temporal stack and synthetic roots; a narrow stdio rendezvous lets host Hermes call scoped MCP through `docker exec` without mounting a Docker socket, credentials or provider networking into Owner. It checks a source-only random code, wrong-source/tool denials, native execution, independent assessment and exact Owner HTTP export before teardown.

This command makes real calls to the already configured provider using synthetic text. It does not activate a production listener, Team, Cron, embedding service or Obsidian profile, and it does not perform Computer Use. Keep provider, runtime, owner effect, assessment, HTTP, preservation and device evidence separate.

## Disposable native Curation Team

`application/curation_work.py` binds one `knowledge-curation` Team to its dedicated CurationManager, the selected triage and normalizer Profiles, and a distinct EvalOfficer. Director admits the exact Capture and WorkSpec first. The Manager reads the pinned `knowledgeos.capture_normalization_team` method and proposes one immutable two-task segment; the owner validates the method, source, explicit assignments and budgets before admitting it.

`application/curation_temporal.py` registers the official [Temporal LangGraph plugin](https://docs.temporal.io/develop/python/integrations/langgraph) with four sequential Activity nodes: Manager, Capture evidence, normalization proposal and assessment. `curation_workflow.py` defines `KnowledgeOSCurationTeamV1` at module scope. The plugin also requires importable task identities, so the nodes are module-level class methods with instance-local owner bindings, not factory-local closures. Every node disables automatic Activity retries. The normalizer maintains a heartbeat with a 15-second timeout; a timeout is not proof that its producer stopped. Unknown effects require the explicit owner recovery boundary below. LangGraph has no separate persistent checkpoint store.

The Manager, triage and independent EvalOfficer are deterministic responsibilities; only the normalizer invokes the native Hermes model. Triage records every page of the exact source chunks. The model receives the existing three-tool scoped contract, and its trace must cover complete ordered source pages before the Pending proposal. Assessment reopens the source, triage evidence, native trace, proposal bytes, successful effect receipt and live human gate. Completed Work means an independently verified Pending proposal, not approval or canonical apply.

The optional `team` extra pins `temporalio==1.34.0`, `temporalio-langgraph==0.1.0` and `langgraph==1.2.0`. `make team-image-build` builds a separate Team image without replacing the read-only or Officer images. The owner binding includes the four Team execution-module digests, package versions and native host adapter binding. Officer and Team dispatches use distinct Workflow types and cannot borrow each other's admission.

Run `make team-smoke`, optionally with `KNOWLEDGEOS_TEAM_EVIDENCE=/absolute/new-result.json`. It uses the selected host Hermes interpreter and existing refreshable credential source, synthetic Capture data and a unique internal-only stack from `ops/compose.team-smoke.yaml`. It checks native start, four distinct Activities, Profile assignments, source-hidden evidence, independent assessment and exact Owner HTTP result bytes. Cleanup removes only disposable roots, containers, networks and database state. Preservation checks permit normal selected OAuth token/timestamp rotation while retaining account identity, token presence, other profiles/providers, configuration and domain data.

The ordinary path does not implement arbitrary Team topology, successor segments, general human-wait/control application, history rollover, general restart/effect recovery, model-based assessment, production services, Cron, embeddings or GUI adoption. The separately bounded committed-result recovery path below is not a substitute for those contracts. Computer Use and native Obsidian interaction remain user-run checks.

## Disposable native Evidence Research Team

`application/research_work.py` binds `knowledgeos.cited_research_team` to one `evidence-research` Team with its dedicated `knowledgeos.research_manager`, exact retriever and synthesizer executor/Profile pins, and a distinct EvalOfficer. `research_admission` constructs a Director-admitted WorkSpec for one exact source ResourceReference and bounded question. The native binding, source digest and question participate in the WorkSpec identity. Its explicit assignments allow one immutable sequential Graph, one segment and two Profile invocations; they do not authorize continuation or another Team method.

The existing `document_enhancement` intake selects this concrete constructor through the trusted `build_template_admissions(..., native_research_runner_binding=...)` keyword. The Director still issues the admission receipt and confirms the exact WorkSpec and source; the browser cannot supply a runtime binding, executor, criterion or Graph. A generic template admission is not silently reinterpreted as this native WorkSpec. Exact method/Team routing selects `KnowledgeOSEvidenceResearchV1`; Research cannot fall back to Curation or the NormalizationOfficer.

`research_temporal.py` registers the official Temporal-backed LangGraph plugin with four sequential method-bound Activities: `manager`, `retrieve_evidence`, `answer_artifact`, `assess`. `research_workflow.py` holds the SDK-only module-level Workflow; Activity task identities are methods on a module-level class with instance-local owner bindings. The selected packages remain `temporalio==1.34.0`, `temporalio-langgraph==0.1.0` and `langgraph==1.2.0`, with no separate persistent LangGraph store. Every Activity has maximum attempts one. Both model stages maintain heartbeats; neither a timeout nor a completed model response authorizes a retry or certifies the Work.

| Responsibility | Model-visible tools | Required evidence or effect |
| --- | --- | --- |
| ResearchManager | No model invocation | Owner-validated exact immutable two-task Graph |
| Retriever Profile | `knowledge_search`, `knowledge_retrieve`, `knowledge_read`, `citation_verify` only | Exact admitted query and generation, all ordered source pages, actual verified citation hashes |
| Synthesizer Profile | `artifact_submit` only | One receipt-bound noncanonical `document_enhancement_report` from the validated retrieval bundle |
| Independent EvalOfficer | No model invocation | Fresh source/generation/citation checks, report coverage/gaps, exact frozen artifact bytes and successful owner effect receipt |

Successful empty queries do not waive exact admitted source reads. Synthesis receives immutable evidence from actual scoped reads. Independent assessment checks integrity, not claim truth or quality; Owner repeats validation under its writer lock. Local `POST /owner/work/result` returns exact artifact bytes and current evidence without HTTP authentication. After a source edit or move, the old artifact remains readable by Work ID as historical with invalid current evidence; its previous assessment grants no current-source or apply authority. No whole-Vault source generation comparison gates ordinary reads.

`ops/clients/hermes/native_research.py` owns Research request, tool, prompt and callback-trace semantics. `native_runtime.py` supplies fresh-process isolation, normal refresh of existing selected credentials, exact provider transport and scoped MCP discovery. There is no Gateway retargeting, provider fallback, general host toolset or credential copy into Owner. The adapter digest hashes canonical `adapter_sha256` and `runtime_sha256`; `research_native_digest` additionally binds the four Research execution/dispatch modules, package pins and complete Runner binding. The normalizer-only Officer retains its distinct request/tool contract and single-file raw-byte binding format. Provider-bound Research excludes local-only, denied and confidential sources.

After `make team-image-build`, run:

```sh
make research-team-smoke \
  KNOWLEDGEOS_RESEARCH_EVIDENCE=/absolute/new-result.json
```

Use the same explicit `KNOWLEDGEOS_HERMES_SOURCE`, `KNOWLEDGEOS_HERMES_PROFILE_HOME` and committed-venv `KNOWLEDGEOS_HERMES_PYTHON` selections as the native model Officer path. The configured route must remain native Hermes 0.22.5 at its selected revision, `openai-codex` / `gpt-6.1-terra`; missing or changed inputs fail closed. Evidence destinations are create-only; omission selects a unique `Tmp/knowledgeos-native-research/*.json` path. The host rendezvous permits exactly one retrieval invocation followed by one synthesis invocation, not an automatic retry.

The smoke uses synthetic five-root fixtures and an internal-only Temporal/PostgreSQL stack. A source-only random code must pass through actual scoped reads into the cited report. Checks cover native history, four Activities, two model invocations, package/module identity, scope/citation boundaries, independent assessment, exact HTTP export and historical result readability after a source edit. Domain data and profile/account identity stay preserved; normal selected token rotation is allowed. The [2026-10-31 native evidence](../../../Tmp/knowledgeos-native-research-2026-10-31/trace-run.json) remains historical proof of its then-current behavior, including the old stale-result refusal, not proof of the changed document-local behavior.

Research completion means one independently integrity-checked noncanonical report for human review, not a note draft, human approval or canonical apply. This binding supplies no general retry, recovery, continuation, pause/cancel/steering or service/disk restore. Never rerun an uncertain synthesis effect merely because its Activity failed. Read-only startup, installed role homes and historical disposable proof do not activate a writable production runtime. The linked historical trace used Core 0.18.1; current pins come from `ops/config/core-adoption.json`. Native Obsidian checks remain separate.

## Bounded Worker restart and committed-effect recovery

This recovery boundary covers a failed normalization or assessment Activity only when the exact native model response, triage evidence, one Pending effect and its proposal registration are already durable, but the final owner Work result is not yet recorded. Workflow history retains completed Manager and triage steps; replay must not execute their bodies again. Such a failed Activity enters an explicit recovery wait rather than rerunning the graph, model or proposal effect.

The Workflow derives the failed Activity ID from the native `ActivityError` and records it with its Temporal run ID through `knowledgeos.curation.mark_unknown.v1`. The read-only Query `curation.recovery_status.v1` returns only status and those native identities. An already-unknown owner application can receive its missing native failure marker without reopening Profile tools.

Compose `CurationTeamExecutor` with an owner-controlled `recovery_quiesced` callback. It must independently confirm the exact native failure and that the previous producer is stopped; neither a heartbeat timeout nor a `quiesced` value in caller input grants recovery. Missing or revoked confirmation fails closed. This callback is a trusted runtime binding, not a model tool or a general public recovery endpoint.

The original admitted actor calls `request_recovery(work_run_id, actor=..., recovery_id=..., target=...)`. The target pins the original dispatch identities, failed Temporal run/Activity, Work revision, generation and fencing token. The owner durably records a `received` intent, including the authenticated actor and ingress, before returning the small `{recovery_id, target}` transport payload. Its private evidence pins the current source, native trace, proposal, effect receipt and human gate. A foreign actor, changed target, conflicting identity or incomplete evidence is denied.

Only then call the selected Workflow's `curation.reconcile.v1` Update with that exact payload and the recovery ID as the Update ID. The Workflow validates its own failed target before scheduling `knowledgeos.curation.reconcile.v1`. Reconciliation reauthorizes the recorded actor/ingress, freshly checks the pinned evidence and independently assesses the existing Pending proposal without invoking the runner or proposal tool. Under the owner writer lock, `worker_finished(recovery_id=...)` again checks current actor authority, the effect receipt, source/proposal bytes and still-pending unexpired human gate, then atomically records the Work result and `applied` recovery receipt. Exact applied replay returns that receipt without another assessment or effect. Receipt and application are distinct states.

After `make team-image-build`, run `make team-recovery-smoke`, optionally with `KNOWLEDGEOS_TEAM_RECOVERY_EVIDENCE=/absolute/new-result.json`. It uses the same synthetic roots, selected real model and private disposable service stack as the Team smoke. A test-only executor holds after the production normalization method has committed; the supervisor kills that actual Worker process, observes the native heartbeat timeout, reconstructs a fresh Worker from frozen owner setup, admits recovery and verifies the native Update acknowledgement. It also runs the exact server history through a fresh SDK Replayer, checks no producer/effect re-execution and exports identical Pending bytes through authenticated Owner HTTP. Both Worker processes, temporary roots and the stack must be gone before a PASS is recorded.

If the model response itself was lost before durable evidence registration, a Pending file alone is insufficient: recovery stays unknown and no provider/effect retry is authorized. Response loss after the ordinary assessment has already committed a terminal Work result is outside this particular path. This command does not prove arbitrary crash-window recovery, Service/PostgreSQL restart, disk restore, live reassignment, successor Graphs, human approval/apply or production adoption.

## Quiescent native domain restore

`ops/tests/support/domain_restore_smoke.py`, `ops/compose.domain-restore-smoke.yaml` and the existing Team composition implement one bounded EX-R02 retained-persistence restart and the coordinated quiescent portion of EX-R10. Select the current Team image and existing Team-smoke Core/control/Services inputs; digest-pinned Temporal Server/Admin Tools 1.31.3 and PostgreSQL 16.14 must already be local. An isolated installed Python check without source mounts requires owner-module digests, Core adoption pins and package versions to match intended source before startup. Mounted source is not fallback package proof.

The runner uses unique internal-only projects and mode-0700 synthetic fixture directories beneath workspace `Tmp`, exported to the Docker VM, never host macOS `TMPDIR` or live roots/profiles/credentials. Evidence paths are create-only. From the KnowledgeOS checkout:

```sh
make domain-restore-smoke DOMAIN_RESTORE_EVIDENCE=/absolute/new-restore.json
```

The bounded procedure is:

1. Finish the controlled Curation initial Graph and durable human wait; stop the Worker, receive the exact revision-bound CANCEL over authenticated Owner HTTP, then stop HTTP.
2. Actually restart PostgreSQL while retaining its named volume and recreate the Temporal Service. Reconstruct the Worker and require the exact Workflow/Run/namespace/type/queue/waiting Query and unchanged owner evidence/counters.
3. Stop Worker and Service; require completed checkpoint Activities and zero other PostgreSQL client connections. Snapshot synthetic Core/control/Vault/State/Runtime trees, bytes/modes, IDs, receipts/artifacts/pins, runtime/module/package identities and both `temporal` and `temporal_visibility` databases. Runtime is retained byte-for-byte under exact pins.
4. Retire the original Service/database/volume and destroy original roots before creating the restored owner. Restore fresh isolated databases/roots, verify tree bytes/modes and rebind only the explicit fixture root locator.
5. Reconstruct the Worker and require the exact saved history prefix and waiting Query. Read-only readiness has a 180-second deadline and tolerates only `UNAVAILABLE` or Query's exact `FAILED_PRECONDITION` message `no poller seen for task queue recently, worker may be down`, after identity validation. It never retries mutations or tolerates identity mismatch.
6. Apply/reconcile the saved CANCEL once and verify authenticated terminal cancellation/cached receipt, unchanged finished Profile/evidence/budget identity, zero provider/proposal effects and fresh replay without owner writes. Remove both projects' containers/networks/volumes and fixture roots; cleanup observes both exact project labels even after preflight failure.

The [Core 0.19.0 bounded restore proof](../../../Tmp/knowledgeos-domain-restore-2026-10-31/core019-run.json) records actual retained PostgreSQL restart/Service recreation, installed-source/pin/package equality, restored native Run identity, received CANCEL applied once, authenticated terminal cancellation, zero provider/proposal/finished-Profile reruns, 40-event replay with zero owner writes and complete two-project/root teardown. This is same-host disposable Docker evidence, not general EX-R10 host/disk-loss acceptance.

In-flight Activity/effect restore, producer hard-loss, effect rollback, unsaved commands, divergent histories, schema/version migration, scheduled backup, disk/host loss, off-host durability, production, GUI and model execution remain outside this proof. No live State migration or production activation is performed.

## Bounded criterion-linked successor

`application/curation_continuation.py` selects a distinct two-segment method, `knowledgeos.capture_normalization_continuation_team`, on the same Curation Team. It is not an implicit retry of the ordinary or recovery path. The exact Director-admitted WorkSpec keeps one Pending-proposal criterion, bounded Manager delegation, both allowed Profiles and the same selected source.

The initial triage Graph records complete source reads. A distinct deterministic EvalOfficer marks the proposal criterion `not_run`, not satisfied. `KnowledgeExOps.record_remaining_work` atomically closes the segment, retains its assessment/checkpoint/evidence, records exact `RemainingWork` and computes continuation authority. The normalizer successor binds the immutable predecessor, WorkSpec revision/digest and RemainingWork identity/digest. It cannot expand criteria, change Profile bindings, rewrite the first Graph or reset consumed counters.

Default limits are two segments, two repeated-gap assessments and two Profile invocations. The first incomplete assessment consumes one repeated-gap assessment. Reaching any limit, or lacking enough Profile budget for the next stage group, durably records `awaiting_human` with `continuation_state=human_required`; no model or proposal call follows. Owner results expose only RemainingWork tied to the latest assessment. Exhausted work requires an authorized owner/Director revision; this path does not apply that revision.

`curation_continuation_temporal.py` and the SDK-only `curation_continuation_workflow.py` register the method-bound `KnowledgeOSCurationContinuationV1`. Two official plugin graphs execute three initial Activities, then three successor Activities only on the committed `allowed` decision. Retries remain disabled and the model maintains heartbeats. Stage scheduling and final Work commit reauthorize the original actor/ingress and current Director/source/runtime binding. Existing scoped MCP policy and Work/Profile/Graph grants govern calls inside an admitted Activity; this is not a general mid-Activity revocation protocol.

Final independent assessment rechecks the original triage context, native trace, Pending bytes, successful receipt and live human gate. It retains both segments and the initial incomplete assessment while citing the latest successor evaluation. Completion means one checked Pending proposal, never approval or canonical apply.

After `make team-image-build`, run `make team-continuation-smoke`, optionally with `KNOWLEDGEOS_TEAM_CONTINUATION_EVIDENCE=/absolute/new-result.json`. Synthetic roots and the existing disposable stack exercise real model completion, three no-provider budget stops, exact histories and fresh SDK replay, authenticated Owner HTTP, source preservation and teardown. Evidence paths are create-only. The segment-limit case also lowers the repeated-gap limit to satisfy Core's constraint that it cannot exceed the segment limit.

Combined continuation/crash recovery, general human control, arbitrary topology, history rollover, model-based evaluation, production activation, Cron, embeddings and native Obsidian remain separate work. Computer Use stays user-run.

## Bounded owner command application

`application/curation_control.py` supports `CANCEL`, or one ordered `PAUSE` → `RESUME` pair, at the closed initial continuation boundary before normalizer, provider or proposal execution. The trusted composition selects `submit_work(..., control_checkpoint=True)` on the exact continuation method; the flag participates in the admission digest and selects `KnowledgeOSCurationControlV1`. Ordinary continuation cannot execute that controlled admission. `CANCEL` is also available while paused; repeated pause/resume and new control after release are outside this bounded route.

After the three initial LangGraph Activities and an independently admitted gap with remaining budget, a native checkpoint Activity verifies current source, WorkSpec, Team/runtime binding, original actor and complete initial evidence. It records a safe `awaiting_human` transition and exact native run/checkpoint target. This is a real wait, not a completed proposal criterion. The controlled plugin registers both graphs, but no successor Activity or Graph admission is authorized before exact applied `RESUME`.

Compose `CurationRunControl(exops, executor)` and pass `run_control` to writable HTTP. Every route uses the trusted local actor and current owner policy; commands remain bound to the original Work actor:

| POST route under `/owner/` | Body fields | Result |
| --- | --- | --- |
| `work/control/target` | `work_run_id` | Exact waiting or paused target; unavailable after release or termination |
| `work/control/cancel` | `work_run_id`, `command_id`, `target`, `expires_at` | Durable command receipt, not application acknowledgement |
| `work/control/pause` | `work_run_id`, `command_id`, `target`, `expires_at` | Durable PAUSE receipt; application returns a fresh paused `next_target` |
| `work/control/resume` | `work_run_id`, `command_id`, `target`, `expires_at` | Durable RESUME receipt targeting the applied PAUSE, not the initial checkpoint |
| `work/control/command` | `work_run_id`, `command_id` | Current owner command receipt |

Receipt constructs the existing Core HumanAction request/response, accepted decision, SteeringDirective and effect intent in the owner journal. It preserves the actual command ingress, pins the target revision/generation/fence/native run and expiry, and adds a pending command fence without advancing the control revision. The Work remains waiting while its Worker is unavailable. Duplicate identical requests return the same receipt; changed identities, target types, expiry, source or authority cannot borrow it.

Use `request_control(work_run_id, kind=..., actor=..., command_id=..., target=..., expires_at=...)` for owner admission. Deliver its exact `{command_id, kind, target}` using `connect_control_temporal` and `deliver_control`; Services supplies only the scoped transport. `curation.control.v1` uses `command_id` as the stable Update ID, and `curation.control_status.v1` is a read-only Query. The application Activity reauthorizes both actual command ingress and original submission, reconstructs partial evidence and verifies the quiescent boundary under the owner writer lock. Each application advances run/control revisions and clears its pending ID. PAUSE records `paused` and a fresh target without completing Work; RESUME records `running` without resetting counters, then releases only the original criterion-linked successor. Only CANCEL terminates Work and records its final execution receipt; none rolls back an effect.

The owner rechecks the applied PAUSE/RESUME chain and current actor/Director/source/binding at every resumed Activity, successor admission and final result commit. Separate-process scoped MCP tools and the final proposal writer independently validate the durable command snapshots, decisions, successful receipts, current pins and immutable assignment. They cannot borrow a running flag or a cached Work binding to bypass release. This is not a general mid-Activity revocation protocol. Original initial evidence remains immutable; final independent checkpoint/artifact evidence is retained atomically with completion.

`received` and `applied` are distinct receipt states. If an Update response is lost after canonical application, `reconcile_control` reauthorizes and returns the exact durable receipt without another native dispatch. Missing application evidence preserves the transport error and pending command. Application rejection or expiry retains the Work fence; this path does not silently authorize another command or continuation. Historical applied receipts validate their retained result snapshots, not later Work revisions, so old PAUSE can reconcile after RESUME and successor completion without reopening a checkpoint or consuming budget. Applied replay may remain valid after expiry, but current access and binding checks still apply.

After `make team-image-build`, `make team-control-smoke` runs no-provider CANCEL plus a PAUSE/RESUME case with one actual model invocation and Pending artifact. It reconstructs Workers at both waits, checks stable-ID Updates, lost responses, historical receipts, exact Owner results, replay and teardown; local HTTP needs no token. For CANCEL only, run `python3 ops/tests/support/curation_control_smoke.py host --case cancel --evidence /absolute/new-result.json`. Evidence paths are create-only. Neither case approves or canonically applies a proposal.

This proves quiescent Worker reconstruction and client Update-response loss, not process hard-loss, control Activity completion-report loss, in-flight control, CUTOVER, repeated pause/resume, general amendment/control, history rollover, Service/disk restore, production or GUI adoption. Computer Use remains user-run.

## Bounded native question, parameter steering and partial CUTOVER

`application/curation_steering.py`, its SDK-free wire module and native Temporal/Workflow modules select a distinct `knowledgeos.capture_steering_team` method and `KnowledgeOSCurationSteeringWorkflow`. This does not widen `curation_control.py`, reinterpret its original closed checkpoint, or alias CUTOVER to RESUME. The exact owner binding selects the existing triage and draft-writer Profiles with independent Manager/EvalOfficer identities. This method is deterministic: it reads one Director-selected Capture and publishes a source-bound normalization report in the owner journal, not a model-produced artifact, Pending proposal or canonical Vault edit.

The initial immutable Graph includes both task Profiles. The official LangGraph plugin compiles it with an `InMemorySaver` and `interrupt_before=["normalization_report"]`. After triage, the Workflow checks the actual checkpoint's pending node before a native owner-bound Activity records the supported boundary. Temporal history reconstructs that in-memory checkpoint after Worker restart; there is no second durable LangGraph store. The owner opens an existing Core `HumanActionRequest` asking for the later report's label. Public `kind=answer` accepts bounded answer JSON and records an existing `HumanActionResponse` with `chosen_action=approve`; it is not a new SteeringDirective wire kind. Applying the answer leaves the genuine partial Graph paused with its later task still unscheduled.

Compose `SafeCurationSteering(exops, director_amendment_admitted=...)` explicitly. Local JSON POST routes under `/owner/` retain the original Work actor, ingress and current owner policy, without HTTP credentials:

| Route | Body | Meaning |
| --- | --- | --- |
| `work/steering/view` | `work_run_id` | Current target, pending question, supported kinds, parameter bounds and received/applied command receipts |
| `work/steering/artifact` | `work_run_id`, bounded JSON `content` | Exact owner-stored Core ArtifactReference; registering evidence does not admit a command |
| `work/steering/request` | `work_run_id`, `command_id`, `kind`, `target`, `expires_at`, `directive_reference`; kind-specific `parameter_id` or `successor_spec` | Durable receipt and dispatch intent before acknowledgment, not application completion |
| `work/steering/export` | `work_run_id` | Independently revalidated completed report reference, exact base64 bytes and assessment |

Answer content is `{"answer":"<nonempty label>"}`. At the paused partial boundary, parameter content `{"value":"source_order"}` or `{"value":"alphabetical"}` may target only the WorkSpec-declared `report.order`; this changes future context, never the executed Graph, source or immutable evidence. CUTOVER uses bounded `{"reason":"..."}` content at that same supported boundary. Receipt pins exact directive artifact, actor/ingress, logical command and Work identity, WorkSpec/control revision, generation/fence, native Run and expiry. Only one command may be pending. An unavailable Worker leaves it received with its dispatch committed. Conflicting IDs, stale targets, expired commands and revoked authority reject; an applied duplicate reconciles the retained successful receipt without reapplying the transition. Receipt JSON object-key order is not an additional wire contract; immutable artifact bytes and hashes remain exact.

The native Update first obtains an independent partial assessment: triage evidence is present and the unscheduled report criterion is `not_run`. Application stops scheduling, verifies the actual interrupted checkpoint and absence of unresolved effects, retains that assessment/artifacts, and transitions `paused → cutting_over → paused`. It closes the old active Graph and increases generation with a new fence; old-generation publication rejects. This route's only publication effect atomically commits report bytes and a successful effect receipt in one owner-journal writer transaction. The boundary therefore supports source reads and that exact atomic journal effect, not cancellation, rollback or reconciliation of an external, provider or filesystem effect.

With the old Graph closed and Work safely paused, `amend_work` additionally requires a new exact WorkSpec/admission receipt, increasing WorkSpec and criteria revisions, and an explicit current trusted Director callback. Its artifact is `{"successor_spec_digest":"sha256:..."}`. The implemented amendment is specifically the labeled-report objective and criteria, adding a nonempty owner label and exact nonempty-source-line count. Old WorkSpec, partial segment and evaluation remain immutable. Different source/binding/delegation/pins/budgets and arbitrary objectives or criteria are not silently admitted. `resume`, with bounded reason content, releases Manager admission of a distinct immutable successor tied to the applied CUTOVER or new WorkSpec revision. The two-segment budget reserves two initial task slots and one successor task slot; CUTOVER does not refund counters or reopen the initial Graph.

The successor independently normalizes the exact source using its frozen answer/order context, then a separate deterministic EvalOfficer checks source, output bytes, effect receipt, criterion evidence and generation. Completion atomically retains original successor-admission/evaluated-run snapshots, bundle, source, triage, context, partial assessment, report, evaluations/checkpoints and publication/Work receipts. Later reads reconstruct that sealed evaluation context and run the full completed `WorkContracts.run` validation with the declared independent assessor; a completion flag alone never authorizes export. Read revalidation neither fabricates a live active Graph nor writes another effect.

After `make team-image-build`, `make steering-smoke` accepts an optional create-only `KNOWLEDGEOS_STEERING_EVIDENCE` destination. The [Core 0.19.0 disposable proof](../../../Tmp/knowledgeos-safe-steering-2026-10-31/core019-run.json) covers an actual native question wait, stopped-Worker HTTP receipt/outbox admission, reconstructed Worker and lost Update acknowledgment, bounded parameter influence on successor context, partial CUTOVER/assessment, denied versus separately Director-admitted amendment, old-generation rejection, exact authenticated Owner export, 132-event replay without owner writes, synthetic Vault preservation and complete service/network/volume teardown. It used no provider. This source-selected proof does not migrate the existing live Core 0.18.1 owner State or supersede the original pins of older runtime proofs.

For separate browser verification, `curation_steering_smoke.py hold` keeps a synthetic actual native Run available for a bounded interval, reads its token only from stdin and delays each admitted delivery so received and applied can be observed separately. Overlay `ops/compose.steering-console-smoke.yaml` on the Team smoke stack: Owner/Worker stays internal, a mount-free relay alone publishes host loopback `17903` through a separate bridge, and the one-off smoke container requires `--use-aliases` for the fixed `owner` upstream. The exact labeled revision is preadmitted only when its trusted fixture flag is selected. Fixture availability is not browser evidence; host reachability and actual browser interactions require their own exercised checks.

This proof excludes model reasoning, external or filesystem-effect reconciliation, running reassignment, arbitrary objective/delegation amendment, production activation, GUI adoption and hard loss during control commit. It is not combined control/crash recovery, general in-flight model control or a replacement for the existing CANCEL/PAUSE/RESUME and rollover boundaries. Native Obsidian and device actions remain user-run.

## Bounded native history rollover

The controlled continuation Workflow supports one Continue-As-New at its idle `awaiting_human` or `paused` boundary. Native history-length suggestions can request it there. A trusted runtime client may also send `curation.rollover.v1` with the exact original five-field dispatch and a stable Update ID; its response means `requested`, not adopted or completed. This is runtime maintenance, not a new public HTTP control route, Work admission or budget grant.

The Workflow fences new control handlers, drains accepted handlers and calls the owner prepare Activity before continuing. `CurationRunControl.prepare_rollover` freezes the canonical checkpoint, applied command cache and any already-received pending command in a separate durable `curation.rollover.<work_run_id>` intent. New owner command admission and native application are blocked during the prepared handoff. A lost prepare response can return the same exact envelope without another write; no uncertain Activity is automatically retried.

The new native Run verifies its actual `continued_run_id`, then calls the adoption Activity. The owner rechecks the original actor, Director, source, exact binding, command decisions, full Work snapshot and handoff digest before authorizing exactly that replacement Run. An identical adoption replay is read-only. Original checkpoint targets and command bodies keep their original native identity; the separate handoff maps execution authority to the replacement without rewriting them. The old Run, a foreign replacement and a second rollover cannot execute effects.

Rollover itself changes no logical Work/Graph/Profile/tool/action ID, Work revision, evidence, remaining criterion or consumed budget. The new Workflow restores the waiting phase and applied receipt cache rather than rerunning the initial Graph or opening another checkpoint. Pending control still needs its original owner-admitted Update and current application authorization. Only the existing exact RESUME releases the immutable successor.

After `make team-image-build`, run `make team-rollover-smoke`, optionally with `KNOWLEDGEOS_TEAM_ROLLOVER_EVIDENCE=/absolute/new-result.json`. It applies PAUSE, stops the idle Worker, admits RESUME through authenticated Owner HTTP, reconstructs a Worker and rolls over before delivering that pending RESUME. It checks unchanged Work/evidence/counters, cached PAUSE replay in the new Workflow, old/foreign Run fencing, second-rollover denial, then one actual native model/Pending successor, independent assessment and exact Owner HTTP bytes. Both native histories replay through fresh SDK replay instances without owner writes. The existing synthetic-root, create-only evidence and teardown boundaries apply.

This single quiescent rollover does not prove arbitrary repeated rollover, hard loss during prepare/adopt, Activity completion-report loss, in-flight effects, combined continuation/crash recovery, Service/disk restore or production adoption. Native history-length suggestion timing is separate from the explicit maintenance-Update smoke. Computer Use and authenticated native Obsidian checks remain user-run.
