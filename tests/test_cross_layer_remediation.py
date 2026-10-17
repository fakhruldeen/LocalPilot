"""Cross-Layer Correctness & Integration Gates Test Suite (13 Gates).

Covers all review findings and cross-layer contracts across:
- Runtime Lifecycle (stop end-to-end, external service protection, multi-state health)
- Storage Inventory (service deletion honor, accurate byte tracking, unmanaged safety)
- Setup Workflow (approval enforcement, verification port isolation, download integrity)
- Ollama Adapter (stream error parsing, exact digest verification, unload verification)
- Update Workflow (typed check contract, rollback honesty)
- CLI & TUI presentation integration
"""

from __future__ import annotations
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from localpilot.application.runtime_service import RuntimeLifecycleService
from localpilot.application.setup_workflow import ApprovalRecord, SetupWorkflow
from localpilot.application.update_workflow import UpdateCheckResult, UpdateWorkflow
from localpilot.cli.main import app
from localpilot.domain.capabilities import InferenceBackendId, ManagedServiceId
from localpilot.domain.execution_plan import ExecutionPlan
from localpilot.domain.models import (
    ConfidenceLevel,
    ExecutionMode,
    NormalizedCandidate,
    StopResult,
    StopStatus,
    TaskCategory,
    VerificationStage,
)
from localpilot.domain.ownership import OwnershipClass, ProcessIdentity
from localpilot.runtimes.base import InstallationScope
from localpilot.runtimes.ollama import OllamaRuntimeAdapter
from localpilot.storage.db import DatabaseManager
from localpilot.storage.inventory import StorageManager


@pytest.fixture
def test_candidate() -> NormalizedCandidate:
    return NormalizedCandidate(
        candidate_id="cand-gate-model",
        model_id="test/gate-model",
        name="Gate Test Model",
        source="huggingface",
        artifact_id="gate-model.gguf",
        architecture="llama",
        family="Test",
        tasks=[TaskCategory.CHAT],
        format="gguf",
        total_bytes=10 * 1024 * 1024,
        runtime="llamacpp",
        usable_context=2048,
        target_context=2048,
        metadata_fetched_at="2026-01-01T00:00:00Z",
        estimated_ram_bytes=20 * 1024 * 1024,
        estimated_disk_bytes=15 * 1024 * 1024,
        confidence=ConfidenceLevel.VERIFIED,
        verification_stage=VerificationStage.SUPPORTED,
    )


# =============================================================================
# GATE 1 & 2: Model Stop End-to-End & External Service Protection
# =============================================================================

@pytest.mark.asyncio
async def test_gate1_and_2_stop_model_success_and_protection(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "stop_test.db")
    svc = RuntimeLifecycleService(db=db)
    conn = db.get_connection()

    # Pre-register an installation and a running Ollama model instance
    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES ('inst-ollama-1', 'llama3:8b', 'Llama 3', 'ollama', '[]',
                     'tested_here', 'compatible', 'ok', 'readonly_discovered', '2026-01-01', '2026-01-01')
            """
        )
        conn.execute(
            """
            INSERT INTO running_instances (
                instance_id, model_id, installation_id, pid, process_create_time,
                executable_path, endpoint_url, runtime_version, launch_config_json,
                log_path, ownership_class, observed_execution_mode, started_at
            ) VALUES ('run-ollama-1', 'llama3:8b', 'inst-ollama-1', 0, 0.0,
                     '/usr/bin/ollama', 'http://127.0.0.1:11434', '1.0', '{}',
                     '/tmp/ollama.log', 'readonly_discovered', 'auto', '2026-01-01T00:00:00Z')
            """
        )

    # Mock Ollama unload
    svc.ollama_adapter.unload_model = AsyncMock(return_value=True)

    result = await svc.stop_running_instance("run-ollama-1")

    assert isinstance(result, StopResult)
    assert result.request_succeeded is True
    assert result.observed_outcome == StopStatus.UNLOADED_ONLY
    # Model ID passed to unload_model
    svc.ollama_adapter.unload_model.assert_awaited_once_with("http://127.0.0.1:11434", "llama3:8b")

    # DB row deleted on success
    rem = conn.execute("SELECT * FROM running_instances WHERE instance_id = 'run-ollama-1'").fetchone()
    assert rem is None


@pytest.mark.asyncio
async def test_gate1_stop_model_failure_retains_db_row(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "stop_fail.db")
    svc = RuntimeLifecycleService(db=db)
    conn = db.get_connection()

    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES ('inst-ollama-2', 'llama3:8b', 'Llama 3', 'ollama', '[]',
                     'tested_here', 'compatible', 'ok', 'localpilot_owned', '2026-01-01', '2026-01-01')
            """
        )
        conn.execute(
            """
            INSERT INTO running_instances (
                instance_id, model_id, installation_id, pid, process_create_time,
                executable_path, endpoint_url, runtime_version, launch_config_json,
                log_path, ownership_class, observed_execution_mode, started_at
            ) VALUES ('run-ollama-2', 'llama3:8b', 'inst-ollama-2', 0, 0.0,
                     '/usr/bin/ollama', 'http://127.0.0.1:11434', '1.0', '{}',
                     '/tmp/ollama.log', 'localpilot_owned', 'auto', '2026-01-01T00:00:00Z')
            """
        )

    # Mock unload_model returning False
    svc.ollama_adapter.unload_model = AsyncMock(return_value=False)

    result = await svc.stop_running_instance("run-ollama-2")

    assert isinstance(result, StopResult)
    assert result.request_succeeded is False
    assert result.observed_outcome == StopStatus.FAILED
    assert bool(result) is False

    # Row MUST NOT be deleted from running_instances when stop failed
    rem = conn.execute("SELECT * FROM running_instances WHERE instance_id = 'run-ollama-2'").fetchone()
    assert rem is not None


# =============================================================================
# GATE 4 & 5: CLI Update Contract & Rollback Honesty
# =============================================================================

def test_gate4_update_check_result_contract(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "up_test.db")
    workflow = UpdateWorkflow(db=db)
    conn = db.get_connection()

    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES ('inst-ollama-up', 'qwen2.5:7b', 'Qwen', 'ollama', '[]',
                     'tested_here', 'compatible', 'ok', 'readonly_discovered', '2026-01-01', '2026-01-01')
            """
        )

    check = workflow.check_updates("inst-ollama-up")
    assert isinstance(check, UpdateCheckResult)
    assert check.status_code == "service_managed"
    assert check.is_update_available is False
    assert check.has_update is False
    assert "explicit pull" in check.details


def test_gate5_rollback_honesty_when_no_changes_made(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "rb_test.db")
    workflow = UpdateWorkflow(db=db)
    conn = db.get_connection()

    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES ('inst-llamacpp-up', 'qwen2.5:7b', 'Qwen', 'llamacpp', '[]',
                     'tested_here', 'compatible', 'ok', 'localpilot_owned', '2026-01-01', '2026-01-01')
            """
        )

    # Calling apply_update with unavailable remote check and no staged file
    res = workflow.apply_update("inst-llamacpp-up", target_version="v2.0.0")

    assert res["success"] is False
    assert res["updated"] is False
    # CRITICAL: Must be False because no backup was restored and no changes were made!
    assert res["rolled_back"] is False
    assert res["rollback_status"] == "no_changes_made"


# =============================================================================
# GATE 6 & 7: Service Deletion Honor & Accurate Byte Counting
# =============================================================================

@pytest.mark.asyncio
async def test_gate6_service_deletion_honors_failure(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "del_test.db")
    storage = StorageManager(db=db, data_dir=tmp_path)
    conn = db.get_connection()

    ref_file = tmp_path / "ollama_model_ref.json"
    ref_file.write_text(json.dumps({"model_id": "bad-model", "endpoint": "http://127.0.0.1:11434"}))

    art_id = storage.register_artifact(
        artifact_id="art-ollama-del",
        physical_path=ref_file,
        bytes_size=len(ref_file.read_bytes()),
        management_mode="runtime_managed",
        deletion_method="service_api_delete",
    )

    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES ('inst-ollama-del', 'bad-model', 'Bad Model', 'ollama', ?,
                     'tested_here', 'compatible', 'ok', 'localpilot_owned', '2026-01-01', '2026-01-01')
            """,
            (json.dumps([art_id]),),
        )

    # Mock Ollama delete_model returning False
    mock_adapter = MagicMock()
    mock_adapter.delete_model = AsyncMock(return_value=False)

    res = await storage.remove_installation_async("inst-ollama-del", delete_files=True, ollama_adapter=mock_adapter)

    assert res["success"] is False
    assert res["bytes_freed"] == 0
    assert len(res["files_deleted"]) == 0
    assert ref_file.exists()

    # Record preserved in database
    row = conn.execute("SELECT * FROM installations WHERE installation_id = 'inst-ollama-del'").fetchone()
    assert row is not None


# =============================================================================
# GATE 8 & 9: Ollama Pull Stream Error Handling & No Fabricated Digests
# =============================================================================

@pytest.mark.asyncio
async def test_gate8_ollama_pull_stream_error_handling(tmp_path: Path) -> None:
    adapter = OllamaRuntimeAdapter(data_dir=tmp_path)

    # Mock httpx streaming response yielding an error json line
    async def mock_aiter_lines(*args, **kwargs):
        yield json.dumps({"status": "pulling manifest"})
        yield json.dumps({"error": "pull model not found in registry"})

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_lines = mock_aiter_lines

    class MockStreamContext:
        async def __aenter__(self):
            return mock_resp
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("httpx.AsyncClient.stream", return_value=MockStreamContext()):
        with pytest.raises(RuntimeError, match="pull model not found in registry"):
            await adapter.pull_model("http://127.0.0.1:11434", "nonexistent_model")


@pytest.mark.asyncio
async def test_gate9_never_fabricate_digest_on_missing_tag(tmp_path: Path) -> None:
    adapter = OllamaRuntimeAdapter(data_dir=tmp_path)

    async def mock_aiter_lines(*args, **kwargs):
        yield json.dumps({"status": "success"})

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_lines = mock_aiter_lines

    class MockStreamContext:
        async def __aenter__(self):
            return mock_resp
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_tags_resp = MagicMock()
    mock_tags_resp.status_code = 200
    mock_tags_resp.json.return_value = {"models": [{"name": "other_model:latest", "digest": "sha256:12345"}]}

    with patch("httpx.AsyncClient.stream", return_value=MockStreamContext()), \
         patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_tags_resp)):
        with pytest.raises(RuntimeError, match="could not be verified in Ollama tags catalog"):
            await adapter.pull_model("http://127.0.0.1:11434", "my_target_model")


# =============================================================================
# GATE 10: Strict Approval Enforcement
# =============================================================================

@pytest.mark.asyncio
async def test_gate10_strict_approval_enforcement(tmp_path: Path, test_candidate: NormalizedCandidate) -> None:
    db = DatabaseManager(tmp_path / "appr_test.db")
    workflow = SetupWorkflow(db=db)
    plan = workflow.build_plan(test_candidate, scope=InstallationScope.USER)

    # 1. Reject missing approval
    with pytest.raises(PermissionError, match="Explicit user approval is required"):
        await workflow.execute_setup(test_candidate, plan, approval=None)

    # 2. Reject tampered plan hash
    fake_approval = ApprovalRecord(approved_by="user", plan_hash="tampered_hash_12345")
    with pytest.raises(PermissionError, match="Approval plan hash mismatch"):
        await workflow.execute_setup(test_candidate, plan, approval=fake_approval)

    # 3. Valid approval succeeds verification check
    valid_approval = ApprovalRecord.create_for_plan(plan)
    assert valid_approval.plan_hash == plan.compute_hash()


# =============================================================================
# GATE 11: Verification Port Isolation
# =============================================================================

@pytest.mark.asyncio
async def test_gate11_verification_port_isolation(tmp_path: Path, test_candidate: NormalizedCandidate) -> None:
    db = DatabaseManager(tmp_path / "iso_test.db")
    workflow = SetupWorkflow(db=db)
    plan = workflow.build_plan(test_candidate, scope=InstallationScope.USER)

    target_dir = Path(plan.target_install_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    real_model_path = target_dir / (test_candidate.artifact_id or f"{test_candidate.model_id}.gguf")
    real_model_path.write_bytes(b"FIXTURE_BYTES")

    # Mock prepare_runtime so test does not download binary
    workflow.llamacpp_adapter.prepare_runtime = AsyncMock(return_value="/bin/true")
    # Simulate start_instance failing to launch verification process
    workflow.llamacpp_adapter.start_instance = AsyncMock(side_effect=RuntimeError("Bind address 8080 already in use"))
    # Even if 8080 has an unrelated server returning True, we must NOT probe it
    workflow.llamacpp_adapter.test_inference = AsyncMock(return_value=True)

    approval = ApprovalRecord.create_for_plan(plan)
    with pytest.raises(RuntimeError, match="Failed to start verification instance"):
        await workflow.execute_setup(test_candidate, plan, approval=approval)

    # Must stay in DOWNLOADED_PREPARED
    conn = db.get_connection()
    row = conn.execute("SELECT readiness_stage FROM installations WHERE model_id = ?", (test_candidate.model_id,)).fetchone()
    assert row is not None
    assert row["readiness_stage"] == VerificationStage.DOWNLOADED_PREPARED.value
    # test_inference was never called against a rogue 8080 port
    workflow.llamacpp_adapter.test_inference.assert_not_called()


# =============================================================================
# GATE 12 & 13: Real Ready-to-Use Run & Reconciliation Multi-State
# =============================================================================

@pytest.mark.asyncio
async def test_gate12_prevent_duplicate_running_instances(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "dup_test.db")
    svc = RuntimeLifecycleService(db=db)
    conn = db.get_connection()

    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES ('inst-dup-1', 'm-dup', 'Dup Model', 'llamacpp', '[]',
                     'tested_here', 'compatible', 'ok', 'localpilot_owned', '2026-01-01', '2026-01-01')
            """
        )
        # Pre-seed existing running instance
        conn.execute(
            """
            INSERT INTO running_instances (
                instance_id, model_id, installation_id, pid, process_create_time,
                executable_path, endpoint_url, runtime_version, launch_config_json,
                log_path, ownership_class, observed_execution_mode, started_at
            ) VALUES ('run-dup-1', 'm-dup', 'inst-dup-1', 1, 0.0,
                     '/bin/true', 'http://127.0.0.1:8080', '1.0', '{}',
                     '/tmp/run.log', 'localpilot_owned', 'auto', '2026-01-01T00:00:00Z')
            """
        )

    with patch("localpilot.domain.ownership.OwnershipVerifier.verify_process", return_value=True):
        res = await svc.start_installed_model("inst-dup-1")
        assert res["already_running"] is True
        assert res["instance_id"] == "run-dup-1"


def test_gate13_reconciliation_multi_state_health(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "health_test.db")
    svc = RuntimeLifecycleService(db=db)
    conn = db.get_connection()

    with conn:
        conn.execute(
            """
            INSERT INTO running_instances (
                instance_id, model_id, installation_id, pid, process_create_time,
                executable_path, endpoint_url, runtime_version, launch_config_json,
                log_path, ownership_class, observed_execution_mode, started_at
            ) VALUES ('run-health-1', 'm-health', 'inst-1', 1, 0.0,
                     '/bin/true', 'http://127.0.0.1:9999', '1.0', '{}',
                     '/tmp/run.log', 'localpilot_owned', 'auto', '2026-01-01T00:00:00Z')
            """
        )

    # Process is alive in OS, but endpoint probe times out / fails
    with patch("localpilot.domain.ownership.OwnershipVerifier.verify_process", return_value=True), \
         patch("httpx.Client.get", side_effect=httpx.ConnectError("Connection refused")):
        active = svc.reconcile_running_instances()
        assert len(active) == 1
        assert active[0]["health_status"] == "unresponsive"
        # The instance was NOT deleted from running_instances
        row = conn.execute("SELECT * FROM running_instances WHERE instance_id = 'run-health-1'").fetchone()
        assert row is not None


# =============================================================================
# GATE 3: CLI Stop & Update Command Output Integration
# =============================================================================

def test_gate3_cli_stop_and_update_commands(tmp_path: Path) -> None:
    from typer.testing import CliRunner
    runner = CliRunner()

    db = DatabaseManager(tmp_path / "cli_gates.db")
    with patch("localpilot.cli.main.get_db", return_value=db), \
         patch("localpilot.application.runtime_service.get_db", return_value=db), \
         patch("localpilot.application.update_workflow.get_db", return_value=db):

        # Test stop command on non-existent instance
        res_stop = runner.invoke(app, ["stop", "inst-nonexistent"])
        assert res_stop.exit_code == 0
        assert "not active or already stopped" in res_stop.stdout

        # Test update command on non-existent installation
        res_up = runner.invoke(app, ["update", "inst-nonexistent"])
        assert res_up.exit_code == 0
        assert "not found" in res_up.stdout


# =============================================================================
# GATE 7: Accurate Byte Counting on Disk
# =============================================================================

@pytest.mark.asyncio
async def test_gate7_accurate_byte_counting_on_disk(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "bytes_test.db")
    storage = StorageManager(db=db, data_dir=tmp_path)
    conn = db.get_connection()

    model_file = tmp_path / "test_model_123.gguf"
    model_file.write_bytes(b"A" * 123456)  # Exactly 123456 bytes

    art_id = storage.register_artifact(
        artifact_id="art-bytes-1",
        physical_path=model_file,
        bytes_size=123456,
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
            ) VALUES ('inst-bytes-1', 'm-bytes', 'Bytes Model', 'llamacpp', ?,
                     'tested_here', 'compatible', 'ok', 'localpilot_owned', '2026-01-01', '2026-01-01')
            """,
            (json.dumps([art_id]),),
        )

    res = await storage.remove_installation_async("inst-bytes-1", delete_files=True)
    assert res["success"] is True
    # Exactly matches bytes freed
    assert res["bytes_freed"] == 123456
    assert not model_file.exists()

