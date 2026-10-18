"""Tests for Phase 5 real-time monitoring and API management experience in TUI and CLI."""

import json
import pytest
from typer.testing import CliRunner

from localpilot.application.accounting_service import ApiAccountingService
from localpilot.application.api_key_service import ApiKeyService
from localpilot.application.version_service import ModelVersionService
from localpilot.cli.main import app
from localpilot.tui.app import (
    LocalPilotTUI,
    CreateKeyModal,
    KeyDisplayModal,
    RotateKeyModal,
    ConnectionSetupModal,
    CompareVersionsModal,
    SwitchAliasModal,
)
from textual.widgets import Button, DataTable, Input, Label, TabbedContent


@pytest.mark.asyncio
async def test_tui_phase5_navigation_and_tabs():
    """Verify Phase 5 tabs, badges, live bar, and tables in LocalPilotTUI."""
    tui = LocalPilotTUI()
    async with tui.run_test() as pilot:
        # Check initial home tab
        assert tui.query_one("#main-tabs", TabbedContent).active == "home-tab"

        # Check Phase 5 live status bar and badges in home tab
        assert tui.query_one("#home-live-bar") is not None
        assert tui.query_one("#home-freshness-pill", Label) is not None
        assert tui.query_one("#btn-pause-live", Button) is not None
        assert tui.query_one("#home-badge-models", Label) is not None
        assert tui.query_one("#home-badge-leases", Label) is not None
        assert tui.query_one("#home-badge-requests", Label) is not None
        assert tui.query_one("#home-badge-tokens", Label) is not None

        # Switch to Gateway tab via key 7
        await pilot.press("7")
        assert tui.query_one("#main-tabs", TabbedContent).active == "gateway-tab"
        assert tui.query_one("#gateway-keys-table", DataTable) is not None
        assert tui.query_one("#gateway-alias-table", DataTable) is not None
        assert tui.query_one("#btn-toggle-gateway", Button) is not None
        assert tui.query_one("#btn-create-key", Button) is not None
        assert tui.query_one("#btn-rotate-key", Button) is not None
        assert tui.query_one("#btn-revoke-key", Button) is not None
        assert tui.query_one("#btn-connection-setup", Button) is not None

        # Switch to Usage & Metrics tab via key 8
        await pilot.press("8")
        assert tui.query_one("#main-tabs", TabbedContent).active == "usage-tab"
        assert tui.query_one("#usage-models-table", DataTable) is not None
        assert tui.query_one("#usage-keys-table", DataTable) is not None
        assert tui.query_one("#usage-requests-table", DataTable) is not None
        assert tui.query_one("#usage-card-requests", Label) is not None
        assert tui.query_one("#usage-card-tokens", Label) is not None
        assert tui.query_one("#usage-card-token-status", Label) is not None
        assert tui.query_one("#usage-card-latency", Label) is not None
        assert tui.query_one("#usage-card-tps", Label) is not None

        # Switch to Jobs & Recovery tab via key 9
        await pilot.press("9")
        assert tui.query_one("#main-tabs", TabbedContent).active == "jobs-tab"
        assert tui.query_one("#jobs-table", DataTable) is not None
        assert tui.query_one("#btn-jobs-resume", Button) is not None
        assert tui.query_one("#btn-jobs-cleanup", Button) is not None


@pytest.mark.asyncio
async def test_tui_live_pause_toggle():
    """Verify live refresh pause toggle (P key and button)."""
    tui = LocalPilotTUI()
    async with tui.run_test() as pilot:
        assert tui._live_paused is False

        # Toggle pause via 'p' key
        await pilot.press("p")
        await pilot.pause()
        assert tui._live_paused is True
        pill = tui.query_one("#home-freshness-pill", Label)
        assert "PAUSED" in str(pill.render())

        # Toggle resume via 'p' key
        await pilot.press("p")
        await pilot.pause()
        assert tui._live_paused is False

        # Toggle pause via button
        btn = tui.query_one("#btn-pause-live", Button)
        btn.press()
        await pilot.pause()
        assert tui._live_paused is True

        # Toggle resume via button
        btn.press()
        await pilot.pause()
        assert tui._live_paused is False


@pytest.mark.asyncio
async def test_tui_time_window_filtering():
    """Verify time window selection buttons update TUI state."""
    tui = LocalPilotTUI()
    async with tui.run_test() as pilot:
        await pilot.press("8")  # Usage tab
        assert tui._selected_time_window == "24h"

        await pilot.click("#btn-usage-1h")
        assert tui._selected_time_window == "1h"

        await pilot.click("#btn-usage-7d")
        assert tui._selected_time_window == "7d"

        await pilot.click("#btn-usage-all")
        assert tui._selected_time_window == "all"


@pytest.mark.asyncio
async def test_create_and_display_key_modals():
    """Verify CreateKeyModal input capture and KeyDisplayModal security warning."""
    tui = LocalPilotTUI()
    async with tui.run_test() as pilot:
        # Test CreateKeyModal
        result_holder = []
        def _on_create_done(val):
            result_holder.append(val)

        tui.push_screen(CreateKeyModal(), _on_create_done)
        await pilot.pause()

        # Update input values
        input_app = tui.screen.query_one("#input-app-name", Input)
        input_app.value = "continue"
        input_desc = tui.screen.query_one("#input-display-name", Input)
        input_desc.value = "VS Code Dev"

        # Submit
        await pilot.click("#btn-submit-key")
        await pilot.pause()

        assert len(result_holder) == 1
        res = result_holder[0]
        assert res["app_name"] == "continue"
        assert res["display_name"] == "VS Code Dev"

        # Test KeyDisplayModal
        display_modal = KeyDisplayModal("key-123", "VS Code Dev", "continue", "sk-local-secrettoken456")
        tui.push_screen(display_modal)
        await pilot.pause()

        # Check secret is displayed and warning is present
        secret_label = tui.screen.query_one("#key-secret-text", Label)
        assert "sk-local-secrettoken456" in str(secret_label.render())
        warning_label = tui.screen.query_one("#key-display-warning", Label)
        assert "never be shown again" in str(warning_label.render()).lower()

        # Dismiss display modal
        await pilot.click("#btn-key-done")
        await pilot.pause()


@pytest.mark.asyncio
async def test_rotate_key_modal():
    """Verify RotateKeyModal explains invalidation and captures confirmation."""
    tui = LocalPilotTUI()
    async with tui.run_test() as pilot:
        result_holder = []
        def _on_rotate_done(val):
            result_holder.append(val)

        modal = RotateKeyModal("key-abc", "OpenCode Key", "sk-local-abc")
        tui.push_screen(modal, _on_rotate_done)
        await pilot.pause()

        # Check warning about immediate invalidation
        msg_label = tui.screen.query_one("#rotate-key-message", Label)
        assert "invalidate the current secret" in str(msg_label.render()).lower()

        # Click Rotate Immediately
        await pilot.click("#btn-confirm-rotate")
        await pilot.pause()

        assert len(result_holder) == 1
        assert result_holder[0] is True


@pytest.mark.asyncio
async def test_connection_setup_modal():
    """Verify ConnectionSetupModal renders snippets for OpenCode, Continue, Cursor, and Python/cURL."""
    tui = LocalPilotTUI()
    async with tui.run_test() as pilot:
        modal = ConnectionSetupModal(api_key="sk-local-mytestkey", default_client="opencode")
        tui.push_screen(modal)
        await pilot.pause()

        assert tui.screen.query_one("#client-opencode") is not None
        assert tui.screen.query_one("#client-continue") is not None
        assert tui.screen.query_one("#client-cursor") is not None
        assert tui.screen.query_one("#client-curl") is not None

        # Close guide
        await pilot.click("#btn-close-guide")
        await pilot.pause()


def test_cli_versions_and_alias_plain_and_json():
    """Verify CLI versions list and alias list support both --json and --plain flags."""
    runner = CliRunner()

    # 1. Versions list --json
    res_v_json = runner.invoke(app, ["versions", "list", "--json"])
    assert res_v_json.exit_code == 0
    parsed_v = json.loads(res_v_json.stdout)
    assert isinstance(parsed_v, list)

    # 2. Versions list --plain
    res_v_plain = runner.invoke(app, ["versions", "list", "--plain"])
    assert res_v_plain.exit_code == 0
    assert ("VERSION_ID" in res_v_plain.stdout or "No model versions found" in res_v_plain.stdout)

    # 3. Alias list --json
    res_a_json = runner.invoke(app, ["alias", "list", "--json"])
    assert res_a_json.exit_code == 0
    parsed_a = json.loads(res_a_json.stdout)
    assert isinstance(parsed_a, list)

    # 4. Alias list --plain
    res_a_plain = runner.invoke(app, ["alias", "list", "--plain"])
    assert res_a_plain.exit_code == 0
    assert ("ALIAS" in res_a_plain.stdout or "No aliases found" in res_a_plain.stdout)


def test_cli_stats_time_window_and_percentiles():
    """Verify stats command supports --time-window, p50/p95 percentiles, and honest token status."""
    runner = CliRunner()

    # Test with 24h window in JSON format
    res_stats_json = runner.invoke(app, ["stats", "--time-window", "24h", "--json"])
    assert res_stats_json.exit_code == 0
    data = json.loads(res_stats_json.stdout)

    summary = data.get("summary", data)
    assert "total_requests" in summary
    assert "p50_latency_ms" in summary
    assert "p95_latency_ms" in summary
    assert "token_reporting_status" in summary
    assert summary["token_reporting_status"] in ("none", "complete", "partial", "unreported")

    # Test with plain output
    res_stats_plain = runner.invoke(app, ["stats", "--plain"])
    assert res_stats_plain.exit_code == 0
    assert "Total Requests:" in res_stats_plain.stdout
    assert "P50 Latency (ms):" in res_stats_plain.stdout
    assert "Token Reporting:" in res_stats_plain.stdout


def test_cli_keys_list_plain_and_json():
    """Verify keys list supports both --json and --plain output."""
    runner = CliRunner()

    res_k_json = runner.invoke(app, ["keys", "list", "--json"])
    assert res_k_json.exit_code == 0
    data = json.loads(res_k_json.stdout)
    assert isinstance(data, list)

    res_k_plain = runner.invoke(app, ["keys", "list", "--plain"])
    assert res_k_plain.exit_code == 0
    assert ("KEY_ID" in res_k_plain.stdout or "No API keys found" in res_k_plain.stdout)


def test_accounting_service_unreported_tokens_honesty():
    """Verify that requests without token counts are flagged as unreported and not claimed as 0 exact tokens."""
    accounting = ApiAccountingService()

    # Record a request without token reporting (prompt_tokens=0, completion_tokens=0)
    req_id = accounting.record_request(
        key_id=None,
        model_alias="default",
        resolved_version_id="mock-v1",
        prompt_tokens=0,
        completion_tokens=0,
        latency_ms=120.0,
    )
    assert req_id is not None

    # Query usage summary
    summary = accounting.get_usage_summary(time_window="1h")
    assert summary["total_requests"] >= 1
    assert summary["unreported_token_requests"] >= 1
    assert summary["token_reporting_status"] in ("unreported", "partial")
    assert summary["unreported_token_requests"] >= 1
    assert summary["token_reporting_status"] in ("unreported", "partial")
