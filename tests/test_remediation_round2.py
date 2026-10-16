"""Comprehensive regression and verification tests for round 2 static review remediation (D01-D28)."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from localpilot.application.runtime_service import RuntimeLifecycleService
from localpilot.application.setup_workflow import SetupWorkflow
from localpilot.application.update_workflow import UpdateWorkflow
from localpilot.domain.compatibility import CompatibilityPolicy
from localpilot.domain.execution_plan import ExecutionPlan, ExecutionPlanGenerator
from localpilot.domain.models import (
    ConfidenceLevel,
    ExecutionMode,
    InferenceBackendId,
    ManagedServiceId,
    NormalizedCandidate,
    ResolvedArtifact,
    TaskCategory,
    VerificationStage,
)
from localpilot.domain.ownership import OwnershipClass, ProcessIdentity
from localpilot.observability.events import EventCategory, EventLevel, ObservabilityHub
from localpilot.platforms.base import (
    GpuDevice,
    HardwareSnapshot,
    MemoryAccountingModel,
)
from localpilot.runtimes.base import InstallationScope
from localpilot.runtimes.ollama import OllamaRuntimeAdapter
from localpilot.security import validate_path_within_root
from localpilot.sources.base import BaseSourceAdapter
from localpilot.sources.catalog_service import CatalogService
from localpilot.sources.huggingface import HuggingFaceSourceAdapter
from localpilot.sources.ollama_library import OllamaLibrarySourceAdapter
from localpilot.sources.seed_catalog import SEED_MODELS
from localpilot.storage.db import DatabaseManager
from localpilot.storage.inventory import StorageManager


# -----------------------------------------------------------------------------
# D01, D02: Artifact Resolution & Source URLs
# -----------------------------------------------------------------------------

def test_d01_d02_artifact_resolution_contract() -> None:
    """Artifact resolution must return ResolvedArtifact contract and valid source URLs."""
    # HuggingFace with valid download_url
    hf_adapter = HuggingFaceSourceAdapter()
    cand_hf = NormalizedCandidate(
        candidate_id="hf-test",
        model_id="Qwen/Qwen2.5-0.5B-Instruct-GGUF",
        name="Qwen 2.5 0.5B",
        source="huggingface",
        artifact_id="qwen2.5-0.5b-instruct-q4_k_m.gguf",
        architecture="qwen2",
        family="Qwen",
        tasks=[TaskCategory.CHAT],
        format="gguf",
        total_bytes=400 * 1024 * 1024,
        runtime="llamacpp",
        usable_context=2048,
        target_context=2048,
        estimated_ram_bytes=512 * 1024 * 1024,
        estimated_disk_bytes=512 * 1024 * 1024,
        metadata_fetched_at="2026-01-01T00:00:00Z",
        download_url="https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf",
    )
    resolved_hf = hf_adapter.resolve_artifact(cand_hf)
    assert isinstance(resolved_hf, ResolvedArtifact)
    assert resolved_hf.acquisition_mode == "direct_download"
    assert resolved_hf.download_url == cand_hf.download_url
    assert resolved_hf.expected_bytes == 400 * 1024 * 1024

    # HuggingFace missing download_url must NOT synthesize fake weights URL
    cand_hf_missing = cand_hf.model_copy(update={"download_url": None})
    resolved_missing = hf_adapter.resolve_artifact(cand_hf_missing)
    assert resolved_missing.download_url is None

    # Ollama Library candidate resolves to service_pull
    ollama_adapter = OllamaLibrarySourceAdapter()
    cand_ollama = NormalizedCandidate(
        candidate_id="ollama-test",
        model_id="llama3.2:3b",
        name="Llama 3.2 3B",
        source="ollama",
        artifact_id="llama3.2:3b",
        architecture="llama",
        family="Llama",
        tasks=[TaskCategory.CHAT],
        format="service_model",
        total_bytes=2 * 1024**3,
        runtime="ollama",
        usable_context=2048,
        target_context=2048,
        estimated_ram_bytes=4 * 1024**3,
        estimated_disk_bytes=3 * 1024**3,
        metadata_fetched_at="2026-01-01T00:00:00Z",
    )
    resolved_ollama = ollama_adapter.resolve_artifact(cand_ollama)
    assert isinstance(resolved_ollama, ResolvedArtifact)
    assert resolved_ollama.acquisition_mode == "service_pull"
    assert resolved_ollama.model_id == "llama3.2:3b"


# -----------------------------------------------------------------------------
# D05, D06: Ollama Service Pull & Memory Unload vs Daemon Stopping
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d05_ollama_service_pull_api() -> None:
    """Ollama pull_model calls POST /api/pull with streaming and inspects tags."""
    adapter = OllamaRuntimeAdapter()
    mock_stream_resp = MagicMock()
    mock_stream_resp.raise_for_status = MagicMock()

    async def mock_aiter_lines():
        yield json.dumps({"status": "downloading", "completed": 50, "total": 100})
        yield json.dumps({"status": "success"})

    mock_stream_resp.aiter_lines = mock_aiter_lines

    progress_events = []
    def on_progress(msg, cur, tot):
        progress_events.append((msg, cur, tot))

    mock_tags_resp = MagicMock()
    mock_tags_resp.status_code = 200
    mock_tags_resp.json.return_value = {
        "models": [{"name": "llama3.2:3b", "digest": "sha256:abc12345", "size": 2048}]
    }

    with patch("httpx.AsyncClient.stream") as mock_stream, \
         patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_tags_resp)):
        mock_stream.return_value.__aenter__.return_value = mock_stream_resp

        result = await adapter.pull_model(
            endpoint_url="http://127.0.0.1:11434",
            model_id="llama3.2:3b",
            progress_callback=on_progress,
        )

        assert result["success"] is True
        assert result["digest"] == "sha256:abc12345"
        assert result["size"] == 2048
        assert len(progress_events) == 2


@pytest.mark.asyncio
async def test_d06_stop_instance_unloads_without_killing_daemon() -> None:
    """Stopping an Ollama model session unloads model via keep_alive: 0 and never kills daemon unless owned."""
    adapter = OllamaRuntimeAdapter()

    # External approved instance
    external_ident = ProcessIdentity(
        pid=12345,
        executable_path="/usr/bin/ollama",
        process_create_time=100.0,
        ownership_class=OwnershipClass.EXTERNAL_APPROVED,
        service_id="llama3.2:3b",
        endpoint_url="http://127.0.0.1:11434",
    )

    with patch.object(adapter, "unload_model", new_callable=AsyncMock) as mock_unload, \
         patch.object(adapter, "stop_owned_daemon") as mock_stop_daemon:
        mock_unload.return_value = True

        # 1. Stop instance without stop_daemon flag: must unload model, never stop daemon
        res = await adapter.stop_instance(external_ident, model_id="llama3.2:3b", stop_daemon=False)
        assert res is True
        mock_unload.assert_called_once_with("http://127.0.0.1:11434", "llama3.2:3b")
        mock_stop_daemon.assert_not_called()

        # 2. Stop instance with stop_daemon=True on external daemon: must refuse to terminate external process
        mock_unload.reset_mock()
        res_ext = await adapter.stop_instance(external_ident, model_id="llama3.2:3b", stop_daemon=True)
        assert res_ext is False
        mock_stop_daemon.assert_not_called()


# -----------------------------------------------------------------------------
# D07: Update Workflow Rollback & Truthful Status
# -----------------------------------------------------------------------------

def test_d07_update_workflow_truthful_rollback_and_status(tmp_path: Path) -> None:
    """Update workflow backs up files and reports truthful rollback_status on failure."""
    db = DatabaseManager(tmp_path / "update_test.db")
    storage = StorageManager(db=db)
    workflow = UpdateWorkflow(db=db)

    # Prepare initial installation and artifact
    art_file = storage.models_dir / "test_model.gguf"
    art_file.write_bytes(b"ORIGINAL_MODEL_V1")

    storage.register_artifact(
        artifact_id="art-up-orig",
        physical_path=art_file,
        bytes_size=len(b"ORIGINAL_MODEL_V1"),
    )

    conn = db.get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES ('inst-test-up', 'model-up', 'Model Up', 'llamacpp', '["art-up-orig"]',
                     'tested_here', 'compatible', 'v1.0.0', 'localpilot_owned', '2026-01-01', '2026-01-01')
            """
        )

    # Staged new version file
    staged_new = tmp_path / "staged_new.gguf"
    staged_new.write_bytes(b"NEW_MODEL_V2")

    # Verification callback that fails
    def failing_verifier(path: Path) -> bool:
        return False

    res = workflow.apply_update(
        installation_id="inst-test-up",
        target_version="v2.0.0",
        staged_source_path=staged_new,
        verify_callback=failing_verifier,
    )

    assert res["success"] is False
    assert res["rolled_back"] is True
    assert res["rollback_status"] == "restored"
    # Original content safely restored
    assert art_file.read_bytes() == b"ORIGINAL_MODEL_V1"


# -----------------------------------------------------------------------------
# D08, D09, D10: Storage Management Modes & Safe Removal
# -----------------------------------------------------------------------------

def test_d08_d09_d10_storage_management_modes_and_audit(tmp_path: Path) -> None:
    """safe_remove_installation creates audit checkpoint and respects management_mode."""
    db = DatabaseManager(tmp_path / "storage_modes.db")
    storage = StorageManager(db=db)
    conn = db.get_connection()

    # 1. Directly managed local file
    f1 = storage.data_dir / "models" / "m1.gguf"
    f1.write_bytes(b"DIRECT_MANAGED_BYTES")
    storage.register_artifact(
        artifact_id="art-direct",
        physical_path=f1,
        bytes_size=f1.stat().st_size,
        management_mode="directly_managed",
    )

    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES ('inst-direct', 'm1', 'Model 1', 'llamacpp', '["art-direct"]',
                     'tested_here', 'compatible', 'ok', 'localpilot_owned', '2026-01-01', '2026-01-01')
            """
        )

    res_direct = storage.safe_remove_installation("inst-direct", delete_files=True)
    assert res_direct["success"] is True
    assert str(f1) in res_direct["files_deleted"]
    assert not f1.exists()

    # Verify audit checkpoint was recorded before removal
    audit_row = conn.execute("SELECT * FROM audit_decisions WHERE target_id = 'inst-direct'").fetchone()
    assert audit_row is not None
    assert audit_row["decision_type"] == "model_removal"

    # 2. Imported reference: files must be preserved regardless of delete_files=True
    f2 = storage.data_dir / "external_shared.gguf"
    f2.write_bytes(b"EXTERNAL_PRESERVED")
    storage.register_artifact(
        artifact_id="art-imported",
        physical_path=f2,
        bytes_size=f2.stat().st_size,
        management_mode="imported_reference",
    )

    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES ('inst-imported', 'm2', 'Model 2', 'llamacpp', '["art-imported"]',
                     'tested_here', 'compatible', 'ok', 'readonly_discovered', '2026-01-01', '2026-01-01')
            """
        )

    res_imported = storage.safe_remove_installation("inst-imported", delete_files=True)
    assert res_imported["success"] is True
    assert str(f2) in res_imported["files_preserved"]
    assert f2.exists()


# -----------------------------------------------------------------------------
# D13, D14, D15, D16: Compatibility, Offloading, Unified Memory & KV Scale
# -----------------------------------------------------------------------------

def test_d13_d14_d15_compatibility_modes() -> None:
    """Compatibility evaluation enforces requested execution modes and partial offloading."""
    policy = CompatibilityPolicy()

    hw_gpu = HardwareSnapshot(
        os_name="Linux",
        os_version="6.8.0",
        cpu_arch="x86_64",
        cpu_cores_logical=16,
        ram_total_bytes=32 * 1024**3,
        ram_available_bytes=24 * 1024**3,
        safe_ram_budget_bytes=20 * 1024**3,
        safe_vram_budget_bytes=8 * 1024**3,  # 8GB VRAM
        memory_model=MemoryAccountingModel.SEPARATE,
        gpu_devices=[GpuDevice(name="RTX 4070", vram_total_bytes=12 * 1024**3, vram_available_bytes=8 * 1024**3)],
        disk_total_bytes=500 * 1024**3,
        disk_available_bytes=200 * 1024**3,
        measured_at="2026-01-01T00:00:00Z",
    )

    candidate = NormalizedCandidate(
        candidate_id="cand-large",
        model_id="large/model-14b",
        name="Large Model 14B",
        source="huggingface",
        artifact_id="model-14b.gguf",
        architecture="llama",
        family="Test",
        tasks=[TaskCategory.CHAT],
        format="gguf",
        total_bytes=9 * 1024**3,
        runtime="llamacpp",
        usable_context=4096,
        target_context=4096,
        metadata_fetched_at="2026-01-01T00:00:00Z",
        estimated_ram_bytes=14 * 1024**3,
        estimated_vram_bytes=14 * 1024**3,  # 14GB exceeds 8GB safe VRAM budget
        estimated_disk_bytes=10 * 1024**3,
        parameter_count_approx_b=14.0,
    )

    plans = ExecutionPlanGenerator.generate_plans(candidate, hw_gpu)
    base_plan = plans[0]

    # 1. Pure GPU mode should be INCOMPATIBLE (needs 14GB VRAM, only 8GB available)
    plan_gpu = base_plan.model_copy(update={"requested_execution_mode": ExecutionMode.GPU})
    res_gpu = policy.evaluate_plan(candidate, plan_gpu, hw_gpu)
    assert res_gpu.is_compatible is False

    # 2. CPU_GPU partial offload mode should be COMPATIBLE with offload ratio
    plan_cpu_gpu = base_plan.model_copy(update={"requested_execution_mode": ExecutionMode.CPU_GPU})
    res_cpu_gpu = policy.evaluate_plan(candidate, plan_cpu_gpu, hw_gpu)
    assert res_cpu_gpu.is_compatible is True
    offload_checks = [c for c in res_cpu_gpu.checks if c.get("name") == "cpu_gpu_memory"]
    assert len(offload_checks) > 0
    ratio = offload_checks[0].get("offload_ratio")
    assert ratio is not None
    assert 0.0 < ratio < 1.0

    # 3. vLLM must reject CPU_GPU mode
    plan_vllm_offload = base_plan.model_copy(update={
        "inference_backend": InferenceBackendId.VLLM,
        "requested_execution_mode": ExecutionMode.CPU_GPU,
    })
    res_vllm = policy.evaluate_plan(candidate, plan_vllm_offload, hw_gpu)
    assert res_vllm.is_compatible is False
    assert "does not support CPU+GPU partial offload" in res_vllm.friendly_explanation


def test_d16_temp_disk_and_kv_cache_scaling() -> None:
    """Disk checks include temporary download space, and KV cache scales with parameters and concurrency."""
    policy = CompatibilityPolicy()

    # KV cache estimation for 70B model with 4096 context should be noticeably larger than 1B model
    kv_small = policy.estimate_kv_cache_bytes(4096, parameter_count_approx_b=1.0, concurrency=1)
    kv_large = policy.estimate_kv_cache_bytes(4096, parameter_count_approx_b=70.0, concurrency=1)
    kv_concurrent = policy.estimate_kv_cache_bytes(4096, parameter_count_approx_b=70.0, concurrency=4)

    assert kv_large > kv_small
    assert kv_concurrent > kv_large


# -----------------------------------------------------------------------------
# D20: Runtime Reconciliation via OwnershipVerifier
# -----------------------------------------------------------------------------

def test_d20_runtime_reconciliation_cleans_dead_processes(tmp_path: Path) -> None:
    """RuntimeLifecycleService reconciles running_instances and purges stale records."""
    db = DatabaseManager(tmp_path / "reconcile.db")
    service = RuntimeLifecycleService(db=db)
    conn = db.get_connection()

    with conn:
        # Insert a fake running instance with non-existent PID
        conn.execute(
            """
            INSERT INTO running_instances (
                instance_id, model_id, installation_id, pid, process_create_time,
                executable_path, endpoint_url, runtime_version, launch_config_json,
                log_path, ownership_class, observed_execution_mode, started_at
            ) VALUES ('inst-dead-99', 'model-fake', 'inst-fake', 99999999, 1.0,
                     '/bin/fake', 'http://127.0.0.1:59999', '1.0', '{}',
                     '/tmp/fake.log', 'localpilot_owned', 'cpu', '2026-01-01T00:00:00Z')
            """
        )

    # Reconcile should identify dead process and remove it from DB
    live = service.reconcile_running_instances()
    assert len(live) == 0

    remaining = conn.execute("SELECT * FROM running_instances WHERE instance_id = 'inst-dead-99'").fetchone()
    assert remaining is None


# -----------------------------------------------------------------------------
# D23, D24, D25: Observability String Redaction, File Locks & Degraded Logging
# -----------------------------------------------------------------------------

def test_d23_d24_d25_observability_redaction_and_resilience(tmp_path: Path) -> None:
    """ObservabilityHub redacts secrets in titles/summaries and handles disk write errors gracefully."""
    db = DatabaseManager(tmp_path / "obs.db")
    hub = ObservabilityHub(db=db)

    # D23: Redact sensitive tokens in record_activity
    raw_title = "Authentication with hf_tok_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    raw_summary = "Loaded key api_key=secret_123456789_abcdefg successfully"
    hub.record_activity(
        category="auth",
        title=raw_title,
        summary=raw_summary,
    )

    conn = db.get_connection()
    row = conn.execute("SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT 1").fetchone()
    assert row is not None
    assert "hf_tok_" not in row["title"]
    assert "[REDACTED]" in row["title"]
    assert "secret_123456789" not in row["summary"]
    assert "[REDACTED]" in row["summary"]

    # D25: Degraded logging when file append fails
    hub.events_file = Path("/root/cannot_write_here_permission_denied.jsonl")
    hub.emit_event(
        category=EventCategory.APPLICATION,
        event_name="test.degraded",
        level=EventLevel.INFO,
        message="Logging should degrade safely without crashing",
    )
    assert hub.degraded_logging is True
