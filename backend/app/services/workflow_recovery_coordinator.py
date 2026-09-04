"""按依赖顺序接管带 lease 的可恢复工作流。"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Callable

from ..models.task import TaskManager
from ..utils.logger import get_logger
from .workflow_run_store import WorkflowRunStore


logger = get_logger("mirofish.workflow_recovery")
STAGE_ORDER = ("ontology", "graph", "prepare", "simulation", "graph_ingestion", "report")


@dataclass
class RecoverySummary:
    scheduled: int = 0
    completed: int = 0
    failed: int = 0


class WorkflowRecoveryCoordinator:
    def __init__(self, *, store=None, task_manager=None, max_concurrency: int = 2,
                 lease_ttl_seconds: int = 60):
        self.store = store or WorkflowRunStore()
        self.task_manager = task_manager or TaskManager()
        self.max_concurrency = max(1, max_concurrency)
        self.lease_ttl_seconds = lease_ttl_seconds
        self.owner = f"recovery-{uuid.uuid4().hex}"
        self.handlers: dict[str, Callable[[dict], None]] = {}
        self._threads: list[threading.Thread] = []
        self._semaphore = threading.Semaphore(self.max_concurrency)
        self._summary = RecoverySummary()
        self._summary_lock = threading.Lock()

    def register(self, stage: str, handler: Callable[[dict], None]):
        self.handlers[stage] = handler

    def _execute(self, run: dict):
        with self._semaphore:
            try:
                self.task_manager.revive_task(
                    run["task_id"], "正在从检查点恢复", {
                        "recovering": True,
                        "checkpoint_stage": run["stage"],
                        **run["checkpoint"],
                    },
                )
                self.handlers[run["stage"]](run)
                self.store.complete(run["run_id"])
                self.task_manager.complete_task(run["task_id"], {
                    "resource_id": run["resource_id"], "recovered": True,
                })
                with self._summary_lock:
                    self._summary.completed += 1
            except Exception as error:
                logger.error("恢复工作流失败: run_id=%s error=%s", run["run_id"], error)
                self.store.fail(run["run_id"], str(error))
                self.task_manager.fail_task(run["task_id"], str(error))
                with self._summary_lock:
                    self._summary.failed += 1

    def recover_pending(self, *, wait: bool = False) -> RecoverySummary:
        order = {stage: index for index, stage in enumerate(STAGE_ORDER)}
        runs = sorted(self.store.list_recoverable(), key=lambda run: order.get(run["stage"], 999))
        for run in runs:
            if run["stage"] not in self.handlers:
                continue
            if not self.store.acquire_lease(run["run_id"], self.owner, self.lease_ttl_seconds):
                continue
            self._summary.scheduled += 1
            if wait:
                self._execute(run)
            else:
                thread = threading.Thread(target=self._execute, args=(run,), daemon=True)
                self._threads.append(thread)
                thread.start()
        if wait:
            self.wait()
        return self._summary

    def wait(self):
        for thread in list(self._threads):
            thread.join()
        self._threads = []
