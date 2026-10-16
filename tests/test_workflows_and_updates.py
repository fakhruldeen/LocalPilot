"""Tests for setup workflow, multi-scope runtime provisioning, and update rollback."""

from pathlib import Path
import pytest

from unittest.mock import AsyncMock

from localpilot.application.setup_workflow import SetupWorkflow
from localpilot.application.update_workflow import UpdateWorkflow
from localpilot.domain.models import (
    ConfidenceLevel,
    ExecutionMode,
    NormalizedCandidate,
    TaskCategory,
    VerificationStage,
)
from localpilot.runtimes.base import InstallationScope
from localpilot.storage.db import DatabaseManager


@pytest.fixture
def test_candidate() -> NormalizedCandidate:
    return NormalizedCandidate(
        candidate_id="cand-test-setup",
        model_id="test/setup-model",
        name="Test Setup Model",
        source="huggingface",
        artifact_id="setup-test.gguf",
        architecture="llama",
        family="Test",
        tasks=[TaskCategory.CHAT],
        format="gguf",
        total_bytes=100 * 1024 * 1024,  # 100 MB
        runtime="llamacpp",
        usable_context=2048,
        target_context=2048,
        metadata_fetched_at="2026-01-01T00:00:00Z",
        estimated_ram_bytes=200 * 1024 * 1024,
        estimated_disk_bytes=150 * 1024 * 1024,
        confidence=ConfidenceLevel.VERIFIED,
        verification_stage=VerificationStage.SUPPORTED,
    )


@pytest.mark.asyncio
async def test_setup_workflow_execution(tmp_path: Path, test_candidate: NormalizedCandidate) -> None:
    db = DatabaseManager(tmp_path / "state.db")
    workflow = SetupWorkflow(db=db)

    # Test plan building for user scope
    plan = workflow.build_plan(test_candidate, scope=InstallationScope.USER)
    assert plan.install_scope == InstallationScope.USER
    assert plan.model_id == test_candidate.model_id

    # Mock prepare_runtime so test runs offline without downloading external release asset
    async def mock_prepare(*args, **kwargs):
        return "/bin/true"
    workflow.llamacpp_adapter.prepare_runtime = mock_prepare
    workflow.llamacpp_adapter.test_inference = AsyncMock(return_value=True)

    # Pre-stage test model fixture file in target directory
    target_dir = Path(plan.target_install_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    test_file = target_dir / (test_candidate.artifact_id or f"{test_candidate.model_id}.gguf")
    test_file.write_bytes(b"TEST_FIXTURE_BYTES" * 100)

    # Execute setup
    res = await workflow.execute_setup(test_candidate, plan)
    assert res["success"] is True
    assert "inst-" in res["installation_id"]

    # Verify audit decision was persisted
    conn = db.get_connection()
    audit_row = conn.execute("SELECT * FROM audit_decisions WHERE target_id = ?", (test_candidate.candidate_id,)).fetchone()
    assert audit_row is not None
    assert audit_row["outcome"] == "approved"

    # Verify installation record
    inst_row = conn.execute("SELECT * FROM installations WHERE installation_id = ?", (res["installation_id"],)).fetchone()
    assert inst_row is not None
    assert inst_row["readiness_stage"] == VerificationStage.TESTED_HERE.value


def test_update_rollback_on_failure(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "state.db")
    conn = db.get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                created_at, updated_at
            ) VALUES ('inst-up-1', 'model-up', 'Up Model', 'llamacpp', '[]', 'tested_here', 'compatible', 'v1.0.0', 'localpilot_owned', '2026-01-01', '2026-01-01')
            """
        )

    workflow = UpdateWorkflow(db=db)
    # Trigger update failure to verify safe rollback
    res = workflow.apply_update("inst-up-1", target_version="v2.0.0", simulate_failure_for_test=True)

    assert res["success"] is False
    assert res["rolled_back"] is True
    assert res["current_version"] == "v1.0.0"

    # Verify DB still has old version
    row = conn.execute("SELECT update_status FROM installations WHERE installation_id = 'inst-up-1'").fetchone()
    assert row["update_status"] == "v1.0.0"
