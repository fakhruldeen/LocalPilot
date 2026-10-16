"""Platform abstraction base classes and hardware data models."""

from __future__ import annotations
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class MemoryAccountingModel(str, Enum):
    SEPARATE = "separate"  # Dedicated RAM and dedicated GPU VRAM
    UNIFIED = "unified"    # Shared memory pool (e.g. Apple Silicon)


class GpuDevice(BaseModel):
    name: str
    vram_total_bytes: Optional[int] = None      # None represents unknown, never 0
    vram_available_bytes: Optional[int] = None  # None represents unknown, never 0
    driver_version: Optional[str] = None
    acceleration_type: str = "cuda"            # "cuda", "rocm", "metal", "vulkan", "cpu"


class HardwareSnapshot(BaseModel):
    """Complete hardware inspection snapshot matching Sections 3.4 and 12."""
    os_name: str
    os_version: str
    cpu_arch: str
    cpu_cores_physical: Optional[int] = None
    cpu_cores_logical: Optional[int] = None
    cpu_features: List[str] = Field(default_factory=list)
    ram_total_bytes: int
    ram_available_bytes: int
    memory_model: MemoryAccountingModel
    gpu_devices: List[GpuDevice] = Field(default_factory=list)
    disk_total_bytes: int
    disk_available_bytes: int
    safe_ram_budget_bytes: int
    safe_vram_budget_bytes: int
    measured_at: str


class PlatformAdapter(ABC):
    """Abstract platform adapter defining OS, directory, and hardware probes."""

    @abstractmethod
    def get_platform_name(self) -> str:
        """Returns normalized platform name ('linux', 'macos', 'unix')."""
        pass

    @abstractmethod
    def get_app_dir(self) -> Path:
        """Returns main application data directory."""
        pass

    @abstractmethod
    def get_log_dir(self) -> Path:
        """Returns main log directory."""
        pass

    @abstractmethod
    def inspect_hardware(self) -> HardwareSnapshot:
        """Gathers system CPU, memory, GPU, and disk metrics."""
        pass
