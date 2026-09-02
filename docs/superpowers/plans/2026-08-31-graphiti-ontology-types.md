# Graphiti Ontology Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Preserve MiroFish ontology types in local Graphiti/Neo4j and classify typed entities correctly for persona generation across both Graphiti and Zep Cloud.

**Architecture:** A dedicated compiler converts persisted ontology JSON into Graphiti Pydantic entity/edge models plus an edge map, and the adapter passes the compiled bundle into every episode write. A separate deterministic entity-kind classifier maps ontology types to personal, institutional, regional, event, or neutral prompts without LLM calls.

**Tech Stack:** Python 3.11/3.12, Pydantic v2, Graphiti Core, Neo4j, Flask, pytest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-31-graphiti-ontology-types-design.md`

## Global Constraints

- Do not mutate persisted ontology payloads.
- Preserve Zep Cloud ontology-write behavior.
- Preserve `GenericEntity` only as a historical/unmatched fallback.
- Reject invalid or ambiguous compiled ontology definitions before graph writes.
- Do not commit without explicit user authorization.

---

### Task 1: Graphiti Ontology Compiler

**Files:**
- Create: `backend/app/services/graphiti_ontology.py`
- Test: `backend/tests/services/test_graphiti_ontology.py`

**Interfaces:**
- Produces: `GraphitiOntologyBundle(entity_types, edge_types, edge_type_map, custom_extraction_instructions)`.
- Produces: `compile_graphiti_ontology(entities, edges) -> GraphitiOntologyBundle`.

- [x] **Step 1: Write failing compiler tests**

Cover entity model docstrings, optional described fields, edge models, accumulated source-target mappings, input immutability, invalid references, identifier normalization, and duplicate normalized names.

- [x] **Step 2: Run tests and confirm missing-module failure**

Run: `uv run --project backend pytest backend/tests/services/test_graphiti_ontology.py -q`

- [x] **Step 3: Implement immutable bundle and identifier normalization**

Use `pydantic.create_model` with optional string fields and `Field(description=...)`. Normalize names to valid identifiers, prefix reserved attributes, and reject collisions.

- [x] **Step 4: Compile edges and edge map**

Validate source/target entity references and build `dict[(source, target), list[edge_name]]` without duplicates. Add extraction instructions requiring the most specific supplied type.

- [x] **Step 5: Run compiler tests**

Run: `uv run --project backend pytest backend/tests/services/test_graphiti_ontology.py -q`

---

### Task 2: Graphiti Adapter Integration

**Files:**
- Modify: `backend/app/services/zep_graphiti_impl.py`
- Test: `backend/tests/services/test_zep_graphiti_impl.py`

**Interfaces:**
- Consumes: `compile_graphiti_ontology` from Task 1.
- Produces: graph-ID keyed compiled ontology cache used by single and bulk episode writes.

- [x] **Step 1: Write failing adapter tests**

Use a fake Graphiti client to assert `set_ontology()` caches a compiled bundle and both `add_episode()` and `add_episode_batch()` pass `entity_types`, `edge_types`, `edge_type_map`, and `custom_extraction_instructions`.

- [x] **Step 2: Replace raw no-op cache with compiled bundles**

Compile once per graph ID, log counts, and keep graphs without ontology on Graphiti defaults.

- [x] **Step 3: Pass bundle parameters into all write paths**

Build a shared keyword-dictionary helper so single and bulk calls cannot diverge.

- [x] **Step 4: Run adapter and graph-builder tests**

Run: `uv run --project backend pytest backend/tests/services/test_zep_graphiti_impl.py backend/tests/test_zep_graph_lifecycle.py -q`

---

### Task 3: Deterministic Entity-Kind Classifier

**Files:**
- Create: `backend/app/services/entity_kind_classifier.py`
- Test: `backend/tests/test_entity_kind_classifier.py`

**Interfaces:**
- Produces: `EntityKind` enum with `individual`, `institution`, `region`, `event`, and `other`.
- Produces: `classify_entity_kind(type_name, attributes=None, description=None) -> EntityKind`.

- [x] **Step 1: Write failing classification tests**

Cover personal custom labels (`CompanyExecutive`, `BoardDirector`, `SecuritiesAnalyst`, `Investor`), institutional labels, region/country/market labels, event/conference labels, attribute signals, descriptions, and unknown fallback.

- [x] **Step 2: Implement ordered deterministic evidence rules**

Use explicit aliases first, then normalized type tokens/suffixes, attribute signals, description terms, and finally `other`. Do not call an LLM.

- [x] **Step 3: Run classifier tests**

Run: `uv run --project backend pytest backend/tests/test_entity_kind_classifier.py -q`

---

### Task 4: Persona Prompt and Fallback Integration

**Files:**
- Modify: `backend/app/services/oasis_profile_generator.py`
- Test: `backend/tests/test_oasis_profile_entity_kinds.py`

**Interfaces:**
- Consumes: `classify_entity_kind` from Task 3.
- Produces: kind-specific prompt selection and rule-based fallback.

- [x] **Step 1: Write failing prompt-selection tests**

Assert custom person types use the individual prompt, institutions use the institutional prompt, region/event types use representative-account prompts, and unknown types use a neutral prompt.

- [x] **Step 2: Replace binary whitelist branching**

Compute entity kind once per profile, select the corresponding prompt/system instruction, and keep profile serialization compatible with OASIS-required fields.

- [x] **Step 3: Fix rule-based fallback defaults**

Individuals receive plausible demographic variation. Institutions keep internal compatibility fields but describe them as virtual. Regions/events use representative accounts. Unknown types use `country=未明确` and do not force ISTJ/China.

- [x] **Step 4: Run profile tests**

Run: `uv run --project backend pytest backend/tests/test_oasis_profile_entity_kinds.py -q`

---

### Task 5: End-to-End Neo4j Verification and Rebuild

**Files:**
- Modify: `README.md`
- Modify: `README-EN.md`
- Test: relevant existing Graphiti and simulation tests

**Interfaces:**
- Consumes all earlier tasks.
- Produces a rebuilt project graph and regenerated profiles with typed-node evidence.

- [x] **Step 1: Run full automated verification**

Run: `uv run --project backend pytest backend/tests -q`

Run: `cd direct_gateway && uv run python -m pytest tests -q`

Run: `npm run build`

Run: `git diff --check`

- [x] **Step 2: Rebuild Docker services**

Run: `docker compose up -d --build`

- [x] **Step 3: Force rebuild the existing project graph**

Use the persisted ontology and corpus for `proj_d3c5175a4d6f`. Verify Neo4j nodes contain custom labels and no graph-wide `GenericEntity` fallback is needed.

- [x] **Step 4: Regenerate simulation profiles**

Prepare a new or force-regenerated simulation from the rebuilt graph. Verify personal types no longer all use `age=30 / other / ISTJ / 中国`, while institutional accounts remain correctly identified.

- [x] **Step 5: Document migration behavior**

Explain that existing generic graphs require force rebuild and profile regeneration; Zep Cloud keeps its ontology write path but uses the shared persona classifier.
