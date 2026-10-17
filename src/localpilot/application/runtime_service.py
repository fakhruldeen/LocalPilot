"""Runtime instance reconciliation and lifecycle management service."""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx

from localpilot.domain.models import NormalizedCandidate, StopResult, StopStatus
from localpilot.domain.ownership import OwnershipClass, OwnershipVerifier, ProcessIdentity
from localpilot.hardware.inspector import SystemInspector
from localpilot.platforms import get_platform_adapter
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
        """Starts an installed model and returns the running instance metadata."""
        conn = self.db.get_connection()
        inst_row = conn.execute(
            "SELECT * FROM installations WHERE installation_id = ?",
            (installation_id,),
        ).fetchone()

        if not inst_row:
            raise ValueError(f"Installation '{installation_id}' not found.")

        model_id = inst_row["model_id"]
        runtime_name = inst_row["runtime_name"]

        # Prevent duplicate instances if already running and verified
        existing_run = conn.execute(
            "SELECT * FROM running_instances WHERE installation_id = ? OR model_id = ?",
            (installation_id, model_id),
        ).fetchone()
        if existing_run:
            existing_data = dict(existing_run)
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

        # Resolve artifact path and size
        art_ids = json.loads(inst_row["artifact_ids_json"]) if inst_row["artifact_ids_json"] else []
        artifact_path = ""
        artifact_bytes = 0
        if art_ids:
            art_row = conn.execute(
                "SELECT physical_path, bytes FROM storage_artifacts WHERE artifact_id = ?",
                (art_ids[0],),
            ).fetchone()
            if art_row:
                artifact_path = art_row["physical_path"]
                artifact_bytes = art_row["bytes"]

        # If no physical artifact, check model_id
        target = artifact_path or model_id

        # Memory verification against actual system capabilities
        sys_report = self.inspector.inspect()
        avail_ram = sys_report.hardware.ram_available_bytes

        # Candidate reconstruction
        c_row = conn.execute(
            "SELECT payload_json FROM catalog_models WHERE model_id = ? LIMIT 1",
            (model_id,),
        ).fetchone()
        if c_row:
            candidate = NormalizedCandidate.model_validate_json(c_row["payload_json"])
        else:
            cand_bytes = artifact_bytes or (1024 * 1024 * 1024)
            candidate = NormalizedCandidate(
                candidate_id=f"inst-{installation_id}",
                model_id=model_id,
                name=inst_row["name"],
                source="local",
                artifact_id=Path(artifact_path).name if artifact_path else f"{model_id}.gguf",
                architecture="llama",
                family="Local Model",
                runtime=runtime_name,
                format="gguf" if runtime_name != "ollama" else "ollama_blob",
                total_bytes=cand_bytes,
                estimated_ram_bytes=cand_bytes,
                estimated_disk_bytes=cand_bytes,
                metadata_fetched_at=datetime.now(timezone.utc).isoformat(),
            )

        if candidate.estimated_ram_bytes > avail_ram:
            raise RuntimeError(
                f"Insufficient memory to start {candidate.name}: requires at least "
                f"{candidate.estimated_ram_bytes / (1024**3):.1f} GB RAM, but only "
                f"{avail_ram / (1024**3):.1f} GB is currently available."
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
        )

        # Verify endpoint readiness probe
        if proc_id.endpoint_url:
            ready = False
            async with httpx.AsyncClient(timeout=2.0) as client:
                probe_url = proc_id.endpoint_url.rstrip("/") + ("/api/tags" if runtime_name == "ollama" else "/v1/models")
                for _ in range(10):
                    try:
                        r = await client.get(probe_url)
                        if r.status_code == 200:
                            ready = True
                            break
                    except Exception:
                        await asyncio.sleep(0.3)
            if not ready:
                logger.warning(f"Instance started at {proc_id.endpoint_url} but probe did not confirm 200 OK within timeout.")

        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO running_instances (
                    instance_id, model_id, installation_id, pid, process_create_time,
                    executable_path, endpoint_url, runtime_version, launch_config_json,
                    log_path, ownership_class, observed_execution_mode, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance_id,
                    model_id,
                    installation_id,
                    proc_id.pid or 0,
                    proc_id.process_create_time or 0.0,
                    proc_id.executable_path or "",
                    proc_id.endpoint_url or "",
                    "1.0",
                    json.dumps({"plan_id": plan_id}),
                    str(log_file),
                    proc_id.ownership_class.value,
                    "auto",
                    now_iso,
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
