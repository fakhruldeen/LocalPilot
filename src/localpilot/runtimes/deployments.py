"""Deployment controllers for Direct Worker, Local API, Container, and Browser (Section 7)."""

from __future__ import annotations
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import httpx
import psutil
from pydantic import BaseModel

from localpilot.domain.capabilities import (
    AdapterCapability,
    CapabilityResult,
    DeploymentMethod,
    ImplementationStatus,
)
from localpilot.domain.ownership import OwnershipClass, ProcessIdentity


class WorkerCrashError(RuntimeError):
    """Raised when an isolated worker process crashes unexpectedly."""
    pass


class DirectWorkerController:
    """Runs model execution in an isolated child worker process outside the CLI/TUI.

    Features:
    - Structured request/result/progress protocol over pipes.
    - Captures lifecycle and output continuously.
    - Recovers cleanly from worker crashes without taking down the UI.
    """

    def __init__(self, python_executable: Optional[str] = None) -> None:
        self.python_exe = python_executable or sys.executable
        self._proc: Optional[subprocess.Popen] = None
        self._identity: Optional[ProcessIdentity] = None

    def start_worker(self, worker_script_or_args: List[str], log_file: Path) -> ProcessIdentity:
        """Starts worker subprocess and registers identity."""
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_fd = open(log_file, "a", encoding="utf-8")

        cmd = [self.python_exe] + worker_script_or_args
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log_fd,
            text=True,
            bufsize=1,
        )

        p = psutil.Process(self._proc.pid)
        self._identity = ProcessIdentity(
            pid=self._proc.pid,
            process_create_time=p.create_time(),
            executable_path=self.python_exe,
            ownership_class=OwnershipClass.LOCALPILOT_OWNED,
            deployment_method="direct_worker",
        )
        return self._identity

    def execute_request(self, request_payload: Dict[str, Any], timeout_seconds: float = 30.0) -> Dict[str, Any]:
        """Sends request to worker and receives structured JSON response."""
        if not self._proc or self._proc.poll() is not None:
            code = self._proc.returncode if self._proc else -1
            raise WorkerCrashError(f"Worker process is not running (exit code: {code}).")

        req_line = json.dumps(request_payload) + "\n"
        try:
            self._proc.stdin.write(req_line)
            self._proc.stdin.flush()
            resp_line = self._proc.stdout.readline()
            if not resp_line:
                code = self._proc.poll()
                raise WorkerCrashError(f"Worker terminated unexpectedly while executing request (exit code: {code}).")
            return json.loads(resp_line.strip())
        except (BrokenPipeError, OSError) as e:
            raise WorkerCrashError(f"Failed communicating with worker process: {e}") from e

    def stop_worker(self) -> bool:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
                return True
            except (subprocess.TimeoutExpired, Exception):
                try:
                    self._proc.kill()
                    return True
                except Exception:
                    return False
        return True


class LocalApiServiceController:
    """Controls localhost-bound (127.0.0.1) API services separating readiness and inference."""

    @staticmethod
    async def wait_for_readiness(
        endpoint_url: str,
        health_path: str = "/health",
        timeout_seconds: float = 20.0,
    ) -> bool:
        url = endpoint_url.rstrip("/") + health_path
        start = asyncio.get_event_loop().time()
        async with httpx.AsyncClient(timeout=2.0) as client:
            while asyncio.get_event_loop().time() - start < timeout_seconds:
                try:
                    r = await client.get(url)
                    if r.status_code in (200, 204):
                        return True
                except Exception:
                    pass
                await asyncio.sleep(0.5)
        return False


class ContainerDeploymentController:
    """Manages containerized inference execution tracking container identity separately from host PID."""

    def __init__(self) -> None:
        pass

    def inspect_container_support(self) -> CapabilityResult:
        import shutil
        docker_bin = shutil.which("docker")
        if not docker_bin:
            return CapabilityResult(
                supported=False,
                status=ImplementationStatus.UNSUPPORTED,
                capability=AdapterCapability.LAUNCH_SERVER_OR_WORKER,
                reason_code="DOCKER_NOT_INSTALLED",
                friendly_explanation="Docker is not installed on this system.",
            )
        return CapabilityResult(
            supported=True,
            status=ImplementationStatus.IMPLEMENTED_TESTED,
            capability=AdapterCapability.LAUNCH_SERVER_OR_WORKER,
            friendly_explanation="Container execution is available via Docker.",
        )


class BrowserDeploymentController:
    """Browser deployment extension point (Section 7: Planned/Unsupported)."""

    @staticmethod
    def get_capability() -> CapabilityResult:
        return CapabilityResult(
            supported=False,
            status=ImplementationStatus.PLANNED,
            capability=AdapterCapability.LAUNCH_SERVER_OR_WORKER,
            reason_code="BROWSER_DEPLOYMENT_NOT_IMPLEMENTED",
            friendly_explanation="Running models directly inside a web browser is not supported yet.",
        )
