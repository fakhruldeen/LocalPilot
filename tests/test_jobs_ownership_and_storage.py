"""Tests for durable jobs, conflict locking, process ownership, and shared storage removal."""

import os
from pathlib import Path
import pytest

from localpilot.application.jobs import JobConflictError, JobManager, JobStatus
from localpilot.domain.ownership import OwnershipClass, OwnershipVerifier, ProcessIdentity
from localpilot.storage.db import DatabaseManager
from localpilot.storage.inventory import StorageManager


def test_job_conflict_prevention(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "state.db")
    mgr = JobManager(db=db)

    # Create active job
    job = mgr.create_job(operation_type="setup", target_id="model-a", approved_plan={"step": 1})
    assert job.status == JobStatus.RUNNING

    # Attempt conflicting concurrent operation on same target
    with pytest.raises(JobConflictError):
        mgr.create_job(operation_type="remove", target_id="model-a", approved_plan={})

    # Complete first job
    mgr.complete_job(job.job_id)

    # Now another job on model-a is allowed
    job2 = mgr.create_job(operation_type="update", target_id="model-a", approved_plan={})
    assert job2.status == JobStatus.RUNNING


def test_process_ownership_verifier() -> None:
    current_pid = os.getpid()
    identity = ProcessIdentity(
        pid=current_pid,
        process_create_time=0.0,  # Invalid create time
        executable_path="/usr/bin/fake-exe",
        ownership_class=OwnershipClass.LOCALPILOT_OWNED,
    )
    # Fails because process creation time does not match
    assert OwnershipVerifier.verify_process(identity) is False


def test_storage_reference_counted_protection(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "state.db")
    storage = StorageManager(db=db)

    # Create dummy artifact file
    dummy_model = tmp_path / "shared_model.gguf"
    dummy_model.write_bytes(b"dummy content" * 100)

    art_id = "art-shared-1"
    # Register artifact
    storage.register_artifact(
        artifact_id=art_id,
        physical_path=dummy_model,
        bytes_size=dummy_model.stat().st_size,
    )

    # Create two installations referencing the same artifact
    conn = db.get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES ('inst-1', 'model-1', 'Model 1', 'llamacpp', '["art-shared-1"]', 'tested_here', 'compatible', 'ok', 'localpilot_owned', '2026-01-01', '2026-01-01')
            """
        )
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES ('inst-2', 'model-2', 'Model 2', 'llamacpp', '["art-shared-1"]', 'tested_here', 'compatible', 'ok', 'localpilot_owned', '2026-01-01', '2026-01-01')
            """
        )
        # Increase ref_count to 2
        conn.execute("UPDATE storage_artifacts SET ref_count = 2 WHERE artifact_id = 'art-shared-1'")

    # Remove inst-1: shared file MUST be preserved!
    res1 = storage.safe_remove_installation("inst-1", delete_files=True)
    assert res1["success"] is True
    assert str(dummy_model) in res1["files_preserved"]
    assert dummy_model.exists() is True  # File still safe on disk!

    # Now remove inst-2: last reference gone -> file is safely deleted
    res2 = storage.safe_remove_installation("inst-2", delete_files=True)
    assert res2["success"] is True
    assert str(dummy_model) in res2["files_deleted"]
    assert dummy_model.exists() is False  # Now safely cleaned up!
