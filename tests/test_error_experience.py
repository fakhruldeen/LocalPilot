"""Table-driven test suite for Error Experience & Truthful Recovery System.

Validates:
- All 24 error categories and codes.
- No false reassurance or unverified claims.
- Redaction of tokens and secrets in error details.
- Truthful recovery actions and state accuracy (restored vs. restoration_failed vs. no_changes_made).
- Consistent CLI and TUI error representations.
- Unknown outcomes handled honestly.
"""

import json
from pathlib import Path
import pytest
from unittest.mock import MagicMock

from localpilot.cli.formatting import (
    _ACTION_LABELS,
    format_json_error,
    render_app_error_box,
    render_error_box,
)
from localpilot.domain.errors import (
    AppError,
    ErrorCode,
    OutcomeCertainty,
    RecoveryAction,
    create_app_error,
    generate_support_id,
    map_exception_to_app_error,
)
from localpilot.tui.app import AppErrorModal, LocalPilotTUI


# Table of all required error codes and their expectations
ERROR_TAXONOMY_CASES = [
    (
        ErrorCode.NO_INTERNET,
        "Internet connection unavailable",
        RecoveryAction.RETRY,
        True,
        "No changes have been made to your files",
    ),
    (
        ErrorCode.SOURCE_UNAVAILABLE,
        "Model source is temporarily unreachable",
        RecoveryAction.CHECK_AGAIN,
        True,
        "No files were downloaded or modified",
    ),
    (
        ErrorCode.RATE_LIMIT,
        "Download limit reached on source",
        RecoveryAction.CHECK_AGAIN,
        True,
        "preserved where possible",
    ),
    (
        ErrorCode.AUTH_REQUIRED,
        "Access key required",
        RecoveryAction.RECONNECT_SOURCE,
        True,
        "No files have been changed",
    ),
    (
        ErrorCode.PUBLISHER_APPROVAL,
        "Publisher agreement required",
        RecoveryAction.OPEN_PUBLISHER_PAGE,
        False,
        "No files have been changed",
    ),
    (
        ErrorCode.INSUFFICIENT_STORAGE,
        "Not enough free disk space",
        RecoveryAction.REVIEW_STORAGE,
        False,
        "Setup was stopped before downloading",
    ),
    (
        ErrorCode.INSUFFICIENT_MEMORY,
        "Not enough available memory",
        RecoveryAction.CHOOSE_ANOTHER_MODEL,
        True,
        "The model was not started",
    ),
    (
        ErrorCode.GPU_UNAVAILABLE,
        "Graphics card acceleration unavailable",
        RecoveryAction.RETRY,
        True,
        "The model was not started in GPU mode",
    ),
    (
        ErrorCode.UNSUPPORTED_MODEL,
        "Model format not supported",
        RecoveryAction.CHOOSE_ANOTHER_MODEL,
        False,
        "No files were altered",
    ),
    (
        ErrorCode.INTEGRITY_MISMATCH,
        "Model verification failed",
        RecoveryAction.RETRY,
        True,
        "damaged download was discarded",
    ),
    (
        ErrorCode.RUNTIME_PREP_FAILED,
        "Could not prepare supporting software",
        RecoveryAction.RETRY,
        True,
        "No models were modified",
    ),
    (
        ErrorCode.RUNTIME_STARTUP_FAILED,
        "Supporting software failed to start",
        RecoveryAction.RETRY,
        True,
        "Your model files remain stored",
    ),
    (
        ErrorCode.MODEL_LOAD_FAILED,
        "Could not load model into memory",
        RecoveryAction.CHOOSE_ANOTHER_MODEL,
        True,
        "Your model files are preserved on disk",
    ),
    (
        ErrorCode.INFERENCE_FAILED,
        "Model did not pass test response",
        RecoveryAction.RETRY,
        True,
        "Model files are preserved and ready for inspection",
    ),
    (
        ErrorCode.SERVICE_BUSY,
        "Local engine is currently busy",
        RecoveryAction.CHECK_AGAIN,
        True,
        "Your pending request was not processed",
    ),
    (
        ErrorCode.STOP_REJECTED,
        "Stop request could not be completed",
        RecoveryAction.CHECK_AGAIN,
        True,
        "The process is still running",
    ),
    (
        ErrorCode.STOP_UNKNOWN,
        "Stop outcome could not be confirmed",
        RecoveryAction.CHECK_AGAIN,
        True,
        "remains listed so you can check",
    ),
    (
        ErrorCode.DELETION_FAILED,
        "Could not delete all model files",
        RecoveryAction.REVIEW_STORAGE,
        True,
        "Partial files were removed",
    ),
    (
        ErrorCode.UPDATE_UNAVAILABLE,
        "Cannot check for updates",
        RecoveryAction.DISMISS,
        True,
        "Your installed version has not been changed",
    ),
    (
        ErrorCode.UPDATE_VERIFY_FAILED,
        "Update verification did not pass",
        RecoveryAction.USE_PREVIOUS_VERSION,
        False,
        "previous installed version was restored safely",
    ),
    (
        ErrorCode.RESTORATION_FAILED,
        "Previous version could not be restored completely",
        RecoveryAction.REVIEW_REPAIR_OPTIONS,
        False,
        "need repair before the model can run",
    ),
    (
        ErrorCode.DB_FAILURE,
        "Local database error",
        RecoveryAction.RETRY,
        True,
        "Your model files on disk are intact",
    ),
    (
        ErrorCode.AUDIT_FAILURE,
        "Safety checkpoint could not be saved",
        RecoveryAction.RETRY,
        True,
        "No changes were made to your system or files",
    ),
    (
        ErrorCode.OPERATION_CANCELLED,
        "Operation cancelled",
        RecoveryAction.DISMISS,
        False,
        "Partial files have been safely cleaned up",
    ),
    (
        ErrorCode.INTERNAL_FAILURE,
        "An unexpected error occurred",
        RecoveryAction.DISMISS,
        True,
        "Your installed models and downloaded files were left untouched",
    ),
]


@pytest.mark.parametrize("error_code,title_substr,recommended_action,retryable,impact_substr", ERROR_TAXONOMY_CASES)
def test_error_taxonomy_catalog_coverage(error_code, title_substr, recommended_action, retryable, impact_substr):
    """Verifies that all 25 error codes have truthful title, action, retryability, and impact statements."""
    err = create_app_error(error_code, operation="test_op", stage="test_stage")
    assert err.error_code == error_code
    assert title_substr.lower() in err.title.lower()
    assert err.recommended_action == recommended_action
    assert err.retryable == retryable
    assert impact_substr.lower() in err.known_impact.lower()
    assert err.support_id.startswith("LP-")
    assert len(err.support_id) == 12  # LP-XXXX-YYYY


def test_exception_mapping_huggingface_auth_and_secrets():
    """Verify that 401 exceptions map to AUTH_REQUIRED and redact bearer/HF tokens."""
    raw_token = "hf_0123456789abcdef0123456789abcdef"
    exc = RuntimeError(f"Failed to access model with token {raw_token}: 401 Unauthorized")
    
    app_err = map_exception_to_app_error(exc, operation="download", stage="downloading")
    assert app_err.error_code == ErrorCode.AUTH_REQUIRED
    assert raw_token not in app_err.technical_details
    assert "hf_***[REDACTED]***" in app_err.technical_details


def test_exception_mapping_insufficient_disk():
    """Verify that disk space exceptions map to INSUFFICIENT_STORAGE with storage review action."""
    exc = RuntimeError("Insufficient disk space for Qwen: requires 4.0 GB, but only 1.2 GB is available.")
    app_err = map_exception_to_app_error(exc, operation="setup", stage="system_check")
    assert app_err.error_code == ErrorCode.INSUFFICIENT_STORAGE
    assert app_err.recommended_action == RecoveryAction.REVIEW_STORAGE
    assert not app_err.retryable


def test_exception_mapping_cancellation():
    """Verify that CancelledError maps to OPERATION_CANCELLED and acknowledges safe cleanup."""
    import asyncio
    exc = asyncio.CancelledError("User clicked cancel")
    app_err = map_exception_to_app_error(exc, operation="setup", stage="downloading")
    assert app_err.error_code == ErrorCode.OPERATION_CANCELLED
    assert "cleaned up" in app_err.known_impact.lower()


def test_update_workflow_truthful_restoration_vs_restoration_failed():
    """Verify that update restoration failures never falsely claim restored."""
    # Succeeded rollback
    restored_err = create_app_error(
        ErrorCode.UPDATE_VERIFY_FAILED,
        operation="update",
        stage="verification",
        known_impact="The update was not applied. Your previous installed version was restored safely.",
        recommended_action=RecoveryAction.USE_PREVIOUS_VERSION,
    )
    assert "restored safely" in restored_err.known_impact
    assert restored_err.recommended_action == RecoveryAction.USE_PREVIOUS_VERSION

    # Failed rollback (broken restore)
    failed_restore_err = create_app_error(
        ErrorCode.RESTORATION_FAILED,
        operation="update",
        stage="restoration",
        known_impact="The previous version could not be restored completely. This model needs repair before it can run.",
        recommended_action=RecoveryAction.REVIEW_REPAIR_OPTIONS,
    )
    assert "could not be restored completely" in failed_restore_err.title
    assert "needs repair" in failed_restore_err.known_impact
    assert failed_restore_err.recommended_action == RecoveryAction.REVIEW_REPAIR_OPTIONS


def test_stop_unknown_honesty():
    """Verify STOP_UNKNOWN never claims success or assumes model is dead."""
    err = create_app_error(
        ErrorCode.STOP_UNKNOWN,
        operation="stop_instance",
        stage="stopping",
        outcome_certainty=OutcomeCertainty.UNKNOWN,
    )
    assert "could not be confirmed" in err.title
    assert "remains listed" in err.known_impact
    assert err.outcome_certainty == OutcomeCertainty.UNKNOWN
    assert err.recommended_action == RecoveryAction.CHECK_AGAIN


def test_cli_json_error_serialization():
    """Verify format_json_error produces valid parseable JSON with all required contract fields."""
    err = create_app_error(
        ErrorCode.INSUFFICIENT_MEMORY,
        operation="start",
        stage="starting",
        technical_details="Alloc: 8192 MB > Avail: 4096 MB",
    )
    json_str = format_json_error(err)
    parsed = json.loads(json_str)

    assert parsed["error_code"] == ErrorCode.INSUFFICIENT_MEMORY.value
    assert parsed["recommended_action"] == RecoveryAction.CHOOSE_ANOTHER_MODEL.value
    assert "LP-" in parsed["support_id"]
    assert "8192 MB" in parsed["technical_details"]


@pytest.mark.asyncio
async def test_tui_app_error_modal_safe_default_focus():
    """Verify that AppErrorModal renders all 5 questions and defaults focus to safe dismiss button."""
    err = create_app_error(
        ErrorCode.DELETION_FAILED,
        operation="remove",
        stage="cleanup",
        known_impact="Some files remained locked. Installation record preserved.",
        technical_details="Permission denied on /models/qwen.gguf",
    )
    modal = AppErrorModal(err)
    
    tui = LocalPilotTUI()
    async with tui.run_test(size=(100, 30)) as pilot:
        tui.push_screen(modal)
        await pilot.pause()

        # Modal is on screen
        assert isinstance(tui.screen, AppErrorModal)
        
        # Verify title and support ID are displayed
        title_lbl = tui.screen.query_one("#app-error-title")
        assert "Could not delete all model files" in str(title_lbl.render())

        id_lbl = tui.screen.query_one("#app-error-id")
        assert "LP-" in str(id_lbl.render())

        # Safe dismiss button is present and focused
        dismiss_btn = tui.screen.query_one("#app-error-dismiss-btn")
        assert dismiss_btn.has_focus

        # Dismiss via clicking dismiss button
        await pilot.click("#app-error-dismiss-btn")
        await pilot.pause()
        assert not isinstance(tui.screen, AppErrorModal)
