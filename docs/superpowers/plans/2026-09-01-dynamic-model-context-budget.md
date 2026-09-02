# Dynamic Model Context Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist per-model context windows and dynamically compact long OASIS Responses conversations without breaking personas or tool-call chains.

**Architecture:** A focused model-metadata module supplies known defaults and dynamic budget calculations. Role assignments persist `context_window_tokens` in their existing SQLite JSON documents, simulation snapshots propagate it into `ResponsesModelBackend`, and a protocol-focused compactor removes oldest atomic history groups before sending Responses requests. Standard Responses providers receive `truncation: auto`; the OAuth gateway omits that unsupported field from the private ChatGPT Codex payload and treats context overflow as deterministic.

**Tech Stack:** Python 3.11/3.12, Flask, SQLite JSON assignments, Vue 3, CAMEL `OpenAITokenCounter`, OpenAI Responses API, pytest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-01-dynamic-model-context-budget-design.md`

## Global Constraints

- `context_window_tokens` belongs to text-model role assignments; embedding assignments do not use it.
- Known GPT-5.6 model IDs default to exactly `1_050_000` tokens and remain manually overridable.
- Unknown assigned text models require a positive manual context window before apply or simulation start.
- Runtime input budget is `C - clamp(floor(C * 0.10), 16_000, 128_000)`.
- Preserve all system/developer messages and full Agent personas.
- Preserve assistant tool calls and matching tool results atomically.
- Never log prompt content, tool arguments, credentials, or unmasked secrets.
- Keep Graphiti `BATCH_SIZE = 20` and `MAX_EPISODE_CHARS = 9_500` unchanged.
- Do not commit without explicit user authorization.

---

### Task 1: Model Metadata and Assignment Validation

**Files:**
- Create: `backend/app/services/model_metadata.py`
- Modify: `backend/app/services/model_config_service.py`
- Modify: `backend/app/services/model_config_store.py`
- Test: `backend/tests/test_model_context_configuration.py`

**Interfaces:**
- Produces: `known_context_window(model: str) -> int | None`.
- Produces: `input_token_budget(context_window_tokens: int) -> int`.
- Produces: assignment normalization that fills known models and validates unknown text models.

- [ ] **Step 1: Write failing metadata and persistence tests**

```python
def test_known_gpt_56_models_have_documented_context():
    assert known_context_window("gpt-5.6-luna") == 1_050_000
    assert known_context_window("gpt-5.6-terra") == 1_050_000
    assert known_context_window("gpt-5.6-sol") == 1_050_000
    assert known_context_window("gpt-5.6") == 1_050_000

def test_dynamic_budget_reserves_ten_percent_with_bounds():
    assert input_token_budget(1_050_000) == 945_000
    assert input_token_budget(100_000) == 84_000
    assert input_token_budget(2_000_000) == 1_872_000

def test_unknown_text_model_requires_context_before_apply(service):
    service.save_draft({"high_throughput": {"connection_id": "conn-custom", "protocol": "openai_responses", "model": "custom-model", "context_window_tokens": None, "timeout": 600, "max_concurrency": 8}})
    with pytest.raises(ValueError, match="最大上下文"):
        service.apply_draft()
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run --project backend pytest backend/tests/test_model_context_configuration.py -q`

Expected: FAIL because `model_metadata` and context validation do not exist.

- [ ] **Step 3: Implement model metadata and normalization**

```python
KNOWN_CONTEXT_WINDOWS = {
    "gpt-5.6": 1_050_000,
    "gpt-5.6-luna": 1_050_000,
    "gpt-5.6-terra": 1_050_000,
    "gpt-5.6-sol": 1_050_000,
}

def input_token_budget(context_window_tokens: int) -> int:
    reserve = min(128_000, max(16_000, int(context_window_tokens * 0.10)))
    budget = context_window_tokens - reserve
    if budget <= 0:
        raise ValueError("模型最大上下文必须大于预留输出空间")
    return budget
```

Normalize known values only when the field is missing, so explicit overrides survive. Validate positive integers for every assigned non-embedding role during apply and snapshot creation.

- [ ] **Step 4: Backfill known assignments in all persisted JSON documents**

Extend store initialization migration to visit `model_role_drafts`, `model_config_versions`, and `project_model_snapshots`, adding `context_window_tokens` only for known model IDs and recording a `model_context_window_version=1` state key.

- [ ] **Step 5: Run focused tests**

Run: `uv run --project backend pytest backend/tests/test_model_context_configuration.py backend/tests/test_model_config_service.py -q`

Expected: PASS.

---

### Task 2: Configuration Center Context Field

**Files:**
- Modify: `backend/app/api/model_settings.py`
- Modify: `frontend/src/views/ModelSettingsView.vue`
- Test: `backend/tests/test_model_settings_api.py`

**Interfaces:**
- Consumes: `known_context_window(model)` from Task 1.
- Produces: provider/model metadata API data containing `context_window_tokens`.
- Produces: editable `draft.high_capability.context_window_tokens` and `draft.high_throughput.context_window_tokens`.

- [ ] **Step 1: Write failing API tests for known and unknown metadata**

```python
def test_model_metadata_endpoint_returns_known_context(client):
    response = client.get("/api/settings/models/metadata?model=gpt-5.6-luna")
    assert response.json["data"]["context_window_tokens"] == 1_050_000

def test_unknown_model_metadata_returns_null(client):
    response = client.get("/api/settings/models/metadata?model=custom-model")
    assert response.json["data"]["context_window_tokens"] is None
```

- [ ] **Step 2: Run API tests and verify failure**

Run: `uv run --project backend pytest backend/tests/test_model_settings_api.py -q`

Expected: FAIL with 404 for the metadata endpoint.

- [ ] **Step 3: Add metadata endpoint and Vue form field**

Add `GET /metadata?model=<model-id>`. In both text-role advanced sections render:

```html
<label>
  最大上下文 Tokens
  <input v-model.number="draft[role.key].context_window_tokens" type="number" min="1">
</label>
```

Known-model selection fills the backend value unless the user has manually edited the field for the current model. Unknown models show a required-field hint. Embedding keeps its existing fields only.

- [ ] **Step 4: Run API tests and frontend build**

Run: `uv run --project backend pytest backend/tests/test_model_settings_api.py -q`

Run: `npm run build`

Expected: both succeed.

---

### Task 3: Propagate Context Window Into Simulation Runtime

**Files:**
- Modify: `backend/app/services/model_router.py`
- Modify: `backend/app/services/simulation_manager.py`
- Modify: `backend/scripts/run_parallel_simulation.py`
- Modify: `backend/scripts/protocol_model_backend.py`
- Test: `backend/tests/test_simulation_model_context.py`

**Interfaces:**
- Consumes: assignment key `context_window_tokens: int`.
- Produces: simulation configuration key `llm_context_window_tokens`.
- Produces: `ResponsesModelBackend(model_type, api_key, url, timeout=None, context_window_tokens: int | None = None)`.

- [ ] **Step 1: Write failing propagation tests**

```python
def test_simulation_config_contains_snapshot_context_window(manager):
    config = manager.generate_simulation_config("sim-1", "requirement", "document", [])
    assert config["llm_context_window_tokens"] == 1_050_000

def test_responses_backend_rejects_missing_context_window():
    with pytest.raises(ValueError, match="context_window_tokens"):
        ResponsesModelBackend("gpt-5.6-luna", "key", "url", context_window_tokens=None)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run --project backend pytest backend/tests/test_simulation_model_context.py -q`

Expected: FAIL because the runtime field is absent.

- [ ] **Step 3: Propagate and validate the field**

Read the role assignment through `ModelRouter`, persist it in generated `simulation_config.json`, pass it through `create_simulation_model`, and reject a missing or non-positive window before launching an OASIS process.

- [ ] **Step 4: Run focused tests**

Run: `uv run --project backend pytest backend/tests/test_simulation_model_context.py -q`

Expected: PASS.

---

### Task 4: Tool-Aware Dynamic History Compaction

**Files:**
- Create: `backend/scripts/context_compactor.py`
- Modify: `backend/scripts/protocol_model_backend.py`
- Modify: `backend/app/services/protocols/base.py`
- Modify: `backend/app/services/protocols/openai_responses.py`
- Test: `backend/tests/test_context_compactor.py`

**Interfaces:**
- Produces: `compact_messages(messages, tools, token_counter, context_window_tokens) -> CompactionResult`.
- Produces: `CompactionResult(messages, original_tokens, compacted_tokens, removed_groups, input_budget)`.
- Adds: `TextGenerationRequest.truncation: str | None`.

- [ ] **Step 1: Write failing compaction tests**

```python
def test_under_budget_history_is_unchanged(counter):
    messages = [{"role": "system", "content": "persona"}, {"role": "user", "content": "recent"}]
    assert compact_messages(messages, [], counter, 100_000).messages == messages

def test_oldest_history_is_removed_before_recent_history(counter):
    result = compact_messages(history_with_old_and_recent_messages(), [], counter, 20_000)
    assert "old" not in [message["content"] for message in result.messages]
    assert result.messages[-1]["content"] == "recent"

def test_system_persona_is_never_removed(counter):
    result = compact_messages(history_over_budget(), [], counter, 20_000)
    assert result.messages[0] == {"role": "system", "content": "persona"}

def test_tool_call_and_matching_output_are_atomic(counter):
    result = compact_messages(history_with_tool_pair(), [], counter, 20_000)
    retained_ids = {message.get("tool_call_id") for message in result.messages if message.get("role") == "tool"}
    assistant_ids = {call["id"] for message in result.messages for call in message.get("tool_calls", [])}
    assert retained_ids == assistant_ids

def test_tool_schema_tokens_count_toward_budget(counter):
    result = compact_messages(short_history(), large_tool_schema(), counter, 20_000)
    assert result.original_tokens > counter.count_tokens_from_messages(short_history())

def test_oversized_fixed_context_fails(counter):
    with pytest.raises(ValueError, match="固定上下文"):
        compact_messages([{"role": "system", "content": "x" * 100_000}], [], counter, 20_000)
```

Use a deterministic fake counter in unit tests so each string's token cost is explicit.

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run --project backend pytest backend/tests/test_context_compactor.py -q`

Expected: FAIL because `context_compactor` does not exist.

- [ ] **Step 3: Implement atomic grouping and dynamic budget**

Create groups in chronological order. Merge each assistant `tool_calls` message with immediately following tool messages whose `tool_call_id` belongs to that assistant message. Preserve fixed messages, remove oldest removable groups, and recount after each removal until `input_token_budget(context_window_tokens)` is satisfied.

- [ ] **Step 4: Integrate compaction into `ResponsesModelBackend`**

Compact the preprocessed messages before `_responses_input`, include serialized tool definitions in the count, log only model/budget/counts, and pass `truncation="auto"` in `TextGenerationRequest`.

- [ ] **Step 5: Forward `truncation` in the protocol client**

```python
if request.truncation:
    kwargs["truncation"] = request.truncation
```

- [ ] **Step 6: Run compactor and protocol tests**

Run: `uv run --project backend pytest backend/tests/test_context_compactor.py backend/tests/test_protocol_clients.py -q`

Expected: PASS.

---

### Task 5: OAuth Gateway Truncation and Error Classification

**Files:**
- Modify: `direct_gateway/app/api.py`
- Modify: `direct_gateway/app/messages.py`
- Modify: `direct_gateway/app/responses_client.py`
- Test: `direct_gateway/tests/test_responses.py`
- Test: `direct_gateway/tests/test_api.py`

**Interfaces:**
- Consumes: native Responses field `truncation: "auto"`.
- Produces: an upstream Codex payload that deliberately omits unsupported `truncation`.
- Produces: non-retryable `ProviderResponseError(code="context_length_exceeded")`.

- [ ] **Step 1: Write failing gateway tests**

```python
def test_gateway_omits_unsupported_auto_truncation():
    payload = build_responses_payload({"messages": [{"role": "user", "content": "hi"}], "truncation": "auto"}, "gpt")
    assert "truncation" not in payload

def test_context_length_error_is_not_retried():
    assert ProviderResponseError("context_length_exceeded", "too long").retryable is False
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd direct_gateway && uv run python -m pytest tests/test_responses.py tests/test_api.py -q`

Expected: FAIL because truncation is currently forwarded and context overflow is currently retryable.

- [ ] **Step 3: Omit private-upstream truncation and classify deterministic errors**

Accept `truncation` in `_chat_request_from_response` but omit it in `build_responses_payload` for the private Codex endpoint. Add `context_length_exceeded` to `_NON_RETRYABLE_CODES`; return HTTP 400 with the provider code and no prompt content.

- [ ] **Step 4: Run full gateway tests**

Run: `cd direct_gateway && uv run python -m pytest tests -q`

Expected: PASS.

---

### Task 6: Full Verification, Migration, and Docker Runtime Check

**Files:**
- Modify: `README.md`
- Modify: `README-EN.md`
- Verify: `backend/uploads/model-config/models.db`

**Interfaces:**
- Consumes all previous tasks.
- Produces a migrated active GPT-5.6 assignment and a deployed runtime with dynamic compaction.

- [ ] **Step 1: Document context-window configuration and migration**

Explain known-model autofill, unknown-model requirement, dynamic reserve calculation, and the difference between Agent context compaction and Graphiti Episode limits.

- [ ] **Step 2: Run all automated verification**

Run: `uv run --project backend pytest backend/tests -q`

Run: `cd direct_gateway && uv run python -m pytest tests -q`

Run: `npm run build`

Run: `git diff --check`

Expected: all commands exit 0.

- [ ] **Step 3: Rebuild local Docker services**

Run: `docker compose up -d --build`

Verify: `curl -fsS http://localhost:5001/health`

- [ ] **Step 4: Verify SQLite migration without exposing secrets**

Read active assignments through `/api/settings/models/active` and assert both text roles report `context_window_tokens=1050000`. Do not print API keys or decrypted credential fields.

- [ ] **Step 5: Run a long-history compaction probe**

Construct a local deterministic request above the dynamic input budget, verify compaction preserves system/persona and tool pairs, and confirm the standard protocol client emits `truncation: auto` while the OAuth private-upstream payload omits it, without sending the synthetic oversized request upstream.

- [ ] **Step 6: Restart the simulation only after current run reaches a safe terminal state**

Use the existing stop/finalization barrier for `sim_264db5dc093c`, then force restart with parallel platforms and graph memory enabled. Verify progress advances without `context_length_exceeded` retries.
