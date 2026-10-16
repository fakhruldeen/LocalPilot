"""Tests for platform adapters, memory accounting models, and read-only inspection."""

import pytest
from localpilot.hardware.inspector import SystemInspector
from localpilot.platforms.base import HardwareSnapshot, MemoryAccountingModel, GpuDevice
from localpilot.platforms.linux import LinuxPlatformAdapter
from localpilot.platforms.macos import MacOSPlatformAdapter


def test_linux_hardware_inspection() -> None:
    adapter = LinuxPlatformAdapter()
    snapshot = adapter.inspect_hardware()

    assert snapshot.os_name == "Linux"
    assert snapshot.ram_total_bytes > 0
    assert snapshot.ram_available_bytes > 0
    assert snapshot.safe_ram_budget_bytes > 0
    assert snapshot.memory_model == MemoryAccountingModel.SEPARATE
    assert snapshot.disk_available_bytes > 0
    assert snapshot.measured_at.endswith("Z")


def test_macos_unified_memory_model(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = MacOSPlatformAdapter()

    # Simulate Apple Silicon arm64
    monkeypatch.setattr(adapter, "_is_apple_silicon", lambda: True)
    snapshot = adapter.inspect_hardware()

    assert snapshot.os_name == "macOS"
    assert snapshot.memory_model == MemoryAccountingModel.UNIFIED
    assert len(snapshot.gpu_devices) == 1
    assert snapshot.gpu_devices[0].acceleration_type == "metal"
    # In unified memory, VRAM budget equals safe RAM budget
    assert snapshot.safe_vram_budget_bytes == snapshot.safe_ram_budget_bytes


def test_macos_intel_separate_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = MacOSPlatformAdapter()

    # Simulate Intel Mac
    monkeypatch.setattr(adapter, "_is_apple_silicon", lambda: False)
    snapshot = adapter.inspect_hardware()

    assert snapshot.os_name == "macOS"
    assert snapshot.memory_model == MemoryAccountingModel.SEPARATE
    assert snapshot.gpu_devices[0].vram_total_bytes is None  # Unknown represented as None, never 0


def test_readonly_system_inspection() -> None:
    inspector = SystemInspector()
    report = inspector.inspect()

    assert isinstance(report.hardware, HardwareSnapshot)
    assert isinstance(report.discovered_runtimes, list)
    assert "curl" in report.supporting_tools
