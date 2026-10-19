"""Tests for the benchmarking service and CLI commands."""

import json
import tempfile
from pathlib import Path
from typer.testing import CliRunner

from localpilot.cli.main import app
from localpilot.storage.db import DatabaseManager
from localpilot.application.benchmark_service import BenchmarkService

runner = CliRunner()


def test_benchmark_service_and_cli(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test_bench.db"
        test_db = DatabaseManager(db_path)

        monkeypatch.setattr("localpilot.storage.db.get_db", lambda *args, **kwargs: test_db)
        monkeypatch.setattr("localpilot.application.benchmark_service.get_db", lambda *args, **kwargs: test_db)

        svc = BenchmarkService(test_db)

        # 1. Test mock benchmark run
        def mock_gen(prompt):
            return {
                "prompt_tokens": 10,
                "completion_tokens": 40,
                "ttft_ms": 30.0,
                "duration_sec": 1.0,
            }

        run_1 = svc.run_benchmark(
            model_alias="llama3:latest",
            suite_name="general",
            rounds=1,
            mock_generator=mock_gen,
        )
        assert run_1.run_id.startswith("bench_")
        assert run_1.tps == 40.0
        assert run_1.ttft_ms == 30.0

        # Run second model for comparison
        def mock_gen_2(prompt):
            return {
                "prompt_tokens": 10,
                "completion_tokens": 80,
                "ttft_ms": 20.0,
                "duration_sec": 1.0,
            }

        run_2 = svc.run_benchmark(
            model_alias="mistral:latest",
            suite_name="general",
            rounds=1,
            mock_generator=mock_gen_2,
        )
        assert run_2.tps == 80.0

        # 2. Test compare
        comp = svc.compare_benchmarks("llama3:latest", "mistral:latest", suite_name="general")
        assert comp.winner_alias == "mistral:latest"
        assert comp.speedup_ratio == 2.0

        # 3. Test CLI results
        res_list = runner.invoke(app, ["bench", "results", "--json"])
        assert res_list.exit_code == 0
        runs_data = json.loads(res_list.output)
        assert len(runs_data) == 2

        # 4. Test CLI show
        res_show = runner.invoke(app, ["bench", "show", run_1.run_id, "--json"])
        assert res_show.exit_code == 0
        show_data = json.loads(res_show.output)
        assert show_data["run_id"] == run_1.run_id
        assert show_data["model_alias"] == "llama3:latest"

        # 5. Test CLI compare
        res_comp = runner.invoke(app, ["bench", "compare", "llama3:latest", "mistral:latest", "--json"])
        assert res_comp.exit_code == 0
        comp_data = json.loads(res_comp.output)
        assert comp_data["winner_alias"] == "mistral:latest"
