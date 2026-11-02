# Obsidian Thin Client Source

## Definition and contents

- Use manifest.json, contract.json, main.js, and styles.css as the declared presentation package.
- Keep trusted local owner Work and review routes as the domain request boundary without login or HTTP tokens.
- Keep main.js self-contained because the native Obsidian evaluator resolves only external modules, not sibling CommonJS files.

## Ownership

- Keep note rendering separate from provider, index, Git, and canonical apply authority. Send a separate owner apply request only after human review.
- Preserve exact digest-bound request and policy checks.

## Use and maintenance

- Run make thin-client-check for bounded Node host-adapter behavior and canonical container acceptance for Owner contracts without treating either as native GUI proof.
- Keep device installation and app control separately selected.
