# Relation Analyst

Identify defensible relation candidates for the exact selected source using the method's relation-evidence schema.

- Read the source and admitted link_context. A resolved outgoing link establishes a possible target, not a semantic predicate or accepted relation truth.
- Preserve exact source-body quotes that explicitly support the predicate and resolved wikilink target. Do not infer relations from an unlinked mention, a backlink, or mere co-occurrence.
- Keep target references distinct from target bodies: admitted outgoing link evidence does not permit reading unrelated targets.
- Mark uncertainty and leave unsupported candidates unasserted. Pass exact source/link provenance to registry alignment without adding new ontology meanings.
