"""核对并补写模拟动作日志中缺失的本地 Graphiti Episode。"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from neo4j import GraphDatabase

from app.config import Config
from app.services.memory_backend_config_service import MemoryBackendConfigService
from app.services.simulation_graph_reconciler import load_expected_activity_batches
from app.services.zep_graph_memory_updater import ZepGraphMemoryUpdater


def _write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("simulation_id")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    MemoryBackendConfigService().apply_runtime_config()

    simulation_dir = Path(Config.UPLOAD_FOLDER) / "simulations" / args.simulation_id
    state_path = simulation_dir / "state.json"
    run_state_path = simulation_dir / "run_state.json"
    if not state_path.exists() or not run_state_path.exists():
        raise SystemExit(f"simulation state not found: {args.simulation_id}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    graph_id = state["graph_id"]
    batches = load_expected_activity_batches(simulation_dir)

    driver = GraphDatabase.driver(
        Config.NEO4J_URI,
        auth=(Config.NEO4J_USER, Config.NEO4J_PASSWORD),
    )
    try:
        with driver.session() as session:
            existing = {
                record["content"]
                for record in session.run(
                    "MATCH (e:Episodic {group_id: $graph_id}) RETURN e.content AS content",
                    graph_id=graph_id,
                )
            }
    finally:
        driver.close()

    missing = [batch for batch in batches if batch.text not in existing]
    print(json.dumps({
        "simulation_id": args.simulation_id,
        "graph_id": graph_id,
        "expected_batches": len(batches),
        "matched_batches": len(batches) - len(missing),
        "missing_batches": len(missing),
        "apply": args.apply,
    }, ensure_ascii=False))
    if not args.apply or not missing:
        return 0

    updater = ZepGraphMemoryUpdater(graph_id, simulation_id=args.simulation_id)
    for index, batch in enumerate(missing, start=1):
        before = updater.get_stats()["failed_count"]
        updater._send_batch_activities(batch.activities, batch.platform)
        if updater.get_stats()["failed_count"] > before:
            raise RuntimeError(f"batch {index}/{len(missing)} failed")
        print(f"reconciled {index}/{len(missing)}")

    run_state = json.loads(run_state_path.read_text(encoding="utf-8"))
    run_state.update({
        "runner_status": "completed",
        "current_round": run_state.get("total_rounds", run_state.get("current_round", 0)),
        "progress_percent": 100.0,
        "completed_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "error": None,
    })
    state.update({
        "status": "completed",
        "current_round": run_state["current_round"],
        "updated_at": datetime.now().isoformat(),
        "error": None,
    })
    _write_json(run_state_path, run_state)
    _write_json(state_path, state)
    print(json.dumps({"reconciled": len(missing), "status": "completed"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
