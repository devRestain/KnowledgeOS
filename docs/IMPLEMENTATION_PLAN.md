# Agent Execution Pipeline

- Read `PROJECT_STATE.md` before selecting or promoting a slice.
- Resolve conflicts in `OBSIDIAN_VAULT_BLUEPRINT.md`, `blueprint/blueprint.yaml`, then `OBSIDIAN_VAULT_WHITEPAPER.md` order.
- Implement one acceptance-gated capability per session and preserve the isolated boundaries below.
- Use fresh lane-local labels: `Cxx` for ordered core capabilities, `Dxx` for gated deployment overlays, and `Exx` for optional extensions.
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
- `C34` — Implement an immutable EmbeddingGemma index and learned-retrieval quality gate.
- `C35` — Implement schema-constrained Gemma 4 answer and proposal routes without direct Vault mutation.
- `C36` — Implement a recoverable one-shot provider queue consumer while background activation stays disabled.

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
  - Keep learned retrieval opt-in until `E02` supplies live-model accuracy, privacy, citation, staleness, latency, and memory evidence. Fake-server tests cannot satisfy that promotion gate.
- `C35` depends on `C26`, `C27`, `C31`, `C33`, and `C34`.
  - Add schema-constrained cited-answer, triage, draft, link, and normalize routes using frozen evidence and proposal-only outcomes.
  - Reject unexpected tool calls or reasoning payloads, do not retain chain-of-thought, and independently validate JSON despite Ollama structured-output support.
  - Verify route orchestration with deterministic recorded responses in C35; require `E02` to evaluate refusal, abstention, citation faithfulness, stale-source rejection, prompt injection, Korean quality, latency, and memory before promoting any live route.
- `C36` depends on `C28`, `C32`, and `D10`.
  - Extend C24 with an idempotent one-shot consumer using atomic claim and lease, bounded concurrency, explicit terminal states, digest-conflict quarantine, and crash recovery.
  - Adopt only a validated response envelope and publish only through the existing C17 exact-response path.
  - Keep LaunchAgent installation, live provider access, mobile transport, and Git network effects inactive until their own gates.

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
- Use the user's selected generation tag `gemma4:12b` as the E02 candidate. Do not silently rewrite it to `gemma4:12b-it-q4_K_M`, `gemma4:12b-mlx`, `gemma4`, or `latest`; record both the requested tag and the locally resolved name/digest when Ollama reports an alias or variant. The existing C30 disabled configuration remains a declaration baseline until this live identity is observed and approved.
- Start generation with `num_ctx=8192`, text-only input, `stream=false`, `think=false`, no tools, temperature `0`, a fixed evaluation seed, bounded output, `keep_alive=0`, one loaded model, one parallel request, and a small bounded queue. Add 16K and 32K context profiles only as measured comparisons; record requested and observed context separately and do not infer runtime capacity from Gemma's advertised maximum.
- Make Qwen3-Embedding the primary E02 embedding family because it supports Korean and more than 100 languages, instruction-aware query encoding, flexible dimensions, and a quality/size ladder:
  - `qwen3-embedding:8b-q4_K_M` is the quality candidate. It exposes the 8B model's 4096-dimension identity while using an Ollama-listed 4.7 GB Q4 artifact; use it when the host's measured memory and generation contention budget permit.
  - `qwen3-embedding:4b-q8_0` is the balanced operational baseline. It exposes the 4B model's 2560-dimension identity and is the first candidate to try when Gemma 4 12B and embedding work must share the host without serializing every interaction.
  - `qwen3-embedding:0.6b-q8_0` is the low-resource control. It provides a 1024-dimension, 32K-context profile for measuring the quality/latency/storage floor, not the recommended final quality target.
  - Keep `embeddinggemma:300m-qat-q8_0` as the existing C34 compatibility control and add `bge-m3:567m` only as an optional multilingual/dense-sparse comparison. BGE-M3 is useful if later retrieval needs its multi-functionality, but its 1024-dimension dense result alone does not replace the Qwen quality candidate.
- Recommendation for E02 is therefore `qwen3-embedding:8b-q4_K_M` for maximum expected retrieval quality and `qwen3-embedding:4b-q8_0` as the likely day-to-day default if co-residency with `gemma4:12b` matters. The final selection is evidence-gated by the frozen Korean/multilingual KnowledgeOS fixture plus host memory and latency measurements, not by public benchmark rank alone.
- Do not reuse the EmbeddingGemma prompt contract for Qwen3. Use an English query-side instruction such as `Given a KnowledgeOS search query, retrieve note passages that answer or directly support the query`; send documents as stable title/text content without a query instruction, set `truncate=false`, request an explicit dimension for any MRL comparison, normalize vectors, and use cosine similarity. Bind the exact query/document templates, dimension, normalization, model tag, full digest, parser/chunker, and indexer version into the index identity.
- Keep C34's current `embeddinggemma:300m-qat-q8_0`, 768-dimension schema, generated artifact, and fake-provider tests frozen during this planning change. An accepted Qwen or BGE-M3 result requires a separate implementation slice to revise the C34 model validator, schema constants, algorithm label, generated configuration, prompt templates, rebuild-on-identity-drift rules, and tests; E02 must not mutate the canonical index in place.
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
- Confirm exact available tags at the time of provisioning from the [Ollama Gemma 4 tags](https://ollama.com/library/gemma4/tags), [Qwen3-Embedding tags](https://ollama.com/library/qwen3-embedding/tags), [BGE-M3 model](https://ollama.com/library/bge-m3), and [EmbeddingGemma tags](https://ollama.com/library/embeddinggemma/tags); bind the full local digest returned by the installed service rather than the abbreviated website value.
- Implement chat, schemas, embedding, context, keep-alive, loopback, and cloud-off behavior from the official [chat API](https://docs.ollama.com/api/chat), [structured outputs](https://docs.ollama.com/capabilities/structured-outputs), [embed API](https://docs.ollama.com/api/embed), and [FAQ](https://docs.ollama.com/faq).
- Keep the Qwen3 query instruction, document role, dimension, and multilingual assumptions aligned with the official [Qwen3-Embedding repository](https://github.com/QwenLM/Qwen3-Embedding) and [Qwen3-Embedding model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B); keep the BGE-M3 fallback aligned with the [BAAI model card](https://huggingface.co/BAAI/bge-m3), and keep the existing compatibility control aligned with the [EmbeddingGemma model card](https://ai.google.dev/gemma/docs/embeddinggemma/model_card).
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
- `E02` — Verify the user-installed host-native `gemma4:12b` generation profile and selected local embedding candidates through the bounded host runner, full-digest capture, Korean/multilingual quality evaluation, and measured internal-SSD-only resource evidence; exclude external-SSD connection, mount, storage, and performance tests, and keep the networkless container and canonical C34 contract unchanged until promotion passes.
- `E03` — Install the LaunchAgent only after the `C36` consumer and rollback gates pass.
- `E04` — Activate each remote or unattended lane through a separate decision and authorization gate.
- `E05` — Implement a removable thin Obsidian client only after `C35` and measured repeated CLI friction justify it.

- `PROJECT_STATE.md` alone records which slices are current or complete. This index declares execution order and acceptance boundaries only; it never authorizes provider, model-download, plugin, host-agent, device, remote, or Git effects, and learned retrieval remains opt-in until its quality gate passes.
