import json

from app.services.simulation_graph_reconciler import load_expected_activity_batches


def test_reconciler_rebuilds_platform_batches_and_excludes_non_content_actions(tmp_path):
    for platform in ("twitter", "reddit"):
        platform_dir = tmp_path / platform
        platform_dir.mkdir()
        rows = [
            {
                "round": 1,
                "timestamp": "2026-09-01T00:00:00+08:00",
                "agent_id": 1,
                "agent_name": "Alice",
                "action_type": "CREATE_POST",
                "action_args": {"content": f"{platform} post"},
                "success": True,
            },
            {
                "round": 1,
                "timestamp": "2026-09-01T00:00:01+08:00",
                "agent_id": 2,
                "agent_name": "Bob",
                "action_type": "DO_NOTHING",
                "action_args": {},
                "success": True,
            },
        ]
        (platform_dir / "actions.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

    batches = load_expected_activity_batches(tmp_path)

    assert [batch.platform for batch in batches] == ["twitter", "reddit"]
    assert [len(batch.activities) for batch in batches] == [1, 1]
    assert "twitter post" in batches[0].text
    assert "reddit post" in batches[1].text
    assert all("DO_NOTHING" not in batch.text for batch in batches)
