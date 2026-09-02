# MiroFishPlus Brand Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将对外品牌和本项目自有数据库统一为 MiroFishPlus，并无损迁移旧 SQLite 与 Docker 卷。

**Architecture:** Python 数据库迁移入口在任何 Store 初始化前用 SQLite Backup API复制旧统一库。宿主机启动脚本负责旧 Docker 容器停机和命名卷复制，Compose 只声明新的 MiroFishPlus 名称；所有旧文件、容器和卷都保留。

**Tech Stack:** Python 3.11/3.12、SQLite、Bash、Docker Compose、Vue 3、pytest。

**Spec:** `docs/superpowers/specs/2026-09-02-mirofishplus-brand-design.md`

## Global Constraints

- 对外名称统一为 `MiroFishPlus`。
- 新统一数据库固定为 `backend/uploads/mirofishplus.db`。
- 旧 `mirofish.db`、旧 Docker 容器和旧命名卷不得删除。
- OASIS 的 `twitter_simulation.db`、`reddit_simulation.db` 不改名。
- Python 包、API 路径、ID 前缀、logger 名称和环境变量保持兼容。
- 新旧数据库并存时以新数据库为准。
- Docker 卷只有在目标不存在或带完整迁移标记时自动处理；未知的非空目标必须阻止启动。

---

### Task 1: SQLite 品牌迁移

**Files:**
- Modify: `backend/app/models/database.py`
- Modify: `backend/app/__init__.py`
- Modify: `backend/scripts/bootstrap_local.py`
- Modify: `backend/tests/test_unified_database.py`
- Modify: `backend/tests/test_bootstrap_local.py`

**Interfaces:**
- Produces: `legacy_unified_database_path(destination: Path | None = None) -> Path`
- Produces: `migrate_legacy_unified_database(destination: Path | None = None, source: Path | None = None) -> bool`
- Changes: `unified_database_path()` returns `Config.UPLOAD_FOLDER / "mirofishplus.db"`.

- [ ] Write failing tests proving the default path, complete Backup API copy, source retention, idempotency, and new-database precedence.
- [ ] Run `uv run --project backend pytest backend/tests/test_unified_database.py backend/tests/test_bootstrap_local.py -q` and confirm expected failures.
- [ ] Implement the migration under the existing process lock. Copy only when destination is absent, run `PRAGMA integrity_check`, compare every non-SQLite user-table row count, and insert `legacy_mirofish_database_v1` into `app_schema_migrations`.
- [ ] Call migration before importing/constructing `TaskManager`, `ModelConfigStore`, or `TaskStore` in Flask startup and bootstrap CLI.
- [ ] Run focused database and configuration tests and confirm all pass.

### Task 2: Public Brand Surfaces

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/src/views/Home.vue`
- Modify: `backend/app/__init__.py`
- Modify: `backend/run.py`
- Modify: `backend/scripts/bootstrap_local.py`
- Modify: `package.json`
- Modify: `demo.py`
- Modify: `CONTRIBUTING.md`
- Modify: `README.md`
- Modify: `README-EN.md`
- Create: `backend/tests/test_mirofishplus_brand.py`

**Interfaces:**
- Produces: health payload `{"status": "ok", "service": "MiroFishPlus Backend"}`.
- Preserves: official-upstream links and internal compatibility identifiers.

- [ ] Write a failing contract test that reads user-facing files and rejects `MiroFish-Local`, while asserting `MiroFishPlus` in HTML title, health service name, startup output, Demo, contribution guide, and both READMEs.
- [ ] Update public text and metadata. Use the actual current repository URL for badges/examples; do not invent a not-yet-renamed hosting URL.
- [ ] Update database paths and compatibility notes in both READMEs and the local bootstrap documents.
- [ ] Run the brand contract test and frontend build.

### Task 3: Docker Project, Container, and Volume Names

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docker-compose.local.yml`
- Modify: `backend/tests/test_local_bootstrap_compose.py`
- Modify: `backend/tests/test_llm_gateway_configuration.py`
- Modify: `backend/tests/test_start_local_script.py`

**Interfaces:**
- Produces Compose project `mirofishplus`.
- Produces containers `mirofishplus`, `mirofishplus-bootstrap`, `mirofishplus-direct-oauth-gateway`, `mirofishplus-hf-prefetch`, `mirofishplus-neo4j`.
- Produces volumes `mirofishplus_direct_oauth_credentials`, `mirofishplus_huggingface_cache`, `mirofishplus_neo4j_data`, `mirofishplus_neo4j_logs`.

- [ ] Change Compose contract tests first and confirm they fail against old names.
- [ ] Update top-level project name, every `container_name`, local image tag, and explicit volume `name` fields.
- [ ] Update health dependencies and script bootstrap-container inspection to new names.
- [ ] Run `docker compose -f docker-compose.yml -f docker-compose.local.yml config --quiet` and focused tests.

### Task 4: Safe Docker Volume Migration

**Files:**
- Create: `scripts/migrate-docker-volume.sh`
- Modify: `scripts/start-local.sh`
- Create: `backend/tests/test_docker_volume_migration_script.py`
- Modify: `backend/tests/test_start_local_script.py`

**Interfaces:**
- Produces: `scripts/migrate-docker-volume.sh OLD_VOLUME NEW_VOLUME`.
- Consumes: `DOCKER_BIN` and optional `VOLUME_COPY_IMAGE`, defaulting to `docker` and `alpine:3.20`.
- Produces marker: `/.mirofishplus_migration_complete` in the target volume after verification.

- [ ] Write behavioral tests using a fake Docker executable for missing source, successful copy, marked target reuse, unmarked nonempty target refusal, and source preservation.
- [ ] Implement source/target inspection without printing file contents. Stop old named containers in `start-local.sh` without removing them.
- [ ] For each old/new volume pair, create the new volume only when safe, copy from a read-only source mount, compare file count and byte count before writing the marker, and fail closed on mismatch.
- [ ] Run shell syntax checks and behavioral tests.

### Task 5: End-to-End Migration Verification

**Files:**
- Modify: `README.md`
- Modify: `README-EN.md`

**Interfaces:**
- Consumes all prior tasks.

- [ ] Run all backend tests, Gateway tests, frontend build, Compose validation, shell syntax checks, and `git diff --check`.
- [ ] Build temporary SQLite fixtures and prove old-to-new row counts for models, tasks, graph settings, and preparation checkpoints.
- [ ] Create temporary named volumes with representative nested and hidden files; run the migration script twice and verify byte/file counts and source retention.
- [ ] Run `npm run docker:up`, verify every new container/volume name and `/health`, and confirm the application opens `mirofishplus.db`.
- [ ] Keep old containers, old volumes, old database and user uploads intact; report the extra disk usage introduced by copied volumes.
