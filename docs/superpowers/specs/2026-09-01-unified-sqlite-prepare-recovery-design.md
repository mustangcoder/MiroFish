# Unified SQLite and Preparation Recovery Design

## Goal

Consolidate every MiroFish-owned SQLite table into `backend/uploads/mirofish.db` and make environment preparation resume from per-entity SQLite checkpoints after an abnormal service restart. OASIS-owned Twitter and Reddit databases remain isolated per simulation.

## Database Boundary

The unified database owns:

- model connections and protocol capabilities;
- encrypted-secret references and masked values;
- model-role drafts, applied versions, and project snapshots;
- memory-backend configuration;
- task history;
- preparation runs and per-entity profile checkpoints.

The following third-party runtime databases remain separate:

- `twitter_simulation.db`
- `reddit_simulation.db`

JSON project and simulation artifacts remain files. The requirement consolidates SQLite storage, not every persistence format.

## Unified Path and Connection Policy

The single MiroFish database path is `Config.UPLOAD_FOLDER / "mirofish.db"`.

Every connection enables:

- `PRAGMA journal_mode=WAL`
- `PRAGMA foreign_keys=ON`
- `PRAGMA busy_timeout=5000`

Stores continue to own their table-specific queries, but obtain the same database path from a shared path helper. They must not create independent default `.db` paths.

## Legacy Migration

Before API registration, a migration coordinator initializes the destination and imports:

- `backend/uploads/model-config/models.db`
- `backend/uploads/tasks/tasks.db`

The coordinator uses `ATTACH DATABASE` and explicit table lists inside an immediate transaction. Destination rows use `INSERT OR IGNORE` so retries are idempotent. It records migration versions in `app_schema_migrations` and verifies source/destination counts before committing.

Legacy databases are retained unchanged as backups. Existing `.bak-*` files and the encryption master key are never deleted or moved.

## Preparation Schema

`simulation_prepare_runs`:

- `run_id TEXT PRIMARY KEY`
- `simulation_id TEXT NOT NULL`
- `project_id TEXT NOT NULL`
- `graph_id TEXT NOT NULL`
- `task_id TEXT NOT NULL`
- `stage TEXT NOT NULL`
- `status TEXT NOT NULL`
- `input_fingerprint TEXT`
- `model_version_id TEXT`
- `total_entities INTEGER NOT NULL DEFAULT 0`
- `completed_entities INTEGER NOT NULL DEFAULT 0`
- `config_generated INTEGER NOT NULL DEFAULT 0`
- `params_json TEXT NOT NULL`
- `generation INTEGER NOT NULL DEFAULT 1`
- `error TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

`simulation_prepare_profiles`:

- `run_id TEXT NOT NULL`
- `entity_uuid TEXT NOT NULL`
- `entity_index INTEGER NOT NULL`
- `user_id INTEGER NOT NULL`
- `entity_type TEXT`
- `status TEXT NOT NULL`
- `profile_json TEXT`
- `attempts INTEGER NOT NULL DEFAULT 0`
- `error TEXT`
- `updated_at TEXT NOT NULL`
- primary key `(run_id, entity_uuid)`
- foreign key `run_id` with cascade delete, although application workflows retain superseded runs.

A partial unique index permits one `pending` or `processing` run per simulation.

## Checkpoint Semantics

After each successful profile generation, one SQLite transaction:

1. upserts the complete `OasisAgentProfile.to_dict()` payload;
2. preserves the original entity index and user ID;
3. marks the entity completed;
4. recalculates and persists the completed count;
5. updates the run timestamp.

A crash before commit repeats at most that one entity. A crash after commit reuses it.

## Input Identity

The run fingerprint hashes:

- project ID and graph ID;
- ordered entity UUIDs and entity types;
- relevant preparation parameters;
- project model snapshot version.

If identity changes, the previous run becomes `superseded`; a new generation is created without deleting old rows.

## Preparation Runner

Extract the API-local background closure into `SimulationPreparationRunner`.

Responsibilities:

- enforce a per-simulation process lock;
- create or recover the active database run;
- expose the stable task ID;
- restore completed profiles by entity UUID;
- submit only missing entities;
- preserve deterministic output order and user IDs;
- materialize Reddit and Twitter output files after all profiles complete;
- continue simulation-config generation when needed;
- mark the simulation and run `ready/completed` only after artifacts are durable.

The existing preparation API delegates to this runner. Repeated page calls return the same active task.

## Startup Recovery

Application startup scans `pending` and `processing` runs after stores and blueprints are initialized. For each valid run it starts one background resume worker. The coordinator is guarded against Flask debug-reloader duplication.

If a persisted task was marked interrupted during startup, recovery reuses its task ID and updates it back to processing. A mismatched or unrecoverable run becomes failed with an actionable error rather than silently restarting from zero.

## UI Behavior

Prepare status returns:

- stable task ID;
- stage;
- total/completed profile counts;
- whether execution was recovered;
- latest persisted timestamp.

The page displays `已从检查点恢复 X/Y` and resumes polling. It never creates another preparation run while an active SQLite run exists.

## Failure and Data Safety

- Never delete legacy databases during migration.
- Never delete superseded preparation runs automatically.
- Never overwrite completed profile checkpoints with a failed attempt.
- Never log profile bodies, prompts, credentials, or decrypted secrets.
- OASIS runtime databases remain untouched.

## Testing

- Both legacy databases migrate into one destination without duplicates.
- Re-running migration is idempotent.
- Existing encrypted secrets remain decryptable with the existing key.
- All default MiroFish stores use `mirofish.db`.
- Per-profile transaction persists payload and count together.
- Completed profiles restore in original order with original user IDs.
- Only missing profiles are submitted after restart.
- Changed graph/entity/model identity supersedes rather than reuses a run.
- Concurrent resume attempts produce one active worker/run.
- Startup recovery resumes processing and reuses the task ID.
- Final artifacts and ready state are produced after resume.
- Existing OASIS database paths remain unchanged.
- Full backend, gateway, frontend, migration, and Docker verification pass.
