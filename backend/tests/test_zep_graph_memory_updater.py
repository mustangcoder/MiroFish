from types import SimpleNamespace
import threading
from queue import Queue

import pytest

from app.services import zep_graph_memory_updater as updater_module
from app.services.zep_graph_memory_updater import (
    AgentActivity,
    ZepGraphMemoryManager,
    ZepGraphMemoryUpdater,
)


def _activity(index=1, content="hello", *, platform="twitter", round_num=None):
    return AgentActivity(
        platform=platform,
        agent_id=index,
        agent_name=f"Agent {index}",
        action_type="CREATE_POST",
        action_args={"content": content},
        round_num=index if round_num is None else round_num,
        timestamp="2026-07-22T12:00:00+08:00",
    )


def _client(add):
    return SimpleNamespace(
        graph=SimpleNamespace(
            add=add,
            episode=SimpleNamespace(
                get=lambda **_kwargs: SimpleNamespace(processed=True)
            ),
        )
    )


def _updater(monkeypatch, add, simulation_id="sim-1"):
    client = _client(add)
    monkeypatch.setattr(updater_module, "get_zep_client", lambda _key: client)
    updater = ZepGraphMemoryUpdater(
        "graph-1",
        api_key="test-key",
        simulation_id=simulation_id,
    )
    updater.SEND_INTERVAL = 0
    return updater


def test_stop_drains_an_immediately_queued_tail_activity(monkeypatch):
    writes = []
    updater = _updater(
        monkeypatch,
        lambda **kwargs: writes.append(kwargs) or SimpleNamespace(uuid_="episode-1"),
    )

    updater.start()
    updater.add_activity(_activity())
    updater.stop()

    assert len(writes) == 1
    assert updater.get_stats()["items_sent"] == 1
    assert updater.get_stats()["queue_size"] == 0


def test_network_write_happens_outside_the_buffer_lock(monkeypatch):
    lock_was_available = []
    updater = None

    def add(**_kwargs):
        acquired = updater._buffer_lock.acquire(blocking=False)
        lock_was_available.append(acquired)
        if acquired:
            updater._buffer_lock.release()
        return SimpleNamespace(uuid_="episode-1")

    updater = _updater(monkeypatch, add)
    updater.start()
    for index in range(updater.BATCH_SIZE):
        updater.add_activity(_activity(index, round_num=1))
    updater.stop()

    assert lock_was_available == [True]


def test_activity_episode_has_provenance_time_and_a_safe_size(monkeypatch):
    writes = []
    updater = _updater(
        monkeypatch,
        lambda **kwargs: writes.append(kwargs) or SimpleNamespace(uuid_="episode-1"),
        simulation_id="sim-provenance",
    )

    updater._send_batch_activities(
        [_activity(content="x" * 20_000)],
        "twitter",
    )

    assert len(writes) == 1
    write = writes[0]
    assert len(write["data"]) <= updater.MAX_EPISODE_CHARS
    assert write["created_at"] == "2026-07-22T12:00:00+08:00"
    assert write["source_description"] == "MiroFish simulation activity batch"
    assert write["metadata"]["simulation_id"] == "sim-provenance"
    assert write["metadata"]["platform"] == "twitter"
    assert write["metadata"]["activity_count"] == 1


def test_graphiti_batches_use_twenty_items_and_keep_hard_character_limit():
    assert ZepGraphMemoryUpdater.BATCH_SIZE == 20
    assert ZepGraphMemoryUpdater.MAX_EPISODE_CHARS == 9_500


def test_episode_payloads_do_not_mix_platforms_or_rounds(monkeypatch):
    updater = _updater(monkeypatch, lambda **_kwargs: SimpleNamespace(uuid_="episode"))
    payloads = updater._build_episode_payloads([
        _activity(1, platform="twitter", round_num=4),
        _activity(2, platform="twitter", round_num=4),
        _activity(3, platform="twitter", round_num=5),
        _activity(4, platform="reddit", round_num=5),
    ])

    assert [len(items) for items, _ in payloads] == [2, 1, 1]
    assert [{(item.platform, item.round_num) for item in items} for items, _ in payloads] == [
        {("twitter", 4)}, {("twitter", 5)}, {("reddit", 5)},
    ]


def test_episode_payloads_split_before_the_hard_character_limit(monkeypatch):
    updater = _updater(monkeypatch, lambda **_kwargs: SimpleNamespace(uuid_="episode"))
    payloads = updater._build_episode_payloads([
        _activity(1, content="x" * 4_500, round_num=8),
        _activity(2, content="y" * 4_500, round_num=8),
    ])

    assert len(payloads) == 2
    assert all(len(text) <= updater.MAX_EPISODE_CHARS for _, text in payloads)


def test_write_logs_and_persists_payload_size_estimate(monkeypatch):
    writes = []
    log_messages = []
    updater = _updater(monkeypatch, lambda **kwargs: writes.append(kwargs) or SimpleNamespace(uuid_="episode"))
    monkeypatch.setattr(updater_module.logger, "info", lambda message, *args: log_messages.append(message % args if args else message))

    updater._send_batch_activities([_activity(1, content="你好 world", round_num=9)], "twitter")

    metadata = writes[0]["metadata"]
    assert metadata["character_count"] > 0
    assert metadata["estimated_token_count"] > 0
    assert any("chars=" in message and "estimated_tokens=" in message for message in log_messages)


def test_failed_non_idempotent_write_is_reported_by_stop(monkeypatch):
    def add(**_kwargs):
        raise RuntimeError("write failed")

    updater = _updater(monkeypatch, add)
    updater.start()
    updater.add_activity(_activity())

    with pytest.raises(RuntimeError, match="ingestion is incomplete"):
        updater.stop()

    assert updater.get_stats()["failed_count"] == 1


def test_definitive_provider_circuit_failure_waits_and_retries_same_batch(monkeypatch):
    attempts = []
    sleeps = []

    def add(**_kwargs):
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise RuntimeError("Error code: 503 - provider circuit is open")
        return SimpleNamespace(uuid_="episode-1")

    updater = _updater(monkeypatch, add)
    monkeypatch.setattr(updater_module.time, "sleep", sleeps.append)

    updater._send_batch_activities([_activity()], "twitter")

    assert attempts == [1, 2]
    assert sleeps == [30]
    assert updater.get_stats()["failed_count"] == 0
    assert updater.get_stats()["items_sent"] == 1


def test_ambiguous_write_failure_is_not_replayed(monkeypatch):
    attempts = []

    def add(**_kwargs):
        attempts.append(len(attempts) + 1)
        raise RuntimeError("connection dropped after write")

    updater = _updater(monkeypatch, add)
    updater._send_batch_activities([_activity()], "twitter")

    assert attempts == [1]
    assert updater.get_stats()["failed_count"] == 1


def test_failed_simulation_action_is_not_ingested(monkeypatch):
    updater = _updater(
        monkeypatch,
        lambda **_kwargs: SimpleNamespace(uuid_="unused"),
    )

    updater.add_activity_from_dict(
        {
            "agent_id": 1,
            "agent_name": "Agent",
            "action_type": "CREATE_POST",
            "action_args": {"content": "not actually posted"},
            "success": False,
        },
        "twitter",
    )

    assert updater.get_stats()["queue_size"] == 0
    assert updater.get_stats()["skipped_count"] == 1


def test_stop_cannot_finish_between_acceptance_check_and_enqueue(monkeypatch):
    writes = []
    updater = _updater(
        monkeypatch,
        lambda **kwargs: writes.append(kwargs) or SimpleNamespace(uuid_="episode-1"),
    )

    put_entered = threading.Event()
    allow_put = threading.Event()

    class BlockingQueue(Queue):
        def put(self, item, block=True, timeout=None):
            put_entered.set()
            assert allow_put.wait(timeout=2)
            return super().put(item, block=block, timeout=timeout)

    updater._activity_queue = BlockingQueue()
    updater.start()
    producer = threading.Thread(target=updater.add_activity, args=(_activity(),))
    producer.start()
    assert put_entered.wait(timeout=1)

    stopper = threading.Thread(target=updater.stop)
    stopper.start()
    stopper.join(timeout=0.1)
    assert stopper.is_alive()

    allow_put.set()
    producer.join(timeout=2)
    stopper.join(timeout=2)

    assert not producer.is_alive()
    assert not stopper.is_alive()
    assert len(writes) == 1


def test_pending_episode_wait_has_a_deadline(monkeypatch):
    updater = _updater(
        monkeypatch,
        lambda **_kwargs: SimpleNamespace(uuid_="episode-1"),
    )
    updater._pending_episode_uuids = ["episode-1"]
    updater.client.graph.episode.get = lambda **_kwargs: SimpleNamespace(
        processed=False
    )
    timestamps = iter([0.0, 2.0])
    monkeypatch.setattr(updater_module, "ZEP_INGESTION_WAIT_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(updater_module.time, "time", lambda: next(timestamps))
    monkeypatch.setattr(updater_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(TimeoutError, match="pending"):
        updater._wait_for_pending_episodes()


def test_explicit_graph_destruction_can_discard_a_stopped_failed_updater():
    updater = SimpleNamespace(
        graph_id="graph-1",
        _running=False,
        _worker_thread=SimpleNamespace(is_alive=lambda: False),
    )
    ZepGraphMemoryManager._updaters["sim-failed"] = updater
    try:
        assert ZepGraphMemoryManager.discard_inactive_updater("sim-failed") is True
        assert "sim-failed" not in ZepGraphMemoryManager._updaters
    finally:
        ZepGraphMemoryManager._updaters.pop("sim-failed", None)


def test_flush_deadline_keeps_unattempted_platform_for_a_safe_retry(monkeypatch):
    now = [0.0]
    writes = []

    def add(**kwargs):
        writes.append(kwargs)
        now[0] = 2.0
        return SimpleNamespace(uuid_=f"episode-{len(writes)}")

    updater = _updater(monkeypatch, add)
    updater._platform_buffers["twitter"] = [_activity(1)]
    reddit_activity = _activity(2)
    reddit_activity.platform = "reddit"
    updater._platform_buffers["reddit"] = [reddit_activity]
    monkeypatch.setattr(updater_module.time, "time", lambda: now[0])

    with pytest.raises(TimeoutError, match="deadline"):
        updater._flush_remaining(deadline=1.0)

    assert updater._platform_buffers["twitter"] == []
    assert updater._platform_buffers["reddit"] == [reddit_activity]

    now[0] = 0.0
    updater._flush_remaining(deadline=1.0)
    assert updater._platform_buffers["reddit"] == []
    assert len(writes) == 2
