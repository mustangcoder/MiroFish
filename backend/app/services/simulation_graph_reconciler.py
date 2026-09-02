"""从持久化动作日志重建并核对模拟图谱活动批次。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .zep_graph_memory_updater import AgentActivity, ZepGraphMemoryUpdater


@dataclass(frozen=True)
class ActivityBatch:
    platform: str
    activities: list[AgentActivity]
    text: str


def load_expected_activity_batches(simulation_dir: str | Path) -> list[ActivityBatch]:
    simulation_dir = Path(simulation_dir)
    builder = ZepGraphMemoryUpdater.__new__(ZepGraphMemoryUpdater)
    batches = []
    for platform in ("twitter", "reddit"):
        action_path = simulation_dir / platform / "actions.jsonl"
        if not action_path.exists():
            continue
        activities = []
        with action_path.open(encoding="utf-8") as handle:
            for line in handle:
                data = json.loads(line)
                if (
                    "event_type" in data
                    or data.get("success") is False
                    or data.get("action_type") == "DO_NOTHING"
                ):
                    continue
                activities.append(AgentActivity(
                    platform=platform,
                    agent_id=data.get("agent_id", 0),
                    agent_name=data.get("agent_name", ""),
                    action_type=data.get("action_type", ""),
                    action_args=data.get("action_args", {}),
                    round_num=data.get("round", 0),
                    timestamp=data.get("timestamp", ""),
                ))
        for start in range(0, len(activities), builder.BATCH_SIZE):
            chunk = activities[start:start + builder.BATCH_SIZE]
            batches.extend(
                ActivityBatch(platform, payload_activities, text)
                for payload_activities, text in builder._build_episode_payloads(chunk)
            )
    return batches
