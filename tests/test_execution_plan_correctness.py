"""Tests for Execution-Plan Correctness and Enforcement.

Verifies:
- Canonical ExecutionSpecification schema and DB round-trip.
- Launch-time hardware/memory revalidation blocking unsafe startup.
- Prevention of silent GPU-to-CPU fallback when GPU is explicitly requested.
- Multi-stage readiness probe failure handling and safe process cleanup.
- Truthful recording of runtime_version and observed_execution_mode (no fabricated "1.0"/"auto").
- Configuration change blocks inappropriate running instance reuse.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from localpilot.application.runtime_service import RuntimeLifecycleService
from localpilot.domain.capabilities import (
    DeploymentMethod,
    InferenceBackendId,
    ManagedServiceId,
)
from localpilot.domain.models import ExecutionMode
from localpilot.domain.execution_plan import (
    ExecutionPlan,
    ExecutionSpecification,
)
from localpilot.hardware.inspector import SystemInspectionReport
from localpilot.platforms.base import (
    GpuDevice,
    HardwareSnapshot,
    MemoryAccountingModel,
)
from localpilot.domain.ownership import OwnershipClass, ProcessIdentity
from localpilot.storage.db import DatabaseManager


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "exec_spec_test.db"
    return DatabaseManager(db_file)


def make_system_report(
    ram_available_bytes: int = 16 * 1024**3,
    ram_total_bytes: int = 16 * 1024**3,
    gpu_devices: list = None,
    os_name: str = "linux",
    memory_model: MemoryAccountingModel = MemoryAccountingModel.SEPARATE,
) -> SystemInspectionReport:
    hw = HardwareSnapshot(
        os_name=os_name,
        os_version="6.8.0",
        cpu_arch="x86_64",
        cpu_cores_physical=4,
        cpu_cores_logical=8,
        ram_total_bytes=ram_total_bytes,
        ram_available_bytes=ram_available_bytes,
        safe_ram_budget_bytes=ram_available_bytes // 2,
        safe_vram_budget_bytes=0,
        disk_total_bytes=100 * 1024**3,
        disk_available_bytes=50 * 1024**3,
        gpu_devices=gpu_devices or [],
        memory_model=memory_model,
        measured_at="2026-10-17T00:00:00Z",
    )
    return SystemInspectionReport(hardware=hw)


def test_canonical_execution_spec_persistence_and_roundtrip(test_db: DatabaseManager, tmp_path: Path):
    """Verifies that ExecutionSpecification round-trips through JSON and database schema correctly."""
    spec = ExecutionSpecification(
        plan_id="plan-test-123",
        installation_id="inst-test-1",
        model_id="model-test",
        canonical_artifact_id="art-test-gguf",
        artifact_path=str(tmp_path / "model.gguf"),
        artifact_sha256="abc123hash",
        inference_backend=InferenceBackendId.LLAMACPP,
        managed_service=ManagedServiceId.NONE,
        deployment_method=DeploymentMethod.LOCAL_API_SERVICE,
        requested_execution_mode=ExecutionMode.GPU,
        offload_layers=33,
        context_window=4096,
        concurrency=1,
        required_memory_budget_bytes=4 * 1024**3,
        memory_accounting_model=MemoryAccountingModel.SEPARATE,
    )

    spec_json = spec.model_dump_json()
    recovered = ExecutionSpecification.model_validate_json(spec_json)
    assert recovered.plan_id == spec.plan_id
    assert recovered.context_window == 4096
    assert recovered.requested_execution_mode == ExecutionMode.GPU
    assert recovered.offload_layers == 33

    # Test DB round-trip via installations table
    conn = test_db.get_connection()
    conn.execute(
        """
        INSERT INTO installations (
            installation_id, model_id, name, runtime_name, artifact_ids_json,
            readiness_stage, compatibility_status, update_status, ownership_class,
            created_at, updated_at, execution_spec_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "inst-test-1",
            "model-test",
            "Test Model",
            "llamacpp",
            json.dumps(["art-test-gguf"]),
            "verified",
            "compatible",
            "up_to_date",
            "owned_child",
            "2026-10-17T00:00:00Z",
            "2026-10-17T00:00:00Z",
            spec_json,
        ),
    )
    conn.commit()

    row = conn.execute("SELECT execution_spec_json FROM installations WHERE installation_id = ?", ("inst-test-1",)).fetchone()
    assert row is not None
    db_spec = ExecutionSpecification.model_validate_json(row["execution_spec_json"])
    assert db_spec.canonical_artifact_id == "art-test-gguf"
    assert db_spec.required_memory_budget_bytes == 4 * 1024**3


@pytest.mark.asyncio
async def test_launch_time_memory_headroom_drop_blocks_startup(test_db: DatabaseManager, tmp_path: Path):
    """Verifies that launch-time drop in available RAM prevents unsafe startup and does not crash OS."""
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"GGUF_TEST_BYTES")

    spec = ExecutionSpecification(
        plan_id="plan-ram-test",
        installation_id="inst-ram-1",
        model_id="model-ram",
        canonical_artifact_id="art-ram-1",
        inference_backend=InferenceBackendId.LLAMACPP,
        requested_execution_mode=ExecutionMode.CPU,
        context_window=2048,
        artifact_path=str(model_file),
        required_memory_budget_bytes=8 * 1024**3,  # Needs 8 GB
    )

    conn = test_db.get_connection()
    conn.execute(
        """
        INSERT INTO installations (
            installation_id, model_id, name, runtime_name, artifact_ids_json,
            readiness_stage, compatibility_status, update_status, ownership_class,
            created_at, updated_at, execution_spec_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "inst-ram-1",
            "model-ram",
            "RAM Heavy Model",
            "llamacpp",
            "[]",
            "verified",
            "compatible",
            "up_to_date",
            "owned_child",
            "2026-10-17T00:00:00Z",
            "2026-10-17T00:00:00Z",
            spec.model_dump_json(),
        ),
    )
    conn.commit()

    service = RuntimeLifecycleService(db=test_db)
    mock_report = make_system_report(ram_available_bytes=2 * 1024**3)

    with patch.object(service.inspector, "inspect", return_value=mock_report):
        with pytest.raises(RuntimeError) as exc_info:
            await service.start_installed_model("inst-ram-1")
        assert "Insufficient memory to start" in str(exc_info.value)
        assert "requires at least 8.0 GB RAM, but only 2.0 GB is currently available" in str(exc_info.value)


@pytest.mark.asyncio
async def test_explicit_gpu_mode_on_cpu_only_system_rejects_silent_fallback(test_db: DatabaseManager, tmp_path: Path):
    """Verifies that an approved GPU plan fails fast rather than silently degrading to CPU."""
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"GGUF_TEST_BYTES")

    spec = ExecutionSpecification(
        plan_id="plan-gpu-test",
        installation_id="inst-gpu-1",
        model_id="model-gpu",
        canonical_artifact_id="art-gpu-1",
        inference_backend=InferenceBackendId.LLAMACPP,
        requested_execution_mode=ExecutionMode.GPU,
        offload_layers=99,
        context_window=2048,
        artifact_path=str(model_file),
        required_memory_budget_bytes=2 * 1024**3,
    )

    conn = test_db.get_connection()
    conn.execute(
        """
        INSERT INTO installations (
            installation_id, model_id, name, runtime_name, artifact_ids_json,
            readiness_stage, compatibility_status, update_status, ownership_class,
            created_at, updated_at, execution_spec_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "inst-gpu-1",
            "model-gpu",
            "GPU Demanding Model",
            "llamacpp",
            "[]",
            "verified",
            "compatible",
            "up_to_date",
            "owned_child",
            "2026-10-17T00:00:00Z",
            "2026-10-17T00:00:00Z",
            spec.model_dump_json(),
        ),
    )
    conn.commit()

    service = RuntimeLifecycleService(db=test_db)
    mock_report = make_system_report(ram_available_bytes=24 * 1024**3, gpu_devices=[])

    with patch.object(service.inspector, "inspect", return_value=mock_report):
        with pytest.raises(RuntimeError) as exc_info:
            await service.start_installed_model("inst-gpu-1")
        assert "Explicit GPU execution mode was approved" in str(exc_info.value)
        assert "no compatible GPU" in str(exc_info.value)
        assert "will not silently fall back to CPU" in str(exc_info.value)


@pytest.mark.asyncio
async def test_readiness_probe_failure_cleans_up_process_and_raises(test_db: DatabaseManager, tmp_path: Path):
    """Verifies that if readiness probe fails, process is terminated and RuntimeError raised."""
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"GGUF_TEST_BYTES")

    spec = ExecutionSpecification(
        plan_id="plan-probe-test",
        installation_id="inst-probe-1",
        model_id="model-probe",
        canonical_artifact_id="art-probe-1",
        inference_backend=InferenceBackendId.LLAMACPP,
        requested_execution_mode=ExecutionMode.CPU,
        context_window=2048,
        artifact_path=str(model_file),
        required_memory_budget_bytes=2 * 1024**3,
    )

    conn = test_db.get_connection()
    conn.execute(
        """
        INSERT INTO installations (
            installation_id, model_id, name, runtime_name, artifact_ids_json,
            readiness_stage, compatibility_status, update_status, ownership_class,
            created_at, updated_at, execution_spec_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "inst-probe-1",
            "model-probe",
            "Probe Test Model",
            "llamacpp",
            "[]",
            "verified",
            "compatible",
            "up_to_date",
            "owned_child",
            "2026-10-17T00:00:00Z",
            "2026-10-17T00:00:00Z",
            spec.model_dump_json(),
        ),
    )
    conn.commit()

    service = RuntimeLifecycleService(db=test_db)
    mock_report = make_system_report()

    dummy_pid = 99991
    mock_adapter = MagicMock()
    proc_id = ProcessIdentity(
        pid=dummy_pid,
        process_create_time=12345.0,
        executable_path="/bin/llama-server",
        endpoint_url="http://127.0.0.1:8080",
        ownership_class=OwnershipClass.LOCALPILOT_OWNED,
        runtime_version="b3200",
        observed_execution_mode="cpu",
    )
    mock_adapter.start_instance = AsyncMock(return_value=proc_id)

    mock_proc = MagicMock()
    mock_proc.terminate = MagicMock()
    mock_proc.wait = MagicMock()

    with patch.object(service.inspector, "inspect", return_value=mock_report), \
         patch.object(service, "get_adapter", return_value=mock_adapter), \
         patch("httpx.AsyncClient.get", side_effect=Exception("Connection refused")), \
         patch("psutil.Process", return_value=mock_proc):

        with pytest.raises(RuntimeError) as exc_info:
            await service.start_installed_model("inst-probe-1")

        assert "readiness probe failed to confirm HTTP 200 OK" in str(exc_info.value)
        assert mock_proc.terminate.called


@pytest.mark.asyncio
async def test_running_instances_records_truthful_runtime_and_mode_without_fabrication(test_db: DatabaseManager, tmp_path: Path):
    """Verifies that running_instances records genuine probed runtime version and observed mode."""
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"GGUF_TEST_BYTES")

    spec = ExecutionSpecification(
        plan_id="plan-truth-test",
        installation_id="inst-truth-1",
        model_id="model-truth",
        canonical_artifact_id="art-truth-1",
        inference_backend=InferenceBackendId.LLAMACPP,
        requested_execution_mode=ExecutionMode.GPU,
        offload_layers=33,
        context_window=2048,
        artifact_path=str(model_file),
        required_memory_budget_bytes=2 * 1024**3,
    )

    conn = test_db.get_connection()
    conn.execute(
        """
        INSERT INTO installations (
            installation_id, model_id, name, runtime_name, artifact_ids_json,
            readiness_stage, compatibility_status, update_status, ownership_class,
            created_at, updated_at, execution_spec_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "inst-truth-1",
            "model-truth",
            "Truthful Model",
            "llamacpp",
            "[]",
            "verified",
            "compatible",
            "up_to_date",
            "owned_child",
            "2026-10-17T00:00:00Z",
            "2026-10-17T00:00:00Z",
            spec.model_dump_json(),
        ),
    )
    conn.commit()

    service = RuntimeLifecycleService(db=test_db)
    gpu = GpuDevice(name="RTX 4096", acceleration_type="cuda")
    mock_report = make_system_report(gpu_devices=[gpu])

    proc_id = ProcessIdentity(
        pid=12345,
        process_create_time=12345.0,
        executable_path="/usr/bin/llama-server",
        endpoint_url="http://127.0.0.1:8080",
        ownership_class=OwnershipClass.LOCALPILOT_OWNED,
        runtime_version="llama.cpp-b3250",
        observed_execution_mode="gpu",
    )
    mock_adapter = MagicMock()
    mock_adapter.start_instance = AsyncMock(return_value=proc_id)
    mock_http_resp = MagicMock(status_code=200)

    with patch.object(service.inspector, "inspect", return_value=mock_report), \
         patch.object(service, "get_adapter", return_value=mock_adapter), \
         patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_http_resp)):

        res = await service.start_installed_model("inst-truth-1")
        assert res["instance_id"] is not None

        row = conn.execute("SELECT * FROM running_instances WHERE instance_id = ?", (res["instance_id"],)).fetchone()
        assert row is not None
        assert row["runtime_version"] == "llama.cpp-b3250"
        assert row["runtime_version"] != "1.0"
        assert row["observed_execution_mode"] == "gpu"
        assert row["observed_execution_mode"] != "auto"
        assert row["execution_spec_json"] is not None


@pytest.mark.asyncio
async def test_running_instance_reuse_blocked_if_spec_mismatched(test_db: DatabaseManager, tmp_path: Path):
    """Verifies that an existing running instance is NOT reused if context or mode differs."""
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"GGUF_TEST_BYTES")

    spec_2k = ExecutionSpecification(
        plan_id="plan-2k",
        installation_id="inst-reuse-1",
        model_id="model-reuse",
        canonical_artifact_id="art-1",
        inference_backend=InferenceBackendId.LLAMACPP,
        requested_execution_mode=ExecutionMode.CPU,
        context_window=2048,
        artifact_path=str(model_file),
        required_memory_budget_bytes=2 * 1024**3,
    )

    spec_8k = ExecutionSpecification(
        plan_id="plan-8k",
        installation_id="inst-reuse-1",
        model_id="model-reuse",
        canonical_artifact_id="art-1",
        inference_backend=InferenceBackendId.LLAMACPP,
        requested_execution_mode=ExecutionMode.CPU,
        context_window=8192,  # Different context window!
        artifact_path=str(model_file),
        required_memory_budget_bytes=3 * 1024**3,
    )

    conn = test_db.get_connection()
    conn.execute(
        """
        INSERT INTO installations (
            installation_id, model_id, name, runtime_name, artifact_ids_json,
            readiness_stage, compatibility_status, update_status, ownership_class,
            created_at, updated_at, execution_spec_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "inst-reuse-1",
            "model-reuse",
            "Reuse Test Model",
            "llamacpp",
            "[]",
            "verified",
            "compatible",
            "up_to_date",
            "owned_child",
            "2026-10-17T00:00:00Z",
            "2026-10-17T00:00:00Z",
            spec_8k.model_dump_json(),
        ),
    )
    # Existing running instance has spec_2k
    conn.execute(
        """
        INSERT INTO running_instances (
            instance_id, model_id, installation_id, pid, process_create_time,
            executable_path, endpoint_url, launch_config_json, log_path, ownership_class,
            observed_execution_mode, started_at, execution_spec_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "run-existing-2k",
            "model-reuse",
            "inst-reuse-1",
            54321,
            12345.0,
            "/bin/llama-server",
            "http://127.0.0.1:8080",
            "{}",
            "/tmp/test.log",
            "owned_child",
            "cpu",
            "2026-10-17T00:00:00Z",
            spec_2k.model_dump_json(),
        ),
    )
    conn.commit()

    service = RuntimeLifecycleService(db=test_db)
    mock_report = make_system_report()

    proc_id = ProcessIdentity(
        pid=54322,
        process_create_time=12345.0,
        executable_path="/bin/llama-server",
        endpoint_url="http://127.0.0.1:8081",
        ownership_class=OwnershipClass.LOCALPILOT_OWNED,
        runtime_version="b3200",
        observed_execution_mode="cpu",
    )
    mock_adapter = MagicMock()
    mock_adapter.start_instance = AsyncMock(return_value=proc_id)
    mock_http_resp = MagicMock(status_code=200)

    with patch.object(service.inspector, "inspect", return_value=mock_report), \
         patch.object(service, "get_adapter", return_value=mock_adapter), \
         patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_http_resp)):

        res = await service.start_installed_model("inst-reuse-1")
        # Ensure it started a NEW instance instead of returning already_running = True for run-existing-2k
        assert res.get("already_running") is not True
        assert res["instance_id"] != "run-existing-2k"
