"""Tests for database migration v6 and associated helper methods."""

import tempfile
from pathlib import Path
from localpilot.storage.db import DatabaseManager


def test_migration_v6_creation_and_helpers():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_localpilot.db"
        db = DatabaseManager(db_path)

        # Verify migration v6 applied
        conn = db.get_connection()
        migrations = conn.execute("SELECT version, description FROM schema_migrations ORDER BY version").fetchall()
        versions = [m["version"] for m in migrations]
        assert 6 in versions

        # Check v6 tables exist
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        for expected in ["benchmarks", "benchmark_runs", "task_runs", "task_artifacts", "search_collections", "search_passages", "voice_runs"]:
            assert expected in tables, f"Expected table {expected} to exist"

        # Test benchmark helpers
        db.record_benchmark_run({
            "run_id": "bench-1",
            "model_alias": "llama3:latest",
            "suite_name": "general",
            "prompt_tokens": 100,
            "completion_tokens": 200,
            "ttft_ms": 120.5,
            "tps": 35.2,
            "total_duration_ms": 5800.0,
            "peak_memory_mb": 4200.0,
            "created_at": "2026-10-19T12:00:00Z",
            "raw_metrics": {"trial_1": 35.2},
        })
        runs = db.list_benchmark_runs("llama3:latest")
        assert len(runs) == 1
        assert runs[0]["run_id"] == "bench-1"
        assert runs[0]["tps"] == 35.2
        assert runs[0]["raw_metrics"]["trial_1"] == 35.2

        single_run = db.get_benchmark_run("bench-1")
        assert single_run is not None
        assert single_run["suite_name"] == "general"

        # Test task runs and artifacts
        db.record_task_run(
            {
                "run_id": "task-run-1",
                "task_id": "summarize",
                "model_alias": "llama3:latest",
                "status": "success",
                "input_summary": "Document of 500 words",
                "output_text": "Executive summary text",
                "duration_ms": 1500.0,
                "tokens_used": 150,
                "created_at": "2026-10-19T12:01:00Z",
            },
            artifacts=[{
                "artifact_id": "art-1",
                "name": "summary.txt",
                "artifact_type": "text",
                "content": "Executive summary text",
            }]
        )
        task_runs = db.list_task_runs("summarize")
        assert len(task_runs) == 1
        assert task_runs[0]["run_id"] == "task-run-1"

        # Test search collection & passages
        db.save_search_collection("col-1", "my-docs", "/path/to/docs", 5, 20)
        cols = db.list_search_collections()
        assert len(cols) == 1
        assert cols[0]["name"] == "my-docs"

        db.save_search_passages([
            {
                "passage_id": "pass-1",
                "collection_id": "col-1",
                "file_path": "/path/to/docs/readme.md",
                "relative_path": "readme.md",
                "line_start": 1,
                "line_end": 10,
                "content": "Hello world passage",
                "metadata": {"title": "Readme"},
            }
        ])
        passages = db.get_search_passages("col-1")
        assert len(passages) == 1
        assert passages[0]["passage_id"] == "pass-1"
        assert passages[0]["metadata"]["title"] == "Readme"

        # Test voice runs
        db.record_voice_run({
            "run_id": "vrun-1",
            "direction": "stt",
            "model": "whisper-base",
            "duration_ms": 800.0,
            "audio_seconds": 3.5,
            "transcript": "Hello LocalPilot",
            "status": "completed",
            "created_at": "2026-10-19T12:02:00Z",
        })
        voice_runs = db.list_voice_runs()
        assert len(voice_runs) == 1
        assert voice_runs[0]["transcript"] == "Hello LocalPilot"
