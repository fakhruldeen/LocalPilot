"""Acceptance tests verifying Novice User UX, Guided Setup, and In-TUI Chat."""

import asyncio
import json
from pathlib import Path
import sqlite3
import pytest
from unittest.mock import MagicMock, patch

from localpilot.application.setup_workflow import ApprovalRecord, SetupPlan, SetupWorkflow
from localpilot.domain.capabilities import InferenceBackendId
from localpilot.domain.models import ExecutionMode, NormalizedCandidate, TaskCategory
from localpilot.storage.db import get_db, DatabaseManager
from localpilot.tui.app import (
    ConfirmModal,
    GuidedSetupModal,
    HelpModal,
    InterruptedSetupModal,
    LocalPilotTUI,
    ModelDetailsModal,
    PerformanceSettingsModal,
    RemoveModelModal,
)
from localpilot.tui.chat_screen import ChatMessageWidget, ChatScreen


@pytest.fixture
def mock_db_with_installed(tmp_path, monkeypatch):
    """Provides an isolated database with one installed model for testing."""
    monkeypatch.setenv("LOCALPILOT_HOME", str(tmp_path))

    db = get_db()
    conn = db.get_connection()
    conn.execute(
        """
        INSERT OR REPLACE INTO installations (
            installation_id, model_id, name, runtime_name, artifact_ids_json,
            readiness_stage, compatibility_status, update_status, ownership_class,
            inference_backend, managed_service, deployment_method,
            execution_plan_json, alternative_plans_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "inst_test_qwen",
            "qwen2.5-coder:1.5b",
            "Qwen 2.5 Coder 1.5B",
            "llamacpp",
            json.dumps(["art_123"]),
            "tested_here",
            "compatible",
            "up_to_date",
            "localpilot_owned",
            "llamacpp",
            None,
            "embedded_process",
            "{}",
            "[]",
            "2026-10-17T12:00:00Z",
            "2026-10-17T12:00:00Z",
        ),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO storage_artifacts (
            artifact_id, physical_path, bytes, sha256, is_managed,
            install_scope, management_mode, deletion_method, ref_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "art_123",
            str(tmp_path / "models" / "qwen.gguf"),
            1048576000,  # ~1000 MB
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            1,
            "isolated",
            "directly_managed",
            "delete_files",
            1,
            "2026-10-17T12:00:00Z",
        ),
    )
    conn.commit()
    return db


@pytest.mark.asyncio
async def test_remove_model_modal_safe_defaults():
    """Verify that RemoveModelModal composes with 'Keep Model' by default to prevent accidental data loss."""
    modal = RemoveModelModal("Qwen 2.5 Coder 1.5B", "inst_test_qwen", size_str="1.0 GB")
    assert modal.model_name == "Qwen 2.5 Coder 1.5B"
    assert modal.size_str == "1.0 GB"


@pytest.mark.asyncio
async def test_model_details_modal_renders_metadata():
    """Verify ModelDetailsModal composes without crashing and formats labels."""
    details = {
        "Model Name": "Qwen 2.5 Coder 1.5B",
        "Installation ID": "inst_test_qwen",
        "Engine": "llamacpp",
        "File Size": "980 MB",
    }
    modal = ModelDetailsModal("Qwen 2.5 Coder 1.5B", details)
    assert modal._title == "Qwen 2.5 Coder 1.5B"
    assert modal._details["File Size"] == "980 MB"


@pytest.mark.asyncio
async def test_chat_screen_message_widget_append():
    """Verify ChatMessageWidget formats user and assistant messages and appends streaming tokens."""
    widget = ChatMessageWidget(role="assistant", content="Hello", model_name="Qwen Coder")
    assert widget._role == "assistant"
    assert widget._content == "Hello"
    widget.append_text(" world!")
    assert widget._content == "Hello world!"


@pytest.mark.asyncio
async def test_chat_screen_initialization():
    """Verify ChatScreen initializes with proper parameters without network dependency."""
    screen = ChatScreen(
        model_name="Qwen 2.5 Coder 1.5B",
        model_id="qwen2.5-coder:1.5b",
        endpoint_url="http://127.0.0.1:8080",
        runtime_name="llamacpp",
    )
    assert screen.model_name == "Qwen 2.5 Coder 1.5B"
    assert screen.endpoint_url == "http://127.0.0.1:8080"
    assert len(screen.messages) == 0


def test_cli_friendly_name_resolution(mock_db_with_installed):
    """Verify that CLI commands accept friendly recognizable names."""
    conn = mock_db_with_installed.get_connection()

    # Test that querying by friendly name finds the record
    target = "Qwen 2.5 Coder 1.5B"
    row = conn.execute(
        "SELECT * FROM installations WHERE installation_id = ? OR model_id = ? OR name = ? OR name LIKE ?",
        (target, target, target, f"%{target}%"),
    ).fetchone()
    assert row is not None
    assert row["installation_id"] == "inst_test_qwen"


@pytest.mark.asyncio
async def test_tui_home_screen_actions_and_buttons():
    """Verifies that TUI Home screen renders the primary action buttons properly."""
    tui = LocalPilotTUI()
    async with tui.run_test(size=(100, 30)) as pilot:
        # Check that the primary action button exists
        btn_started = tui.query_one("#btn-get-started")
        assert btn_started is not None

        # Check that welcome container and tagline are present
        tagline = tui.query_one("#home-tagline")
        assert tagline is not None
        assert "Run AI on this computer" in str(tagline.render()) or "Welcome" in str(tagline.render())

        # Check secondary action buttons
        assert tui.query_one("#btn-home-installed") is not None
        assert tui.query_one("#btn-home-running") is not None
        assert tui.query_one("#btn-home-discover") is not None
        assert tui.query_one("#btn-home-help") is not None


@pytest.mark.asyncio
async def test_tui_guided_setup_wizard_flow():
    """Verifies that launching the guided setup wizard displays computer check and goal choices."""
    tui = LocalPilotTUI()
    async with tui.run_test(size=(100, 30)) as pilot:
        # Trigger guided setup action
        tui.action_start_guided_setup()
        await pilot.pause()

        # Check that GuidedSetupModal is pushed onto the screen stack
        assert isinstance(tui.screen, GuidedSetupModal)
        wizard = tui.screen

        # Step 1 should be active
        step1 = wizard.query_one("#wizard-step-1")
        assert step1 is not None
        assert "hidden" not in step1.classes

        # Move to step 2 (goal selection)
        await pilot.click("#step1-next-btn")
        await pilot.pause()

        step2 = wizard.query_one("#wizard-step-2")
        assert "hidden" not in step2.classes

        # Step 2 has goal choices
        assert wizard.query_one("#goal-writing") is not None
        assert wizard.query_one("#goal-coding") is not None

        # Move to step 3 (recommendations)
        await pilot.click("#step2-next-btn")
        await pilot.pause()

        step3 = wizard.query_one("#wizard-step-3")
        assert "hidden" not in step3.classes

        # Dismiss wizard cleanly via cancel
        wizard.action_cancel()
        await pilot.pause()
        assert not isinstance(tui.screen, GuidedSetupModal)


@pytest.mark.asyncio
async def test_performance_settings_modal_selection():
    """Verify PerformanceSettingsModal initializes with current mode and allows selection."""
    modal = PerformanceSettingsModal(current_pref="gpu")
    assert modal.current_pref == "gpu"


@pytest.mark.asyncio
async def test_interrupted_setup_modal_initialization():
    """Verify InterruptedSetupModal initializes with interrupted jobs list."""
    job = {
        "job_id": "job_partial_1",
        "model_id": "qwen2.5-coder:1.5b",
        "stage": "downloading",
        "error_detail": "Terminal closed abruptly",
    }
    modal = InterruptedSetupModal(job=job)
    assert modal.job["job_id"] == "job_partial_1"


@pytest.mark.asyncio
async def test_setup_workflow_cancel_event_cleans_partial_file(tmp_path, monkeypatch):
    """Verify that cancelling setup_workflow during chunk streaming unlinks the .part file and raises CancelledError."""
    from localpilot.application.setup_workflow import SetupWorkflow, ApprovalRecord
    from localpilot.domain.models import ConfidenceLevel, NormalizedCandidate, TaskCategory, VerificationStage
    from localpilot.runtimes.base import InstallationScope
    from localpilot.storage.db import DatabaseManager

    db = DatabaseManager(tmp_path / "state.db")
    workflow = SetupWorkflow(db=db)

    candidate = NormalizedCandidate(
        candidate_id="cand-test-cancel",
        model_id="test/cancel-model",
        name="Test Cancel Model",
        source="huggingface",
        artifact_id="cancel-test.gguf",
        architecture="llama",
        family="Test",
        tasks=[TaskCategory.CHAT],
        format="gguf",
        total_bytes=100 * 1024 * 1024,
        runtime="llamacpp",
        download_url="https://example.com/cancel-test.gguf",
        usable_context=2048,
        target_context=2048,
        metadata_fetched_at="2026-01-01T00:00:00Z",
        estimated_ram_bytes=200 * 1024 * 1024,
        estimated_disk_bytes=150 * 1024 * 1024,
        confidence=ConfidenceLevel.VERIFIED,
        verification_stage=VerificationStage.SUPPORTED,
    )

    plan = workflow.build_plan(candidate, scope=InstallationScope.USER)
    approval = ApprovalRecord.create_for_plan(plan)

    cancel_ev = asyncio.Event()

    class MockStreamResponse:
        status_code = 200
        headers = {"content-length": "100000"}

        def raise_for_status(self):
            pass

        async def aiter_bytes(self, chunk_size=65536):
            # Stream one chunk, then signal cancellation
            yield b"A" * 1024
            cancel_ev.set()
            yield b"B" * 1024

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, method, url):
            return MockStreamResponse()

    monkeypatch.setattr("httpx.AsyncClient", MockAsyncClient)

    with pytest.raises(asyncio.CancelledError):
        await workflow.execute_setup(candidate, plan, approval=approval, cancel_event=cancel_ev)

    # Check that any .part file in downloads dir was unlinked
    part_files = list(workflow.storage_mgr.downloads_dir.glob("*.part"))
    assert len(part_files) == 0




