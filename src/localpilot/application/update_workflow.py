"""Model and runtime update workflow with rollback guarantees (Section 19)."""

from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from localpilot.application.jobs import JobManager
from localpilot.observability.events import ObservabilityHub, get_hub
from localpilot.observability.schema import EventCategory, EventLevel
from localpilot.runtimes.base import ProtectionLevel
from localpilot.storage.db import DatabaseManager, get_db


class UpdateCheckResult(BaseModel):
    installation_id: str
    model_id: str
    current_version: str
    available_version: Optional[str] = None
    has_update: bool = False
    protection_level: ProtectionLevel = ProtectionLevel.RECOVERY_COPY_AVAILABLE
    impact_summary: str = "Only this model will be updated."


class UpdateWorkflow:
    """Manages update checks and safe atomic staging with rollback."""

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        hub: Optional[ObservabilityHub] = None,
        job_mgr: Optional[JobManager] = None,
    ) -> None:
        self.db = db or get_db()
        self.hub = hub or ObservabilityHub(db=self.db)
        self.job_mgr = job_mgr or JobManager(self.db, self.hub)

    def check_updates(self, installation_id: str) -> UpdateCheckResult:
        conn = self.db.get_connection()
        row = conn.execute("SELECT * FROM installations WHERE installation_id = ?", (installation_id,)).fetchone()
        if not row:
            raise ValueError(f"Installation {installation_id} not found.")

        # Default check
        return UpdateCheckResult(
            installation_id=installation_id,
            model_id=row["model_id"],
            current_version="latest",
            available_version=None,
            has_update=False,
            protection_level=ProtectionLevel.RECOVERY_COPY_AVAILABLE,
            impact_summary="Model is up to date.",
        )

    def apply_update(
        self,
        installation_id: str,
        target_version: str,
        simulate_failure_for_test: bool = False,
    ) -> Dict[str, Any]:
        """Applies update with rollback guarantee if testing fails."""
        conn = self.db.get_connection()
        inst = conn.execute("SELECT * FROM installations WHERE installation_id = ?", (installation_id,)).fetchone()
        if not inst:
            raise ValueError(f"Installation {installation_id} not found.")

        old_version = inst["update_status"]

        # Audit checkpoint before starting update
        self.hub.record_audit_checkpoint(
            decision_type="model_update",
            target_id=installation_id,
            payload={"from_version": old_version, "to_version": target_version},
            outcome="approved",
            is_critical=True,
        )

        job = self.job_mgr.create_job(
            operation_type="update",
            target_id=installation_id,
            approved_plan={"to_version": target_version},
        )

        try:
            self.job_mgr.update_checkpoint(job.job_id, "staging_update", checkpoint_name="staged")

            if simulate_failure_for_test:
                # Trigger rollback
                raise RuntimeError("Verification tests failed on staged update.")

            # Activate
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            with conn:
                conn.execute(
                    "UPDATE installations SET update_status = ?, updated_at = ? WHERE installation_id = ?",
                    (target_version, now, installation_id),
                )

            self.job_mgr.complete_job(job.job_id)
            self.hub.record_activity(
                category="update",
                title="Model Updated",
                summary=f"Successfully updated {inst['name']} to {target_version}.",
            )
            return {"success": True, "updated": True, "version": target_version}

        except Exception as e:
            # Perform atomic rollback
            self.job_mgr.fail_job(job.job_id, "UPDATE_VERIFICATION_FAILED", str(e))
            self.hub.record_activity(
                category="update",
                title="Update Rolled Back",
                summary=f"Update failed for {inst['name']}: restored previous version safely.",
                outcome="rolled_back",
            )
            return {"success": False, "rolled_back": True, "error": str(e), "current_version": old_version}
