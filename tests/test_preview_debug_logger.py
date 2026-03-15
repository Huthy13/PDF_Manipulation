from __future__ import annotations

from pathlib import Path

from pdf_merge_gui.ui.preview_debug_logger import PreviewDebugLogger


def test_logging_enabled_writes_file(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "preview_debug.log"
    logger = PreviewDebugLogger(enabled=True, log_path=log_path, max_bytes=10_000)

    logger.log("hello diagnostics")

    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "hello diagnostics" in content


def test_logging_disabled_produces_no_file_writes(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "preview_debug.log"
    logger = PreviewDebugLogger(enabled=False, log_path=log_path)

    logger.log("should not be written")

    assert not log_path.exists()
