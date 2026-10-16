"""Normalized candidate models and domain value objects."""

from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class VerificationStage(str, Enum):
    SUPPORTED = "supported"              # LocalPilot supports this artifact/runtime/platform
    EXPECTED_TO_FIT = "expected_to_fit"  # Passed estimated resource check
    DOWNLOADED_PREPARED = "downloaded_prepared"  # Model downloaded and prepared on disk
    TESTED_HERE = "tested_here"          # Verified inference completed on this computer


class ConfidenceLevel(str, Enum):
    VERIFIED = "verified"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class TaskCategory(str, Enum):
    CHAT = "chat"
    CODING = "coding"
    REASONING = "reasoning"
    VISION = "vision"
    EMBEDDING = "embedding"


class ExecutionMode(str, Enum):
    AUTO = "automatic"
    CPU = "cpu"
    GPU = "gpu"
    CPU_GPU = "cpu_gpu"


from localpilot.domain.capabilities import (
    DeploymentMethod,
    InferenceBackendId,
    ManagedServiceId,
)


class ResolvedArtifact(BaseModel):
    """Explicit artifact resolution contract matching Section 9.1."""
    source: str
    model_id: str
    artifact_id: str
    revision: str = "main"
    filename: str
    download_url: Optional[str] = None
    expected_bytes: int = 0
    expected_sha256: Optional[str] = None
    acquisition_mode: str = "direct_download"  # "direct_download", "service_pull", "local_file"


class NormalizedCandidate(BaseModel):
    """Normalized runnable candidate configuration matching Section 11."""
    candidate_id: str
    model_id: str
    name: str
    source: str                          # "huggingface", "ollama", "local"
    revision: Optional[str] = None
    artifact_id: str
    equivalence_group: Optional[str] = None
    architecture: str                    # "llama", "qwen2", "phi3", etc.
    family: str                          # "Llama 3", "Qwen 2.5", etc.
    tasks: List[TaskCategory] = Field(default_factory=lambda: [TaskCategory.CHAT])
    languages: List[str] = Field(default_factory=lambda: ["en"])
    parameter_count: Optional[str] = None  # e.g. "3B", "7B", "14B"

    # Files, format and acquisition URLs
    format: str                          # "gguf", "ollama_blob"
    total_bytes: int
    files: List[str] = Field(default_factory=list)
    sha256: Optional[str] = None
    download_url: Optional[str] = None
    source_url: Optional[str] = None

    # Runtime & Execution (backward compatible + 3-dimension extensions)
    runtime: str                         # "ollama", "llamacpp" (backward compatible alias)
    preferred_backend: Optional[InferenceBackendId] = None
    preferred_service: Optional[ManagedServiceId] = None
    supported_deployments: List[DeploymentMethod] = Field(
        default_factory=lambda: [DeploymentMethod.LOCAL_API_SERVICE, DeploymentMethod.DIRECT_WORKER]
    )
    min_runtime_version: Optional[str] = None
    execution_mode: ExecutionMode = ExecutionMode.AUTO
    gpu_layers: Optional[int] = None

    # Context
    advertised_max_context: int = 4096
    usable_context: int = 4096
    target_context: int = 4096

    # Timestamps
    verified_release_at: Optional[str] = None
    repo_created_at: Optional[str] = None
    repo_modified_at: Optional[str] = None
    artifact_published_at: Optional[str] = None
    metadata_fetched_at: str

    # Access
    requires_auth: bool = False
    requires_terms_acceptance: bool = False
    license: Optional[str] = None

    # Resource Estimates
    estimated_ram_bytes: int
    estimated_vram_bytes: int = 0
    estimated_disk_bytes: int
    temp_disk_bytes: int = 0

    # Verification and confidence
    confidence: ConfidenceLevel = ConfidenceLevel.ESTIMATED
    verification_stage: VerificationStage = VerificationStage.SUPPORTED
    provenance: Dict[str, Any] = Field(default_factory=dict)

    @property
    def parameter_count_approx_b(self) -> float:
        """Parses parameter_count (e.g. '1.5B', '7B') to float billions, default 7.0."""
        if not self.parameter_count:
            return 7.0
        val = self.parameter_count.upper().replace("B", "").replace("M", "").strip()
        try:
            num = float(val)
            if "M" in self.parameter_count.upper():
                return num / 1000.0
            return num
        except ValueError:
            return 7.0
