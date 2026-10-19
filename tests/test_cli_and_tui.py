"""Tests for CLI commands and Textual TUI interface."""

import json
from pathlib import Path
import pytest
from typer.testing import CliRunner

from localpilot.cli.main import app
from localpilot.tui.app import LocalPilotTUI


runner = CliRunner()


def test_cli_doctor_plain() -> None:
    result = runner.invoke(app, ["doctor", "--plain"])
    assert result.exit_code == 0
    assert "OS:" in result.stdout
    assert "System RAM:" in result.stdout


def test_cli_doctor_json() -> None:
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "hardware" in data
    assert "ram_total_bytes" in data["hardware"]


def test_cli_discover_plain_and_json() -> None:
    res_plain = runner.invoke(app, ["discover", "--plain"])
    assert res_plain.exit_code == 0
    assert "Source:" in res_plain.stdout

    res_json = runner.invoke(app, ["discover", "--json"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.stdout)
    assert isinstance(data, list)
    assert len(data) > 0


def test_cli_storage_and_activity() -> None:
    res_storage = runner.invoke(app, ["storage", "--json"])
    assert res_storage.exit_code == 0
    data = json.loads(res_storage.stdout)
    assert "total_managed_bytes" in data

    res_activity = runner.invoke(app, ["activity", "--json"])
    assert res_activity.exit_code == 0


def test_cli_guide_and_docs() -> None:
    res_guide = runner.invoke(app, ["guide"])
    assert res_guide.exit_code == 0
    assert "LocalPilot Guide" in res_guide.stdout

    res_novice = runner.invoke(app, ["guide", "novice"])
    assert res_novice.exit_code == 0
    assert "Beginner's Guide to Local AI" in res_novice.stdout

    res_technical = runner.invoke(app, ["guide", "technical"])
    assert res_technical.exit_code == 0
    assert "Technical Architecture" in res_technical.stdout

    res_docs_alias = runner.invoke(app, ["docs", "faq"])
    assert res_docs_alias.exit_code == 0
    assert "Frequently Asked Questions" in res_docs_alias.stdout


def test_cli_help_descriptions() -> None:
    res_help = runner.invoke(app, ["--help"])
    assert res_help.exit_code == 0
    assert "guide" in res_help.stdout
    assert "tui" in res_help.stdout

    res_doctor_help = runner.invoke(app, ["doctor", "--help"])
    assert res_doctor_help.exit_code == 0
    assert "Non-technical:" in res_doctor_help.stdout
    assert "Technical:" in res_doctor_help.stdout
    assert "Examples:" in res_doctor_help.stdout

    res_setup_help = runner.invoke(app, ["setup", "--help"])
    assert res_setup_help.exit_code == 0
    assert "Non-technical:" in res_setup_help.stdout
    assert "Technical:" in res_setup_help.stdout


@pytest.mark.asyncio
async def test_tui_app_smoke() -> None:
    tui = LocalPilotTUI()
    async with tui.run_test() as pilot:
        # Check initial tab is home-tab and hardware specs table is present
        assert tui.query_one("#main-tabs").active == "home-tab"
        hw_table = tui.query_one("#home-hw-table")
        assert hw_table is not None
        assert hw_table.row_count >= 5

        # Switch to discover tab via keybinding
        await pilot.press("d")
        assert tui.query_one("#main-tabs").active == "discover-tab"

        # Check default filter button is RAM: Fits Budget
        compat_btn = tui.query_one("#btn-discover-compat-toggle")
        assert "RAM: Fits Budget" in str(compat_btn.label)

        # Check table loaded rows
        table = tui.query_one("#discover-table")
        assert table.row_count > 0

        # Switch to activity tab
        await pilot.press("a")
        assert tui.query_one("#main-tabs").active == "activity-tab"

        # Switch to gateway tab via keybinding 7
        await pilot.press("7")
        assert tui.query_one("#main-tabs").active == "gateway-tab"

        # Open help modal via ? keybinding
        await pilot.press("question_mark")
        # Pop screen
        await pilot.press("escape")


def test_cli_api_and_keys_rotation():
    runner = CliRunner()

    # 1. Verify api alias matches gateway
    res_api = runner.invoke(app, ["api", "--help"])
    assert res_api.exit_code == 0
    assert "start" in res_api.stdout
    assert "status" in res_api.stdout
    assert "test" in res_api.stdout

    # 2. Test api status when not running
    res_status = runner.invoke(app, ["api", "status"])
    assert res_status.exit_code == 0
    assert "LocalPilot Gateway is not running" in res_status.stdout

    # 3. Create key, list it, and rotate it
    res_create = runner.invoke(app, ["keys", "create", "Rotatable Key", "--app", "opencode"])
    assert res_create.exit_code == 0
    assert "sk-local-" in res_create.stdout

    # Extract key_id from output
    import re
    match = re.search(r"Key ID:\s+(\S+)", res_create.stdout)
    assert match is not None
    key_id = match.group(1)

    res_rotate = runner.invoke(app, ["keys", "rotate", key_id])
    assert res_rotate.exit_code == 0
    assert "Key Rotated" in res_rotate.stdout
    assert "sk-local-" in res_rotate.stdout


@pytest.mark.asyncio
async def test_tui_update_list_button_and_first_run():
    from localpilot.sources.catalog_service import CatalogService
    cat = CatalogService()
    assert isinstance(cat.is_first_run(), bool)

    tui = LocalPilotTUI()
    async with tui.run_test() as pilot:
        await pilot.press("d")
        assert tui.query_one("#main-tabs").active == "discover-tab"
        refresh_btn = tui.query_one("#btn-discover-refresh")
        assert refresh_btn is not None
        assert "Update List" in str(refresh_btn.label)

        # Trigger catalog refresh action
        tui.action_refresh_catalog_action()
        await pilot.pause()


