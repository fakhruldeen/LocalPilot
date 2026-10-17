"""Model and runtime update workflow with honest status and rollback guarantees (Section 19)."""

from __future__ import annotations
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from localpilot.application.jobs import JobManager
from localpilot.observability.events import ObservabilityHub, get_hub
from localpilot.observability.schema import EventCategory, EventLevel
from localpilot.runtimes.base import ProtectionLevel
from localpilot.storage.db import DatabaseManager, get_db, get_default_data_dir


class UpdateCheckResult(BaseModel):
    installation_id: str
    model_id: str
    current_version: str
    available_version: Optional[str] = None
    has_update: bool = False
    protection_level: ProtectionLevel = ProtectionLevel.RECOVERY_COPY_AVAILABLE
    impact_summary: str = "Update checking unavailable for this model configuration."
    status_code: str = "unknown"

    @property
    def is_update_available(self) -> bool:
        return self.has_update

    @property
    def details(self) -> str:
        return self.impact_summary


class UpdateWorkflow:
    """Manages update checks and safe atomic staging with real rollback."""

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        hub: Optional[ObservabilityHub] = None,
        job_mgr: Optional[JobManager] = None,
        data_dir: Optional[Path] = None,
    ) -> None:
        self.db = db or get_db()
        self.hub = hub or ObservabilityHub(db=self.db)
        self.job_mgr = job_mgr or JobManager(self.db, self.hub)
        self.data_dir = data_dir or get_default_data_dir()
        self.backups_dir = self.data_dir / "backups"
        self.backups_dir.mkdir(parents=True, exist_ok=True)

    def check_updates(self, installation_id: str) -> UpdateCheckResult:
        conn = self.db.get_connection()
        row = conn.execute("SELECT * FROM installations WHERE installation_id = ?", (installation_id,)).fetchone()
        if not row:
            raise ValueError(f"Installation {installation_id} not found.")

        # Determine installed identity from artifacts or plan
        artifact_ids = json.loads(row["artifact_ids_json"]) if row["artifact_ids_json"] else []
        art_sha = None
        if artifact_ids:
            art_row = conn.execute("SELECT sha256 FROM storage_artifacts WHERE artifact_id = ?", (artifact_ids[0],)).fetchone()
            if art_row:
                art_sha = art_row["sha256"]

        current_ver = art_sha or "installed-initial"

        # Honest reporting: If source/runtime does not expose live comparison, report explicitly
        runtime = row["runtime_name"]
        if runtime == "ollama":
            # Ollama update checking
            return UpdateCheckResult(
                installation_id=installation_id,
                model_id=row["model_id"],
                current_version=current_ver,
                available_version=None,
                has_update=False,
                protection_level=ProtectionLevel.RECOVERY_COPY_AVAILABLE,
                impact_summary="Ollama model updates require explicit pull.",
                status_code="service_managed",
            )

        # For static/local/llama.cpp models without active remote digest lookup
        return UpdateCheckResult(
            installation_id=installation_id,
            model_id=row["model_id"],
            current_version=current_ver,
            available_version=None,
            has_update=False,
            protection_level=ProtectionLevel.RECOVERY_COPY_AVAILABLE,
            impact_summary="Update checking is unavailable for this artifact source.",
            status_code="check_unavailable",
        )

    check_for_updates = check_updates

    def apply_update(
        self,
        installation_id: str,
        target_version: str,
        staged_source_path: Optional[Path] = None,
        verify_callback: Optional[Any] = None,
        simulate_failure_for_test: bool = False,
        approval: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Applies update with verified rollback guarantee if verification fails (D06, D07)."""
        conn = self.db.get_connection()
        inst = conn.execute("SELECT * FROM installations WHERE installation_id = ?", (installation_id,)).fetchone()
        if not inst:
            raise ValueError(f"Installation {installation_id} not found.")

        # D06: Refuse to claim success without actual update artifact
        check = self.check_updates(installation_id)
        if not staged_source_path and not simulate_failure_for_test:
            if check.status_code in ("check_unavailable", "service_managed", "UPDATE_CHECK_UNAVAILABLE", "OLLAMA_SERVICE_MANAGED") or not check.has_update:
                return {
                    "success": False,
                    "updated": False,
                    "rolled_back": False,
                    "rollback_status": "no_changes_made",
                    "status_code": check.status_code,
                    "error": check.impact_summary or "No verified update artifact available to apply.",
                    "target_version": target_version,
                    "current_version": inst["update_status"],
                }

        artifact_ids = json.loads(inst["artifact_ids_json"]) if inst["artifact_ids_json"] else []
        target_file: Optional[Path] = None
        backup_file: Optional[Path] = None

        if artifact_ids:
            art = conn.execute("SELECT physical_path FROM storage_artifacts WHERE artifact_id = ?", (artifact_ids[0],)).fetchone()
            if art and art["physical_path"]:
                target_file = Path(art["physical_path"])

        # Audit checkpoint before starting update
        audit_payload = {"target_version": target_version, "installation_id": installation_id}
        if approval:
            audit_payload["approval"] = getattr(approval, "model_dump", lambda: approval)()

        self.hub.record_audit_checkpoint(
            decision_type="model_update",
            target_id=installation_id,
            payload=audit_payload,
            outcome="approved",
            is_critical=True,
        )

        job = self.job_mgr.create_job(
            operation_type="update",
            target_id=installation_id,
            approved_plan={"to_version": target_version},
        )

        backup_created = False
        file_modified = False
        rollback_performed = False
        rollback_status = "no_changes_made"

        try:
            self.job_mgr.update_checkpoint(job.job_id, "staging_update", checkpoint_name="staged")

            # Create real recovery backup if physical file exists
            if target_file and target_file.exists():
                backup_file = self.backups_dir / f"{installation_id}_{target_file.name}.bak"
                if target_file.is_file():
                    shutil.copy2(target_file, backup_file)
                    backup_created = True

            if simulate_failure_for_test:
                # Mark as modified if testing rollback of modified file
                if target_file and target_file.exists():
                    file_modified = True
                    target_file.write_bytes(b"CORRUPT_STAGED_TEST_BYTES")
                raise RuntimeError("Verification tests failed on staged update.")

            new_sha = None
            new_size = None
            if staged_source_path:
                if not staged_source_path.exists():
                    raise FileNotFoundError(f"Staged update file not found at {staged_source_path}")
                if target_file:
                    shutil.copy2(staged_source_path, target_file)
                    file_modified = True
                    if verify_callback and not verify_callback(target_file):
                        raise RuntimeError("Staged update failed verification test.")
                    import hashlib
                    hasher = hashlib.sha256()
                    with open(target_file, "rb") as f:
                        for chunk in iter(lambda: f.read(65536), b""):
                            hasher.update(chunk)
                    new_sha = hasher.hexdigest()
                    new_size = target_file.stat().st_size

            # Apply update to installation record and canonical artifact metadata
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            with conn:
                conn.execute(
                    "UPDATE installations SET update_status = ?, updated_at = ? WHERE installation_id = ?",
                    ("up_to_date", now, installation_id),
                )
                if artifact_ids and new_size is not None and new_sha is not None:
                    conn.execute(
                        "UPDATE storage_artifacts SET bytes = ?, sha256 = ? WHERE artifact_id = ?",
                        (new_size, new_sha, artifact_ids[0]),
                    )

            # Cleanup backup copy after successful update
            if backup_file and backup_file.exists():
                backup_file.unlink()

            self.job_mgr.complete_job(job.job_id)
            self.hub.record_activity(
                category="update",
                title="Model Updated",
                summary=f"Successfully updated {inst['name']} to version {target_version}.",
            )
            return {"success": True, "updated": True, "version": target_version, "rolled_back": False, "rollback_status": "no_changes_made"}

        except Exception as e:
            # D07: Perform restoration only if file was modified and backup exists
            if file_modified:
                if backup_created and backup_file and backup_file.exists() and target_file:
                    try:
                        shutil.copy2(backup_file, target_file)
                        backup_file.unlink()
                        rollback_performed = True
                        rollback_status = "restored"
                    except Exception:
                        rollback_status = "restoration_failed"
                else:
                    rollback_status = "recovery_unavailable"
            else:
                if backup_file and backup_file.exists():
                    backup_file.unlink()
                rollback_status = "no_changes_made"

            self.job_mgr.fail_job(job.job_id, "UPDATE_VERIFICATION_FAILED", str(e))
            self.hub.record_activity(
                category="update",
                title="Update Rolled Back" if rollback_performed else "Update Failed",
                summary=(
                    f"Update failed for {inst['name']}: restored previous version safely."
                    if rollback_performed
                    else f"Update failed for {inst['name']}: {e}"
                ),
                outcome="rolled_back" if rollback_performed else "failed",
            )
            return {
                "success": False,
                "updated": False,
                "rolled_back": bool(rollback_performed and rollback_status == "restored"),
                "rollback_status": rollback_status,
                "error": str(e),
                "target_version": target_version,
                "current_version": inst["update_status"],
            }
