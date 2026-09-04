from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_report_console_output_is_resizable_and_persistent():
    source = (ROOT / "frontend/src/components/Step4Report.vue").read_text(
        encoding="utf-8"
    )

    assert 'role="separator"' in source
    assert 'aria-orientation="horizontal"' in source
    assert '@pointerdown="startConsoleResize"' in source
    assert '@dblclick="resetConsoleHeight"' in source
    assert '@keydown="handleConsoleResizeKeydown"' in source
    assert ':style="{ height: `${consoleHeight}px` }"' in source
    assert "const MIN_CONSOLE_HEIGHT = 100" in source
    assert "window.innerHeight * 0.6" in source
    assert "localStorage.getItem(CONSOLE_HEIGHT_STORAGE_KEY)" in source
    assert "localStorage.setItem(CONSOLE_HEIGHT_STORAGE_KEY" in source


def test_report_console_resize_cleans_up_global_pointer_listeners():
    source = (ROOT / "frontend/src/components/Step4Report.vue").read_text(
        encoding="utf-8"
    )

    assert "window.addEventListener('pointermove', handleConsoleResize)" in source
    assert "window.removeEventListener('pointermove', handleConsoleResize)" in source
    assert "window.removeEventListener('pointerup', stopConsoleResize)" in source
