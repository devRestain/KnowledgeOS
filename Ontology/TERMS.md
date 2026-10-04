# KnowledgeOS Domain Terms

This page is maintained authoring material, not a published domain package or executable registry. Existing meanings remain at `../blueprint/blueprint.yaml#/note_types` and `../blueprint/blueprint.yaml#/property_registry` under the Operation's Blueprint precedence. Generated `ops/policies/properties.yaml` is not an independent definition source.

## Reference existing definitions

Use a Blueprint path and exact selector when discussing an existing type or property. Retain its existing identifier; do not mint a new identifier merely to copy an established definition into this page.

## Author an additional term

For a selected new meaning, write an owner-reviewed entry with the following information. These are authoring prompts rather than a new wire schema.

| Information | Authoring requirement |
|---|---|
| Stable term identity | Choose a stable `knowledgeos.domain.*` identifier for the new meaning. |
| Label and definition | Write the display label and precise meaning independently; equal labels do not imply equivalence. |
| Owner and publisher | Identify KnowledgeOS and the responsible definition publisher. |
| Source | Link the authoritative definition path, exact selector, and source revision or content digest used. |
| Applicability | State which objects and purposes use the term and what remains unknown. |
| Relations and evidence | Reference applicable owned relation definitions and bounded evidence. |
| Review and use boundary | Reference owner review evidence and the contract change or later package adoption needed before operative use. |

The Core `SemanticTerm` record can express a namespaced term with a package pin, but its domain-namespace checks do not validate an entire domain package's contents. Do not invent a release digest or claim runtime registration from this page. Durable assertions and their reviews stay with the owner State or existing domain models; current Core assertion records retain their Experience source requirements.
