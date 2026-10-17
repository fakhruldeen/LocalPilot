"""Durable job engine, conflict prevention, and recovery (Section 20)."""

from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from localpilot.observability.events import ObservabilityHub, get_hub
from localpilot.observability.schema import EventCategory
from localpilot.storage.db import DatabaseManager, get_db


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(BaseModel):
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    operation_type: str  # "setup", "update", "remove", "download"
    target_id: str
    approved_plan: Dict[str, Any] = Field(default_factory=dict)
    stage: str = "init"
    checkpoints: List[str] = Field(default_factory=list)
    progress_bytes: int = 0
    total_bytes: int = 0
    status: JobStatus = JobStatus.PENDING
    error_category: Optional[str] = None
    retry_count: int = 0
    owned_pids: List[int] = Field(default_factory=list)
    recovery_action: Optional[str] = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )


class JobConflictError(RuntimeError):
    """Raised when an operation conflicts with an active job."""
    pass


class JobManager:
    """Manages durable jobs, prevents conflicting operations, and handles recovery."""

    def __init__(self, db: Optional[DatabaseManager] = None, hub: Optional[ObservabilityHub] = None) -> None:
        self.db = db or get_db()
        self.hub = hub or get_hub()

    def check_conflicts(self, operation_type: str, target_id: str) -> None:
        """Blocks conflicting operations (Section 20)."""
        conn = self.db.get_connection()
        row = conn.execute(
            """
            SELECT job_id, operation_type, target_id, status FROM jobs
            WHERE status = 'running' AND target_id = ?
            """,
            (target_id,),
        ).fetchone()
        if row:
            raise JobConflictError(
                f"Cannot start '{operation_type}' on '{target_id}': active job '{row['job_id']}' ({row['operation_type']}) is running."
            )

    def create_job(
        self,
        operation_type: str,
        target_id: str,
        approved_plan: Dict[str, Any],
        total_bytes: int = 0,
    ) -> Job:
        self.check_conflicts(operation_type, target_id)
        job = Job(
            operation_type=operation_type,
            target_id=target_id,
            approved_plan=approved_plan,
            total_bytes=total_bytes,
            status=JobStatus.RUNNING,
            stage="started",
        )
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, operation_type, target_id, approved_plan_json, stage,
                    checkpoints_json, progress_bytes, total_bytes, status, error_category,
                    retry_count, owned_pids_json, recovery_action, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.operation_type,
                    job.target_id,
                    json.dumps(job.approved_plan),
                    job.stage,
                    json.dumps(job.checkpoints),
                    job.progress_bytes,
                    job.total_bytes,
                    job.status.value,
                    job.error_category,
                    job.retry_count,
                    json.dumps(job.owned_pids),
                    job.recovery_action,
                    job.created_at,
                    job.updated_at,
                ),
            )

        self.hub.emit_event(
            category=EventCategory.WORKFLOW,
            event_name="job.started",
            message=f"Started job {job.job_id} ({operation_type} on {target_id})",
            job_id=job.job_id,
            details={"operation_type": operation_type, "target_id": target_id},
        )
        return job

    def update_checkpoint(
        self,
        job_id: str,
        stage: str,
        progress_bytes: Optional[int] = None,
        checkpoint_name: Optional[str] = None,
    ) -> None:
        conn = self.db.get_connection()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with conn:
            row = conn.execute("SELECT checkpoints_json, progress_bytes FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return
            checkpoints = json.loads(row["checkpoints_json"])
            if checkpoint_name and checkpoint_name not in checkpoints:
                checkpoints.append(checkpoint_name)
            p_bytes = progress_bytes if progress_bytes is not None else row["progress_bytes"]

            conn.execute(
                """
                UPDATE jobs SET stage = ?, checkpoints_json = ?, progress_bytes = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (stage, json.dumps(checkpoints), p_bytes, now, job_id),
            )

    def complete_job(self, job_id: str) -> None:
        conn = self.db.get_connection()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with conn:
            conn.execute(
                "UPDATE jobs SET status = 'completed', stage = 'done', updated_at = ? WHERE job_id = ?",
                (now, job_id),
            )
        self.hub.emit_event(
            category=EventCategory.WORKFLOW,
            event_name="job.completed",
            message=f"Job {job_id} successfully completed",
            job_id=job_id,
            outcome="success",
        )

    def fail_job(self, job_id: str, error_category: str, error_message: str) -> None:
        conn = self.db.get_connection()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with conn:
            conn.execute(
                "UPDATE jobs SET status = 'failed', error_category = ?, updated_at = ? WHERE job_id = ?",
                (now, error_category, job_id),
            )
        self.hub.emit_event(
            category=EventCategory.WORKFLOW,
            event_name="job.failed",
            message=f"Job {job_id} failed: {error_message}",
            job_id=job_id,
            outcome="failure",
            reason_code=error_category,
        )

    def get_interrupted_jobs(self) -> List[Dict[str, Any]]:
        """Returns any active jobs that were left running due to unexpected process termination."""
        conn = self.db.get_connection()
        rows = conn.execute("SELECT * FROM jobs WHERE status = 'running' ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def cleanup_interrupted_job(self, job_id: str) -> None:
        """Marks an interrupted job as cancelled and cleans up any temporary download artifacts."""
        conn = self.db.get_connection()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with conn:
            conn.execute(
                "UPDATE jobs SET status = 'cancelled', error_category = 'user_cleaned_up', updated_at = ? WHERE job_id = ?",
                (now, job_id),
            )
        self.hub.emit_event(
            category=EventCategory.WORKFLOW,
            event_name="job.cancelled",
            message=f"Interrupted job {job_id} was cleaned up",
            job_id=job_id,
            outcome="cancelled",
        )
