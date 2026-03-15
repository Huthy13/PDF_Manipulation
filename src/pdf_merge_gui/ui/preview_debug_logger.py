from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path


class PreviewDebugLogger:
    DEFAULT_MAX_BYTES = 1_000_000

    def __init__(self, *, enabled: bool = False, log_path: Path | None = None, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.enabled = enabled
        self.max_bytes = max(max_bytes, 1_024)
        self.log_path = log_path or self.default_log_path()

    @classmethod
    def default_log_path(cls) -> Path:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            base = Path(local_app_data)
        else:
            base = Path.home()
        return base / "PDF_Merge_GUI" / "logs" / "preview_debug.log"

    @classmethod
    def env_override_enabled(cls, default: bool = False) -> bool:
        raw = os.environ.get("PDF_MERGE_GUI_PREVIEW_DEBUG")
        if raw is None:
            return default
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def log(self, message: str) -> None:
        if not self.enabled:
            return

        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        line = f"{timestamp} {message.rstrip()}\n"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._cap_file_size_if_needed(len(line.encode("utf-8")))
        with self.log_path.open("a", encoding="utf-8") as fp:
            fp.write(line)

    def _cap_file_size_if_needed(self, incoming_bytes: int) -> None:
        if not self.log_path.exists():
            return
        current_size = self.log_path.stat().st_size
        if current_size + incoming_bytes <= self.max_bytes:
            return

        keep_bytes = max(self.max_bytes // 2, 1_024)
        with self.log_path.open("rb") as fp:
            fp.seek(max(current_size - keep_bytes, 0))
            tail = fp.read()
        with self.log_path.open("wb") as fp:
            fp.write(tail)

