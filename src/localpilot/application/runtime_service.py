"""Runtime instance reconciliation and lifecycle management service."""

from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
import psutil

from localpilot.domain.capabilities import InferenceBackendId, ManagedServiceId, DeploymentMethod
from localpilot.domain.execution_plan import ExecutionPlan, ExecutionSpecification
from localpilot.domain.models import ExecutionMode, NormalizedCandidate, StopResult, StopStatus
from localpilot.domain.ownership import OwnershipClass, OwnershipVerifier, ProcessIdentity
from localpilot.hardware.inspector import SystemInspector
from localpilot.platforms import get_platform_adapter
from localpilot.platforms.base import MemoryAccountingModel
from localpilot.runtimes.base import BaseRuntimeAdapter
from localpilot.runtimes.llamacpp import LlamaCppRuntimeAdapter
from localpilot.runtimes.ollama import OllamaRuntimeAdapter
from localpilot.storage.db import DatabaseManager, get_db

logger = logging.getLogger(__name__)


class RuntimeLifecycleService:
    """Manages running model instances and reconciles state with live OS processes."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or get_db()
        self.platform = get_platform_adapter()
        self.inspector = SystemInspector()
        self.ollama_adapter = OllamaRuntimeAdapter()
        self.llamacpp_adapter = LlamaCppRuntimeAdapter()

    def get_adapter(self, runtime_name: str) -> BaseRuntimeAdapter:
        if runtime_name == "ollama":
            return self.ollama_adapter
        if runtime_name == "llamacpp":
            return self.llamacpp_adapter
        raise ValueError(f"Unsupported runtime adapter: '{runtime_name}'")

    def reconcile_running_instances(self) -> List[Dict[str, Any]]:
        """Reconciles database running_instances against real OS processes and endpoint health."""
        conn = self.db.get_connection()
        rows = conn.execute("SELECT * FROM running_instances").fetchall()
        active_instances: List[Dict[str, Any]] = []

        dead_instance_ids: List[str] = []

        for row in rows:
            data = dict(row)
            pid = data.get("pid")
            create_time = data.get("process_create_time")
            exe_path = data.get("executable_path")
            endpoint = data.get("endpoint_url")
            ownership_str = data.get("ownership_class", "unknown")

            try:
                ownership = OwnershipClass(ownership_str)
            except ValueError:
                ownership = OwnershipClass.UNKNOWN

            identity = ProcessIdentity(
                pid=pid if pid and pid > 0 else None,
                process_create_time=create_time,
                executable_path=exe_path,
                endpoint_url=endpoint,
                ownership_class=ownership,
            )

            is_alive = OwnershipVerifier.verify_process(identity)

            # Check endpoint responsiveness without dropping instances on transient timeouts
            if is_alive:
                data["health_status"] = "healthy"
                if endpoint:
                    try:
                        with httpx.Client(timeout=1.5) as client:
                            probe = "/api/tags" if "ollama" in data.get("executable_path", "").lower() or data.get("managed_service") == "ollama" else "/v1/models"
                            resp = client.get(endpoint.rstrip("/") + probe)
                            if resp.status_code >= 500:
                                data["health_status"] = "degraded"
                    except Exception:
                        data["health_status"] = "unresponsive"

                active_instances.append(data)
            else:
                dead_instance_ids.append(data["instance_id"])

        if dead_instance_ids:
            with conn:
                for inst_id in dead_instance_ids:
                    conn.execute("DELETE FROM running_instances WHERE instance_id = ?", (inst_id,))

        return active_instances

    async def start_installed_model(
        self,
        installation_id: str,
        plan_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Starts an installed model adhering strictly to its canonical ExecutionSpecification."""
        conn = self.db.get_connection()
        inst_row = conn.execute(
            "SELECT * FROM installations WHERE installation_id = ?",
            (installation_id,),
        ).fetchone()

        if not inst_row:
            raise ValueError(f"Installation '{installation_id}' not found.")

        model_id = inst_row["model_id"]
        runtime_name = inst_row["runtime_name"]

        # Resolve artifact path and size
        art_ids = json.loads(inst_row["artifact_ids_json"]) if inst_row["artifact_ids_json"] else []
        artifact_path = ""
        artifact_bytes = 0
        artifact_sha256 = None
        if art_ids:
            art_row = conn.execute(
                "SELECT physical_path, bytes, sha256 FROM storage_artifacts WHERE artifact_id = ?",
                (art_ids[0],),
            ).fetchone()
            if art_row:
                artifact_path = art_row["physical_path"]
                artifact_bytes = art_row["bytes"]
                artifact_sha256 = art_row["sha256"]

        # 1. Load canonical ExecutionSpecification from installation record
        spec: Optional[ExecutionSpecification] = None
        raw_spec = inst_row["execution_spec_json"] if "execution_spec_json" in inst_row.keys() else None
        if raw_spec and raw_spec.strip() and raw_spec != "{}":
            try:
                spec = ExecutionSpecification.model_validate_json(raw_spec)
            except Exception:
                spec = None

        if not spec and inst_row["execution_plan_json"] and inst_row["execution_plan_json"] != "{}":
            try:
                plan = ExecutionPlan.model_validate_json(inst_row["execution_plan_json"])
                spec = plan.to_execution_spec(
                    installation_id=installation_id,
                    artifact_path=artifact_path,
                    artifact_sha256=artifact_sha256,
                )
            except Exception:
                spec = None

        if not spec:
            spec = ExecutionSpecification(
                installation_id=installation_id,
                plan_id=f"plan-legacy-{installation_id}",
                model_id=model_id,
                canonical_artifact_id=art_ids[0] if art_ids else f"art-{model_id}",
                artifact_path=artifact_path,
                artifact_sha256=artifact_sha256,
                inference_backend=InferenceBackendId.LLAMACPP if runtime_name == "llamacpp" else InferenceBackendId.UNKNOWN_SERVICE_MANAGED,
                managed_service=ManagedServiceId.OLLAMA if runtime_name == "ollama" else ManagedServiceId.NONE,
                deployment_method=DeploymentMethod.LOCAL_API_SERVICE,
                requested_execution_mode=ExecutionMode.AUTO,
                context_window=2048,
                required_memory_budget_bytes=artifact_bytes or (2 * 1024**3),
                memory_accounting_model=MemoryAccountingModel.SEPARATE,
                is_verified=False,
            )

        # 2. Launch-Time Revalidation
        sys_report = self.inspector.inspect()
        avail_ram = sys_report.hardware.ram_available_bytes

        # Check memory budget
        required_ram = spec.required_memory_budget_bytes
        if required_ram > avail_ram:
            raise RuntimeError(
                f"Insufficient memory to start {inst_row['name']}: requires at least "
                f"{required_ram / (1024**3):.1f} GB RAM, but only "
                f"{avail_ram / (1024**3):.1f} GB is currently available."
            )

        # Revalidate requested GPU mode: no silent GPU to CPU fallback
        if spec.requested_execution_mode in (ExecutionMode.GPU, ExecutionMode.CPU_GPU):
            is_macos = sys_report.hardware.os_name.lower() == "macos"
            is_apple_silicon = is_macos and sys_report.hardware.memory_model == MemoryAccountingModel.UNIFIED
            has_gpu = bool(sys_report.hardware.gpu_devices) or is_apple_silicon
            if not has_gpu:
                raise RuntimeError(
                    f"Explicit GPU execution mode was approved for {inst_row['name']}, but no compatible "
                    "GPU or graphics accelerator was detected on this computer. LocalPilot will not silently fall back to CPU."
                )

        # Revalidate artifact existence on disk
        if spec.artifact_path and not Path(spec.artifact_path).exists():
            raise FileNotFoundError(
                f"Model weight file not found at '{spec.artifact_path}'. Installation needs repair."
            )

        # 3. Duplicate instance & configuration reuse check
        existing_run = conn.execute(
            "SELECT * FROM running_instances WHERE installation_id = ? OR model_id = ?",
            (installation_id, model_id),
        ).fetchone()
        if existing_run:
            existing_data = dict(existing_run)
            can_reuse = True
            existing_spec_raw = existing_data.get("execution_spec_json")
            if existing_spec_raw:
                try:
                    existing_spec = ExecutionSpecification.model_validate_json(existing_spec_raw)
                    if (
                        existing_spec.artifact_path != spec.artifact_path
                        or existing_spec.requested_execution_mode != spec.requested_execution_mode
                        or existing_spec.context_window != spec.context_window
                    ):
                        can_reuse = False
                except Exception:
                    pass

            if can_reuse:
                pid = existing_data.get("pid")
                create_time = existing_data.get("process_create_time")
                identity = ProcessIdentity(
                    pid=pid if pid and pid > 0 else None,
                    process_create_time=create_time,
                    executable_path=existing_data.get("executable_path"),
                    endpoint_url=existing_data.get("endpoint_url"),
                    ownership_class=OwnershipClass.LOCALPILOT_OWNED if pid else OwnershipClass.READONLY_DISCOVERED,
                )
                if OwnershipVerifier.verify_process(identity):
                    return {
                        "instance_id": existing_data["instance_id"],
                        "model_id": existing_data["model_id"],
                        "installation_id": existing_data.get("installation_id") or installation_id,
                        "endpoint_url": existing_data["endpoint_url"],
                        "pid": existing_data["pid"],
                        "started_at": existing_data["started_at"],
                        "already_running": True,
                    }

        adapter = self.get_adapter(runtime_name)
        target = spec.artifact_path or model_id

        # Candidate wrapper for adapter interface
        candidate = NormalizedCandidate(
            candidate_id=f"inst-{installation_id}",
            model_id=model_id,
            name=inst_row["name"],
            source="local",
            artifact_id=spec.canonical_artifact_id,
            architecture="llama",
            family="Local Model",
            runtime=runtime_name,
            format="gguf" if runtime_name != "ollama" else "ollama_blob",
            total_bytes=artifact_bytes or required_ram,
            estimated_ram_bytes=required_ram,
            estimated_disk_bytes=artifact_bytes or required_ram,
            usable_context=spec.context_window,
            metadata_fetched_at=datetime.now(timezone.utc).isoformat(),
        )

        import uuid
        instance_id = f"inst-{uuid.uuid4().hex[:8]}"
        log_dir = self.platform.get_log_dir()
        log_file = log_dir / f"instance-{instance_id}.log"

        proc_id = await adapter.start_instance(
            model_path_or_id=target,
            candidate=candidate,
            instance_id=instance_id,
            log_file=log_file,
            spec=spec,
        )

        # 4. Multi-Stage Readiness Probe
        if proc_id.endpoint_url:
            ready = False
            async with httpx.AsyncClient(timeout=2.0) as client:
                probe_url = proc_id.endpoint_url.rstrip("/") + ("/api/tags" if runtime_name == "ollama" else "/v1/models")
                for _ in range(15):
                    try:
                        r = await client.get(probe_url)
                        if r.status_code == 200:
                            ready = True
                            break
                    except Exception:
                        await asyncio.sleep(0.3)
            if not ready:
                # Cleanup owned process safely on readiness failure
                if proc_id.pid and proc_id.ownership_class == OwnershipClass.LOCALPILOT_OWNED:
                    try:
                        p = psutil.Process(proc_id.pid)
                        p.terminate()
                        try:
                            p.wait(timeout=2)
                        except Exception:
                            p.kill()
                    except Exception:
                        pass
                raise RuntimeError(
                    f"Instance started at {proc_id.endpoint_url} (PID: {proc_id.pid}) but readiness probe "
                    "failed to confirm HTTP 200 OK within timeout. Process terminated safely."
                )

        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        runtime_ver = getattr(proc_id, "runtime_version", None)
        obs_mode = getattr(proc_id, "observed_execution_mode", None) or (
            "gpu" if spec.offload_layers > 0 else "cpu"
        )

        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO running_instances (
                    instance_id, model_id, installation_id, pid, process_create_time,
                    executable_path, endpoint_url, runtime_version, launch_config_json,
                    log_path, ownership_class, observed_execution_mode, started_at,
                    execution_spec_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance_id,
                    model_id,
                    installation_id,
                    proc_id.pid or 0,
                    proc_id.process_create_time or 0.0,
                    proc_id.executable_path or "",
                    proc_id.endpoint_url or "",
                    runtime_ver,
                    json.dumps({"plan_id": spec.plan_id}),
                    str(log_file),
                    proc_id.ownership_class.value,
                    obs_mode,
                    now_iso,
                    spec.model_dump_json(),
                ),
            )

        return {
            "instance_id": instance_id,
            "model_id": model_id,
            "installation_id": installation_id,
            "endpoint_url": proc_id.endpoint_url,
            "pid": proc_id.pid,
            "started_at": now_iso,
            "already_running": False,
        }

    async def stop_running_instance(self, target: str, force: bool = False) -> StopResult:
        """Stops a running instance honestly and clears it from running_instances only if stop succeeds."""
        conn = self.db.get_connection()
        row = conn.execute(
            "SELECT * FROM running_instances WHERE instance_id = ? OR installation_id = ? OR model_id = ?",
            (target, target, target),
        ).fetchone()

        if not row:
            inst_row = conn.execute(
                "SELECT installation_id FROM installations WHERE name = ? OR model_id = ?",
                (target, target),
            ).fetchone()
            if inst_row:
                row = conn.execute(
                    "SELECT * FROM running_instances WHERE installation_id = ?",
                    (inst_row["installation_id"],),
                ).fetchone()

        if not row:
            return StopResult(
                target_instance_id=target,
                request_succeeded=False,
                observed_outcome=StopStatus.ALREADY_STOPPED,
                reason=f"Instance {target} was not found in running instances.",
            )

        data = dict(row)
        instance_id = data.get("instance_id", target)
        model_id = data.get("model_id")
        try:
            ownership = OwnershipClass(data.get("ownership_class", "unknown"))
        except ValueError:
            ownership = OwnershipClass.UNKNOWN

        identity = ProcessIdentity(
            pid=data.get("pid") if data.get("pid") and data.get("pid") > 0 else None,
            process_create_time=data.get("process_create_time"),
            executable_path=data.get("executable_path"),
            endpoint_url=data.get("endpoint_url"),
            ownership_class=ownership,
            service_id="ollama" if "ollama" in (data.get("executable_path") or "").lower() or (data.get("endpoint_url") or "").endswith("11434") else None,
        )

        # Look up runtime adapter
        inst_row = None
        if data.get("installation_id"):
            inst_row = conn.execute(
                "SELECT runtime_name FROM installations WHERE installation_id = ?",
                (data["installation_id"],),
            ).fetchone()

        runtime_name = inst_row["runtime_name"] if inst_row else "llamacpp"
        if "ollama" in data.get("executable_path", "").lower() or (data.get("endpoint_url") or "").endswith("11434"):
            runtime_name = "ollama"

        try:
            adapter = self.get_adapter(runtime_name)
        except Exception as e:
            return StopResult(
                target_instance_id=instance_id,
                model_id=model_id,
                request_succeeded=False,
                observed_outcome=StopStatus.FAILED,
                reason=f"Could not resolve adapter for {runtime_name}: {e}",
            )

        if runtime_name == "ollama":
            stopped = await adapter.stop_instance(identity, model_id=model_id, stop_daemon=force)
        else:
            stopped = await adapter.stop_instance(identity)

        if stopped:
            with conn:
                conn.execute("DELETE FROM running_instances WHERE instance_id = ?", (instance_id,))

            return StopResult(
                target_instance_id=instance_id,
                model_id=model_id,
                request_succeeded=True,
                observed_outcome=StopStatus.STOPPED if runtime_name != "ollama" else StopStatus.UNLOADED_ONLY,
                reason=f"Successfully stopped/unloaded instance {instance_id}.",
                remaining_state="stopped",
            )
        else:
            return StopResult(
                target_instance_id=instance_id,
                model_id=model_id,
                request_succeeded=False,
                observed_outcome=StopStatus.FAILED,
                reason=f"Adapter {runtime_name} reported failure when attempting to stop instance {instance_id}.",
                remaining_state="running",
            )
