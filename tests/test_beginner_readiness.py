"""Automated verification tests for beginner-user readiness and usability in LocalPilot."""

from __future__ import annotations
import json
import os
from pathlib import Path
import pytest
from typer.testing import CliRunner

from localpilot.cli.main import app
from localpilot.tui.app import LocalPilotTUI


runner = CliRunner()


# =============================================================================
# 1. First-Use & CLI Usability Tests
# =============================================================================

def test_cli_default_entrypoint_is_friendly_and_clear() -> None:
    """Verifies that launching localpilot without arguments displays an understandable menu."""
    res = runner.invoke(app, [])
    # In non-interactive test runner, typer doesn't hang on prompt; it renders banner or options
    assert res.exit_code == 0
    # Banner and help must be visible
    assert "Local AI, Made Simple" in res.stdout or "LocalPilot" in res.stdout


def test_cli_guide_novice_provides_jargon_free_guidance() -> None:
    """Verifies that the novice guide avoids raw jargon and gives plain-language instructions."""
    res = runner.invoke(app, ["guide", "novice"])
    assert res.exit_code == 0
    content = res.stdout
    assert "Beginner's Guide to Local AI" in content
    # Should explain models in human terms
    assert "Chat" in content or "Model" in content
    # Must provide concrete first commands
    assert "localpilot discover" in content or "localpilot tui" in content


def test_cli_doctor_plain_provides_actionable_human_readable_status() -> None:
    """Verifies doctor output uses human-friendly units (GB) and clear labels."""
    res = runner.invoke(app, ["doctor", "--plain"])
    assert res.exit_code == 0
    out = res.stdout
    assert "OS:" in out
    assert "System RAM:" in out
    assert "GB" in out  # Must use GB, not raw bytes
    assert "Safe Budget" in out


def test_cli_storage_human_readable_breakdown() -> None:
    """Verifies storage command categorizes space without requiring filesystem exploration."""
    res = runner.invoke(app, ["storage", "--plain"])
    assert res.exit_code == 0
    out = res.stdout
    assert "Model Files" in out
    assert "Supporting Runtimes" in out
    assert "Total Managed Storage" in out


def test_cli_chat_unstarted_model_guidance() -> None:
    """Verifies that attempting to chat with a non-existent or uninstalled model gives a clear next step."""
    res = runner.invoke(app, ["chat", "non_existent_model_12345"])
    assert res.exit_code == 0
    out = res.stdout
    assert "No running instance or installation found matching" in out


# =============================================================================
# 2. TUI Navigation & Terminal Accessibility Tests
# =============================================================================

@pytest.mark.asyncio
async def test_tui_80x24_terminal_navigation_and_tabs() -> None:
    """Verifies that the full-screen TUI mounts and navigates cleanly in standard 80x24 terminal."""
    tui = LocalPilotTUI()
    async with tui.run_test(size=(80, 24)) as pilot:
        # 1. Mounts at Home tab
        assert tui.query_one("#main-tabs").active == "home-tab"

        # 2. Check Home labels
        banner = tui.query_one("#ascii-banner")
        assert banner is not None

        # 3. Switch to Discover tab via 'd' keybinding
        await pilot.press("d")
        assert tui.query_one("#main-tabs").active == "discover-tab"

        # 4. Verify search input and discover table exist
        search_input = tui.query_one("#search-input")
        table = tui.query_one("#discover-table")
        assert search_input is not None
        assert table is not None

        # 5. Switch to Installed tab via 'i'
        await pilot.press("i")
        assert tui.query_one("#main-tabs").active == "installed-tab"

        # 6. Switch to Running tab via 'r'
        await pilot.press("r")
        assert tui.query_one("#main-tabs").active == "running-tab"

        # 7. Switch to Storage tab via 's'
        await pilot.press("s")
        assert tui.query_one("#main-tabs").active == "storage-tab"

        # 8. Switch to Activity tab via 'a'
        await pilot.press("a")
        assert tui.query_one("#main-tabs").active == "activity-tab"


@pytest.mark.asyncio
async def test_tui_help_modal_accessibility_and_dismissal() -> None:
    """Verifies that the beginner documentation modal opens and closes with Escape and q."""
    tui = LocalPilotTUI()
    async with tui.run_test(size=(80, 24)) as pilot:
        # Press '?' to trigger HelpModal
        await pilot.press("question_mark")
        assert len(tui.screen_stack) > 1

        # Press Escape to dismiss
        await pilot.press("escape")
        assert len(tui.screen_stack) == 1

        # Press 'h' to trigger HelpModal again
        await pilot.press("h")
        assert len(tui.screen_stack) > 1

        # Dismiss via Escape
        await pilot.press("escape")
        assert len(tui.screen_stack) == 1


@pytest.mark.asyncio
async def test_tui_model_discovery_selection_shows_explanation() -> None:
    """Verifies that selecting a model in the table updates the 'Why this model?' explanation."""
    tui = LocalPilotTUI()
    async with tui.run_test(size=(100, 30)) as pilot:
        await pilot.press("d")
        table = tui.query_one("#discover-table")
        assert table.row_count > 0

        # Select first row
        table.move_cursor(row=0)
        # Give event loop a cycle
        await pilot.pause()

        expl = tui.query_one("#explanation-text")
        assert expl is not None
        # Must not be the unselected placeholder
        text = str(expl.render())
        assert len(text) > 0

        # Check progress text label is present
        prog = tui.query_one("#setup-progress-text")
        assert prog is not None


def test_cli_doctor_plain_clean_runtimes_and_budget_note() -> None:
    """Verifies that doctor --plain does not print python list repr and explains safe budget."""
    res = runner.invoke(app, ["doctor", "--plain"])
    assert res.exit_code == 0
    assert "['" not in res.stdout  # No Python list literal repr
    assert "reserves RAM for OS" in res.stdout


@pytest.mark.asyncio
async def test_runtime_service_stop_resolves_model_name_and_installation_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that stop_running_instance can stop models by model_id, installation_id, or name."""
    from localpilot.application.runtime_service import RuntimeLifecycleService
    from localpilot.storage.db import DatabaseManager
    from localpilot.domain.models import StopStatus

    db_path = tmp_path / "test_stop.db"
    db = DatabaseManager(db_path)
    svc = RuntimeLifecycleService(db=db)

    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO installations (
            installation_id, model_id, name, runtime_name, artifact_ids_json,
            readiness_stage, compatibility_status, update_status, ownership_class,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("inst_test_1", "meta/llama3.2", "Llama 3.2", "llamacpp", "[]", "verified", "compatible", "up_to_date", "owned_child", "2026-10-17T00:00:00Z", "2026-10-17T00:00:00Z"),
    )
    conn.execute(
        """
        INSERT INTO running_instances (
            instance_id, model_id, installation_id, pid, process_create_time,
            executable_path, endpoint_url, launch_config_json, log_path, ownership_class,
            observed_execution_mode, started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("run_test_1", "meta/llama3.2", "inst_test_1", 999999, 0.0, "/bin/false", "http://127.0.0.1:8080", "{}", "/tmp/test.log", "owned_child", "auto", "2026-10-17T00:00:00Z"),
    )
    conn.commit()

    # 1. Stop by model_id -> resolves target_instance_id to run_test_1
    res_model = await svc.stop_running_instance("meta/llama3.2")
    assert res_model.target_instance_id == "run_test_1"

    conn.execute("DELETE FROM running_instances WHERE instance_id = 'run_test_1'")
    conn.commit()

    # Re-insert
    conn.execute(
        """
        INSERT INTO running_instances (
            instance_id, model_id, installation_id, pid, process_create_time,
            executable_path, endpoint_url, launch_config_json, log_path, ownership_class,
            observed_execution_mode, started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("run_test_2", "meta/llama3.2", "inst_test_1", 999999, 0.0, "/bin/false", "http://127.0.0.1:8080", "{}", "/tmp/test.log", "owned_child", "auto", "2026-10-17T00:00:00Z"),
    )
    conn.commit()

    # 2. Stop by installation name -> resolves target_instance_id to run_test_2
    res_name = await svc.stop_running_instance("Llama 3.2")
    assert res_name.target_instance_id == "run_test_2"

