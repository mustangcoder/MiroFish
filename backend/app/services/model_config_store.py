"""模型连接、角色草稿、版本和项目快照的 SQLite 仓库。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ..models.model_config import (
    APIProtocol,
    AuthType,
    ConfigVersion,
    ConnectionProtocol,
    ModelCapability,
    ModelConnection,
    ModelRole,
    ProtocolSource,
    ProtocolVerificationStatus,
    ProjectModelSnapshot,
    ProviderVendor,
)
from .credential_cipher import CredentialCipher
from .provider_catalog import infer_vendor, protocol_capability


class ModelConfigStore:
    def __init__(self, path: str | Path, cipher: CredentialCipher):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cipher = cipher
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS model_connections (
                    connection_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    connection_type TEXT NOT NULL, base_url TEXT NOT NULL,
                    api_key_encrypted TEXT, api_key_masked TEXT,
                    is_local INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    vendor TEXT, protocol TEXT, auth_type TEXT, capability TEXT
                );
                CREATE TABLE IF NOT EXISTS model_role_drafts (
                    role TEXT PRIMARY KEY, config_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_config_versions (
                    version_id TEXT PRIMARY KEY, assignments_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_config_state (
                    state_key TEXT PRIMARY KEY, state_value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_model_snapshots (
                    project_id TEXT PRIMARY KEY, version_id TEXT NOT NULL,
                    assignments_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_test_runs (
                    test_id TEXT PRIMARY KEY, connection_id TEXT NOT NULL,
                    test_type TEXT NOT NULL, status TEXT NOT NULL,
                    latency_ms INTEGER, error_code TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_connection_protocols (
                    connection_id TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    source TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    last_tested_at TEXT,
                    error_code TEXT,
                    PRIMARY KEY(connection_id, protocol),
                    FOREIGN KEY(connection_id) REFERENCES model_connections(connection_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS memory_backend_config (
                    config_id INTEGER PRIMARY KEY CHECK (config_id = 1),
                    backend TEXT NOT NULL,
                    zep_api_key_encrypted TEXT,
                    zep_api_key_masked TEXT,
                    neo4j_uri TEXT,
                    neo4j_user TEXT,
                    neo4j_password_encrypted TEXT,
                    neo4j_password_masked TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_model_connections_type ON model_connections(connection_type);
                CREATE INDEX IF NOT EXISTS idx_model_test_runs_connection ON model_test_runs(connection_id, created_at DESC);
            """)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(model_connections)").fetchall()
            }
            for column in ("vendor", "protocol", "auth_type", "capability"):
                if column not in columns:
                    connection.execute(f"ALTER TABLE model_connections ADD COLUMN {column} TEXT")
            self._migrate_provider_protocol_schema(connection)
            self._migrate_connection_protocol_rows(connection)
            self._migrate_role_assignment_protocols(connection)

    def _migrate_provider_protocol_schema(self, connection):
        rows = connection.execute(
            "SELECT connection_id,connection_type,base_url,api_key_encrypted,vendor,protocol,auth_type,capability FROM model_connections"
        ).fetchall()
        for row in rows:
            if all(row[key] for key in ("vendor", "protocol", "auth_type", "capability")):
                continue
            legacy_type = row["connection_type"]
            vendor = infer_vendor(row["base_url"])
            if legacy_type == "embedding":
                protocol = APIProtocol.OPENAI_EMBEDDINGS
            else:
                protocol = APIProtocol.OPENAI_CHAT_COMPLETIONS
            if legacy_type == "direct_oauth_gateway":
                vendor = ProviderVendor.CHATGPT_SUBSCRIPTION
                auth_type = AuthType.OAUTH_GATEWAY
            else:
                auth_type = AuthType.API_KEY if row["api_key_encrypted"] else AuthType.NONE
            capability = protocol_capability(protocol)
            connection.execute(
                """
                UPDATE model_connections
                SET vendor=?,protocol=?,auth_type=?,capability=?
                WHERE connection_id=?
                """,
                (vendor.value, protocol.value, auth_type.value, capability.value, row["connection_id"]),
            )
        connection.execute(
            """
            INSERT INTO model_config_state VALUES ('provider_protocol_schema_version', '1')
            ON CONFLICT(state_key) DO UPDATE SET state_value=excluded.state_value
            """
        )

    def migrate_provider_protocol_schema(self):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._migrate_provider_protocol_schema(connection)
            self._migrate_connection_protocol_rows(connection)

    def _migrate_connection_protocol_rows(self, connection):
        connection.execute(
            """
            INSERT OR IGNORE INTO model_connection_protocols (
                connection_id,protocol,capability,source,verification_status
            )
            SELECT legacy.connection_id,legacy.protocol,legacy.capability,'manual','untested'
            FROM model_connections AS legacy
            WHERE legacy.protocol IS NOT NULL AND legacy.capability IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM model_connection_protocols AS current
                  WHERE current.connection_id=legacy.connection_id
              )
            """
        )

    @staticmethod
    def _assignment_protocol(connection, connection_id):
        row = connection.execute(
            "SELECT protocol FROM model_connection_protocols WHERE connection_id=? ORDER BY rowid LIMIT 1",
            (connection_id,),
        ).fetchone()
        return row[0] if row else None

    def _migrate_role_assignment_protocols(self, connection):
        for row in connection.execute("SELECT role,config_json FROM model_role_drafts").fetchall():
            config = json.loads(row["config_json"])
            if config.get("connection_id") and not config.get("protocol"):
                protocol = self._assignment_protocol(connection, config["connection_id"])
                if protocol:
                    config["protocol"] = protocol
                    connection.execute(
                        "UPDATE model_role_drafts SET config_json=? WHERE role=?",
                        (json.dumps(config, ensure_ascii=False), row["role"]),
                    )
        for table, key in (("model_config_versions", "version_id"), ("project_model_snapshots", "project_id")):
            rows = connection.execute(f"SELECT {key},assignments_json FROM {table}").fetchall()
            for row in rows:
                assignments = json.loads(row["assignments_json"])
                changed = False
                for config in assignments.values():
                    if config.get("connection_id") and not config.get("protocol"):
                        protocol = self._assignment_protocol(connection, config["connection_id"])
                        if protocol:
                            config["protocol"] = protocol
                            changed = True
                if changed:
                    connection.execute(
                        f"UPDATE {table} SET assignments_json=? WHERE {key}=?",
                        (json.dumps(assignments, ensure_ascii=False), row[key]),
                    )
        connection.execute(
            """
            INSERT INTO model_config_state VALUES ('multi_protocol_assignment_version', '1')
            ON CONFLICT(state_key) DO UPDATE SET state_value=excluded.state_value
            """
        )
        connection.execute(
            """
            INSERT INTO model_config_state VALUES ('multi_protocol_schema_version', '1')
            ON CONFLICT(state_key) DO UPDATE SET state_value=excluded.state_value
            """
        )

    def save_memory_backend_config(self, config):
        current = self.get_memory_backend_secrets()
        zep_api_key = config.get("zep_api_key") or current.get("zep_api_key", "")
        neo4j_password = config.get("neo4j_password") or current.get("neo4j_password", "")
        values = (
            config["backend"],
            self.cipher.encrypt(zep_api_key) if zep_api_key else None,
            self.cipher.mask(zep_api_key) if zep_api_key else None,
            config.get("neo4j_uri", ""),
            config.get("neo4j_user", ""),
            self.cipher.encrypt(neo4j_password) if neo4j_password else None,
            self.cipher.mask(neo4j_password) if neo4j_password else None,
            datetime.now().isoformat(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_backend_config VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(config_id) DO UPDATE SET
                    backend=excluded.backend,
                    zep_api_key_encrypted=excluded.zep_api_key_encrypted,
                    zep_api_key_masked=excluded.zep_api_key_masked,
                    neo4j_uri=excluded.neo4j_uri,
                    neo4j_user=excluded.neo4j_user,
                    neo4j_password_encrypted=excluded.neo4j_password_encrypted,
                    neo4j_password_masked=excluded.neo4j_password_masked,
                    updated_at=excluded.updated_at
                """,
                values,
            )

    def get_memory_backend_config(self):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT backend,zep_api_key_masked,neo4j_uri,neo4j_user,neo4j_password_masked FROM memory_backend_config WHERE config_id=1"
            ).fetchone()
        return dict(row) if row else None

    def get_memory_backend_secrets(self):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT zep_api_key_encrypted,neo4j_password_encrypted FROM memory_backend_config WHERE config_id=1"
            ).fetchone()
        if row is None:
            return {}
        return {
            "zep_api_key": self.cipher.decrypt(row["zep_api_key_encrypted"]) if row["zep_api_key_encrypted"] else "",
            "neo4j_password": self.cipher.decrypt(row["neo4j_password_encrypted"]) if row["neo4j_password_encrypted"] else "",
        }

    def create_connection(self, name, vendor, protocol, auth_type, capability, base_url, api_key):
        vendor = ProviderVendor(vendor)
        protocol = APIProtocol(protocol)
        auth_type = AuthType(auth_type)
        capability = ModelCapability(capability)
        now = datetime.now().isoformat()
        connection_id = f"conn_{uuid.uuid4().hex[:12]}"
        encrypted = self.cipher.encrypt(api_key) if api_key else None
        masked = self.cipher.mask(api_key) if api_key else None
        legacy_type = "embedding" if capability == ModelCapability.EMBEDDING else "openai_compatible"
        if auth_type == AuthType.OAUTH_GATEWAY:
            legacy_type = "direct_oauth_gateway"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO model_connections (
                    connection_id,name,connection_type,base_url,
                    api_key_encrypted,api_key_masked,is_local,enabled,
                    created_at,updated_at,vendor,protocol,auth_type,capability
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    connection_id, name, legacy_type, base_url, encrypted, masked,
                    now, now, vendor.value, protocol.value, auth_type.value, capability.value,
                ),
            )
            connection.execute(
                """
                INSERT INTO model_connection_protocols (
                    connection_id,protocol,capability,source,verification_status
                ) VALUES (?, ?, ?, 'manual', 'untested')
                """,
                (connection_id, protocol.value, capability.value),
            )
        return self.get_connection(connection_id)

    def import_environment_models(self, specs):
        now = datetime.now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            imported = connection.execute(
                "SELECT state_value FROM model_config_state WHERE state_key='environment_imported'"
            ).fetchone()
            if imported is not None:
                return False
            for spec in specs:
                connection_id = f"conn_{uuid.uuid4().hex[:12]}"
                api_key = spec.get("api_key", "")
                connection.execute(
                    """
                    INSERT INTO model_connections (
                        connection_id,name,connection_type,base_url,
                        api_key_encrypted,api_key_masked,is_local,enabled,
                        created_at,updated_at,vendor,protocol,auth_type,capability
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        connection_id,
                        spec["name"],
                        spec.get("connection_type", "embedding" if spec.get("capability") == "embedding" else "openai_compatible"),
                        spec["base_url"],
                        self.cipher.encrypt(api_key) if api_key else None,
                        self.cipher.mask(api_key) if api_key else None,
                        now,
                        now,
                        ProviderVendor(spec.get("vendor", infer_vendor(spec["base_url"]))).value,
                        APIProtocol(spec.get("protocol", APIProtocol.OPENAI_EMBEDDINGS if spec.get("connection_type") == "embedding" else APIProtocol.OPENAI_CHAT_COMPLETIONS)).value,
                        AuthType(spec.get("auth_type", AuthType.API_KEY if api_key else AuthType.NONE)).value,
                        ModelCapability(spec.get("capability", ModelCapability.EMBEDDING if spec.get("connection_type") == "embedding" else ModelCapability.TEXT_GENERATION)).value,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO model_connection_protocols (
                        connection_id,protocol,capability,source,verification_status
                    ) VALUES (?, ?, ?, 'manual', 'untested')
                    """,
                    (
                        connection_id,
                        APIProtocol(spec.get("protocol", APIProtocol.OPENAI_EMBEDDINGS if spec.get("connection_type") == "embedding" else APIProtocol.OPENAI_CHAT_COMPLETIONS)).value,
                        ModelCapability(spec.get("capability", ModelCapability.EMBEDDING if spec.get("connection_type") == "embedding" else ModelCapability.TEXT_GENERATION)).value,
                    ),
                )
                config = {"connection_id": connection_id, "protocol": spec["protocol"], "model": spec["model"]}
                connection.execute(
                    "INSERT INTO model_role_drafts VALUES (?, ?, ?)",
                    (ModelRole(spec["role"]).value, json.dumps(config, ensure_ascii=False), now),
                )
            connection.execute(
                "INSERT INTO model_config_state VALUES ('environment_imported', '1')"
            )
        return True

    def _protocols_for(self, connection, connection_id):
        rows = connection.execute(
            "SELECT * FROM model_connection_protocols WHERE connection_id=? ORDER BY rowid",
            (connection_id,),
        ).fetchall()
        return tuple(ConnectionProtocol(
            APIProtocol(item["protocol"]), ModelCapability(item["capability"]),
            ProtocolSource(item["source"]), ProtocolVerificationStatus(item["verification_status"]),
            item["last_tested_at"], item["error_code"],
        ) for item in rows)

    def _public_connection(self, row, protocols=()):
        return ModelConnection(
            row["connection_id"], row["name"], ProviderVendor(row["vendor"]),
            APIProtocol(row["protocol"]), AuthType(row["auth_type"]),
            ModelCapability(row["capability"]), row["base_url"], row["api_key_masked"],
            bool(row["enabled"]), row["created_at"], row["updated_at"], protocols,
        )

    def get_connection(self, connection_id):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM model_connections WHERE connection_id=?", (connection_id,)).fetchone()
            protocols = self._protocols_for(connection, connection_id) if row else ()
        if row is None:
            raise KeyError(connection_id)
        return self._public_connection(row, protocols)

    def list_connections(self):
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM model_connections ORDER BY created_at").fetchall()
            values = [(row, self._protocols_for(connection, row["connection_id"])) for row in rows]
        return [self._public_connection(row, protocols) for row, protocols in values]

    def list_connection_protocols(self, connection_id):
        with self._connect() as connection:
            exists = connection.execute("SELECT 1 FROM model_connections WHERE connection_id=?", (connection_id,)).fetchone()
            if not exists:
                raise KeyError(connection_id)
            return self._protocols_for(connection, connection_id)

    def replace_connection_protocols(self, connection_id, protocols):
        protocols = list(protocols)
        if not protocols:
            raise ValueError("连接至少需要启用一个协议")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT 1 FROM model_connections WHERE connection_id=?", (connection_id,)).fetchone():
                raise KeyError(connection_id)
            connection.execute("DELETE FROM model_connection_protocols WHERE connection_id=?", (connection_id,))
            for item in protocols:
                connection.execute(
                    """
                    INSERT INTO model_connection_protocols VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        connection_id, APIProtocol(item["protocol"]).value,
                        ModelCapability(item["capability"]).value,
                        ProtocolSource(item.get("source", ProtocolSource.MANUAL)).value,
                        ProtocolVerificationStatus(item.get("verification_status", ProtocolVerificationStatus.UNTESTED)).value,
                        item.get("last_tested_at"), item.get("error_code"),
                    ),
                )
        return self.list_connection_protocols(connection_id)

    def get_connection_secret(self, connection_id):
        with self._connect() as connection:
            row = connection.execute("SELECT api_key_encrypted FROM model_connections WHERE connection_id=?", (connection_id,)).fetchone()
        if row is None:
            raise KeyError(connection_id)
        return self.cipher.decrypt(row[0]) if row[0] else ""

    def delete_connection(self, connection_id):
        draft = self.get_draft()
        if any(value.get("connection_id") == connection_id for value in draft.values()):
            raise ValueError("连接正在被模型角色使用")
        with self._connect() as connection:
            connection.execute("DELETE FROM model_connections WHERE connection_id=?", (connection_id,))

    def update_connection(self, connection_id, **changes):
        current = self.get_connection(connection_id)
        api_key = changes.pop("api_key", None)
        values = {
            "name": changes.get("name", current.name),
            "base_url": changes.get("base_url", current.base_url),
            "enabled": int(changes.get("enabled", current.enabled)),
            "vendor": ProviderVendor(changes.get("vendor", current.vendor)).value,
            "protocol": APIProtocol(changes.get("protocol", current.protocol)).value,
            "auth_type": AuthType(changes.get("auth_type", current.auth_type)).value,
            "capability": ModelCapability(changes.get("capability", current.capability)).value,
            "updated_at": datetime.now().isoformat(),
        }
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE model_connections SET
                    name=:name,base_url=:base_url,enabled=:enabled,
                    vendor=:vendor,protocol=:protocol,auth_type=:auth_type,
                    capability=:capability,updated_at=:updated_at
                WHERE connection_id=:connection_id
                """,
                {**values, "connection_id": connection_id},
            )
            if api_key:
                connection.execute("UPDATE model_connections SET api_key_encrypted=?, api_key_masked=? WHERE connection_id=?", (self.cipher.encrypt(api_key), self.cipher.mask(api_key), connection_id))
        return self.get_connection(connection_id)

    def get_state(self, key):
        with self._connect() as connection:
            row = connection.execute("SELECT state_value FROM model_config_state WHERE state_key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key, value):
        with self._connect() as connection:
            connection.execute("INSERT INTO model_config_state VALUES (?, ?) ON CONFLICT(state_key) DO UPDATE SET state_value=excluded.state_value", (key, value))

    def list_versions(self):
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM model_config_versions ORDER BY created_at DESC").fetchall()
        return [ConfigVersion(row["version_id"], self._decode(row["assignments_json"]), row["created_at"]) for row in rows]

    def record_test(self, connection_id, test_type, status, latency_ms, error_code=None):
        now = datetime.now().isoformat()
        with self._connect() as connection:
            connection.execute("INSERT INTO model_test_runs VALUES (?, ?, ?, ?, ?, ?, ?)", (f"test_{uuid.uuid4().hex[:12]}", connection_id, test_type, status, latency_ms, error_code, now))

    def latest_test(self, connection_id, test_type=None):
        with self._connect() as connection:
            if test_type:
                row = connection.execute(
                    "SELECT test_type,status,latency_ms,error_code,created_at FROM model_test_runs WHERE connection_id=? AND test_type=? ORDER BY created_at DESC LIMIT 1",
                    (connection_id, test_type),
                ).fetchone()
            else:
                row = connection.execute("SELECT test_type,status,latency_ms,error_code,created_at FROM model_test_runs WHERE connection_id=? ORDER BY created_at DESC LIMIT 1", (connection_id,)).fetchone()
        return dict(row) if row else None

    def save_draft(self, assignments):
        now = datetime.now().isoformat()
        with self._connect() as connection:
            for role, config in assignments.items():
                role = ModelRole(role)
                connection.execute(
                    "INSERT INTO model_role_drafts VALUES (?, ?, ?) ON CONFLICT(role) DO UPDATE SET config_json=excluded.config_json, updated_at=excluded.updated_at",
                    (role.value, json.dumps(config, ensure_ascii=False), now),
                )

    def get_draft(self):
        with self._connect() as connection:
            rows = connection.execute("SELECT role, config_json FROM model_role_drafts").fetchall()
        return {ModelRole(row["role"]): json.loads(row["config_json"]) for row in rows}

    @staticmethod
    def _encode(assignments):
        return json.dumps({ModelRole(role).value: config for role, config in assignments.items()}, ensure_ascii=False)

    @staticmethod
    def _decode(value):
        return {ModelRole(role): config for role, config in json.loads(value).items()}

    def apply_draft(self):
        assignments = self.get_draft()
        if set(assignments) != set(ModelRole):
            raise ValueError("三个模型角色必须全部配置")
        version_id = f"modelcfg_{uuid.uuid4().hex[:12]}"
        now = datetime.now().isoformat()
        encoded = self._encode(assignments)
        with self._connect() as connection:
            connection.execute("INSERT INTO model_config_versions VALUES (?, ?, ?)", (version_id, encoded, now))
            connection.execute("INSERT INTO model_config_state VALUES ('active_version', ?) ON CONFLICT(state_key) DO UPDATE SET state_value=excluded.state_value", (version_id,))
        return ConfigVersion(version_id, assignments, now)

    def get_active_version(self):
        with self._connect() as connection:
            row = connection.execute("SELECT state_value FROM model_config_state WHERE state_key='active_version'").fetchone()
            if row is None:
                return None
            version = connection.execute("SELECT * FROM model_config_versions WHERE version_id=?", (row[0],)).fetchone()
        return ConfigVersion(version["version_id"], self._decode(version["assignments_json"]), version["created_at"])

    def get_or_create_project_snapshot(self, project_id):
        existing = self.get_project_snapshot(project_id)
        if existing:
            return existing
        active = self.get_active_version()
        if active is None:
            raise ValueError("尚未应用模型配置")
        now = datetime.now().isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO project_model_snapshots VALUES (?, ?, ?, ?)",
                (project_id, active.version_id, self._encode(active.assignments), now),
            )
        return self.get_project_snapshot(project_id)

    def get_project_snapshot(self, project_id):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM project_model_snapshots WHERE project_id=?", (project_id,)).fetchone()
        if row is None:
            return None
        return ProjectModelSnapshot(row["project_id"], row["version_id"], self._decode(row["assignments_json"]), row["created_at"])
