# KnowledgeOS Domain Relations

Use `../blueprint/blueprint.yaml#/relation_registry` and `#/property_registry` as the existing executable relation sources. `../ops/policies/relations.yaml` is a generated policy copy. Read the source for exact predicates, permitted endpoints, inverse aliases, context edges, and provenance rather than copying those definitions here.

## Preserve current meanings

Keep canonical relation, context relation, and ordinary wikilink meanings distinct according to the Blueprint. Its internal inverse aliases are declared local behavior; they do not establish a cross-Operation alignment. A proposal confidence score or a delivery receipt is not a canonical knowledge claim.

## Author an additional relation

These prompts describe an owner proposal and do not define a new public record format.

| Information | Authoring requirement |
|---|---|
| Stable relation identity | Assign an owner-scoped identifier without renaming an existing Blueprint predicate. |
| Definition | State the relation's intended meaning and purpose. |
| Direction | Declare subject-to-object direction; do not assume symmetry or inverse materialization. |
| Endpoint kinds | Identify the permitted subject and object types and their authoritative source selectors. |
| Evidence and provenance | Reference supporting sources and distinguish authored, proposed, and canonical observations. |
| Applicability and exclusions | State when the relation applies and what absence cannot establish. |
| Review and use boundary | Reference owner review and any Blueprint contract change required for operative use. |

Core `derivedFrom` and KnowledgeOS `derived_from` retain their respective definitions. Similar spelling is not an adopted transform or permission to substitute one for the other. Put any actual cross-domain mismatch and mapping proposal in ALIGNMENTS. No ontology relation grants read, export, execution, or canonical mutation authority.
