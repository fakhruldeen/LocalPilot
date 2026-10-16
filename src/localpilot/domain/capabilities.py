"""Capability taxonomy, implementation statuses, and provider identities."""

from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class ImplementationStatus(str, Enum):
    IMPLEMENTED_TESTED = "implemented_tested"
    IMPLEMENTED_UNVERIFIED = "implemented_unverified"
    PLANNED = "planned"
    UNSUPPORTED = "unsupported"


class InferenceBackendId(str, Enum):
    LLAMACPP = "llama.cpp"
    VLLM = "vllm"
    MLX_LM = "mlx-lm"
    TRANSFORMERS = "transformers"
    UNKNOWN_SERVICE_MANAGED = "unknown/service-managed"


class ManagedServiceId(str, Enum):
    NONE = "none"
    OLLAMA = "ollama"
    LM_STUDIO = "lm_studio"
    DOCKER_MODEL_RUNNER = "docker_model_runner"


def normalize_service_id(name: str) -> Optional[ManagedServiceId]:
    """Normalizes service aliases into canonical identities.

    Crucial: 'llmster', 'lmstudio', and 'LM Studio / llmster' all map to LM_STUDIO.
    Never registers 'llmster' and 'lm_studio' as duplicate providers.
    """
    if not name:
        return ManagedServiceId.NONE
    norm = name.lower().replace("-", "_").replace(" ", "_").replace("/", "_")
    norm = "_".join(part for part in norm.split("_") if part)
    if "llmster" in norm or "lmstudio" in norm or "lm_studio" in norm:
        return ManagedServiceId.LM_STUDIO
    if "ollama" in norm:
        return ManagedServiceId.OLLAMA
    if "docker_model" in norm or "model_runner" in norm:
        return ManagedServiceId.DOCKER_MODEL_RUNNER
    if norm in ("none", ""):
        return ManagedServiceId.NONE
    return None


class LMStudioComponent(str, Enum):
    """Components of the LM Studio ecosystem."""
    DESKTOP_APP = "desktop_app"
    HEADLESS_DAEMON = "llmster_daemon"
    CLI = "lms_cli"


class DeploymentMethod(str, Enum):
    DIRECT_WORKER = "direct_worker"
    LOCAL_API_SERVICE = "local_api_service"
    CONTAINER = "container"
    BROWSER = "browser"


class AdapterCapability(str, Enum):
    DETECT_INSTALLATION = "detect_installation"
    INSPECT_VERSION = "inspect_version"
    INSTALL_SOFTWARE = "install_software"
    LIST_LOCAL_MODELS = "list_local_models"
    DOWNLOAD_OR_IMPORT_MODEL = "download_or_import_model"
    INSPECT_ARTIFACT_IDENTITY = "inspect_artifact_identity"
    LAUNCH_SERVER_OR_WORKER = "launch_server_or_worker"
    LOAD_MODEL = "load_model"
    CHECK_API_READINESS = "check_api_readiness"
    RUN_TEST_INFERENCE = "run_test_inference"
    STREAM_OUTPUT = "stream_output"
    INSPECT_LOADED_MODELS = "inspect_loaded_models"
    INSPECT_RESOURCE_USAGE = "inspect_resource_usage"
    UNLOAD_MODEL = "unload_model"
    STOP_OWNED_PROCESS = "stop_owned_process"
    REMOVE_MODEL = "remove_model"
    CHECK_UPDATES = "check_updates"
    STAGE_TEST_UPDATE = "stage_test_update"
    RESTORE_PREVIOUS_VERSION = "restore_previous_version"
    KEEP_RUNNING_AFTER_EXIT = "keep_running_after_exit"
    REDISCOVER_RETAINED_EXECUTION = "rediscover_retained_execution"


class CapabilityResult(BaseModel):
    supported: bool
    status: ImplementationStatus
    capability: AdapterCapability
    reason_code: Optional[str] = None
    friendly_explanation: str
