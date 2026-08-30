# Multi-Protocol Provider Connections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one provider connection expose multiple protocols so the same HTTP service can serve text and embedding model roles.

**Architecture:** Shared transport configuration remains in `model_connections`; protocol capability and verification state move to `model_connection_protocols`. Role assignments become an explicit `(connection_id, protocol, model)` triple, and every runtime consumer resolves the selected protocol from the role assignment.

**Tech Stack:** Python 3.11/3.12, Flask, SQLite, OpenAI and Anthropic SDKs, Vue 3, Axios, pytest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-30-multi-protocol-provider-connections-design.md`

## Global Constraints

- Preserve encrypted credentials and never return plaintext secrets.
- Preserve legacy connection columns during the compatibility period.
- Existing drafts, active versions, and project snapshots must remain readable.
- New public API payloads use `protocols`; legacy single-protocol payloads remain internal to environment migration.
- A role assignment consists of `connection_id`, `protocol`, and `model`.
- Do not create a Git commit without explicit user authorization.

---

### Task 1: Protocol-State Domain Types and SQLite Migration

**Files:**
- Modify: `backend/app/models/model_config.py`
- Modify: `backend/app/services/model_config_store.py`
- Test: `backend/tests/test_model_config_store.py`

**Interfaces:**
- Produces: `ProtocolSource`, `ProtocolVerificationStatus`, and `ConnectionProtocol`.
- Produces: `ModelConnection.protocols: tuple[ConnectionProtocol, ...]`.
- Produces: `ModelConfigStore.replace_connection_protocols(connection_id, protocols)` and `list_connection_protocols(connection_id)`.

- [ ] **Step 1: Write failing migration and CRUD tests**

Add tests that create a legacy database, initialize the store, and assert the legacy protocol becomes one `ConnectionProtocol`. Add a multi-protocol connection fixture and assert both text and embedding rows survive reload without exposing secrets.

- [ ] **Step 2: Run tests and verify the missing table/type failures**

Run: `uv run --project backend pytest backend/tests/test_model_config_store.py -q`

- [ ] **Step 3: Add protocol-state enums and dataclass**

Define exact values:

```python
class ProtocolSource(str, Enum):
    DETECTED = "detected"
    MANUAL = "manual"

class ProtocolVerificationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNTESTED = "untested"

@dataclass(frozen=True)
class ConnectionProtocol:
    protocol: APIProtocol
    capability: ModelCapability
    source: ProtocolSource
    verification_status: ProtocolVerificationStatus
    last_tested_at: str | None = None
    error_code: str | None = None
```

- [ ] **Step 4: Add the protocol table and idempotent migration**

Create `model_connection_protocols` with primary key `(connection_id, protocol)`, foreign key deletion, capability/source/status fields, and nullable test metadata. Backfill only connections without rows. Hydrate `ModelConnection.protocols` from the table while leaving legacy columns intact.

- [ ] **Step 5: Add store replacement and lookup methods**

`replace_connection_protocols` must use one transaction, reject an empty list, delete only rows belonging to the target connection, and insert the supplied set. Connection create/update/delete must maintain the child rows atomically.

- [ ] **Step 6: Run store tests**

Run: `uv run --project backend pytest backend/tests/test_model_config_store.py -q`

---

### Task 2: Role-Assignment Migration and Validation

**Files:**
- Modify: `backend/app/services/model_config_store.py`
- Modify: `backend/app/services/model_config_service.py`
- Modify: `backend/app/services/model_router.py`
- Test: `backend/tests/test_model_config_store.py`
- Test: `backend/tests/test_provider_connection_api.py`
- Test: `backend/tests/test_oauth_gateway_credentials.py`

**Interfaces:**
- Consumes: `ModelConnection.protocols` from Task 1.
- Produces: role assignment dictionaries containing `connection_id`, `protocol`, and `model`.
- Produces: `ModelConfigService.validate_draft(assignments)` membership and capability checks.

- [ ] **Step 1: Write failing assignment migration tests**

Persist a legacy draft, active version, and project snapshot without protocol fields. Reopen the store and assert every assignment gains the referenced connection’s migrated protocol. Assert the migration is idempotent.

- [ ] **Step 2: Write failing validation tests**

Cover a text role selecting an embedding protocol, an embedding role selecting a text protocol, and a role selecting a protocol not enabled on its connection.

- [ ] **Step 3: Implement assignment backfill**

During schema migration, decode draft/version JSON, add missing protocol values from the connection’s only migrated protocol row, and update only changed rows. Preserve immutable version IDs and snapshot references.

- [ ] **Step 4: Update validation and routing**

Validate the assignment protocol against `connection.protocols`; derive capability from that selected row. `ModelRouter.resolve` must return the assignment protocol instead of a connection-level field while continuing to inject OAuth internal credentials.

- [ ] **Step 5: Run focused tests**

Run: `uv run --project backend pytest backend/tests/test_model_config_store.py backend/tests/test_provider_connection_api.py backend/tests/test_oauth_gateway_credentials.py -q`

---

### Task 3: Automatic Protocol Detection and Manual Correction

**Files:**
- Create: `backend/app/services/protocol_detector.py`
- Modify: `backend/app/services/draft_connection_tester.py`
- Modify: `backend/app/services/model_config_service.py`
- Modify: `backend/app/api/model_settings.py`
- Test: `backend/tests/test_protocol_detector.py`
- Test: `backend/tests/test_provider_connection_api.py`

**Interfaces:**
- Produces: `ProtocolDetector.detect(data) -> list[dict]` with `protocol`, `capability`, `source`, `verification_status`, `latency_ms`, and optional `error_code`/`message`.
- Connection create consumes `protocols: list[dict]` and persists the selected set.

- [ ] **Step 1: Write failing detector tests with HTTP mock transports**

Cover OpenAI Responses, Chat Completions, Embeddings, Anthropic Messages, a service returning HTTP 200 with an error envelope, a true missing endpoint, and timeout. Assert API keys never appear in results.

- [ ] **Step 2: Run detector tests and confirm failure**

Run: `uv run --project backend pytest backend/tests/test_protocol_detector.py -q`

- [ ] **Step 3: Implement bounded candidate detection**

Use vendor catalog candidates, a model-list request, and minimal endpoint probes. Normalize nonstandard response/status shapes into stable states. Do not persist submitted data. Cap each probe timeout and run independent candidates concurrently with a small fixed worker count.

- [ ] **Step 4: Validate manually corrected protocol sets**

Require at least one protocol. Restrict known vendors to catalog protocols. Convert manually enabled unsuccessful candidates to `source=manual` and `verification_status=untested`; preserve successful detected rows as `source=detected` and `passed`.

- [ ] **Step 5: Update detection and connection APIs**

`POST /connections/test-draft` returns `protocols`. `POST /connections` accepts the selected protocol-state list and returns the hydrated multi-protocol connection. Reject top-level public `protocol` payloads.

- [ ] **Step 6: Run focused tests**

Run: `uv run --project backend pytest backend/tests/test_protocol_detector.py backend/tests/test_provider_connection_api.py -q`

---

### Task 4: Protocol-Specific Model Discovery

**Files:**
- Modify: `backend/app/services/model_discovery.py`
- Modify: `backend/app/api/model_settings.py`
- Test: `backend/tests/test_model_discovery.py`
- Test: `backend/tests/test_provider_connection_api.py`

**Interfaces:**
- Produces: `ModelDiscovery.list_models(connection, api_key, role, protocol)`.
- API requires query parameters `role` and `protocol` for `/connections/<id>/models`.

- [ ] **Step 1: Write failing protocol-membership and filtering tests**

Assert a multi-protocol LM Studio connection returns text models for Chat Completions and embedding models for Embeddings. Reject protocols not enabled on the connection.

- [ ] **Step 2: Implement protocol-specific client selection**

Select OpenAI or Anthropic model listing from the requested protocol. Preserve manual-entry fallback when listing is unsupported, but return a validation error for a protocol not belonging to the connection.

- [ ] **Step 3: Update API query validation and response**

Require protocol, validate role compatibility, and return the existing `{data, manual_entry}` shape.

- [ ] **Step 4: Run focused tests**

Run: `uv run --project backend pytest backend/tests/test_model_discovery.py backend/tests/test_provider_connection_api.py -q`

---

### Task 5: Configuration-Center Multi-Protocol Connection Modal

**Files:**
- Modify: `frontend/src/api/modelSettings.js`
- Modify: `frontend/src/views/ModelSettingsView.vue`
- Test: `backend/tests/test_memory_backend_settings_ui.py`

**Interfaces:**
- Consumes: detection response from Task 3.
- Produces: connection create payload with `protocols` and no model.

- [ ] **Step 1: Write failing static UI contract tests**

Assert the modal contains a protocol result list, detected/manual/untested labels, protocol checkboxes, “探测协议”, at-least-one validation, and no model field.

- [ ] **Step 2: Replace the single protocol selector with detection state**

Maintain reactive protocol rows. API Key/no-auth flows call detection; OAuth loads managed preset rows. Editing transport fields clears detection. Checkbox corrections mark unsuccessful enabled rows manual/untested.

- [ ] **Step 3: Update creation gating and feedback**

Disable creation until at least one protocol is selected and the required detection/manual acknowledgement is satisfied. Keep errors inside the modal and preserve unsaved inputs on failure.

- [ ] **Step 4: Add responsive and accessible protocol controls**

Use visible labels, keyboard-operable checkboxes, status text in an `aria-live` region, 44px controls, and a single-column mobile layout.

- [ ] **Step 5: Run UI contract and build checks**

Run: `uv run --project backend pytest backend/tests/test_memory_backend_settings_ui.py -q`

Run: `npm run build`

---

### Task 6: Three-Stage Role Selection

**Files:**
- Modify: `frontend/src/api/modelSettings.js`
- Modify: `frontend/src/views/ModelSettingsView.vue`
- Test: `backend/tests/test_memory_backend_settings_ui.py`

**Interfaces:**
- Consumes: connection protocol rows and protocol-specific model-list API.
- Produces: draft assignments with `connection_id`, `protocol`, and `model`.

- [ ] **Step 1: Write failing role cascade tests**

Assert every role renders Provider, protocol, and model controls. Assert changing Provider clears protocol/model; changing protocol clears model. Assert capability-compatible connection and protocol filtering.

- [ ] **Step 2: Implement compatible connection and protocol selectors**

Embedding accepts connections with an enabled embedding protocol. Text roles accept connections with an enabled text protocol. Populate the protocol selector from the chosen connection’s compatible rows.

- [ ] **Step 3: Load models using the selected protocol**

Pass role and protocol to `getConnectionModels`. Preserve manual entry fallback. Clear stale model state whenever an upstream selection changes.

- [ ] **Step 4: Update draft load/save/apply payloads**

Hydrate migrated assignments, preserve current selections, and include protocol on save and apply.

- [ ] **Step 5: Run UI and API tests**

Run: `uv run --project backend pytest backend/tests/test_memory_backend_settings_ui.py backend/tests/test_provider_connection_api.py -q`

Run: `npm run build`

---

### Task 7: Runtime Consumer Regression and Documentation

**Files:**
- Modify: `backend/app/utils/llm_client.py`
- Modify: `backend/app/services/zep_graphiti_impl.py`
- Modify: `backend/app/services/simulation_runner.py`
- Modify: `backend/scripts/protocol_model_backend.py`
- Modify: `README.md`
- Modify: `README-EN.md`
- Test: `backend/tests/test_llm_protocol_routing.py`
- Test: `backend/tests/services/test_graphiti_protocol_client.py`
- Test: `backend/tests/test_simulation_protocol_routing.py`

**Interfaces:**
- Consumes: role-resolved protocol from Task 2.
- Produces: unchanged protocol adapter behavior for every runtime consumer.

- [ ] **Step 1: Extend routing tests with one multi-protocol connection**

Use one connection for a Responses text role and an Embeddings role. Assert each consumer receives the role-selected protocol and model.

- [ ] **Step 2: Remove remaining connection-level protocol assumptions**

Update runtime glue only where tests identify legacy field access. Keep protocol adapters unchanged.

- [ ] **Step 3: Update user documentation**

Document multi-protocol connections, automatic detection, manual correction, role-level protocol/model selection, and Docker host URL `http://host.docker.internal:<port>/v1`.

- [ ] **Step 4: Run full verification**

Run: `uv run --project backend pytest backend/tests -q`

Run: `cd direct_gateway && uv run python -m pytest tests -q`

Run: `npm run build`

Run: `git diff --check`

- [ ] **Step 5: Rebuild and verify Docker/browser flows**

Run: `docker compose up -d --build`

Verify health, protocol detection, one LM Studio connection appearing in text and Embedding roles, filtered model lists, saved draft round-trip, and absence of browser console errors. Remove only temporary connections created by this verification.
