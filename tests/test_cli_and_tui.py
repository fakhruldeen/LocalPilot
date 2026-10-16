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
