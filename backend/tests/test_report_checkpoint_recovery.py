from app.services.report_agent import ReportManager, ReportOutline, ReportSection


def test_report_checkpoint_loads_outline_and_only_valid_complete_sections(tmp_path, monkeypatch):
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path))
    outline = ReportOutline("Title", "Summary", [ReportSection("One"), ReportSection("Two")])
    ReportManager.save_outline("report-1", outline)
    ReportManager.save_section("report-1", 1, ReportSection("One", "completed content"))
    invalid = tmp_path / "report-1" / "section_02.md"
    invalid.write_text("## Wrong title\n\npartial", encoding="utf-8")

    restored = ReportManager.load_outline("report-1")

    assert [section.title for section in restored.sections] == ["One", "Two"]
    assert ReportManager.load_valid_section("report-1", 1, "One") == "completed content"
    assert ReportManager.load_valid_section("report-1", 2, "Two") is None
    assert not (tmp_path / "report-1" / "section_01.md.tmp").exists()
