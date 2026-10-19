"""Tests for vision and structured multimodal AI tools."""

import json
import tempfile
from pathlib import Path
from typer.testing import CliRunner

from localpilot.cli.main import app
from localpilot.storage.db import DatabaseManager

runner = CliRunner()


def test_multimodal_tools_workflow(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test_multi.db"
        test_db = DatabaseManager(db_path)

        monkeypatch.setattr("localpilot.storage.db.get_db", lambda *args, **kwargs: test_db)
        monkeypatch.setattr("localpilot.cli.main.get_db", lambda *args, **kwargs: test_db)
        monkeypatch.setattr("localpilot.application.task_service.get_db", lambda *args, **kwargs: test_db)

        # 1. Non-existent image file check
        res_nofile = runner.invoke(app, ["tools", "describe-image", "/path/to/missing.png", "--json"])
        assert res_nofile.exit_code == 1
        nofile_data = json.loads(res_nofile.output)
        assert "does not exist on disk" in nofile_data["error"]

        # Create dummy image file
        dummy_img = tmp_path / "diagram.png"
        dummy_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        # 2. Text-only model guard check
        res_text_only = runner.invoke(app, ["tools", "describe-image", str(dummy_img), "--model", "llama3:latest", "--json"])
        assert res_text_only.exit_code == 1
        text_data = json.loads(res_text_only.output)
        assert "text-only" in text_data["error"]
        assert "discover --task vision" in text_data["error"]

        # 3. Vision-capable model check with mock
        monkeypatch.setattr(
            "localpilot.application.task_service.TaskService._call_inference",
            lambda *args, **kwargs: "A flowchart diagram with boxes and arrows."
        )
        res_vision = runner.invoke(app, ["tools", "describe-image", str(dummy_img), "--model", "llava:latest", "--json"])
        assert res_vision.exit_code == 0
        vis_data = json.loads(res_vision.output)
        assert vis_data["status"] == "success"
        assert "flowchart" in vis_data["output_text"]

        # 4. Read-image (OCR)
        monkeypatch.setattr(
            "localpilot.application.task_service.TaskService._call_inference",
            lambda *args, **kwargs: "Extracted Text: LOCALPILOT 2026"
        )
        res_ocr = runner.invoke(app, ["tools", "read-image", str(dummy_img), "--model", "minicpm-v:latest", "--json"])
        assert res_ocr.exit_code == 0
        ocr_data = json.loads(res_ocr.output)
        assert "LOCALPILOT" in ocr_data["output_text"]

        # 5. Review-table
        sample_csv = tmp_path / "data.csv"
        sample_csv.write_text("item,cost\napple,2\nbanana,1\n")
        monkeypatch.setattr(
            "localpilot.application.task_service.TaskService._call_inference",
            lambda *args, **kwargs: "Summary: Total cost $3, Apple is most expensive."
        )
        res_table = runner.invoke(app, ["tools", "review-table", str(sample_csv), "--json"])
        assert res_table.exit_code == 0
        tbl_data = json.loads(res_table.output)
        assert "Apple is most expensive" in tbl_data["output_text"]

        # 6. Meeting-notes
        sample_notes = tmp_path / "meeting.txt"
        sample_notes.write_text("Alice: Let's release 0.3.0. Bob: Agreed, will test tomorrow.")
        monkeypatch.setattr(
            "localpilot.application.task_service.TaskService._call_inference",
            lambda *args, **kwargs: "Action Items: Bob to test release 0.3.0."
        )
        res_notes = runner.invoke(app, ["tools", "meeting-notes", str(sample_notes), "--json"])
        assert res_notes.exit_code == 0
        notes_data = json.loads(res_notes.output)
        assert "Bob to test" in notes_data["output_text"]
