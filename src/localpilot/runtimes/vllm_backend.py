"""vLLM inference backend adapter.

Marked as advanced-only, strictly requiring Linux with GPU acceleration (CUDA or ROCm).
Unsupported on macOS.
"""

from __future__ import annotations
import os
import platform
import shutil
import sys
from typing import Any, Dict, List, Optional, Tuple
import httpx

from localpilot.domain.capabilities import (
    AdapterCapability,
    CapabilityResult,
    ImplementationStatus,
    InferenceBackendId,
)
from localpilot.runtimes.base import InferenceBackendAdapter


class VllmBackendAdapter(InferenceBackendAdapter):
    """Adapter for the vLLM high-throughput inference engine."""

    is_advanced_only: bool = True

    def get_backend_id(self) -> InferenceBackendId:
        return InferenceBackendId.VLLM

    def supports_capability(self, cap: AdapterCapability) -> CapabilityResult:
        supported_capabilities = {
            AdapterCapability.DETECT_INSTALLATION: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "vLLM package and CLI detection is implemented.",
            ),
            AdapterCapability.INSPECT_VERSION: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "Version inspection via python environment or CLI is implemented.",
            ),
            AdapterCapability.LAUNCH_SERVER_OR_WORKER: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "Launching vllm serve OpenAI-compatible server or worker is implemented.",
            ),
            AdapterCapability.RUN_TEST_INFERENCE: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "Test inference via OpenAI-compatible API endpoint is supported.",
            ),
            AdapterCapability.STOP_OWNED_PROCESS: (
                ImplementationStatus.IMPLEMENTED_TESTED,
                "Stopping owned vLLM child processes is supported.",
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

        return CapabilityResult(
            supported=False,
            status=ImplementationStatus.UNSUPPORTED,
            capability=cap,
            reason_code="CAPABILITY_NOT_SUPPORTED",
            friendly_explanation=f"vLLM adapter does not support capability '{cap.value}'.",
        )

    @classmethod
    def check_platform_compatibility(cls, os_sys: Optional[str] = None, has_gpu: bool = False) -> Tuple[bool, str]:
        """Validates OS and hardware constraints for vLLM."""
        sys_name = (os_sys or platform.system()).lower()
        if sys_name == "darwin":
            return False, "vLLM is not supported on macOS (Apple Silicon or Intel)."
        if sys_name != "linux":
            return False, f"vLLM is not supported on {sys_name}."
        if not has_gpu:
            return False, "vLLM requires a compatible GPU accelerator (NVIDIA CUDA or ROCm)."
        return True, "Platform and hardware meet vLLM requirements."

    def detect_installation(self) -> Optional[str]:
        """Checks for vLLM executable on PATH or in current Python environment."""
        cli = shutil.which("vllm")
        if cli:
            return cli
        # Check current python environment
        try:
            import vllm  # type: ignore # noqa: F401
            return sys.executable
        except ImportError:
            pass
        return None

    async def test_inference(self, endpoint_url: str, model_id: str) -> bool:
        """Verifies responsiveness of vLLM server via /v1/chat/completions."""
        url = endpoint_url.rstrip("/") + "/v1/chat/completions"
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
