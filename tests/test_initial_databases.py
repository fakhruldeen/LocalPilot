"""Tests for initial database files (sources, providers, developers, models) and catalog CLI commands."""

from __future__ import annotations
import json
from pathlib import Path
import pytest
from typer.testing import CliRunner

from localpilot.cli.main import app
from localpilot.data import (
    get_data_dir,
    get_initial_developers,
    get_initial_models,
    get_initial_providers,
    get_initial_sources,
    load_initial_json,
)
from localpilot.domain.models import NormalizedCandidate


def test_initial_data_files_exist():
    """Verify that all 4 initial database files exist across packaged, repo root, and docs directories."""
    repo_root = Path(__file__).resolve().parent.parent
    expected_files = ["sources.json", "providers.json", "developers.json", "models.json"]
    dirs = [
        repo_root / "src" / "localpilot" / "data",
        repo_root / "data",
        repo_root / "docs" / "data",
        repo_root / "web" / "data",
    ]

    for d in dirs:
        assert d.exists() and d.is_dir(), f"Directory {d} does not exist"
        for fname in expected_files:
            fpath = d / fname
            assert fpath.exists(), f"File {fpath} does not exist"
            assert fpath.stat().st_size > 0, f"File {fpath} is empty"
            payload = json.loads(fpath.read_text())
            assert payload.get("as_of_date") == "2026-10-20"
            assert payload.get("updated_at") == "2026-10-20T00:00:00Z"


def test_sources_database_schema():
    """Verify structure and contents of sources.json."""
    sources = get_initial_sources()
    assert len(sources) >= 5
    source_ids = [s["source_id"] for s in sources]
    assert "huggingface" in source_ids
    assert "ollama" in source_ids
    assert "modelscope" in source_ids
    assert "local_dir" in source_ids

    for s in sources:
        assert "source_id" in s
        assert "name" in s
        assert "url" in s
        assert "formats_supported" in s
        assert isinstance(s["formats_supported"], list)


def test_providers_database_schema():
    """Verify structure and contents of providers.json."""
    providers = get_initial_providers()
    assert len(providers) >= 15
    provider_ids = [p["provider_id"] for p in providers]
    assert "meta" in provider_ids
    assert "google" in provider_ids
    assert "deepseek" in provider_ids
    assert "mistral" in provider_ids
    assert "microsoft" in provider_ids
    assert "qwen" in provider_ids
    assert "nvidia" in provider_ids

    for p in providers:
        assert "provider_id" in p
        assert "name" in p
        assert "website" in p
        assert "flagship_families" in p
        assert isinstance(p["flagship_families"], list)


def test_developers_database_schema():
    """Verify structure and contents of developers.json."""
    devs = get_initial_developers()
    assert len(devs) >= 10
    dev_ids = [d["developer_id"] for d in devs]
    assert "bartowski" in dev_ids
    assert "thebloke" in dev_ids
    assert "unsloth" in dev_ids
    assert "ggerganov" in dev_ids
    assert "mlx-community" in dev_ids
    assert "lmstudio-community" in dev_ids
    assert "ggml-org" in dev_ids

    for d in devs:
        assert "developer_id" in d
        assert "name" in d
        assert "role" in d
        assert "specialties" in d
        assert isinstance(d["specialties"], list)


def test_models_database_schema_and_validation():
    """Verify that all models in models.json validate cleanly as NormalizedCandidate objects."""
    models_raw = get_initial_models()
    assert len(models_raw) >= 40

    candidates = [NormalizedCandidate.model_validate(m) for m in models_raw]
    assert len(candidates) == len(models_raw)

    # Check required fields and enrichments
    for m in models_raw:
        assert "candidate_id" in m
        assert "model_id" in m
        assert "name" in m
        assert "short_alias" in m
        assert "source" in m

        # Check that no suspect repetitive mock hashes remain
        h = m.get("sha256")
        if h:
            assert not (len(h) == 64 and h[:32] == h[32:]), f"Repetitive hash found in {m['model_id']}"

        # Check attribution separation for bartowski and unsloth community quants
        if "bartowski" in m["model_id"] or "unsloth" in m["model_id"]:
            assert m.get("is_official") is False
            assert "via" in m.get("publisher_display", "") or m.get("publisher_display") in ("bartowski", "unsloth")

        # Check gpt-oss family is associated with OpenAI
        if "gpt-oss" in m["model_id"]:
            assert m.get("base_model_provider_id") == "openai" or "OpenAI" in m.get("publisher_display", "")

    # Verify 2026 models exist
    models_2026 = [m for m in models_raw if str(m.get("verified_release_at", "")).startswith("2026")]
    assert len(models_2026) >= 10, f"Expected at least 10 2026 models, found {len(models_2026)}"
    aliases = {m.get("short_alias") for m in models_raw}
    assert "gemma3:4b" in aliases
    assert "qwen3:8b" in aliases
    assert "deepseek-r2:7b" in aliases
    assert "gpt-oss:20b-2026" in aliases
    assert "gemma4:e2b" in aliases


def test_staging_package_deliverables():
    """Verify that the staging package files and ZIP bundle exist and are valid."""
    repo_root = Path(__file__).resolve().parent.parent
    staging_dir = repo_root / "data" / "staging"
    assert staging_dir.exists() and staging_dir.is_dir()

    # 1. candidates_models.json
    models_file = staging_dir / "candidates_models.json"
    assert models_file.exists()
    models_data = json.loads(models_file.read_text())
    assert models_data["total_count"] == 82
    assert len(models_data["records"]) == 82

    # 2. candidates_models.csv
    csv_file = staging_dir / "candidates_models.csv"
    assert csv_file.exists()
    lines = [line.strip() for line in csv_file.read_text().splitlines() if line.strip()]
    assert len(lines) == 83  # 1 header + 82 records

    # 3. candidates_providers.json
    prov_file = staging_dir / "candidates_providers.json"
    assert prov_file.exists()
    prov_data = json.loads(prov_file.read_text())
    assert prov_data["total_count"] == 13

    # 4. candidates_developers.json
    dev_file = staging_dir / "candidates_developers.json"
    assert dev_file.exists()
    dev_data = json.loads(dev_file.read_text())
    assert dev_data["total_count"] == 3

    # 5. README.md
    readme_file = staging_dir / "README.md"
    assert readme_file.exists()
    assert "Promotion Safeguards" in readme_file.read_text()

    # 6. ZIP archive
    zip_file = staging_dir / "localpilot_staging_package_20261020.zip"
    assert zip_file.exists()
    assert zip_file.stat().st_size > 1000


def test_catalog_cli_commands():
    """Verify CLI commands localpilot catalog sources, providers, developers, stats, export."""
    runner = CliRunner()

    res = runner.invoke(app, ["catalog", "sources"])
    assert res.exit_code == 0
    assert "Hugging Face" in res.output or "huggingface" in res.output

    res = runner.invoke(app, ["catalog", "providers"])
    assert res.exit_code == 0
    assert "Google" in res.output
    assert "Meta" in res.output
    assert "Reflection" in res.output or "OpenAI" in res.output

    res = runner.invoke(app, ["catalog", "developers"])
    assert res.exit_code == 0
    assert "bartowski" in res.output
    assert "MLX Community" in res.output

    res = runner.invoke(app, ["catalog", "stats"])
    assert res.exit_code == 0
    assert "Initial Bundled Models" in res.output
