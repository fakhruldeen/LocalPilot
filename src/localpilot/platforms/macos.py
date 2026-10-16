"""macOS platform adapter with Apple Silicon unified memory handling."""

from __future__ import annotations
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
import psutil

from localpilot.platforms.base import (
    GpuDevice,
    HardwareSnapshot,
    MemoryAccountingModel,
    PlatformAdapter,
)


class MacOSPlatformAdapter(PlatformAdapter):
    """Adapter for macOS systems."""

    def get_platform_name(self) -> str:
        return "macos"

    def get_app_dir(self) -> Path:
        base = "~/Library/Application Support/LocalPilot"
        p = Path(os.path.expanduser(base)).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def get_log_dir(self) -> Path:
        base = "~/Library/Logs/LocalPilot"
        p = Path(os.path.expanduser(base)).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _is_apple_silicon(self) -> bool:
        arch = platform.machine().lower()
        if arch in ("arm64", "aarch64"):
            return True
        try:
            res = subprocess.run(["sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip() == "1":
                return True
        except Exception:
            pass
        return False

    def inspect_hardware(self) -> HardwareSnapshot:
        mem = psutil.virtual_memory()
        is_arm = self._is_apple_silicon()
        memory_model = MemoryAccountingModel.UNIFIED if is_arm else MemoryAccountingModel.SEPARATE

        gpu_devices: List[GpuDevice] = []
        if is_arm:
            # Apple Silicon Unified Memory
            gpu_devices.append(
                GpuDevice(
                    name="Apple Integrated GPU (Metal)",
                    vram_total_bytes=mem.total,
                    vram_available_bytes=mem.available,
                    driver_version="Metal",
                    acceleration_type="metal",
                )
            )
            # In unified memory, VRAM shares the same pool as RAM
            os_reserve = max(int(mem.total * 0.20), 2048 * 1024 * 1024)
            safe_budget = max(0, mem.available - os_reserve)
            safe_ram_budget = safe_budget
            safe_vram_budget = safe_budget
        else:
            # Intel Mac
            gpu_devices.append(
                GpuDevice(
                    name="Intel/AMD Mac GPU",
                    vram_total_bytes=None,  # unknown
                    vram_available_bytes=None,
                    driver_version=None,
                    acceleration_type="cpu",
                )
            )
            os_reserve = max(int(mem.total * 0.20), 1536 * 1024 * 1024)
            safe_ram_budget = max(0, mem.available - os_reserve)
            safe_vram_budget = 0

        app_dir = self.get_app_dir()
        disk_usage = shutil.disk_usage(app_dir)

        return HardwareSnapshot(
            os_name="macOS",
            os_version=platform.mac_ver()[0] or platform.release(),
            cpu_arch=platform.machine(),
            cpu_cores_physical=psutil.cpu_count(logical=False),
            cpu_cores_logical=psutil.cpu_count(logical=True),
            cpu_features=["neon" if is_arm else "sse4_2"],
            ram_total_bytes=mem.total,
            ram_available_bytes=mem.available,
            memory_model=memory_model,
            gpu_devices=gpu_devices,
            disk_total_bytes=disk_usage.total,
            disk_available_bytes=disk_usage.free,
            safe_ram_budget_bytes=safe_ram_budget,
            safe_vram_budget_bytes=safe_vram_budget,
            measured_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
