"""Tests for local document indexing, BM25 retrieval, and grounded RAG search."""

import json
import tempfile
from pathlib import Path
from typer.testing import CliRunner

from localpilot.cli.main import app
from localpilot.storage.db import DatabaseManager
from localpilot.application.search_service import SearchService

runner = CliRunner()


def test_search_service_and_cli(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test_search.db"
        test_db = DatabaseManager(db_path)

        monkeypatch.setattr("localpilot.storage.db.get_db", lambda *args, **kwargs: test_db)
        monkeypatch.setattr("localpilot.cli.main.get_db", lambda *args, **kwargs: test_db)
        monkeypatch.setattr("localpilot.application.search_service.get_db", lambda *args, **kwargs: test_db)

        # Create sample document folder
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        doc1 = docs_dir / "guide.md"
        doc1.write_text("# LocalPilot Installation\n\nRun pip install localpilot to install the CLI.\nThen run localpilot setup to configure your computer.")

        doc2 = docs_dir / "architecture.py"
        doc2.write_text("# Core architecture\ndef start_gateway(port=8080):\n    print('Listening on port', port)\n")

        svc = SearchService(test_db)

        # 1. Add collection
        col = svc.add_collection(docs_dir, name="test-docs")
        assert col.name == "test-docs"
        assert col.file_count == 2
        assert col.passage_count >= 2

        # 2. Find passages
        passages = svc.find_passages("installation pip install", collection_name_or_id="test-docs")
        assert len(passages) > 0
        assert "guide.md" in passages[0].relative_path
        assert passages[0].score > 0

        # 3. Ask question grounded in passages
        res_ask = svc.ask(
            query="How do I install LocalPilot?",
            collection_name_or_id="test-docs",
            mock_answer="To install LocalPilot, run `pip install localpilot` [1].",
        )
        assert "pip install" in res_ask.answer
        assert len(res_ask.sources) > 0

        # 4. Test CLI: search list
        res_list = runner.invoke(app, ["search", "list", "--json"])
        assert res_list.exit_code == 0
        cols_data = json.loads(res_list.output)
        assert len(cols_data) == 1
        assert cols_data[0]["name"] == "test-docs"

        # 5. Test CLI: search status
        res_stat = runner.invoke(app, ["search", "status", "test-docs", "--json"])
        assert res_stat.exit_code == 0
        stat_data = json.loads(res_stat.output)
        assert stat_data["file_count"] == 2

        # 6. Test CLI: search find
        res_find = runner.invoke(app, ["search", "find", "gateway port", "--collection", "test-docs", "--json"])
        assert res_find.exit_code == 0
        find_data = json.loads(res_find.output)
        assert len(find_data) > 0

        # 7. Test CLI: search remove
        res_rem = runner.invoke(app, ["search", "remove", "test-docs", "--json"])
        assert res_rem.exit_code == 0
        rem_data = json.loads(res_rem.output)
        assert rem_data["success"] is True
