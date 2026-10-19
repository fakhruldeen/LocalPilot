"""Tests for the goal-oriented TUI tabs: Tools, Search, Benchmarks, and System Doctor."""

import pytest
from localpilot.tui.app import LocalPilotTUI
from textual.widgets import DataTable, TabbedContent


@pytest.mark.asyncio
async def test_tui_goal_oriented_tabs():
    tui = LocalPilotTUI()
    async with tui.run_test(size=(120, 40)) as pilot:
        # Check tools-tab
        tabs = tui.query_one("#main-tabs", TabbedContent)
        tabs.active = "tools-tab"
        await pilot.pause()
        assert tabs.active == "tools-tab"
        tools_table = tui.query_one("#tools-table", DataTable)
        assert tools_table is not None
        assert tools_table.row_count >= 18

        # Check search-tab
        tabs.active = "search-tab"
        await pilot.pause()
        assert tabs.active == "search-tab"
        search_table = tui.query_one("#search-collections-table", DataTable)
        assert search_table is not None
        assert search_table.row_count >= 1

        # Check bench-tab
        tabs.active = "bench-tab"
        await pilot.pause()
        assert tabs.active == "bench-tab"
        bench_table = tui.query_one("#bench-table", DataTable)
        assert bench_table is not None
        assert bench_table.row_count >= 1

        # Check doctor-tab
        tabs.active = "doctor-tab"
        await pilot.pause()
        assert tabs.active == "doctor-tab"
        doctor_table = tui.query_one("#doctor-findings-table", DataTable)
        assert doctor_table is not None
        assert doctor_table.row_count >= 1
