"""Linux platform adapter."""

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


class LinuxPlatformAdapter(PlatformAdapter):
    """Adapter for Linux systems."""

    def get_platform_name(self) -> str:
        return "linux"

    def get_app_dir(self) -> Path:
        base = os.environ.get("XDG_DATA_HOME", "~/.local/share/localpilot")
        p = Path(os.path.expanduser(base)).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def get_log_dir(self) -> Path:
        base = os.environ.get("XDG_STATE_HOME", "~/.local/state/localpilot/logs")
        p = Path(os.path.expanduser(base)).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _get_cpu_features(self) -> List[str]:
        features = []
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.exists():
            try:
                content = cpuinfo.read_text(encoding="utf-8", errors="ignore")
                for line in content.splitlines():
                    if line.startswith("flags") or line.startswith("Features"):
                        parts = line.split(":", 1)
                        if len(parts) > 1:
                            features = parts[1].strip().split()
                            break
            except Exception:
                pass
        return features

    def _probe_nvidia_gpus(self) -> List[GpuDevice]:
        devices: List[GpuDevice] = []
        smi = shutil.which("nvidia-smi")
        if not smi:
            return devices

        try:
            # Query GPU details via CSV
            cmd = [
                smi,
                "--query-gpu=name,memory.total,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                for line in res.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 4:
                        name, total_mb, free_mb, driver = parts[0], parts[1], parts[2], parts[3]
                        try:
                            tot_bytes = int(total_mb) * 1024 * 1024
                            free_bytes = int(free_mb) * 1024 * 1024
                        except ValueError:
                            tot_bytes = None
                            free_bytes = None
                        devices.append(
                            GpuDevice(
                                name=name,
                                vram_total_bytes=tot_bytes,
                                vram_available_bytes=free_bytes,
                                driver_version=driver,
                                acceleration_type="cuda",
                            )
                        )
        except Exception:
            pass

        return devices

    def inspect_hardware(self) -> HardwareSnapshot:
        mem = psutil.virtual_memory()
        cpu_features = self._get_cpu_features()
        gpu_devices = self._probe_nvidia_gpus()

        # Disk usage for application directory
        app_dir = self.get_app_dir()
        disk_usage = shutil.disk_usage(app_dir)

        # Planning budget: reserve 15% of available RAM (min 768MB, max 1.5GB) for OS/Desktop headroom
        os_reserve = min(max(int(mem.available * 0.15), 768 * 1024 * 1024), 1536 * 1024 * 1024)
        safe_ram_budget = max(0, mem.available - os_reserve)

        # For VRAM on Linux: sum available VRAM
        safe_vram_budget = sum(
            d.vram_available_bytes or 0 for d in gpu_devices if d.vram_available_bytes is not None
        )

        return HardwareSnapshot(
            os_name="Linux",
            os_version=platform.release(),
            cpu_arch=platform.machine(),
            cpu_cores_physical=psutil.cpu_count(logical=False),
            cpu_cores_logical=psutil.cpu_count(logical=True),
            cpu_features=cpu_features,
            ram_total_bytes=mem.total,
            ram_available_bytes=mem.available,
            memory_model=MemoryAccountingModel.SEPARATE,
            gpu_devices=gpu_devices,
            disk_total_bytes=disk_usage.total,
            disk_available_bytes=disk_usage.free,
            safe_ram_budget_bytes=safe_ram_budget,
            safe_vram_budget_bytes=safe_vram_budget,
            measured_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
