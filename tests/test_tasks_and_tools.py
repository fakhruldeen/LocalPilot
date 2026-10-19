"""Tests for the 18 AI tools interfaces, execution engine, and CLI commands."""

import json
import tempfile
from pathlib import Path
from typer.testing import CliRunner

from localpilot.cli.main import app
from localpilot.storage.db import DatabaseManager
from localpilot.application.task_service import TaskService, TASK_DEFINITIONS

runner = CliRunner()


def test_task_registry_count():
    task_svc = TaskService()
    tasks = task_svc.list_tasks()
    assert len(tasks) >= 18
    task_ids = {t.task_id for t in tasks}
    expected = {
        "chat", "summarize", "rewrite", "translate", "compare", "explain-code",
        "generate-code", "refactor-code", "generate-image", "generate-video",
        "draft", "extract", "classify", "review-table", "meeting-notes",
        "ask-files", "find-passages", "transcribe", "speak", "voice",
        "describe-image", "read-image",
    }
    assert expected.issubset(task_ids)


def test_tools_cli_and_service(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test_tasks.db"
        test_db = DatabaseManager(db_path)

        monkeypatch.setattr("localpilot.storage.db.get_db", lambda *args, **kwargs: test_db)
        monkeypatch.setattr("localpilot.cli.main.get_db", lambda *args, **kwargs: test_db)
        monkeypatch.setattr("localpilot.application.task_service.get_db", lambda *args, **kwargs: test_db)

        # Mock inference call
        monkeypatch.setattr(
            "localpilot.application.task_service.TaskService._call_inference",
            lambda self, gateway_url, model_alias, system_prompt, user_prompt, api_key: (
                f"Generated response for {user_prompt[:20]}"
            )
        )

        # 1. tools list
        res_list = runner.invoke(app, ["tools", "list", "--json"])
        assert res_list.exit_code == 0
        tasks_data = json.loads(res_list.output)
        assert len(tasks_data) >= 18

        # 2. tools summarize with direct text
        res_sum = runner.invoke(app, ["tools", "summarize", "Short text to summarize", "--bullets", "--json"])
        assert res_sum.exit_code == 0
        sum_data = json.loads(res_sum.output)
        assert sum_data["task_id"] == "summarize"
        assert sum_data["status"] == "success"
        assert "Generated response" in sum_data["output_text"]

        # 3. tools explain-code from file
        sample_code = tmp_path / "test.py"
        sample_code.write_text("def hello():\n    print('world')\n")
        res_code = runner.invoke(app, ["tools", "explain-code", str(sample_code), "--json"])
        assert res_code.exit_code == 0
        code_data = json.loads(res_code.output)
        assert code_data["task_id"] == "explain-code"
        assert code_data["status"] == "success"

        # 4. tools translate
        res_trans = runner.invoke(app, ["tools", "translate", "Hello", "--target", "Arabic", "--json"])
        assert res_trans.exit_code == 0

        # 5. tools rewrite
        res_rew = runner.invoke(app, ["tools", "rewrite", "Bad writing", "--tone", "formal", "--json"])
        assert res_rew.exit_code == 0

        # 6. tools compare
        file2 = tmp_path / "test2.py"
        file2.write_text("def hello_v2():\n    return 'world'\n")
        res_comp = runner.invoke(app, ["tools", "compare", str(sample_code), str(file2), "--json"])
        assert res_comp.exit_code == 0

        # 7. tools extract & classify
        res_ext = runner.invoke(app, ["tools", "extract", "Invoice #101 total $50", "--schema", "invoice_id,amount", "--json"])
        assert res_ext.exit_code == 0

        res_cls = runner.invoke(app, ["tools", "classify", "The app crashed", "--labels", "bug,feedback", "--json"])
        assert res_cls.exit_code == 0

        # 8. tools generate-code & refactor-code
        res_gencode = runner.invoke(app, ["tools", "generate-code", "Binary search function", "--language", "python", "--json"])
        assert res_gencode.exit_code == 0
        assert json.loads(res_gencode.output)["task_id"] == "generate-code"

        res_refactor = runner.invoke(app, ["tools", "refactor-code", str(sample_code), "--issue", "Add type hints", "--json"])
        assert res_refactor.exit_code == 0
        assert json.loads(res_refactor.output)["task_id"] == "refactor-code"

        # 9. tools generate-image & generate-video
        res_genimg = runner.invoke(app, ["tools", "generate-image", "A serene lake", "--style", "cinematic", "--json"])
        assert res_genimg.exit_code == 0
        assert json.loads(res_genimg.output)["task_id"] == "generate-image"

        res_genvid = runner.invoke(app, ["tools", "generate-video", "Drone over trees", "--json"])
        assert res_genvid.exit_code == 0
        assert json.loads(res_genvid.output)["task_id"] == "generate-video"

        # 10. tools history
        res_hist = runner.invoke(app, ["tools", "history", "--json"])
        assert res_hist.exit_code == 0
        hist_data = json.loads(res_hist.output)
        assert len(hist_data) >= 10
