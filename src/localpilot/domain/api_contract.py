"""Typed specifications and schemas for LocalPilot Local Inference API, Keys, and Accounting.

Implements Sections 6, 8, 9, 10, and 11 of the architecture:
- ApiKeyRecord & ApiKeyPolicy: Local high-entropy credential contract with safe prefix.
- ApiRequestRecord: Privacy-first request accounting (no prompt/content recording).
- ChatCompletionRequest / ChatCompletionResponse: OpenAI-compatible wire formats.
- MetricsDictionary: Standardized definitions of latency, throughput, and token counts.
"""

from __future__ import annotations
import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field


class ApiKeyStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class TokenUsageSource(str, Enum):
    RUNTIME = "runtime"                    # Authoritative runtime output
    LOCAL_TOKENIZER = "local_tokenizer"    # Verified offline tokenizer computation
    ESTIMATE = "estimate"                  # Bounded heuristic approximation
    UNKNOWN = "unknown"                    # Unmeasured / unavailable


class ApiKeyPolicy(BaseModel):
    """Access control and execution boundary policy for a local API key."""
    allowed_aliases: List[str] = Field(default_factory=lambda: ["*"])
    allowed_model_aliases: Optional[List[str]] = None
    allowed_capabilities: List[str] = Field(default_factory=lambda: ["chat", "streaming"])
    rate_limit_rpm: Optional[int] = None   # None represents unlimited on loopback
    max_concurrency: int = 2
    max_tokens_per_request: Optional[int] = 4096

    def __init__(self, **data: Any) -> None:
        if "allowed_model_aliases" in data and "allowed_aliases" not in data:
            data["allowed_aliases"] = data["allowed_model_aliases"]
        elif "allowed_aliases" in data and "allowed_model_aliases" not in data:
            data["allowed_model_aliases"] = data["allowed_aliases"]
        super().__init__(**data)


class ApiKeyRecord(BaseModel):
    """A LocalPilot-issued local credential for application integration.

    Never stored in plaintext; only nonreversible verifier and safe prefix are persisted.
    """
    key_id: str = Field(default_factory=lambda: f"lp_key_{secrets.token_hex(6)}")
    display_name: str                      # Friendly client label (e.g., 'VS Code', 'OpenCode')
    app_name: Optional[str] = None
    secret_prefix: str                     # Safe prefix for identification (e.g., 'lp_live_ab12...')
    verifier: str                          # SHA-256 nonreversible hash
    verifier_algorithm: str = "sha256"
    status: ApiKeyStatus = ApiKeyStatus.ACTIVE
    policy: ApiKeyPolicy = Field(default_factory=ApiKeyPolicy)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    expires_at: Optional[str] = None
    revoked_at: Optional[str] = None
    last_used_at: Optional[str] = None

    @property
    def name(self) -> str:
        return self.display_name

    @property
    def prefix(self) -> str:
        return self.secret_prefix

    @property
    def is_active(self) -> bool:
        return self.status == ApiKeyStatus.ACTIVE

    @is_active.setter
    def is_active(self, val: bool) -> None:
        self.status = ApiKeyStatus.ACTIVE if val else ApiKeyStatus.REVOKED

    @classmethod
    def generate(
        cls,
        display_name: Optional[str] = None,
        name: Optional[str] = None,
        policy: Optional[ApiKeyPolicy] = None,
        expires_at: Optional[str] = None,
        app_name: Optional[str] = None,
    ) -> tuple[str, ApiKeyRecord]:
        """Generates a high-entropy secret key and returns (plaintext_secret, record).

        The plaintext secret must only be displayed once upon creation.
        """
        label = name or display_name or "default_key"
        raw_token = f"sk-local-{secrets.token_urlsafe(32)}"
        prefix = f"{raw_token[:12]}..."
        verifier = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

        record = cls(
            display_name=label,
            app_name=app_name,
            secret_prefix=prefix,
            verifier=verifier,
            verifier_algorithm="sha256",
            status=ApiKeyStatus.ACTIVE,
            policy=policy or ApiKeyPolicy(),
            expires_at=expires_at,
        )
        return raw_token, record

    def verify(self, candidate_secret: str) -> bool:
        """Verifies candidate plaintext secret in constant time against nonreversible verifier."""
        if not self.is_active:
            return False
        if self.expires_at:
            try:
                exp_dt = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > exp_dt:
                    return False
            except Exception:
                # Malformed expiry must fail closed
                return False
        candidate_hash = hashlib.sha256(candidate_secret.encode("utf-8")).hexdigest()
        return secrets.compare_digest(self.verifier, candidate_hash)


class ApiRequestRecord(BaseModel):
    """Privacy-first metadata record of an inference request.

    Stores timing, routing, token metrics, and status. NEVER stores prompt/response content.
    """
    request_id: str = Field(default_factory=lambda: f"req-{uuid.uuid4().hex[:12]}")
    key_id: str
    requested_model: str                   # Alias or model name specified by client
    resolved_version_id: str               # Immutable model version executing request
    resolved_profile_id: str               # Exact execution profile used
    endpoint: str = "/v1/chat/completions"
    status: Literal["completed", "error", "cancelled"] = "completed"
    error_category: Optional[str] = None
    queue_duration_ms: Optional[float] = None
    load_duration_ms: Optional[float] = None
    time_to_first_token_ms: Optional[float] = None
    total_duration_ms: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    tokens_per_second: Optional[float] = None
    usage_source: TokenUsageSource = TokenUsageSource.RUNTIME
    is_streaming: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


# =============================================================================
# OpenAI-Compatible Wire Contracts (Section 6)
# =============================================================================

class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: Optional[str] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    """Standard OpenAI-compatible Chat Completion request envelope."""
    model: str
    messages: List[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = 1.0
    max_tokens: Optional[int] = None
    stop: Optional[Union[str, List[str]]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    user: Optional[str] = None


class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: Optional[Literal["stop", "length", "tool_calls", "content_filter"]] = "stop"


class ChatCompletionResponse(BaseModel):
    """Standard OpenAI-compatible Chat Completion non-streaming response."""
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(datetime.now(timezone.utc).timestamp()))
    model: str
    choices: List[ChatChoice]
    usage: Optional[UsageInfo] = None
    system_fingerprint: Optional[str] = "fp_localpilot"


class ChatDelta(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class StreamChoice(BaseModel):
    index: int = 0
    delta: ChatDelta
    finish_reason: Optional[Literal["stop", "length", "tool_calls", "content_filter"]] = None


class ChatCompletionChunk(BaseModel):
    """Standard OpenAI-compatible SSE streaming chunk envelope."""
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(datetime.now(timezone.utc).timestamp()))
    model: str
    choices: List[StreamChoice]
    usage: Optional[UsageInfo] = None


class ApiErrorEnvelope(BaseModel):
    """Standard OpenAI-compatible error response envelope."""
    error: Dict[str, Any]

    @classmethod
    def create(cls, message: str, type_name: str = "invalid_request_error", code: Optional[str] = None) -> ApiErrorEnvelope:
        err = {"message": message, "type": type_name}
        if code:
            err["code"] = code
        return cls(error=err)


# =============================================================================
# Metrics Dictionary (Section 11)
# =============================================================================

class MetricsDictionary:
    """Canonical, versioned definitions of all operational and performance metrics."""
    VERSION = "metrics-v1"

    DEFINITIONS = {
        "end_to_end_latency_ms": {
            "name": "End-to-End Latency",
            "meaning": "Elapsed time from receipt of HTTP request to transmission of final response byte.",
            "units": "milliseconds",
            "clock": "monotonic",
            "uncertainty": "exact",
        },
        "time_to_first_content_ms": {
            "name": "Time to First Generated Content (TTFC)",
            "meaning": "Elapsed time from receipt of request until the first meaningful generated token chunk is sent.",
            "units": "milliseconds",
            "clock": "monotonic",
            "uncertainty": "exact",
        },
        "queue_wait_ms": {
            "name": "Queue Wait Time",
            "meaning": "Duration spent waiting in the gateway request queue for an available concurrency slot.",
            "units": "milliseconds",
            "clock": "monotonic",
            "uncertainty": "exact",
        },
        "output_tokens_per_second": {
            "name": "Per-Request Generation Speed",
            "meaning": "Number of output tokens generated divided by active generation duration (excluding queue/load wait).",
            "units": "tokens/sec",
            "clock": "monotonic",
            "uncertainty": "authoritative_when_runtime_reported",
        },
        "aggregate_throughput_tps": {
            "name": "Aggregate Output Throughput",
            "meaning": "Total output tokens generated across all concurrent requests within sample window divided by window seconds.",
            "units": "tokens/sec",
            "clock": "monotonic",
            "uncertainty": "window_averaged",
        },
    }

    @classmethod
    def get_all_definitions(cls) -> Dict[str, Any]:
        """Returns all metric definitions."""
        return cls.DEFINITIONS

    @classmethod
    def get_definition(cls, metric_key: str) -> Optional[Dict[str, Any]]:
        """Retrieves metric definition by key or common shorthand alias."""
        if metric_key in cls.DEFINITIONS:
            d = dict(cls.DEFINITIONS[metric_key])
            d["title"] = d["name"]
            return d
        aliases = {
            "tps": "output_tokens_per_second",
            "ttft_ms": "time_to_first_content_ms",
            "rss_mb": "queue_wait_ms",
            "prompt_tokens": "time_to_first_content_ms",
            "completion_tokens": "output_tokens_per_second",
        }
        target = aliases.get(metric_key)
        if target and target in cls.DEFINITIONS:
            d = dict(cls.DEFINITIONS[target])
            d["title"] = d["name"]
            return d
        return None


class ConnectionStatus(str, Enum):
    ACTIVE = "active"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


class ConnectionVerificationResult(BaseModel):
    """Result of step-by-step connection diagnostic probes."""
    config_valid: bool = False
    gateway_reachable: bool = False
    key_valid: bool = False
    model_authorized: bool = False
    probe_passed: bool = False
    client_accepted: Optional[bool] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None


class ConnectionProfile(BaseModel):
    """Persistent record of an application connection without raw credentials."""
    connection_id: str = Field(default_factory=lambda: f"conn_{secrets.token_hex(6)}")
    name: str
    client_type: str
    template_version: str = "1.0"
    model_alias: str
    gateway_url: str
    key_id: str
    target_path: Optional[str] = None
    backup_path: Optional[str] = None
    status: ConnectionStatus = ConnectionStatus.ACTIVE
    verification: ConnectionVerificationResult = Field(default_factory=ConnectionVerificationResult)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


class HookEventType(str, Enum):
    GATEWAY_STARTED = "gateway.started"
    GATEWAY_STOPPED = "gateway.stopped"
    MODEL_READY = "model.ready"
    MODEL_UNLOADED = "model.unloaded"
    SETUP_COMPLETED = "setup.completed"
    SETUP_FAILED = "setup.failed"
    VERSION_ACTIVATED = "version.activated"
    RECOVERY_REQUIRED = "recovery.required"
    CONNECTION_VERIFIED = "connection.verified"
    ALL = "all"


class HookEvent(BaseModel):
    """Sanitized, privacy-safe lifecycle event payload."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    operation_id: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self.sanitize()

    def sanitize(self) -> None:
        """Enforces privacy-first boundaries: strips secrets, tokens, prompts, and completions."""
        forbidden_keys = {
            "key", "api_key", "secret", "token", "password", "authorization",
            "prompt", "completion", "messages", "content", "bearer", "hashed_secret"
        }
        sanitized_data: Dict[str, Any] = {}
        for k, v in self.data.items():
            if k.lower() in forbidden_keys:
                continue
            if isinstance(v, str) and (v.startswith("sk-local-") or v.startswith("Bearer ")):
                continue
            sanitized_data[k] = v
        self.data = sanitized_data


class HookDefinition(BaseModel):
    """Isolated lifecycle hook definition with safety and circuit breaker attributes."""
    hook_id: str = Field(default_factory=lambda: f"hook_{secrets.token_hex(6)}")
    name: str
    event_type: str
    action_type: str = "executable"  # "builtin" or "executable"
    command: List[str] = Field(default_factory=list)
    is_enabled: bool = False
    approved_by_user: bool = False
    consecutive_failures: int = 0
    is_tripped: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


class HookRunResult(BaseModel):
    """Outcome of an isolated lifecycle hook invocation."""
    run_id: str = Field(default_factory=lambda: f"hrun_{secrets.token_hex(6)}")
    hook_id: str
    event_type: str
    triggered_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    status: str  # "success", "failed", "timeout", "circuit_broken", "skipped"
    exit_code: Optional[int] = None
    duration_ms: float = 0.0
    output_summary: Optional[str] = None

