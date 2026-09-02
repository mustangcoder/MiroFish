import sqlite3

from scripts.bootstrap_local import bootstrap
from app.services.credential_cipher import CredentialCipher
from app.services.model_config_store import ModelConfigStore


def _counts(path):
    with sqlite3.connect(path) as connection:
        return {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in (
                "model_connections",
                "task_history",
                "simulation_prepare_runs",
                "simulation_prepare_profiles",
                "memory_backend_config",
            )
        }


def test_bootstrap_creates_required_tables_and_is_idempotent(tmp_path):
    database = tmp_path / "uploads" / "mirofish.db"
    environment = {
        "ZEP_BACKEND": "graphiti",
        "NEO4J_URI": "bolt://neo4j:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "password",
    }

    first = bootstrap(database, environment=environment)
    first_counts = _counts(database)
    second = bootstrap(database, environment=environment)

    required = {
        "model_connections",
        "task_history",
        "simulation_prepare_runs",
        "simulation_prepare_profiles",
        "memory_backend_config",
        "app_schema_migrations",
    }
    assert required <= set(first["required_tables"])
    assert second["required_tables"] == first["required_tables"]
    assert _counts(database) == first_counts
    assert second["memory_backend"] == "graphiti"


def test_bootstrap_does_not_overwrite_existing_memory_backend_config(tmp_path):
    database = tmp_path / "uploads" / "mirofish.db"
    key_path = database.parent / "model-config" / "master.key"
    store = ModelConfigStore(database, CredentialCipher(key_path))
    store.save_memory_backend_config({
        "backend": "graphiti",
        "neo4j_uri": "bolt://saved-neo4j:7687",
        "neo4j_user": "saved-user",
        "neo4j_password": "saved-password",
    })

    bootstrap(database, environment={"ZEP_BACKEND": "cloud", "ZEP_API_KEY": "new-key"})

    assert store.get_memory_backend_config()["neo4j_uri"] == "bolt://saved-neo4j:7687"
