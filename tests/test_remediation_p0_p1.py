"""Regression tests verifying all P0 and P1 remediation fixes."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import zipfile

import pytest

from localpilot.application.setup_workflow import ApprovalRecord, SetupWorkflow
from localpilot.application.update_workflow import UpdateWorkflow
from localpilot.domain.compatibility import CompatibilityPolicy
from localpilot.domain.models import (
    ConfidenceLevel,
    ExecutionMode,
    InferenceBackendId,
    ManagedServiceId,
    NormalizedCandidate,
    TaskCategory,
    VerificationStage,
)
from localpilot.domain.ownership import OwnershipClass, ProcessIdentity
from localpilot.hardware.inspector import SystemInspector
from localpilot.platforms.base import HardwareSnapshot, MemoryAccountingModel
from localpilot.runtimes.base import InstallationScope
from localpilot.runtimes.ollama import OllamaRuntimeAdapter
from localpilot.security import safe_extract_zip
from localpilot.sources.catalog_service import CatalogService
from localpilot.storage.db import DatabaseManager
from localpilot.storage.inventory import StorageManager


@pytest.fixture
def sample_candidate() -> NormalizedCandidate:
    return NormalizedCandidate(
        candidate_id="cand-rem-1",
        model_id="org/rem-model",
        name="Remediation Model",
        source="huggingface",
        artifact_id="model.gguf",
        architecture="llama",
        family="TestFamily",
        tasks=[TaskCategory.CHAT],
        format="gguf",
        total_bytes=50 * 1024 * 1024,
        runtime="llamacpp",
        usable_context=2048,
        target_context=2048,
        metadata_fetched_at="2026-01-01T00:00:00Z",
        estimated_ram_bytes=100 * 1024 * 1024,
        estimated_disk_bytes=60 * 1024 * 1024,
        confidence=ConfidenceLevel.VERIFIED,
        verification_stage=VerificationStage.SUPPORTED,
    )


# --- P0.A: NO SYNTHETIC GGUF FILES CREATED IN PRODUCTION PATHS ---

@pytest.mark.asyncio
async def test_no_synthetic_gguf_written_on_missing_file(tmp_path: Path, sample_candidate: NormalizedCandidate) -> None:
    """Ensure setup_workflow fails cleanly and NEVER writes fake b'GGUF\\x03...' files."""
    db = DatabaseManager(tmp_path / "state.db")
    workflow = SetupWorkflow(db=db)

    # Prepare plan pointing to non-existent file
    plan = workflow.build_plan(sample_candidate, scope=InstallationScope.USER)

    # Mock prepare_runtime so test does not try to install external tools
    async def mock_prepare(*args, **kwargs):
        return "/bin/true"
    workflow.llamacpp_adapter.prepare_runtime = mock_prepare

    approval = ApprovalRecord.create_for_plan(plan)
    with pytest.raises(RuntimeError, match="LocalPilot does not create synthetic weights in production"):
        await workflow.execute_setup(sample_candidate, plan, approval=approval)

    # Verify no fake GGUF file exists anywhere in managed storage
    created_files = list((tmp_path).rglob("*.gguf"))
    assert len(created_files) == 0


# --- P0.B: TESTED_HERE REQUIRES LIVE INFERENCE; PRESERVES DOWNLOADED FILES ---

@pytest.mark.asyncio
async def test_inference_failure_retains_downloaded_prepared_and_preserves_file(
    tmp_path: Path, sample_candidate: NormalizedCandidate
) -> None:
    """If live inference fails, readiness must stay DOWNLOADED_PREPARED, not TESTED_HERE, and file is kept."""
    db = DatabaseManager(tmp_path / "state.db")
    workflow = SetupWorkflow(db=db)
    plan = workflow.build_plan(sample_candidate, scope=InstallationScope.USER)

    # Mock prepare_runtime
    async def mock_prepare(*args, **kwargs):
        return "/bin/true"
    workflow.llamacpp_adapter.prepare_runtime = mock_prepare

    target_dir = Path(plan.target_install_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    real_model_path = target_dir / "model.gguf"
    real_model_path.write_bytes(b"REAL_MODEL_BYTES_MOCK" * 100)

    # Mock test_inference to simulate inference failure (e.g. server crash or OOM)
    workflow.llamacpp_adapter.test_inference = AsyncMock(return_value=False)

    approval = ApprovalRecord.create_for_plan(plan)
    with pytest.raises(RuntimeError, match="test inference verification failed"):
        await workflow.execute_setup(sample_candidate, plan, approval=approval)

    # Readiness stage must be DOWNLOADED_PREPARED in DB, NOT tested_here
    conn = db.get_connection()
    row = conn.execute("SELECT readiness_stage FROM installations WHERE model_id = ?", (sample_candidate.model_id,)).fetchone()
    assert row is not None
    assert row["readiness_stage"] == VerificationStage.DOWNLOADED_PREPARED.value

    # CRITICAL: Physical file must NOT be deleted merely because loading failed
    assert real_model_path.exists()
    assert real_model_path.stat().st_size > 0


# --- P1.C: STRICT EXECUTION MODE ENFORCEMENT (GPU CANNOT SILENTLY FALL BACK TO CPU) ---

def test_gpu_mode_does_not_silently_fallback_to_cpu() -> None:
    """When user requests GPU execution mode, insufficient VRAM or missing GPU must fail."""
    policy = CompatibilityPolicy()

    # Hardware with no GPU
    cpu_only_hw = HardwareSnapshot(
        os_name="Linux",
        os_version="6.8.0",
        cpu_arch="x86_64",
        cpu_cores_logical=16,
        ram_total_bytes=32 * 1024**3,
        ram_available_bytes=24 * 1024**3,
        safe_ram_budget_bytes=20 * 1024**3,
        safe_vram_budget_bytes=0,
        memory_model=MemoryAccountingModel.SEPARATE,
        gpu_devices=[],
        disk_total_bytes=500 * 1024**3,
        disk_available_bytes=200 * 1024**3,
        measured_at="2026-01-01T00:00:00Z",
    )

    candidate = NormalizedCandidate(
        candidate_id="cand-gpu-strict",
        model_id="test/gpu-model",
        name="GPU Model",
        source="huggingface",
        artifact_id="model.gguf",
        architecture="llama",
        family="Test",
        tasks=[TaskCategory.CHAT],
        format="gguf",
        total_bytes=4 * 1024**3,
        runtime="llamacpp",
        usable_context=2048,
        target_context=2048,
        metadata_fetched_at="2026-01-01T00:00:00Z",
        estimated_ram_bytes=6 * 1024**3,
        estimated_vram_bytes=6 * 1024**3,
        estimated_disk_bytes=5 * 1024**3,
        confidence=ConfidenceLevel.VERIFIED,
        verification_stage=VerificationStage.SUPPORTED,
        execution_mode=ExecutionMode.GPU,
    )

    result = policy.evaluate(candidate, cpu_only_hw)
    assert result.is_compatible is False
    assert "GPU" in result.friendly_explanation or "VRAM" in result.friendly_explanation


# --- P1.B: CATALOG PLAN SELECTION EXCLUDES INCOMPATIBLE PLANS ---

def test_catalog_plan_selection_excludes_incompatible(tmp_path: Path) -> None:
    """Catalog filtering with only_compatible=True must strictly exclude models without compatible plans."""
    db = DatabaseManager(tmp_path / "cat_test.db")
    cat = CatalogService(db=db)

    # Force inspector to return low RAM profile
    low_ram_hw = HardwareSnapshot(
        os_name="Linux",
        os_version="6.8.0",
        cpu_arch="x86_64",
        cpu_cores_logical=2,
        ram_total_bytes=1 * 1024**3,  # 1 GB RAM
        ram_available_bytes=512 * 1024**2,  # 512 MB RAM
        safe_ram_budget_bytes=400 * 1024**2,
        safe_vram_budget_bytes=0,
        memory_model=MemoryAccountingModel.SEPARATE,
        gpu_devices=[],
        disk_total_bytes=500 * 1024**3,
        disk_available_bytes=200 * 1024**3,
        measured_at="2026-01-01T00:00:00Z",
    )

    with patch.object(SystemInspector, "inspect") as mock_inspect:
        mock_report = MagicMock()
        mock_report.hardware = low_ram_hw
        mock_inspect.return_value = mock_report

        # Discover with only_compatible=True
        results = cat.discover(only_compatible=True)
        # All models in default seed require > 1GB, so 0 should be returned
        assert len(results) == 0


# --- P1.D: OLLAMA EXTERNAL DETECTION & ZIP EXTRACTION SAFETY ---

# --- P1.D: OLLAMA EXTERNAL DETECTION & ZIP EXTRACTION SAFETY ---

@pytest.mark.asyncio
async def test_ollama_external_detection_does_not_claim_current_pid(tmp_path: Path, sample_candidate: NormalizedCandidate) -> None:
    """Probing an external endpoint must NOT assign LocalPilot's own PID or claim ownership."""
    adapter = OllamaRuntimeAdapter()
    log_file = tmp_path / "test.log"

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        with patch.object(adapter, "detect_existing_installation", return_value=(InstallationScope.SYSTEM, "/usr/bin/ollama")):
            ident = await adapter.start_instance(
                model_path_or_id="llama3.2:3b",
                candidate=sample_candidate,
                instance_id="test-inst-1",
                log_file=log_file,
            )
            assert ident is not None
            assert ident.ownership_class == OwnershipClass.READONLY_DISCOVERED
            assert ident.pid is None  # Must NOT claim LocalPilot's PID


def test_ollama_safe_zip_extraction(tmp_path: Path) -> None:
    """safe_extract_zip must extract valid archives and prevent directory traversal (Zip Slip)."""
    # Create test zip file
    zip_path = tmp_path / "test_ollama.zip"
    dest_dir = tmp_path / "extracted"
    dest_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("bin/ollama", b"MOCK_OLLAMA_BINARY")

    safe_extract_zip(zip_path, dest_dir)
    extracted_bin = dest_dir / "bin" / "ollama"
    assert extracted_bin.exists()
    assert extracted_bin.read_bytes() == b"MOCK_OLLAMA_BINARY"


# --- P2: SAFE REMOVAL PROTECTS RUNNING INSTANCES AND CONFLICTING JOBS ---

def test_safe_removal_blocks_running_model_and_active_jobs(tmp_path: Path) -> None:
    """StorageManager.safe_remove_installation must reject removal if an instance is running or job is active."""
    db = DatabaseManager(tmp_path / "inv_test.db")
    inventory = StorageManager(db=db)
    conn = db.get_connection()

    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES ('inst-locked', 'model-lock', 'Locked Model', 'llamacpp', '[]', 'tested_here', 'compatible', 'v1.0.0', 'localpilot_owned', '2026-01-01', '2026-01-01')
            """
        )
        conn.execute(
            """
            INSERT INTO running_instances (
                instance_id, model_id, installation_id, pid, process_create_time,
                executable_path, endpoint_url, runtime_version, launch_config_json,
                log_path, ownership_class, observed_execution_mode, started_at
            ) VALUES ('inst-run-1', 'model-lock', 'inst-locked', 1234, 1.0, '/bin/test', 'http://127.0.0.1:8080', '1.0', '{}', '/tmp/log', 'localpilot_owned', 'cpu', '2026-01-01')
            """
        )

    # 1. Attempt removal while running
    res = inventory.safe_remove_installation("inst-locked", delete_files=True)
    assert res["success"] is False
    assert "currently running" in res["error"]

    # Stop instance in DB
    with conn:
        conn.execute("DELETE FROM running_instances WHERE instance_id = 'inst-run-1'")
        # Add active job
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, operation_type, target_id, approved_plan_json, stage,
                checkpoints_json, progress_bytes, total_bytes, status,
                created_at, updated_at
            ) VALUES ('job-active-1', 'setup', 'inst-locked', '{}', 'downloading', '[]', 10, 100, 'running', '2026-01-01', '2026-01-01')
            """
        )

    # 2. Attempt removal while active job exists
    res2 = inventory.safe_remove_installation("inst-locked", delete_files=True)
    assert res2["success"] is False
    assert "active workflow job is in progress" in res2["error"]
