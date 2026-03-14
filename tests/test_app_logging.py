from __future__ import annotations

import logging

from pdf_merge_gui import app


def test_configure_logging_sets_debug_level_and_force(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_basic_config(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(app.logging, "basicConfig", fake_basic_config)

    app._configure_logging()

    assert len(calls) == 1
    assert calls[0]["level"] == logging.DEBUG
    assert calls[0]["force"] is True
    assert "%(levelname)s" in str(calls[0]["format"])


def test_main_configures_logging_before_launching_ui(monkeypatch) -> None:
    call_order: list[str] = []

    def fake_configure_logging() -> None:
        call_order.append("configure_logging")

    class FakeRoot:
        def mainloop(self) -> None:
            call_order.append("mainloop")

    def fake_tk():
        call_order.append("tk")
        return FakeRoot()

    def fake_controller(root) -> None:
        call_order.append("controller")
        assert isinstance(root, FakeRoot)

    monkeypatch.setattr(app, "_configure_logging", fake_configure_logging)
    monkeypatch.setattr(app.tk, "Tk", fake_tk)
    monkeypatch.setattr(app, "PdfMergeController", fake_controller)

    app.main()

    assert call_order == ["configure_logging", "tk", "controller", "mainloop"]
