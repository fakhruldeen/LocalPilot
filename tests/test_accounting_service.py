"""Unit tests for ApiAccountingService, privacy standard, and metric calculations."""

import tempfile
from pathlib import Path
import pytest

from localpilot.application.accounting_service import ApiAccountingService
from localpilot.storage.db import DatabaseManager


@pytest.fixture
def temp_accounting():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "test_acct.db"
        db = DatabaseManager(db_file)
        # Pre-insert valid keys for foreign key references
        conn = db.get_connection()
        with conn:
            conn.execute(
                "INSERT INTO api_keys (key_id, name, prefix, hashed_secret, created_at, policy_json) "
                "VALUES ('k-1', 'Key 1', 'lp_1...', 'hash1', '2026-01-01T00:00:00Z', '{}'), "
                "       ('k-2', 'Key 2', 'lp_2...', 'hash2', '2026-01-01T00:00:00Z', '{}')"
            )
        yield ApiAccountingService(db)


def test_record_and_summary(temp_accounting):
    # Empty summary
    empty_summary = temp_accounting.get_usage_summary()
    assert empty_summary["total_requests"] == 0

    # Record 2 requests
    req_id_1 = temp_accounting.record_request(
        key_id="k-1",
        model_alias="coding",
        resolved_version_id="ver-1",
        prompt_tokens=20,
        completion_tokens=40,
        latency_ms=500.0,
        first_token_latency_ms=100.0,
        status_code=200,
        app_name="opencode",
    )
    assert req_id_1.startswith("req-")

    req_id_2 = temp_accounting.record_request(
        key_id="k-2",
        model_alias="writing",
        resolved_version_id="ver-2",
        prompt_tokens=10,
        completion_tokens=20,
        latency_ms=300.0,
        first_token_latency_ms=80.0,
        status_code=500,
        error_code="inference_failed",
        app_name="vscode",
    )
    assert req_id_2.startswith("req-")

    # Overall summary
    summary = temp_accounting.get_usage_summary()
    assert summary["total_requests"] == 2
    assert summary["successful_requests"] == 1
    assert summary["error_requests"] == 1
    assert summary["total_prompt_tokens"] == 30
    assert summary["total_completion_tokens"] == 60
    assert summary["total_tokens"] == 90
    assert summary["avg_latency_ms"] == 400.0
    assert summary["avg_first_token_latency_ms"] == 90.0
    assert summary["tokens_per_second"] > 0


def test_per_model_and_key_breakdowns(temp_accounting):
    temp_accounting.record_request(
        key_id="k-1",
        model_alias="coding",
        resolved_version_id="ver-1",
        prompt_tokens=50,
        completion_tokens=50,
        latency_ms=200.0,
        status_code=200,
    )
    temp_accounting.record_request(
        key_id="k-1",
        model_alias="coding",
        resolved_version_id="ver-1",
        prompt_tokens=30,
        completion_tokens=20,
        latency_ms=100.0,
        status_code=200,
    )

    models = temp_accounting.get_per_model_breakdown()
    assert len(models) == 1
    assert models[0]["model_alias"] == "coding"
    assert models[0]["request_count"] == 2
    assert models[0]["total_tokens"] == 150

    keys = temp_accounting.get_per_key_breakdown()
    assert len(keys) == 1
    assert keys[0]["key_id"] == "k-1"
    assert keys[0]["request_count"] == 2
