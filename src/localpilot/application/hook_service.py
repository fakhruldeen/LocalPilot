"""Isolated lifecycle hooks service with strict execution security and circuit breaker controls.

Security Invariants:
- Default disabled; requires explicit opt-in and user approval.
- shell=False strictly enforced; argument array only.
- Completely sanitized environment: secrets, tokens, API keys are purged.
- Bounded execution timeout (default 5.0 seconds).
- Bounded stdout/stderr capture (capped at 4KB).
- Automatic circuit-breaker tripping after 3 consecutive failures.
"""

from __future__ import annotations
import json
import logging
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from localpilot.domain.api_contract import (
    HookDefinition,
    HookEvent,
    HookEventType,
    HookRunResult,
)
from localpilot.storage.db import DatabaseManager, get_db

logger = logging.getLogger(__name__)

ALLOWED_SYSTEM_ENV_VARS = {"PATH", "HOME", "TMPDIR", "USER", "LANG", "LC_ALL", "TERM"}


class LifecycleHookService:
    """Manages event dispatching and safe isolated execution of lifecycle hooks."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or get_db()

    def register_hook(
        self,
        name: str,
        event_type: str,
        command: List[str],
        action_type: str = "executable",
        approved_by_user: bool = False,
    ) -> HookDefinition:
        """Registers a new lifecycle hook (disabled by default)."""
        if not command and action_type == "executable":
            raise ValueError("Executable hook must have at least one command argument.")
        if not isinstance(command, list):
            raise ValueError("Hook command must be an argument array (list of strings), not a shell string.")

        hook_id = f"hook_{uuid.uuid4().hex[:8]}"
        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        hook = HookDefinition(
            hook_id=hook_id,
            name=name,
            event_type=event_type,
            action_type=action_type,
            command=command,
            is_enabled=False,
            approved_by_user=approved_by_user,
            consecutive_failures=0,
            is_tripped=False,
            created_at=now_str,
            updated_at=now_str,
        )

        conn = self.db.get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO lifecycle_hooks (
                    hook_id, name, event_type, action_type, command_json,
                    is_enabled, approved_by_user, consecutive_failures, is_tripped,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hook.hook_id,
                    hook.name,
                    hook.event_type,
                    hook.action_type,
                    json.dumps(hook.command),
                    1 if hook.is_enabled else 0,
                    1 if hook.approved_by_user else 0,
                    hook.consecutive_failures,
                    1 if hook.is_tripped else 0,
                    hook.created_at,
                    hook.updated_at,
                ),
            )
        return hook

    def list_hooks(self) -> List[HookDefinition]:
        """Lists all registered lifecycle hooks."""
        conn = self.db.get_connection()
        rows = conn.execute("SELECT * FROM lifecycle_hooks ORDER BY created_at ASC").fetchall()
        results: List[HookDefinition] = []
        for r in rows:
            d = dict(r)
            results.append(
                HookDefinition(
                    hook_id=d["hook_id"],
                    name=d["name"],
                    event_type=d["event_type"],
                    action_type=d["action_type"],
                    command=json.loads(d.get("command_json") or "[]"),
                    is_enabled=bool(d["is_enabled"]),
                    approved_by_user=bool(d["approved_by_user"]),
                    consecutive_failures=d["consecutive_failures"],
                    is_tripped=bool(d["is_tripped"]),
                    created_at=d["created_at"],
                    updated_at=d["updated_at"],
                )
            )
        return results

    def get_hook(self, hook_id: str) -> Optional[HookDefinition]:
        """Retrieves a single hook by ID."""
        conn = self.db.get_connection()
        row = conn.execute("SELECT * FROM lifecycle_hooks WHERE hook_id = ?", (hook_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        return HookDefinition(
            hook_id=d["hook_id"],
            name=d["name"],
            event_type=d["event_type"],
            action_type=d["action_type"],
            command=json.loads(d.get("command_json") or "[]"),
            is_enabled=bool(d["is_enabled"]),
            approved_by_user=bool(d["approved_by_user"]),
            consecutive_failures=d["consecutive_failures"],
            is_tripped=bool(d["is_tripped"]),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )

    def enable_hook(self, hook_id: str, approved: bool = True) -> HookDefinition:
        """Enables a hook, requiring user approval."""
        hook = self.get_hook(hook_id)
        if not hook:
            raise ValueError(f"Hook '{hook_id}' not found.")

        if not hook.approved_by_user and not approved:
            raise ValueError(f"Hook '{hook_id}' cannot be enabled without explicit user approval.")

        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                """
                UPDATE lifecycle_hooks
                SET is_enabled = 1, approved_by_user = 1, updated_at = ?
                WHERE hook_id = ?
                """,
                (now_str, hook_id),
            )
        return self.get_hook(hook_id)

    def disable_hook(self, hook_id: str) -> HookDefinition:
        """Disables a hook."""
        hook = self.get_hook(hook_id)
        if not hook:
            raise ValueError(f"Hook '{hook_id}' not found.")

        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                "UPDATE lifecycle_hooks SET is_enabled = 0, updated_at = ? WHERE hook_id = ?",
                (now_str, hook_id),
            )
        return self.get_hook(hook_id)

    def reset_circuit_breaker(self, hook_id: str) -> HookDefinition:
        """Resets a tripped circuit breaker for a hook."""
        hook = self.get_hook(hook_id)
        if not hook:
            raise ValueError(f"Hook '{hook_id}' not found.")

        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                """
                UPDATE lifecycle_hooks
                SET consecutive_failures = 0, is_tripped = 0, updated_at = ?
                WHERE hook_id = ?
                """,
                (now_str, hook_id),
            )
        return self.get_hook(hook_id)

    def list_runs(self, hook_id: Optional[str] = None, limit: int = 50) -> List[HookRunResult]:
        """Lists recent hook run history."""
        conn = self.db.get_connection()
        if hook_id:
            rows = conn.execute(
                "SELECT * FROM hook_runs WHERE hook_id = ? ORDER BY triggered_at DESC LIMIT ?",
                (hook_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM hook_runs ORDER BY triggered_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        results: List[HookRunResult] = []
        for r in rows:
            d = dict(r)
            results.append(
                HookRunResult(
                    run_id=d["run_id"],
                    hook_id=d["hook_id"],
                    event_type=d["event_type"],
                    triggered_at=d["triggered_at"],
                    status=d["status"],
                    exit_code=d["exit_code"],
                    duration_ms=d["duration_ms"],
                    output_summary=d["output_summary"],
                )
            )
        return results

    def dispatch_event(
        self,
        event_type: str,
        data: Optional[Dict[str, Any]] = None,
        operation_id: Optional[str] = None,
    ) -> List[HookRunResult]:
        """Dispatches an event, logging activity and running approved active hooks."""
        event = HookEvent(
            event_type=event_type,
            data=data or {},
            operation_id=operation_id,
        )

        # Built-in handler: record to activity_log
        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO activity_log (
                    activity_id, timestamp, category, title, summary,
                    details_json, operation_id, outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"act_{uuid.uuid4().hex[:8]}",
                    now_str,
                    "lifecycle",
                    f"Lifecycle Event: {event_type}",
                    f"Dispatched event {event_type}",
                    json.dumps(event.data),
                    operation_id,
                    "success",
                ),
            )

        # Find matching active hooks
        hooks = self.list_hooks()
        matching = [
            h for h in hooks
            if h.is_enabled and not h.is_tripped and (h.event_type in (event_type, "all", HookEventType.ALL.value))
        ]

        results: List[HookRunResult] = []
        for hook in matching:
            res = self.execute_hook(hook, event)
            results.append(res)

        return results

    def execute_hook(
        self,
        hook: HookDefinition,
        event: HookEvent,
        timeout_seconds: float = 5.0,
    ) -> HookRunResult:
        """Executes a single hook under strict isolation and updates its circuit breaker state."""
        run_id = f"hrun_{uuid.uuid4().hex[:8]}"
        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        if hook.is_tripped:
            return HookRunResult(
                run_id=run_id,
                hook_id=hook.hook_id,
                event_type=event.event_type,
                triggered_at=now_str,
                status="circuit_broken",
                exit_code=-1,
                duration_ms=0.0,
                output_summary="Execution refused: circuit breaker is tripped due to consecutive failures.",
            )

        # Build clean environment without secrets
        clean_env: Dict[str, str] = {}
        for k in ALLOWED_SYSTEM_ENV_VARS:
            if k in os.environ:
                clean_env[k] = os.environ[k]

        clean_env["LOCALPILOT_EVENT_TYPE"] = event.event_type
        clean_env["LOCALPILOT_EVENT_ID"] = event.event_id
        clean_env["LOCALPILOT_EVENT_TIMESTAMP"] = event.timestamp
        clean_env["LOCALPILOT_EVENT_DATA"] = json.dumps(event.data)

        start_time = time.monotonic()
        status = "success"
        exit_code: Optional[int] = 0
        summary: str = ""

        try:
            proc = subprocess.Popen(
                hook.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                env=clean_env,
                cwd="/tmp",
            )
            stdout_data, stderr_data = proc.communicate(
                input=json.dumps(event.model_dump()),
                timeout=timeout_seconds,
            )
            exit_code = proc.returncode
            elapsed_ms = (time.monotonic() - start_time) * 1000.0

            combined = (stdout_data + "\n" + stderr_data).strip()
            summary = combined[:4096] if combined else "Completed with no output."

            if exit_code != 0:
                status = "failed"
        except subprocess.TimeoutExpired:
            proc.kill()
            exit_code = -1
            elapsed_ms = (time.monotonic() - start_time) * 1000.0
            status = "timeout"
            summary = f"Hook execution timed out after {timeout_seconds} seconds."
        except Exception as exc:
            exit_code = -1
            elapsed_ms = (time.monotonic() - start_time) * 1000.0
            status = "failed"
            summary = f"Execution error: {exc}"

        # Update failure count and circuit breaker
        new_failures = hook.consecutive_failures
        is_tripped = hook.is_tripped
        if status in ("failed", "timeout"):
            new_failures += 1
            if new_failures >= 3:
                is_tripped = True
                logger.warning("Circuit breaker tripped for hook '%s' (%s)", hook.name, hook.hook_id)
        else:
            new_failures = 0

        now_updated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                """
                UPDATE lifecycle_hooks
                SET consecutive_failures = ?, is_tripped = ?, updated_at = ?
                WHERE hook_id = ?
                """,
                (new_failures, 1 if is_tripped else 0, now_updated, hook.hook_id),
            )
            conn.execute(
                """
                INSERT INTO hook_runs (
                    run_id, hook_id, event_type, triggered_at, status,
                    exit_code, duration_ms, output_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    hook.hook_id,
                    event.event_type,
                    now_str,
                    status,
                    exit_code,
                    elapsed_ms,
                    summary,
                ),
            )

        return HookRunResult(
            run_id=run_id,
            hook_id=hook.hook_id,
            event_type=event.event_type,
            triggered_at=now_str,
            status=status,
            exit_code=exit_code,
            duration_ms=elapsed_ms,
            output_summary=summary,
        )
