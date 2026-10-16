"""Tests for Observability, event envelopes, secret redaction, audit checkpoints, and security."""

import os
import tempfile
from pathlib import Path
import pytest

from localpilot.observability.context import operation_context, get_operation_id
from localpilot.observability.events import ObservabilityHub, AuditCheckpointFailureError
from localpilot.observability.export import generate_support_bundle_dict
from localpilot.observability.redaction import redact_string, redact_data
from localpilot.observability.schema import EventCategory, EventLevel
from localpilot.security import validate_path_within_root, validate_localhost_endpoint, SecurityViolationError
from localpilot.storage.db import DatabaseManager


def test_redaction_secrets_and_home() -> None:
    hf_token = "hf_abcdef1234567890abcdef1234567890ab"
    bearer_token = "Bearer 1234567890abcdef12345678"
    home = os.path.expanduser("~")

    raw = f"Token is {hf_token} and {bearer_token} in path {home}/secret/models"
    redacted = redact_string(raw)

    assert hf_token not in redacted
    assert "hf_***[REDACTED]***" in redacted
    assert bearer_token not in redacted
    assert "***[REDACTED]***" in redacted
    assert home not in redacted
    assert "~/secret/models" in redacted


def test_redaction_dictionary_structures() -> None:
    data = {
        "user_prompt": "What is my bank account password?",
        "api_key": "super_secret_key_12345",
        "nested": {
            "token": "secret_token",
            "normal_field": "public_model_info",
        },
    }
    redacted = redact_data(data)
    assert redacted["user_prompt"] == "***[REDACTED]***"
    assert redacted["api_key"] == "***[REDACTED]***"
    assert redacted["nested"]["token"] == "***[REDACTED]***"
    assert redacted["nested"]["normal_field"] == "public_model_info"


def test_event_envelope_and_rotation(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "state.db")
    hub = ObservabilityHub(db=db, log_dir=tmp_path / "logs", max_log_size_bytes=500)

    with operation_context() as op_id:
        assert get_operation_id() == op_id
        envelope = hub.emit_event(
            category=EventCategory.APPLICATION,
            event_name="app.started",
            message="LocalPilot initialized successfully",
            details={"config": "default"},
        )
        assert envelope.operation_id == op_id
        assert envelope.schema_version == 1
        assert envelope.timestamp.endswith("Z")
        assert envelope.category == EventCategory.APPLICATION

    # Test file rotation by writing multiple events
    for i in range(25):
        hub.emit_event(
            category=EventCategory.MONITORING,
            event_name="sample.metric",
            message=f"Sample message {i} with padding text to trigger log file rotation over bytes limit",
        )

    log_files = list((tmp_path / "logs").glob("events.jsonl*"))
    assert len(log_files) > 1  # Confirms rotation occurred


def test_audit_checkpoint_failure_blocks_destructive_action(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "state.db")
    hub = ObservabilityHub(db=db, log_dir=tmp_path / "logs")

    # Corrupt DB connection to simulate failure
    db.get_connection().close()
    class BrokenDB(DatabaseManager):
        def _init_schema(self) -> None:
            pass

        def get_connection(self):
            raise RuntimeError("Disk full / I/O error")

    broken_hub = ObservabilityHub(db=BrokenDB(tmp_path / "broken.db"), log_dir=tmp_path / "logs")

    with pytest.raises(AuditCheckpointFailureError) as exc_info:
        broken_hub.record_audit_checkpoint(
            decision_type="model_deletion",
            target_id="model-123",
            payload={"files": ["/data/model.gguf"]},
            outcome="approved",
            is_critical=True,
        )
    assert "CRITICAL" in str(exc_info.value)


def test_security_path_validation(tmp_path: Path) -> None:
    allowed_root = tmp_path / "models"
    allowed_root.mkdir()
    valid_file = allowed_root / "test.gguf"
    valid_file.touch()

    # Valid path inside root
    res = validate_path_within_root(valid_file, allowed_root)
    assert res == valid_file.resolve()

    # Path traversal attack
    evil_path = allowed_root / ".." / "evil.txt"
    with pytest.raises(SecurityViolationError):
        validate_path_within_root(evil_path, allowed_root)


def test_security_localhost_endpoint() -> None:
    assert validate_localhost_endpoint("http://127.0.0.1:11434") is True
    assert validate_localhost_endpoint("http://localhost:8080/health") is True
    assert validate_localhost_endpoint("http://[::1]:1234") is True

    with pytest.raises(SecurityViolationError):
        validate_localhost_endpoint("https://remote-server.evil.com/api")


def test_support_bundle_redaction(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "state.db")
    bundle = generate_support_bundle_dict(db=db)
    assert "system" in bundle
    assert "os" in bundle["system"]
    assert "recent_activities" in bundle
