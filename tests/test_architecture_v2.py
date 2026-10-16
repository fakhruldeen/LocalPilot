"""Comprehensive tests for Architecture v2 (3-axis execution, services, backends, and safety)."""

import json
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from localpilot.domain.capabilities import (
    AdapterCapability,
    DeploymentMethod,
    ImplementationStatus,
    InferenceBackendId,
    LMStudioComponent,
    ManagedServiceId,
    normalize_service_id,
)
from localpilot.domain.compatibility import CompatibilityPolicy
from localpilot.domain.execution_plan import ExecutionPlan, ExecutionPlanGenerator
from localpilot.domain.models import (
    ConfidenceLevel,
    ExecutionMode,
    NormalizedCandidate,
    TaskCategory,
    VerificationStage,
)
from localpilot.domain.ownership import OwnershipClass, ProcessIdentity
from localpilot.hardware.inspector import SystemInspector
from localpilot.observability.events import ObservabilityHub
from localpilot.observability.schema import EventCategory, EventEnvelope, EventLevel
from localpilot.platforms.base import HardwareSnapshot, MemoryAccountingModel
from localpilot.runtimes.deployments import DirectWorkerController, WorkerCrashError
from localpilot.runtimes.docker_model_runner import DockerModelRunnerServiceAdapter
from localpilot.runtimes.lm_studio import LMStudioServiceAdapter
from localpilot.runtimes.ollama import OllamaRuntimeAdapter
from localpilot.runtimes.vllm_backend import VllmBackendAdapter
from localpilot.sources.catalog_service import CatalogService
from localpilot.storage.db import DatabaseManager
from localpilot.storage.inventory import StorageManager


def test_schema_v1_to_v2_migration(tmp_path: Path) -> None:
    """Scenario 1: Non-destructive SQLite migration from v1 to v2 schema."""
    db_file = tmp_path / "test_v1.db"

    # Set up raw v1 database with initial table structure
    conn = sqlite3.connect(str(db_file))
    conn.execute("""
    CREATE TABLE schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL,
        description TEXT NOT NULL
    );
    """)
    conn.execute("INSERT INTO schema_migrations VALUES (1, '2026-01-01T00:00:00Z', 'v1 initial');")

    conn.execute("""
    CREATE TABLE installations (
        installation_id TEXT PRIMARY KEY,
        model_id TEXT NOT NULL,
        name TEXT NOT NULL,
        runtime_name TEXT NOT NULL,
        artifact_ids_json TEXT NOT NULL,
        readiness_stage TEXT NOT NULL,
        compatibility_status TEXT NOT NULL,
        update_status TEXT NOT NULL,
        ownership_class TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)
    conn.execute("""
    INSERT INTO installations VALUES (
        'inst-ollama-1', 'llama3.2:3b', 'Llama 3.2 3B', 'ollama', '["art-1"]',
        'tested_here', 'compatible', 'up_to_date', 'localpilot_owned',
        '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
    );
    """)
    conn.execute("""
    CREATE TABLE running_instances (
        instance_id TEXT PRIMARY KEY,
        model_id TEXT NOT NULL,
        installation_id TEXT,
        pid INTEGER NOT NULL,
        process_create_time REAL NOT NULL,
        executable_path TEXT NOT NULL,
        endpoint_url TEXT NOT NULL,
        runtime_version TEXT,
        launch_config_json TEXT NOT NULL,
        log_path TEXT NOT NULL,
        ownership_class TEXT NOT NULL,
        observed_execution_mode TEXT NOT NULL,
        started_at TEXT NOT NULL
    );
    """)
    conn.commit()
    conn.close()

    # Now open with DatabaseManager which applies migrations
    mgr = DatabaseManager(db_file)
    c2 = mgr.get_connection()

    # Verify migration table
    versions = [row["version"] for row in c2.execute("SELECT version FROM schema_migrations").fetchall()]
    assert 2 in versions

    # Verify new columns exist in installations
    inst_cols = {row["name"] for row in c2.execute("PRAGMA table_info(installations)").fetchall()}
    assert "inference_backend" in inst_cols
    assert "managed_service" in inst_cols
    assert "deployment_method" in inst_cols
    assert "execution_plan_json" in inst_cols
    assert "alternative_plans_json" in inst_cols

    # Verify backfill for legacy Ollama row
    row = c2.execute("SELECT * FROM installations WHERE installation_id = 'inst-ollama-1'").fetchone()
    assert row["inference_backend"] == "unknown/service-managed"
    assert row["managed_service"] == "ollama"
    assert row["deployment_method"] == "local_api_service"

    # Verify execution_plans table exists
    plan_cols = {row["name"] for row in c2.execute("PRAGMA table_info(execution_plans)").fetchall()}
    assert "plan_id" in plan_cols
    assert "candidate_id" in plan_cols
    assert "inference_backend" in plan_cols


def test_lm_studio_llmster_unification_and_no_duplicates() -> None:
    """Scenario 2: Unify LM Studio Desktop, llmster daemon, and lms CLI."""
    assert normalize_service_id("llmster") == ManagedServiceId.LM_STUDIO
    assert normalize_service_id("lmstudio") == ManagedServiceId.LM_STUDIO
    assert normalize_service_id("lm_studio") == ManagedServiceId.LM_STUDIO
    assert normalize_service_id("LM Studio / llmster") == ManagedServiceId.LM_STUDIO
    assert normalize_service_id("ollama") == ManagedServiceId.OLLAMA
    assert normalize_service_id("docker_model_runner") == ManagedServiceId.DOCKER_MODEL_RUNNER

    adapter = LMStudioServiceAdapter()
    assert adapter.get_service_id() == ManagedServiceId.LM_STUDIO

    # External ownership safety rules
    stop_cap = adapter.supports_capability(AdapterCapability.STOP_OWNED_PROCESS)
    assert stop_cap.supported is False
    assert stop_cap.reason_code == "MANAGED_SERVICE_EXTERNAL_OWNERSHIP"

    install_cap = adapter.supports_capability(AdapterCapability.INSTALL_SOFTWARE)
    assert install_cap.supported is False


@pytest.mark.asyncio
async def test_lm_studio_read_only_probing() -> None:
    """Scenario 3: LM Studio local endpoint read-only probing."""
    adapter = LMStudioServiceAdapter(endpoint_url="http://127.0.0.1:1234")

    # Mock successful response
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [{"id": "qwen2.5-7b"}]}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        ready = await adapter.check_api_readiness()
        assert ready is True

        models = await adapter.list_models()
        assert len(models) == 1
        assert models[0]["id"] == "qwen2.5-7b"


def test_docker_model_runner_distinction() -> None:
    """Scenario 4: Docker Model Runner distinct from generic container deployment."""
    dmr = DockerModelRunnerServiceAdapter()
    assert dmr.get_service_id() == ManagedServiceId.DOCKER_MODEL_RUNNER

    # Safety: does not mutate daemon
    install_cap = dmr.supports_capability(AdapterCapability.INSTALL_SOFTWARE)
    assert install_cap.supported is False
    assert install_cap.reason_code == "EXTERNAL_SERVICE_PROTECTION"


def test_vllm_backend_platform_constraints() -> None:
    """Scenario 5: vLLM is marked advanced-only and strictly checks Linux + GPU."""
    adapter = VllmBackendAdapter()
    assert adapter.is_advanced_only is True
    assert adapter.get_backend_id() == InferenceBackendId.VLLM

    # macOS Apple Silicon or Intel must be rejected
    ok, reason = VllmBackendAdapter.check_platform_compatibility("darwin", has_gpu=True)
    assert ok is False
    assert "macOS" in reason

    # Linux without GPU must be rejected
    ok, reason = VllmBackendAdapter.check_platform_compatibility("linux", has_gpu=False)
    assert ok is False
    assert "GPU" in reason

    # Linux with GPU is accepted
    ok, reason = VllmBackendAdapter.check_platform_compatibility("linux", has_gpu=True)
    assert ok is True


def test_subprocess_worker_crash_recovery(tmp_path: Path) -> None:
    """Scenario 6: Direct worker process runs externally and handles crashes without killing parent."""
    controller = DirectWorkerController()
    log_file = tmp_path / "worker.log"

    # Start a python process that immediately exits with code 42
    controller.start_worker(["-c", "import sys; sys.exit(42)"], log_file=log_file)

    with pytest.raises(WorkerCrashError) as exc_info:
        controller.execute_request({"type": "ping"}, timeout_seconds=2.0)

    assert "exit code" in str(exc_info.value) or "terminated" in str(exc_info.value)


@pytest.mark.asyncio
async def test_stop_instance_vs_stop_daemon() -> None:
    """Scenario 7: Stopping external/shared model must unload memory without terminating daemon."""
    adapter = OllamaRuntimeAdapter()

    identity = ProcessIdentity(
        pid=99999,
        executable_path="/usr/bin/ollama",
        process_create_time=1234567.0,
        ownership_class=OwnershipClass.EXTERNAL_APPROVED,
        service_id="llama3.2:3b",
        endpoint_url="http://127.0.0.1:11434",
    )

    with patch.object(adapter, "unload_model", new_callable=AsyncMock) as mock_unload:
        mock_unload.return_value = True
        stopped = await adapter.stop_instance(identity)
        assert stopped is True
        mock_unload.assert_called_once_with("http://127.0.0.1:11434", "llama3.2:3b")


def test_discovery_deduplication_one_row_per_candidate(tmp_path: Path) -> None:
    """Scenario 8: Discovery table shows exactly 1 row per candidate, with alternative plans attached."""
    db = DatabaseManager(tmp_path / "test_cat.db")
    cat = CatalogService(db=db)

    results = cat.discover()
    assert len(results) > 0

    candidate_ids = [r.candidate.candidate_id for r in results]
    # Assert every row has a distinct candidate_id (NO duplicate rows)
    assert len(candidate_ids) == len(set(candidate_ids))

    for r in results:
        assert r.recommended_plan is not None
        assert isinstance(r.alternative_plans, list)


def test_observability_correlation_with_3axis_fields(tmp_path: Path) -> None:
    """Scenario 9: Observability records include plan ID and 3-axis fields."""
    db = DatabaseManager(tmp_path / "obs.db")
    hub = ObservabilityHub(db=db)

    # 1. Event envelope serialization test
    event = hub.emit_event(
        category=EventCategory.PROCESS,
        event_name="process.spawned",
        message="Spawned direct worker process",
        level=EventLevel.INFO,
        execution_plan_id="plan-123",
        inference_backend="llama.cpp",
        managed_service="none",
        deployment_method="direct_worker",
    )
    assert event.execution_plan_id == "plan-123"
    assert event.inference_backend == "llama.cpp"
    assert event.managed_service == "none"
    assert event.deployment_method == "direct_worker"

    # 2. Audit checkpoint persistence test
    audit_id = hub.record_audit_checkpoint(
        decision_type="model_setup",
        target_id="cand-test",
        payload={
            "execution_plan_id": "plan-123",
            "inference_backend": "llama.cpp",
            "managed_service": "none",
            "deployment_method": "direct_worker",
        },
        outcome="approved",
        is_critical=True,
    )
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM audit_decisions WHERE decision_id = ?", (audit_id,)).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload["execution_plan_id"] == "plan-123"
    assert payload["inference_backend"] == "llama.cpp"


def test_storage_inventory_preserves_external_files(tmp_path: Path) -> None:
    """Scenario 10: Storage inventory never touches non-owned external files."""
    db = DatabaseManager(tmp_path / "storage.db")
    sm = StorageManager(db=db)

    external_file = tmp_path / "external_model.gguf"
    external_file.write_bytes(b"DATA")

    # Register artifact as unmanaged external
    sm.register_artifact(
        artifact_id="art-ext-1",
        physical_path=external_file,
        bytes_size=4,
        is_managed=False,
    )

    # Create installation with EXTERNAL_APPROVED ownership
    conn = db.get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                "inst-ext-1", "ext-model", "External Model", "external",
                json.dumps(["art-ext-1"]), "tested_here", "compatible", "up_to_date",
                OwnershipClass.EXTERNAL_APPROVED.value
            ),
        )

    # Remove installation with delete_files=True
    res = sm.safe_remove_installation("inst-ext-1", delete_files=True)
    assert res["success"] is True

    # External file must NOT be deleted
    assert external_file.exists()
