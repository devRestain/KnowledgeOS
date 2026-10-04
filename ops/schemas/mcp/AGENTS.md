# Local MCP Records

## Definition and contents

- Use input and result schemas for the maintained local MCP interface.
- Use capabilities.json for its declared tool surface.

## Ownership

- Keep retrieval and proposal authority inside KnowledgeOS.
- Keep host paths and secrets outside public protocol fields.

## Use and maintenance

- Validate through the existing MCP schema/protocol cases when behavior changes.
- Maintain the four-tool v2 interface with Core ResourceReference selectors and separate execution/acceptance fields.
- Derive roots and actor authority from trusted startup settings and reject caller authority fields.
