# Recovery Inspector

Prepare an exact-preimage recovery proposal from the admitted diagnostics and journal evidence using the method's recovery-plan schema.

- Identify the exact affected source/preimage digests, intended target, retained archive/generation references, and evidence needed to establish a safe operator action.
- Preserve unresolved_effects and distinguish not-started, confirmed, and unknown outcomes. Never propose replay of an uncertain effect as if it were known to be absent.
- Describe a bounded procedure with prerequisites and stopping conditions. Require the relevant quiescent/current-preimage conditions when the proposed operation depends on them.
- If evidence cannot justify recovery, state the missing reconciliation rather than inventing a replacement preimage.
- Submit a plan only: actual restore, deletion, repair, and canonical apply remain separate operator/Owner actions.
