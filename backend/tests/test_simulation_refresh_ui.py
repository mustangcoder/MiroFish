from pathlib import Path

from app.api.simulation import _select_active_prepare_task


ROOT = Path(__file__).resolve().parents[2]


def test_step3_refresh_restores_existing_run_without_force_restart():
    view = (ROOT / "frontend/src/components/Step3Simulation.vue").read_text()

    assert "restoreExistingSimulation" in view
    assert "await getRunStatus(props.simulationId)" in view
    assert "checkPlatformsCompleted(data)" in view
    assert "doStartSimulation(false)" in view
    assert "force: shouldForce" in view
    assert "force: true" not in view
    assert "restoreExistingSimulation()" in view


def test_report_generation_keeps_waiting_command_environment_alive():
    view = (ROOT / "frontend/src/components/Step3Simulation.vue").read_text()

    assert "closeSimulationEnv" not in view
    assert "finalizeSimulationForReport" not in view
    assert "await generateReport({" in view


def test_step2_checks_persisted_prepare_status_before_starting_prepare():
    view = (ROOT / "frontend/src/components/Step2EnvSetup.vue").read_text()

    assert "restorePreparedSimulation" in view
    assert "await getPrepareStatus({ simulation_id: props.simulationId })" in view
    assert "await restorePreparedSimulation()" in view
    assert view.index("await getPrepareStatus({ simulation_id: props.simulationId })") < view.index("await prepareSimulation({")


def test_step2_restores_checkpoint_progress_and_reuses_the_persisted_task():
    view = (ROOT / "frontend/src/components/Step2EnvSetup.vue").read_text()

    assert "recoveredProfiles" in view
    assert "data.recovered_profiles" in view
    assert "data.total_profiles" in view
    assert "log.prepareRecovered" in view
    assert "taskId.value = res.data.task_id" in view


def test_history_continue_routes_to_latest_persisted_workflow_node():
    view = (ROOT / "frontend/src/components/HistoryDatabase.vue").read_text()

    assert "latestSimulationDestination" in view
    assert "simulation.report_id" in view
    assert "simulation.runner_status" in view
    assert "name: 'SimulationRun'" in view
    assert "name: 'Report'" in view


def test_active_prepare_task_is_reused_after_page_navigation():
    tasks = [
        {"task_id": "old", "task_type": "simulation_prepare", "status": "completed", "metadata": {"simulation_id": "sim-1"}},
        {"task_id": "other", "task_type": "simulation_prepare", "status": "processing", "metadata": {"simulation_id": "sim-2"}},
        {"task_id": "active", "task_type": "simulation_prepare", "status": "processing", "metadata": {"simulation_id": "sim-1"}, "progress": 42},
    ]

    assert _select_active_prepare_task(tasks, "sim-1")["task_id"] == "active"
    assert _select_active_prepare_task(tasks, "missing") is None


def test_dedicated_history_page_uses_simulation_history_for_latest_destination():
    view = (ROOT / "frontend/src/views/HistoryView.vue").read_text()
    project_list = (ROOT / "frontend/src/components/HistoryProjectList.vue").read_text()

    assert "getSimulationHistory" in view
    assert "workflowRank" in view
    assert "projectSimulations" in view
    assert "latestProjectDestination" in view
    assert "function openProject(project)" in view
    assert "$emit('open', project)" in project_list
