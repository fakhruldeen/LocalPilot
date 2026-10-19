"""Tests for diagnostics and self-healing repair workflows."""

import json
import tempfile
from pathlib import Path
from typer.testing import CliRunner

from localpilot.cli.main import app
from localpilot.storage.db import DatabaseManager, get_db

runner = CliRunner()


def test_diagnostics_and_repairs_workflow(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test_diag.db"
        test_db = DatabaseManager(db_path)

        monkeypatch.setattr("localpilot.storage.db.get_db", lambda *args, **kwargs: test_db)
        monkeypatch.setattr("localpilot.application.diagnostic_service.get_db", lambda *args, **kwargs: test_db)
        monkeypatch.setattr("localpilot.application.integration_service.get_db", lambda *args, **kwargs: test_db)

        # 1. General diagnostics run
        res_gen = runner.invoke(app, ["doctor", "--json"])
        assert res_gen.exit_code == 0
        gen_data = json.loads(res_gen.output)
        assert "findings" in gen_data
        assert any(f["category"] == "system" for f in gen_data["findings"])

        # 2. Targeted model check (model does not exist)
        res_mod = runner.invoke(app, ["doctor", "--model", "nonexistent-model", "--json"])
        assert res_mod.exit_code == 0
        mod_data = json.loads(res_mod.output)
        assert any(f["severity"] == "error" for f in mod_data["findings"])

        # 3. Targeted connection check (connection does not exist)
        res_conn = runner.invoke(app, ["doctor", "--connection", "conn_fake123", "--json"])
        assert res_conn.exit_code == 0
        conn_data = json.loads(res_conn.output)
        assert any(f["severity"] == "error" for f in conn_data["findings"])

        # 4. Targeted tool check
        res_tool = runner.invoke(app, ["doctor", "--tool", "summarize", "--json"])
        assert res_tool.exit_code == 0
        tool_data = json.loads(res_tool.output)
        assert "findings" in tool_data

        # 5. Insert dummy model version without default alias to test repair
        conn = test_db.get_connection()
        with conn:
            conn.execute(
                "INSERT INTO logical_models (model_id, display_name, task_type, default_alias, created_at, updated_at) "
                "VALUES ('test-m', 'Test Model', 'chat', 'test:latest', datetime('now'), datetime('now'))"
            )
            conn.execute(
                "INSERT INTO model_versions (version_id, logical_model_id, version_tag, created_at, "
                "artifact_uri, artifact_digest, artifact_size_bytes, context_length, installed_path) "
                "VALUES ('ver-test-1', 'test-m', 'v1', datetime('now'), 'uri', 'sha', 1000, 2048, '/tmp/test.gguf')"
            )
            # Delete any default alias
            conn.execute("DELETE FROM model_aliases WHERE alias_name = 'default'")

        # Verify missing default alias finding
        res_diag_before = runner.invoke(app, ["doctor", "--json"])
        assert res_diag_before.exit_code == 0
        data_before = json.loads(res_diag_before.output)
        assert any(f["finding_id"] == "model_missing_default_alias" for f in data_before["findings"])

        # Run repair
        res_repair = runner.invoke(app, ["doctor", "--repair", "--yes", "--json"])
        assert res_repair.exit_code == 0
        repair_data = json.loads(res_repair.output)
        assert len(repair_data["repairs_executed"]) > 0

        # Verify default alias was repaired
        row = conn.execute("SELECT target_version_id FROM model_aliases WHERE alias_name = 'default'").fetchone()
        assert row is not None
        assert row["target_version_id"] == "ver-test-1"
