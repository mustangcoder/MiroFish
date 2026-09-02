# Local Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过 `npm run docker:up` 幂等完成本地配置准备、SQLite 初始化、Docker 服务启动和健康检查。

**Architecture:** `scripts/start-local.sh` 负责宿主机编排，`backend/scripts/bootstrap_local.py` 负责容器内数据库初始化。Compose 使用一次性 `bootstrap` 服务建立明确的健康依赖链，Flask 保留现有启动初始化作为防御性保障。

**Tech Stack:** Bash、npm scripts、Docker Compose、Python 3.11、SQLite、pytest。

**Spec:** `docs/superpowers/specs/2026-09-02-local-bootstrap-design.md`

## Global Constraints

- 唯一推荐入口为 `npm run docker:up`。
- 缺失 `.env` 时从 `.env.example` 创建，已有 `.env` 不覆盖。
- 所有初始化必须幂等，不删除数据库、上传文件或 Docker 卷。
- MiroFish 自有 SQLite 数据继续使用 `backend/uploads/mirofish.db`。
- OASIS Twitter/Reddit SQLite 数据库保持独立。
- Neo4j 固定使用项目现有版本 `5.26.0`。
- 初始化失败时主应用不得启动，并保留诊断现场。

---

### Task 1: 数据库初始化 CLI

**Files:**
- Create: `backend/scripts/bootstrap_local.py`
- Create: `backend/tests/test_bootstrap_local.py`
- Modify: `backend/app/models/database.py`

**Interfaces:**
- Consumes: `unified_database_path()`、`initialize_unified_database()`、`ModelConfigStore`、`TaskStore`、`SimulationPrepareStore`、`MemoryBackendConfigService`。
- Produces: `bootstrap(database_path: Path | None = None) -> dict`，返回数据库路径、关键表列表及配置初始化状态；CLI 成功输出 JSON 并返回 `0`。

- [ ] **Step 1: Write failing tests for empty and repeated initialization**

```python
def test_bootstrap_creates_all_required_tables_and_is_idempotent(tmp_path, monkeypatch):
    result1 = bootstrap(tmp_path / "mirofish.db")
    result2 = bootstrap(tmp_path / "mirofish.db")
    assert result1["required_tables"] == result2["required_tables"]
    assert {"model_connections", "task_history", "simulation_prepare_runs"} <= set(result2["required_tables"])
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `uv run --project backend pytest backend/tests/test_bootstrap_local.py -q`

Expected: FAIL because `backend.scripts.bootstrap_local` does not exist.

- [ ] **Step 3: Implement the bootstrap function and CLI**

The implementation must initialize stores in this order:

```python
ModelConfigStore(path, CredentialCipher(key_path))
TaskStore(path)
initialize_unified_database(path)
ModelConfigStore(path, CredentialCipher(key_path))
SimulationPrepareStore(path)
MemoryBackendConfigService(store=model_store).initialize_from_environment()
```

After initialization, query `sqlite_master`, verify the literal required-table set, execute a `BEGIN IMMEDIATE`/`ROLLBACK` write-lock probe, and return a secret-free summary.

- [ ] **Step 4: Verify focused database tests**

Run: `uv run --project backend pytest backend/tests/test_bootstrap_local.py backend/tests/test_unified_database.py backend/tests/test_memory_backend_config_service.py -q`

Expected: PASS.

---

### Task 2: Compose 初始化依赖链

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docker-compose.local.yml`
- Create: `backend/tests/test_local_bootstrap_compose.py`

**Interfaces:**
- Consumes: `backend/scripts/bootstrap_local.py` CLI。
- Produces: Compose 服务 `bootstrap`；`mirofish` 依赖 `bootstrap` 成功；本地覆盖为 `bootstrap` 与 `mirofish` 设置 `ZEP_BACKEND=graphiti` 和 `NEO4J_URI=bolt://neo4j:7687`。

- [ ] **Step 1: Write failing Compose contract tests**

```python
def test_compose_has_successful_bootstrap_dependency():
    config = yaml.safe_load(Path("docker-compose.yml").read_text())
    assert config["services"]["bootstrap"]["restart"] == "no"
    assert config["services"]["mirofish"]["depends_on"]["bootstrap"]["condition"] == "service_completed_successfully"
```

Also assert the local override retains `neo4j:5.26.0`, injects `bolt://neo4j:7687`, and makes bootstrap depend on Neo4j health.

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `uv run --project backend pytest backend/tests/test_local_bootstrap_compose.py -q`

Expected: FAIL because the bootstrap service is absent.

- [ ] **Step 3: Add bootstrap service and health dependencies**

Use the existing Dockerfile and uploads mount. The service command is:

```yaml
command: ["uv", "run", "--project", "backend", "python", "backend/scripts/bootstrap_local.py"]
restart: "no"
```

Add a MiroFish healthcheck against `http://127.0.0.1:5001/health`. In the local override, merge Neo4j health dependencies into both `bootstrap` and `mirofish`.

- [ ] **Step 4: Validate merged Compose configuration**

Run: `docker compose -f docker-compose.yml -f docker-compose.local.yml config --quiet`

Expected: exit code `0`.

---

### Task 3: 宿主机一键启动脚本

**Files:**
- Create: `scripts/start-local.sh`
- Modify: `package.json`
- Create: `backend/tests/test_start_local_script.py`

**Interfaces:**
- Consumes: Docker Compose files and `bootstrap` service from Task 2。
- Produces: `npm run docker:up`；脚本可通过 `DOCKER_BIN` 注入测试替身，默认值为 `docker`。

- [ ] **Step 1: Write failing behavioral tests with a fake Docker executable**

Tests run the actual shell script in a temporary copied project fixture and assert:

```python
assert (fixture / ".env").read_text() == (fixture / ".env.example").read_text()
assert "compose -f docker-compose.yml -f docker-compose.local.yml up -d --build" in recorded_calls
```

Add cases proving an existing `.env` remains byte-for-byte unchanged and Docker unavailability returns nonzero without invoking Compose.

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `uv run --project backend pytest backend/tests/test_start_local_script.py -q`

Expected: FAIL because `scripts/start-local.sh` does not exist.

- [ ] **Step 3: Implement strict startup orchestration**

The shell script must use:

```bash
set -euo pipefail
DOCKER_BIN="${DOCKER_BIN:-docker}"
COMPOSE=("$DOCKER_BIN" compose -f docker-compose.yml -f docker-compose.local.yml)
```

It checks Docker, copies `.env.example` only when needed, creates `backend/uploads`, executes Compose `up -d --build`, checks bootstrap exit code with `docker compose ps -a`, waits up to 180 seconds for backend `/health`, and prints diagnostic commands on failure.

- [ ] **Step 4: Add npm entry and verify behavior tests**

Add `"docker:up": "bash scripts/start-local.sh"` to `package.json`.

Run: `uv run --project backend pytest backend/tests/test_start_local_script.py -q`

Expected: PASS.

---

### Task 4: 文档与端到端验证

**Files:**
- Modify: `README.md`
- Modify: `README-EN.md`

**Interfaces:**
- Consumes: `npm run docker:up`。
- Produces: 中文和英文的一键启动说明、初始化行为、失败诊断和保留的高级 Compose 命令。

- [ ] **Step 1: Document the one-command flow**

Document:

```bash
npm run docker:up
```

Explain `.env` auto-creation, `mirofish.db` initialization, service URLs, idempotency, and that existing data is never reset.

- [ ] **Step 2: Run all automated verification**

Run:

```bash
uv run --project backend pytest backend/tests -q
uv run --project direct_gateway pytest direct_gateway/tests -q
npm run build
git diff --check
```

Expected: all commands exit `0`.

- [ ] **Step 3: Run Docker end-to-end verification**

Run `npm run docker:up` twice. After each run verify:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml ps
curl --fail http://localhost:5001/health
```

Query `backend/uploads/mirofish.db` before and after the second run and confirm configuration/task row counts do not increase merely because startup repeated. Do not remove volumes or files after verification.
