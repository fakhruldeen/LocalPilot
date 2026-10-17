"""Structured typed error contracts, recovery actions, and message catalogs for LocalPilot.

Provides calm, non-technical, state-accurate error messages and supported recovery actions
without false reassurance or raw secret leakage.
"""

from __future__ import annotations
from enum import Enum
import hashlib
import os
import random
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from localpilot.observability.redaction import redact_data, redact_string


class ErrorCode(str, Enum):
    # Network & Sources
    NO_INTERNET = "LP_ERR_NO_INTERNET"
    SOURCE_UNAVAILABLE = "LP_ERR_SOURCE_UNAVAILABLE"
    RATE_LIMIT = "LP_ERR_RATE_LIMIT"
    AUTH_REQUIRED = "LP_ERR_AUTH_REQUIRED"
    PUBLISHER_APPROVAL = "LP_ERR_PUBLISHER_APPROVAL"

    # Hardware & Resources
    INSUFFICIENT_STORAGE = "LP_ERR_INSUFFICIENT_STORAGE"
    INSUFFICIENT_MEMORY = "LP_ERR_INSUFFICIENT_MEMORY"
    GPU_UNAVAILABLE = "LP_ERR_GPU_UNAVAILABLE"
    UNSUPPORTED_MODEL = "LP_ERR_UNSUPPORTED_MODEL"

    # Integrity & Preparation
    INTEGRITY_MISMATCH = "LP_ERR_INTEGRITY_MISMATCH"
    RUNTIME_PREP_FAILED = "LP_ERR_RUNTIME_PREP_FAILED"
    RUNTIME_STARTUP_FAILED = "LP_ERR_RUNTIME_STARTUP_FAILED"
    MODEL_LOAD_FAILED = "LP_ERR_MODEL_LOAD_FAILED"
    INFERENCE_FAILED = "LP_ERR_INFERENCE_FAILED"

    # Operations & State
    SERVICE_BUSY = "LP_ERR_SERVICE_BUSY"
    STOP_REJECTED = "LP_ERR_STOP_REJECTED"
    STOP_UNKNOWN = "LP_ERR_STOP_UNKNOWN"
    DELETION_FAILED = "LP_ERR_DELETION_FAILED"

    # Updates & Recovery
    UPDATE_UNAVAILABLE = "LP_ERR_UPDATE_UNAVAILABLE"
    UPDATE_VERIFY_FAILED = "LP_ERR_UPDATE_VERIFY_FAILED"
    RESTORATION_FAILED = "LP_ERR_RESTORATION_FAILED"

    # System & Audit
    DB_FAILURE = "LP_ERR_DB_FAILURE"
    AUDIT_FAILURE = "LP_ERR_AUDIT_FAILURE"
    OPERATION_CANCELLED = "LP_ERR_OPERATION_CANCELLED"
    INTERNAL_FAILURE = "LP_ERR_INTERNAL_FAILURE"


class OutcomeCertainty(str, Enum):
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"
    PARTIAL = "partial"


class RecoveryAction(str, Enum):
    RETRY = "retry"
    RESUME = "resume"
    CHECK_AGAIN = "check_again"
    RECONNECT_SOURCE = "reconnect_source"
    OPEN_PUBLISHER_PAGE = "open_publisher_page"
    CHOOSE_ANOTHER_MODEL = "choose_another_model"
    REVIEW_STORAGE = "review_storage"
    USE_PREVIOUS_VERSION = "use_previous_version"
    REVIEW_REPAIR_OPTIONS = "review_repair_options"
    EXPORT_DIAGNOSTICS = "export_diagnostics"
    BACK = "back"
    DISMISS = "dismiss"


class AppError(BaseModel):
    """Standardized, state-accurate error contract for user presentation and recovery."""

    error_code: ErrorCode
    operation: str
    stage: str
    title: str
    explanation: str
    known_impact: str
    recommended_action: RecoveryAction
    supported_actions: List[RecoveryAction] = Field(default_factory=list)
    retryable: bool = False
    outcome_certainty: OutcomeCertainty = OutcomeCertainty.CONFIRMED
    current_state: str = "failed"
    support_id: str
    technical_details: str = ""
    raw_exception_name: Optional[str] = None

    def to_user_dict(self) -> Dict[str, Any]:
        """Returns safe user-presentable dictionary for serialization or JSON output."""
        return {
            "error_code": self.error_code.value,
            "title": self.title,
            "explanation": self.explanation,
            "known_impact": self.known_impact,
            "recommended_action": self.recommended_action.value,
            "supported_actions": [a.value for a in self.supported_actions],
            "retryable": self.retryable,
            "outcome_certainty": self.outcome_certainty.value,
            "current_state": self.current_state,
            "support_id": self.support_id,
            "technical_details": self.technical_details,
        }


def generate_support_id() -> str:
    """Generates a unique, non-identifying correlation support ID (e.g., LP-E810-7B2A)."""
    raw = f"{time.time()}-{os.getpid()}-{random.randint(1000, 9999)}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8].upper()
    return f"LP-{h[:4]}-{h[4:]}"


# Canonical catalog of explanations and impact statements
_CATALOG: Dict[ErrorCode, Dict[str, Any]] = {
    ErrorCode.NO_INTERNET: {
        "title": "Internet connection unavailable",
        "explanation": "We could not connect to the model source. Your computer may be offline or the connection timed out.",
        "known_impact": "No changes have been made to your files, installed models, or settings.",
        "recommended_action": RecoveryAction.RETRY,
        "supported_actions": [RecoveryAction.RETRY, RecoveryAction.BACK],
        "retryable": True,
    },
    ErrorCode.SOURCE_UNAVAILABLE: {
        "title": "Model source is temporarily unreachable",
        "explanation": "The source repository server did not respond or returned a temporary service error.",
        "known_impact": "No files were downloaded or modified.",
        "recommended_action": RecoveryAction.CHECK_AGAIN,
        "supported_actions": [RecoveryAction.CHECK_AGAIN, RecoveryAction.CHOOSE_ANOTHER_MODEL, RecoveryAction.BACK],
        "retryable": True,
    },
    ErrorCode.RATE_LIMIT: {
        "title": "Download limit reached on source",
        "explanation": "The model source has temporarily paused downloads from your network address to prevent overload.",
        "known_impact": "Your progress up to this point has been preserved where possible.",
        "recommended_action": RecoveryAction.CHECK_AGAIN,
        "supported_actions": [RecoveryAction.CHECK_AGAIN, RecoveryAction.CHOOSE_ANOTHER_MODEL, RecoveryAction.BACK],
        "retryable": True,
    },
    ErrorCode.AUTH_REQUIRED: {
        "title": "Access key required",
        "explanation": "This model source requires an access key (token) before allowing downloads.",
        "known_impact": "No files have been changed on your computer.",
        "recommended_action": RecoveryAction.RECONNECT_SOURCE,
        "supported_actions": [RecoveryAction.RECONNECT_SOURCE, RecoveryAction.CHOOSE_ANOTHER_MODEL, RecoveryAction.BACK],
        "retryable": True,
    },
    ErrorCode.PUBLISHER_APPROVAL: {
        "title": "Publisher agreement required",
        "explanation": "The publisher requires agreeing to specific terms of use on their website before downloading this model.",
        "known_impact": "No files have been changed on your computer.",
        "recommended_action": RecoveryAction.OPEN_PUBLISHER_PAGE,
        "supported_actions": [RecoveryAction.OPEN_PUBLISHER_PAGE, RecoveryAction.CHOOSE_ANOTHER_MODEL, RecoveryAction.BACK],
        "retryable": False,
    },
    ErrorCode.INSUFFICIENT_STORAGE: {
        "title": "Not enough free disk space",
        "explanation": "Your computer does not have enough free disk space to store and run this model safely.",
        "known_impact": "Setup was stopped before downloading files to protect your free space.",
        "recommended_action": RecoveryAction.REVIEW_STORAGE,
        "supported_actions": [RecoveryAction.REVIEW_STORAGE, RecoveryAction.CHOOSE_ANOTHER_MODEL, RecoveryAction.BACK],
        "retryable": False,
    },
    ErrorCode.INSUFFICIENT_MEMORY: {
        "title": "Not enough available memory (RAM)",
        "explanation": "Another application or your system is currently using memory this model needs to run reliably.",
        "known_impact": "The model was not started. Your existing files and settings are unaffected.",
        "recommended_action": RecoveryAction.CHOOSE_ANOTHER_MODEL,
        "supported_actions": [RecoveryAction.CHOOSE_ANOTHER_MODEL, RecoveryAction.CHECK_AGAIN, RecoveryAction.BACK],
        "retryable": True,
    },
    ErrorCode.GPU_UNAVAILABLE: {
        "title": "Graphics card acceleration unavailable",
        "explanation": "The requested graphics card (GPU) could not be initialized or does not have sufficient video memory.",
        "known_impact": "The model was not started in GPU mode. No settings were altered.",
        "recommended_action": RecoveryAction.RETRY,
        "supported_actions": [RecoveryAction.RETRY, RecoveryAction.CHOOSE_ANOTHER_MODEL, RecoveryAction.BACK],
        "retryable": True,
    },
    ErrorCode.UNSUPPORTED_MODEL: {
        "title": "Model format not supported",
        "explanation": "This model file format or architecture is not compatible with the installed supporting software.",
        "known_impact": "No files were altered on your computer.",
        "recommended_action": RecoveryAction.CHOOSE_ANOTHER_MODEL,
        "supported_actions": [RecoveryAction.CHOOSE_ANOTHER_MODEL, RecoveryAction.BACK],
        "retryable": False,
    },
    ErrorCode.INTEGRITY_MISMATCH: {
        "title": "Model verification failed",
        "explanation": "The downloaded file did not match the publisher's cryptographic fingerprint. It may be incomplete or corrupted.",
        "known_impact": "The damaged download was discarded to protect your system from unstable execution.",
        "recommended_action": RecoveryAction.RETRY,
        "supported_actions": [RecoveryAction.RETRY, RecoveryAction.CHOOSE_ANOTHER_MODEL, RecoveryAction.BACK],
        "retryable": True,
    },
    ErrorCode.RUNTIME_PREP_FAILED: {
        "title": "Could not prepare supporting software",
        "explanation": "LocalPilot could not set up the supporting runtime engine needed to run this model.",
        "known_impact": "No models were modified. Supporting files in your user folder remain isolated.",
        "recommended_action": RecoveryAction.RETRY,
        "supported_actions": [RecoveryAction.RETRY, RecoveryAction.EXPORT_DIAGNOSTICS, RecoveryAction.BACK],
        "retryable": True,
    },
    ErrorCode.RUNTIME_STARTUP_FAILED: {
        "title": "Supporting software failed to start",
        "explanation": "The supporting engine process did not start or stopped responding shortly after launch.",
        "known_impact": "Your model files remain stored on your computer, but the model is currently stopped.",
        "recommended_action": RecoveryAction.RETRY,
        "supported_actions": [RecoveryAction.RETRY, RecoveryAction.EXPORT_DIAGNOSTICS, RecoveryAction.BACK],
        "retryable": True,
    },
    ErrorCode.MODEL_LOAD_FAILED: {
        "title": "Could not load model into memory",
        "explanation": "The supporting software started, but the model file could not be loaded into working memory.",
        "known_impact": "Your model files are preserved on disk. The process has been safely shut down.",
        "recommended_action": RecoveryAction.CHOOSE_ANOTHER_MODEL,
        "supported_actions": [RecoveryAction.CHOOSE_ANOTHER_MODEL, RecoveryAction.RETRY, RecoveryAction.BACK],
        "retryable": True,
    },
    ErrorCode.INFERENCE_FAILED: {
        "title": "Model did not pass test response",
        "explanation": "The model started, but it failed to answer a test question properly. It may need different parameters.",
        "known_impact": "Model files are preserved and ready for inspection. The model is not yet marked ready for everyday use.",
        "recommended_action": RecoveryAction.RETRY,
        "supported_actions": [RecoveryAction.RETRY, RecoveryAction.CHOOSE_ANOTHER_MODEL, RecoveryAction.BACK],
        "retryable": True,
    },
    ErrorCode.SERVICE_BUSY: {
        "title": "Local engine is currently busy",
        "explanation": "Another model operation or active request is currently using the local engine.",
        "known_impact": "Your pending request was not processed.",
        "recommended_action": RecoveryAction.CHECK_AGAIN,
        "supported_actions": [RecoveryAction.CHECK_AGAIN, RecoveryAction.DISMISS],
        "retryable": True,
    },
    ErrorCode.STOP_REJECTED: {
        "title": "Stop request could not be completed",
        "explanation": "The running model could not be stopped safely because process ownership could not be verified.",
        "known_impact": "The process is still running. No files or records were removed.",
        "recommended_action": RecoveryAction.CHECK_AGAIN,
        "supported_actions": [RecoveryAction.CHECK_AGAIN, RecoveryAction.EXPORT_DIAGNOSTICS, RecoveryAction.DISMISS],
        "retryable": True,
    },
    ErrorCode.STOP_UNKNOWN: {
        "title": "Stop outcome could not be confirmed",
        "explanation": "We sent the stop signal, but could not verify that the process terminated completely.",
        "known_impact": "The model remains listed so you can check its status again before proceeding.",
        "recommended_action": RecoveryAction.CHECK_AGAIN,
        "supported_actions": [RecoveryAction.CHECK_AGAIN, RecoveryAction.DISMISS],
        "retryable": True,
    },
    ErrorCode.DELETION_FAILED: {
        "title": "Could not delete all model files",
        "explanation": "Some model files could not be deleted from disk, possibly because another application has them open.",
        "known_impact": "Partial files were removed, but locked files remain on disk. The installation record was retained.",
        "recommended_action": RecoveryAction.REVIEW_STORAGE,
        "supported_actions": [RecoveryAction.REVIEW_STORAGE, RecoveryAction.RETRY, RecoveryAction.DISMISS],
        "retryable": True,
    },
    ErrorCode.UPDATE_UNAVAILABLE: {
        "title": "Cannot check for updates",
        "explanation": "The update source could not be contacted or does not provide version information for this model.",
        "known_impact": "Your installed version has not been changed.",
        "recommended_action": RecoveryAction.DISMISS,
        "supported_actions": [RecoveryAction.DISMISS, RecoveryAction.CHECK_AGAIN],
        "retryable": True,
    },
    ErrorCode.UPDATE_VERIFY_FAILED: {
        "title": "Update verification did not pass",
        "explanation": "The updated model version failed initial verification tests.",
        "known_impact": "The update was not applied. Your previous installed version was restored safely.",
        "recommended_action": RecoveryAction.USE_PREVIOUS_VERSION,
        "supported_actions": [RecoveryAction.USE_PREVIOUS_VERSION, RecoveryAction.DISMISS],
        "retryable": False,
    },
    ErrorCode.RESTORATION_FAILED: {
        "title": "Previous version could not be restored completely",
        "explanation": "The update failed, and an error occurred while attempting to restore the backup copy.",
        "known_impact": "This model files are in an incomplete state and need repair before the model can run.",
        "recommended_action": RecoveryAction.REVIEW_REPAIR_OPTIONS,
        "supported_actions": [RecoveryAction.REVIEW_REPAIR_OPTIONS, RecoveryAction.EXPORT_DIAGNOSTICS, RecoveryAction.DISMISS],
        "retryable": False,
    },
    ErrorCode.DB_FAILURE: {
        "title": "Local database error",
        "explanation": "LocalPilot could not access or write to its local settings database.",
        "known_impact": "Your model files on disk are intact, but settings could not be updated.",
        "recommended_action": RecoveryAction.RETRY,
        "supported_actions": [RecoveryAction.RETRY, RecoveryAction.EXPORT_DIAGNOSTICS, RecoveryAction.DISMISS],
        "retryable": True,
    },
    ErrorCode.AUDIT_FAILURE: {
        "title": "Safety checkpoint could not be saved",
        "explanation": "A mandatory safety record could not be written to disk. The requested change was halted.",
        "known_impact": "No changes were made to your system or files.",
        "recommended_action": RecoveryAction.RETRY,
        "supported_actions": [RecoveryAction.RETRY, RecoveryAction.DISMISS],
        "retryable": True,
    },
    ErrorCode.OPERATION_CANCELLED: {
        "title": "Operation cancelled",
        "explanation": "You cancelled this operation before it completed.",
        "known_impact": "Partial files have been safely cleaned up. Your existing models are unaffected.",
        "recommended_action": RecoveryAction.DISMISS,
        "supported_actions": [RecoveryAction.DISMISS, RecoveryAction.BACK],
        "retryable": False,
    },
    ErrorCode.INTERNAL_FAILURE: {
        "title": "An unexpected error occurred",
        "explanation": "LocalPilot encountered an unexpected internal condition while processing your request.",
        "known_impact": "Your installed models and downloaded files were left untouched where verified.",
        "recommended_action": RecoveryAction.DISMISS,
        "supported_actions": [RecoveryAction.DISMISS, RecoveryAction.EXPORT_DIAGNOSTICS, RecoveryAction.RETRY],
        "retryable": True,
    },
}


def create_app_error(
    error_code: ErrorCode,
    operation: str,
    stage: str,
    title: Optional[str] = None,
    explanation: Optional[str] = None,
    known_impact: Optional[str] = None,
    recommended_action: Optional[RecoveryAction] = None,
    supported_actions: Optional[List[RecoveryAction]] = None,
    retryable: Optional[bool] = None,
    outcome_certainty: OutcomeCertainty = OutcomeCertainty.CONFIRMED,
    current_state: str = "failed",
    technical_details: str = "",
    raw_exception_name: Optional[str] = None,
) -> AppError:
    """Builds an AppError using defaults from the catalog while honoring state-specific overrides."""
    cat = _CATALOG.get(error_code, _CATALOG[ErrorCode.INTERNAL_FAILURE])

    sanitized_details = redact_string(technical_details or "")

    return AppError(
        error_code=error_code,
        operation=operation,
        stage=stage,
        title=title or cat["title"],
        explanation=explanation or cat["explanation"],
        known_impact=known_impact or cat["known_impact"],
        recommended_action=recommended_action or cat["recommended_action"],
        supported_actions=supported_actions if supported_actions is not None else cat["supported_actions"],
        retryable=retryable if retryable is not None else cat["retryable"],
        outcome_certainty=outcome_certainty,
        current_state=current_state,
        support_id=generate_support_id(),
        technical_details=sanitized_details,
        raw_exception_name=raw_exception_name,
    )


def map_exception_to_app_error(
    exc: Exception,
    operation: str = "general",
    stage: str = "execution",
    current_state: str = "failed",
    extra_details: str = "",
) -> AppError:
    """Maps arbitrary runtime exceptions into truthful, structured AppError contracts."""
    exc_type = type(exc).__name__
    raw_msg = str(exc)
    raw_msg_lower = raw_msg.lower()

    # Sanitized technical detail string
    full_details = f"{exc_type}: {raw_msg}"
    if extra_details:
        full_details += f" | {extra_details}"
    sanitized_details = redact_string(full_details)

    # Specific known exception classifications
    if exc_type == "CancelledError" or isinstance(exc, BaseException) and "cancelled" in raw_msg_lower:
        return create_app_error(
            ErrorCode.OPERATION_CANCELLED,
            operation=operation,
            stage=stage,
            current_state=current_state,
            technical_details=sanitized_details,
            raw_exception_name=exc_type,
        )

    if exc_type == "AuditCheckpointFailureError" or "audit checkpoint" in raw_msg_lower:
        return create_app_error(
            ErrorCode.AUDIT_FAILURE,
            operation=operation,
            stage=stage,
            current_state=current_state,
            technical_details=sanitized_details,
            raw_exception_name=exc_type,
        )

    if exc_type in ("ConnectError", "NetworkError", "TimeoutException") or "connection refused" in raw_msg_lower or "name or service not known" in raw_msg_lower:
        return create_app_error(
            ErrorCode.NO_INTERNET,
            operation=operation,
            stage=stage,
            current_state=current_state,
            technical_details=sanitized_details,
            raw_exception_name=exc_type,
        )

    if "401" in raw_msg or "unauthorized" in raw_msg_lower:
        return create_app_error(
            ErrorCode.AUTH_REQUIRED,
            operation=operation,
            stage=stage,
            current_state=current_state,
            technical_details=sanitized_details,
            raw_exception_name=exc_type,
        )

    if "403" in raw_msg or "gated repo" in raw_msg_lower or "access to model" in raw_msg_lower:
        return create_app_error(
            ErrorCode.PUBLISHER_APPROVAL,
            operation=operation,
            stage=stage,
            current_state=current_state,
            technical_details=sanitized_details,
            raw_exception_name=exc_type,
        )

    if "429" in raw_msg or "rate limit" in raw_msg_lower or "too many requests" in raw_msg_lower:
        return create_app_error(
            ErrorCode.RATE_LIMIT,
            operation=operation,
            stage=stage,
            current_state=current_state,
            technical_details=sanitized_details,
            raw_exception_name=exc_type,
        )

    if "insufficient disk space" in raw_msg_lower or "not enough space" in raw_msg_lower:
        return create_app_error(
            ErrorCode.INSUFFICIENT_STORAGE,
            operation=operation,
            stage=stage,
            current_state=current_state,
            technical_details=sanitized_details,
            raw_exception_name=exc_type,
        )

    if "insufficient memory" in raw_msg_lower or "not enough ram" in raw_msg_lower or "memory budget exceeded" in raw_msg_lower:
        return create_app_error(
            ErrorCode.INSUFFICIENT_MEMORY,
            operation=operation,
            stage=stage,
            current_state=current_state,
            technical_details=sanitized_details,
            raw_exception_name=exc_type,
        )

    if "integrity check failed" in raw_msg_lower or "checksum" in raw_msg_lower or "hash mismatch" in raw_msg_lower:
        return create_app_error(
            ErrorCode.INTEGRITY_MISMATCH,
            operation=operation,
            stage=stage,
            current_state=current_state,
            technical_details=sanitized_details,
            raw_exception_name=exc_type,
        )

    if "test inference" in raw_msg_lower or "inference verification" in raw_msg_lower:
        return create_app_error(
            ErrorCode.INFERENCE_FAILED,
            operation=operation,
            stage=stage,
            current_state=current_state,
            technical_details=sanitized_details,
            raw_exception_name=exc_type,
        )

    if "llama-server failed to become ready" in raw_msg_lower or "ollama daemon failed to become ready" in raw_msg_lower:
        return create_app_error(
            ErrorCode.RUNTIME_STARTUP_FAILED,
            operation=operation,
            stage=stage,
            current_state=current_state,
            technical_details=sanitized_details,
            raw_exception_name=exc_type,
        )

    if "gpu" in raw_msg_lower and ("not found" in raw_msg_lower or "cuda" in raw_msg_lower or "metal" in raw_msg_lower):
        return create_app_error(
            ErrorCode.GPU_UNAVAILABLE,
            operation=operation,
            stage=stage,
            current_state=current_state,
            technical_details=sanitized_details,
            raw_exception_name=exc_type,
        )

    if "sqlite" in raw_msg_lower or "database is locked" in raw_msg_lower:
        return create_app_error(
            ErrorCode.DB_FAILURE,
            operation=operation,
            stage=stage,
            current_state=current_state,
            technical_details=sanitized_details,
            raw_exception_name=exc_type,
        )

    # Default fallback
    return create_app_error(
        ErrorCode.INTERNAL_FAILURE,
        operation=operation,
        stage=stage,
        current_state=current_state,
        technical_details=sanitized_details,
        raw_exception_name=exc_type,
    )
