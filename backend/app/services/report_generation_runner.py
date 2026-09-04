"""支持章节检查点恢复的报告后台运行器。"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid

from ..models.task import TaskManager, TaskStatus
from ..utils.locale import get_locale, set_locale
from ..utils.logger import get_logger
from ..utils.zep_lifecycle import register_graph_reader, unregister_graph_reader
from .report_agent import ReportAgent, ReportManager
from .workflow_run_store import WorkflowRunStore


logger = get_logger("mirofish.report_generation_runner")


class ReportGenerationRunner:
    def __init__(self, *, store=None, task_manager=None, agent_factory=ReportAgent,
                 save_report=ReportManager.save_report,
                 register_reader=register_graph_reader, unregister_reader=unregister_graph_reader):
        self.store = store or WorkflowRunStore()
        self.task_manager = task_manager or TaskManager()
        self.agent_factory = agent_factory
        self.save_report = save_report
        self.register_reader = register_reader
        self.unregister_reader = unregister_reader
        self.owner = f"report-{uuid.uuid4().hex}"
        self._lock = threading.Lock()
        self._workers: dict[str, threading.Thread] = {}
        self._retry_timer = None

    @staticmethod
    def _fingerprint(checkpoint):
        payload = json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def start(self, *, graph_id: str, simulation_id: str, project_id: str,
              simulation_requirement: str, report_id: str | None = None,
              task_id: str | None = None, locale: str | None = None):
        report_id = report_id or f"report_{uuid.uuid4().hex[:12]}"
        task_id = task_id or self.task_manager.create_task("report_generate", {
            "project_id": project_id, "simulation_id": simulation_id,
            "graph_id": graph_id, "report_id": report_id,
        })
        checkpoint = {
            "graph_id": graph_id, "simulation_id": simulation_id, "project_id": project_id,
            "simulation_requirement": simulation_requirement, "report_id": report_id,
            "locale": locale or get_locale(), "checkpoint_current": 0,
        }
        run = self.store.create_or_get_run(
            resource_type="report", resource_id=report_id, task_id=task_id, stage="report",
            input_fingerprint=self._fingerprint(checkpoint), checkpoint=checkpoint,
        )
        spawned = self._spawn(run)
        return {"report_id": report_id, "task_id": run["task_id"], "run_id": run["run_id"],
                "status": "generating", "reused": not spawned}

    def _spawn(self, run):
        report_id = run["resource_id"]
        with self._lock:
            worker = self._workers.get(report_id)
            if worker and worker.is_alive():
                return False
            if not self.store.acquire_lease(run["run_id"], self.owner, 60):
                return False
            self.register_reader(run["checkpoint"]["graph_id"], report_id)
            try:
                worker = threading.Thread(target=self._execute, args=(run,), daemon=True,
                                          name=f"report-{report_id}")
                self._workers[report_id] = worker
                worker.start()
            except Exception:
                self.unregister_reader(run["checkpoint"]["graph_id"], report_id)
                self.store.release_lease(run["run_id"], self.owner)
                raise
            return True

    def _execute(self, run):
        checkpoint = run["checkpoint"]
        report_id = checkpoint["report_id"]
        graph_id = checkpoint["graph_id"]
        set_locale(checkpoint.get("locale") or "zh")
        try:
            self.task_manager.revive_task(run["task_id"], "正在从报告检查点继续", {
                "recovering": True, "checkpoint_stage": "report",
                "checkpoint_current": checkpoint.get("checkpoint_current", 0),
            })
            agent = self.agent_factory(
                graph_id=graph_id, simulation_id=checkpoint["simulation_id"],
                simulation_requirement=checkpoint["simulation_requirement"],
            )

            def progress(stage, value, message):
                current = {**checkpoint, "checkpoint_stage": stage, "checkpoint_current": value,
                           "checkpoint_message": message}
                self.store.heartbeat(run["run_id"], self.owner, current, ttl_seconds=60)
                self.task_manager.update_task(
                    run["task_id"], status=TaskStatus.PROCESSING, progress=value,
                    message=f"[{stage}] {message}", progress_detail=current,
                )

            report = agent.generate_report(
                progress_callback=progress, report_id=report_id, resume=True,
            )
            self.save_report(report)
            if getattr(getattr(report, "status", None), "value", None) != "completed":
                raise RuntimeError(report.error or "报告生成")
            self.store.complete(run["run_id"])
            self.task_manager.complete_task(run["task_id"], {
                "report_id": report_id, "simulation_id": checkpoint["simulation_id"],
                "status": "completed", "recovered": True,
            })
        except Exception as error:
            logger.error("报告生成或恢复失败: report_id=%s error=%s", report_id, error)
            self.store.fail(run["run_id"], str(error))
            self.task_manager.fail_task(run["task_id"], str(error))
        finally:
            self.unregister_reader(graph_id, report_id)
            with self._lock:
                self._workers.pop(report_id, None)

    def recover_pending(self):
        count = 0
        deferred = False
        for run in self.store.list_recoverable():
            if run["stage"] != "report":
                continue
            if self._spawn(run):
                count += 1
            else:
                deferred = True
        if deferred:
            with self._lock:
                if self._retry_timer is None or not self._retry_timer.is_alive():
                    self._retry_timer = threading.Timer(65, self.recover_pending)
                    self._retry_timer.daemon = True
                    self._retry_timer.start()
        return count

    def wait(self, report_id: str, timeout=None):
        with self._lock:
            worker = self._workers.get(report_id)
        if worker:
            worker.join(timeout)
            return not worker.is_alive()
        return True


_runner = None
_runner_lock = threading.Lock()


def get_report_generation_runner():
    global _runner
    if _runner is None:
        with _runner_lock:
            if _runner is None:
                _runner = ReportGenerationRunner()
    return _runner
