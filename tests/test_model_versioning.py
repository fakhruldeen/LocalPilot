"""Unit and acceptance tests for versioned model management, aliases, and shared file safety."""

import tempfile
from pathlib import Path
import pytest

from localpilot.application.version_service import ModelVersionService
from localpilot.domain.versioning import (
    ExecutionProfile,
    LogicalModel,
    ModelVersion,
    VerificationRecord,
)
from localpilot.storage.db import DatabaseManager


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "test_localpilot.db"
        db = DatabaseManager(db_file)
        yield db, Path(tmp_dir)


def test_migration_v4_and_backfill(temp_db):
    db, tmp_dir = temp_db
    conn = db.get_connection()

    # Pre-populate an installation
    conn.execute(
        """
        INSERT INTO installations (
            installation_id, model_id, name, runtime_name, artifact_ids_json,
            readiness_stage, compatibility_status, update_status, ownership_class,
            inference_backend, managed_service, deployment_method,
            created_at, updated_at
        ) VALUES (
            'inst-legacy-12345', 'llama-3.2-3b', 'Llama 3.2 3B', 'llamacpp', '["art-1"]',
            'tested_here', 'compatible', 'unknown', 'localpilot_owned',
            'llama.cpp', 'none', 'local_api_service',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
        )
        """
    )
    # Trigger v4 backfill logic
    db.backfill_v4_from_installations(conn)

    service = ModelVersionService(db)
    models = service.list_logical_models()
    assert any(m.logical_model_id == "llama-3.2-3b" for m in models)

    versions = service.list_versions("llama-3.2-3b")
    assert len(versions) >= 1
    assert versions[0].logical_model_id == "llama-3.2-3b"

    alias = service.get_alias("llama-3.2-3b:latest")
    assert alias is not None
    assert alias.version_id == versions[0].version_id

    default_alias = service.get_alias("default")
    assert default_alias is not None
    assert default_alias.version_id == versions[0].version_id


def test_pin_and_unpin_protection(temp_db):
    db, tmp_dir = temp_db
    service = ModelVersionService(db)

    ver = ModelVersion(
        version_id="ver-pin-test-1",
        logical_model_id="qwen-2.5-7b",
        version_label="v1",
        quantization="Q4_K_M",
        artifact_size_bytes=4 * 1024 * 1024 * 1024,
    )
    service.register_version(ver)

    # Pin
    pinned = service.pin_version("ver-pin-test-1", reason="Important release")
    assert pinned.is_pinned is True
    assert pinned.pin_count == 1
    assert pinned.pinned_reason == "Important release"

    # Attempt safe remove while pinned -> must raise ValueError
    with pytest.raises(ValueError, match="is pinned"):
        service.safe_remove_version("ver-pin-test-1")

    # Unpin
    unpinned = service.unpin_version("ver-pin-test-1")
    assert unpinned.is_pinned is False
    assert unpinned.pin_count == 0


def test_alias_activation_and_rollback(temp_db):
    db, tmp_dir = temp_db
    service = ModelVersionService(db)

    # Register two versions
    v1 = ModelVersion(version_id="ver-app-1", logical_model_id="gemma-2-2b", version_label="v1")
    v2 = ModelVersion(version_id="ver-app-2", logical_model_id="gemma-2-2b", version_label="v2")
    service.register_version(v1)
    service.register_version(v2)

    # Activate alias 'writing' -> v1
    a1 = service.activate_alias("writing", "ver-app-1", reason="Initial activation")
    assert a1.version_id == "ver-app-1"
    assert service.resolve_alias("writing").version_id == "ver-app-1"

    # Switch alias 'writing' -> v2
    a2 = service.activate_alias("writing", "ver-app-2", reason="Upgrade to v2")
    assert a2.version_id == "ver-app-2"
    assert service.resolve_alias("writing").version_id == "ver-app-2"

    # Check history
    history = service.get_alias_history("writing")
    assert len(history) == 2
    assert history[1].previous_version_id == "ver-app-1"
    assert history[1].new_version_id == "ver-app-2"

    # Cannot remove v2 while it's active under alias 'writing'
    with pytest.raises(ValueError, match="currently active under alias"):
        service.safe_remove_version("ver-app-2")

    # Rollback alias 'writing' -> back to v1
    rb = service.rollback_alias("writing")
    assert rb.version_id == "ver-app-1"
    assert service.resolve_alias("writing").version_id == "ver-app-1"


def test_shared_artifact_file_protection(temp_db):
    db, tmp_dir = temp_db
    service = ModelVersionService(db)

    shared_file = tmp_dir / "shared_weights.gguf"
    shared_file.write_bytes(b"GGUF_MOCK_DATA")

    # Version A and Version B share the exact same weights file
    v_a = ModelVersion(
        version_id="ver-shared-a",
        logical_model_id="shared-family",
        version_label="v1-balanced",
        installed_path=str(shared_file),
        artifact_digest="sha256:11223344",
    )
    v_b = ModelVersion(
        version_id="ver-shared-b",
        logical_model_id="shared-family",
        version_label="v1-custom",
        installed_path=str(shared_file),
        artifact_digest="sha256:11223344",
    )
    service.register_version(v_a)
    service.register_version(v_b)

    # Removing v_a must NOT delete the shared file on disk
    res_a = service.safe_remove_version("ver-shared-a")
    assert res_a["deleted_from_db"] is True
    assert res_a["file_deleted"] is False
    assert res_a["shared_by_other_versions"] is True
    assert shared_file.exists()

    # Removing v_b (now the sole remaining version referencing the file) deletes the file
    res_b = service.safe_remove_version("ver-shared-b")
    assert res_b["deleted_from_db"] is True
    assert res_b["file_deleted"] is True
    assert not shared_file.exists()


def test_compare_versions_and_verification_record(temp_db):
    db, tmp_dir = temp_db
    service = ModelVersionService(db)

    v1 = ModelVersion(
        version_id="ver-cmp-1",
        logical_model_id="test-cmp",
        version_label="v1",
        quantization="Q4_K_M",
        artifact_size_bytes=2000000,
        context_length=2048,
    )
    v2 = ModelVersion(
        version_id="ver-cmp-2",
        logical_model_id="test-cmp",
        version_label="v2",
        quantization="Q8_0",
        artifact_size_bytes=4000000,
        context_length=4096,
    )
    service.register_version(v1)
    service.register_version(v2)

    cmp = service.compare_versions("ver-cmp-1", "ver-cmp-2")
    assert cmp["differences"]["same_logical_model"] is True
    assert cmp["differences"]["quantization_changed"] is True
    assert cmp["differences"]["context_length_changed"] is True
    assert cmp["differences"]["size_diff_bytes"] == 2000000

    # Verification records
    vr = VerificationRecord(
        version_id="ver-cmp-2",
        runtime_version="llama.cpp-b3500",
        test_type="inference_smoke",
        passed=True,
        benchmark_tps=45.2,
        first_token_latency_ms=120.5,
    )
    service.record_verification(vr)
    verifs = service.get_verifications("ver-cmp-2")
    assert len(verifs) == 1
    assert verifs[0].passed is True
    assert verifs[0].benchmark_tps == 45.2
