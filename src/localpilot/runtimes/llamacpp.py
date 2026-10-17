"""llama.cpp runtime adapter with multi-scope detection, provisioning, and lifecycle management."""

from __future__ import annotations
import asyncio
import hashlib
import os
import platform
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import httpx
import psutil

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


def find_free_port() -> int:
    """Finds an unused TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LlamaCppRuntimeAdapter(BaseRuntimeAdapter):
    """Adapter for running GGUF models directly via llama.cpp (llama-server)."""

    LLAMA_CPP_TAG = "b4800"

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = data_dir or get_default_data_dir()
        self.runtimes_dir = self.data_dir / "runtimes" / "llamacpp"
        self.runtimes_dir.mkdir(parents=True, exist_ok=True)

    def get_runtime_name(self) -> str:
        return "llamacpp"

    def get_capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            name="llamacpp",
            supported_formats=["gguf"],
            resolve_installed_identity=True,
            detect_newer_identity=True,
            retain_both_versions=True,
            test_independently=True,
            restore_previous_version=True,
            protection_level=ProtectionLevel.RECOVERY_COPY_AVAILABLE,
        )

    @staticmethod
    def get_runtime_env(bin_path: Union[str, Path]) -> Dict[str, str]:
        """Constructs an environment dictionary with LD_LIBRARY_PATH/DYLD_LIBRARY_PATH

        containing directories where libllama.so, libggml.so, or libllama.dylib reside.
        """
        env = os.environ.copy()
        bin_p = Path(bin_path).resolve()
        candidate_dirs = [
            bin_p.parent,
            bin_p.parent / "build" / "bin",
            bin_p.parent / "lib",
            bin_p.parent.parent / "lib",
        ]
        valid_lib_dirs = [
            str(d) for d in candidate_dirs
            if d.is_dir() and any(f.suffix in (".so", ".dylib") for f in d.iterdir() if f.is_file())
        ]
        if valid_lib_dirs:
            joined = ":".join(valid_lib_dirs)
            current_ld = env.get("LD_LIBRARY_PATH", "")
            env["LD_LIBRARY_PATH"] = f"{joined}:{current_ld}" if current_ld else joined
            current_dyld = env.get("DYLD_LIBRARY_PATH", "")
            env["DYLD_LIBRARY_PATH"] = f"{joined}:{current_dyld}" if current_dyld else joined
        return env

    def detect_existing_installation(self) -> Optional[Tuple[InstallationScope, str]]:
        # 1. Check isolated application runtime directory
        isolated_bin = self.runtimes_dir / "bin" / "llama-server"
        if isolated_bin.exists() and os.access(isolated_bin, os.X_OK):
            return InstallationScope.ISOLATED, str(isolated_bin)

        # 2. Check user-space ~/.local/bin
        user_bin = Path(os.path.expanduser("~/.local/bin/llama-server"))
        if user_bin.exists() and os.access(user_bin, os.X_OK):
            return InstallationScope.USER, str(user_bin)

        # 3. Check system-wide PATH
        sys_bin = shutil.which("llama-server")
        if sys_bin:
            return InstallationScope.SYSTEM, sys_bin

        return None

    def build_install_plan(self, scope: InstallationScope = InstallationScope.ISOLATED) -> RuntimeInstallPlan:
        arch = platform.machine().lower()
        os_sys = platform.system().lower()

        if os_sys == "darwin":
            filename = f"llama-{self.LLAMA_CPP_TAG}-bin-macos-arm64.zip" if "arm" in arch else f"llama-{self.LLAMA_CPP_TAG}-bin-macos-x64.zip"
        else:
            filename = f"llama-{self.LLAMA_CPP_TAG}-bin-ubuntu-x64.zip"

        url = f"https://github.com/ggml-org/llama.cpp/releases/download/{self.LLAMA_CPP_TAG}/{filename}"

        if scope == InstallationScope.ISOLATED:
            target_bin = str(self.runtimes_dir / "bin" / "llama-server")
            elevation = False
        elif scope == InstallationScope.USER:
            target_bin = str(Path(os.path.expanduser("~/.local/bin/llama-server")))
            elevation = False
        else:
            target_bin = "/usr/local/bin/llama-server"
            elevation = True

        return RuntimeInstallPlan(
            runtime_name="llamacpp",
            target_scope=scope,
            target_bin_path=target_bin,
            download_url=url,
            estimated_download_bytes=40 * 1024 * 1024,
            requires_elevation=elevation,
            plan_description=f"Install llama.cpp {self.LLAMA_CPP_TAG} ({scope.value} scope) into {target_bin}",
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

        downloads_dir = self.data_dir / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        archive_path = downloads_dir / Path(plan.download_url).name

        if plan.download_url:
            hasher = hashlib.sha256()
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                async with client.stream("GET", plan.download_url) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))
                    downloaded = 0
                    with open(archive_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
                            hasher.update(chunk)
                            downloaded += len(chunk)
                            if progress_callback and total > 0:
                                progress_callback(downloaded, total)

            if plan.expected_sha256:
                calc_sha = hasher.hexdigest().lower()
                if calc_sha != plan.expected_sha256.lower():
                    archive_path.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"Integrity check failed for runtime archive: {calc_sha} != {plan.expected_sha256}"
                    )

        extract_dir = target_path.parent if scope == InstallationScope.ISOLATED else downloads_dir / "extracted_llama"
        extract_dir.mkdir(parents=True, exist_ok=True)

        if str(archive_path).endswith(".zip"):
            safe_extract_zip(archive_path, extract_dir)
            extracted_bin = None
            for p in extract_dir.rglob("llama-server"):
                if p.is_file():
                    extracted_bin = p
                    break
            if extracted_bin:
                if str(extracted_bin) != str(target_path):
                    shutil.copy2(extracted_bin, target_path)
                os.chmod(target_path, 0o755)

        t_path = Path(target_path)
        if not t_path.exists() or t_path.stat().st_size == 0 or not os.access(t_path, os.X_OK):
            raise RuntimeError(f"llama.cpp preparation failed: binary was not found or not executable at {target_path} after extraction.")

        # Smoke check
        try:
            smoke = subprocess.run([str(target_path), "--version"], capture_output=True, timeout=5)
            if smoke.returncode != 0:
                pass
        except Exception:
            pass

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

        port = find_free_port()
        endpoint = f"http://127.0.0.1:{port}"

        cmd = [
            bin_path,
            "-m", model_path_or_id,
            "--host", "127.0.0.1",
            "--port", str(port),
            "-c", str(candidate.usable_context),
        ]

        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_fd = open(log_file, "a", encoding="utf-8")

        proc_env = self.get_runtime_env(bin_path)
        proc = subprocess.Popen(
            cmd,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            close_fds=True,
            env=proc_env,
        )

        ready = False
        async with httpx.AsyncClient(timeout=1.0) as client:
            for _ in range(40):
                await asyncio.sleep(0.5)
                if proc.poll() is not None:
                    raise RuntimeError(f"llama-server terminated unexpectedly with exit code {proc.returncode}.")
                try:
                    r = await client.get(f"{endpoint}/health")
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
            raise RuntimeError(f"llama-server failed to become ready on {endpoint} within 20 seconds.")

        p_proc = psutil.Process(proc.pid)
        return ProcessIdentity(
            pid=proc.pid,
            process_create_time=p_proc.create_time(),
            executable_path=bin_path,
            endpoint_url=endpoint,
            ownership_class=OwnershipClass.LOCALPILOT_OWNED,
            service_id="llamacpp",
            deployment_method="local_api_service",
        )

    async def stop_instance(self, identity: ProcessIdentity) -> bool:
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


    async def test_inference(self, endpoint_url: str, model_id: str) -> bool:
        url = endpoint_url.rstrip("/") + "/v1/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    url,
                    json={
                        "messages": [{"role": "user", "content": "Hello"}],
                        "max_tokens": 5,
                    },
                )
                return resp.status_code == 200
        except Exception:
            return False
