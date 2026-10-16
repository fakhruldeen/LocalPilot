"""Generates previewable, redacted support diagnostic bundles (Section 24)."""

from __future__ import annotations
import json
import os
import platform
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from localpilot.observability.activity import ActivityService
from localpilot.observability.audit import AuditService
from localpilot.observability.redaction import redact_data
from localpilot.storage.db import DatabaseManager, get_db, get_default_data_dir, get_default_log_dir


def generate_support_bundle_dict(db: Optional[DatabaseManager] = None) -> Dict[str, Any]:
    """Generates the in-memory redacted diagnostic data."""
    db_mgr = db or get_db()
    activity_svc = ActivityService(db_mgr)
    audit_svc = AuditService(db_mgr)

    system_info = {
        "os": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
    }

    recent_activities = [a.model_dump() for a in activity_svc.get_activities(limit=25)]
    recent_decisions = audit_svc.get_decisions(limit=25)

    data = {
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "system": system_info,
        "recent_activities": recent_activities,
        "recent_decisions": recent_decisions,
    }

    return redact_data(data)


def export_support_bundle(output_file: Path | str, db: Optional[DatabaseManager] = None) -> Path:
    """Exports redacted diagnostics to a JSON bundle."""
    out_path = Path(output_file).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_data = generate_support_bundle_dict(db)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(bundle_data, f, indent=2)
    return out_path
