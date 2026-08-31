from pathlib import Path


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
