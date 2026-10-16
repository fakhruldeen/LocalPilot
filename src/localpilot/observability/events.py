"""Central ObservabilityHub emitting structured events, activity logs, and audit records."""

from __future__ import annotations
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from localpilot.observability.context import (
    get_deployment_method,
    get_execution_plan_id,
    get_inference_backend,
    get_installation_id,
    get_instance_id,
    get_job_id,
    get_managed_service,
    get_operation_id,
    get_session_id,
)
from localpilot.observability.redaction import redact_data
from localpilot.observability.schema import (
    ActivityRecord,
    EventCategory,
    EventEnvelope,
    EventLevel,
)
from localpilot.storage.db import DatabaseManager, get_db, get_default_log_dir


class AuditCheckpointFailureError(RuntimeError):
    """Raised when an essential audit checkpoint fails to persist, blocking destructive actions."""
    pass


class ObservabilityHub:
    """Coordinates structured event logging, activity timeline persistence, and audit checkpoints."""

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        log_dir: Optional[Path | str] = None,
        max_log_size_bytes: int = 10 * 1024 * 1024,  # 10 MB
        max_log_files: int = 5,
    ) -> None:
        self.db = db or get_db()
        self.log_dir = Path(log_dir).resolve() if log_dir else get_default_log_dir()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.max_log_size_bytes = max_log_size_bytes
        self.max_log_files = max_log_files
        self.events_file = self.log_dir / "events.jsonl"
        self._lock = threading.Lock()

    def _rotate_if_needed(self) -> None:
        if not self.events_file.exists():
            return
        if self.events_file.stat().st_size < self.max_log_size_bytes:
            return

        for i in range(self.max_log_files - 1, 0, -1):
            src = self.log_dir / f"events.jsonl.{i}"
            dst = self.log_dir / f"events.jsonl.{i+1}"
            if src.exists():
                if dst.exists():
                    dst.unlink()
                src.rename(dst)

        first_rotated = self.log_dir / "events.jsonl.1"
        if first_rotated.exists():
            first_rotated.unlink()
        self.events_file.rename(first_rotated)

    def emit_event(
        self,
        category: EventCategory,
        event_name: str,
        message: str,
        level: EventLevel = EventLevel.INFO,
        outcome: Optional[str] = None,
        reason_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        operation_id: Optional[str] = None,
        job_id: Optional[str] = None,
        installation_id: Optional[str] = None,
        instance_id: Optional[str] = None,
        execution_plan_id: Optional[str] = None,
        inference_backend: Optional[str] = None,
        managed_service: Optional[str] = None,
        deployment_method: Optional[str] = None,
    ) -> EventEnvelope:
        """Constructs, redacts, logs, and returns an EventEnvelope."""
        raw_details = details or {}
        sanitized_details = redact_data(raw_details)

        envelope = EventEnvelope(
            level=level,
            category=category,
            event_name=event_name,
            message=redact_data(message),
            session_id=session_id or get_session_id(),
            operation_id=operation_id or get_operation_id(),
            job_id=job_id or get_job_id(),
            installation_id=installation_id or get_installation_id(),
            instance_id=instance_id or get_instance_id(),
            execution_plan_id=execution_plan_id or get_execution_plan_id(),
            inference_backend=inference_backend or get_inference_backend(),
            managed_service=managed_service or get_managed_service(),
            deployment_method=deployment_method or get_deployment_method(),
            outcome=outcome,
            reason_code=reason_code,
            details=sanitized_details,
        )

        line = envelope.model_dump_json() + "\n"
        with self._lock:
            try:
                self._rotate_if_needed()
                with open(self.events_file, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception as e:
                # Fallback to standard logging without crashing normal execution
                logging.getLogger("localpilot").error("Failed writing event log: %s", e)

        return envelope

    def record_activity(
        self,
        category: str,
        title: str,
        summary: str,
        details: Optional[Dict[str, Any]] = None,
        outcome: str = "success",
        operation_id: Optional[str] = None,
    ) -> ActivityRecord:
        """Records a user-facing activity item into the database."""
        record = ActivityRecord(
            category=category,
            title=title,
            summary=summary,
            details=redact_data(details or {}),
            operation_id=operation_id or get_operation_id(),
            outcome=outcome,
        )
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO activity_log (
                    activity_id, timestamp, category, title, summary, details_json, operation_id, outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.activity_id,
                    record.timestamp,
                    record.category,
                    record.title,
                    record.summary,
                    json.dumps(record.details),
                    record.operation_id,
                    record.outcome,
                ),
            )
        return record

    def record_audit_checkpoint(
        self,
        decision_type: str,
        target_id: str,
        payload: Dict[str, Any],
        outcome: str,
        reason_code: Optional[str] = None,
        is_critical: bool = True,
    ) -> str:
        """Persists an immutable audit decision before critical operations.

        Enforces Section 23: if a required approval/audit checkpoint cannot be saved
        before a destructive operation, blocks that operation.
        """
        import uuid
        decision_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        sanitized_payload = redact_data(payload)

        try:
            conn = self.db.get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO audit_decisions (
                        decision_id, timestamp, decision_type, target_id, payload_json, outcome, reason_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        decision_id,
                        now,
                        decision_type,
                        target_id,
                        json.dumps(sanitized_payload),
                        outcome,
                        reason_code,
                    ),
                )
            self.emit_event(
                category=EventCategory.SECURITY,
                event_name="audit.checkpoint.recorded",
                message=f"Audit checkpoint recorded for {decision_type} on {target_id}",
                outcome=outcome,
                reason_code=reason_code,
                details={"decision_id": decision_id, "decision_type": decision_type},
            )
            return decision_id
        except Exception as e:
            if is_critical:
                raise AuditCheckpointFailureError(
                    f"CRITICAL: Failed to persist required audit checkpoint for {decision_type} on {target_id}: {e}"
                ) from e
            logging.getLogger("localpilot").error("Non-critical audit checkpoint failed: %s", e)
            return ""


# Shared global hub
_global_hub: Optional[ObservabilityHub] = None


def get_hub() -> ObservabilityHub:
    global _global_hub
    if _global_hub is None:
        _global_hub = ObservabilityHub()
    return _global_hub
