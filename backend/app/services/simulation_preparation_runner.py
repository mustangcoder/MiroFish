"""可跨进程重启恢复的模拟环境准备运行器。"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Callable

from ..models.task import TaskManager, TaskStatus
from ..utils.locale import get_locale, set_locale, t
from ..utils.logger import get_logger
from .simulation_manager import SimulationManager, SimulationStatus
from .simulation_prepare_store import SimulationPrepareStore


logger = get_logger("mirofish.simulation_preparation_runner")


class SimulationPreparationRunner:
    """确保每个模拟只有一个准备线程，并复用持久化任务。"""

    def __init__(
        self,
        *,
        store: SimulationPrepareStore | None = None,
        task_manager: TaskManager | None = None,
        manager_factory: Callable[[], SimulationManager] = SimulationManager,
        project_manager=None,
    ):
        if project_manager is None:
            from ..models.project import ProjectManager

            project_manager = ProjectManager
        self.store = store or SimulationPrepareStore()
        self.task_manager = task_manager or TaskManager()
        self.manager_factory = manager_factory
        self.project_manager = project_manager
        self._lock = threading.Lock()
        self._workers: dict[str, threading.Thread] = {}

    @staticmethod
    def _fingerprint(graph_id: str, params: dict[str, Any]) -> str:
        payload = json.dumps(
            {"graph_id": graph_id, "params": params},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def start(
        self,
        simulation_id: str,
        *,
        entity_types=None,
        use_llm_for_profiles: bool = True,
        parallel_profile_count: int = 5,
        force_regenerate: bool = False,
        locale: str | None = None,
    ) -> dict[str, Any]:
        manager = self.manager_factory()
        state = manager.get_simulation(simulation_id)
        if state is None:
            raise ValueError(f"模拟不存在: {simulation_id}")
        project = self.project_manager.get_project(state.project_id)
        if project is None:
            raise ValueError(f"项目不存在: {state.project_id}")
        if not project.simulation_requirement:
            raise ValueError("项目缺少模拟需求")

        with self._lock:
            worker = self._workers.get(simulation_id)
            active = self.store.get_active_run(simulation_id)
            if force_regenerate and not (worker and worker.is_alive()):
                self.store.supersede_active(simulation_id)
                active = None

            reused = active is not None
            if active is None:
                params = {
                    "entity_types": entity_types,
                    "use_llm_for_profiles": bool(use_llm_for_profiles),
                    "parallel_profile_count": int(parallel_profile_count),
                    "locale": locale or get_locale(),
                }
                task_id = self.task_manager.create_task(
                    "simulation_prepare",
                    {"simulation_id": simulation_id, "project_id": state.project_id},
                )
                active = self.store.create_or_get_run(
                    simulation_id=simulation_id,
                    task_id=task_id,
                    graph_id=state.graph_id,
                    input_fingerprint=self._fingerprint(state.graph_id, params),
                    total_profiles=int(getattr(state, "entities_count", 0) or 0),
                    params=params,
                )

            task_id = active["task_id"]
            if self.task_manager.get_task(task_id) is None:
                task_id = self.task_manager.create_task(
                    "simulation_prepare",
                    {"simulation_id": simulation_id, "project_id": state.project_id},
                )
                self.store.update_run(active["run_id"], task_id=task_id)
                active = self.store.get_run(active["run_id"])

            if not (worker and worker.is_alive()):
                worker = threading.Thread(
                    target=self._execute,
                    args=(active,),
                    daemon=True,
                    name=f"prepare-{simulation_id}",
                )
                self._workers[simulation_id] = worker
                worker.start()

        return {
            "simulation_id": simulation_id,
            "task_id": task_id,
            "run_id": active["run_id"],
            "status": "preparing",
            "reused": reused,
            "recovered_profiles": active["completed_profiles"],
            "expected_entities_count": active["total_profiles"],
        }

    def _execute(self, run: dict[str, Any]) -> None:
        simulation_id = run["simulation_id"]
        task_id = run["task_id"]
        params = run["params"]
        set_locale(params.get("locale") or get_locale())
        manager = self.manager_factory()
        try:
            state = manager.get_simulation(simulation_id)
            if state is None:
                raise ValueError(f"模拟不存在: {simulation_id}")
            project = self.project_manager.get_project(state.project_id)
            if project is None:
                raise ValueError(f"项目不存在: {state.project_id}")
            document_text = self.project_manager.get_extracted_text(state.project_id) or ""
            self.task_manager.update_task(
                task_id,
                status=TaskStatus.PROCESSING,
                progress=max(
                    0,
                    int(run["completed_profiles"] / max(run["total_profiles"], 1) * 70),
                ),
                message=t("progress.startPreparingEnv"),
                error="",
            )
            stage_details = {}

            def progress_callback(stage, progress, message, **kwargs):
                stage_weights = {
                    "reading": (0, 20),
                    "generating_profiles": (20, 70),
                    "generating_config": (70, 90),
                    "copying_scripts": (90, 100),
                }
                start, end = stage_weights.get(stage, (0, 100))
                current_progress = int(start + (end - start) * progress / 100)
                stage_names = {
                    "reading": t("progress.readingGraphEntities"),
                    "generating_profiles": t("progress.generatingProfiles"),
                    "generating_config": t("progress.generatingSimConfig"),
                    "copying_scripts": t("progress.preparingScripts"),
                }
                stage_index = list(stage_weights).index(stage) + 1 if stage in stage_weights else 1
                stage_details[stage] = {
                    "current": kwargs.get("current", 0),
                    "total": kwargs.get("total", 0),
                }
                detail = stage_details[stage]
                progress_detail = {
                    "current_stage": stage,
                    "current_stage_name": stage_names.get(stage, stage),
                    "stage_index": stage_index,
                    "total_stages": len(stage_weights),
                    "stage_progress": progress,
                    "current_item": detail["current"],
                    "total_items": detail["total"],
                    "item_description": message,
                    "recovered_profiles": run["completed_profiles"],
                }
                prefix = f"[{stage_index}/{len(stage_weights)}] {stage_names.get(stage, stage)}"
                detailed_message = (
                    f"{prefix}: {detail['current']}/{detail['total']} - {message}"
                    if detail["total"] > 0
                    else f"{prefix}: {message}"
                )
                self.task_manager.update_task(
                    task_id,
                    progress=current_progress,
                    message=detailed_message,
                    progress_detail=progress_detail,
                )

            result = manager.prepare_simulation(
                simulation_id=simulation_id,
                simulation_requirement=project.simulation_requirement,
                document_text=document_text,
                defined_entity_types=params.get("entity_types"),
                use_llm_for_profiles=params.get("use_llm_for_profiles", True),
                progress_callback=progress_callback,
                parallel_profile_count=params.get("parallel_profile_count", 5),
                prepare_run_id=run["run_id"],
                prepare_store=self.store,
            )
            if result.status == SimulationStatus.FAILED:
                raise RuntimeError(result.error or "模拟准备失败")
            self.store.update_run(run["run_id"], status="completed", stage="completed")
            self.task_manager.complete_task(task_id, result=result.to_simple_dict())
        except Exception as error:
            logger.error("准备模拟失败: simulation_id=%s error=%s", simulation_id, error)
            self.store.update_run(run["run_id"], status="failed", error=str(error))
            self.task_manager.fail_task(task_id, str(error))
        finally:
            with self._lock:
                current = self._workers.get(simulation_id)
                if current is threading.current_thread():
                    self._workers.pop(simulation_id, None)

    def recover_pending(self) -> int:
        recovered = 0
        for run in self.store.list_recoverable_runs():
            try:
                self.start(run["simulation_id"])
                recovered += 1
            except Exception as error:
                logger.error(
                    "恢复准备任务失败: simulation_id=%s error=%s",
                    run["simulation_id"],
                    error,
                )
                self.store.update_run(run["run_id"], status="failed", error=str(error))
                if self.task_manager.get_task(run["task_id"]):
                    self.task_manager.fail_task(run["task_id"], str(error))
        return recovered

    def wait(self, simulation_id: str, timeout: float | None = None) -> bool:
        with self._lock:
            worker = self._workers.get(simulation_id)
        if worker is None:
            return True
        worker.join(timeout)
        return not worker.is_alive()


_runner: SimulationPreparationRunner | None = None
_runner_lock = threading.Lock()


def get_simulation_preparation_runner() -> SimulationPreparationRunner:
    global _runner
    if _runner is None:
        with _runner_lock:
            if _runner is None:
                _runner = SimulationPreparationRunner()
    return _runner
