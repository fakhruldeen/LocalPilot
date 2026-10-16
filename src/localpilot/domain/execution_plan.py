"""Execution plan model and plan generation service (Section 4)."""

from __future__ import annotations
import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from localpilot.domain.capabilities import (
    DeploymentMethod,
    InferenceBackendId,
    ManagedServiceId,
)
from localpilot.domain.models import (
    ConfidenceLevel,
    ExecutionMode,
    NormalizedCandidate,
    VerificationStage,
)
from localpilot.domain.ownership import OwnershipClass
from localpilot.platforms.base import HardwareSnapshot, MemoryAccountingModel


class ExecutionPlan(BaseModel):
    """Complete, explicit execution plan matching Section 4."""
    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    candidate_id: str
    source: str
    model_id: str
    artifact_id: str
    revision: Optional[str] = None
    model_format: str

    # Three orthogonal execution dimensions
    inference_backend: InferenceBackendId
    backend_version: Optional[str] = None
    managed_service: ManagedServiceId = ManagedServiceId.NONE
    service_version: Optional[str] = None
    deployment_method: DeploymentMethod

    # Execution placement & parameters
    requested_execution_mode: ExecutionMode = ExecutionMode.AUTO
    expected_device_placement: Optional[str] = None  # None if not observable, never invented
    advertised_max_context: int
    usable_context: int
    target_context: int
    concurrency: int = 1

    # Resource accounting & estimates
    memory_accounting_model: MemoryAccountingModel
    estimated_ram_bytes: int
    estimated_vram_bytes: int = 0
    estimated_disk_bytes: int
    runtime_overhead_bytes: int = 512 * 1024 * 1024
    confidence: ConfidenceLevel = ConfidenceLevel.ESTIMATED
    verification_stage: VerificationStage = VerificationStage.SUPPORTED

    # Storage and endpoint
    storage_ownership: str = "localpilot_managed"
    storage_location: str
    endpoint_url: Optional[str] = None
    required_installations: List[str] = Field(default_factory=list)
    install_scope: str = "isolated"

    # Ownership and safety
    ownership_class: OwnershipClass = OwnershipClass.LOCALPILOT_OWNED
    is_advanced_only: bool = False
    protection_level: str = "recovery_copy_available"
    supported_monitoring_ops: List[str] = Field(
        default_factory=lambda: ["process_health", "endpoint_ping", "resource_usage"]
    )
    summary_label: str = ""

    @property
    def format(self) -> str:
        return self.model_format


class ExecutionPlanGenerator:
    """Generates valid candidate execution plans based on platform capabilities and detected services."""

    @staticmethod
    def generate_plans(
        candidate: NormalizedCandidate,
        hardware: HardwareSnapshot,
        available_services: Optional[List[ManagedServiceId]] = None,
    ) -> List[ExecutionPlan]:
        services = available_services or []
        plans: List[ExecutionPlan] = []
        is_macos = hardware.os_name.lower() == "macos"
        is_apple_silicon = is_macos and hardware.memory_model == MemoryAccountingModel.UNIFIED

        # 1. Standard llama.cpp plan (Direct local API service)
        if candidate.format == "gguf":
            device_placement = "Apple Metal" if is_apple_silicon else ("CUDA GPU" if hardware.gpu_devices else "CPU")
            plans.append(
                ExecutionPlan(
                    candidate_id=candidate.candidate_id,
                    source=candidate.source,
                    model_id=candidate.model_id,
                    artifact_id=candidate.artifact_id,
                    revision=candidate.revision,
                    model_format=candidate.format,
                    inference_backend=InferenceBackendId.LLAMACPP,
                    managed_service=ManagedServiceId.NONE,
                    deployment_method=DeploymentMethod.LOCAL_API_SERVICE,
                    requested_execution_mode=ExecutionMode.AUTO,
                    expected_device_placement=device_placement,
                    advertised_max_context=candidate.advertised_max_context,
                    usable_context=candidate.usable_context,
                    target_context=candidate.target_context,
                    memory_accounting_model=hardware.memory_model,
                    estimated_ram_bytes=candidate.estimated_ram_bytes,
                    estimated_vram_bytes=candidate.estimated_vram_bytes,
                    estimated_disk_bytes=candidate.estimated_disk_bytes,
                    storage_location=f"models/llamacpp/{candidate.artifact_id}",
                    endpoint_url="http://127.0.0.1:8080",
                    summary_label="llama.cpp (Local Service)",
                )
            )

            # Direct worker process plan (runs inference outside UI process)
            plans.append(
                ExecutionPlan(
                    candidate_id=candidate.candidate_id,
                    source=candidate.source,
                    model_id=candidate.model_id,
                    artifact_id=candidate.artifact_id,
                    revision=candidate.revision,
                    model_format=candidate.format,
                    inference_backend=InferenceBackendId.LLAMACPP,
                    managed_service=ManagedServiceId.NONE,
                    deployment_method=DeploymentMethod.DIRECT_WORKER,
                    requested_execution_mode=ExecutionMode.AUTO,
                    expected_device_placement=device_placement,
                    advertised_max_context=candidate.advertised_max_context,
                    usable_context=candidate.usable_context,
                    target_context=candidate.target_context,
                    memory_accounting_model=hardware.memory_model,
                    estimated_ram_bytes=candidate.estimated_ram_bytes,
                    estimated_vram_bytes=candidate.estimated_vram_bytes,
                    estimated_disk_bytes=candidate.estimated_disk_bytes,
                    storage_location=f"models/llamacpp/{candidate.artifact_id}",
                    endpoint_url=None,
                    summary_label="llama.cpp (Direct Worker Process)",
                )
            )

        # 2. Ollama execution plan
        if candidate.source == "ollama" or ManagedServiceId.OLLAMA in services or candidate.format == "ollama_blob":
            plans.append(
                ExecutionPlan(
                    candidate_id=candidate.candidate_id,
                    source=candidate.source,
                    model_id=candidate.model_id,
                    artifact_id=candidate.artifact_id,
                    revision=candidate.revision,
                    model_format="ollama_blob",
                    inference_backend=InferenceBackendId.UNKNOWN_SERVICE_MANAGED,
                    managed_service=ManagedServiceId.OLLAMA,
                    deployment_method=DeploymentMethod.LOCAL_API_SERVICE,
                    requested_execution_mode=ExecutionMode.AUTO,
                    expected_device_placement=None,  # Ollama manages placement internally
                    advertised_max_context=candidate.advertised_max_context,
                    usable_context=candidate.usable_context,
                    target_context=candidate.target_context,
                    memory_accounting_model=hardware.memory_model,
                    estimated_ram_bytes=candidate.estimated_ram_bytes,
                    estimated_vram_bytes=candidate.estimated_vram_bytes,
                    estimated_disk_bytes=candidate.estimated_disk_bytes,
                    storage_location="models/ollama",
                    endpoint_url="http://127.0.0.1:11434",
                    summary_label="Ollama Service",
                )
            )

        # 3. LM Studio / llmster plan (if available or GGUF format on compatible host)
        if ManagedServiceId.LM_STUDIO in services:
            plans.append(
                ExecutionPlan(
                    candidate_id=candidate.candidate_id,
                    source=candidate.source,
                    model_id=candidate.model_id,
                    artifact_id=candidate.artifact_id,
                    revision=candidate.revision,
                    model_format=candidate.format,
                    inference_backend=InferenceBackendId.UNKNOWN_SERVICE_MANAGED,
                    managed_service=ManagedServiceId.LM_STUDIO,
                    deployment_method=DeploymentMethod.LOCAL_API_SERVICE,
                    requested_execution_mode=ExecutionMode.AUTO,
                    expected_device_placement=None,
                    advertised_max_context=candidate.advertised_max_context,
                    usable_context=candidate.usable_context,
                    target_context=candidate.target_context,
                    memory_accounting_model=hardware.memory_model,
                    estimated_ram_bytes=candidate.estimated_ram_bytes,
                    estimated_vram_bytes=candidate.estimated_vram_bytes,
                    estimated_disk_bytes=candidate.estimated_disk_bytes,
                    storage_location="external/lm_studio",
                    endpoint_url="http://127.0.0.1:1234",
                    ownership_class=OwnershipClass.EXTERNAL_APPROVED,
                    summary_label="LM Studio / llmster Service",
                )
            )

        # 4. Docker Model Runner plan (if available)
        if ManagedServiceId.DOCKER_MODEL_RUNNER in services:
            plans.append(
                ExecutionPlan(
                    candidate_id=candidate.candidate_id,
                    source=candidate.source,
                    model_id=candidate.model_id,
                    artifact_id=candidate.artifact_id,
                    revision=candidate.revision,
                    model_format=candidate.format,
                    inference_backend=InferenceBackendId.UNKNOWN_SERVICE_MANAGED,
                    managed_service=ManagedServiceId.DOCKER_MODEL_RUNNER,
                    deployment_method=DeploymentMethod.LOCAL_API_SERVICE,
                    requested_execution_mode=ExecutionMode.AUTO,
                    expected_device_placement=None,
                    advertised_max_context=candidate.advertised_max_context,
                    usable_context=candidate.usable_context,
                    target_context=candidate.target_context,
                    memory_accounting_model=hardware.memory_model,
                    estimated_ram_bytes=candidate.estimated_ram_bytes,
                    estimated_vram_bytes=candidate.estimated_vram_bytes,
                    estimated_disk_bytes=candidate.estimated_disk_bytes,
                    storage_location="docker/model_runner",
                    endpoint_url="http://127.0.0.1:11435",
                    ownership_class=OwnershipClass.EXTERNAL_APPROVED,
                    summary_label="Docker Model Runner Service",
                )
            )

        # 5. Optional advanced vLLM plan (Linux only with GPU or explicit CPU, marked advanced)
        if not is_macos and hardware.gpu_devices:
            plans.append(
                ExecutionPlan(
                    candidate_id=candidate.candidate_id,
                    source=candidate.source,
                    model_id=candidate.model_id,
                    artifact_id=candidate.artifact_id,
                    revision=candidate.revision,
                    model_format=candidate.format,
                    inference_backend=InferenceBackendId.VLLM,
                    managed_service=ManagedServiceId.NONE,
                    deployment_method=DeploymentMethod.LOCAL_API_SERVICE,
                    requested_execution_mode=ExecutionMode.GPU,
                    expected_device_placement="CUDA GPU",
                    advertised_max_context=candidate.advertised_max_context,
                    usable_context=candidate.usable_context,
                    target_context=candidate.target_context,
                    memory_accounting_model=hardware.memory_model,
                    estimated_ram_bytes=int(candidate.estimated_ram_bytes * 1.5),
                    estimated_vram_bytes=int(candidate.estimated_vram_bytes * 1.3),
                    estimated_disk_bytes=candidate.estimated_disk_bytes,
                    storage_location=f"models/vllm/{candidate.model_id}",
                    endpoint_url="http://127.0.0.1:8000",
                    is_advanced_only=True,
                    summary_label="vLLM (Advanced GPU Service)",
                )
            )

        return plans
