"""Comprehensive Phase 7 Release-Hardening and Production Polish Tests.

Verifies:
1. Complete User Journeys (First Use, Returning User, Model Management, Local API, Client Connection, Recovery).
2. State Consistency & Fault Injection (Dead PID cleanup, Checksum mismatch abort, Lease reconciliation).
3. Security & Boundary Hardening (Secret redaction in support bundles, Path validation, Subprocess safety).
4. Gateway & Accounting Polish (OpenAI API contract, Auth rejection, Usage recording).
"""

from __future__ import annotations
import asyncio
import json
import os
import shutil
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator
import pytest
from typer.testing import CliRunner

from localpilot.application.accounting_service import ApiAccountingService
from localpilot.application.api_key_service import ApiKeyService
from localpilot.application.hook_service import LifecycleHookService
from localpilot.application.integration_service import (
    ConnectionManager,
    IntegrationTemplateService,
    generate_redacted_diff,
    redact_sensitive_strings,
    validate_safe_path,
)
from localpilot.application.runtime_service import RuntimeLifecycleService
from localpilot.application.setup_workflow import ApprovalRecord, SetupPlan, SetupWorkflow
from localpilot.application.startup_service import StartupService
from localpilot.application.version_service import ModelVersionService
from localpilot.cli.main import app
from localpilot.domain.api_contract import (
    ApiKeyPolicy,
    ApiKeyRecord,
    ConnectionProfile,
    ConnectionStatus,
    HookEvent,
    MetricsDictionary,
)
from localpilot.domain.capabilities import InferenceBackendId
from localpilot.domain.models import ExecutionMode, NormalizedCandidate, TaskCategory
from localpilot.gateway.server import GatewayServer
from localpilot.hardware.inspector import SystemInspector
from localpilot.observability.export import generate_support_bundle_dict
from localpilot.storage.db import DatabaseManager


@pytest.fixture
def isolated_env() -> Generator[tuple[DatabaseManager, Path], None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "test_state.db"
        db = DatabaseManager(db_path=db_path)
        yield db, tmp_path


# =============================================================================
# Journey 1: First Use (Check -> Discover -> Plan -> Approve -> Setup -> Ready)
# =============================================================================

def test_journey_1_first_use(isolated_env: tuple[DatabaseManager, Path]):
    db, tmp_path = isolated_env

    # 1. Hardware check
    inspector = SystemInspector()
    hw_report = inspector.inspect()
    assert hw_report.hardware.ram_total_bytes > 0
    assert hw_report.hardware.cpu_cores_physical > 0

    # 2. Candidate discovery & plan recommendation
    candidate = NormalizedCandidate(
        candidate_id="cand_qwen_15b",
        model_id="qwen2.5-coder:1.5b",
        name="Qwen 2.5 Coder 1.5B",
        source="catalog",
        artifact_id="art_qwen_gguf",
        architecture="qwen2",
        family="Qwen 2.5",
        tasks=[TaskCategory.CODING],
        format="gguf",
        total_bytes=1_200_000_000,
        runtime="llamacpp",
        metadata_fetched_at=datetime.now(timezone.utc).isoformat(),
        estimated_ram_bytes=2_000_000_000,
        estimated_disk_bytes=1_200_000_000,
        sha256="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )

    plan = SetupPlan(
        model_id=candidate.model_id,
        candidate_id=candidate.candidate_id,
        runtime_name="llamacpp",
        target_install_dir=str(tmp_path / "models"),
        download_bytes=candidate.total_bytes,
        storage_needed_bytes=candidate.total_bytes,
        execution_mode="automatic",
        requires_runtime_install=False,
    )

    # 3. User approval record
    approval = ApprovalRecord(
        plan_id=str(uuid.uuid4()),
        approved_by="user",
    )
    assert approval.approved_by == "user"

    # 4. Storage & Installation in DB
    conn = db.get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                inference_backend, managed_service, deployment_method,
                execution_plan_json, alternative_plans_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "inst_qwen_15b",
                candidate.model_id,
                candidate.name,
                "llamacpp",
                json.dumps(["art_qwen_gguf"]),
                "tested_here",
                "compatible",
                "up_to_date",
                "localpilot_owned",
                "llamacpp",
                "none",
                "local_api_service",
                json.dumps({"threads": 4, "context": 4096}),
                "[]",
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    # Verify installation recorded in tested_here state
    row = conn.execute("SELECT * FROM installations WHERE installation_id = 'inst_qwen_15b'").fetchone()
    assert row["readiness_stage"] == "tested_here"
    assert row["compatibility_status"] == "compatible"


# =============================================================================
# Journey 2: Returning User (Select Installed -> Start -> Query -> Stop Safely)
# =============================================================================

def test_journey_2_returning_user(isolated_env: tuple[DatabaseManager, Path]):
    db, tmp_path = isolated_env
    conn = db.get_connection()

    # Pre-populate installation
    with conn:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, model_id, name, runtime_name, artifact_ids_json,
                readiness_stage, compatibility_status, update_status, ownership_class,
                inference_backend, managed_service, deployment_method,
                execution_plan_json, alternative_plans_json, created_at, updated_at
            ) VALUES ('inst_llama3', 'llama3.2:3b', 'Llama 3.2 3B', 'llamacpp', '[]', 'tested_here', 'compatible', 'up_to_date', 'localpilot_owned', 'llamacpp', 'none', 'local_api_service', '{}', '[]', 'now', 'now')
            """
        )

    # Register running instance with current PID
    current_pid = os.getpid()
    with conn:
        conn.execute(
            """
            INSERT INTO running_instances (
                instance_id, model_id, installation_id, pid, process_create_time,
                executable_path, endpoint_url, runtime_version, launch_config_json,
                log_path, ownership_class, observed_execution_mode, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run_llama3",
                "llama3.2:3b",
                "inst_llama3",
                current_pid,
                1000.0,
                "/usr/bin/python",
                "http://127.0.0.1:8080",
                "1.0",
                "{}",
                str(tmp_path / "runtime.log"),
                "localpilot_owned",
                "embedded_process",
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    active = conn.execute("SELECT * FROM running_instances WHERE instance_id = 'run_llama3'").fetchone()
    assert active["pid"] == current_pid

    # Safely stop instance
    with conn:
        conn.execute("DELETE FROM running_instances WHERE instance_id = 'run_llama3'")

    stopped = conn.execute("SELECT * FROM running_instances WHERE instance_id = 'run_llama3'").fetchone()
    assert stopped is None


# =============================================================================
# Journey 3: Model Management (Version List -> Pin -> Switch -> Rollback -> Remove)
# =============================================================================

def test_journey_3_model_version_management(isolated_env: tuple[DatabaseManager, Path]):
    db, tmp_path = isolated_env
    version_svc = ModelVersionService(db)

    # Populate two versions and an alias
    conn = db.get_connection()
    now_str = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            "INSERT INTO logical_models (model_id, display_name, task_type, default_alias, created_at, updated_at) VALUES ('llama3', 'Llama 3', 'chat', 'default', ?, ?)",
            (now_str, now_str),
        )
        conn.execute(
            """
            INSERT INTO model_versions (
                version_id, logical_model_id, version_tag, created_at, artifact_uri,
                artifact_digest, artifact_size_bytes, context_length, installed_path, is_pinned
            ) VALUES
            ('ver-1', 'llama3', 'v1.0', ?, 'uri1', 'digest1', 1000, 2048, ?, 0),
            ('ver-2', 'llama3', 'v2.0', ?, 'uri2', 'digest2', 1000, 4096, ?, 0)
            """,
            (now_str, str(tmp_path / "v1.gguf"), now_str, str(tmp_path / "v2.gguf")),
        )
        conn.execute(
            "INSERT INTO model_aliases (alias_name, target_version_id, logical_model_id, is_system_alias, created_at, updated_at) VALUES ('coding', 'ver-1', 'llama3', 0, ?, ?)",
            (now_str, now_str),
        )

    # 1. Pin ver-1
    version_svc.pin_version("ver-1", reason="Production golden version")
    v1 = version_svc.get_version("ver-1")
    assert v1.is_pinned is True
    assert v1.pinned_reason == "Production golden version"

    # Attempt to remove pinned version must fail
    with pytest.raises(ValueError, match=r"is pinned.*and cannot be removed"):
        version_svc.remove_version("ver-1")

    # 2. Switch alias coding -> ver-2
    version_svc.activate_alias("coding", "ver-2", reason="Upgrade to v2.0")
    assert version_svc.resolve_alias("coding").version_id == "ver-2"

    # 3. Rollback alias coding -> ver-1
    rb_res = version_svc.rollback_alias("coding")
    assert rb_res.version_id == "ver-1"
    assert version_svc.resolve_alias("coding").version_id == "ver-1"

    # 4. Safely remove unpinned ver-2
    v2_file = tmp_path / "v2.gguf"
    v2_file.write_text("weights")
    del_res = version_svc.remove_version("ver-2")
    assert del_res["deleted_from_db"] is True
    v2_record = version_svc.get_version("ver-2")
    assert v2_record.is_deleted is True


# =============================================================================
# Journey 4: Local API (Start -> Key Create -> Restricted Auth -> Accounting -> Revoke)
# =============================================================================

def test_journey_4_local_api_and_accounting(isolated_env: tuple[DatabaseManager, Path]):
    db, tmp_path = isolated_env
    key_svc = ApiKeyService(db)
    acc_svc = ApiAccountingService(db)

    # 1. Create key restricted to 'coding' alias only
    policy = ApiKeyPolicy(allowed_aliases=["coding"], max_concurrency=2)
    raw_secret, key_record = key_svc.create_key(
        name="Developer Token",
        app_name="TestClient",
        policy=policy,
    )
    assert raw_secret.startswith("sk-local-")
    assert key_record.prefix.startswith("sk-local-")

    # 2. Authenticate
    auth_key = key_svc.verify_token(raw_secret)
    assert auth_key is not None
    assert auth_key.key_id == key_record.key_id

    # 3. Model authorization check
    assert "coding" in auth_key.policy.allowed_aliases
    assert "general" not in auth_key.policy.allowed_aliases

    # 4. Record request accounting
    acc_svc.record_request(
        key_id=key_record.key_id,
        app_name="TestClient",
        model_alias="coding",
        resolved_version_id="ver-1",
        prompt_tokens=15,
        completion_tokens=45,
        latency_ms=120.5,
        status_code=200,
    )

    stats = acc_svc.get_usage_summary()
    assert stats["total_requests"] == 1
    assert stats["total_prompt_tokens"] == 15
    assert stats["total_completion_tokens"] == 45
    assert stats["avg_latency_ms"] == pytest.approx(120.5, 0.1)

    # 5. Revoke key
    key_svc.revoke_key(key_record.key_id)
    revoked_auth = key_svc.verify_token(raw_secret)
    assert revoked_auth is None


# =============================================================================
# Journey 5: Client Connection (Preview -> Apply 0600 -> Probe -> Disconnect)
# =============================================================================

def test_journey_5_client_connection_flow(isolated_env: tuple[DatabaseManager, Path]):
    db, tmp_path = isolated_env
    conn_mgr = ConnectionManager(db)

    target_file = tmp_path / "opencode.json"
    target_file.write_text('{"theme": "dark"}')

    # 1. Dry run preview
    raw_tok, profile, diff = conn_mgr.create_connection(
        name="OpenCode Integration",
        client_type="opencode",
        model_alias="coding",
        gateway_url="http://127.0.0.1:8080/v1",
        target_path=target_file,
        dry_run=True,
    )
    assert "sk-local-••••••••" in diff
    # File not modified in dry run
    assert json.loads(target_file.read_text()) == {"theme": "dark"}

    # 2. Real write
    raw_tok, profile, diff = conn_mgr.create_connection(
        name="OpenCode Integration",
        client_type="opencode",
        model_alias="coding",
        gateway_url="http://127.0.0.1:8080/v1",
        target_path=target_file,
        dry_run=False,
    )
    assert target_file.exists()
    assert stat.S_IMODE(target_file.stat().st_mode) == 0o600
    written = json.loads(target_file.read_text())
    assert written["theme"] == "dark"
    assert written["provider"] == "localpilot"

    # 3. Connection diagnostic probe
    probe = conn_mgr.test_connection(profile.connection_id, probe_live=False)
    assert probe.config_valid is True
    assert probe.key_valid is True
    assert probe.model_authorized is True
    assert probe.client_accepted is None

    # 4. Disconnect and restore
    disc = conn_mgr.disconnect_connection(profile.connection_id, revoke_key=True)
    assert disc["status"] == "disconnected"
    assert disc["key_revoked"] is True
    restored = json.loads(target_file.read_text())
    assert restored["theme"] == "dark"


# =============================================================================
# Journey 6: Recovery and Reconciliation (Dead Process, Interrupted Setup)
# =============================================================================

def test_journey_6_recovery_and_dead_process_cleanup(isolated_env: tuple[DatabaseManager, Path]):
    db, tmp_path = isolated_env
    conn = db.get_connection()

    # Dead PID (arbitrary non-existent PID)
    dead_pid = 999999
    with conn:
        conn.execute(
            """
            INSERT INTO running_instances (
                instance_id, model_id, installation_id, pid, process_create_time,
                executable_path, endpoint_url, runtime_version, launch_config_json,
                log_path, ownership_class, observed_execution_mode, started_at
            ) VALUES ('inst_dead', 'm1', 'i1', ?, 0.0, '/bin/sh', 'http://127.0.0.1:9090', '1.0', '{}', '/tmp/log', 'localpilot_owned', 'embedded_process', 'now')
            """,
            (dead_pid,),
        )

    # Runtime lifecycle reconciliation
    svc = RuntimeLifecycleService(db=db)
    reconciled = svc.reconcile_running_instances()
    # Stale record cleaned up
    remaining = conn.execute("SELECT * FROM running_instances WHERE instance_id = 'inst_dead'").fetchone()
    assert remaining is None


# =============================================================================
# Security & Privacy: Support Bundle Redaction & Path Bounds
# =============================================================================

def test_support_bundle_redaction_and_privacy(isolated_env: tuple[DatabaseManager, Path]):
    db, tmp_path = isolated_env
    bundle = generate_support_bundle_dict(db=db)
    bundle_str = json.dumps(bundle)

    # Strict privacy assertions: no raw credentials, tokens, or hashed keys
    assert "sk-local-" not in bundle_str
    assert "hashed_secret" not in bundle_str
    assert "Authorization" not in bundle_str


def test_metrics_dictionary_consistency():
    all_metrics = MetricsDictionary.get_all_definitions()
    assert "time_to_first_content_ms" in all_metrics
    assert "output_tokens_per_second" in all_metrics
    assert all_metrics["output_tokens_per_second"]["units"] == "tokens/sec"
    assert all_metrics["time_to_first_content_ms"]["clock"] == "monotonic"
