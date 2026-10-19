"""Tests for guided application connections, previews, verification, and restoration."""

import json
import tempfile
from pathlib import Path
from typer.testing import CliRunner

from localpilot.cli.main import app
from localpilot.storage.db import DatabaseManager, get_db
from localpilot.application.integration_service import ConnectionManager

runner = CliRunner()


def test_connections_cli_workflow(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test.db"
        test_db = DatabaseManager(db_path)

        monkeypatch.setattr("localpilot.storage.db.get_db", lambda *args, **kwargs: test_db)
        monkeypatch.setattr("localpilot.application.integration_service.get_db", lambda *args, **kwargs: test_db)
        monkeypatch.setattr("localpilot.application.api_key_service.get_db", lambda *args, **kwargs: test_db)

        # 1. Preview mode with --preview
        res = runner.invoke(app, ["connect", "opencode", "--preview", "--json"])
        assert res.exit_code == 0
        data = json.loads(res.output)
        assert data["success"] is True
        assert data["preview_only"] is True
        assert "diff" in data

        # 2. Write mode creating an actual connection
        cfg_file = tmp_path / "opencode.json"
        # Monkeypatch the target config path for opencode
        monkeypatch.setattr(
            "localpilot.application.integration_service.IntegrationTemplateService.apply_opencode_config",
            lambda target_path, base_url, api_key, model_alias, dry_run: {
                "target_path": str(cfg_file),
                "backup_path": str(tmp_path / "opencode.json.bak"),
                "applied_settings": {"provider": "localpilot"},
                "diff": "+ provider: localpilot",
                "dry_run": dry_run,
            }
        )

        # Create dummy backup file so restore can be tested
        (tmp_path / "opencode.json.bak").write_text('{"provider": "original"}')
        cfg_file.write_text('{"provider": "localpilot"}')

        res_write = runner.invoke(app, ["connect", "opencode", "--write", "--json"])
        assert res_write.exit_code == 0
        write_data = json.loads(res_write.output)
        assert write_data["success"] is True
        assert write_data["preview_only"] is False
        conn_id = write_data["connection"]["connection_id"]

        # 3. List connections
        res_list = runner.invoke(app, ["connections", "list", "--json"])
        assert res_list.exit_code == 0
        conns = json.loads(res_list.output)
        assert any(c["connection_id"] == conn_id for c in conns)

        # 4. Inspect connection
        res_insp = runner.invoke(app, ["connections", "inspect", conn_id, "--json"])
        assert res_insp.exit_code == 0
        insp_data = json.loads(res_insp.output)
        assert insp_data["connection_id"] == conn_id
        assert insp_data["client_type"] == "opencode"

        # 5. Test connection
        res_test = runner.invoke(app, ["connections", "test", conn_id, "--json"])
        assert res_test.exit_code == 0
        test_data = json.loads(res_test.output)
        assert "config_valid" in test_data

        # 6. Restore connection
        res_restore = runner.invoke(app, ["connections", "restore", conn_id, "--json"])
        assert res_restore.exit_code == 0
        restore_data = json.loads(res_restore.output)
        assert restore_data["success"] is True
        assert restore_data["status"] == "restored"

        # 7. Remove connection
        res_remove = runner.invoke(app, ["connections", "remove", conn_id, "--json"])
        assert res_remove.exit_code == 0
        remove_data = json.loads(res_remove.output)
        assert remove_data["status"] == "removed"
