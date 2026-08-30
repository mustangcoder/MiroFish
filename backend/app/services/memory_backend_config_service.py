"""记忆后端配置的 SQLite 持久化与运行时应用。"""

import os
import time
from pathlib import Path
from urllib.parse import urlparse

from ..config import Config
from .credential_cipher import CredentialCipher
from .model_config_store import ModelConfigStore


class MemoryBackendConfigService:
    def __init__(self, store=None, environment=None):
        root = Path(Config.UPLOAD_FOLDER) / "model-config"
        self.store = store or ModelConfigStore(root / "models.db", CredentialCipher(root / "master.key"))
        self.environment = environment if environment is not None else os.environ

    def initialize_from_environment(self):
        if self.store.get_memory_backend_config() is not None:
            return
        self.store.save_memory_backend_config({
            "backend": self.environment.get("ZEP_BACKEND", Config.ZEP_BACKEND),
            "zep_api_key": self.environment.get("ZEP_API_KEY", Config.ZEP_API_KEY or ""),
            "neo4j_uri": self.environment.get("NEO4J_URI", Config.NEO4J_URI),
            "neo4j_user": self.environment.get("NEO4J_USER", Config.NEO4J_USER),
            "neo4j_password": self.environment.get("NEO4J_PASSWORD", Config.NEO4J_PASSWORD),
        })

    def get_config(self):
        return self.store.get_memory_backend_config()

    def get_secrets(self):
        return self.store.get_memory_backend_secrets()

    def validate(self, config):
        backend = config.get("backend")
        if backend not in {"cloud", "graphiti"}:
            raise ValueError("记忆后端必须是 cloud 或 graphiti")
        current_secrets = self.get_secrets()
        if backend == "cloud" and not (config.get("zep_api_key") or current_secrets.get("zep_api_key")):
            raise ValueError("Zep Cloud 模式需要 ZEP API Key")
        if backend == "graphiti":
            uri = config.get("neo4j_uri", "")
            if urlparse(uri).scheme not in {"bolt", "neo4j", "bolt+s", "neo4j+s", "bolt+ssc", "neo4j+ssc"}:
                raise ValueError("Neo4j URI 必须使用 bolt 或 neo4j 协议")
            if not config.get("neo4j_user"):
                raise ValueError("Neo4j 用户名不能为空")
            if not (config.get("neo4j_password") or current_secrets.get("neo4j_password")):
                raise ValueError("Neo4j 密码不能为空")
        return config

    def save_config(self, config):
        self.validate(config)
        self.store.save_memory_backend_config(config)
        return self.get_config()

    def apply_runtime_config(self):
        public = self.get_config()
        if public is None:
            return
        secrets = self.get_secrets()
        Config.ZEP_BACKEND = public["backend"]
        Config.ZEP_API_KEY = secrets.get("zep_api_key", "")
        Config.NEO4J_URI = public.get("neo4j_uri", "")
        Config.NEO4J_USER = public.get("neo4j_user", "")
        Config.NEO4J_PASSWORD = secrets.get("neo4j_password", "")
        from .zep_factory import reset_zep_client
        reset_zep_client()

    def test_connection(self, config):
        self.validate(config)
        secrets = self.get_secrets()
        started_at = time.monotonic()
        if config["backend"] == "cloud":
            from zep_cloud.client import Zep
            api_key = config.get("zep_api_key") or secrets.get("zep_api_key", "")
            Zep(api_key=api_key).project.get()
        else:
            from neo4j import GraphDatabase
            password = config.get("neo4j_password") or secrets.get("neo4j_password", "")
            driver = GraphDatabase.driver(
                config["neo4j_uri"],
                auth=(config["neo4j_user"], password),
            )
            try:
                driver.verify_connectivity()
            finally:
                driver.close()
        return {
            "backend": config["backend"],
            "status": "passed",
            "latency_ms": round((time.monotonic() - started_at) * 1000),
        }
