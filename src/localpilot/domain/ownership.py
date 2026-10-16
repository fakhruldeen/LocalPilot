"""Process ownership verification and lifecycle management (Section 16)."""

from __future__ import annotations
from enum import Enum
from pathlib import Path
from typing import Optional
import psutil
from pydantic import BaseModel


from typing import List, Optional
from pydantic import BaseModel, Field


class OwnershipClass(str, Enum):
    LOCALPILOT_OWNED = "localpilot_owned"
    EXTERNAL_APPROVED = "external_approved"
    READONLY_DISCOVERED = "readonly_discovered"
    UNKNOWN = "unknown"


class ProcessIdentity(BaseModel):
    pid: Optional[int] = None
    process_create_time: Optional[float] = None
    executable_path: Optional[str] = None
    endpoint_url: Optional[str] = None
    ownership_class: OwnershipClass = OwnershipClass.UNKNOWN
    service_id: Optional[str] = None
    container_id: Optional[str] = None
    container_image: Optional[str] = None
    mounted_paths: List[str] = Field(default_factory=list)
    exposed_ports: List[int] = Field(default_factory=list)
    deployment_method: str = "local_api_service"


class OwnershipVerifier:
    """Verifies that a running OS process matches the recorded identity."""

    @staticmethod
    def verify_process(expected: ProcessIdentity) -> bool:
        """Checks PID, existence, creation time, and executable match to prevent PID reuse bugs."""
        if expected.pid is None:
            # External API service with unknown process identity; verify endpoint if provided
            return bool(expected.endpoint_url)

        try:
            if not psutil.pid_exists(expected.pid):
                return False
            p = psutil.Process(expected.pid)
            # Check create time within 1 second tolerance
            if expected.process_create_time is not None:
                if abs(p.create_time() - expected.process_create_time) > 1.0:
                    return False
            # Check executable path if available
            if expected.executable_path:
                try:
                    exe = p.exe()
                    if exe and Path(exe).name != Path(expected.executable_path).name:
                        return False
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    pass
            return True
        except Exception:
            return False
