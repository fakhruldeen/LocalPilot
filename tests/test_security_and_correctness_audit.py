"""Reproducing and verifying security, concurrency, accounting, and correctness remediations."""

import json
import os
import tempfile
import threading
from pathlib import Path
import httpx
import pytest

from localpilot.application.api_key_service import ApiKeyService
from localpilot.application.integration_service import IntegrationTemplateService
from localpilot.application.version_service import ModelVersionService
from localpilot.domain.api_contract import ApiKeyPolicy, ApiKeyRecord, ApiKeyStatus, TokenUsageSource
from localpilot.domain.versioning import ModelVersion
from localpilot.gateway.server import GatewayServer
from localpilot.storage.db import DatabaseManager


def test_restricted_key_cannot_inspect_forbidden_model():
    """SEC-01: Restricted key cannot access forbidden model via GET /v1/models/{model_id}."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db = DatabaseManager(tmp_path / "test_sec01.db")
        ver_svc = ModelVersionService(db)

        # Register models & aliases
        v1 = ModelVersion(version_id="ver-code-1", logical_model_id="code-model", version_label="v1", installed_path=str(tmp_path / "code.gguf"))
        (tmp_path / "code.gguf").write_text("dummy")
        ver_svc.register_version(v1)
        ver_svc.activate_alias("coding", "ver-code-1")

        v2 = ModelVersion(version_id="ver-write-1", logical_model_id="write-model", version_label="v1", installed_path=str(tmp_path / "write.gguf"))
        (tmp_path / "write.gguf").write_text("dummy")
        ver_svc.register_version(v2)
        ver_svc.activate_alias("writing", "ver-write-1")

        key_svc = ApiKeyService(db)
        token, _ = key_svc.create_key("Coding Only", policy=ApiKeyPolicy(allowed_aliases=["coding"]))

        server = GatewayServer(host="127.0.0.1", port=0, db=db, data_dir=tmp_path)
        actual_port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        base_url = f"http://127.0.0.1:{actual_port}"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            # Authorized model succeeds
            r_ok = httpx.get(f"{base_url}/v1/models/coding", headers=headers)
            assert r_ok.status_code == 200

            # Forbidden model must be rejected (404 model_not_found or 403 forbidden)
            r_forbidden = httpx.get(f"{base_url}/v1/models/writing", headers=headers)
            assert r_forbidden.status_code in (403, 404)
        finally:
            server.shutdown()
            server.server_close()


def test_invalid_stored_policy_and_expiry_fail_closed():
    """SEC-03: Corrupt policy_json fails closed to empty allowlist; corrupt expiry fails closed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db = DatabaseManager(tmp_path / "test_sec03.db")
        key_svc = ApiKeyService(db)

        token, key_record = key_svc.create_key("Tampered Key")

        # Tamper DB with invalid policy_json
        conn = db.get_connection()
        with conn:
            conn.execute("UPDATE api_keys SET policy_json = '{invalid json' WHERE key_id = ?", (key_record.key_id,))

        verified = key_svc.verify_token(token)
        assert verified is not None
        # Must fail closed: allowed_aliases must NOT be wildcard ["*"]
        assert verified.policy.allowed_aliases == []

        # Test corrupt expiry
        rec = ApiKeyRecord(
            display_name="Corrupt Expiry",
            secret_prefix="sk-local-test",
            verifier="dummy",
            expires_at="not-a-valid-iso-date",
        )
        # Must fail closed
        assert rec.verify("secret") is False


def test_rotation_does_not_reactivate_revoked_key():
    """SEC-04: Rotating a revoked key must preserve its revoked status or refuse rotation."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db = DatabaseManager(tmp_path / "test_sec04.db")
        key_svc = ApiKeyService(db)

        token, key_record = key_svc.create_key("Revokable Key")
        key_svc.revoke_key(key_record.key_id)
        assert key_svc.get_key(key_record.key_id).is_active is False

        # Attempt rotate
        with pytest.raises(ValueError, match="revoked"):
            key_svc.rotate_key(key_record.key_id)


def test_request_lease_does_not_erase_user_pin():
    """CONC-01: In-flight request lease must not decrement or erase user retention pin."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db = DatabaseManager(tmp_path / "test_conc01.db")
        ver_svc = ModelVersionService(db)

        v1 = ModelVersion(version_id="ver-lease-1", logical_model_id="lease-mod", version_label="v1")
        ver_svc.register_version(v1)

        # User pins model
        ver_svc.pin_version("ver-lease-1", reason="User approved baseline")
        pinned_v = ver_svc.get_version("ver-lease-1")
        assert pinned_v.is_pinned is True
        assert pinned_v.pin_count == 1

        # Simulate execution lease acquire and release
        lease_id = ver_svc.acquire_execution_lease("ver-lease-1", owner="request-123")
        assert lease_id is not None
        assert ver_svc.is_version_in_use("ver-lease-1") is True

        # Releasing execution lease must NOT affect user pin
        ver_svc.release_execution_lease(lease_id)
        assert ver_svc.is_version_in_use("ver-lease-1") is True  # still pinned by user!
        refreshed = ver_svc.get_version("ver-lease-1")
        assert refreshed.is_pinned is True
        assert refreshed.pin_count == 1


def test_readiness_probe_fails_on_missing_model_files():
    """HLTH-01: /v1/health/ready must fail (503) if registered model file is missing from disk."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db = DatabaseManager(tmp_path / "test_hlth01.db")
        ver_svc = ModelVersionService(db)

        # Register alias pointing to nonexistent file
        v1 = ModelVersion(
            version_id="ver-missing-1",
            logical_model_id="ghost-model",
            version_label="v1",
            installed_path=str(tmp_path / "nonexistent_file.gguf"),
        )
        ver_svc.register_version(v1)
        ver_svc.activate_alias("ghost", "ver-missing-1")

        server = GatewayServer(host="127.0.0.1", port=0, db=db, data_dir=tmp_path)
        actual_port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        base_url = f"http://127.0.0.1:{actual_port}"
        try:
            r = httpx.get(f"{base_url}/v1/health/ready")
            assert r.status_code == 503
            assert r.json()["ready"] is False
        finally:
            server.shutdown()
            server.server_close()


def test_gateway_ephemeral_port_recorded_accurately():
    """PROC-01: When bound to port 0, write_state records the actual allocated port, not 0."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db = DatabaseManager(tmp_path / "test_proc01.db")
        server = GatewayServer(host="127.0.0.1", port=0, db=db, data_dir=tmp_path)
        actual_port = server.server_address[1]
        assert actual_port > 0

        server.write_state()
        state = GatewayServer.get_running_state(data_dir=tmp_path)
        assert state is not None
        assert state["port"] == actual_port
        server.server_close()


def test_integration_refuses_malformed_config_and_protects_backups():
    """CFG-01: Refuse to overwrite malformed JSON; create unique backups with 0600 permissions."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        target = tmp_path / "opencode.json"
        target.write_text("{malformed: bad json,")

        # Must raise ValueError and preserve original content
        with pytest.raises(ValueError, match="Malformed"):
            IntegrationTemplateService.apply_opencode_config(
                target_path=target,
                base_url="http://127.0.0.1:8080/v1",
                api_key="sk-local-test",
            )
        assert target.read_text() == "{malformed: bad json,"

        # Test valid file update generates unique backup and 0600 mode
        target.write_text(json.dumps({"existingKey": "val"}))
        res = IntegrationTemplateService.apply_opencode_config(
            target_path=target,
            base_url="http://127.0.0.1:8080/v1",
            api_key="sk-local-test",
        )
        assert Path(res["backup_path"]).exists()
        assert oct(target.stat().st_mode & 0o777) == oct(0o600)


def test_capability_enforcement_and_token_limits():
    """SEC-02: Keys without 'chat' or 'streaming' capability or exceeding max_tokens fail closed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db = DatabaseManager(tmp_path / "test_sec02.db")
        key_svc = ApiKeyService(db)
        ver_svc = ModelVersionService(db)

        mv = ModelVersion(version_id="ver-llama-sec", logical_model_id="llama-sec", version_label="v1")
        ver_svc.register_version(mv)
        ver_svc.activate_alias("llama", "ver-llama-sec")

        # Key 1: Only streaming allowed, no chat
        raw1, rec1 = key_svc.create_key(
            name="k1",
            app_name="StreamOnlyApp",
            policy=ApiKeyPolicy(allowed_aliases=["*"], allowed_capabilities=["streaming"]),
        )
        # Key 2: Max tokens = 50
        raw2, rec2 = key_svc.create_key(
            name="k2",
            app_name="LimitedTokensApp",
            policy=ApiKeyPolicy(allowed_aliases=["*"], allowed_capabilities=["chat", "streaming"], max_tokens_per_request=50),
        )

        server = GatewayServer(host="127.0.0.1", port=0, db=db, data_dir=tmp_path)
        actual_port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        base_url = f"http://127.0.0.1:{actual_port}"

        try:
            # Key 1 requests non-streaming chat -> 403 Forbidden
            r1 = httpx.post(
                f"{base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {raw1}"},
                json={"model": "llama", "messages": [{"role": "user", "content": "hi"}], "stream": False},
            )
            assert r1.status_code == 403
            assert r1.json()["error"]["code"] == "capability_forbidden"

            # Key 2 requests 100 max_tokens -> 400 Bad Request
            r2 = httpx.post(
                f"{base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {raw2}"},
                json={"model": "llama", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
            )
            assert r2.status_code == 400
            assert r2.json()["error"]["code"] == "token_limit_exceeded"
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=2.0)


def test_cors_loopback_only_and_payload_size_limit():
    """SEC-05: Reject wildcard CORS for untrusted origins; enforce 10MB payload limit (413)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db = DatabaseManager(tmp_path / "test_sec05.db")
        key_svc = ApiKeyService(db)
        raw_key, rec = key_svc.create_key(name="k3", app_name="CorsTest")

        server = GatewayServer(host="127.0.0.1", port=0, db=db, data_dir=tmp_path)
        actual_port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        base_url = f"http://127.0.0.1:{actual_port}"

        try:
            # 1. External Origin in OPTIONS request must not receive Access-Control-Allow-Origin
            r_opt = httpx.options(
                f"{base_url}/v1/chat/completions",
                headers={"Origin": "https://malicious-site.com"},
            )
            assert "Access-Control-Allow-Origin" not in r_opt.headers

            # Loopback origin must be permitted
            r_loopback = httpx.options(
                f"{base_url}/v1/chat/completions",
                headers={"Origin": "http://localhost:3000"},
            )
            assert r_loopback.headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"

            # 2. Oversized Content-Length must be rejected with 413
            import http.client
            h_conn = http.client.HTTPConnection("127.0.0.1", actual_port)
            h_conn.putrequest("POST", "/v1/chat/completions")
            h_conn.putheader("Authorization", f"Bearer {raw_key}")
            h_conn.putheader("Content-Length", str(15 * 1024 * 1024))
            h_conn.endheaders()
            h_resp = h_conn.getresponse()
            assert h_resp.status == 413
            err_data = json.loads(h_resp.read().decode())
            assert err_data["error"]["code"] == "payload_too_large"
            h_conn.close()
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=2.0)


def test_reject_unsupported_tools():
    """CORR-01: Requests with tools or tool_choice are rejected with 400 Bad Request."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db = DatabaseManager(tmp_path / "test_corr01.db")
        key_svc = ApiKeyService(db)
        raw_key, rec = key_svc.create_key(name="k4", app_name="ToolsTest")

        server = GatewayServer(host="127.0.0.1", port=0, db=db, data_dir=tmp_path)
        actual_port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        base_url = f"http://127.0.0.1:{actual_port}"

        try:
            r = httpx.post(
                f"{base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {raw_key}"},
                json={
                    "model": "coding",
                    "messages": [{"role": "user", "content": "hello"}],
                    "tools": [{"type": "function", "function": {"name": "test"}}],
                },
            )
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "tools_unsupported"
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=2.0)

