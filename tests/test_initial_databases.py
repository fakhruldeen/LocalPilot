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
    ]

    for d in dirs:
        assert d.exists() and d.is_dir(), f"Directory {d} does not exist"
        for fname in expected_files:
            fpath = d / fname
            assert fpath.exists(), f"File {fpath} does not exist"
            assert fpath.stat().st_size > 0, f"File {fpath} is empty"


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
        assert "release_date_display" in m
        assert "capabilities" in m
        assert isinstance(m["capabilities"], list)


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

    res = runner.invoke(app, ["catalog", "developers"])
    assert res.exit_code == 0
    assert "bartowski" in res.output

    res = runner.invoke(app, ["catalog", "stats"])
    assert res.exit_code == 0
    assert "Initial Bundled Models" in res.output
