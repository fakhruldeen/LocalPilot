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
        # Check initial tab is home-tab
        assert tui.query_one("#main-tabs").active == "home-tab"

        # Switch to discover tab via keybinding
        await pilot.press("d")
        assert tui.query_one("#main-tabs").active == "discover-tab"

        # Check table loaded rows
        table = tui.query_one("#discover-table")
        assert table.row_count > 0

        # Switch to activity tab
        await pilot.press("a")
        assert tui.query_one("#main-tabs").active == "activity-tab"

        # Open help modal via ? keybinding
        await pilot.press("question_mark")
        # Pop screen
        await pilot.press("escape")
