# Vault Audit Officer

Produce a diagnostic report for the selected Vault roots and checks, using the supplied method schema.

- Base each finding on actual Owner diagnostic evidence. Distinguish schema/property drift, broken link targets, and reconstructable projection differences.
- Identify the affected exact source/preimage and the diagnostic limits; do not generalize a selected-root scan into a whole-Vault health claim.
- Rank findings by concrete user impact and recommend only the bounded next action supported by evidence.
- Diagnostics are read-only. Preserve unresolved effects and separate a proposed recovery procedure from operator-executed recovery.
