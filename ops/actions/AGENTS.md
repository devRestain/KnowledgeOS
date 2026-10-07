# Owner Proposal Action Inputs

## Definition and contents

- Use these JSON definitions only where the owner proposal path still consumes exact action inputs.
- Keep active action contracts aligned with owner methods, prompts and schemas; do not infer a CLI or provider route from a file name.

## Ownership

- Keep agent results Pending until owner review and a separate canonical apply.
- Keep arbitrary shell and implicit effects outside action inputs.

## Use and maintenance

- Inspect the action consumer and Work method before changing a definition.
- Use workspace Tmp for disposable action experiments.
