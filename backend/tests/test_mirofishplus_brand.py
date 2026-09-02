from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_public_brand_surfaces_use_mirofishplus():
    files = [
        ROOT / "README.md",
        ROOT / "README-EN.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "demo.py",
        ROOT / "frontend/index.html",
        ROOT / "frontend/src/views/Home.vue",
        ROOT / "frontend/src/views/HistoryView.vue",
        ROOT / "frontend/src/views/InteractionView.vue",
        ROOT / "frontend/src/views/MainView.vue",
        ROOT / "frontend/src/views/ModelSettingsView.vue",
        ROOT / "frontend/src/views/Process.vue",
        ROOT / "frontend/src/views/ReportView.vue",
        ROOT / "frontend/src/views/SimulationRunView.vue",
        ROOT / "frontend/src/views/SimulationView.vue",
        ROOT / "locales/zh.json",
        ROOT / "locales/en.json",
        ROOT / "backend/app/__init__.py",
        ROOT / "backend/run.py",
        ROOT / "backend/scripts/bootstrap_local.py",
        ROOT / "scripts/start-local.sh",
        ROOT / "package.json",
    ]

    for path in files:
        content = path.read_text(encoding="utf-8")
        assert "MiroFish-Local" not in content, path
        assert ">MIROFISH<" not in content, path
        assert "MiroFishPlus" in content or "MIROFISHPLUS" in content, path


def test_oasis_database_names_remain_compatible():
    runner = (ROOT / "backend/scripts/run_parallel_simulation.py").read_text(encoding="utf-8")

    assert "twitter_simulation.db" in runner
    assert "reddit_simulation.db" in runner
