"""Runtime adapter base classes, multi-scope provisioner, and lifecycle interface."""

from __future__ import annotations
import os
import shutil
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from pydantic import BaseModel

from localpilot.domain.models import NormalizedCandidate
from localpilot.domain.ownership import OwnershipClass, ProcessIdentity


class InstallationScope(str, Enum):
    ISOLATED = "isolated"  # Inside LocalPilot app folder: ~/.local/share/localpilot/runtimes/...
    USER = "user"          # In ~/.local/bin and ~/.local/lib
    SYSTEM = "system"      # In /usr/local/bin and /usr/local/lib (requires elevation)


class ProtectionLevel(str, Enum):
    PREVIOUS_VERSION_RETAINED = "previous_version_retained"
    RECOVERY_COPY_AVAILABLE = "recovery_copy_available"
    ROLLBACK_UNAVAILABLE = "rollback_unavailable"


class RuntimeCapabilities(BaseModel):
    name: str
    supported_formats: List[str]
    resolve_installed_identity: bool = True
    detect_newer_identity: bool = True
    retain_both_versions: bool = True
    test_independently: bool = True
    restore_previous_version: bool = True
    protection_level: ProtectionLevel = ProtectionLevel.RECOVERY_COPY_AVAILABLE


class RuntimeInstallPlan(BaseModel):
    runtime_name: str
    target_scope: InstallationScope
    target_bin_path: str
    download_url: Optional[str] = None
    expected_sha256: Optional[str] = None
    estimated_download_bytes: int = 0
    requires_elevation: bool = False
    plan_description: str


from localpilot.domain.capabilities import (
    AdapterCapability,
    CapabilityResult,
    ImplementationStatus,
    InferenceBackendId,
    ManagedServiceId,
)


class BaseRuntimeAdapter(ABC):
    """Abstract interface for model execution engines."""

    @abstractmethod
    def get_runtime_name(self) -> str:
        pass

    @abstractmethod
    def get_capabilities(self) -> RuntimeCapabilities:
        pass

    def supports_capability(self, cap: AdapterCapability) -> CapabilityResult:
        """Returns structured capability support result."""
        caps = self.get_capabilities()
        # Default mapping based on declared capabilities
        if cap == AdapterCapability.RESTORE_PREVIOUS_VERSION and not caps.restore_previous_version:
            return CapabilityResult(
                supported=False,
                status=ImplementationStatus.UNSUPPORTED,
                capability=cap,
                reason_code="ROLLBACK_NOT_SUPPORTED",
                friendly_explanation=f"{self.get_runtime_name()} does not support automatic rollback to previous version.",
            )
        return CapabilityResult(
            supported=True,
            status=ImplementationStatus.IMPLEMENTED_TESTED,
            capability=cap,
            friendly_explanation=f"Capability '{cap.value}' is supported by {self.get_runtime_name()}.",
        )

    @abstractmethod
    def detect_existing_installation(self) -> Optional[Tuple[InstallationScope, str]]:
        """Checks system, user, and isolated scopes for an existing binary.

        Returns (scope, binary_path) if found, else None.
        """
        pass

    @abstractmethod
    def build_install_plan(self, scope: InstallationScope = InstallationScope.ISOLATED) -> RuntimeInstallPlan:
        """Constructs an installation plan for the specified scope."""
        pass

    @abstractmethod
    async def prepare_runtime(
        self,
        scope: InstallationScope = InstallationScope.ISOLATED,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> str:
        """Installs runtime into chosen scope if not present and returns binary path."""
        pass

    @abstractmethod
    async def start_instance(
        self,
        model_path_or_id: str,
        candidate: NormalizedCandidate,
        instance_id: str,
        log_file: Path,
    ) -> ProcessIdentity:
        """Starts model process and returns verified process identity."""
        pass

    @abstractmethod
    async def stop_instance(self, identity: ProcessIdentity) -> bool:
        """Stops the running model process safely."""
        pass

    @abstractmethod
    async def test_inference(self, endpoint_url: str, model_id: str) -> bool:
        """Runs a verification inference request to confirm model readiness."""
        pass


class InferenceBackendAdapter(ABC):
    """Abstract interface for an inference computation engine."""

    @abstractmethod
    def get_backend_id(self) -> InferenceBackendId:
        pass

    @abstractmethod
    def supports_capability(self, cap: AdapterCapability) -> CapabilityResult:
        pass


class ManagedServiceAdapter(ABC):
    """Abstract interface for a managed execution daemon/service."""

    @abstractmethod
    def get_service_id(self) -> ManagedServiceId:
        pass

    @abstractmethod
    def supports_capability(self, cap: AdapterCapability) -> CapabilityResult:
        pass

    @abstractmethod
    async def unload_model(self, endpoint_url: str, model_id: str) -> bool:
        """Unloads model memory without stopping the service daemon."""
        pass
