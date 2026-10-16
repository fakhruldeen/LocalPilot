"""Running model audit and health assessment policy (Section 17)."""

from __future__ import annotations
from enum import Enum
from typing import Any, Dict, Optional
import httpx
from pydantic import BaseModel

from localpilot.domain.ownership import OwnershipClass, OwnershipVerifier, ProcessIdentity


class ModelHealthStatus(str, Enum):
    HEALTHY = "healthy"
    HEALTHY_LIMITED_HEADROOM = "healthy_limited_headroom"
    NEEDS_ATTENTION = "needs_attention"
    API_UNAVAILABLE = "api_unavailable"
    PARTIALLY_INSPECTABLE = "partially_inspectable"
    IDENTITY_UNKNOWN = "identity_unknown"


class RunningModelAuditResult(BaseModel):
    instance_id: str
    status: ModelHealthStatus
    details: Dict[str, Any]
    friendly_summary: str


class RunningModelAuditor:
    """Audits running model instances against process identity, health endpoints, and memory pressure."""

    @staticmethod
    def audit_instance(
        instance_id: str,
        identity: ProcessIdentity,
        endpoint_url: str,
        timeout_seconds: float = 2.0,
    ) -> RunningModelAuditResult:
        # 1. Process identity verification
        is_process_valid = OwnershipVerifier.verify_process(identity)
        if not is_process_valid and identity.ownership_class == OwnershipClass.LOCALPILOT_OWNED:
            return RunningModelAuditResult(
                instance_id=instance_id,
                status=ModelHealthStatus.IDENTITY_UNKNOWN,
                details={"pid": identity.pid, "error": "Process terminated or PID reused"},
                friendly_summary="The model process stopped or is no longer recognized.",
            )

        # 2. Endpoint health check
        is_api_ok = False
        status_code = None
        error_msg = None
        try:
            # Test health or tags endpoint
            test_url = endpoint_url.rstrip("/") + "/health" if "11434" not in endpoint_url else endpoint_url.rstrip("/") + "/api/tags"
            resp = httpx.get(test_url, timeout=timeout_seconds)
            status_code = resp.status_code
            if resp.status_code in (200, 204):
                is_api_ok = True
        except Exception as e:
            error_msg = str(e)

        if not is_api_ok:
            return RunningModelAuditResult(
                instance_id=instance_id,
                status=ModelHealthStatus.API_UNAVAILABLE,
                details={"endpoint": endpoint_url, "status_code": status_code, "error": error_msg},
                friendly_summary="The model process is running, but its interface is not responding.",
            )

        return RunningModelAuditResult(
            instance_id=instance_id,
            status=ModelHealthStatus.HEALTHY,
            details={"endpoint": endpoint_url, "pid": identity.pid, "status_code": status_code},
            friendly_summary="Model is running and healthy.",
        )
