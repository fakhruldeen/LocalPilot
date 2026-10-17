"""Ollama runtime adapter with multi-scope detection, provisioning, and lifecycle management."""

from __future__ import annotations
import asyncio
import json
import os
import platform
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import httpx
import psutil

from localpilot.domain.capabilities import ManagedServiceId
from localpilot.domain.models import NormalizedCandidate
from localpilot.domain.ownership import OwnershipClass, ProcessIdentity

from localpilot.runtimes.base import (
    BaseRuntimeAdapter,
    InstallationScope,
    ProtectionLevel,
    RuntimeCapabilities,
    RuntimeInstallPlan,
)
from localpilot.security import safe_extract_tar, safe_extract_zip
from localpilot.storage.db import get_default_data_dir


class OllamaRuntimeAdapter(BaseRuntimeAdapter):
    """Adapter for running models via Ollama."""

    OLLAMA_VERSION = "0.5.12"

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = data_dir or get_default_data_dir()
        self.models_dir = self.data_dir / "models" / "ollama"
        self.models_dir.mkdir(parents=True, exist_ok=True)

    def get_runtime_name(self) -> str:
        return "ollama"

    def get_capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            name="ollama",
            supported_formats=["ollama_blob", "gguf"],
            resolve_installed_identity=True,
            detect_newer_identity=True,
            retain_both_versions=False,  # Ollama tags overwrite unless distinct tag
            test_independently=True,
            restore_previous_version=True,
            protection_level=ProtectionLevel.RECOVERY_COPY_AVAILABLE,
        )

    def detect_existing_installation(self) -> Optional[Tuple[InstallationScope, str]]:
        # 1. Check isolated application runtime directory
        isolated_bin = self.data_dir / "runtimes" / "ollama" / "bin" / "ollama"
        if isolated_bin.exists() and os.access(isolated_bin, os.X_OK):
            return InstallationScope.ISOLATED, str(isolated_bin)

        # 2. Check user-space ~/.local/bin
        user_bin = Path(os.path.expanduser("~/.local/bin/ollama"))
        if user_bin.exists() and os.access(user_bin, os.X_OK):
            return InstallationScope.USER, str(user_bin)

        # 3. Check system-wide PATH
        sys_bin = shutil.which("ollama")
        if sys_bin:
            return InstallationScope.SYSTEM, sys_bin

        return None

    def build_install_plan(self, scope: InstallationScope = InstallationScope.ISOLATED) -> RuntimeInstallPlan:
        arch = platform.machine().lower()
        os_sys = platform.system().lower()

        if os_sys == "darwin":
            filename = "ollama-darwin.zip"
        elif "arm" in arch or "aarch64" in arch:
            filename = "ollama-linux-arm64.tgz"
        else:
            filename = "ollama-linux-amd64.tgz"

        url = f"https://github.com/ollama/ollama/releases/download/v{self.OLLAMA_VERSION}/{filename}"

        if scope == InstallationScope.ISOLATED:
            target_bin = str(self.data_dir / "runtimes" / "ollama" / "bin" / "ollama")
            elevation = False
        elif scope == InstallationScope.USER:
            target_bin = str(Path(os.path.expanduser("~/.local/bin/ollama")))
            elevation = False
        else:
            target_bin = "/usr/local/bin/ollama"
            elevation = True

        return RuntimeInstallPlan(
            runtime_name="ollama",
            target_scope=scope,
            target_bin_path=target_bin,
            download_url=url,
            estimated_download_bytes=60 * 1024 * 1024,
            requires_elevation=elevation,
            plan_description=f"Install Ollama v{self.OLLAMA_VERSION} ({scope.value} scope) into {target_bin}",
        )

    async def prepare_runtime(
        self,
        scope: InstallationScope = InstallationScope.ISOLATED,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> str:
        existing = self.detect_existing_installation()
        if existing:
            return existing[1]

        plan = self.build_install_plan(scope)
        target_path = Path(plan.target_bin_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Download release archive into downloads dir
        downloads_dir = self.data_dir / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        archive_path = downloads_dir / Path(plan.download_url).name

        if plan.download_url:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                async with client.stream("GET", plan.download_url) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))
                    downloaded = 0
                    with open(archive_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback and total > 0:
                                progress_callback(downloaded, total)

        # Extract archive
        extract_dir = target_path.parent.parent if scope == InstallationScope.ISOLATED else downloads_dir / "extracted_ollama"
        extract_dir.mkdir(parents=True, exist_ok=True)

        if str(archive_path).endswith(".tgz") or str(archive_path).endswith(".tar.gz"):
            safe_extract_tar(archive_path, extract_dir)
        elif str(archive_path).endswith(".zip"):
            safe_extract_zip(archive_path, extract_dir)

        # Locate extracted binary
        extracted_bin = None
        for p in extract_dir.rglob("ollama"):
            if p.is_file():
                extracted_bin = p
                break

        if extracted_bin:
            if str(extracted_bin) != str(target_path):
                shutil.copy2(extracted_bin, target_path)
            os.chmod(target_path, 0o755)

        t_path = Path(target_path)
        if not t_path.exists() or t_path.stat().st_size == 0 or not os.access(t_path, os.X_OK):
            raise RuntimeError(f"Ollama preparation failed: binary was not found or not executable at {target_path} after extraction.")

        # Smoke check execution
        smoke = subprocess.run([str(target_path), "--version"], capture_output=True, timeout=5)
        if smoke.returncode != 0:
            err = smoke.stderr.decode("utf-8", errors="ignore") or smoke.stdout.decode("utf-8", errors="ignore")
            raise RuntimeError(f"Ollama binary smoke check failed with exit code {smoke.returncode}: {err}")

        return str(target_path)

    async def start_instance(
        self,
        model_path_or_id: str,
        candidate: NormalizedCandidate,
        instance_id: str,
        log_file: Path,
    ) -> ProcessIdentity:
        existing = self.detect_existing_installation()
        bin_path = existing[1] if existing else await self.prepare_runtime(InstallationScope.ISOLATED)

        # Check if ollama daemon is already responding
        endpoint = "http://127.0.0.1:11434"
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                r = await client.get(f"{endpoint}/api/tags")
                if r.status_code == 200:
                    # Daemon already running externally; represent with unknown/None process identity
                    return ProcessIdentity(
                        pid=None,
                        process_create_time=None,
                        executable_path=bin_path,
                        endpoint_url=endpoint,
                        ownership_class=OwnershipClass.READONLY_DISCOVERED,
                        service_id="ollama",
                        deployment_method="local_api_service",
                    )
        except Exception:
            pass

        # Spawn owned daemon
        env = os.environ.copy()
        env["OLLAMA_HOST"] = "127.0.0.1:11434"
        env["OLLAMA_MODELS"] = str(self.models_dir)

        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_fd = open(log_file, "a", encoding="utf-8")

        proc = subprocess.Popen(
            [bin_path, "serve"],
            env=env,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )

        # Wait for daemon readiness asynchronously while monitoring child process health
        ready = False
        async with httpx.AsyncClient(timeout=1.0) as client:
            for _ in range(30):
                await asyncio.sleep(0.5)
                if proc.poll() is not None:
                    raise RuntimeError(f"Ollama daemon terminated unexpectedly with exit code {proc.returncode}.")
                try:
                    r = await client.get(f"{endpoint}/api/tags")
                    if r.status_code == 200:
                        ready = True
                        break
                except Exception:
                    pass

        if not ready:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
            raise RuntimeError("Ollama daemon failed to become ready within 15 seconds.")

        p_proc = psutil.Process(proc.pid)
        return ProcessIdentity(
            pid=proc.pid,
            process_create_time=p_proc.create_time(),
            executable_path=bin_path,
            endpoint_url=endpoint,
            ownership_class=OwnershipClass.LOCALPILOT_OWNED,
            service_id="ollama",
            deployment_method="local_api_service",
        )

    def get_service_id(self) -> ManagedServiceId:
        return ManagedServiceId.OLLAMA

    async def pull_model(
        self,
        endpoint_url: str,
        model_id: str,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[str, Any]:
        """Pulls a model through the Ollama service API (Section 10.3)."""
        url = endpoint_url.rstrip("/") + "/api/pull"
        error_msg = None
        saw_terminal_status = False
        async with httpx.AsyncClient(timeout=600.0) as client:
            async with client.stream("POST", url, json={"name": model_id, "stream": True}) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if "error" in data:
                            error_msg = data["error"]
                            break
                        status_msg = data.get("status", "Pulling model...")
                        if status_msg == "success":
                            saw_terminal_status = True
                        completed = data.get("completed", 0)
                        total = data.get("total", 0)
                        if progress_callback:
                            progress_callback(status_msg, completed, total)
                    except Exception:
                        pass

        if error_msg:
            raise RuntimeError(f"Ollama pull failed for {model_id}: {error_msg}")

        # Inspect pulled model digest and size
        digest = None
        size = 0
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                tags_r = await client.get(endpoint_url.rstrip("/") + "/api/tags")
                if tags_r.status_code == 200:
                    models = tags_r.json().get("models", [])
                    target_names = {model_id, f"{model_id}:latest" if ":" not in model_id else model_id}
                    for m in models:
                        name_tag = m.get("name", "")
                        model_tag = m.get("model", "")
                        if name_tag in target_names or model_tag in target_names:
                            digest = m.get("digest")
                            size = m.get("size", 0)
                            break
        except Exception:
            pass

        if not digest:
            raise RuntimeError(f"Model '{model_id}' was pulled but could not be verified in Ollama tags catalog.")

        return {
            "success": True,
            "model_id": model_id,
            "digest": digest,
            "size": size,
        }

    async def delete_model(self, endpoint_url: str, model_id: str) -> bool:
        """Deletes model weights via Ollama API (Section 18.2)."""
        url = endpoint_url.rstrip("/") + "/api/delete"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.request("DELETE", url, json={"name": model_id})
                return resp.status_code in (200, 204)
        except Exception:
            return False

    async def unload_model(self, endpoint_url: str, model_id: str) -> bool:
        """Unloads model memory without stopping the service daemon (Section 6.A)."""
        if not model_id:
            return False
        url = endpoint_url.rstrip("/") + "/api/generate"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # keep_alive=0 instructs Ollama to immediately unload the model from VRAM/RAM
                resp = await client.post(url, json={"model": model_id, "keep_alive": 0})
                return resp.status_code == 200
        except Exception:
            return False

    def stop_owned_daemon(self, identity: ProcessIdentity) -> bool:
        """Terminates an owned daemon process with verified process identity."""
        if identity.ownership_class != OwnershipClass.LOCALPILOT_OWNED or identity.pid is None:
            return False
        if psutil.pid_exists(identity.pid):
            try:
                p = psutil.Process(identity.pid)
                if identity.process_create_time and abs(p.create_time() - identity.process_create_time) > 1.0:
                    return False
                p.terminate()
                p.wait(timeout=5)
                return True
            except (psutil.TimeoutExpired, Exception):
                try:
                    p.kill()
                    return True
                except Exception:
                    return False
        return True

    async def stop_instance(
        self,
        identity: ProcessIdentity,
        model_id: Optional[str] = None,
        stop_daemon: bool = False,
    ) -> bool:
        """Stops the running model. Never stops external/shared daemons automatically."""
        # Unload model memory via API without killing daemon
        target_model = model_id or (identity.service_id if identity.service_id and identity.service_id != "ollama" else None)
        if not target_model:
            return False

        unloaded = False
        if identity.endpoint_url:
            unloaded = await self.unload_model(identity.endpoint_url, target_model)

        # Only terminate daemon process if explicitly requested and strictly owned
        if stop_daemon:
            if identity.ownership_class == OwnershipClass.LOCALPILOT_OWNED:
                stopped = self.stop_owned_daemon(identity)
                return unloaded and stopped
            return False

        return unloaded

    async def test_inference(self, endpoint_url: str, model_id: str) -> bool:
        url = endpoint_url.rstrip("/") + "/api/generate"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    url,
                    json={"model": model_id, "prompt": "Hi", "stream": False},
                )
                return resp.status_code == 200
        except Exception:
            return False
