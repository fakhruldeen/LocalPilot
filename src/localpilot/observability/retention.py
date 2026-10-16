"""Log retention, file rotation, and storage bounding policies (Section 23)."""

from __future__ import annotations
import os
from pathlib import Path
from typing import Optional
from localpilot.storage.db import get_default_log_dir


class RetentionManager:
    """Enforces total storage limits and cleans older log and temporary files."""

    def __init__(
        self,
        log_dir: Optional[Path | str] = None,
        max_total_log_bytes: int = 50 * 1024 * 1024,  # 50 MB
    ) -> None:
        self.log_dir = Path(log_dir).resolve() if log_dir else get_default_log_dir()
        self.max_total_log_bytes = max_total_log_bytes

    def enforce_limits(self) -> int:
        """Removes oldest rotated log files if total log directory size exceeds limit."""
        if not self.log_dir.exists():
            return 0

        files = [p for p in self.log_dir.glob("*.log*") if p.is_file()]
        total_size = sum(p.stat().st_size for p in files)
        bytes_freed = 0

        if total_size <= self.max_total_log_bytes:
            return 0

        # Sort files by modification time, oldest first
        files.sort(key=lambda p: p.stat().st_mtime)

        for p in files:
            # Never delete active unrotated events.jsonl unless absolutely necessary
            if p.name == "events.jsonl" and len(files) > 1:
                continue
            sz = p.stat().st_size
            try:
                p.unlink()
                bytes_freed += sz
                total_size -= sz
                if total_size <= self.max_total_log_bytes:
                    break
            except Exception:
                pass

        return bytes_freed
