# Unified SQLite and Preparation Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate MiroFish-owned SQLite data into `mirofish.db` and resume environment preparation from durable per-profile checkpoints.

**Architecture:** A shared database module owns the path, schema migration, and legacy imports. Existing model/task stores retain focused query APIs while sharing one file. A preparation checkpoint store and runner persist each completed profile transactionally and recover active work after restart.

**Tech Stack:** Python 3.11/3.12, SQLite WAL, Flask, pytest, Vue 3, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-01-unified-sqlite-prepare-recovery-design.md`

## Global Constraints

- Use `backend/uploads/mirofish.db` for all MiroFish-owned SQLite tables.
- Keep OASIS Twitter/Reddit databases separate.
- Retain legacy databases and all superseded checkpoint rows.
- Preserve encrypted credential compatibility.
- Use atomic SQLite transactions per completed profile.
- Do not delete user data.

---

### Task 1: Unified Database and Legacy Migration

**Files:**
- Create: `backend/app/models/database.py`
- Modify: `backend/app/__init__.py`
- Modify: `backend/app/models/task.py`
- Modify: `backend/app/services/model_config_service.py`
- Modify: `backend/app/services/model_router.py`
- Modify: `backend/app/services/memory_backend_config_service.py`
- Test: `backend/tests/test_unified_database.py`

- [ ] Write failing tests for path unification, model/task migration, idempotency, and retained source files.
- [ ] Run `uv run --project backend pytest backend/tests/test_unified_database.py -q` and confirm failure.
- [ ] Implement `unified_database_path()` and `initialize_unified_database()` with explicit legacy table imports and migration markers.
- [ ] Point all default stores at `mirofish.db`.
- [ ] Run focused model/task/unified tests.

### Task 2: Preparation Checkpoint Store

**Files:**
- Create: `backend/app/services/simulation_prepare_store.py`
- Test: `backend/tests/test_simulation_prepare_store.py`

- [ ] Write failing tests for run creation, active uniqueness, profile transaction, ordered restore, supersede, and progress counts.
- [ ] Implement schema and transactional repository methods.
- [ ] Verify concurrent calls retain one active run.
- [ ] Run focused tests.

### Task 3: Resumable Profile Generation

**Files:**
- Modify: `backend/app/services/oasis_profile_generator.py`
- Modify: `backend/app/services/simulation_manager.py`
- Test: `backend/tests/test_simulation_prepare_resume.py`

- [ ] Write failing tests proving completed profiles are not regenerated and order/user IDs are preserved.
- [ ] Add existing-profile restoration and per-profile checkpoint callback support.
- [ ] Generate only missing entities and materialize complete output files.
- [ ] Run focused tests.

### Task 4: Shared Preparation Runner and Startup Recovery

**Files:**
- Create: `backend/app/services/simulation_preparation_runner.py`
- Modify: `backend/app/api/simulation.py`
- Modify: `backend/app/__init__.py`
- Modify: `backend/app/models/task.py`
- Test: `backend/tests/test_simulation_preparation_runner.py`

- [ ] Write failing tests for stable task reuse, interrupted-task revival, one worker per simulation, and startup resume.
- [ ] Extract API preparation execution into the runner.
- [ ] Register startup recovery after app initialization.
- [ ] Return persisted recovery progress from prepare/status APIs.
- [ ] Run focused tests.

### Task 5: UI Recovery Status

**Files:**
- Modify: `frontend/src/components/Step2EnvSetup.vue`
- Modify: `locales/zh.json`
- Modify: `locales/en.json`
- Test: `backend/tests/test_simulation_refresh_ui.py`

- [ ] Add a failing frontend contract test for checkpoint recovery copy and stable task reuse.
- [ ] Render recovered X/Y status and continue polling the persisted task.
- [ ] Run frontend build and UI contract tests.

### Task 6: Migration and End-to-End Verification

**Files:**
- Modify: `README.md`
- Modify: `README-EN.md`

- [ ] Run all backend and gateway tests plus frontend build and `git diff --check`.
- [ ] Build Docker services.
- [ ] Verify `mirofish.db` contains model, task, and preparation tables.
- [ ] Verify legacy DB files still exist and row counts match.
- [ ] Run a restart recovery probe proving completed profile rows are reused.
