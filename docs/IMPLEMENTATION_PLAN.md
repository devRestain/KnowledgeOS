# Agent Execution Pipeline

- Read `PROJECT_STATE.md` before selecting or promoting a slice.
- Resolve conflicts in `OBSIDIAN_VAULT_BLUEPRINT.md`, `blueprint/blueprint.yaml`, then `OBSIDIAN_VAULT_WHITEPAPER.md` order.
- Implement one acceptance-gated capability per session and preserve the isolated boundaries below.
- Use fresh lane-local labels: `Cxx` for ordered core capabilities, `Dxx` for gated deployment overlays, `Exx` for optional extensions, `Pxx` for plugin and settings plans, and `Fxx` for cross-lane GUI-first Obsidian and `vaultctl` boundary refactors.
- Interpret numeric order only within the same lane; never infer ordering or authorization across lanes.
- Treat planning, state migration, documentation maintenance, and legacy labels in Git history as non-ordering provenance.

## Core capability lane

- `C01` — Establish the Colima/Docker runtime and verification harness.
- `C02` — Validate the Blueprint with Draft 2020-12 JSON Schema.
- `C03` — Validate registry, path, action, command, and bridge semantics.
- `C04` — Validate Base, dashboard, projection, and transaction semantics.
- `C05` — Define generated-artifact ownership and byte-for-byte zero-diff checks.
- `C06` — Implement operational policy, strict note schema, and note engine.
- `C07` — Implement templates, additive bootstrap, and create-only project bundles.
- `C08` — Compile Bases, Home, Mobile, and deterministic dashboards.
- `C09` — Verify the portable Vault fixture and restricted-mode smoke boundary.
- `C10` — Implement offline bridge, response, and root-sentinel schemas.
- `C11` — Implement offline mobile shortcuts, durable outbox, and recovery contracts.
- `C12` — Implement provider-free read-only diagnostics and CLI integration.
- `C13` — Implement create-only local commands and guarded formatting.
- `C14` — Implement hash-bound asset, capture-finalize, and archive transactions.
- `C15` — Implement fsynced recovery journals and idempotent transaction replay.
- `C16` — Implement read-only reconcile, repair plans, repair apply, and receipt verification.
- `C17` — Implement provider-free bridge ingest, exact local Git publish, and crash recovery.
- `C18` — Implement the read-only deterministic triage proposal contract.
- `C19` — Implement provider-free review, approve, reject, and apply closure.
- `C20` — Implement remaining proposal actions, facade routes, and PRD traceability.
- `C21` — Implement deterministic JSONL projection and atomic generation pointers.
- `C22` — Implement lexical and typed-link retrieval with a frozen evaluation baseline.
- `C23` — Implement cited answers and the full provider-free `guestbook-horror` flow.
- `C24` — Render background artifacts and verify synthetic wake and recovery behavior.
- `C25` — Repair the central retrieval privacy gate and review-corpus filtering.
- `C26` — Bind displayed evidence, citations, and fused retrieval provenance to exact source bytes.
- `C27` — Implement action-specific proposal schemas, daily-fragment binding, and deterministic proposal generation.
- `C28` — Make proposal approval and rejection replay-safe, journaled, and crash-recoverable.
- `C29` — Publish privacy-minimized AI projections with explicit local and remote eligibility classes.
- `C30` — Reconcile capability diagnostics and define the generated local-model configuration contract.
- `C31` — Implement provider-neutral request, response, receipt, and frozen-context envelopes.
- `C32` — Implement the executable pipeline broker and deterministic synthetic provider adapter.
- `C33` — Implement a bounded Ollama connector and verify it against a fake local service.
- `C34` — Operate the immutable Qwen index and learned-retrieval quality gate after the accepted E02 promotion.
- `C35` — Implement schema-constrained Gemma 4 answer and proposal routes without direct Vault mutation.
- `C36` — Implement a recoverable one-shot provider queue consumer while provider-queue background activation stays disabled.
- `C37` — Finalize the Mac plugin-profile Blueprint and canonical `gemma4:12b` identity, then regenerate owned artifacts.
- `C38` — Lock local generation identity and promote it through an evidence envelope.
- `C39` — Add the local embedding adapter and non-destructive index promotion gate.
- `C40` — Promote provider routes through the broker without granting write authority.
- `C41` — Implement the removable Obsidian thin client behind the authenticated loopback broker.
- `C42` — Close privacy, citation, quality, and observability gates.
- `C43` — Operate and roll back local AI safely across independently authorized lanes.

## Parallel lane rule

- `P01` remains an independent core/community-plugin setting-state lane; it does not gate starting C38 and it never operates Obsidian or changes a live profile.
- `F01` through `F06` remain a separate GUI-first period-note and `vaultctl` boundary lane; F work consumes explicitly recorded P01 evidence only where its plan declares that dependency.
- `C38` starts as its own local-generation identity lane. C38 may centralize the `gemma4:12b` profile and validate private evidence bindings without importing P01 or F implementation work.
- Cross-lane sequencing exists only where a capability explicitly declares a dependency, such as C41 consuming P01 setting-state evidence; numeric labels do not impose ordering across P, F, and C lanes.

## C25+ acceptance sequence

- `C25` depends on `C22`, `C23`, and `E01`.
  - Replace the exclusive review-path branch with one composable decision that always checks path, type, scope, sensitivity, and `ai_policy`.
  - Reapply the same policy during candidate fusion and graph expansion and immediately before any future model context is materialized.
  - Accept only after `include_review=true` still excludes confidential, denied, wrong-type, wrong-path, and wrong-scope notes across lexical, vector, and answer routes.
  - Keep provider calls, Vault writes, network access, and plugin actions absent from this release-blocking repair.
- `C26` depends on `C25`.
  - Bind every citation to note id, path, source content hash, exact locator, full chunk hash, displayed excerpt bytes, and a separate excerpt hash or byte span.
  - Prevent a `/frontmatter` candidate from displaying fallback body text, and keep lexical and vector chunk provenance distinct through RRF.
  - Reject any locator, excerpt, projection-generation, source-content, or per-channel provenance drift.
- `C27` depends on `C25` and `C26`.
  - Separate the C18 triage-candidate shape from strict `draft_note`, `link_suggestions`, and `normalize` mutation-proposal schemas.
  - Recompute and verify daily fragment bytes from the supplied locator instead of accepting a shape-valid fragment digest alone.
  - Generate one create-only `01_AI_Review/Pending` artifact with a bounded diff and source, target, prompt, action, policy, schema, and candidate bindings; perform no canonical target mutation.
  - Mark each C20 route as executable or controlled unsupported instead of treating a read-only plan as completed behavior.
- `C28` depends on `C27`.
  - Derive approval and rejection replay identity before adding timestamps, return `NO_OP` for an identical replay, and quarantine the same id with different bytes.
  - Journal rejection intent before rewriting or moving a proposal and recover deterministically from every injected interruption before receipt publication.
  - Keep unrelated Vault-tree drift from blocking a rejection that cannot mutate the proposed target.
- `C29` depends on `C25`.
  - Omit confidential and `deny` titles, bodies, properties, and incident edges from AI projection artifacts instead of copying then filtering them at query time.
  - Permit `local_only` content only in a local-eligible generation and derive any future remote context from `remote_ok` content only.
  - Publish immutable generations and one atomic pointer; reject the current full-content generation where a v2 privacy-minimized generation is required.
- `C30` depends on `C25` through `C29`.
  - Replace hard-coded C12-era overlay statements with separately derived `declared`, `configured`, `reachable`, `authorized`, `verified`, `enabled`, and `healthy` states.
  - Generate and validate the Blueprint-declared but currently absent `ops/config/local-models.yaml` without enabling a model profile.
  - Report projection version, usable index generation, D07 plugin evidence, C24 background inactivity, and E01 opt-in state without inferring device proof from files.
- `C31` depends on `C25`, `C26`, `C29`, and `C30`.
  - Define strict provider request, response, identity receipt, failure, and authorization schemas with byte, item, depth, time, and output-token limits.
  - Bind job, action, prompt, output schema, policy decision, frozen candidates, index generation, source hashes, provider route, model tag and full digest, Ollama version, and inference options.
  - Let the networkless core write one immutable mode-0600 context envelope under `runtime/`; give the provider runner no Vault mount and no canonical apply capability.
  - Keep raw prompt and response bytes out of Vault, Git, ordinary logs, and long-lived receipts; treat all model output as untrusted data.
- `C32` depends on `C27`, `C28`, and `C31`; `D10` verifies the resulting synthetic adapter.
  - Execute validated input → frozen context → policy decision → provider interface → strict output validation → C27 proposal or C26 answer artifact.
  - Simulate success, refusal, malformed JSON, schema violations, prompt injection, oversized output, timeout, cancellation, overload, replay, digest conflict, and adapter crash.
  - Preserve the existing provider-free route and forbid a provider result from calling C19 apply or writing canonical Vault content.
- `C33` depends on `D10`.
  - Implement `/api/chat`, `/api/embed`, `/api/tags`, `/api/show`, `/api/ps`, and version checks behind C31, but test only against a fake loopback service in this core slice.
  - Allow only an explicit loopback profile, reject redirects and arbitrary endpoints, require cloud-disabled state, set hard deadlines and payload caps, and never auto-pull or silently fall back.
  - Keep the canonical Compose `dev` service at `network_mode: none`; use a separately gated host-native runner with only per-job runtime inbox/outbox access.
- `C34` depends on `C25`, `C29`, `C31`, and `C33`.
  - Key every vector generation by projection generation, parser/chunker, embedding tag and full digest, dimension, prompt-role templates, indexer version, and retrieval configuration.
  - Use distinct query and document prompts, reject silent truncation, require the same model and dimension for index and query, and rebuild on any identity drift.
  - Implement a frozen Korean and multilingual evaluator that compares lexical-only, E01 feature hashing, learned vector, and RRF; verify its logic with deterministic fixtures in C34.
  - Keep learned retrieval opt-in until the `E02` promotion gates pass for live-model accuracy, privacy, citation, staleness, latency, and memory evidence. Fake-server tests cannot satisfy that promotion gate.
- `C35` depends on `C26`, `C27`, `C31`, `C33`, and `C34`.
  - Add schema-constrained cited-answer, triage, draft, link, and normalize routes using frozen evidence and proposal-only outcomes.
  - Reject unexpected tool calls or reasoning payloads, do not retain chain-of-thought, and independently validate JSON despite Ollama structured-output support.
  - Verify route orchestration with deterministic recorded responses in C35; require `E02` to evaluate refusal, abstention, citation faithfulness, stale-source rejection, prompt injection, Korean quality, latency, and memory before promoting any live route.
- `C36` depends on `C28`, `C32`, and `D10`.
  - Extend C24 with an idempotent one-shot consumer using atomic claim and lease, bounded concurrency, explicit terminal states, digest-conflict quarantine, and crash recovery.
  - Adopt only a validated response envelope and publish only through the existing C17 exact-response path.
  - Keep provider-queue background activation, live provider access, mobile transport, and Git network effects inactive within C36; let E03 own any separately authorized provider-free LaunchAgent installation.

## C37-C43 follow-up implementation plan

The C37-C43 lane records the remaining local-AI and Obsidian-assistance work after C25-C36. The order is intentional: first make the checked-in identity and generated contracts unambiguous, then promote local generation and embeddings only after E02 evidence, then add a thin Obsidian client, and finally harden evaluation and operations. A C label describes an implementation contract; it never authorizes model installation, network access, Obsidian UI changes, plugin mutation, LaunchAgent activation, or canonical Vault apply.

### C37 — Finalize the plugin profile and canonical Gemma identity

- Dependencies: C30, C31, C35, C36, and the accepted Mac plugin profile decision.
- Freeze `blueprint/blueprint.yaml#/plugin_profiles/mac_baseline` as the ten-plugin Mac allowlist and keep the mobile community-plugin list empty. The five newly profiled adapters remain UX roles only: startup experience, contextual commands, typed-relation navigation, bounded navigation, and low-risk property viewing.
- Make `gemma4:12b` the only generation identity in C30 configuration, C31 fixtures, C35 route validation, E02 defaults, documentation, and generated artifacts. Reject aliases, quantization variants, runtime variants, and silent rewrites; a live service may supply only the resolved name and full digest as evidence for the same requested identity.
- Regenerate every owned artifact from the current Blueprint SHA, including the deployed Property Dictionary copy, and keep the generated-artifact ownership contract byte-for-byte zero-diff.
- Acceptance: Blueprint semantic validation, source checksum, generated artifact export/check, C30/C31/C35/E02 identity regressions, state validation, and changed-root whitespace checks pass. Any pre-existing dirty Vault content outside the generated scope remains preserved and is reported separately.

### C38 — Lock local generation identity and promote it through an evidence envelope

- Dependencies: C37 and E02 host-native inspection, with the E02 promotion gates still open until explicitly measured.
- Centralize one canonical generation profile for `gemma4:12b`: bounded context, deterministic request options, no tools, no reasoning retention, no automatic pull, no fallback, and a private mode-0600 request/response/receipt envelope.
- Bind job, action, prompt digest, output schema digest, policy decision, frozen evidence, projection/index generation, provider route, requested model tag, resolved model name, full digest, Ollama version, host profile, and inference options. Treat the provider output as untrusted even when structured output is requested.
- Add identity-drift, digest-conflict, unavailable-service, timeout, refusal, oversized-output, replay, and private-artifact tests. Keep declaration/configuration/reachability/authorization/verification/enabled/healthy states separate and preserve `not_run` when host evidence was not collected.
- Promotion requires explicit host/network authorization, loopback and cloud-off evidence, internal-SSD resource evidence, frozen Korean/multilingual quality results, citation and staleness checks, and a human decision. It must not enable C30 by changing a declaration file alone.

### C39 — Add the local embedding adapter and a non-destructive index promotion gate

  - Dependencies: C25, C29, C34, C37, and E02 embedding measurements.
- Define a provider-neutral local embedding interface that supports distinct query/document templates, explicit dimensions, normalization, truncation refusal, full model digest capture, parser/chunker identity, and indexer version binding. Make Qwen3-Embedding 8B the live default selection, keep EmbeddingGemma as the emergency resource fallback, and validate the accepted Qwen identity through a temporary privacy-eligible C34 canonical build at the E02 gate while defer durable pointer materialization until KnowledgeOS implementation completion.
- Compare the selected local candidates on the same privacy-filtered Korean and multilingual fixture against lexical-only, E01 feature hashing, the current learned baseline, and RRF. Record Recall@k or the fixture's required-path predicates, MRR/nDCG where applicable, citation freshness, cold/warm latency, throughput, memory/device placement, refusal/truncation, index size, and replay behavior.
- Rebuild into a new immutable generation on any model, dimension, prompt, parser, chunker, or policy drift. Never mutate the canonical index in place, silently switch dimensions, or let a plugin-owned index become KnowledgeOS evidence.
- Acceptance requires a measured quality/resource/privacy gate and a reviewable promotion receipt; fake loopback responses can verify orchestration only and cannot satisfy live promotion.

### C40 — Promote provider routes through the broker without granting write authority

- Dependencies: C31-C33, C35, C37-C39, and an accepted E02 promotion decision.
- Extend the existing broker with an explicitly enabled local route for answer, triage, draft, link, and normalize actions. The broker alone may assemble frozen context and call the bounded transport; an Obsidian adapter never calls Ollama directly.
- Validate route schemas independently after transport, reject tool-call/reasoning payloads and prompt-injection markers, cap bytes/items/depth/time/output, and keep raw prompts/responses out of Vault, Git, synchronized plugin settings, and ordinary logs.
- Preserve proposal-only and cited-answer outcomes. C19 remains the only canonical apply authority; a provider response can create a private review artifact or answer receipt but cannot edit a canonical note, invoke a plugin write, or publish Git/network effects.
- Acceptance covers success, refusal, malformed/schema-invalid output, stale citation, source drift, digest drift, timeout, cancellation, overload, replay, crash recovery, and explicit disabled/not-authorized states.

### C41 — Implement the removable Obsidian thin client

- Dependencies: C37, C40, P01 setting-state inventory, and the separately planned E05 client decision.
- Keep the finalized plugin profile as optional presentation adapters. The client may collect a question, current note or selection scope, open an exact citation, show a bounded diff, and expose explicit approve/reject controls. It may not become policy, retrieval, index, provider, or apply authority.
- Route every request through an authenticated KnowledgeOS loopback broker that rechecks content/policy/index digests. Keep embeddings, prompts, responses, session state, and receipts in ignored private `runtime/`, never `.obsidian`, `.smart-env`, Git, or a synchronized Vault path.
- Prefer a plugin-free CLI and Markdown fallback for every surface. Direct Ollama calls, wildcard origins, LAN binding, autonomous agent/tool modes, synchronized secrets, and plugin-owned RAG indexes are prohibited.
- Acceptance is a removable client contract plus static fixtures and a user-reviewed device test plan. Installation, Obsidian execution, settings mutation, and mobile behavior remain separate deployment/device evidence and stay `not_run` until authorized.

### C42 — Close privacy, citation, quality, and observability gates

- Dependencies: C25-C41.
- Reapply path/type/scope/sensitivity/`ai_policy` filtering immediately before context materialization and again before route validation. Exclude confidential and denied content from local and remote projections according to eligibility, and bind every displayed citation to exact source bytes, locator, chunk, excerpt, projection generation, and channel provenance.
- Add frozen Korean/multilingual evaluation cases for abstention, refusal, prompt injection, stale sources, contradictory notes, wrong scope, citation faithfulness, and quality regressions. Keep model output non-authoritative and do not retain chain-of-thought.
- Emit bounded, redacted metrics for latency, queue depth, failures, replay/no-op, digest conflicts, privacy rejections, citation drift, and resource pressure. Logs must not contain source bodies, credentials, raw prompts, raw responses, or synchronized personal content.
- Acceptance requires deterministic fixtures, privacy/citation regression tests, redaction tests, bounded-log checks, and a promotion report that distinguishes static, semantic, runtime, external-service, deployment, and device evidence.

### C43 — Operate and roll back local AI safely

- Dependencies: C36, C40-C42, E03, and any separately authorized E04 lane.
- Keep one-shot queue consumption, host-runner access, LaunchAgent scheduling, provider activation, remote/unattended work, and mobile transport as independent gates. Default to disabled, explicit authorization, private per-job spools, atomic claims, leases, quarantine, replay-safe terminal markers, and exact rollback.
- Define installation, upgrade, disable, rollback, and recovery receipts for the broker, host runner, model identity, embedding index generations, and thin client. A failed check must leave the prior profile and canonical Vault unchanged.
- Acceptance requires dry-run and simulated crash evidence, exact ownership checks, no Git/network/device side effects unless separately authorized, and a user-facing handoff that lists the next gate and any `not_run` evidence.

## E05 lane — Removable desktop Obsidian thin client

E05 is the optional AI presentation lane adopted by `D111`. It consumes the
C41 request and response contract but does not replace the C19 review/apply
authority, the C42 safety gates, or the C43 deployment and rollback gates.

- Dependencies: C41-C43, the accepted P01 setting-state vocabulary, and the
  authenticated broker deployment contract. The artifact may be prepared
  without installing or operating Obsidian.
- Package a removable desktop plugin under
  `ops/clients/obsidian-thin-client/` with only fixed review entry points:
  `open-review` and `ask-current-note`. Support `current_note` and `selection`
  scope, exact citation opening, bounded diff presentation, and explicit
  approve/reject presentation.
- Build the exact C41 request in the plugin: canonical JSON, content and
  selection digests, policy/privacy/index bindings, authenticated
  `127.0.0.1` loopback endpoint, and proposal-only flags. Persist only
  non-secret endpoint and digest settings; accept the broker token for one
  in-memory request and clear it after the call.
- Keep the control-side broker seam in
  `ops/src/vaultops/thin_client_http.py`. Its HTTP adapter accepts only one
  canonical JSON `POST /broker` request over an explicit IPv4 loopback
  binding, compares the bearer token in memory, and dispatches through
  `VaultThinClientBroker`. The broker rechecks the persisted note bytes, the
  retrieval and privacy policy digests, and the current immutable index
  generation before calling the existing provider-free `vaultctl ask` path.
  Drift returns a bounded C41 conflict; it never becomes a provider, Vault,
  Git, scheduler, or canonical-apply authority.
- Expose that same seam through the explicit control-side command
  `vaultctl ai client --serve --token-file <mode-0600-file>` or
  `vaultctl ai client --serve --token-stdin`. The command reads one bounded
  token into memory, binds only `127.0.0.1`, and emits a token-free readiness
  record before serving; it is a separately operated deployment seam and does
  not give the plugin shell-launch authority. Persistent service operation
  remains separate deployment evidence.
- Keep the plugin presentation-only. It must not expose an arbitrary
  `vaultctl`/shell launcher, call Ollama, bind LAN or wildcard origins, write
  notes or plugin settings as part of review, persist raw prompt/response
  bytes, perform Git effects, or grant canonical apply authority.
- Acceptance: the removable manifest/contract, provider-free broker seam, and
  ephemeral authenticated loopback tests pass; JavaScript syntax and C41
  request/response boundary checks pass; live plugin installation, Obsidian
  execution, broker deployment, provider activation, and device behavior
  remain separate `not_run` evidence until explicitly authorized.

## E02 host-native live verification plan

- Treat `E02` as an explicitly authorized host-native external-service verification slice that uses the internal SSD only. Ollama and the selected models run on the user's physical machine so its native CPU, unified memory, GPU, or Metal/CUDA/ROCm backend remains available; do not install Ollama in Compose, add GPU passthrough to `ops`, or make the container a model host.
- Keep the E02 model store, runtime index, temporary benchmark artifacts, and model-load path on the internal SSD for the entire verification. Do not connect, mount, probe, benchmark, or compare an external SSD during E02; external-NVMe experiments require a separately scoped future slice and do not produce E02 production evidence.
- Keep the canonical `ops` Compose `dev` service at `network_mode: none`. Its loopback is container-local, so it must not call the physical host's `localhost:11434` directly. Use a project-owned host runner as the only Ollama caller and exchange one bounded C31 request/response job through private `runtime/` files:
  `networkless container → immutable context/request → host runner with per-job spool access → 127.0.0.1:11434 Ollama → immutable response/receipt → networkless validation → C19 review/approval`.
- Give the host runner access only to the selected `runtime/runs/JOB_ID` request, context, response, and receipt paths. It must not mount or edit `KnowledgeHub`, `.git`, `.obsidian`, model policy, or canonical apply paths, and it must not receive shell, tool, plugin, or autonomous writer authority. The response remains untrusted until the container rechecks schema, source and policy digests, model identity, output bounds, and replay state.
- Treat the local Ollama HTTP API as an unauthenticated loopback service. Require `OLLAMA_HOST=127.0.0.1:11434`, `OLLAMA_NO_CLOUD=1`, no wildcard origins, proxy, tunnel, LAN binding, or automatic pull. Preserve the existing no-fallback rule and reject cloud model names even when a host installation can resolve them.
- Start E02 with read-only host/service inspection. Record host platform and architecture, Ollama version, requested tag, resolved name, full manifest digest, file size, quantization, advertised and effective context, backend/device placement, processor split, model load state, and the internal-SSD model storage location. Do not treat the user's installation as repository or runtime evidence until the explicitly authorized inspection produces an `external_service` record.
- Separate internal-SSD cold-load, warm-load, and unload behavior. Measure model load time, prompt/evaluation throughput, first-token and total latency, peak host memory/VRAM, queueing, timeout, refusal, recovery, and digest drift. Keep one model and one request in the safe baseline; test concurrent generation and embedding only as a separate resource profile after the host has shown that both fit without unacceptable contention. Do not claim that the physical machine's resources are fully used without observed device and memory evidence, and do not compare storage devices in E02.

### E02 runner runtime and isolation decision

- The native Ollama macOS application and local server are independent of the KnowledgeOS Python runtime. The official macOS download lists macOS `14 Sonoma` or later and no Python prerequisite; the official `ollama-python` client release declares `requires-python >=3.8`. Python `3.12.8` therefore does not conflict with the installed Ollama client/server. Keep the project transport on the bounded stdlib HTTP client and do not add the Ollama Python SDK merely to reach the local API.
- The global mise defaults do not need to change. Keep the user's global Python `3.14.7` and uv `0.12.10` selections for other projects, and let the project-local `mise.toml` select Python `3.12.8` and uv `0.8.14` only inside KnowledgeOS. From the project root, `mise install` installs the versions declared by that project configuration and `mise exec -- ...` selects them for a command; do not use `mise use --global` for this lane. The project-local `[settings] python.github_attestations = false` is an explicit narrow exception because mise `2026.9.1` found no GitHub artifact attestation for the Python `3.12.8` precompiled artifact; keep the global attestation policy unchanged and record the exception as a supply-chain verification tradeoff. The tool artifacts may live in mise's user tool directory, but the global configuration and global version selections remain untouched. Keep project packages in the ignored `runtime/host-runner/.venv` environment with `UV_PROJECT_ENVIRONMENT=runtime/host-runner/.venv uv sync --locked --no-dev`; do not install project packages into the global Python site-packages. The exact pin remains declared by `mise.toml`, `ops/Dockerfile`, `ops/pyproject.toml`, and `ops/uv.lock`.
- Prefer the simpler one-shot host-native E02 runner for live host Ollama verification once the project-scoped mise pins are installed. The launcher accepts exactly one `runtime/runs/JOB_ID` spool, sets `OLLAMA_HOST=127.0.0.1:11434` and `OLLAMA_NO_CLOUD=1`, clears proxy and tunnel variables, calls the bounded stdlib transport directly, writes only private response and receipt artifacts, and exits after one job. It does not mount or edit `KnowledgeHub`, `.git`, `.obsidian`, model policy, or canonical apply paths; its path guards and private runtime scope are part of the acceptance contract.
- This host-native one-shot profile removes the relay, Unix socket, Colima/virtiofs socket forwarding, and extra `e02-live` image lifecycle. Its explicit tradeoff is weaker OS-level isolation than a container, so it remains limited to the E02 host verification lane and must never become a persistent provider process or an autonomous writer.
- Retain a pinned one-shot `e02-live` image as the isolated fallback when process-level host isolation is required. Build it from the same pinned Dockerfile contract, give it one private `runtime/runs/JOB_ID` bind mount, a read-only root filesystem, `tmpfs`, a non-root numeric user, dropped capabilities, `no-new-privileges`, and bounded CPU/memory/PID limits. Do not mount `KnowledgeHub`, `.git`, `.obsidian`, the model store, the Docker socket, or any canonical apply path.
- Reject `network_mode: host` and an unrestricted `host.docker.internal` exception even for the fallback: they broaden reachability and do not establish the project's loopback-only guarantee. The host-native one-shot plan is the simpler live path; the container path is an optional isolation profile, and neither path permits persistent E02 operation.
- Do not install `socat`, GPU SDKs, model files, or a global Ollama SDK as a shortcut. They do not remove the policy, digest, path, and output validation requirements. If project-scoped mise installation fails because of host filesystem permissions, stop at the runtime boundary and request that exact installation separately rather than silently using global Python `3.14.7`, system Python, or an unpinned environment.

## Local Ollama profile baseline

- Preserve this boundary: networkless core → immutable private runtime request → project-owned host runner → `127.0.0.1:11434` Ollama → immutable private response → networkless validation → review proposal → fresh human approval.
- Keep E02 production verification independent of external storage. If an experimental model exists only on an external SSD, record it as unavailable for E02; do not auto-mount, auto-download, silently fall back, or promote it until the model and its derived index are copied to the internal SSD and the complete E02 profile is rerun without the external SSD.
- Use `gemma4:12b` as the only permitted generation model identity across C30, E02, and later provider routes. Reject aliases, quantization or runtime variants, and silent rewrites; record the exact requested tag plus the locally resolved name and full digest when Ollama reports identity. The existing C30 disabled configuration remains a declaration baseline until this live identity is observed and approved.
- Start generation with `num_ctx=8192`, text-only input, `stream=false`, `think=false`, no tools, temperature `0`, a fixed evaluation seed, bounded output, `keep_alive=0`, one loaded model, one parallel request, and a small bounded queue. Add 16K and 32K context profiles only as measured comparisons; record requested and observed context separately and do not infer runtime capacity from Gemma's advertised maximum.
- Make `qwen3-embedding:8b-q4_K_M` the live default E02 embedding selector. It exposes the 8B model's 4096-dimension identity while using the user-approved Ollama-listed 4.7 GB Q4 artifact; use it only through the measured, loopback, internal-SSD boundary and the explicit serial activation policy.
  - Keep `embeddinggemma:300m-qat-q8_0` as the explicit `emergency_resource_fallback`. It is available when the Qwen resource profile is unacceptable, but it is not the default embedding selection.
  - The current E02 host scope contains only `gemma4:12b`, `qwen3-embedding:8b-q4_K_M`, and `embeddinggemma:300m-qat-q8_0`; uninstalled Qwen size variants and other embedding families are not E02 candidates in this branch.
- The default selection is therefore Qwen, with EmbeddingGemma opened only as a resource-emergency fallback. Selection remains evidence-gated by the frozen Korean/multilingual KnowledgeOS fixture plus host memory and latency measurements, not by public benchmark rank alone.
- Do not reuse the EmbeddingGemma prompt contract for Qwen3. Use an English query-side instruction such as `Given a KnowledgeOS search query, retrieve note passages that answer or directly support the query`; send documents as stable title/text content without a query instruction, set `truncate=false`, request an explicit dimension for any MRL comparison, normalize vectors, and use cosine similarity. Bind the exact query/document templates, dimension, normalization, model tag, full digest, parser/chunker, and indexer version into the index identity.
- Validate replacement of the C34 canonical model identity with the accepted Qwen `4096`-dimension index through the E02 temporary eligible-source build and preserve the separate C39 candidate path as an immutable audit artifact. Defer durable pointer materialization until KnowledgeOS implementation completion; EmbeddingGemma remains the separately named emergency fallback and is not loaded concurrently with Qwen.
- Accept the observed Gemma4 `24%` free-memory floor only under `serial_only`, `one_model_loaded`, `concurrent_requests=false`, and `unattended_activation=false`; reject concurrent or unattended activation.
- For the C39 embedding-only lane record generation refusal as `not_applicable`, evaluate the frozen contradiction fixture, and apply the `0.40` unknown-query abstention threshold to the top learned score.
- Compare lexical-only, E01 feature hashing, the frozen C34 learned baseline, each selected live embedding candidate, and RRF on the same privacy-filtered C34 fixture. Record Recall@1/3/5 or the fixture's equivalent prefix/required-path predicates, MRR or nDCG where applicable, Korean and multilingual error cases, citation/source freshness, cold and warm latency, throughput, peak memory/VRAM, device placement, truncation/refusal rate, index size, and replay/digest behavior. Promote only when the candidate improves the agreed retrieval gate without privacy leakage, stale-source acceptance, citation drift, or unsafe resource pressure.
- Require `OLLAMA_NO_CLOUD=1`, loopback-only binding, no proxy or tunnel, no wildcard origins, no automatic pull, and pre/post checks of Ollama version, full model digest, capability, quantization, and effective context.
- Keep provisioning separate from inference: installation, model download, live service inspection, and full-digest capture require explicit host/network authorization and produce `external_service` evidence under `E02`. A plan or checked-in model tag never proves that a host model is installed, reachable, GPU-resident, or healthy.
- Do not claim byte-deterministic model output. Reproducibility binds inputs, candidate set, schemas, policies, model identity, options, host profile, and validation outcome; quality is measured on frozen fixtures.

## Obsidian AI client decision

- Do not adopt a third-party AI plugin as the KnowledgeOS policy, retrieval, index, proposal, or write authority.
- Keep `vaultctl` and the project-owned runtime artifacts as the first executable baseline. A future `E05` client may own only question input, current-note or selection scope, citation opening, diff preview, and approve or reject controls.
- Route a thin client through an authenticated KnowledgeOS loopback broker, never directly to Ollama. The broker rechecks content and policy digests and C19 remains the only canonical apply path.
- Keep embeddings, prompts, responses, and session state in ignored private `runtime/`, not `.obsidian`, `.smart-env`, Git, or a synchronized Vault namespace.
- Use Local LLM Helper only as an isolated UX reference for source links, approval cards, and local chat; its plugin-owned index, synchronized settings, and approval state do not satisfy KnowledgeOS digest and receipt contracts.
- Use Smart Connections only as an independent read-only convenience experiment if separately authorized; never merge `.smart-env` output with the canonical runtime index or cite it as KnowledgeOS evidence.
- Do not adopt Copilot Agent modes, Local GPT write actions, auto-updating prompt packs, or any plugin-owned RAG pipeline as an authoritative route; their app-level permission prompts are not C19 proposal approval.
- Reject wildcard `OLLAMA_ORIGINS`, `OLLAMA_HOST=0.0.0.0`, direct plugin writes, autonomous agent or tool modes, synchronized secrets, and plugin-owned policy decisions.
- Require a measured CLI-friction result before implementing `E05`; plugin installation, Obsidian execution, and mobile behavior remain separate deployment and device evidence.

## External implementation references

- Confirm the current Gemma release and model shape against [Google Gemma releases](https://ai.google.dev/gemma/docs/releases) and the [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4).
- Confirm exact available tags at the time of provisioning from the [Ollama Gemma 4 tags](https://ollama.com/library/gemma4/tags), [Qwen3-Embedding tags](https://ollama.com/library/qwen3-embedding/tags), and [EmbeddingGemma tags](https://ollama.com/library/embeddinggemma/tags); bind the full local digest returned by the installed service rather than the abbreviated website value.
- Implement chat, schemas, embedding, context, keep-alive, loopback, and cloud-off behavior from the official [chat API](https://docs.ollama.com/api/chat), [structured outputs](https://docs.ollama.com/capabilities/structured-outputs), [embed API](https://docs.ollama.com/api/embed), and [FAQ](https://docs.ollama.com/faq).
- Keep the Qwen3 query instruction, document role, dimension, and multilingual assumptions aligned with the official [Qwen3-Embedding repository](https://github.com/QwenLM/Qwen3-Embedding) and [Qwen3-Embedding model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), and keep the existing compatibility control aligned with the [EmbeddingGemma model card](https://ai.google.dev/gemma/docs/embeddinggemma/model_card).
- Treat third-party plugins as unsandboxed application code under the [Obsidian plugin security boundary](https://github.com/obsidianmd/obsidian-help/blob/master/en/Extending%20Obsidian/Plugin%20security.md).
- Compare only UX patterns from [Local LLM Helper](https://github.com/manimohans/obsidian-local-llm-helper), [Smart Connections](https://smartconnections.app/smart-principles), [Copilot for Obsidian](https://docs.obsidiancopilot.com/llm-providers/), and [Local GPT](https://github.com/pfrankov/obsidian-local-gpt); none of these sources supersede project policy or evidence contracts.

## Deployment overlay lane

- `D01` — Configure Git identity and the production root sentinel after explicit confirmation.
- `D02` — Verify the Obsidian Mac Core profile from app-generated configuration.
- `D03` — Install and audit QuickAdd under its own approval gate.
- `D04` — Install and audit Templater under its own approval gate.
- `D05` — Install and audit Tasks under its own approval gate.
- `D06` — Install and audit Linter under its own approval gate.
- `D07` — Install and audit Obsidian Git under its own approval gate.
- `D08` — Verify iPhone and iPad Working Copy transport and the local result renderer after `D01` and `C11`.
- `D09` — Verify one live mobile bridge round trip after `D08` and `C17`.
- `D10` — Verify the C32 synthetic provider adapter across every route without claiming live-provider evidence.

## Optional extension lane

- `E01` — Evaluate local vector and RRF against the frozen `C22` baseline.
- `E02` — Verify the user-installed host-native `gemma4:12b` generation profile and selected local embedding candidates through the bounded host runner, full-digest capture, Korean/multilingual quality evaluation, contradiction and abstention coverage, and measured internal-SSD-only resource evidence; exclude external-SSD connection, mount, storage, and performance tests, and validate C34 canonical replacement through a temporary eligible-source build after the accepted Qwen promotion decision while defer durable pointer materialization until KnowledgeOS implementation completion.
- `E03` — Activate the provider-free LaunchAgent only after the `C36` consumer and rollback gates pass; keep `RunAtLoad=false` and provider queue consumption separately gated.
- `E04` — For the current branch, pursue local Ollama as the only provider lane and defer remote model routes; prepare and separately authorize local activation through C38-C43 while keeping queue, scheduler, LaunchAgent, and canonical apply effects independently gated.
- `E05` — Implement a removable thin Obsidian client only after `C35` and measured repeated CLI friction justify it.
- `P01` — Implement the canonical core-plugin and required-community-plugin setting-state registry, diagnostics, and evidence gates described below in its independent lane.
- `P02` through `P12` — Plan and verify one bounded Core or required-community-plugin setting family per session after P01, without treating the plan itself as Obsidian UI, plugin, device, or Git authorization.

## P01 — Core and required community-plugin setting-state contract

P01 exists because a plugin being installed, enabled, configured, executable, and healthy are different claims. It must also eliminate guessed setting names: a setting is authoritative only when the current plugin version exposes the exact serialized path or the exact live UI label. A missing setting is recorded as `unknown` or `unconfigured`; it is never synthesized into a JSON file because an earlier recommendation used a similar name.

### Objective and required profile

- Close the E06 through E10 installation-target labels at the installed Mac-profile boundary. Migrate their unfinished setting, runtime, and device verification into P01; do not keep E06 through E10 as active work items.
- Maintain one auditable Mac profile contract for the eleven required community plugins: `quickadd`, `templater-obsidian`, `obsidian-tasks-plugin`, `obsidian-linter`, `obsidian-git`, `homepage`, `note-toolbar`, `breadcrumbs`, `notebook-navigator`, `obsidian-meta-bind-plugin`, and `knowledgeos-thin-client`.
- Keep the first five roles unchanged: human capture routing, template rendering, task querying, bounded hygiene, and manual Mac Git UI. Register the new roles as startup experience, contextual command surface, typed-relation navigation, bounded navigation surface, low-risk property view, and proposal-only presentation client.
- Make `blueprint/blueprint.yaml#/plugin_profiles/mac_baseline` the exact Mac community-plugin allowlist. An unexpected community-plugin ID, a missing required ID, or a duplicate ID is a semantic failure. Treat `knowledgeos-thin-client` as an authorized proposal-only presentation adapter whose broker, provider, retrieval, and canonical apply authorities remain outside the plugin. Keep `plugin_profiles.mobile_baseline.community_plugins` empty.
- Preserve the plugin-free canonical path: Markdown/YAML notes, Bases, `vaultctl`, and human review remain usable when any adapter is disabled or unavailable.

### Authoritative sources and state vocabulary

- Read core enablement from the profile's `core-plugins.json`; read serialized core settings from the profile's `app.json`, `appearance.json`, `hotkeys.json`, `workspace.json`, and any core-owned files that the current Obsidian version actually writes. Do not treat a `true` flag as proof that a workflow is correct or that the app executed it.
- Read community installation from `community-plugins.json`, identity and version from each plugin's `manifest.json`, and serialized settings from that version's `data.json`. Use official plugin documentation to explain meaning, not to claim that a key exists in the installed build.
- Use a live UI inspection only for settings that are not serialized. Record the exact UI label, displayed value, Obsidian/plugin version, and inspection date. Codex does not operate the Obsidian UI under the project boundary; this is a user/device evidence step.
- Track at least these independent states for every component: `declared`, `installed`, `configured`, `enabled`, `verified`, `healthy`, and `fallback_available`. Map them to static, semantic, runtime, and device evidence rather than collapsing them into one boolean.
- Give every setting registry entry an owner (`core`, plugin ID, or user), profile (`mac` or `mobile`), source locator, current value, desired policy, allowed value/domain, dependency, fallback, mutation risk, verification method, and rollback action. Omit a field only when the source genuinely has no such concept.

### Core-plugin rules

- Treat Properties, Bases, Daily notes, Templates, Search, Backlinks, Outgoing links, Bookmarks, and File recovery as the cross-device core set already declared by the Blueprint. Treat Workspaces as Mac-only. Normalize each human name to its actual Obsidian ID before auditing.
- Classify every other serialized core flag separately as required, optional, or explicitly disabled. The current presence of Graph, Canvas, Tag pane, Outline, Word count, Sync, or another flag is an observation to review, not an automatic policy decision. P01 must not disable a core feature merely because it is outside the minimum set.
- Bind core settings to the workflow they protect: Properties visibility and frontmatter ownership, Daily notes/templates paths, Search/Backlink/Outgoing-link navigation, Bases views, Bookmarks, File Recovery, Workspaces, and hotkeys. If a value is UI-only or version-dependent, record the evidence requirement instead of inventing an `app.json` key.
- Require a safe fallback for each core component before changing its enabled state. For example, Notebook Navigator cannot justify disabling File Explorer until its navigation smoke test and an immediate core-explorer rollback are both recorded.

### Required community-plugin policy boundaries

P01 records policy boundaries first and accepts exact setting paths only after the installed version is inspected:

- QuickAdd: choices must resolve to Blueprint-owned capture/template targets; online or AI behavior, secrets, and unreviewed macros remain off; a choice that cannot be traced to a human-approved command is not required.
- Templater: the canonical template folder must be verified; system commands, shell execution, user scripts, and global new-file triggers remain off unless a separate decision authorizes them; folder/file template mappings must be exact and reversible.
- Tasks: query and status behavior must remain read-oriented except for an explicit human completion action; JavaScript queries and unbounded automation remain off; task date/status conventions must agree with the Vault schema.
- Linter: enable only an explicit rule allowlist after comparing it with the Property Dictionary and templates; bulk rewrite, YAML-key removal, or any rule that can destroy user-authored structure remains off until separately accepted.
- Obsidian Git: keep pull, push, auto-commit, auto-pull, and file-change backup automation off; retain manual Mac Git UI and status visibility; Git network effects remain separately authorized.
- Homepage: allow one approved startup target and a plugin-free fallback; verify open mode, view mode, auto-create, refresh, command list, and separate-mobile behavior only when those controls are exposed by the installed version; do not assume a setting from another release.
- Note Toolbar: allow only reviewed folder mappings and explicit human commands; scripting and external/system actions remain off; every toolbar action must identify its target, mutation class, and rollback path.
- Breadcrumbs: allow only Blueprint-approved typed relation fields; implied/transitive relations, automatic field creation, and unbounded external relation sources remain off until their exact behavior is verified; relation changes remain ordinary human-reviewed note edits.
- Notebook Navigator: use it as a bounded navigation adapter; record hidden folders/tags/properties and display scope explicitly; do not authorize bulk move/delete/property operations; retain File Explorer as the immediate fallback until device smoke evidence passes.
- Meta Bind: keep JavaScript and developer bypass modes off; allow only reviewed input/view/button declarations and approved property names; exclude templates and protected review/canonical paths from mutation; treat every write-capable control as an explicit human action requiring a rollback path.
- KnowledgeOS Thin Client: allow only the authenticated `127.0.0.1` broker presentation surface with digest-bound review controls; keep provider calls, retrieval/index ownership, canonical apply, token persistence, and arbitrary plugin writes outside the plugin authority.

The plugin-specific bullets define safety and ownership boundaries, not guessed configuration keys. A setting that is absent from `data.json` and absent from the current UI is not “configured to false”; it is `unconfigured` and blocks a claim that depends on it.

### Implementation and acceptance slice

- Add a version-aware, read-only plugin/core setting registry and extend `vaultctl plugins audit --profile mac` (or a separately named P01 command) to report installation, configuration, enablement, verification, health, and fallback states independently. It must never edit `.obsidian`, plugin data, notes, or Git state.
- Add schema and tests for exact eleven-plugin membership, duplicate/unexpected IDs, manifest/data locator validity, missing-setting handling, forbidden defaults, core-to-profile ownership, and mobile empty-plugin invariants. Keep fixtures independent of the user's dirty Vault profile.
- Record static profile evidence from the manifests and JSON files, semantic evidence from the Blueprint/schema/policy registry, runtime evidence only from an executed diagnostic or smoke path, and device evidence only from a user-authorized Mac inspection. Never promote one evidence class to another.
- Require rollback evidence for every setting mutation: save the original bytes or UI value, make one bounded change, rerun the relevant smoke check, and restore the exact previous value on failure. Do not batch unrelated plugin settings.
- Acceptance requires the exact eleven IDs in the Mac baseline, zero unexpected IDs, an explicit core-plugin policy for every serialized flag, no invented settings, all forbidden defaults disabled or explicitly unresolved, preserved plugin-free fallbacks, and separate `not_run` records for unperformed Obsidian runtime/device checks.

### Exclusions and handoff

- P01 does not install, uninstall, update, or enable plugins; change the user's Mac settings; operate Obsidian; change mobile community-plugin state; authorize Git network effects; or turn any plugin into a KnowledgeOS policy/apply authority.
- The next implementation slice starts with a read-only inventory of the current Mac profile, then adds the registry and diagnostics, then performs one plugin/core setting family at a time with static, semantic, runtime, and device evidence recorded separately in `PROJECT_STATE.md`.

- `PROJECT_STATE.md` alone records which slices are current or complete. This index declares execution order and acceptance boundaries only; it never authorizes provider, model-download, plugin, host-agent, device, remote, or Git effects, and learned retrieval remains opt-in until its quality gate passes.

## P02-P12 — Per-component setting-state planning sessions

P01 now owns the read-only registry, diagnostics, exact ten-plugin membership, Core ownership vocabulary, missing-setting classification, and evidence separation. The following sessions split the remaining setting-policy work into one bounded planning task per Core or required community-plugin area. Each label is a separate future session: adding these sections does not configure Obsidian, change plugin data, operate a device, or promote runtime/device evidence.

The default order is `P02 → P03 → P04 → P05 → P06 → P07 → P08 → P09 → P10 → P11 → P12`. Numeric order is local to the P lane; F sessions may consume a completed P contract but do not silently absorb its setting ownership.

### P02 — Define the Obsidian Core setting-state and fallback contract

- Dependencies: `P01`, `blueprint/blueprint.yaml#/plugin_profiles`, the installed `core-plugins.json`, `app.json`, `appearance.json`, `hotkeys.json`, and `workspace.json` observations.
- Plan one normalized Core setting registry for Properties, Bases, Daily notes, Templates, Search, Backlinks, Outgoing links, Bookmarks, File recovery, and Mac-only Workspaces. Bind each entry to its workflow, source locator, allowed domain, fallback, mutation risk, and verification method.
- Define the exact Daily Notes folder/date/template contract (`10_Journal/Daily`, the approved date format, and `99_System/Templates/T10_Daily.md`) and the Core Templates folder without inventing keys for UI-only values.
- Classify every other serialized Core flag as required, optional, or explicitly disabled. Do not disable Graph, Canvas, Tag pane, Outline, Word count, Sync, File Explorer, or another observed flag merely because it is outside the minimum set.
- Acceptance: every observed Core flag has an explicit policy; UI-only settings are `unknown` until exact UI evidence exists; Properties visibility and Daily Notes/template ownership are unambiguous; File Explorer and Markdown/Bases fallbacks remain available; mobile Core policy is not inferred from Mac state.
- Evidence: static and semantic registry checks. Obsidian runtime, device UI, and setting mutation remain `not_run` unless separately authorized.
- Exclusions: no Core plugin enable/disable, no Obsidian UI operation, no mobile change, no note rewrite, and no replacement of `vaultctl` validation authority.

### P03 — Define the QuickAdd capture-routing contract

- Dependencies: `P01`, `P02`, the Blueprint `quickadd_choices`, the template inventory, and the current QuickAdd manifest/data locator.
- Plan the primary Home capture choices: `CAPTURE_THOUGHT`, `NEW_IDEA`, `NEW_PROJECT`, `NEW_QUESTION`, and `NEW_KNOWLEDGE`. Register secondary Blueprint choices only when each has an exact human-approved template and target path.
- For every choice, record the command/choice identity, template, target pattern, create-only behavior, collision behavior, prompt fields, hotkey if any, and plugin-free fallback. Keep `99_System/Templates` as the only canonical template folder.
- Keep AI, online features, secrets, URI callbacks, unreviewed macros, shell/system execution, and arbitrary path selection out of the allowed domain. A choice with no traceable Blueprint target remains `unconfigured`, not guessed.
- Acceptance: each required choice maps to one Blueprint-owned target; no choice can overwrite or silently mutate an existing canonical note; primary Home actions and hotkeys have exact installed-version command evidence; the current zero-choice state is either deliberately configured or explicitly recorded as incomplete.
- Evidence: static `data.json`/manifest inspection and semantic target comparison; QuickAdd runtime and device capture smoke remain separate.
- Exclusions: no QuickAdd installation/update/enablement, no live capture, no AI/provider call, no URI callback, and no Vault mutation.

### P04 — Define the Templater bounded-rendering contract

- Dependencies: `P01`, `P02`, the Templater manifest/data locator, `T10_Daily.md`, `T11_Weekly.md`, `T12_Monthly.md`, and the F02 template contract.
- Plan the canonical template folder, exact installed-version setting labels, permitted template syntax, explicit invocation boundary, and ownership split between Core Daily Notes, Notebook Navigator, and Templater.
- Keep system commands, shell execution, user scripts, startup templates, arbitrary folder/file mappings, network, AI, Git, `vaultctl`, canonical apply, and global new-file triggers outside the allowed domain.
- Resolve the `{{...}}` versus Templater-expression boundary explicitly. If current period templates remain Notebook Navigator built-in templates, Templater stays manual/bounded; if F02 migrates a template to Templater syntax, record the exact one-time renderer path and prevent a second global trigger.
- Acceptance: the template owner and renderer are unique for each note type; the canonical folder is verified; all forbidden execution capabilities are disabled or explicitly unresolved; mappings are reversible; F02 can consume the contract without inventing a setting key.
- Evidence: static and semantic policy checks first; rendered fixture/runtime/device evidence belongs to F02 or a separately authorized UI session.
- Exclusions: no Templater installation/update/enablement, no template conversion, no Obsidian UI operation, no shell/script execution, and no existing-note rewrite.

### P05 — Define the Tasks query and human-completion contract

- Dependencies: `P01`, `P02`, the Vault task/status schema, the Tasks manifest/data locator, and the existing task query fixtures.
- Plan the global filter, status mapping, created/done/cancelled date policy, recurrence representation, auto-suggest behavior, query ownership, and human completion action.
- Keep task querying read-oriented. JavaScript queries, `filter by function` execution, unbounded automation, automatic canonical apply, and provider-backed task changes remain outside the allowed domain.
- Record whether an absent JavaScript setting is `unknown` or `unconfigured`; do not infer `false` from the absence of a serialized key. Keep `#task` and status conventions aligned with the Property Dictionary and note templates.
- Acceptance: every status and date behavior has a schema owner; query results do not become a write authority; JS/unbounded capabilities are off or explicitly unresolved; completion is an explicit human action; the Markdown task fallback remains valid.
- Evidence: static/semantic query and status registry checks; Tasks runtime and device completion smoke remain separate.
- Exclusions: no Tasks plugin operation, no bulk task rewrite, no automatic completion, no JavaScript query execution, and no external task service.

### P06 — Define the Linter bounded-hygiene contract

- Dependencies: `P01`, `P02`, the generated Property Dictionary, all canonical templates, and the installed Linter manifest/data locator.
- Plan a small, explicit, version-aware rule allowlist. Each candidate rule must identify the fields it can change, the affected note types, the destructive-risk class, the before/after evidence, and the rollback action.
- Keep lint-on-save, lint-on-file-change, bulk rewrite, YAML-key removal, tag migration, filename changes, timestamp overwrites, and rules that infer canonical structure off until separately accepted.
- Start from a safe all-rules-off/manual-only baseline. Do not invent rule IDs or enable a rule merely because its name appeared in an external Linter release.
- Acceptance: every enabled rule is traceable to the Property Dictionary/templates; no rule can remove user-authored structure; manual execution is the only default trigger; a one-file rollback exists; zero enabled rules is correctly reported as safe baseline rather than healthy hygiene completion.
- Evidence: static rule/data inspection and semantic allowlist review; Linter runtime/file-change evidence remains separate.
- Exclusions: no bulk lint, no automatic rewrite, no plugin setting mutation, and no modification of canonical notes during planning.

### P07 — Define the Obsidian Git manual-Mac contract

- Dependencies: `P01`, `P02`, the Git boundary in `blueprint/blueprint.yaml`, the Obsidian Git manifest/data locator, and the separately authorized Git identity/deployment policy.
- Plan manual status visibility, staged-set review, commit behavior, branch display, pull/push controls, and the exact separation between local UI configuration and Git network authorization.
- Keep auto-save, auto-commit, auto-push, auto-pull, pull-on-boot, pull-before-push, file-change backup, and unattended sync disabled. Record the fallback as ordinary Git or no Git operation, not as an automatic recovery path.
- Acceptance: every automation interval and boolean has a policy; status visibility remains available without network calls; pull/push are separately authorized; no setting implies that a successful local status check proves remote synchronization.
- Evidence: static/semantic settings and authorization checks; Git runtime/network/device evidence remains `not_run` unless separately authorized.
- Exclusions: no Git pull/push/commit, no identity mutation, no remote write, no automatic backup, and no plugin installation/update.

### P08 — Define the Homepage startup and plugin-free fallback contract

- Dependencies: `P01`, `P02`, the canonical `Home.md`, the Homepage manifest/data locator, and the Home/dashboard action inventory.
- Plan one Mac startup target, open mode, view mode, empty-state behavior, auto-create policy, refresh/command policy, and the explicit fallback path when Homepage is unavailable.
- Target `Home.md` with Reading view and replace-last-note behavior. Keep startup command lists empty unless a command is individually reviewed; do not let Homepage auto-run QuickAdd, Templater, Git, Sync, AI, shell, or canonical apply actions.
- Keep mobile independent: do not infer a mobile Homepage profile from Mac settings, and preserve a plain `Mobile.md`/Markdown fallback.
- Acceptance: one approved startup target exists; `Home.md` opens without Homepage; auto-create/refresh/commands/mobile values are exact installed-version evidence or explicitly unresolved; the Home action list points only to reviewed commands/files.
- Evidence: static/semantic Homepage registry checks; actual startup/view behavior is runtime/device evidence and remains separate.
- Exclusions: no Homepage installation/update, no startup UI operation, no auto-run command, no Dataview dependency, and no mobile profile mutation.

### P09 — Define the Breadcrumbs typed-relation contract

- Dependencies: `P01`, `P02`, the Blueprint relation registry, the Property Dictionary, and the Breadcrumbs manifest/data locator.
- Plan the exact edge fields, context/semantic groups, view set, link direction, and human review boundary. The allowed relation fields remain `projects`, `sources`, `related`, `supports`, `contradicts`, `explains`, `applies_to`, `derived_from`, `implements`, and `raises`.
- Keep implied/transitive relations, automatic field creation, external relation sources, arbitrary relation builders, and automatic inverse materialization off or explicitly unresolved until exact installed-version behavior is verified.
- Acceptance: every displayed relation has a Blueprint field owner; relation changes remain ordinary reviewed note edits; views are read/navigation surfaces; transitive or implied output cannot silently become canonical frontmatter; Backlinks/Outgoing links remain the fallback.
- Evidence: static/semantic edge-field and view registry checks; relation rendering and device navigation remain separate.
- Exclusions: no relation rewrite, no automatic field generation, no bulk note mutation, no plugin operation, and no device smoke claim.

### P10 — Define the Notebook Navigator bounded-calendar contract

- Dependencies: `P01`, `P02`, `P04`, the F02/F03 period-note contracts, the installed Notebook Navigator manifest/data locator, and the Core File Explorer fallback.
- Plan the exact weekly/monthly folder, filename pattern, template, template engine, calendar command, profile, hidden-scope, display-scope, confirmation, and existing-file behavior. Daily ownership remains Core Daily Notes even if Notebook Navigator can open daily notes.
- Align weekly/monthly navigation with `10_Journal/Weekly`, `10_Journal/Monthly`, `T11_Weekly.md`, and `T12_Monthly.md` through exact installed-version settings. Do not guess keys, token names, or command IDs from another release.
- Choose and record one template ownership mode. The recommended current baseline is explicit Notebook Navigator built-in rendering for current `{{...}}` tokens with Templater global trigger off; a Templater-owned mode requires the separate F02 syntax/fixture decision.
- Keep hidden folders/tags/properties explicit, bulk move/delete/property operations unavailable, confirmation enabled, and File Explorer as the immediate fallback.
- Acceptance: weekly/monthly mapping is represented by exact settings or an explicit incompatibility decision; daily duplication is rejected; template engine ownership is unique; calendar commands have version evidence; plugin-free navigation remains usable.
- Evidence: static/semantic mapping and ownership checks; actual creation/opening, no-overwrite, and device behavior belong to F03 or separately authorized runtime/device work.
- Exclusions: no Notebook Navigator setting mutation, no note creation/move/delete, no daily-note ownership change, no device smoke claim, and no File Explorer disablement.

### P11 — Define the Note Toolbar contextual-surface contract

- Dependencies: `P01`, `P08`, `P09`, `P10`, the current toolbar mappings, the Home action inventory, and the installed Note Toolbar manifest/data locator.
- Plan the primary Home/contextual actions for Today, weekly/monthly opening, capture, review, navigation, Backlinks, Outgoing links, Breadcrumbs, and Bases. Every action must resolve to a reviewed file link or exact installed command ID.
- Keep scripting, URI callbacks, shell/system actions, external processes, network, Git, AI, `vaultctl`, and approval-free canonical mutation off. Write-capable buttons must identify the human action, target property/file, mutation class, and rollback path.
- Keep Command Palette as recovery/diagnostic fallback only. Do not create a toolbar entry whose command ID is inferred from an older version or whose target cannot be verified.
- Acceptance: the intended normal journey starts from Home or a contextual toolbar; every mapping has a source/target/rollback record; toolbar configuration is presentation/navigation rather than authority; plugin-free fallback and Command Palette recovery remain available.
- Evidence: static/semantic mapping and capability checks; F04 owns actual GUI execution/device smoke and must consume this contract.
- Exclusions: no Note Toolbar scripting, no live toolbar editing, no external command, no canonical apply, and no device evidence from JSON alone.

### P12 — Define the Meta Bind low-risk property-view contract

- Dependencies: `P01`, `P02`, the generated Property Dictionary, approved note types, the Meta Bind manifest/data locator, and protected review/template paths.
- Plan the approved input/view declarations for `status`, `priority`, `next_action`, and `today_focus`, including allowed values, note-type scope, write target, mutation class, and rollback behavior.
- Keep JavaScript, developer mode, code-block restriction bypass, arbitrary buttons, shell/system actions, network, AI, Git, and automatic canonical apply off. Button templates remain empty until each button has a human-action contract.
- Verify folder exclusion semantics for `99_System/Templates`, `01_AI_Review/Pending`, and other protected paths using the installed version's exact UI label/value. Do not assume that a shorthand `templates` entry excludes the canonical `99_System/Templates` path.
- Acceptance: every input field maps to an approved property; no protected path exposes a write control; JS/dev bypass is off or explicitly unresolved; every write-capable control has a rollback path; YAML/frontmatter remains usable without Meta Bind.
- Evidence: static/semantic field and exclusion checks; actual property editing and device behavior remain separately authorized.
- Exclusions: no Meta Bind input/button execution, no protected-note mutation, no JavaScript, no plugin setting change, and no device smoke claim.

The P02-P12 sessions are planning and setting-state contracts. They do not supersede P01, F01-F06, D03-D07, or the user/device authorization boundary. A future implementation turn must start only one label, read `PROJECT_STATE.md`, preserve unrelated dirty changes, and update the machine-state evidence for that label only after its checks have actually run.

## F lane — GUI-first period notes and `vaultctl` boundary refactor

The F lane is a deliberate fork from the C/D/E/P lanes because it changes ownership between the KnowledgeOS CLI, Obsidian Core, Notebook Navigator, Templater, and Note Toolbar. It is a contract and implementation lane, not a new authority lane. F sessions preserve the following ownership:

- `C07` and the internal `template_engine.py` remain the deterministic renderer for general typed notes, capture, project, artifact, proposal, AI, bootstrap, and test fixtures. F does not delete or replace that engine wholesale.
- `C13` remains the owner of strict create-only general note creation. F removes only the period-note production writer and keeps general typed-note validation, collision checks, and overwrite prevention unchanged.
- `D04` remains the separately gated installation and audit boundary for Templater. F may define the Templater contract and static fixture requirements, but it does not install, enable, or configure the plugin.
- `P01` remains the owner of version-aware core/community-plugin setting state. F consumes P01 evidence for Notebook Navigator and Note Toolbar mappings and never invents serialized setting keys or UI command IDs.
- `E04` remains the independent local Ollama activation lane; remote model routes are deferred for the current branch. F does not activate a scheduler, provider, LaunchAgent, Git network effect, or background writer.
- `E05` remains the optional AI thin-client lane. The F05 status adapter is a bounded Obsidian connectivity diagnostic, not an AI client, retrieval route, provider transport, or canonical writer.

The normal human journey is GUI-first: Home or a contextual Note Toolbar button opens the approved Obsidian action; Obsidian Core owns daily creation; Notebook Navigator owns weekly/monthly creation and opening; Templater renders the bounded period fields; the user reviews the note; and `vaultctl note validate` is used when a contract check is needed. Command Palette remains a recovery and diagnostic fallback, never the intended primary journey. No F session operates Obsidian or changes a live plugin profile without separate user/device authorization.

### F01 — Remove the period writer and repair the public CLI contract

- Dependencies: `C07` template and note contracts, `C13` create-only local commands, the current Blueprint command registry, and the current period parser/dispatch/tests.
- Remove the `vaultctl period` parser, the `period create` dispatch, the production `create_period_note()` workflow, its production-only imports, and the semantic/public command entries that expose the writer. Remove references from external automation or LaunchAgent definitions when they are actual executable references; preserve historical plan evidence as history rather than silently rewriting it.
- Keep `vaultctl note create` strict and general-purpose. Daily, weekly, and monthly must remain rejected by `note create`; the rejection must state that those notes are created inside Obsidian through the approved GUI workflow and that `vaultctl note validate` validates an existing period note. Do not make `note create` a second period writer.
- Replace period-writer tests with contract tests for rejection, validation of a fixed GUI-rendered fixture, collision behavior, and preservation of ordinary typed-note creation. A deterministic test-only fixture builder is allowed only when it is not importable as a production workflow and does not become a second canonical writer.
- Acceptance: `vaultctl period create` is absent from the public command surface; daily/weekly/monthly `note create` requests fail closed with the approved message; ordinary typed-note creation still passes its existing contract; no production module imports or calls `create_period_note()`; and `vaultctl note validate` is the documented validation route for GUI-created period notes.
- Evidence: static parser/dispatch/semantic/Blueprint search; semantic command-surface check; runtime CLI rejection and note validation; artifact fixture validation; no Obsidian/device evidence in this session.

### F02 — Convert weekly/monthly templates to the bounded Templater contract

- Dependencies: `F01`, `C07`'s retained internal renderer, `D04`'s separately gated Templater profile, and the existing `T11_Weekly.md` and `T12_Monthly.md` contracts.
- Convert only the weekly and monthly GUI templates to verified Templater date/file expressions. Daily remains owned by Obsidian Core Daily Notes and keeps `T10_Daily.md` as the KnowledgeOS template reference; Notebook Navigator must not become a second daily-note owner.
- Preserve the weekly contract: `type: weekly`; ISO-week `id` and `title`; Monday `created`, `modified`, and `period_start`; Sunday `period_end`; Monday-to-Sunday daily links; existing summary markers; `ai_policy: ask`; and `ai_status: idle`.
- Preserve the monthly contract: `type: monthly`; `id: monthly-YYYY-MM`; `title: YYYY-MM`; first-of-month `created`, `modified`, and `period_start`; actual calendar-month `period_end`; and the existing review sections. Replace the unsupported or unverified `month_end` token rather than assuming Notebook Navigator or Templater compatibility.
- Keep Templater non-executing for this lane: no system commands, shell, user scripts, external processes, AI, network, Git, `vaultctl`, canonical apply, global new-file trigger, or approval-free edits to existing notes. Templater renders the new GUI-created document only.
- Use a saved Templater-rendered fixture or a test-only deterministic fixture builder for container tests. The fixture must pass `vaultctl note validate`; the container test must not invoke Templater or Obsidian.
- Acceptance: ISO year-boundary cases, leap/non-leap February, 30-day, and 31-day month cases pass; rendered IDs, titles, paths, period fields, and links match the KnowledgeOS contract; summary markers remain unchanged; same-period reopen behavior is represented as an open-existing/no-overwrite contract; and GUI/device execution remains separately `not_run` until authorized.
- Evidence: static template and forbidden-token inspection; semantic template/Blueprint/schema checks; runtime `vaultctl note validate` against rendered fixtures; artifact frontmatter/path/title evidence; no device evidence yet.

### F03 — Align Notebook Navigator mapping with the KnowledgeOS title and path contract

- Dependencies: `F02`, `P01` setting-state inventory, the installed Notebook Navigator version/manifest, and the existing `.obsidian-mac/plugins/notebook-navigator/data.json` observation.
- Define the weekly mapping as `10_Journal/Weekly/{iso_year}/{iso_year}-W{iso_week}.md` with the displayed and frontmatter title `YYYY-Www`. Define the monthly mapping as `10_Journal/Monthly/{year}/{year}-{month}.md` with the displayed and frontmatter title `YYYY-MM`.
- Replace the current observed custom patterns (`gggg/[W]ww` and `YYYY/YYYYMM`) only through exact version-supported Notebook Navigator settings. Do not guess a key, token, or command ID from another release. If the installed version cannot express the required folder/file mapping, block the setting change and record the incompatibility instead of reintroducing a CLI period writer.
- Keep weekly/monthly creation and opening in Notebook Navigator, with create-before-open confirmation enabled and existing-file behavior reduced to open-only. Keep File Explorer as the immediate fallback until device smoke evidence passes. Daily creation remains Core Daily Notes even if Notebook Navigator can navigate to a daily note.
- Treat this session's static configuration work and the later GUI verification as different evidence. A checked-in `data.json` proves only serialized configuration; it does not prove that Notebook Navigator created the note, used Templater, or preserved an existing file.
- Acceptance: the target mapping and title contract are represented by exact installed-version settings or an explicit blocked decision; old patterns are not treated as acceptable; no daily ownership duplication is introduced; and the device generation/open smoke remains separately `not_run` until user authorization.
- Evidence: static manifest/data inspection; semantic mapping-to-Blueprint comparison; runtime/artifact evidence only after a separately authorized GUI run; device evidence otherwise `not_run`.

### F04 — Make Note Toolbar and contextual buttons the primary user path

- Dependencies: `F03`, `P01`'s Note Toolbar state contract, the current toolbar mappings under `KnowledgeHub/.obsidian-mac/plugins/note-toolbar/data.json`, and the existing Home/dashboard action inventory.
- Inventory every intended repeated Obsidian action and assign it a primary button or contextual surface. At minimum, provide explicit GUI access for Today/Daily, Open weekly note, Open monthly note, Home, and the relevant review/navigation surfaces. Use file links for stable notes and command actions only when the exact installed command ID is verified.
- Treat Note Toolbar as a presentation and navigation layer, not a writer, policy engine, shell launcher, AI client, approval authority, or direct canonical mutation surface. Buttons must not execute arbitrary scripts, system commands, external processes, network calls, Git actions, `vaultctl`, or approval-free canonical mutation. A button that writes a property must remain an explicit human action with a declared mutation class and rollback path.
- Remove command-palette-first language from the intended journey in the relevant human-facing documentation at the close of the implementation slice. Keep Command Palette instructions only as a recovery/diagnostic fallback. Do not read or modify README files during planning; any README update belongs to session close and must be derived from verified state.
- Do not add placeholder toolbar commands. If Notebook Navigator does not expose a stable weekly/monthly command in the installed build, keep the action unconfigured/unknown and record the exact user/device verification needed. The fallback must remain usable without a community plugin.
- Acceptance: normal daily/weekly/monthly journeys begin from Home or a contextual toolbar button; all toolbar targets resolve to reviewed files or verified command IDs; no arbitrary execution capability is present; the fallback path is explicit; and toolbar/device execution evidence is separate from static JSON evidence.
- Evidence: static toolbar JSON and Blueprint inspection; semantic action/ownership/forbidden-capability checks; device smoke only after authorization; no claim that a button works from configuration bytes alone.

### F05 — Add one bounded internal Obsidian CLI status adapter

- Dependencies: `C12` diagnostics, `F01` public command cleanup, `P01` capability-state vocabulary, and the official Obsidian CLI behavior documented for the installed environment. `E05` may consume the status contract later but does not own it.
- Choose `vaultctl obsidian status` as the only initial public adapter command. It may report executable discovery, fixed CLI version output, current Vault identity, app connection state, and an allowlisted capability summary. `vaultctl doctor` may show this as a separately labeled overlay; it must not collapse app connection into plugin health or document-contract health.
- Build one internal adapter with fixed argv, no shell, bounded timeout, bounded stdout/stderr bytes, bounded exit-code handling, validated Vault identity, and fail-closed states for missing PATH entry, app-not-running, Vault mismatch, malformed output, timeout, and unexpected capability output. Do not expose raw command text, arbitrary argv, arbitrary file paths, or pass-through forms.
- Do not expose `read`, `write`, `create`, `append`, `search`, `command`, `eval`, plugin enable/disable/reload, or any equivalent writer/control operation. Do not use the official CLI to replace `vaultctl note validate`, `vaultctl note create`, `vaultctl fmt`, retrieval, citations, proposal approval, or canonical Markdown hashing.
- Remove direct `obsidian` invocations from external automation when they are actual executable paths; external pipelines call `vaultctl` only. Keep live app/PATH/Vault connection checks as deployment/device/external-service evidence requiring separate authorization; fake executables and deterministic adapter tests prove only the adapter contract.
- Acceptance: only `vaultctl obsidian status` is public; the adapter is the sole internal CLI call path; raw pass-through is impossible; bounded/fail-closed behavior is tested; no read/write/control API is exposed; and unavailable live app evidence remains `not_run` rather than being inferred from manifests.
- Evidence: static source/command-surface inspection; semantic argv/capability/path policy checks; runtime fake-executable adapter tests; deployment/device/external-service checks only when separately authorized.

### F06 — Integrate the GUI contract, validation, toolbar, and adapter evidence

- Dependencies: `F01` through `F05`, `C07`, `C12`, `C13`, `D04`, `E04`, `E05`, and `P01` without changing their ownership or authorization boundaries.
- Reconcile the Blueprint command list, semantic command registry, source imports, tests, LaunchAgent/automation references, period templates, plugin settings, toolbar mappings, and status diagnostics. Distinguish static, semantic, runtime, artifact, deployment, external-service, and device evidence in `PROJECT_STATE.md`.
- Verify the contract slice with the smallest canonical checks first, then the required source/Blueprint/schema/container/test/lint checks for the changed scope. Run test and lint sequentially. Run state validation after every state write and `git diff --check` in both control and `KnowledgeHub` roots.
- Record GUI generation/opening, Templater execution, existing-note no-overwrite, toolbar execution, app connection, and PATH/Vault identity as `not_run`, `blocked`, or `pass` according to actual authorized evidence. Static files and fake adapter tests must never be promoted to device or deployment proof.
- Acceptance: the final public surface has no period writer, weekly/monthly GUI artifacts validate through `vaultctl note validate`, daily ownership is Core Daily Notes, weekly/monthly ownership is Notebook Navigator, Templater remains bounded, toolbar is the primary intended GUI path, the Obsidian CLI is status-only behind the adapter, AI proposal/approval boundaries remain unchanged, and every unrun external/device class is explicitly recorded.
- Handoff: leave one executable next F slice, preserve the existing C/D/E/P goals and blockers, preserve all unrelated dirty control/Vault changes, and require separate user/device authorization before touching Obsidian UI or plugin settings.

### F lane sequencing and non-goals

The intended order is `F01 → F02 → F03 → F04 → F05 → F06`. `F02` can retain deterministic fixture support from `C07`; `F03` and `F04` consume `P01`'s exact setting-state evidence; `F05` extends the diagnostic boundary from `C12`; and `F06` closes the evidence contract. `E04` local Ollama activation, deferred remote routes, `E05` AI thin-client implementation, `D04` plugin installation/audit, and any device/UI action remain separately gated and are not silently pulled into an F session.

F explicitly excludes direct Obsidian manipulation, plugin installation or setting mutation, Notebook Navigator/Templater runtime claims from static files, official CLI document writes, a second production period writer, command-palette-first UX, automatic AI summary insertion, canonical apply, Git network effects, and any new provider or remote authority.
