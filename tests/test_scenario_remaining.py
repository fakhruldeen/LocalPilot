"""Regression tests reproducing confirmed defects across remaining layers."""

from __future__ import annotations
import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from localpilot.application.runtime_service import RuntimeLifecycleService
from localpilot.application.setup_workflow import ApprovalRecord, SetupPlan, SetupWorkflow
from localpilot.application.update_workflow import UpdateWorkflow
from localpilot.cli.main import discover_models, run_doctor, list_installed, list_running, show_storage
from localpilot.domain.capabilities import InferenceBackendId, ManagedServiceId
from localpilot.domain.execution_plan import ExecutionPlan
from localpilot.domain.models import (
    ConfidenceLevel,
    ExecutionMode,
    NormalizedCandidate,
    ResolvedArtifact,
    TaskCategory,
    VerificationStage,
)
from localpilot.domain.ownership import OwnershipClass, ProcessIdentity
from localpilot.runtimes.base import InstallationScope
from localpilot.storage.db import DatabaseManager
from localpilot.storage.inventory import StorageManager


# -----------------------------------------------------------------------------
# 1. Defect: start_installed_model crashes on rep.hardware.memory
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_installed_model_memory_attribute(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "test_start.db")
    svc = RuntimeLifecycleService(db=db)
    conn = db.get_connection()

    dummy_model = tmp_path / "start_model.gguf"
    dummy_model.write_bytes(b"MODEL_BYTES")

    art_id = svc.db.db_path.parent  # just unique string
    with conn:
        conn.execute(
            """
            INSERT INTO storage_artifacts (
                artifact_id, physical_path, bytes, sha256, is_managed, install_scope,
                management_mode, deletion_method, ref_count, created_at
            ) VALUES ('art-start', ?, 11, 'sha', 1, 'isolated', 'directly_managed', 'delete_files', 1, '2026-01-01')
            """,
            (str(dummy_model),),
        )
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES ('inst-start', 'model/test', 'Test Model', 'llamacpp', ?,
                     'tested_here', 'compatible', 'ok', 'localpilot_owned', '2026-01-01', '2026-01-01')
            """,
            (json.dumps(["art-start"]),),
        )

    # Mock adapter start_instance and probe so it doesn't spawn real subprocess
    proc_id = ProcessIdentity(
        pid=1234,
        process_create_time=100.0,
        executable_path="/bin/fake",
        endpoint_url="http://127.0.0.1:8080",
        ownership_class=OwnershipClass.LOCALPILOT_OWNED,
    )
    svc.llamacpp_adapter.start_instance = AsyncMock(return_value=proc_id)

    mock_resp = MagicMock(status_code=200)
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)):
        res = await svc.start_installed_model("inst-start")
        assert res["model_id"] == "model/test"
        assert res["pid"] == 1234
        assert res["already_running"] is False


# -----------------------------------------------------------------------------
# 2. Defect: Failed filesystem unlink drops DB records and reports success
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_fs_deletion_preserves_records(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "test_del_fail.db")
    storage = StorageManager(db=db, data_dir=tmp_path)
    conn = db.get_connection()

    model_file = tmp_path / "models" / "undeletable.gguf"
    model_file.parent.mkdir(parents=True, exist_ok=True)
    model_file.write_bytes(b"LOCKED_BYTES")

    art_id = storage.register_artifact(
        "art-locked",
        model_file,
        len(b"LOCKED_BYTES"),
        management_mode="directly_managed",
        deletion_method="delete_files",
    )
    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES ('inst-locked', 'm-locked', 'Locked Model', 'llamacpp', ?,
                     'tested_here', 'compatible', 'ok', 'localpilot_owned', '2026-01-01', '2026-01-01')
            """,
            (json.dumps([art_id]),),
        )

    # Patch unlink to simulate PermissionError
    with patch.object(Path, "unlink", side_effect=PermissionError("Permission denied")):
        res = await storage.remove_installation_async("inst-locked", delete_files=True)
        assert res["success"] is False
        assert str(model_file) in res["files_preserved"]
        assert len(res["files_deleted"]) == 0

        # Installation and storage artifact records MUST still exist in database
        inst_row = conn.execute("SELECT * FROM installations WHERE installation_id = 'inst-locked'").fetchone()
        assert inst_row is not None
        art_row = conn.execute("SELECT * FROM storage_artifacts WHERE artifact_id = ?", (art_id,)).fetchone()
        assert art_row is not None


# -----------------------------------------------------------------------------
# 3. Defect: Update activation leaves stale storage_artifacts metadata
# -----------------------------------------------------------------------------

def test_update_syncs_storage_artifacts(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "test_up_sync.db")
    storage = StorageManager(db=db, data_dir=tmp_path)
    conn = db.get_connection()

    old_file = tmp_path / "models" / "up_model.gguf"
    old_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.write_bytes(b"OLD_CONTENT")
    old_sha = hashlib.sha256(b"OLD_CONTENT").hexdigest()

    art_id = storage.register_artifact("art-up", old_file, len(b"OLD_CONTENT"), sha256=old_sha)
    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES ('inst-up', 'm-up', 'Up Model', 'llamacpp', ?,
                     'tested_here', 'compatible', 'v1.0', 'localpilot_owned', '2026-01-01', '2026-01-01')
            """,
            (json.dumps([art_id]),),
        )

    new_staged = tmp_path / "staged.gguf"
    new_bytes = b"NEW_ACTIVATED_CONTENT_FOR_V2"
    new_staged.write_bytes(new_bytes)
    new_sha = hashlib.sha256(new_bytes).hexdigest()

    workflow = UpdateWorkflow(db=db, data_dir=tmp_path)
    res = workflow.apply_update("inst-up", target_version="v2.0", staged_source_path=new_staged)

    assert res["success"] is True
    assert res["updated"] is True

    # storage_artifacts MUST be updated with new bytes and new sha256
    art_row = conn.execute("SELECT * FROM storage_artifacts WHERE artifact_id = ?", (art_id,)).fetchone()
    assert art_row["bytes"] == len(new_bytes)
    assert art_row["sha256"] == new_sha


# -----------------------------------------------------------------------------
# 4. Defect: Direct invocation of Typer CLI command functions
# -----------------------------------------------------------------------------

def test_cli_direct_invocation_options(capsys) -> None:
    # None of these should raise AttributeError: 'OptionInfo' object has no attribute 'lower'
    # Nor should they treat OptionInfo as a truthy boolean
    try:
        discover_models()
    except Exception as e:
        pytest.fail(f"discover_models() direct invocation failed: {e}")

    try:
        list_installed()
    except Exception as e:
        pytest.fail(f"list_installed() direct invocation failed: {e}")

    try:
        list_running()
    except Exception as e:
        pytest.fail(f"list_running() direct invocation failed: {e}")

    try:
        show_storage()
    except Exception as e:
        pytest.fail(f"show_storage() direct invocation failed: {e}")


# -----------------------------------------------------------------------------
# 5. Defect: Truncated pre-existing file without SHA256 accepted as valid
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_setup_rejects_truncated_existing_file(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "test_trunc.db")
    workflow = SetupWorkflow(db=db, data_dir=tmp_path)

    cand = NormalizedCandidate(
        candidate_id="cand-trunc",
        model_id="test/trunc-model",
        name="Trunc Model",
        source="huggingface",
        artifact_id="trunc.gguf",
        architecture="llama",
        family="Test",
        tasks=[TaskCategory.CHAT],
        format="gguf",
        total_bytes=1000,
        expected_bytes=1000,
        sha256=None,  # No sha256 provided
        runtime="llamacpp",
        usable_context=2048,
        target_context=2048,
        metadata_fetched_at="2026-01-01T00:00:00Z",
        estimated_ram_bytes=2000,
        estimated_disk_bytes=1000,
        confidence=ConfidenceLevel.VERIFIED,
        verification_stage=VerificationStage.SUPPORTED,
    )

    plan = workflow.build_plan(cand, scope=InstallationScope.ISOLATED)
    target_dir = Path(plan.target_install_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "trunc.gguf"
    # Pre-stage a truncated file of only 50 bytes (expected 1000)
    target_file.write_bytes(b"X" * 50)

    workflow.llamacpp_adapter.prepare_runtime = AsyncMock(return_value="/bin/true")
    workflow.llamacpp_adapter.start_instance = AsyncMock(return_value=ProcessIdentity(pid=1, endpoint_url="http://127.0.0.1:8080"))
    workflow.llamacpp_adapter.test_inference = AsyncMock(return_value=True)

    approval = ApprovalRecord.create_for_plan(plan)

    # Since no download_url exists, if it correctly rejects the 50-byte file,
    # it must raise RuntimeError that file not found / no URL, NOT reuse the truncated file!
    with pytest.raises(RuntimeError, match="not found at .* and no downloadable source URL is available"):
        await workflow.execute_setup(cand, plan, approval=approval)


# -----------------------------------------------------------------------------
# 6. Defect: ApprovalRecord with plan_hash=None bypasses validation
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approval_enforces_mandatory_plan_hash(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "test_appr_hash.db")
    workflow = SetupWorkflow(db=db, data_dir=tmp_path)

    cand = NormalizedCandidate(
        candidate_id="cand-appr",
        model_id="test/appr-model",
        name="Appr Model",
        source="huggingface",
        artifact_id="appr.gguf",
        architecture="llama",
        family="Test",
        tasks=[TaskCategory.CHAT],
        format="gguf",
        total_bytes=10,
        runtime="llamacpp",
        usable_context=2048,
        target_context=2048,
        metadata_fetched_at="2026-01-01T00:00:00Z",
        estimated_ram_bytes=20,
        estimated_disk_bytes=10,
        confidence=ConfidenceLevel.VERIFIED,
        verification_stage=VerificationStage.SUPPORTED,
    )
    plan = workflow.build_plan(cand, scope=InstallationScope.ISOLATED)

    # Approval with plan_hash=None must be rejected
    invalid_approval = ApprovalRecord(approved_by="user", plan_hash=None)
    with pytest.raises(PermissionError, match="Approval must be bound to the exact material plan"):
        await workflow.execute_setup(cand, plan, approval=invalid_approval)
