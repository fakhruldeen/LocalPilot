"""Tests for Phase 6: Guided application connections, safe config export, startup service, and lifecycle hooks."""

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Generator
import pytest
from typer.testing import CliRunner

from localpilot.application.api_key_service import ApiKeyService
from localpilot.application.hook_service import LifecycleHookService
from localpilot.application.integration_service import (
    ConnectionManager,
    IntegrationTemplateService,
    generate_redacted_diff,
    redact_sensitive_strings,
    validate_safe_path,
)
from localpilot.application.startup_service import StartupService
from localpilot.cli.main import app
from localpilot.domain.api_contract import (
    ApiKeyPolicy,
    ConnectionProfile,
    ConnectionStatus,
    HookEvent,
)
from localpilot.storage.db import DatabaseManager


@pytest.fixture
def temp_db() -> Generator[DatabaseManager, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_state.db"
        db = DatabaseManager(db_path=db_path)
        yield db


def test_redact_sensitive_strings_and_diff():
    secret_key = "sk-local-abcdef1234567890abcdef1234567890"
    bearer_token = "Bearer secret-bearer-token"
    sample_text = f"apiKey: {secret_key}\nAuthorization: {bearer_token}"
    redacted = redact_sensitive_strings(sample_text)
    assert secret_key not in redacted
    assert "sk-local-••••••••" in redacted
    assert "Bearer ••••••••" in redacted

    old_cfg = '{\n  "apiKey": "sk-local-oldtoken123456789",\n  "model": "chat"\n}'
    new_cfg = '{\n  "apiKey": "sk-local-newtoken987654321",\n  "model": "coding"\n}'
    diff = generate_redacted_diff(old_cfg, new_cfg)
    assert "sk-local-oldtoken" not in diff
    assert "sk-local-newtoken" not in diff
    assert "sk-local-••••••••" in diff
    assert '-  "model": "chat"' in diff
    assert '+  "model": "coding"' in diff


def test_validate_safe_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        safe_file = Path(tmpdir) / "opencode.json"
        validated = validate_safe_path(safe_file)
        assert validated == safe_file

    with pytest.raises(ValueError, match="sensitive system location"):
        validate_safe_path("/etc/shadow")

    with pytest.raises(ValueError, match="sensitive system location"):
        validate_safe_path("/proc/cpuinfo")


def test_safe_config_export_malformed_protection_and_mode(tmp_path: Path):
    target_file = tmp_path / "opencode.json"
    target_file.write_text("invalid json content {{{")

    with pytest.raises(ValueError, match="Refusing to overwrite"):
        IntegrationTemplateService.apply_opencode_config(
            target_path=target_file,
            base_url="http://127.0.0.1:8080/v1",
            api_key="sk-local-testkey123",
            model_alias="coding",
        )

    # Valid initial file
    target_file.write_text('{"existing_setting": true}')
    res = IntegrationTemplateService.apply_opencode_config(
        target_path=target_file,
        base_url="http://127.0.0.1:8080/v1",
        api_key="sk-local-testkey123",
        model_alias="coding",
    )
    assert Path(res["target_path"]).exists()
    assert Path(res["backup_path"]).exists()

    # Verify 0600 file mode
    mode = stat.S_IMODE(target_file.stat().st_mode)
    assert mode == 0o600

    data = json.loads(target_file.read_text())
    assert data["existing_setting"] is True
    assert data["provider"] == "localpilot"
    assert data["model"] == "coding"


def test_connection_manager_lifecycle_and_disconnect(temp_db: DatabaseManager, tmp_path: Path):
    mgr = ConnectionManager(temp_db)

    # 1. Create OpenCode connection
    target_path = tmp_path / "opencode.json"
    target_path.write_text('{"custom_user_option": 42}')

    raw_token, profile, diff = mgr.create_connection(
        name="Developer OpenCode",
        client_type="opencode",
        model_alias="coding",
        gateway_url="http://127.0.0.1:8080/v1",
        target_path=target_path,
    )

    assert raw_token.startswith("sk-local-")
    assert profile.name == "Developer OpenCode"
    assert profile.status == ConnectionStatus.ACTIVE
    assert profile.key_id.startswith("lp_key_")
    assert "sk-local-••••••••" in diff

    # Verify listed
    conns = mgr.list_connections()
    assert len(conns) == 1
    assert conns[0].connection_id == profile.connection_id

    # 2. Test disconnect with key revocation
    disc_res = mgr.disconnect_connection(profile.connection_id, revoke_key=True)
    assert disc_res["status"] == "disconnected"
    assert disc_res["key_revoked"] is True

    # Check key is revoked
    key_record = mgr.api_keys.get_key(profile.key_id)
    assert key_record.is_active is False

    # Check target file restored custom option
    restored_data = json.loads(target_path.read_text())
    assert restored_data["custom_user_option"] == 42


def test_continue_config_safe_merge_and_disconnect(temp_db: DatabaseManager, tmp_path: Path):
    mgr = ConnectionManager(temp_db)
    continue_path = tmp_path / "continue_config.json"
    initial_content = {
        "models": [
            {"title": "OpenAI Cloud GPT-4", "provider": "openai", "model": "gpt-4"}
        ]
    }
    continue_path.write_text(json.dumps(initial_content))

    _, profile, _ = mgr.create_connection(
        name="VS Code Continue",
        client_type="continue",
        model_alias="coding",
        gateway_url="http://127.0.0.1:8080/v1",
        target_path=continue_path,
    )

    # Verify both models exist
    merged = json.loads(continue_path.read_text())
    assert len(merged["models"]) == 2
    assert merged["models"][0]["title"] == "OpenAI Cloud GPT-4"
    assert "LocalPilot" in merged["models"][1]["title"]

    # Disconnect
    mgr.disconnect_connection(profile.connection_id, revoke_key=False)

    # Verify OpenAI model preserved and LocalPilot model removed
    after_disc = json.loads(continue_path.read_text())
    assert len(after_disc["models"]) == 1
    assert after_disc["models"][0]["title"] == "OpenAI Cloud GPT-4"


def test_connection_verification_probe_reporting(temp_db: DatabaseManager, tmp_path: Path):
    mgr = ConnectionManager(temp_db)
    target_path = tmp_path / "opencode.json"

    _, profile, _ = mgr.create_connection(
        name="Test App",
        client_type="opencode",
        model_alias="coding",
        gateway_url="http://127.0.0.1:8080/v1",
        target_path=target_path,
    )

    # Test probe
    probe = mgr.test_connection(profile.connection_id, probe_live=False)
    assert probe.config_valid is True
    assert probe.key_valid is True
    assert probe.model_authorized is True
    assert probe.client_accepted is None
    assert "External application acceptance must be verified" in probe.details["client_acceptance"]


def test_startup_service_preview_and_status(tmp_path: Path):
    svc_linux = StartupService(platform_name="linux")
    prev_linux = svc_linux.preview()
    assert prev_linux["platform"] == "linux"
    assert "systemd/user/localpilot-gateway.service" in prev_linux["target_path"]
    assert "[Unit]" in prev_linux["content"]
    assert "[Service]" in prev_linux["content"]
    assert "ExecStart=" in prev_linux["content"]

    svc_mac = StartupService(platform_name="darwin")
    prev_mac = svc_mac.preview()
    assert prev_mac["platform"] == "darwin"
    assert "LaunchAgents/com.localpilot.gateway.plist" in prev_mac["target_path"]
    assert "<key>Label</key>" in prev_mac["content"]

    status = svc_linux.get_status()
    assert "installed" in status
    assert "platform" in status


def test_lifecycle_hook_sanitization_and_isolation(temp_db: DatabaseManager):
    hook_svc = LifecycleHookService(temp_db)

    # 1. Event sanitization
    dirty_data = {
        "model_alias": "coding",
        "api_key": "sk-local-secret123",
        "prompt": "write malicious code",
        "authorization": "Bearer secrettoken",
        "safe_metric": 42,
    }
    event = HookEvent(event_type="model.ready", data=dirty_data)
    assert "api_key" not in event.data
    assert "prompt" not in event.data
    assert "authorization" not in event.data
    assert event.data["model_alias"] == "coding"
    assert event.data["safe_metric"] == 42

    # 2. Register hook
    hook = hook_svc.register_hook(
        name="Echo Test",
        event_type="model.ready",
        command=[sys.executable, "-c", "import sys; print('Hook output:', sys.stdin.read()[:50])"],
        approved_by_user=False,
    )
    assert hook.is_enabled is False

    # Cannot enable without approval
    with pytest.raises(ValueError, match="cannot be enabled without explicit user approval"):
        hook_svc.enable_hook(hook.hook_id, approved=False)

    # Enable with approval
    enabled_hook = hook_svc.enable_hook(hook.hook_id, approved=True)
    assert enabled_hook.is_enabled is True
    assert enabled_hook.approved_by_user is True

    # Execute
    res = hook_svc.execute_hook(enabled_hook, event)
    assert res.status == "success"
    assert res.exit_code == 0
    assert "Hook output:" in res.output_summary


def test_lifecycle_hook_timeout_and_circuit_breaker(temp_db: DatabaseManager):
    hook_svc = LifecycleHookService(temp_db)

    # Failing hook
    failing_hook = hook_svc.register_hook(
        name="Failing Hook",
        event_type="test.fail",
        command=[sys.executable, "-c", "import sys; sys.exit(1)"],
        approved_by_user=True,
    )
    failing_hook = hook_svc.enable_hook(failing_hook.hook_id, approved=True)
    event = HookEvent(event_type="test.fail", data={"attempt": 1})

    # Run 1: failure 1
    r1 = hook_svc.execute_hook(failing_hook, event)
    assert r1.status == "failed"
    h1 = hook_svc.get_hook(failing_hook.hook_id)
    assert h1.consecutive_failures == 1
    assert h1.is_tripped is False

    # Run 2: failure 2
    r2 = hook_svc.execute_hook(h1, event)
    assert r2.status == "failed"
    h2 = hook_svc.get_hook(failing_hook.hook_id)
    assert h2.consecutive_failures == 2
    assert h2.is_tripped is False

    # Run 3: failure 3 -> trips circuit breaker
    r3 = hook_svc.execute_hook(h2, event)
    assert r3.status == "failed"
    h3 = hook_svc.get_hook(failing_hook.hook_id)
    assert h3.consecutive_failures == 3
    assert h3.is_tripped is True

    # Run 4: execution refused due to tripped circuit breaker
    r4 = hook_svc.execute_hook(h3, event)
    assert r4.status == "circuit_broken"
    assert "Execution refused" in r4.output_summary

    # Reset circuit breaker
    h_reset = hook_svc.reset_circuit_breaker(h3.hook_id)
    assert h_reset.is_tripped is False
    assert h_reset.consecutive_failures == 0


def test_cli_automation_commands(temp_db: DatabaseManager, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("localpilot.cli.main.get_db", lambda *args, **kwargs: temp_db)
    monkeypatch.setattr("localpilot.application.integration_service.get_db", lambda *args, **kwargs: temp_db)
    monkeypatch.setattr("localpilot.application.api_key_service.get_db", lambda *args, **kwargs: temp_db)
    monkeypatch.setattr("localpilot.application.hook_service.get_db", lambda *args, **kwargs: temp_db)

    runner = CliRunner()

    # 1. localpilot connect dry-run JSON
    res_conn = runner.invoke(app, ["connect", "opencode", "--dry-run", "--json"])
    assert res_conn.exit_code == 0
    data_conn = json.loads(res_conn.output)
    assert data_conn["preview_only"] is True
    assert "sk-local-••••••••" in data_conn["diff"]

    # 2. localpilot startup preview JSON
    res_start = runner.invoke(app, ["startup", "preview", "--json"])
    assert res_start.exit_code == 0
    data_start = json.loads(res_start.output)
    assert data_start["supported"] is True

    # 3. localpilot connections list JSON
    res_conns = runner.invoke(app, ["connections", "list", "--json"])
    assert res_conns.exit_code == 0
    assert json.loads(res_conns.output) == []

    # 4. localpilot hooks list JSON
    res_hooks = runner.invoke(app, ["hooks", "list", "--json"])
    assert res_hooks.exit_code == 0
    assert json.loads(res_hooks.output) == []
