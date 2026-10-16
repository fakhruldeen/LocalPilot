"""Experimental Unix fallback platform adapter."""

from __future__ import annotations
import os
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path
import psutil

from localpilot.platforms.base import (
    HardwareSnapshot,
    MemoryAccountingModel,
    PlatformAdapter,
)


class UnixPlatformAdapter(PlatformAdapter):
    """Adapter for other Unix/POSIX environments (experimental)."""

    def get_platform_name(self) -> str:
        return "unix-experimental"

    def get_app_dir(self) -> Path:
        base = os.environ.get("LOCALPILOT_DATA_DIR", "~/.localpilot")
        p = Path(os.path.expanduser(base)).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def get_log_dir(self) -> Path:
        base = os.environ.get("LOCALPILOT_LOG_DIR", "~/.localpilot/logs")
        p = Path(os.path.expanduser(base)).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def inspect_hardware(self) -> HardwareSnapshot:
        mem = psutil.virtual_memory()
        app_dir = self.get_app_dir()
        disk_usage = shutil.disk_usage(app_dir)
        os_reserve = max(int(mem.total * 0.25), 1536 * 1024 * 1024)

        return HardwareSnapshot(
            os_name=platform.system(),
            os_version=platform.release(),
            cpu_arch=platform.machine(),
            cpu_cores_physical=psutil.cpu_count(logical=False),
            cpu_cores_logical=psutil.cpu_count(logical=True),
            cpu_features=[],
            ram_total_bytes=mem.total,
            ram_available_bytes=mem.available,
            memory_model=MemoryAccountingModel.SEPARATE,
            gpu_devices=[],
            disk_total_bytes=disk_usage.total,
            disk_available_bytes=disk_usage.free,
            safe_ram_budget_bytes=max(0, mem.available - os_reserve),
            safe_vram_budget_bytes=0,
            measured_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
