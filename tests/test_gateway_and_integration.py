"""Integration tests for GatewayServer on loopback and IntegrationTemplateService."""

import json
import tempfile
import threading
from pathlib import Path
import httpx
import pytest

from localpilot.application.integration_service import IntegrationTemplateService
from localpilot.application.version_service import ModelVersionService
from localpilot.domain.versioning import ModelVersion
from localpilot.gateway.server import GatewayServer
from localpilot.storage.db import DatabaseManager


def test_integration_templates_and_config_writing():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # 1. OpenCode template and writer
        opencode_file = tmp_path / "opencode.json"
        res_oc = IntegrationTemplateService.apply_opencode_config(
            target_path=opencode_file,
            base_url="http://127.0.0.1:8080/v1",
            api_key="sk-local-testkey",
            model_alias="coding",
        )
        assert opencode_file.exists()
        oc_data = json.loads(opencode_file.read_text())
        assert oc_data["baseUrl"] == "http://127.0.0.1:8080/v1"
        assert oc_data["model"] == "coding"
        assert oc_data["apiKey"] == "sk-local-testkey"

        # Apply again to verify backup creation
        res_oc2 = IntegrationTemplateService.apply_opencode_config(
            target_path=opencode_file,
            base_url="http://127.0.0.1:8080/v1",
            api_key="sk-local-testkey-updated",
            model_alias="coding",
        )
        backup_file = opencode_file.with_suffix(".json.bak")
        assert backup_file.exists()
        assert json.loads(backup_file.read_text())["apiKey"] == "sk-local-testkey"
        assert json.loads(opencode_file.read_text())["apiKey"] == "sk-local-testkey-updated"

        # 2. Continue template and writer
        continue_file = tmp_path / "config.json"
        res_cont = IntegrationTemplateService.apply_continue_config(
            target_path=continue_file,
            base_url="http://127.0.0.1:8080/v1",
            api_key="sk-local-contkey",
            model_alias="coding",
        )
        assert continue_file.exists()
        cont_data = json.loads(continue_file.read_text())
        assert len(cont_data["models"]) == 1
        assert cont_data["models"][0]["apiBase"] == "http://127.0.0.1:8080/v1"

        # 3. Cursor instructions
        cursor_info = IntegrationTemplateService.get_cursor_instructions(
            base_url="http://127.0.0.1:8080/v1",
            api_key="sk-local-cursor",
            model_alias="coding",
        )
        assert len(cursor_info["steps"]) >= 4
        assert cursor_info["configuration"]["api_key"] == "sk-local-cursor"

        # 4. Client snippets
        snippets = IntegrationTemplateService.get_client_snippets(
            base_url="http://127.0.0.1:8080/v1",
            api_key="sk-local-snippet",
            model_alias="writing",
        )
        assert "curl" in snippets
        assert "python" in snippets
        assert "nodejs" in snippets
        assert "sk-local-snippet" in snippets["python"]


def test_gateway_server_loopback_endpoints():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_file = tmp_path / "test_gw.db"
        db = DatabaseManager(db_file)

        # Register a version and an alias
        ver_svc = ModelVersionService(db)
        mv = ModelVersion(version_id="ver-gw-test-1", logical_model_id="gw-llama", version_label="v1")
        ver_svc.register_version(mv)
        ver_svc.activate_alias("coding", "ver-gw-test-1")

        # Start gateway on ephemeral port (port 0)
        server = GatewayServer(host="127.0.0.1", port=0, db=db, data_dir=tmp_path)
        actual_port = server.server_address[1]

        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        base_url = f"http://127.0.0.1:{actual_port}"

        try:
            # 1. Health check (no auth needed)
            r_health = httpx.get(f"{base_url}/health")
            assert r_health.status_code == 200
            health_json = r_health.json()
            assert health_json["status"] == "ok"
            assert health_json["service"] == "localpilot-gateway"

            # 2. GET /v1/models without auth -> 401
            r_unauth = httpx.get(f"{base_url}/v1/models")
            assert r_unauth.status_code == 401
            assert "error" in r_unauth.json()

            # 3. Create key and GET /v1/models with auth -> 200
            token, key_record = server.key_service.create_key("Gateway Client")
            headers = {"Authorization": f"Bearer {token}"}
            r_models = httpx.get(f"{base_url}/v1/models", headers=headers)
            assert r_models.status_code == 200
            models_data = r_models.json()
            assert models_data["object"] == "list"
            assert any(m["id"] == "coding" for m in models_data["data"])

            # 4. State lock file testing
            server.write_state()
            state = GatewayServer.get_running_state(data_dir=tmp_path)
            assert state is not None
            assert state["port"] == 0 or state["port"] == actual_port

        finally:
            server.shutdown()
            server.server_close()
            server.clear_state()
