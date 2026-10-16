"""Runtime instance reconciliation and lifecycle management service."""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx

from localpilot.domain.models import NormalizedCandidate
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
        return self.llamacpp_adapter

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

            # Check endpoint responsiveness
            if is_alive and endpoint:
                try:
                    with httpx.Client(timeout=1.5) as client:
                        resp = client.get(endpoint.rstrip("/") + ("/api/tags" if "ollama" in data.get("executable_path", "").lower() or data.get("managed_service") == "ollama" else "/v1/models"))
                        if resp.status_code >= 500:
                            is_alive = False
                except Exception:
                    # Non-fatal if server is just busy or starting up, but if process is dead, it will have failed already
                    pass

            if is_alive:
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
        adapter = self.get_adapter(runtime_name)

        # Resolve artifact path
        art_ids = json.loads(inst_row["artifact_ids_json"]) if inst_row["artifact_ids_json"] else []
        artifact_path = ""
        if art_ids:
            art_row = conn.execute(
                "SELECT physical_path FROM storage_artifacts WHERE artifact_id = ?",
                (art_ids[0],),
            ).fetchone()
            if art_row:
                artifact_path = art_row["physical_path"]

        # If no physical artifact, check model_id
        target = artifact_path or model_id

        # Candidate reconstruction
        c_row = conn.execute(
            "SELECT payload_json FROM catalog_models WHERE model_id = ? LIMIT 1",
            (model_id,),
        ).fetchone()
        if c_row:
            candidate = NormalizedCandidate.model_validate_json(c_row["payload_json"])
        else:
            candidate = NormalizedCandidate(
                candidate_id=f"inst-{installation_id}",
                model_id=model_id,
                name=inst_row["name"],
                source="local",
                format="gguf" if runtime_name != "ollama" else "ollama_blob",
                total_bytes=1024 * 1024 * 1024,
                estimated_ram_bytes=1024 * 1024 * 1024,
                estimated_disk_bytes=1024 * 1024 * 1024,
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
        )

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
        }

    async def stop_running_instance(self, instance_id: str, force: bool = False) -> bool:
        """Stops a running instance and clears it from running_instances."""
        conn = self.db.get_connection()
        row = conn.execute(
            "SELECT * FROM running_instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()

        if not row:
            return False

        data = dict(row)
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
        )

        # Look up runtime adapter
        inst_row = None
        if data.get("installation_id"):
            inst_row = conn.execute(
                "SELECT runtime_name FROM installations WHERE installation_id = ?",
                (data["installation_id"],),
            ).fetchone()

        runtime_name = inst_row["runtime_name"] if inst_row else "llamacpp"
        if "ollama" in data.get("executable_path", "").lower() or data.get("endpoint_url", "").endswith("11434"):
            runtime_name = "ollama"

        adapter = self.get_adapter(runtime_name)
        stopped = await adapter.stop_instance(identity)

        with conn:
            conn.execute("DELETE FROM running_instances WHERE instance_id = ?", (instance_id,))

        return stopped
