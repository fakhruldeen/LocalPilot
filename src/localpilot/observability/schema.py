"""Observability schema definitions, event envelopes, and audit records."""

from __future__ import annotations
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class EventLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class EventCategory(str, Enum):
    APPLICATION = "application"
    USER_ACTION = "user_action"
    WORKFLOW = "workflow"
    CATALOG = "catalog"
    AUTHENTICATION = "authentication"
    HARDWARE = "hardware"
    COMPATIBILITY = "compatibility"
    SCORING = "scoring"
    INSTALLATION = "installation"
    DOWNLOAD = "download"
    RUNTIME = "runtime"
    PROCESS = "process"
    MODEL = "model"
    MONITORING = "monitoring"
    UPDATE = "update"
    STORAGE = "storage"
    SECURITY = "security"
    RECOVERY = "recovery"
    PLATFORM = "platform"


class EventEnvelope(BaseModel):
    """Standardized event envelope matching Section 21.4."""
    schema_version: int = 1
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    level: EventLevel = EventLevel.INFO
    category: EventCategory
    event_name: str
    message: str
    session_id: Optional[str] = None
    operation_id: Optional[str] = None
    job_id: Optional[str] = None
    installation_id: Optional[str] = None
    instance_id: Optional[str] = None
    execution_plan_id: Optional[str] = None
    inference_backend: Optional[str] = None
    managed_service: Optional[str] = None
    deployment_method: Optional[str] = None
    outcome: Optional[str] = None
    reason_code: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class ActivityRecord(BaseModel):
    """User-friendly activity timeline item."""
    activity_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    category: str
    title: str
    summary: str
    details: Dict[str, Any] = Field(default_factory=dict)
    operation_id: Optional[str] = None
    outcome: str = "success"


class RecommendationDecisionRecord(BaseModel):
    """Immutable audit record explaining why a recommendation was made (Section 21.5)."""
    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    candidate_id: str
    policy_version: str = "compatibility-v1"
    scoring_version: str = "scoring-v1"
    hardware_snapshot: Dict[str, Any]
    execution_plan: Dict[str, Any]
    checks: List[Dict[str, Any]]
    score_components: Dict[str, float]
    total_score: float
    confidence: str
    rank: int
    outcome: str
    reason_code: Optional[str] = None


class InstallationAuditRecord(BaseModel):
    """Audit record for model or runtime installations."""
    record_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    target_type: str  # "model" or "runtime"
    target_id: str
    install_scope: str  # "isolated", "user", "system"
    plan: Dict[str, Any]
    approved_by_user: bool
    changes_made: List[str]
    outcome: str
    reason_code: Optional[str] = None


class DeletionAuditRecord(BaseModel):
    """Audit record for model or runtime deletion."""
    record_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    target_type: str
    target_id: str
    deletion_method: str  # "unregister", "delete_files", "runtime_native"
    files_affected: List[str]
    bytes_recovered: int
    approved_by_user: bool
    outcome: str
    reason_code: Optional[str] = None


class UpdateAuditRecord(BaseModel):
    """Audit record for updates and rollbacks."""
    record_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    target_type: str
    target_id: str
    from_version: str
    to_version: str
    protection_level: str
    test_outcome: str
    activation_outcome: str
    rollback_occurred: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)
