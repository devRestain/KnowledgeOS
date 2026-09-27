---
title: Set verification tiers and the regression budget
status: resolved
assignee: codex
claimed: 2026-09-27
resolved: 2026-09-27
labels:
  - wayfinder:grilling
  - T01
parent: ../MAP.md
blocked_by:
  - Choose the system-artifact ownership model
  - Define open-world plugin invariants
  - Retire overlapping and historical test contracts
  - Design the ephemeral smoke lifecycle
blocks:
  - Accept the implementation-ready T01 contract
---

# Set verification tiers and the regression budget

## Question

Which checks belong in the fast default regression, generated-artifact
reconciliation, container integration, and separately authorized live/device
tiers, and what count, duration, isolation, mutation, and failure-locality budget
must the rebuilt suite satisfy?

The answer must name canonical commands, marker or directory boundaries,
sequential execution constraints, expected evidence classes, and invariance
experiments. It must avoid treating a smaller test count as success unless
retained responsibilities and failure detection are explicit.

## Findings

- Three canonical baseline runs completed in 85.48, 88.95, and 96.02 seconds;
  the median is 88.95 seconds. The latest profiled run passed all 431 cases.
- No individual case exceeded two seconds. The cost is distributed across
  repeated control-tree materialization and many valid integration boundaries,
  not one pathological test.
- Twenty-seven files currently copy the whole control checkout into a temporary
  tree. This repeated broad setup is unnecessary when each test can declare its
  Blueprint, policy, schema, fixture, Vault, and runtime inputs.
- The accepted responsibility matrix reduces file count and snapshot overlap,
  but test count alone cannot represent safety coverage. Runtime, source size,
  forbidden inputs, and stable failure locality provide better budgets.
- Pytest markers cannot make a live or deployed test safe: a forgotten marker
  expression can still collect it. External tiers therefore require distinct
  Make targets and must not live in the default pytest tree as live tests.

## Resolution

Adopt five command-separated verification tiers. All repository tests and
test-only support remain beneath `ops/tests`; every pytest invocation is rooted
in the repository Makefile.

| Tier | Canonical commands | Inputs and effects | Evidence class |
|---|---|---|---|
| Control trust | `make source-check`, `make verify`, `make blueprint-check`, `make schema-check` | Read control sources only; no real Vault, runtime, profile, network, service, or device access | Static, semantic, and control-artifact |
| Hermetic regression | `make test`; focused `make test PYTEST_ARGS="..."`; `make test-invariance` | Read-only control source mount, disposable caches and `tmp_path`, empty nonwritable masks over both real Vault aliases and both runtime aliases, no network | Runtime in the isolated container |
| Container equivalence | `make container-source-check`, `make container-verify` | Prove the packaged control source and control foundation only; no deployed Vault inspection | Container runtime |
| Explicit deployment inspection | `make vault-artifact-check`, `make profile-check` | Read-only named `99_System` parity or open-world serialized profile inspection; never a prerequisite of control or regression targets | Artifact or serialized deployment |
| Authorized live smoke | `make live-smoke SMOKE_ACTION=<accepted-action>` | Exact authorized service/device plus one UUID-bound `99_System/Smoke` note and private recovery journal | Live runtime, external-service, or device |

`make lint` uses the same hermetic Compose override as pytest and runs after the
test command, never concurrently. Add `make acceptance` as a sequential recipe
that invokes source, control foundation, Blueprint, control schema, container
source, container foundation, test, and lint checks in that order. It excludes
deployment inspection and live smoke.

### Boundary implementation

1. Add `ops/compose.test.yaml` and address volume entries by target so the base
   control, Vault, and runtime mounts are replaced rather than shadowed.
2. Mount the control source read-only. Give pytest only `/tmp`, an isolated
   container home, and disposable uv/pytest caches as writable locations.
3. Mask `/workspace/control/KnowledgeHub`, `/workspace/KnowledgeHub`,
   `/workspace/control/runtime`, and `/workspace/runtime` with empty read-only
   volumes. Keep `network_mode: none`.
4. Require the Make entrypoint marker and a hermetic-run marker. Before
   collection, verify all four aliases are empty, nonwritable, and distinct from
   every pytest temporary root.
5. Run a collection boundary scan that rejects source-root Vault/runtime
   derivation, absolute live aliases, copying the real Vault, raw live
   diagnostics, and whole `shutil.copytree(CONTROL_ROOT, ...)` setup.
6. Replace all 27 whole-control copies with declared minimal inputs through
   `ops/tests/support/control_factory.py`.
7. Use no `live`, `device`, or `deployment` pytest marker. Those capabilities
   are absent from default pytest and exist only as separate Make commands.

### Structural budget

The rebuilt default suite must satisfy all of these hard gates:

- zero reads or writes to the four real Vault/runtime aliases;
- zero direct real-Vault references in collected test source;
- zero whole-control or real-Vault `copytree` calls;
- no real `.obsidian*` profile fixture and no total plugin-count assertion;
- at most 57 `test_*.py` files after the five planned replacement files;
- at most 11,750 Python lines beneath `ops/tests`, including support modules but
  excluding generated cache files; and
- no newly added test without a responsibility named in the accepted matrix or
  an explicit update to that matrix.

Collected case count has no independent pass threshold. Parametrized cases may
increase when they improve failure locality, but final handoff must explain the
net count and map every addition and deletion to a responsibility.

### Runtime budget

Measure on the same canonical `dev` image and host used for the 88.95-second
median baseline, after one unmeasured warm-up run:

- two consecutive `make test` runs must each finish in at most 75 seconds;
- their arithmetic mean must finish in at most 72 seconds, a material reduction
  from the baseline median;
- `make test-invariance` must finish in at most 15 seconds;
- no individual pytest call may exceed two seconds without a recorded reason
  and a distinct integration responsibility; and
- a focused single-file Make run must start, collect, and report the named file
  without running unrelated tests.

If host contention invalidates a timing run, record it as unmeasured and repeat;
do not delete safety coverage to satisfy a noisy measurement.

### Required invariance experiments

`make test-invariance` runs only synthesized temporary Vaults and profiles. It
must prove unchanged KnowledgeOS results after each independent mutation:

1. add one ordinary note;
2. delete one ordinary note;
3. edit an ordinary note body and frontmatter;
4. add an unrelated community-plugin ID and benign directory;
5. add unrelated keys and records to a required plugin data file;
6. reorder records and change an advisory version or presentation preference;
7. add a future unrelated Core flag; and
8. place unrelated bytes in the disposable Vault alias presented to the runner.

The last experiment proves the mask and source boundary, not tolerance of a
specific real-Vault snapshot. Record real KnowledgeHub status before and after
acceptance to prove it was not mutated.

### Failure locality budget

- An ordinary note or unmanaged plugin mutation produces no failure.
- A missing required plugin degrades only its declared capability.
- A safety-critical owned setting produces one stable lane-specific failure
  code without echoing arbitrary profile data.
- A control artifact mismatch fails `make schema-check` and identifies one
  control path.
- A deployed `99_System` mismatch fails only `make vault-artifact-check`; it
  cannot fail `make test` or control verification.
- A smoke identity, journal, marker, or sealed-digest mismatch fails only the
  explicit smoke, preserves the note, and requests exact confirmation.

## Consequences

- The default gate becomes both structurally hermetic and measurably lighter.
- Deployment drift remains observable without coupling ordinary development to
  the user's active Vault.
- Test and lint remain sequential, while focused Make runs preserve efficient
  local iteration.
- The implementation-ready contract ticket is now unblocked.

## Evidence

- `make test PYTEST_ARGS='--durations=40 -q'` — 431 passed in 85.48 seconds;
  slowest call 1.56 seconds.
- Earlier canonical runs — 430 passed in 88.95 seconds and 431 passed in 96.02
  seconds.
- `rg` inventory on 2026-09-27 — 27 whole-control `copytree` implementations,
  16 direct real-Vault files, 67 test files, and 13,143 test-file lines.
- The resolved hermetic boundary, artifact ownership, plugin invariants,
  responsibility matrix, and smoke lifecycle tickets — accepted tier inputs and
  effects.
