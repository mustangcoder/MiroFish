from app.services.graph_ingestion_store import GraphIngestionStore


def test_ingestion_batch_is_claimed_once_and_written_is_not_replayed(tmp_path):
    store = GraphIngestionStore(tmp_path / "mirofishplus.db")
    assert store.claim("batch-1", "sim-1", "graph-1", "twitter", 3, 4, 120) is True
    store.mark_written("batch-1", "episode-1")

    assert store.claim("batch-1", "sim-1", "graph-1", "twitter", 3, 4, 120) is False
    assert store.get("batch-1")["status"] == "written"


def test_retryable_batch_can_be_reclaimed_but_ambiguous_batch_cannot(tmp_path):
    store = GraphIngestionStore(tmp_path / "mirofishplus.db")
    store.claim("retry", "sim-1", "graph-1", "reddit", 2, 3, 90)
    store.mark_failed("retry", "rate limited", retryable=True)
    assert store.claim("retry", "sim-1", "graph-1", "reddit", 2, 3, 90) is True

    store.claim("ambiguous", "sim-1", "graph-1", "reddit", 2, 3, 90)
    store.mark_failed("ambiguous", "connection reset", retryable=False)
    assert store.claim("ambiguous", "sim-1", "graph-1", "reddit", 2, 3, 90) is False


def test_pending_batches_survive_reopening_database(tmp_path):
    database = tmp_path / "mirofishplus.db"
    GraphIngestionStore(database).claim(
        "batch-1", "sim-1", "graph-1", "twitter", 8, 20, 500
    )

    rows = GraphIngestionStore(database).list_incomplete("sim-1")
    assert [(row["batch_key"], row["status"]) for row in rows] == [("batch-1", "writing")]
