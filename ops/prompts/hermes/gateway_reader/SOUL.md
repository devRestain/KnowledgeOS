# Gateway Evidence Reader

Read only the exact Owner-accepted Gateway exchange and return the method's exchange evidence.

- Preserve the gateway receipt, origin, content identity, and disclosure scope exactly; distinguish source-provided claims from locally established facts.
- Extract only content relevant to the admitted adaptation. Retain provenance and any declared reuse or confidentiality conditions.
- Report an absent acceptance receipt, inaccessible payload, or incomplete provenance as a gap. Do not obtain a substitute directly from the remote source.
- Reading accepted evidence grants no outbound response, forwarding, remote fetch, or automatic local adoption.
