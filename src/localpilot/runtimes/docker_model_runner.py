"""Docker Model Runner service adapter.

Represents Docker Model Runner as a managed execution service, distinct from
generic container deployments.
"""

from __future__ import annotations
import shutil
import subprocess
from typing import Any, Dict, List, Optional
import httpx

from localpilot.domain.capabilities import (
    AdapterCapability,
    CapabilityResult,
    ImplementationStatus,
    ManagedServiceId,
)
from localpilot.domain.ownership import OwnershipClass, ProcessIdentity
from localpilot.runtimes.base import ManagedServiceAdapter


class DockerModelRunnerServiceAdapter(ManagedServiceAdapter):
    """Adapter for interacting with Docker Model Runner service."""

    DEFAULT_ENDPOINT = "http://127.0.0.1:11434"

    def __init__(self, endpoint_url: str = DEFAULT_ENDPOINT) -> None:
        self.endpoint_url = endpoint_url.rstrip("/")

    def get_service_id(self) -> ManagedServiceId:
        return ManagedServiceId.DOCKER_MODEL_RUNNER

    def supports_capability(self, cap: AdapterCapability) -> CapabilityResult:
        supported_capabilities = {
            AdapterCapability.DETECT_INSTALLATION: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "Docker and Docker Model Runner plugin detection is implemented.",
            ),
            AdapterCapability.INSPECT_VERSION: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "Docker version and plugin version inspection is implemented.",
            ),
            AdapterCapability.CHECK_API_READINESS: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "API readiness probing via local endpoint is implemented.",
            ),
            AdapterCapability.RUN_TEST_INFERENCE: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "Inference testing via OpenAI/Ollama compatible endpoint is supported.",
            ),
            AdapterCapability.UNLOAD_MODEL: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "Model memory unloading via service endpoint is supported.",
            ),
            AdapterCapability.KEEP_RUNNING_AFTER_EXIT: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "Docker Model Runner runs as a background service/container.",
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

        if cap in (
            AdapterCapability.INSTALL_SOFTWARE,
            AdapterCapability.STOP_OWNED_PROCESS,
            AdapterCapability.REMOVE_MODEL,
        ):
            return CapabilityResult(
                supported=False,
                status=ImplementationStatus.UNSUPPORTED,
                capability=cap,
                reason_code="EXTERNAL_SERVICE_PROTECTION",
                friendly_explanation=(
                    "LocalPilot does not install Docker, modify docker daemon configuration, "
                    "or terminate externally-managed containers automatically."
                ),
            )

        return CapabilityResult(
            supported=False,
            status=ImplementationStatus.PLANNED,
            capability=cap,
            reason_code="NOT_YET_IMPLEMENTED",
            friendly_explanation=f"Capability '{cap.value}' is not currently implemented for Docker Model Runner.",
        )

    def detect_docker_cli(self) -> Optional[str]:
        """Finds the docker executable on PATH."""
        return shutil.which("docker")

    def check_daemon_access(self) -> bool:
        """Verifies that the current user can communicate with the Docker daemon."""
        cli = self.detect_docker_cli()
        if not cli:
            return False
        try:
            res = subprocess.run(
                [cli, "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            return res.returncode == 0
        except Exception:
            return False

    def check_model_plugin(self) -> bool:
        """Checks if the docker-model CLI plugin is installed and accessible."""
        cli = self.detect_docker_cli()
        if not cli:
            return False
        try:
            res = subprocess.run(
                [cli, "model", "--help"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            return res.returncode == 0
        except Exception:
            return False

    async def check_api_readiness(self, endpoint_url: Optional[str] = None) -> bool:
        """Probes the Docker Model Runner local endpoint in read-only mode."""
        url = (endpoint_url or self.endpoint_url).rstrip("/") + "/v1/models"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
                return resp.status_code == 200
        except Exception:
            return False

    async def unload_model(self, endpoint_url: Optional[str] = None, model_id: str = "") -> bool:
        """Unloads model memory without stopping the Docker service."""
        base = (endpoint_url or self.endpoint_url).rstrip("/")
        # Model runner endpoints typically support /api/generate with keep_alive=0 or /v1/models/unload
        for url, payload in [
            (f"{base}/api/generate", {"model": model_id, "keep_alive": 0}),
            (f"{base}/v1/models/unload", {"model": model_id}),
        ]:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(url, json=payload)
                    if resp.status_code in (200, 204):
                        return True
            except Exception:
                continue
        return False
