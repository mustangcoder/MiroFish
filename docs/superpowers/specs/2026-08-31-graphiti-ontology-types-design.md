# Graphiti Ontology Types Design

## Goal

Make the local Graphiti backend apply MiroFish ontology definitions during extraction so Neo4j entity nodes retain real business-type labels and downstream persona generation can distinguish people, institutions, regions, and other entity classes.

## Current Failure

`GraphBuilderService.set_ontology()` passes raw entity and edge definitions to the backend adapter. The local `GraphitiClient.set_ontology()` stores those definitions in an in-memory dictionary but `add_episode()` and `add_episode_bulk()` omit Graphiti's `entity_types`, `edge_types`, and `edge_type_map` parameters. Graphiti consequently extracts every node as the default `Entity` type.

The simulation compatibility fallback then labels all such nodes `GenericEntity`. `OasisProfileGenerator` treats every type not present in its individual whitelist as an institution, causing all profiles to use the institutional defaults `age=30`, `gender=other`, and strong ISTJ/China bias.

## Ontology Compiler

Add a focused compiler that converts the raw MiroFish ontology into an immutable Graphiti ontology bundle:

- `entity_types: dict[str, type[pydantic.BaseModel]]`
- `edge_types: dict[str, type[pydantic.BaseModel]]`
- `edge_type_map: dict[tuple[str, str], list[str]]`
- `custom_extraction_instructions: str`

Entity and edge names must be valid identifiers matching `^[A-Za-z][A-Za-z0-9_]*$`. Invalid names are normalized to identifiers; duplicate normalized names are rejected rather than silently overwritten.

Attributes are normalized with the repository's existing ontology helpers. Attribute names that collide with Pydantic members or reserved graph properties are prefixed. All extracted custom attributes are optional strings with a field description. Type descriptions become Pydantic model docstrings because Graphiti uses `__doc__` in its extraction prompt.

Edge source-target definitions populate `edge_type_map`. References to unknown entity types are rejected with a readable validation error. Multiple edge types for the same source-target pair are accumulated without duplicates.

Custom extraction instructions explicitly require selecting the most specific supplied entity type and reserve the default `Entity` type for genuinely unmatched concepts.

## Adapter Integration

`GraphitiClient.set_ontology()` compiles and caches a bundle per graph ID instead of raw JSON.

Both `add_episode()` and `add_episode_batch()` fetch the graph's compiled bundle and pass all four Graphiti ontology arguments. A graph without a configured ontology continues to use Graphiti defaults.

The cache remains process-local because graph construction calls `set_ontology()` immediately before adding episodes. After a backend restart, rebuilding a project calls `set_ontology()` again from its persisted project ontology. Type labels themselves persist in Neo4j.

## Downstream Behavior

Newly built nodes carry labels such as `Entity + Person`, `Entity + Investor`, or `Entity + ListedCompany`. `ZepEntityReader` uses these labels directly. The `GenericEntity` fallback remains only for historical graphs or unmatched nodes when an entire graph has no custom labels.

No heuristic retyping of existing nodes is attempted. Existing graphs must be force rebuilt from their source corpus and persisted ontology. Simulations based on the rebuilt graph must regenerate profiles.

## Persona Entity-Kind Classification

Preserving ontology labels is necessary but not sufficient. `OasisProfileGenerator` currently decides whether an entity is an individual using an exact hard-coded whitelist. Custom person-like ontology labels such as `CompanyExecutive`, `BoardDirector`, `SecuritiesAnalyst`, and `Investor` therefore fall into the institutional prompt even when Zep Cloud preserved their labels correctly.

Replace the binary whitelist check with a deterministic entity-kind classifier that returns one of:

- `individual`
- `institution`
- `region`
- `event`
- `other`

Classification uses the following evidence in order:

1. Explicit known-type aliases for common ontology labels.
2. Normalized type-name tokens and suffixes such as `Person`, `Executive`, `Director`, `Analyst`, `Investor`, `Journalist`, `Company`, `Organization`, `Agency`, `Media`, `Platform`, `Exchange`, `Region`, `Country`, `Market`, `Event`, and `Conference`.
3. Attribute-name signals such as `full_name`, `age`, `occupation`, `title`, `org_name`, `company_name`, and `location`.
4. The ontology type description when available.
5. A conservative `other` fallback rather than silently treating every unknown type as an institution.

The classifier must not call an LLM. It must be deterministic, independently testable, and shared by both Zep Cloud and local Graphiti profile generation.

Prompt selection changes accordingly:

- `individual` uses the personal profile prompt and generates meaningful age, gender, MBTI, profession, and country.
- `institution` uses the institutional account prompt. Its virtual age and MBTI remain internal compatibility fields and must not be presented as real demographic facts.
- `region` creates an explicitly representative regional observer account, not a fictional government or person.
- `event` creates an event-information or archival account when an Agent is required.
- `other` uses a neutral representative-account prompt with no assumption that it is an institution.

Rule-based fallback must follow the same entity-kind result. It must not default all unknown labels to `age=30`, `gender=other`, `ISTJ`, and `中国`. Country should prefer explicit entity attributes or descriptions; otherwise use `未明确` rather than inventing China.

This change applies to both backends:

- Zep Cloud already persists ontology labels, but benefits from correct classification of custom person-like types.
- Local Graphiti first needs the ontology propagation in this design, then uses the same classifier.

## Validation and Failure Handling

- Reject malformed ontology definitions before writing episodes.
- Cap entity and edge types at the existing `MAX_ONTOLOGY_TYPES` limit.
- Do not mutate the original ontology payload.
- Do not retry ambiguous graph writes.
- Log compiled entity/edge counts and edge-map pair counts without logging source document content.
- A compilation failure marks graph construction failed with an actionable error.

## Testing

- Compiler creates Pydantic entity and edge models with descriptions and optional fields.
- Name normalization and duplicate detection are deterministic.
- Invalid source-target references fail before graph writes.
- `set_ontology()` caches compiled bundles by graph ID.
- Single and bulk episode calls pass entity types, edge types, edge map, and extraction instructions.
- A fake Graphiti extraction path verifies custom labels survive adapter conversion.
- Entity-kind classification correctly recognizes `CompanyExecutive`, `BoardDirector`, `SecuritiesAnalyst`, and `Investor` as individuals.
- Company, media, platform, exchange, government agency, and organization types classify as institutions.
- Region and event types use their dedicated representative-account prompts.
- Unknown types use the neutral fallback without forced `30 / other / ISTJ / 中国` values.
- Zep Cloud custom labels and local Graphiti labels produce the same entity-kind result.
- Existing Graphiti graphs without custom labels still use `GenericEntity` fallback.
- Zep Cloud ontology-write behavior remains unchanged while its downstream persona classification uses the shared classifier.
- Full backend and Docker Neo4j regression tests pass.
