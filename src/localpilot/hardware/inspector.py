"""Hardware inspector and read-only multi-scope runtime discovery service."""

from __future__ import annotations
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field

from localpilot.domain.capabilities import (
    InferenceBackendId,
    LMStudioComponent,
    ManagedServiceId,
)
from localpilot.platforms import get_platform_adapter
from localpilot.platforms.base import HardwareSnapshot


class DiscoveredRuntime(BaseModel):
    name: str                 # "ollama", "llamacpp", etc.
    scope: str                # "system", "user", "isolated"
    binary_path: str
    version: Optional[str] = None
    is_service_running: bool = False
    endpoint_url: Optional[str] = None


class DiscoveredBackend(BaseModel):
    backend_id: InferenceBackendId
    binary_path: Optional[str] = None
    version: Optional[str] = None
    scope: str = "system"
    is_available: bool = True


class DiscoveredManagedService(BaseModel):
    service_id: ManagedServiceId
    components: Dict[str, Optional[str]] = Field(default_factory=dict)
    is_service_running: bool = False
    endpoint_url: Optional[str] = None
    version: Optional[str] = None
    scope: str = "system"


class SystemInspectionReport(BaseModel):
    hardware: HardwareSnapshot
    discovered_runtimes: List[DiscoveredRuntime] = Field(default_factory=list)
    discovered_backends: List[DiscoveredBackend] = Field(default_factory=list)
    discovered_services: List[DiscoveredManagedService] = Field(default_factory=list)
    supporting_tools: Dict[str, Optional[str]] = Field(default_factory=dict)  # "curl", "git", etc.


class SystemInspector:
    """Performs non-mutating hardware inspection and runtime discovery."""

    def __init__(self) -> None:
        self.platform = get_platform_adapter()

    def _determine_scope(self, bin_path: str) -> str:
        app_dir = str(self.platform.get_app_dir())
        home_local = os.path.expanduser("~/.local")
        home = os.path.expanduser("~")

        if bin_path.startswith(app_dir):
            return "isolated"
        if bin_path.startswith(home_local) or bin_path.startswith(home):
            return "user"
        return "system"

    def discover_backends(self) -> List[DiscoveredBackend]:
        backends: List[DiscoveredBackend] = []

        # 1. llama.cpp
        llama_bin = shutil.which("llama-server") or shutil.which("llama-cli")
        app_llama = self.platform.get_app_dir() / "runtimes" / "llamacpp" / "bin" / "llama-server"
        if not llama_bin and app_llama.exists() and os.access(app_llama, os.X_OK):
            llama_bin = str(app_llama)

        if llama_bin:
            scope = self._determine_scope(llama_bin)
            backends.append(
                DiscoveredBackend(
                    backend_id=InferenceBackendId.LLAMACPP,
                    binary_path=llama_bin,
                    scope=scope,
                    is_available=True,
                )
            )

        # 2. vLLM (advanced, Linux-only check)
        from localpilot.runtimes.vllm_backend import VllmBackendAdapter
        vllm_adapter = VllmBackendAdapter()
        vllm_bin = vllm_adapter.detect_installation()
        if vllm_bin:
            scope = self._determine_scope(vllm_bin)
            backends.append(
                DiscoveredBackend(
                    backend_id=InferenceBackendId.VLLM,
                    binary_path=vllm_bin,
                    scope=scope,
                    is_available=True,
                )
            )

        return backends

    def discover_services(self) -> List[DiscoveredManagedService]:
        services: List[DiscoveredManagedService] = []

        # 1. Ollama service
        ollama_bin = shutil.which("ollama")
        app_ollama = self.platform.get_app_dir() / "runtimes" / "ollama" / "bin" / "ollama"
        if not ollama_bin and app_ollama.exists() and os.access(app_ollama, os.X_OK):
            ollama_bin = str(app_ollama)

        endpoint = "http://127.0.0.1:11434"
        running = False
        try:
            resp = httpx.get(f"{endpoint}/api/tags", timeout=1.0)
            if resp.status_code == 200:
                running = True
        except Exception:
            running = False

        if ollama_bin or running:
            scope = self._determine_scope(ollama_bin) if ollama_bin else "system"
            services.append(
                DiscoveredManagedService(
                    service_id=ManagedServiceId.OLLAMA,
                    components={"binary": ollama_bin},
                    is_service_running=running,
                    endpoint_url=endpoint if running else None,
                    scope=scope,
                )
            )

        # 2. LM Studio / llmster service (Unified canonical identity)
        from localpilot.runtimes.lm_studio import LMStudioServiceAdapter
        lms_adapter = LMStudioServiceAdapter()
        comps = lms_adapter.detect_components()
        lms_endpoint = "http://127.0.0.1:1234"
        lms_running = False
        try:
            resp = httpx.get(f"{lms_endpoint}/v1/models", timeout=1.0)
            if resp.status_code == 200:
                lms_running = True
        except Exception:
            lms_running = False

        has_lms = any(v is not None for v in comps.values()) or lms_running
        if has_lms:
            # Pick dominant component path for scope
            bin_path = comps[LMStudioComponent.CLI] or comps[LMStudioComponent.HEADLESS_DAEMON] or comps[LMStudioComponent.DESKTOP_APP]
            scope = self._determine_scope(bin_path) if bin_path else "system"
            services.append(
                DiscoveredManagedService(
                    service_id=ManagedServiceId.LM_STUDIO,
                    components={k.value: v for k, v in comps.items()},
                    is_service_running=lms_running,
                    endpoint_url=lms_endpoint if lms_running else None,
                    scope=scope,
                )
            )

        # 3. Docker Model Runner
        from localpilot.runtimes.docker_model_runner import DockerModelRunnerServiceAdapter
        dmr_adapter = DockerModelRunnerServiceAdapter()
        has_docker_cli = dmr_adapter.detect_docker_cli()
        has_plugin = dmr_adapter.check_model_plugin() if has_docker_cli else False
        if has_plugin:
            services.append(
                DiscoveredManagedService(
                    service_id=ManagedServiceId.DOCKER_MODEL_RUNNER,
                    components={"docker": has_docker_cli, "model_plugin": "true"},
                    is_service_running=False,
                    scope=self._determine_scope(has_docker_cli) if has_docker_cli else "system",
                )
            )

        return services

    def discover_runtimes(self) -> List[DiscoveredRuntime]:
        """Backward-compatible runtime discovery."""
        runtimes: List[DiscoveredRuntime] = []

        # 1. Probe Ollama
        ollama_bin = shutil.which("ollama")
        app_ollama = self.platform.get_app_dir() / "runtimes" / "ollama" / "bin" / "ollama"
        if not ollama_bin and app_ollama.exists() and os.access(app_ollama, os.X_OK):
            ollama_bin = str(app_ollama)

        if ollama_bin:
            scope = self._determine_scope(ollama_bin)
            running = False
            endpoint = "http://127.0.0.1:11434"
            try:
                resp = httpx.get(f"{endpoint}/api/tags", timeout=1.0)
                if resp.status_code == 200:
                    running = True
            except Exception:
                running = False

            runtimes.append(
                DiscoveredRuntime(
                    name="ollama",
                    scope=scope,
                    binary_path=ollama_bin,
                    is_service_running=running,
                    endpoint_url=endpoint if running else None,
                )
            )

        # 2. Probe llama.cpp (llama-server)
        llama_bin = shutil.which("llama-server") or shutil.which("llama-cli")
        app_llama = self.platform.get_app_dir() / "runtimes" / "llamacpp" / "bin" / "llama-server"
        if not llama_bin and app_llama.exists() and os.access(app_llama, os.X_OK):
            llama_bin = str(app_llama)

        if llama_bin:
            scope = self._determine_scope(llama_bin)
            runtimes.append(
                DiscoveredRuntime(
                    name="llamacpp",
                    scope=scope,
                    binary_path=llama_bin,
                    is_service_running=False,
                    endpoint_url=None,
                )
            )

        return runtimes

    def discover_supporting_tools(self) -> Dict[str, Optional[str]]:
        tools = ["curl", "git", "sh", "tar", "python3"]
        return {tool: shutil.which(tool) for tool in tools}

    def inspect(self) -> SystemInspectionReport:
        hw = self.platform.inspect_hardware()
        runtimes = self.discover_runtimes()
        backends = self.discover_backends()
        services = self.discover_services()
        tools = self.discover_supporting_tools()
        return SystemInspectionReport(
            hardware=hw,
            discovered_runtimes=runtimes,
            discovered_backends=backends,
            discovered_services=services,
            supporting_tools=tools,
        )
