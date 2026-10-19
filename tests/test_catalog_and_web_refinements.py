"""Unit tests verifying web reorganization, TUI catalog statistics, ASCII banner, and new providers."""

import os
from pathlib import Path
import pytest
from textual.widgets import DataTable, Button, Label, Checkbox

from localpilot.cli.formatting import HOME_ASCII_BANNER
from localpilot.data import (
    get_initial_sources,
    get_initial_providers,
    get_initial_developers,
    get_initial_models,
)
from localpilot.sources.catalog_service import CatalogService
from localpilot.tui.app import (
    LocalPilotTUI,
    ComprehensiveFilterModal,
    CatalogStatsModal,
    get_catalog_statistics_rows,
)


def test_no_html_in_root_or_scattered():
    """Verify that all website html files are served from /web and nowhere else."""
    repo_root = Path(__file__).resolve().parent.parent

    # No html files in root
    root_htmls = list(repo_root.glob("*.html"))
    assert not root_htmls, f"Found HTML files in root directory: {root_htmls}"

    # No html files in ar/
    ar_htmls = list((repo_root / "ar").glob("*.html"))
    assert not ar_htmls, f"Found HTML files in ar/ directory: {ar_htmls}"

    # No html files in docs/
    docs_htmls = list((repo_root / "docs").glob("*.html")) + list((repo_root / "docs" / "ar").glob("*.html"))
    assert not docs_htmls, f"Found HTML files in docs/ directory: {docs_htmls}"

    # Web directory has all website pages
    web_dir = repo_root / "web"
    assert web_dir.is_dir()
    expected_pages = ["index.html", "guide.html", "models.html", "install.html", "faq.html", "architecture.html"]
    for page in expected_pages:
        assert (web_dir / page).is_file(), f"Missing web/{page}"
        assert (web_dir / "ar" / page).is_file(), f"Missing web/ar/{page}"


def test_ascii_art_banner_first_line_tab_aligned():
    """Verify HOME_ASCII_BANNER first line starts with a tab character to move tab to the right."""
    lines = HOME_ASCII_BANNER.splitlines()
    assert len(lines) == 5
    # First line starts with a tab
    assert lines[0].startswith("\t"), "First line of HOME_ASCII_BANNER must start with a tab"
    assert "██" in lines[0]


def test_catalog_statistics_rows():
    """Verify get_catalog_statistics_rows provides the 7 expected categories."""
    rows = get_catalog_statistics_rows()
    assert len(rows) == 7
    categories = [r[0] for r in rows]
    assert "Initial Bundled Models" in categories
    assert "Local Cached Models" in categories
    assert "Model Registries / Sources" in categories
    assert "AI Labs / Providers" in categories
    assert "Community Developers" in categories
    assert "Favorited Models" in categories
    assert "Installed Models" in categories


def test_providers_include_kimi_groq_glm_klm():
    """Verify providers database and filter lists contain kimi, groq, glm, and klm."""
    providers = get_initial_providers()
    provider_ids = {p.get("provider_id") for p in providers}
    assert "kimi" in provider_ids
    assert "groq" in provider_ids
    assert "glm" in provider_ids
    assert "klm" in provider_ids

    # Check ComprehensiveFilterModal.PROVIDERS
    modal_prov_ids = {val.lower() for val, _ in ComprehensiveFilterModal.PROVIDERS}
    assert "kimi" in modal_prov_ids
    assert "groq" in modal_prov_ids
    assert "glm" in modal_prov_ids
    assert "klm" in modal_prov_ids
    assert "minimax" in modal_prov_ids
    assert "gpt-oss" in modal_prov_ids


def test_browser_url_resolves_cleanly_for_minilm():
    """Verify browser_url provides canonical working URL without 404 for all-MiniLM-L6-v2."""
    cat = CatalogService()
    results = cat.discover(query="minilm", only_compatible=False)
    assert len(results) > 0
    minilm = results[0].candidate
    assert minilm.browser_url == "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2"
    assert not minilm.browser_url.endswith("-GGUF")


def test_gpt_oss_and_minimas_available():
    """Verify gpt-oss and minimas (MiniMax) models are present and discoverable."""
    cat = CatalogService()
    gpt_results = cat.discover(query="gpt-oss", only_compatible=False)
    assert len(gpt_results) > 0
    assert any("GPT-OSS" in r.candidate.name for r in gpt_results)

    minimax_results = cat.discover(query="minimax", only_compatible=False)
    assert len(minimax_results) > 0
    assert any("MiniMax" in r.candidate.name for r in minimax_results)


def test_2025_models_present_in_catalog():
    """Verify that modern 2025 models are present in initial catalog."""
    models = get_initial_models()
    models_2025 = [m for m in models if m.get("release_date_display", "").startswith("2025")]
    assert len(models_2025) >= 15, f"Expected at least 15 models from 2025, found {len(models_2025)}"
    names_2025 = {m.get("name", "") for m in models_2025}
    assert any("DeepSeek R1" in n for n in names_2025)
    assert any("Phi-4" in n for n in names_2025)
    assert any("Qwen 2.5 Coder" in n for n in names_2025)
    assert any("MiniMax" in n for n in names_2025)


def test_catalog_service_discover_with_new_providers():
    """Verify CatalogService can discover models by kimi, groq, glm, and klm."""
    cat = CatalogService()
    for prov in ("kimi", "groq", "glm", "klm"):
        results = cat.discover(provider=prov, only_compatible=False)
        assert len(results) > 0, f"Expected models matching provider '{prov}', got 0"


@pytest.mark.asyncio
async def test_tui_catalog_statistics_and_filter_multi_lines():
    """Verify TUI has Catalog Statistics table on Home tab and multi-line capabilities in filter dialog."""
    tui = LocalPilotTUI()
    async with tui.run_test(size=(120, 45)) as pilot:
        await pilot.pause()

        # Check Home tab has the catalog stats card and table
        stats_table = tui.query_one("#home-catalog-stats-table", DataTable)
        assert stats_table is not None
        assert stats_table.row_count == 7

        # Open ComprehensiveFilterModal and verify quick buttons in several lines
        tui.action_open_comprehensive_filter()
        await pilot.pause()

        modal = tui.screen
        assert isinstance(modal, ComprehensiveFilterModal)
        # Check quick buttons for different lines
        btn_code = modal.query_one("#btn-quick-code", Button)
        btn_writing = modal.query_one("#btn-quick-writing", Button)
        btn_embed = modal.query_one("#btn-quick-embed", Button)
        assert btn_code is not None
        assert btn_writing is not None
        assert btn_embed is not None

        # Test clicking quick writing filter button
        await pilot.click("#btn-quick-writing")
        chk_writing = modal.query_one("#chk-filter-cap-writing", Checkbox)
        chk_code = modal.query_one("#chk-filter-cap-coding", Checkbox)
        assert chk_writing.value is True
        assert chk_code.value is False

        await pilot.click("#btn-filter-cancel")
        await pilot.pause()


def test_web_server_serves_from_web_directory():
    """Verify WebServer serves static assets from /web directory over loopback HTTP."""
    import httpx
    from localpilot.application.web_service import WebServer, get_web_dir

    web_dir = get_web_dir()
    assert web_dir.is_dir()
    assert (web_dir / "index.html").is_file()

    server = WebServer(host="127.0.0.1", port=0, web_dir=web_dir)
    server.start(blocking=False, open_browser=False)
    actual_port = server.server.server_address[1]

    try:
        base_url = f"http://127.0.0.1:{actual_port}"
        # 1. Fetch index.html
        resp = httpx.get(f"{base_url}/")
        assert resp.status_code == 200
        assert "<title>" in resp.text
        assert "LocalPilot" in resp.text

        # 2. Fetch models.html
        resp_models = httpx.get(f"{base_url}/models.html")
        assert resp_models.status_code == 200
        assert "LocalPilot" in resp_models.text

        # 3. Fetch data/models.json
        resp_data = httpx.get(f"{base_url}/data/models.json")
        assert resp_data.status_code == 200
        models_json = resp_data.json()
        assert "models" in models_json
    finally:
        server.stop()


def test_gateway_serves_website_pages():
    """Verify GatewayServer serves website pages and data from /web while preserving API."""
    import tempfile
    import threading
    import httpx
    from localpilot.gateway.server import GatewayServer
    from localpilot.storage.db import DatabaseManager

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db = DatabaseManager(tmp_path / "gw_web_test.db")

        server = GatewayServer(host="127.0.0.1", port=0, db=db, data_dir=tmp_path)
        actual_port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        base_url = f"http://127.0.0.1:{actual_port}"
        try:
            # 1. Standard API health still returns JSON
            r_health = httpx.get(f"{base_url}/health")
            assert r_health.status_code == 200
            assert r_health.json()["status"] == "ok"

            # 2. Browser request to / with text/html Accept header serves website index.html
            r_ui = httpx.get(f"{base_url}/", headers={"Accept": "text/html,application/xhtml+xml"})
            assert r_ui.status_code == 200
            assert "text/html" in r_ui.headers["Content-Type"]
            assert "<!DOCTYPE html>" in r_ui.text

            # 3. Fetching models.html through gateway
            r_models = httpx.get(f"{base_url}/models.html")
            assert r_models.status_code == 200
            assert "<!DOCTYPE html>" in r_models.text

            # 4. Fetching data/sources.json through gateway
            r_sources = httpx.get(f"{base_url}/data/sources.json")
            assert r_sources.status_code == 200
            assert "sources" in r_sources.json()
        finally:
            server.shutdown()
            server.server_close()

