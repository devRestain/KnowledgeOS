# Hermetic Test Support

## Definition and contents

- Use this package for deterministic control/plugin factories and snapshots.
- Keep shared scaffolding reusable across maintained Operation tests.

## Ownership

- Keep these official helpers with the Operation.
- Build synthetic inputs without reading real Vault or Runtime data.

## Use and maintenance

- Follow the parent test guide and Make entrypoints.
- Place disposable verification experiments in workspace Tmp instead of expanding production fixtures.
