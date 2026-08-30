# Provider Protocol Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将模型连接重构为厂商、协议、认证和能力四层模型，并让 OpenAI Responses、OpenAI Chat Completions、Anthropic Messages 与 OpenAI Embeddings 在配置中心、业务 LLM、Graphiti 和 OASIS 中真实生效。

**Architecture:** SQLite 保存厂商、协议、认证和能力，协议适配器统一文本生成结果，业务层只依赖协议中立接口。Graphiti 与 OASIS 通过桥接层消费同一协议配置，厂商预设仅负责默认地址与可选协议，不参与底层路由。

**Tech Stack:** Python 3.11/3.12、Flask、SQLite、OpenAI Python SDK、Anthropic Python SDK、Graphiti、CAMEL/OASIS、Vue 3、Axios、Docker Compose。

**Spec:** `docs/superpowers/specs/2026-08-30-provider-protocol-architecture-design.md`

## Global Constraints

- 所有模型能力均通过 HTTP API 接入，不区分本地或在线。
- 文本协议固定为 `openai_responses`、`openai_chat_completions`、`anthropic_messages`。
- 向量协议第一版仅支持 `openai_embeddings`。
- API Key 必须使用现有 Fernet 密钥加密，接口不得返回明文。
- 旧 Chat Completions 连接不得自动升级为 Responses。
- 迁移必须幂等并在 SQLite 事务内完成。
- 未经用户明确同意不得执行 Git 提交；每个任务末尾只检查提交边界。

---

## File Structure

- `backend/app/models/model_config.py`：厂商、协议、认证、能力枚举和连接公共模型。
- `backend/app/services/provider_catalog.py`：厂商预设、默认地址、支持协议和域名识别。
- `backend/app/services/model_config_store.py`：SQLite schema 迁移、密钥持久化和连接 CRUD。
- `backend/app/services/model_config_service.py`：连接与角色兼容性校验、环境导入。
- `backend/app/services/protocols/base.py`：协议中立请求与结果类型。
- `backend/app/services/protocols/openai_responses.py`：Responses API 实现。
- `backend/app/services/protocols/openai_chat.py`：Chat Completions 实现。
- `backend/app/services/protocols/anthropic_messages.py`：Anthropic Messages 实现。
- `backend/app/services/protocols/openai_embeddings.py`：Embeddings 实现。
- `backend/app/services/protocols/factory.py`：按协议创建适配器。
- `backend/app/utils/llm_client.py`：业务 LLM 使用协议适配器。
- `backend/app/services/model_connection_tester.py`：真实协议连接探测。
- `backend/app/services/model_discovery.py`：厂商级模型发现与手工模型降级。
- `backend/app/services/graphiti_protocol_client.py`：Graphiti 文本协议桥接。
- `backend/app/services/zep_graphiti_impl.py`：装配 Graphiti 桥接和 Embedding。
- `backend/app/services/model_router.py`：向运行时和模拟子进程传递协议信息。
- `backend/scripts/run_parallel_simulation.py`、`run_twitter_simulation.py`、`run_reddit_simulation.py`：CAMEL 协议路由。
- `frontend/src/views/ModelSettingsView.vue`：厂商驱动的连接表单。
- `frontend/src/api/modelSettings.js`：连接元数据与 CRUD API。

### Task 1: Domain Types and Provider Catalog

**Files:**
- Modify: `backend/app/models/model_config.py`
- Create: `backend/app/services/provider_catalog.py`
- Create: `backend/tests/test_provider_catalog.py`

**Interfaces:**
- Produces: `ProviderVendor`, `APIProtocol`, `AuthType`, `ModelCapability` 枚举。
- Produces: `get_provider_spec(vendor) -> ProviderSpec`、`infer_vendor(base_url) -> ProviderVendor`、`protocol_capability(protocol) -> ModelCapability`。

- [ ] **Step 1: Write failing enum and catalog tests**

```python
def test_deepseek_supports_two_text_protocols():
    spec = get_provider_spec(ProviderVendor.DEEPSEEK)
    assert spec.protocols == (
        APIProtocol.OPENAI_CHAT_COMPLETIONS,
        APIProtocol.ANTHROPIC_MESSAGES,
    )

def test_deployment_location_is_not_a_domain_type():
    assert "LOCAL" not in ProviderVendor.__members__
    assert "is_local" not in ModelConnection.__dataclass_fields__
```

- [ ] **Step 2: Run red test**

Run: `uv run --project backend pytest backend/tests/test_provider_catalog.py -q`

Expected: FAIL because the enums and catalog do not exist.

- [ ] **Step 3: Implement types and immutable catalog**

```python
class ProviderVendor(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    KIMI = "kimi"
    CHATGPT_SUBSCRIPTION = "chatgpt_subscription"
    CUSTOM = "custom"

class APIProtocol(str, Enum):
    OPENAI_RESPONSES = "openai_responses"
    OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    OPENAI_EMBEDDINGS = "openai_embeddings"
```

Define `ProviderSpec` as a frozen dataclass containing `vendor`, `label`, `default_base_url`, `protocols`, and `default_auth_type`.

- [ ] **Step 4: Run green test**

Run: `uv run --project backend pytest backend/tests/test_provider_catalog.py -q`

Expected: PASS.

- [ ] **Step 5: Check commit boundary**

Run: `git diff --check && git status --short`

Expected: only Task 1 files plus pre-existing branch changes; do not commit without approval.

### Task 2: SQLite Schema and Idempotent Migration

**Files:**
- Modify: `backend/app/services/model_config_store.py`
- Modify: `backend/tests/test_model_config_store.py`

**Interfaces:**
- Consumes: Task 1 enums and `infer_vendor`.
- Produces: `ModelConfigStore.migrate_provider_protocol_schema() -> None`.
- Produces: connection rows with `vendor`, `protocol`, `auth_type`, `capability`.

- [ ] **Step 1: Write migration tests for all legacy types**

```python
def test_legacy_connections_migrate_without_changing_protocol(tmp_path):
    store = legacy_store(tmp_path, [
        ("openai_compatible", "https://api.deepseek.com", True),
        ("local_openai", "http://model.internal/v1", False),
        ("embedding", "http://embedding.internal/v1", False),
        ("direct_oauth_gateway", "http://direct-oauth-gateway:8090/v1", False),
    ])
    store.migrate_provider_protocol_schema()
    rows = store.list_connections()
    assert rows[0].protocol == APIProtocol.OPENAI_CHAT_COMPLETIONS
    assert rows[1].vendor == ProviderVendor.CUSTOM
    assert rows[2].protocol == APIProtocol.OPENAI_EMBEDDINGS
    assert rows[3].auth_type == AuthType.OAUTH_GATEWAY
```

Also call migration twice and assert row counts and values do not change.

- [ ] **Step 2: Run red migration tests**

Run: `uv run --project backend pytest backend/tests/test_model_config_store.py -q`

Expected: FAIL because new columns and migration are absent.

- [ ] **Step 3: Add columns and transactional migration**

Use `PRAGMA table_info(model_connections)` before `ALTER TABLE ... ADD COLUMN`. Start migration with `BEGIN IMMEDIATE`, populate new fields from legacy values, and set `provider_protocol_schema_version=1` in `model_config_state` only after all rows succeed.

- [ ] **Step 4: Update CRUD and public connection mapping**

Change `create_connection` to consume explicit `vendor`, `protocol`, `auth_type`, and `capability`. Keep the physical `is_local` column only for backward-compatible inserts with value `0`; never expose it in `ModelConnection`.

- [ ] **Step 5: Run green migration and store tests**

Run: `uv run --project backend pytest backend/tests/test_model_config_store.py -q`

Expected: PASS and plaintext secrets absent from database bytes.

- [ ] **Step 6: Check commit boundary**

Run: `git diff --check && git status --short`

Expected: Task 1-2 changes ready; do not commit.

### Task 3: Native Protocol Adapters

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Create: `backend/app/services/protocols/__init__.py`
- Create: `backend/app/services/protocols/base.py`
- Create: `backend/app/services/protocols/openai_responses.py`
- Create: `backend/app/services/protocols/openai_chat.py`
- Create: `backend/app/services/protocols/anthropic_messages.py`
- Create: `backend/app/services/protocols/openai_embeddings.py`
- Create: `backend/app/services/protocols/factory.py`
- Create: `backend/tests/protocols/test_text_protocols.py`
- Create: `backend/tests/protocols/test_embeddings_protocol.py`

**Interfaces:**
- Produces: `TextGenerationRequest`, `TextGenerationResult`, `EmbeddingResult` dataclasses.
- Produces: `create_text_client(connection, api_key)` and `create_embedding_client(connection, api_key)`.

- [ ] **Step 1: Write failing protocol contract tests with fake SDK clients**

```python
def test_responses_adapter_uses_output_text():
    sdk = FakeOpenAI(responses_output_text='{"ok":true}')
    result = OpenAIResponsesClient(sdk).generate(request())
    assert result.text == '{"ok":true}'

def test_anthropic_adapter_extracts_only_text_blocks():
    sdk = FakeAnthropic(content=[TextBlock(text="hello")])
    result = AnthropicMessagesClient(sdk).generate(request())
    assert result.text == "hello"
```

Assert system messages are moved to Anthropic's top-level `system`, `max_tokens` is always positive, and request IDs are preserved.

- [ ] **Step 2: Run red protocol tests**

Run: `uv run --project backend pytest backend/tests/protocols -q`

Expected: FAIL because adapters do not exist.

- [ ] **Step 3: Add Anthropic SDK dependency**

Add `anthropic>=0.75.0,<1` to `backend/pyproject.toml`, then run `uv lock --project backend`.

- [ ] **Step 4: Implement protocol-neutral dataclasses and adapters**

Responses calls `client.responses.create(model=..., input=..., max_output_tokens=...)`. Chat calls `client.chat.completions.create`. Anthropic calls `client.messages.create(model=..., system=..., messages=..., max_tokens=...)`. Embeddings calls `client.embeddings.create` and validates non-empty positive-length vectors.

- [ ] **Step 5: Implement factory protocol validation**

Raise `ValueError("连接协议不是文本生成协议")` when creating a text client for `openai_embeddings`, and the symmetric error for embedding creation.

- [ ] **Step 6: Run green protocol tests**

Run: `uv run --project backend pytest backend/tests/protocols -q`

Expected: PASS.

- [ ] **Step 7: Check commit boundary**

Run: `git diff --check && git status --short`

Expected: protocol layer isolated and tested; do not commit.

### Task 4: Model Configuration API, Discovery, and Tests

**Files:**
- Modify: `backend/app/api/model_settings.py`
- Modify: `backend/app/services/model_config_service.py`
- Modify: `backend/app/services/model_connection_tester.py`
- Modify: `backend/app/services/model_discovery.py`
- Modify: `backend/tests/test_model_discovery.py`
- Create: `backend/tests/test_provider_connection_api.py`

**Interfaces:**
- Consumes: Tasks 1-3 catalog, store, and factories.
- Produces: `GET /api/settings/models/provider-catalog`.
- Updates: connection create/update payloads to `vendor`, `protocol`, `auth_type`, `base_url`, `api_key`.

- [ ] **Step 1: Write failing API validation tests**

```python
def test_kimi_rejects_responses_protocol(client):
    response = client.post("/api/settings/models/connections", json={
        "name": "Kimi",
        "vendor": "kimi",
        "protocol": "openai_responses",
        "auth_type": "api_key",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key": "secret",
    })
    assert response.status_code == 400
    assert "Kimi 不支持 OpenAI Responses" in response.json["error"]
```

Add tests that custom accepts all protocols and embedding cannot be assigned to text roles.

- [ ] **Step 2: Run red API tests**

Run: `uv run --project backend pytest backend/tests/test_provider_connection_api.py backend/tests/test_model_discovery.py -q`

Expected: FAIL because the payload and catalog endpoint are absent.

- [ ] **Step 3: Implement catalog endpoint and connection validation**

Return labels, defaults, protocols, auth types, and capabilities without secrets. Validate protocol membership for non-custom vendors and derive capability from protocol server-side.

- [ ] **Step 4: Replace connection tester with real protocol probes**

Text tests call `generate` with `Return OK.`; embedding tests call `embed(["MiroFish connection test"])`. Map authentication, endpoint, protocol mismatch, model, timeout, and network errors to stable error codes.

- [ ] **Step 5: Make model discovery optional**

Use vendor-specific list endpoints when supported. Return `{data: [], manual_entry: true}` when listing is unsupported; do not convert model-list failure into connection failure.

- [ ] **Step 6: Run green API tests**

Run: `uv run --project backend pytest backend/tests/test_provider_connection_api.py backend/tests/test_model_discovery.py -q`

Expected: PASS.

- [ ] **Step 7: Check commit boundary**

Run: `git diff --check && git status --short`

Expected: configuration API supports new schema; do not commit.

### Task 5: Business LLM and Graphiti Integration

**Files:**
- Modify: `backend/app/utils/llm_client.py`
- Modify: `backend/app/services/model_router.py`
- Create: `backend/app/services/graphiti_protocol_client.py`
- Modify: `backend/app/services/zep_graphiti_impl.py`
- Create: `backend/tests/test_llm_protocol_routing.py`
- Create: `backend/tests/services/test_graphiti_protocol_client.py`

**Interfaces:**
- Consumes: `create_text_client`, `TextGenerationRequest`, protocol fields from ModelRouter.
- Produces: `GraphitiProtocolClient` implementing Graphiti's async `generate_response` contract.

- [ ] **Step 1: Write failing routing tests**

```python
def test_llm_client_routes_anthropic_messages(monkeypatch):
    router = fake_router(protocol="anthropic_messages")
    client = LLMClient(router=router, text_client_factory=fake_factory)
    assert client.chat([{"role": "user", "content": "hi"}]) == "anthropic-result"
```

Add equivalent Responses and Chat Completions cases plus JSON parsing regression cases.

- [ ] **Step 2: Run red integration tests**

Run: `uv run --project backend pytest backend/tests/test_llm_protocol_routing.py backend/tests/services/test_graphiti_protocol_client.py -q`

Expected: FAIL because LLMClient directly constructs OpenAI and Graphiti uses OpenAIGenericClient.

- [ ] **Step 3: Route business LLM through protocol factory**

Preserve `_clean_chat_text`, strict JSON parsing, retry counts, and safe `LLMResponseError`. Pass temperature and output token settings through `TextGenerationRequest`.

- [ ] **Step 4: Implement Graphiti bridge**

Translate Graphiti message objects to normalized messages, call the selected text client asynchronously without sharing event-loop-bound SDK clients, and return Graphiti-compatible text/JSON results.

- [ ] **Step 5: Assemble Graphiti from protocol and embedding connections**

Replace `_build_default_llm_client` branching on URL with protocol-driven construction. Keep the OpenAI Embeddings bridge and batch-size limiting behavior.

- [ ] **Step 6: Run green integration and existing graph tests**

Run: `uv run --project backend pytest backend/tests/test_llm_protocol_routing.py backend/tests/services/test_graphiti_protocol_client.py backend/tests/services/test_zep_graphiti_impl.py -q`

Expected: PASS.

- [ ] **Step 7: Check commit boundary**

Run: `git diff --check && git status --short`

Expected: business and Graphiti paths protocol-neutral; do not commit.

### Task 6: OASIS/CAMEL Protocol Routing

**Files:**
- Modify: `backend/app/services/model_router.py`
- Modify: `backend/scripts/run_parallel_simulation.py`
- Modify: `backend/scripts/run_twitter_simulation.py`
- Modify: `backend/scripts/run_reddit_simulation.py`
- Create: `backend/scripts/protocol_model_backend.py`
- Create: `backend/tests/test_simulation_protocol_routing.py`

**Interfaces:**
- Consumes: protocol, Base URL, model, and secret environment produced by `ModelRouter.build_simulation_environment`.
- Produces: `create_simulation_model() -> BaseModelBackend` in each script through a shared helper.

- [ ] **Step 1: Write failing simulation routing tests**

```python
@pytest.mark.parametrize("protocol,platform", [
    ("openai_chat_completions", "openai-compatible-model"),
    ("anthropic_messages", "anthropic"),
    ("openai_responses", "protocol-bridge"),
])
def test_simulation_backend_matches_protocol(protocol, platform):
    assert describe_backend(protocol) == platform
```

- [ ] **Step 2: Run red simulation tests**

Run: `uv run --project backend pytest backend/tests/test_simulation_protocol_routing.py -q`

Expected: FAIL because scripts force `ModelPlatformType.OPENAI`.

- [ ] **Step 3: Add protocol to subprocess environment**

Return `LLM_PROTOCOL` and `LLM_AUTH_TYPE` alongside existing secret values. Ensure simulation config JSON and logs never include API keys.

- [ ] **Step 4: Implement shared CAMEL model selection**

Use CAMEL Anthropic for `anthropic_messages`, OpenAI-compatible for Chat Completions, and a `BaseModelBackend` adapter backed by `OpenAIResponsesClient` for Responses.

- [ ] **Step 5: Replace duplicated script factories**

All three simulation scripts import `create_simulation_model` from `protocol_model_backend.py`; remove hard-coded OpenAI platform selection.

- [ ] **Step 6: Run green simulation tests**

Run: `uv run --project backend pytest backend/tests/test_simulation_protocol_routing.py backend/tests/test_zep_simulation_barrier.py -q`

Expected: PASS.

- [ ] **Step 7: Check commit boundary**

Run: `git diff --check && git status --short`

Expected: all simulation entry points share one protocol router; do not commit.

### Task 7: Configuration Center UI

**Files:**
- Modify: `frontend/src/views/ModelSettingsView.vue`
- Modify: `frontend/src/api/modelSettings.js`
- Modify: `backend/tests/test_memory_backend_settings_ui.py`

**Interfaces:**
- Consumes: provider catalog endpoint and new connection API payload.
- Produces: vendor → protocol → auth → Base URL → secret form flow.

- [ ] **Step 1: Extend static UI regression test**

Assert the view contains `厂商或接入方式`, `接口协议`, `认证方式`, and does not contain `在线 OpenAI-compatible`, `本地文本模型`, or `is_local`.

- [ ] **Step 2: Run red UI test**

Run: `uv run --project backend pytest backend/tests/test_memory_backend_settings_ui.py -q`

Expected: FAIL on legacy options.

- [ ] **Step 3: Load catalog and implement dependent selects**

Selecting a vendor updates allowed protocols and default Base URL. Selecting a protocol derives capability. Selecting `custom` exposes all protocols. Selecting `none` hides the secret field; `oauth_gateway` shows the existing ChatGPT login controls.

- [ ] **Step 4: Render connection cards with labels**

Show vendor label, protocol label, capability, Base URL, masked credential, test state, and delete action. Never display internal enum strings as the primary label.

- [ ] **Step 5: Build and run UI regression test**

Run: `uv run --project backend pytest backend/tests/test_memory_backend_settings_ui.py -q && npm run build`

Expected: PASS; Vite build exits 0.

- [ ] **Step 6: Check commit boundary**

Run: `git diff --check && git status --short`

Expected: UI changes isolated; do not commit.

### Task 8: End-to-End Migration and Verification

**Files:**
- Modify: `.env.example`
- Modify: `.env.production.example`
- Modify: `README.md`
- Modify: `README-EN.md`
- Modify: `docs/superpowers/specs/2026-08-30-provider-protocol-architecture-design.md` only if implementation reveals a documented mismatch.

**Interfaces:**
- Consumes: all previous tasks.
- Produces: deployable, documented feature and verified existing database migration.

- [ ] **Step 1: Back up the live SQLite database**

Run: `cp backend/uploads/model-config/models.db backend/uploads/model-config/models.db.bak-before-provider-protocol-migration`

Expected: backup exists before the application performs migration.

- [ ] **Step 2: Run full backend regression**

Run: `uv run --project backend pytest backend/tests -q`

Expected: all tests pass; only known third-party deprecation warnings remain.

- [ ] **Step 3: Build frontend and validate Compose**

Run: `npm run build`

Run: `NEO4J_PASSWORD=verification-only docker compose -f docker-compose.production.yml config --quiet`

Expected: both commands exit 0.

- [ ] **Step 4: Rebuild Docker deployment**

Run: `docker compose up -d --build`

Expected: `mirofish` starts and the existing `mirofish-neo4j` remains healthy.

- [ ] **Step 5: Verify migrated API data without secrets**

Run: `curl -fsS http://127.0.0.1:5001/api/settings/models/connections`

Expected: every connection has vendor/protocol/auth/capability labels, no plaintext API key, and no `is_local`.

- [ ] **Step 6: Browser end-to-end checks**

Verify `/settings` loads without console errors; create and test mocked or available connections for Chat Completions and Embeddings; verify Responses and Anthropic request shapes through contract tests when live credentials are unavailable; verify role filters and legacy rows render correctly.

- [ ] **Step 7: Confirm no legacy runtime types remain**

Run: `rg -n 'openai_compatible|local_openai|is_local' frontend/src backend/app --glob '!backend/.venv/**'`

Expected: no runtime references except explicit migration constants in `model_config_store.py`.

- [ ] **Step 8: Final commit boundary review**

Run: `git diff --check && git status --short && git diff --stat`

Expected: implementation is complete and uncommitted; request user approval before any commit.
