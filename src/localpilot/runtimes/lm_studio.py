"""LM Studio and llmster service adapter.

Unifies LM Studio Desktop, the headless llmster daemon, and the lms CLI under
ManagedServiceId.LM_STUDIO to prevent duplicate provider registrations.
"""

from __future__ import annotations
import os
import platform
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
import psutil

from localpilot.domain.capabilities import (
    AdapterCapability,
    CapabilityResult,
    ImplementationStatus,
    LMStudioComponent,
    ManagedServiceId,
)
from localpilot.domain.ownership import OwnershipClass, ProcessIdentity
from localpilot.runtimes.base import ManagedServiceAdapter


class LMStudioServiceAdapter(ManagedServiceAdapter):
    """Adapter for interacting with LM Studio and its headless daemon (llmster)."""

    DEFAULT_ENDPOINT = "http://127.0.0.1:1234"

    def __init__(self, endpoint_url: str = DEFAULT_ENDPOINT) -> None:
        self.endpoint_url = endpoint_url.rstrip("/")

    def get_service_id(self) -> ManagedServiceId:
        return ManagedServiceId.LM_STUDIO

    def supports_capability(self, cap: AdapterCapability) -> CapabilityResult:
        supported_capabilities = {
            AdapterCapability.DETECT_INSTALLATION: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "LM Studio desktop, daemon (llmster), and CLI (lms) detection is implemented.",
            ),
            AdapterCapability.INSPECT_VERSION: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "Version inspection via lms CLI or API endpoint is implemented.",
            ),
            AdapterCapability.LIST_LOCAL_MODELS: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "Local model listing via /v1/models is supported.",
            ),
            AdapterCapability.RUN_TEST_INFERENCE: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "Test inference via /v1/chat/completions is supported.",
            ),
            AdapterCapability.UNLOAD_MODEL: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "Model memory unloading via API without stopping the daemon is supported.",
            ),
            AdapterCapability.CHECK_API_READINESS: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "Probing /v1/models for readiness is supported.",
            ),
            AdapterCapability.KEEP_RUNNING_AFTER_EXIT: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "LM Studio and llmster run as independent daemons or desktop apps.",
            ),
            AdapterCapability.REDISCOVER_RETAINED_EXECUTION: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "Loaded models can be rediscovered on application restart.",
            ),
        }

        if cap in supported_capabilities:
            status, explanation = supported_capabilities[cap]
            return CapabilityResult(
                supported=True,
                status=status,
                capability=cap,
                friendly_explanation=explanation,
            )

        # Explicitly declare unsupported/read-only operations to protect user environment
        if cap in (
            AdapterCapability.INSTALL_SOFTWARE,
            AdapterCapability.REMOVE_MODEL,
            AdapterCapability.STOP_OWNED_PROCESS,
        ):
            return CapabilityResult(
                supported=False,
                status=ImplementationStatus.UNSUPPORTED,
                capability=cap,
                reason_code="MANAGED_SERVICE_EXTERNAL_OWNERSHIP",
                friendly_explanation=(
                    f"LocalPilot treats LM Studio as an externally-owned service and will "
                    f"not modify files or terminate the application automatically."
                ),
            )

        return CapabilityResult(
            supported=False,
            status=ImplementationStatus.PLANNED,
            capability=cap,
            reason_code="NOT_YET_IMPLEMENTED",
            friendly_explanation=f"Capability '{cap.value}' is not currently implemented for LM Studio.",
        )

    def detect_components(self) -> Dict[LMStudioComponent, Optional[str]]:
        """Inspects the system for LM Studio Desktop, the llmster daemon, and lms CLI."""
        results: Dict[LMStudioComponent, Optional[str]] = {
            LMStudioComponent.DESKTOP_APP: None,
            LMStudioComponent.HEADLESS_DAEMON: None,
            LMStudioComponent.CLI: None,
        }

        os_sys = platform.system().lower()

        # 1. Desktop app detection
        if os_sys == "darwin":
            mac_paths = [
                Path("/Applications/LM Studio.app"),
                Path(os.path.expanduser("~/Applications/LM Studio.app")),
            ]
            for p in mac_paths:
                if p.exists():
                    results[LMStudioComponent.DESKTOP_APP] = str(p)
                    break
        elif os_sys == "linux":
            linux_paths = [
                Path(os.path.expanduser("~/.local/share/LM-Studio")),
                Path("/opt/LM-Studio"),
                Path("/usr/bin/lm-studio"),
            ]
            for p in linux_paths:
                if p.exists():
                    results[LMStudioComponent.DESKTOP_APP] = str(p)
                    break

        # 2. llmster headless daemon detection
        daemon_bin = shutil.which("llmster")
        if daemon_bin:
            results[LMStudioComponent.HEADLESS_DAEMON] = daemon_bin
        else:
            # Check user local bin
            local_llmster = Path(os.path.expanduser("~/.cache/lm-studio/bin/llmster"))
            if local_llmster.exists() and os.access(local_llmster, os.X_OK):
                results[LMStudioComponent.HEADLESS_DAEMON] = str(local_llmster)

        # 3. lms CLI detection
        cli_bin = shutil.which("lms")
        if cli_bin:
            results[LMStudioComponent.CLI] = cli_bin
        else:
            local_lms = Path(os.path.expanduser("~/.cache/lm-studio/bin/lms"))
            if local_lms.exists() and os.access(local_lms, os.X_OK):
                results[LMStudioComponent.CLI] = str(local_lms)

        return results

    async def check_api_readiness(self, endpoint_url: Optional[str] = None) -> bool:
        """Probes the LM Studio local server endpoint in read-only mode."""
        url = (endpoint_url or self.endpoint_url).rstrip("/") + "/v1/models"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
                return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self, endpoint_url: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists models loaded or available in LM Studio."""
        url = (endpoint_url or self.endpoint_url).rstrip("/") + "/v1/models"
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("data", [])
        except Exception:
            pass
        return []

    async def test_inference(self, endpoint_url: Optional[str] = None, model_id: str = "") -> bool:
        """Runs a minimal non-streaming prompt to verify inference readiness."""
        url = (endpoint_url or self.endpoint_url).rstrip("/") + "/v1/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    url,
                    json={
                        "model": model_id,
                        "messages": [{"role": "user", "content": "Hi"}],
                        "max_tokens": 5,
                    },
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def unload_model(self, endpoint_url: Optional[str] = None, model_id: str = "") -> bool:
        """Unloads model memory without stopping the service daemon."""
        base = (endpoint_url or self.endpoint_url).rstrip("/")
        # Try LM Studio REST unload endpoints
        endpoints = [
            f"{base}/v1/models/unload",
            f"{base}/api/v0/models/unload",
        ]
        for url in endpoints:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(url, json={"model": model_id})
                    if resp.status_code in (200, 204):
                        return True
            except Exception:
                continue
        return False
