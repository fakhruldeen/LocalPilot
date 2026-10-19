"""Local authenticated OpenAI-compatible HTTP inference gateway server (Section 6 & 11)."""

from __future__ import annotations
import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx

from localpilot.application.accounting_service import ApiAccountingService
from localpilot.application.api_key_service import ApiKeyService
from localpilot.application.chat_service import ChatService
from localpilot.application.runtime_service import RuntimeLifecycleService
from localpilot.application.version_service import ModelVersionService
from localpilot.domain.api_contract import (
    ApiErrorEnvelope,
    ApiKeyRecord,
    ChatChoice,
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatDelta,
    ChatMessage,
    StreamChoice,
    UsageInfo,
)
from localpilot import __version__ as GATEWAY_VERSION
from localpilot.storage.db import DatabaseManager, get_db, get_default_data_dir

logger = logging.getLogger(__name__)


def get_openapi_spec() -> Dict[str, Any]:
    """Generates the OpenAPI 3.1.0 specification for the LocalPilot inference gateway."""
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "LocalPilot Inference Gateway API",
            "version": GATEWAY_VERSION,
            "description": "Authenticated, loopback-only OpenAI-compatible inference gateway for local models.",
            "license": {"name": "GPL-3.0-only"},
        },
        "servers": [
            {"url": "http://127.0.0.1:8080", "description": "Local loopback server"}
        ],
        "paths": {
            "/health": {
                "get": {
                    "summary": "Gateway health status",
                    "responses": {"200": {"description": "Gateway status ok"}},
                }
            },
            "/v1/health/live": {
                "get": {
                    "summary": "Liveness probe verifying gateway process is alive",
                    "responses": {"200": {"description": "Gateway process is alive"}},
                }
            },
            "/v1/health/ready": {
                "get": {
                    "summary": "Readiness probe verifying models and execution readiness",
                    "responses": {
                        "200": {"description": "At least one model version is ready for inference"},
                        "503": {"description": "No model version or alias is ready for inference"},
                    },
                }
            },
            "/openapi.json": {
                "get": {
                    "summary": "OpenAPI 3.1.0 specification",
                    "responses": {"200": {"description": "OpenAPI JSON document"}},
                }
            },
            "/v1/models": {
                "get": {
                    "summary": "List available models and aliases authorized for the API key",
                    "security": [{"BearerAuth": []}],
                    "responses": {
                        "200": {"description": "List of authorized models"},
                        "401": {"description": "Missing or invalid API key"},
                    },
                }
            },
            "/v1/models/{model_id}": {
                "get": {
                    "summary": "Retrieve specific model or alias information",
                    "security": [{"BearerAuth": []}],
                    "parameters": [{"name": "model_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {"description": "Model information"},
                        "404": {"description": "Model not found"},
                    },
                }
            },
            "/v1/chat/completions": {
                "post": {
                    "summary": "Execute chat completion with or without streaming",
                    "security": [{"BearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["model", "messages"],
                                    "properties": {
                                        "model": {"type": "string", "description": "Model alias or name"},
                                        "messages": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "required": ["role", "content"],
                                                "properties": {
                                                    "role": {"type": "string", "enum": ["system", "user", "assistant"]},
                                                    "content": {"type": "string"},
                                                },
                                            },
                                        },
                                        "temperature": {"type": "number", "default": 0.7},
                                        "top_p": {"type": "number", "default": 0.95},
                                        "max_tokens": {"type": "integer"},
                                        "stream": {"type": "boolean", "default": False},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Chat completion response or SSE stream"},
                        "400": {"description": "Bad request or missing required parameters"},
                        "401": {"description": "Unauthorized"},
                        "404": {"description": "Model not found"},
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "Local API Key (sk-local-...)",
                }
            }
        },
    }


class GatewayRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler implementing OpenAI-compatible inference routes on loopback."""

    server: "GatewayServer"  # type: ignore

    def log_message(self, format: str, *args: Any) -> None:
        """Suppresses default stderr server logging."""
        pass

    def _apply_cors_headers(self) -> None:
        """Applies restricted CORS headers, permitting loopback origins only."""
        origin = self.headers.get("Origin")
        if not origin:
            return
        from urllib.parse import urlparse
        try:
            parsed = urlparse(origin)
            hostname = parsed.hostname or ""
            if hostname in ("127.0.0.1", "localhost", "::1"):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
        except Exception:
            pass

    def _send_json(self, status_code: int, data: Dict[str, Any]) -> None:
        """Sends JSON response with Content-Type application/json."""
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._apply_cors_headers()
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()
        self.wfile.write(payload)

    def _send_error(self, status_code: int, message: str, error_type: str = "invalid_request_error", code: Optional[str] = None) -> None:
        """Sends structured error envelope matching OpenAI schema."""
        envelope = ApiErrorEnvelope.create(message=message, type_name=error_type, code=code)
        self._send_json(status_code, envelope.model_dump())

    def _authenticate(self) -> Optional[ApiKeyRecord]:
        """Validates Bearer token from Authorization header against ApiKeyService."""
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._send_error(401, "Missing or malformed Authorization header. Expected 'Authorization: Bearer <key>'", code="unauthorized")
            return None

        token = auth[7:].strip()
        key_record = self.server.key_service.verify_token(token)
        if not key_record:
            self._send_error(401, "Invalid, expired, or revoked API key.", code="invalid_api_key")
            return None

        return key_record

    def do_OPTIONS(self) -> None:
        """Handles CORS pre-flight requests."""
        self.send_response(204)
        self._apply_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        """Routes GET requests for health, readiness, openapi, and model discovery."""
        path = self.path.split("?")[0].rstrip("/")
        if not path:
            path = "/"

        if path in ("/health", "/v1/health", "/"):
            self._send_json(200, {
                "status": "ok",
                "service": "localpilot-gateway",
                "version": GATEWAY_VERSION,
                "loopback_only": True,
            })
            return

        if path == "/v1/health/live":
            self._send_json(200, {
                "status": "alive",
                "service": "localpilot-gateway",
                "version": GATEWAY_VERSION,
                "loopback_only": True,
            })
            return

        if path == "/v1/health/ready":
            aliases = self.server.version_service.list_aliases()
            if not aliases:
                self._send_json(503, {
                    "status": "not_ready",
                    "service": "localpilot-gateway",
                    "version": GATEWAY_VERSION,
                    "reason": "No models or aliases registered yet",
                    "ready": False,
                })
                return

            ready_models = 0
            for a in aliases:
                ver = self.server.version_service.get_version(a.version_id, include_deleted=False)
                if not ver:
                    continue
                if ver.installed_path and not os.path.exists(ver.installed_path):
                    continue
                ready_models += 1

            if ready_models > 0:
                self._send_json(200, {
                    "status": "ready",
                    "service": "localpilot-gateway",
                    "version": GATEWAY_VERSION,
                    "available_models": ready_models,
                    "ready": True,
                })
            else:
                self._send_json(503, {
                    "status": "not_ready",
                    "service": "localpilot-gateway",
                    "version": GATEWAY_VERSION,
                    "reason": "No model files found on disk for active aliases",
                    "ready": False,
                })
            return

        if path in ("/openapi.json", "/v1/openapi.json"):
            self._send_json(200, get_openapi_spec())
            return

        if path == "/v1/models":
            key_record = self._authenticate()
            if not key_record:
                return

            aliases = self.server.version_service.list_aliases()
            allowed = key_record.policy.allowed_aliases if key_record.policy else ["*"]

            models_data = []
            for a in aliases:
                if "*" in allowed or a.alias_name in allowed:
                    models_data.append({
                        "id": a.alias_name,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "localpilot",
                        "permission": [],
                        "root": a.logical_model_id or a.alias_name,
                        "parent": None,
                    })

            self._send_json(200, {"object": "list", "data": models_data})
            return

        if path.startswith("/v1/models/"):
            key_record = self._authenticate()
            if not key_record:
                return

            model_id = path[len("/v1/models/"):]
            allowed = key_record.policy.allowed_aliases if key_record.policy else ["*"]
            if "*" not in allowed and model_id not in allowed:
                self._send_error(404, f"Model '{model_id}' does not exist or access is forbidden.", code="model_not_found")
                return

            alias = self.server.version_service.get_alias(model_id)
            if not alias:
                self._send_error(404, f"Model '{model_id}' does not exist.", code="model_not_found")
                return

            self._send_json(200, {
                "id": alias.alias_name,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "localpilot",
                "permission": [],
                "root": alias.logical_model_id,
            })
            return

        self._send_error(404, f"Route not found: {self.path}", code="route_not_found")

    def do_POST(self) -> None:
        """Routes POST requests for chat completions."""
        path = self.path.split("?")[0].rstrip("/")

        if path != "/v1/chat/completions":
            self._send_error(404, f"Endpoint not found: {self.path}", code="endpoint_not_found")
            return

        # 1. Authenticate API Key
        key_record = self._authenticate()
        if not key_record:
            return

        # 2. Check Content-Length (10MB limit)
        cl_header = self.headers.get("Content-Length")
        if cl_header:
            try:
                content_length = int(cl_header)
                if content_length > 10 * 1024 * 1024:
                    self._send_error(413, "Payload exceeds maximum allowed size of 10MB.", error_type="invalid_request_error", code="payload_too_large")
                    return
            except ValueError:
                self._send_error(400, "Invalid Content-Length header.", code="bad_request")
                return
        else:
            content_length = 0

        # 3. Parse JSON Request
        try:
            body = self.rfile.read(content_length).decode("utf-8")
            payload = json.loads(body)
        except Exception as exc:
            self._send_error(400, f"Malformed request body: {exc}", code="bad_request")
            return

        # 4. Reject Unsupported Capabilities (Tools & Function Calling)
        if payload.get("tools") or payload.get("tool_choice") or payload.get("functions") or payload.get("function_call"):
            self._send_error(
                400,
                "Tool use and function calling are not supported by this model runtime.",
                error_type="invalid_request_error",
                code="tools_unsupported",
            )
            return

        try:
            request = ChatCompletionRequest.model_validate(payload)
        except Exception as exc:
            self._send_error(400, f"Invalid chat completion request structure: {exc}", code="bad_request")
            return

        # 5. Check API Key Policy Permissions
        policy = key_record.policy
        if policy:
            allowed_caps = policy.allowed_capabilities or ["chat", "streaming"]
            if "chat" not in allowed_caps:
                self._send_error(403, "API key policy does not allow 'chat' capability.", error_type="permission_error", code="capability_forbidden")
                return
            if request.stream and "streaming" not in allowed_caps:
                self._send_error(403, "API key policy does not allow 'streaming' capability.", error_type="permission_error", code="capability_forbidden")
                return
            if policy.max_tokens_per_request is not None and policy.max_tokens_per_request > 0:
                requested_max = payload.get("max_tokens") or payload.get("max_completion_tokens")
                if requested_max and int(requested_max) > policy.max_tokens_per_request:
                    self._send_error(
                        400,
                        f"Requested max_tokens ({requested_max}) exceeds key limit ({policy.max_tokens_per_request}).",
                        error_type="invalid_request_error",
                        code="token_limit_exceeded",
                    )
                    return

        allowed_aliases = policy.allowed_aliases if policy else ["*"]
        if "*" not in allowed_aliases and request.model not in allowed_aliases:
            self._send_error(403, f"API key policy forbids access to model '{request.model}'.", code="model_forbidden")
            return

        # 6. Resolve Model Target
        target_version = self.server.version_service.resolve_alias(request.model)
        if not target_version:
            self._send_error(404, f"Model or alias '{request.model}' is not installed or available.", code="model_not_found")
            return

        # 7. Acquire In-flight Execution Lease
        lease_id = None
        try:
            lease_id = self.server.version_service.acquire_execution_lease(
                target_version.version_id, owner="request"
            )
        except Exception as lease_err:
            logger.warning(f"Could not acquire execution lease: {lease_err}")

        start_time = time.monotonic()
        prompt_tokens = 0
        completion_tokens = 0
        status_code = 200
        error_code = None
        first_token_latency_ms: Optional[float] = None
        headers_sent = False

        try:
            # 8. Locate or Start Model Runtime Instance
            runtime_info = self._get_or_start_runtime(target_version)
            endpoint_url = runtime_info["endpoint_url"]
            runtime_name = runtime_info.get("runtime_name", "llamacpp")
            model_target = runtime_info.get("model_id", target_version.logical_model_id)

            messages_dict = [{"role": m.role, "content": m.content or ""} for m in request.messages]

            # 9. Execute Inference
            if not request.stream:
                # Non-Streaming Request
                assistant_text = asyncio.run(
                    ChatService.send_message(
                        endpoint_url=endpoint_url,
                        runtime_name=runtime_name,
                        model_id=model_target,
                        messages=messages_dict,
                    )
                )
                total_tokens = prompt_tokens + completion_tokens

                resp = ChatCompletionResponse(
                    model=request.model,
                    choices=[
                        ChatChoice(
                            index=0,
                            message=ChatMessage(role="assistant", content=assistant_text),
                            finish_reason="stop",
                        )
                    ],
                    usage=UsageInfo(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                    ),
                )
                self._send_json(200, resp.model_dump())
                headers_sent = True
            else:
                # Streaming SSE Request
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self._apply_cors_headers()
                self.end_headers()
                headers_sent = True

                chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

                async def stream_tokens():
                    nonlocal completion_tokens, first_token_latency_ms
                    first_token = True
                    async for token in ChatService.stream_message(
                        endpoint_url=endpoint_url,
                        runtime_name=runtime_name,
                        model_id=model_target,
                        messages=messages_dict,
                    ):
                        if first_token:
                            first_token_latency_ms = round((time.monotonic() - start_time) * 1000.0, 2)
                            first_token = False

                        chunk = ChatCompletionChunk(
                            id=chat_id,
                            model=request.model,
                            choices=[
                                StreamChoice(
                                    index=0,
                                    delta=ChatDelta(role="assistant", content=token),
                                    finish_reason=None,
                                )
                            ],
                        )
                        chunk_payload = f"data: {chunk.model_dump_json()}\n\n".encode("utf-8")
                        try:
                            self.wfile.write(chunk_payload)
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            break

                    # Final finish chunk
                    final_chunk = ChatCompletionChunk(
                        id=chat_id,
                        model=request.model,
                        choices=[
                            StreamChoice(
                                index=0,
                                delta=ChatDelta(),
                                finish_reason="stop",
                            )
                        ],
                    )
                    try:
                        self.wfile.write(f"data: {final_chunk.model_dump_json()}\n\n".encode("utf-8"))
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass

                asyncio.run(stream_tokens())

        except Exception as exc:
            status_code = 500
            error_code = "inference_failed"
            logger.exception("Inference error in GatewayServer")
            if not headers_sent:
                try:
                    self._send_error(500, f"Inference execution failed: {exc}", code=error_code)
                except Exception:
                    pass
            else:
                try:
                    err_chunk = {
                        "error": {
                            "message": f"Inference execution failed: {exc}",
                            "type": "server_error",
                            "code": error_code,
                        }
                    }
                    self.wfile.write(f"data: {json.dumps(err_chunk)}\n\n".encode("utf-8"))
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except Exception:
                    pass
        finally:
            # 10. Release In-Flight Execution Lease
            if lease_id:
                try:
                    self.server.version_service.release_execution_lease(lease_id)
                except Exception:
                    pass

            # 11. Record Usage Accounting
            latency_ms = round((time.monotonic() - start_time) * 1000.0, 2)
            try:
                self.server.accounting_service.record_request(
                    key_id=key_record.key_id,
                    model_alias=request.model,
                    resolved_version_id=target_version.version_id,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=latency_ms,
                    first_token_latency_ms=first_token_latency_ms,
                    status_code=status_code,
                    error_code=error_code,
                    app_name=key_record.app_name,
                )
            except Exception as acct_exc:
                logger.warning(f"Failed to record API request accounting: {acct_exc}")

    def _get_or_start_runtime(self, target_version: Any) -> Dict[str, Any]:
        """Resolves an active running runtime instance or starts one on-demand."""
        running = self.server.runtime_service.reconcile_running_instances()
        for inst in running:
            if (
                inst.get("installation_id") == target_version.installation_id
                or inst.get("model_id") == target_version.logical_model_id
            ):
                return {
                    "endpoint_url": inst["endpoint_url"],
                    "runtime_name": inst.get("runtime_name", "llamacpp"),
                    "model_id": inst.get("model_id", target_version.logical_model_id),
                }

        # If not already running and installation exists, start it
        if target_version.installation_id:
            started = asyncio.run(
                self.server.runtime_service.start_installed_model(target_version.installation_id)
            )
            return {
                "endpoint_url": started["endpoint_url"],
                "runtime_name": "llamacpp",
                "model_id": target_version.logical_model_id,
            }

        raise RuntimeError(f"No running instance found and no installation registered for version {target_version.version_id}")


class GatewayServer(ThreadingHTTPServer):
    """Threaded HTTP server running on strict loopback (127.0.0.1) for local inference."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        db: Optional[DatabaseManager] = None,
        data_dir: Optional[Path] = None,
    ) -> None:
        self.db = db or get_db()
        self.data_dir = data_dir or get_default_data_dir()
        self.key_service = ApiKeyService(self.db)
        self.version_service = ModelVersionService(self.db)
        self.accounting_service = ApiAccountingService(self.db)
        self.runtime_service = RuntimeLifecycleService(self.db)
        self.lock_file = self.data_dir / "gateway.json"

        # Force strict loopback
        if host not in ("127.0.0.1", "localhost"):
            logger.warning(f"Binding to '{host}' requested; enforcing loopback '127.0.0.1' for safety.")
            host = "127.0.0.1"

        self.target_host = host
        self.target_port = port

        super().__init__((host, port), GatewayRequestHandler)

    def write_state(self) -> None:
        """Writes current gateway server process state to data directory."""
        actual_port = self.server_address[1] if hasattr(self, "server_address") and self.server_address else self.target_port
        state = {
            "pid": os.getpid(),
            "host": self.target_host,
            "port": actual_port,
            "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        try:
            self.lock_file.write_text(json.dumps(state, indent=2))
        except Exception as exc:
            logger.warning(f"Could not write gateway state lock file: {exc}")

    def clear_state(self) -> None:
        """Removes gateway lock state upon shutdown."""
        if self.lock_file.exists():
            try:
                self.lock_file.unlink()
            except Exception:
                pass

    @classmethod
    def get_running_state(cls, data_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
        """Checks if a gateway process is currently recorded as running."""
        dd = data_dir or get_default_data_dir()
        lf = dd / "gateway.json"
        if not lf.exists():
            return None
        try:
            data = json.loads(lf.read_text())
            pid = data.get("pid")
            if pid and os.path.exists(f"/proc/{pid}"):
                return data
            # On platforms without /proc, verify via os.kill(pid, 0)
            if pid:
                try:
                    os.kill(pid, 0)
                    return data
                except (OSError, ProcessLookupError):
                    pass
        except Exception:
            pass
        return None
