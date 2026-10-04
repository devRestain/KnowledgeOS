---
title: Define the hermetic regression boundary
status: resolved
assignee: codex
claimed: 2026-09-26
resolved: 2026-09-26
labels:
  - wayfinder:grilling
  - T01
parent: ../MAP.md
blocks:
  - Choose the system-artifact ownership model
  - Retire overlapping and historical test contracts
  - Design the ephemeral smoke lifecycle
---

# Define the hermetic regression boundary

## Question

Which repository and environment surfaces may each canonical test tier read or
write, and how will the implementation make an accidental dependency on the
real mutable KnowledgeHub fail immediately rather than silently becoming a new
snapshot contract?

The answer must classify control-repository sources, deterministic fixtures,
temporary Vaults, deployed `99_System` artifacts, hidden machine protocol
artifacts, the real user Vault, runtime storage, host services, and device/UI
state. It must also decide whether enforcement is structural, runner-level,
test-level, or a combination.

## Findings

- The canonical `dev` service exposes the real Vault at both
  `/workspace/control/KnowledgeHub` and `/workspace/KnowledgeHub`.
- It exposes the real runtime at both `/workspace/control/runtime` and
  `/workspace/runtime`.
- `docker compose run -v <container-path>` successfully overlays each of those
  four paths with an empty anonymous volume. A disposable probe saw no entries
  at any masked path.
- The canonical uid/gid (`501:20`) could not write a probe into the masked
  Vault; the write failed with `Permission denied`.
- A CLI bind intended to remount the whole control checkout read-only did not
  override the service's existing writable bind. The probe created one empty
  control-root file, which was immediately removed. The implementation must
  therefore use a Compose test override whose target-keyed volume declaration
  replaces the base mount; it must not rely on a second ad hoc parent bind.
- `network_mode: none` and the container-local `HOME` already supply useful
  network and host-home isolation, but neither prevents access to the mounted
  Vault or runtime.

## Resolution

Adopt four verification tiers with an explicit capability boundary.

### 1. Hermetic default regression

`make test` is the only canonical default pytest entrypoint. It may:

- read tracked KnowledgeOS control sources, Blueprint inputs, schemas,
  policies, prompts, actions, scripts, and deterministic test fixtures;
- create isolated control, Vault, profile, Git, and runtime trees only beneath
  pytest-managed temporary directories; and
- write only pytest temporary data and explicitly disposable tool caches.

It must not read or write:

- the real `KnowledgeHub` through either container alias;
- the real project `runtime` through either container alias;
- deployed root notes, `99_System` outputs, `.vault-bridge`, or any real
  `.obsidian*` profile;
- host application state, host services, devices, connectors, external
  accounts, or network services; or
- tracked control-repository files.

An isolated fixture may use the production names `KnowledgeHub` and `runtime`,
but its root must descend from `tmp_path`; it must be seeded only from declared
fixtures or deterministic generators. Copying the real Vault into a temporary
directory is forbidden because it preserves the dependency even though the
subsequent writes are isolated.

### 2. Generated-artifact reconciliation

Artifact parity is a separate explicit tier, not part of default pytest. It may
compare control-repository expected outputs with a later accepted allowlist of
deployed system outputs. Any deployed Markdown path in that allowlist must be
under `../../../../../../Vaults/KnowledgeHub/99_System`; ordinary root notes and user content are never
artifact inputs.

The artifact check is read-only. A generator/export command is a separate
explicit writer and may update only accepted system-owned targets. The exact
allowlist and the disposition of hidden bridge protocol copies belong to
[Choose the system-artifact ownership model](choose-the-system-artifact-ownership-model.md).

### 3. Serialized deployment diagnostics

Read-only commands such as a plugin audit may inspect the real serialized
profile only when explicitly invoked outside pytest. Their result is static or
deployment evidence, not regression or device proof. They may validate only
required capabilities and safety-critical fields; the precise invariant matrix
belongs to [Define open-world plugin invariants](define-open-world-plugin-invariants.md).

Profile mutation, plugin installation, and plugin execution remain outside this
tier.

### 4. Live, runtime, and device smoke

A live smoke is a separately named, separately authorized target. It may access
only the exact service/device surface under test and one collision-proof
temporary document namespace selected by
[Design the ephemeral smoke lifecycle](design-the-ephemeral-smoke-lifecycle.md).
It is never a dependency of `make test`, artifact reconciliation, or lint.

## Enforcement decision

Use all three enforcement layers; none is sufficient alone.

1. **Runner layer:** add a Compose test override for the existing `dev` service.
   It replaces the control bind with a read-only bind and overlays both real
   Vault aliases and both real runtime aliases with empty read-only anonymous
   volumes. Keep `network_mode: none`, the isolated container home, frozen/no-sync
   dependencies, bytecode disabled, and pytest's repository cache disabled.
2. **Session layer:** add a pytest session-start guard that requires an explicit
   hermetic-run marker, verifies all four masked paths are empty and not
   writable, and aborts before collection when the guard is absent or invalid.
3. **Source layer:** add one collection-time boundary scan that rejects default
   tests which derive `KnowledgeHub` or `runtime` from the real source root,
   invoke live diagnostics with the real root, use absolute live aliases, or
   copy the real Vault. Expressions rooted in `tmp_path` remain allowed.
4. **Fixture layer:** expose helpers named for source-only reads and isolated
   control roots. Do not expose a `live_vault` fixture to default tests.
5. **Command layer:** support focused execution through the guarded canonical
   entrypoint, for example `make test PYTEST_ARGS="tests/test_name.py"`. Raw
   `docker compose run ... pytest` is noncanonical because it can omit masks.
6. **Tier layer:** give artifact reconciliation, serialized deployment
   diagnostics, and live smoke distinct commands and evidence classes. None may
   be pulled into the default test dependency graph.

## Consequences

- The sixteen currently direct-coupled test files must be rewritten, merged, or
  deleted before guarded `make test` can pass.
- Generator and bootstrap behavior remains testable in memory or in disposable
  Vaults; only deployed-copy equality leaves default pytest.
- Existing dirty KnowledgeHub content becomes irrelevant to the default suite
  by construction rather than by an allowlist of tolerated changes.
- Accidental reads fail early because the real paths are masked; accidental
  writes fail at the mount boundary; source-pattern drift fails during
  collection.
- Focused development runs remain possible without weakening the boundary.
- [Choose the system-artifact ownership model](choose-the-system-artifact-ownership-model.md),
  [Design the ephemeral smoke lifecycle](design-the-ephemeral-smoke-lifecycle.md),
  and the hermetic portion of
  [Retire overlapping and historical test contracts](retire-overlapping-and-historical-test-contracts.md)
  are now unblocked.

## Evidence

- `ops/compose.yaml` — observed the four real Vault/runtime mount aliases and
  network-disabled `dev` service.
- `docker compose run --help` — confirmed per-run volume overlays are supported.
- Disposable four-volume `find` probe — returned no entries with exit code 0.
- Disposable masked-Vault `touch` probe — failed with `Permission denied`.
- Disposable parent `:ro` remount probe — demonstrated that an ad hoc CLI parent
  bind does not replace the existing writable service bind; the resulting empty
  probe file was removed and the control status restored.
- `planning/wayfinder/test-architecture/BASELINE.md` — identified the sixteen
  direct real-Vault test files and the current 430-test baseline.
