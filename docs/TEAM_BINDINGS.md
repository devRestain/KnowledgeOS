# Team and Officer bindings

[`ops/config/team-catalog.json`](../ops/config/team-catalog.json) defines the six dedicated Manager identities and candidate standalone Officers. [`ops/config/work-methods.json`](../ops/config/work-methods.json) defines their method contracts. The owner admits either a Team with its exact Manager or one standalone Officer. Shared teammate definitions can be reused only through a new owner-local executor and Profile binding.

An admitted Work pins its method digest, source ResourceReference, Profile, Graph lineage, criteria and budget. Its effective permission is the intersection of owner policy, Work scope, method stage and trusted Runner binding. A client cannot select actor, root or binding through MCP arguments. Explicit user pins have no silent fallback. Every effect uses the owner journal and can require reconciliation before continuation.

`vaultmcp` exposes all tool names to a general client, but Work-only calls require a trusted owner-issued context. A producing Manager, teammate or Officer cannot submit an independent EvalOfficer assessment for its own artifact. Human decision and canonical apply are distinct owner actions.

Current positive and negative evidence is source and isolated fixture evidence. Native Profile installation, Runner launch identity, per-call sandboxing, live WorkRun and cross-Operation reuse are pending adoption checks.
