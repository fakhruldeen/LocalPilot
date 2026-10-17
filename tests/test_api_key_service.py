"""Unit tests for ApiKeyService, credential storage, and constant-time validation."""

import tempfile
from pathlib import Path
import pytest

from localpilot.application.api_key_service import ApiKeyService
from localpilot.domain.api_contract import ApiKeyPolicy
from localpilot.storage.db import DatabaseManager


@pytest.fixture
def temp_service():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "test_keys.db"
        db = DatabaseManager(db_file)
        yield ApiKeyService(db)


def test_create_and_verify_key(temp_service):
    raw_token, record = temp_service.create_key(
        name="VS Code Key",
        app_name="vscode",
    )

    assert raw_token.startswith("sk-local-")
    assert record.display_name == "VS Code Key"
    assert record.app_name == "vscode"
    assert record.is_active is True

    # Verification success
    verified = temp_service.verify_token(raw_token)
    assert verified is not None
    assert verified.key_id == record.key_id

    # Verification failure on bad token
    assert temp_service.verify_token("sk-local-badtoken12345") is None
    assert temp_service.verify_token("") is None


def test_list_and_get_keys(temp_service):
    _, k1 = temp_service.create_key("Key 1", app_name="app1")
    _, k2 = temp_service.create_key("Key 2", app_name="app2")

    keys = temp_service.list_keys()
    assert len(keys) == 2
    assert any(k.key_id == k1.key_id for k in keys)
    assert any(k.key_id == k2.key_id for k in keys)

    fetched = temp_service.get_key(k1.key_id)
    assert fetched is not None
    assert fetched.display_name == "Key 1"
    assert fetched.app_name == "app1"


def test_revoke_and_delete_key(temp_service):
    raw_token, record = temp_service.create_key("Temporary Key")

    # Verify active
    assert temp_service.verify_token(raw_token) is not None

    # Revoke
    res = temp_service.revoke_key(record.key_id)
    assert res is True

    # Token must now fail verification
    assert temp_service.verify_token(raw_token) is None

    fetched = temp_service.get_key(record.key_id)
    assert fetched.is_active is False
    assert fetched.revoked_at is not None

    # Delete
    del_res = temp_service.delete_key(record.key_id)
    assert del_res is True
    assert temp_service.get_key(record.key_id) is None
