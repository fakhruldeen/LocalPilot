"""Tests for Update Recovery and Shared Artifact Protection Matrix.

Verifies:
- Pre-activation verification of staged update files before touching active files.
- Verified rollback guarantees if failure occurs during activation.
- Protection of immutable shared artifacts (ref_count > 1) during updates.
"""

import json
from pathlib import Path

import pytest

from localpilot.application.update_workflow import UpdateWorkflow
from localpilot.storage.db import DatabaseManager


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "update_test.db"
    return DatabaseManager(db_file)


def test_update_pre_activation_verification_failure_leaves_file_and_db_untouched(test_db: DatabaseManager, tmp_path: Path):
    """Verifies that if a staged update fails verification callback, the active file and DB are completely untouched."""
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    active_file = models_dir / "active_model.gguf"
    active_file.write_bytes(b"ORIGINAL_ACTIVE_BYTES")

    staged_dir = tmp_path / "staged"
    staged_dir.mkdir(parents=True, exist_ok=True)
    staged_file = staged_dir / "update_v2.gguf"
    staged_file.write_bytes(b"STAGED_CORRUPT_BYTES")

    conn = test_db.get_connection()
    art_id = "art_active_1"
    conn.execute(
        """
        INSERT INTO storage_artifacts (
            artifact_id, physical_path, bytes, sha256, is_managed,
            install_scope, management_mode, deletion_method, ref_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (art_id, str(active_file), len(b"ORIGINAL_ACTIVE_BYTES"), "sha1", 1, "isolated", "managed", "direct_file_delete", 1, "2026-10-17T00:00:00Z"),
    )
    conn.execute(
        """
        INSERT INTO installations (
            installation_id, model_id, name, runtime_name, artifact_ids_json,
            readiness_stage, compatibility_status, update_status, ownership_class,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("inst-upd-1", "model-test", "Test Model", "llamacpp", json.dumps([art_id]), "verified", "compatible", "v1.0.0", "owned_child", "2026-10-17T00:00:00Z", "2026-10-17T00:00:00Z"),
    )
    conn.commit()

    workflow = UpdateWorkflow(db=test_db, data_dir=tmp_path)

    # Verification callback rejects the staged file
    def rejecting_verifier(path: Path) -> bool:
        return False

    res = workflow.apply_update(
        "inst-upd-1",
        target_version="v2.0.0",
        staged_source_path=staged_file,
        verify_callback=rejecting_verifier,
    )

    assert res["success"] is False
    assert res["rolled_back"] is True
    assert res["rollback_status"] == "restored"

    # Active file must remain 100% original
    assert active_file.read_bytes() == b"ORIGINAL_ACTIVE_BYTES"

    # DB update status must still be v1.0.0
    row = conn.execute("SELECT update_status FROM installations WHERE installation_id = 'inst-upd-1'").fetchone()
    assert row["update_status"] == "v1.0.0"


def test_update_failure_with_file_modification_rolls_back_bytes_and_db(test_db: DatabaseManager, tmp_path: Path):
    """Verifies that if an unexpected error occurs after file modification, the backup is restored to active file."""
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    art_file = models_dir / "active_model.gguf"
    art_file.write_bytes(b"ORIGINAL_SAFE_BYTES")

    conn = test_db.get_connection()
    art_id = "art_active_2"
    conn.execute(
        """
        INSERT INTO storage_artifacts (
            artifact_id, physical_path, bytes, sha256, is_managed,
            install_scope, management_mode, deletion_method, ref_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (art_id, str(art_file), len(b"ORIGINAL_SAFE_BYTES"), "sha2", 1, "isolated", "managed", "direct_file_delete", 1, "2026-10-17T00:00:00Z"),
    )
    conn.execute(
        """
        INSERT INTO installations (
            installation_id, model_id, name, runtime_name, artifact_ids_json,
            readiness_stage, compatibility_status, update_status, ownership_class,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("inst-upd-2", "model-test-2", "Test Model 2", "llamacpp", json.dumps([art_id]), "verified", "compatible", "v1.0.0", "owned_child", "2026-10-17T00:00:00Z", "2026-10-17T00:00:00Z"),
    )
    conn.commit()

    workflow = UpdateWorkflow(db=test_db, data_dir=tmp_path)

    # Force simulated failure
    res = workflow.apply_update("inst-upd-2", target_version="v2.0.0", simulate_failure_for_test=True)

    assert res["success"] is False
    assert res["rolled_back"] is True
    assert res["current_version"] == "v1.0.0"
    assert art_file.read_bytes() == b"ORIGINAL_SAFE_BYTES"

    # Verify DB update_status remains v1.0.0
    row = conn.execute("SELECT update_status FROM installations WHERE installation_id = 'inst-upd-2'").fetchone()
    assert row["update_status"] == "v1.0.0"


def test_immutable_shared_artifact_update_does_not_mutate_shared_file(test_db: DatabaseManager, tmp_path: Path):
    """Verifies that updating an installation with ref_count > 1 allocates a new artifact and protects the shared file."""
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    shared_file = models_dir / "shared_base.gguf"
    shared_file.write_bytes(b"SHARED_ORIGINAL_BYTES")

    staged_dir = tmp_path / "staged"
    staged_dir.mkdir(parents=True, exist_ok=True)
    staged_file = staged_dir / "new_variant_v2.gguf"
    staged_file.write_bytes(b"NEW_VARIANT_V2_BYTES")

    conn = test_db.get_connection()
    shared_art_id = "art_shared_base"
    conn.execute(
        """
        INSERT INTO storage_artifacts (
            artifact_id, physical_path, bytes, sha256, is_managed,
            install_scope, management_mode, deletion_method, ref_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (shared_art_id, str(shared_file), len(b"SHARED_ORIGINAL_BYTES"), "sha_shared", 1, "shared", "managed", "direct_file_delete", 2, "2026-10-17T00:00:00Z"),
    )

    # Two installations sharing this artifact
    conn.execute(
        """
        INSERT INTO installations (
            installation_id, model_id, name, runtime_name, artifact_ids_json,
            readiness_stage, compatibility_status, update_status, ownership_class,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "inst-shared-1", "model-shared", "App 1", "llamacpp", json.dumps([shared_art_id]), "verified", "compatible", "v1.0.0", "owned_child", "2026-10-17T00:00:00Z", "2026-10-17T00:00:00Z",
            "inst-shared-2", "model-shared", "App 2", "llamacpp", json.dumps([shared_art_id]), "verified", "compatible", "v1.0.0", "owned_child", "2026-10-17T00:00:00Z", "2026-10-17T00:00:00Z",
        ),
    )
    conn.commit()

    workflow = UpdateWorkflow(db=test_db, data_dir=tmp_path)

    # Update only inst-shared-1
    res = workflow.apply_update(
        "inst-shared-1",
        target_version="v2.0.0",
        staged_source_path=staged_file,
        verify_callback=lambda p: True,
    )

    assert res["success"] is True
    assert res["updated"] is True
    assert res["version"] == "v2.0.0"

    # The shared base file MUST remain untouched!
    assert shared_file.read_bytes() == b"SHARED_ORIGINAL_BYTES"

    # Installation 2 still points to the shared artifact and is still at v1.0.0
    inst2_row = conn.execute("SELECT * FROM installations WHERE installation_id = 'inst-shared-2'").fetchone()
    assert inst2_row["update_status"] == "v1.0.0"
    assert json.loads(inst2_row["artifact_ids_json"]) == [shared_art_id]

    # Shared artifact ref_count is decremented to 1
    shared_art_row = conn.execute("SELECT ref_count FROM storage_artifacts WHERE artifact_id = ?", (shared_art_id,)).fetchone()
    assert shared_art_row["ref_count"] == 1

    # Installation 1 now points to a new isolated artifact
    inst1_row = conn.execute("SELECT * FROM installations WHERE installation_id = 'inst-shared-1'").fetchone()
    assert inst1_row["update_status"] == "up_to_date"
    new_art_ids = json.loads(inst1_row["artifact_ids_json"])
    assert new_art_ids != [shared_art_id]

    new_art_row = conn.execute("SELECT * FROM storage_artifacts WHERE artifact_id = ?", (new_art_ids[0],)).fetchone()
    assert new_art_row is not None
    assert Path(new_art_row["physical_path"]).read_bytes() == b"NEW_VARIANT_V2_BYTES"
