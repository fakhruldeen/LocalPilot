"""Domain entities for versioned model management, aliases, and verification evidence.

Implements Sections 4 & 5 of the architecture:
- LogicalModel: stable user-recognizable model family/identity.
- ModelVersion: immutable execution-relevant model artifact record.
- ExecutionProfile: versioned configuration (backend, placement, resource limits).
- ModelAlias: stable user/API-facing pointer to an approved (version, profile) target.
- AliasHistoryRecord: audit trail of alias activations, switches, and rollbacks.
- VerificationRecord: local evidence confirming host readiness and benchmark outcomes.
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from localpilot.domain.capabilities import (
    DeploymentMethod,
    InferenceBackendId,
    ManagedServiceId,
)
from localpilot.domain.execution_plan import ExecutionSpecification
from localpilot.domain.models import ExecutionMode


class LogicalModel(BaseModel):
    """A user-recognizable model grouping (e.g. 'Llama 3.2 3B', 'Qwen 2.5 Coder 7B')."""
    logical_model_id: str
    display_name: str
    family: str = "general"
    source_lineage: str = "local"
    task_type: str = "general"
    description: Optional[str] = None
    default_alias: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    updated_at: Optional[str] = None


class ModelVersion(BaseModel):
    """An immutable, execution-relevant installed model version record.

    Represents exact weights, upstream revision, format variant, and integrity status.
    Once installed and verified, this record is strictly immutable.
    """
    version_id: str
    logical_model_id: str
    version_label: str = "v1"                       # e.g. "v1.0.0", "1.5b-q4_k_m"
    source: str = "local"                           # "huggingface", "ollama", "local"
    upstream_revision: Optional[str] = None         # Exact commit SHA or manifest digest; never invented
    format_variant: str = "gguf-q4_k_m"             # "gguf-q4_k_m", "ollama_blob"
    parameter_count_approx_b: float = 7.0
    artifact_manifest: List[Dict[str, Any]] = Field(default_factory=list)  # artifact IDs, paths, expected hashes, sizes
    integrity_status: str = "verified"              # "verified", "needs_verification", "tampered"
    installation_status: str = "installed"          # "installed", "staged", "archived"
    is_pinned: bool = False                         # Pin protection against automatic prune or replacement
    pinned_reason: Optional[str] = None
    pin_count: int = 0
    installed_path: Optional[str] = None
    installation_id: Optional[str] = None
    artifact_uri: Optional[str] = None
    artifact_digest: Optional[str] = None
    artifact_size_bytes: int = 0
    quantization: Optional[str] = None
    context_length: int = 2048
    compatibility_matrix: Dict[str, Any] = Field(default_factory=dict)
    is_deleted: bool = False
    deleted_at: Optional[str] = None
    installed_at: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


class ExecutionProfile(BaseModel):
    """A versioned execution configuration for an exact model version.

    Specifies the backend, placement, context window, concurrency, and memory budget.
    """
    profile_id: str = Field(default_factory=lambda: f"prof-{uuid.uuid4().hex[:8]}")
    version_id: str
    profile_name: str = "balanced"                   # "balanced", "extended_context", "gpu_full", "cpu_low_ram"
    inference_backend: str = "llama.cpp"
    managed_service: str = "none"
    deployment_method: str = "local_api_service"
    execution_mode: str = "auto"
    offload_layers: int = 0
    threads: Optional[int] = None
    gpu_layers: Optional[int] = None
    context_window: int = 2048
    context_tokens: int = 2048
    concurrency: int = 1
    temperature: float = 0.7
    required_memory_budget_bytes: int = 2 * 1024**3
    execution_spec: Optional[ExecutionSpecification] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


class ModelAlias(BaseModel):
    """A stable user- and API-facing pointer (e.g. 'default', 'writing', 'coding').

    Resolves to an exact approved (version_id, profile_id) target.
    """
    alias_name: str                                  # "default", "writing", "coding"
    version_id: str
    logical_model_id: Optional[str] = None
    profile_id: Optional[str] = None
    description: Optional[str] = None
    is_system_alias: bool = False
    created_at: Optional[str] = None
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


class AliasHistoryRecord(BaseModel):
    """Audit record capturing alias redirection, activation, and rollback history."""
    history_id: str = Field(default_factory=lambda: f"ahist-{uuid.uuid4().hex[:8]}")
    alias_name: str
    previous_version_id: Optional[str] = None
    new_version_id: str
    previous_profile_id: Optional[str] = None
    new_profile_id: Optional[str] = None
    operation: str = "switch"                        # "activate", "switch", "rollback"
    reason: Optional[str] = None
    changed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


class VerificationRecord(BaseModel):
    """Evidence of local host verification for an exact version/profile combination."""
    verification_id: str = Field(default_factory=lambda: f"vrec-{uuid.uuid4().hex[:8]}")
    version_id: str
    profile_id: Optional[str] = None
    runtime_version: Optional[str] = None
    hardware_snapshot_hash: Optional[str] = None
    instance_id: Optional[str] = None
    test_type: str = "readiness_probe"               # "readiness_probe", "inference_smoke"
    outcome: str = "passed"                          # "passed", "failed"
    passed: bool = True
    benchmark_tps: Optional[float] = None
    first_token_latency_ms: Optional[float] = None
    error_message: Optional[str] = None
    observed_metrics: Dict[str, Any] = Field(default_factory=dict)
    limitations: Optional[str] = None
    verified_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

