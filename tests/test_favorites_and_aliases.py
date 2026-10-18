import pytest
from typer.testing import CliRunner
from localpilot.cli.main import app
from localpilot.sources.catalog_service import CatalogService
from localpilot.storage.db import DatabaseManager, get_db

runner = CliRunner()


@pytest.fixture(autouse=True)
def reset_db(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    import localpilot.storage.db as db_mod
    db_mod._global_db = None
    yield
    db_mod._global_db = None


def test_catalog_short_alias_resolution(tmp_path):
    cat_svc = CatalogService()

    # Resolve predefined seed models by short alias
    cand_gemma = cat_svc.get_candidate("gemma2:2b")
    assert cand_gemma is not None
    assert cand_gemma.short_alias == "gemma2:2b"
    assert "gemma-2-2b" in cand_gemma.model_id.lower()

    cand_deepseek = cat_svc.get_candidate("deepseek-r1:1.5b")
    assert cand_deepseek is not None
    assert cand_deepseek.short_alias == "deepseek-r1:1.5b"

    cand_qwen = cat_svc.get_candidate("qwen2.5-coder:1.5b")
    assert cand_qwen is not None
    assert cand_qwen.short_alias == "qwen2.5-coder:1.5b"


def test_favorites_management():
    cat_svc = CatalogService()

    ref = "gemma2:2b"
    assert not cat_svc.is_favorite(ref)

    # Add favorite
    cat_svc.add_favorite(ref, notes="My favorite compact model")
    assert cat_svc.is_favorite(ref)
    favs = cat_svc.list_favorites_detailed()
    assert len(favs) == 1
    assert favs[0]["model_ref"] == ref
    assert favs[0]["notes"] == "My favorite compact model"

    # Toggle favorite off
    res = cat_svc.toggle_favorite(ref)
    assert res is False
    assert not cat_svc.is_favorite(ref)

    # Toggle favorite on
    res = cat_svc.toggle_favorite(ref)
    assert res is True
    assert cat_svc.is_favorite(ref)

    # Discover with only_favorites
    fav_models = cat_svc.discover(query="", only_favorites=True)
    assert any(m.candidate.short_alias == "gemma2:2b" for m in fav_models)


def test_cli_favorite_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    # Reset db singleton
    import localpilot.storage.db as db_mod
    db_mod._global_db = None

    # Add favorite
    res = runner.invoke(app, ["favorite", "add", "qwen2.5:3b"])
    assert res.exit_code == 0
    assert "Added" in res.output

    # List favorites
    res = runner.invoke(app, ["favorite", "list"])
    assert res.exit_code == 0
    assert "qwen2.5:3b" in res.output

    # Discover filtering by favorites
    res = runner.invoke(app, ["discover", "--favorites", "--plain"])
    assert res.exit_code == 0
    assert "qwen" in res.output.lower()

    # Toggle off
    res = runner.invoke(app, ["favorite", "toggle", "qwen2.5:3b"])
    assert res.exit_code == 0
    assert "Removed" in res.output


def test_official_models_prioritized_sorting():
    cat_svc = CatalogService()
    cands = cat_svc.discover(query="", only_compatible=False)
    assert len(cands) > 0

    # Ensure top models from major labs exist and have official flags
    official_candidates = [m.candidate for m in cands if getattr(m.candidate, "is_official", False)]
    assert len(official_candidates) >= 12

    # Verify official resource URLs and publishers
    publishers = {((cand.provenance or {}).get("publisher") or "").lower() for cand in official_candidates}
    assert "google" in publishers
    assert "meta" in publishers
    assert any("qwen" in p or "alibaba" in p for p in publishers)
    assert any("deepseek" in p for p in publishers)

    for cand in official_candidates:
        assert cand.official_resource_url is not None
        assert cand.official_resource_url.startswith("http")

    # Verify sorting: all official candidates must appear before non-official candidates
    saw_nonofficial = False
    for m in cands:
        is_off = getattr(m.candidate, "is_official", False)
        if not is_off:
            saw_nonofficial = True
        else:
            assert not saw_nonofficial, f"Official candidate {m.candidate.short_alias or m.candidate.name} appeared after a non-official candidate!"



def test_cli_discover_official_resources():
    res = runner.invoke(app, ["discover", "--plain"])
    assert res.exit_code == 0
    assert "Official" in res.output
    # Check that top official models are present in output
    assert "gemma" in res.output.lower()
    assert "llama" in res.output.lower()

