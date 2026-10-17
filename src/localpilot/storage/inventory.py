"""Inventory tracking, storage categories, and safe removal policies (Section 18)."""

from __future__ import annotations
import asyncio
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from enum import Enum
from localpilot.domain.ownership import OwnershipClass
from localpilot.observability.events import ObservabilityHub, get_hub
from localpilot.security import validate_path_within_root
from localpilot.storage.db import DatabaseManager, get_db, get_default_data_dir


class UpdateType(str, Enum):
    """The four distinct update types (Section 11)."""
    APPLICATION = "application_update"
    MODEL_REVISION = "model_revision_update"
    MANAGED_SERVICE = "managed_service_update"
    BACKEND = "backend_update"


class StorageCategorySummary(BaseModel):
    model_files_bytes: int = 0
    runtime_installations_bytes: int = 0
    incomplete_downloads_bytes: int = 0
    temporary_files_bytes: int = 0
    retained_updates_bytes: int = 0
    total_managed_bytes: int = 0


class StorageManager:
    """Manages physical storage, downloads, and safe reference-counted removal."""

    def __init__(self, db: Optional[DatabaseManager] = None, data_dir: Optional[Path] = None) -> None:
        self.db = db or get_db()
        self.data_dir = data_dir or (self.db.db_path.parent if hasattr(self.db, "db_path") and self.db.db_path else get_default_data_dir())
        self.models_dir = self.data_dir / "models"
        self.runtimes_dir = self.data_dir / "runtimes"
        self.downloads_dir = self.data_dir / "downloads"
        self.tmp_dir = self.data_dir / "tmp"

        for d in (self.models_dir, self.runtimes_dir, self.downloads_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)

    def _get_dir_size(self, path: Path) -> int:
        total = 0
        if not path.exists():
            return 0
        for entry in os.scandir(path):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
            elif entry.is_dir(follow_symlinks=False):
                total += self._get_dir_size(Path(entry.path))
        return total

    def inspect_storage_categories(self) -> StorageCategorySummary:
        model_sz = self._get_dir_size(self.models_dir)
        runtime_sz = self._get_dir_size(self.runtimes_dir)
        download_sz = self._get_dir_size(self.downloads_dir)
        tmp_sz = self._get_dir_size(self.tmp_dir)

        return StorageCategorySummary(
            model_files_bytes=model_sz,
            runtime_installations_bytes=runtime_sz,
            incomplete_downloads_bytes=download_sz,
            temporary_files_bytes=tmp_sz,
            total_managed_bytes=model_sz + runtime_sz + download_sz + tmp_sz,
        )

    def register_artifact(
        self,
        artifact_id: str,
        physical_path: Path | str,
        bytes_size: int,
        sha256: Optional[str] = None,
        is_managed: bool = True,
        install_scope: str = "isolated",
        management_mode: str = "directly_managed",
        deletion_method: str = "delete_files",
    ) -> str:
        path_str = str(Path(physical_path).resolve())
        conn = self.db.get_connection()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with conn:
            # Check if artifact already registered (D08: return canonical artifact_id)
            row = conn.execute("SELECT artifact_id, ref_count FROM storage_artifacts WHERE physical_path = ?", (path_str,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE storage_artifacts SET ref_count = ref_count + 1 WHERE physical_path = ?",
                    (path_str,),
                )
                return str(row["artifact_id"])
            else:
                conn.execute(
                    """
                    INSERT INTO storage_artifacts (
                        artifact_id, physical_path, bytes, sha256, is_managed, install_scope,
                        management_mode, deletion_method, ref_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        artifact_id,
                        path_str,
                        bytes_size,
                        sha256,
                        1 if is_managed else 0,
                        install_scope,
                        management_mode,
                        deletion_method,
                        now,
                    ),
                )
                return artifact_id

    async def remove_installation_async(
        self,
        installation_id: str,
        delete_files: bool = True,
        hub: Optional[ObservabilityHub] = None,
        ollama_adapter: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Async implementation of safe model and artifact removal."""
        conn = self.db.get_connection()
        inst = conn.execute("SELECT * FROM installations WHERE installation_id = ?", (installation_id,)).fetchone()
        if not inst:
            return {"success": False, "error": f"Installation {installation_id} not found."}

        # 1. Prevent removal of currently running models (D09)
        running = conn.execute(
            "SELECT * FROM running_instances WHERE installation_id = ?",
            (installation_id,),
        ).fetchall()
        if running:
            return {
                "success": False,
                "error": f"Cannot remove installation {installation_id}: model is currently running. Stop the instance first.",
            }

        # 2. Prevent removal during active conflicting jobs
        active_jobs = conn.execute(
            "SELECT * FROM jobs WHERE target_id = ? AND status IN ('pending', 'running')",
            (installation_id,),
        ).fetchall()
        if active_jobs:
            return {
                "success": False,
                "error": f"Cannot remove installation {installation_id}: active workflow job is in progress.",
            }

        # D10: Save mandatory audit checkpoint before state changes
        active_hub = hub or ObservabilityHub(db=self.db)
        active_hub.record_audit_checkpoint(
            decision_type="model_removal",
            target_id=installation_id,
            payload={"installation_id": installation_id, "delete_files": delete_files, "model_id": inst["model_id"]},
            outcome="approved",
            is_critical=True,
        )

        artifact_ids = json.loads(inst["artifact_ids_json"]) if inst["artifact_ids_json"] else []
        files_deleted = []
        files_preserved = []
        bytes_freed = 0
        service_delete_failed = False
        service_delete_err = ""

        # Plan removal actions per artifact
        for art_id in artifact_ids:
            art = conn.execute("SELECT * FROM storage_artifacts WHERE artifact_id = ?", (art_id,)).fetchone()
            if not art:
                continue

            new_ref = max(0, art["ref_count"] - 1)
            p = Path(art["physical_path"])
            is_owned = inst["ownership_class"] == OwnershipClass.LOCALPILOT_OWNED.value
            mgmt_mode = art["management_mode"]
            del_method = art["deletion_method"]
            is_imported = mgmt_mode == "imported_reference" or not art["is_managed"]

            if delete_files and new_ref == 0 and is_owned and not is_imported:
                if mgmt_mode == "runtime_managed" or del_method == "service_api_delete":
                    # Service-managed model (e.g. Ollama)
                    adapter = ollama_adapter
                    if not adapter:
                        from localpilot.runtimes.ollama import OllamaRuntimeAdapter
                        adapter = OllamaRuntimeAdapter(data_dir=self.data_dir)

                    endpoint = "http://127.0.0.1:11434"
                    model_target = inst["model_id"]
                    if p.exists() and p.is_file():
                        try:
                            meta = json.loads(p.read_text(encoding="utf-8"))
                            endpoint = meta.get("endpoint", endpoint)
                            model_target = meta.get("model_id", model_target)
                        except Exception:
                            pass

                    try:
                        deleted_service = await adapter.delete_model(endpoint, model_target)
                    except Exception as e:
                        deleted_service = False
                        service_delete_err = str(e)

                    if not deleted_service:
                        service_delete_failed = True
                        if not service_delete_err:
                            service_delete_err = f"Service at {endpoint} returned failure when deleting {model_target}"
                        files_preserved.append(f"service:{endpoint}/{model_target}")
                        break

                    if p.exists() and p.is_file():
                        p.unlink(missing_ok=True)
                    bytes_freed += art["bytes"]
                    files_deleted.append(f"service:{endpoint}/{model_target}")

                elif mgmt_mode == "directly_managed" or del_method == "delete_files":
                    if p.exists() and p.is_file():
                        try:
                            validate_path_within_root(p, self.data_dir)
                            sz = p.stat().st_size
                            p.unlink()
                            bytes_freed += sz
                            files_deleted.append(str(p))
                        except Exception:
                            files_preserved.append(str(p))
                    else:
                        files_preserved.append(str(p))
                else:
                    files_preserved.append(str(p))
            else:
                files_preserved.append(str(p))

            with conn:
                if new_ref > 0:
                    conn.execute("UPDATE storage_artifacts SET ref_count = ? WHERE artifact_id = ?", (new_ref, art_id))
                else:
                    conn.execute("DELETE FROM storage_artifacts WHERE artifact_id = ?", (art_id,))

        if service_delete_failed:
            return {
                "success": False,
                "error": f"Failed deleting model from service: {service_delete_err}. Model record preserved.",
                "bytes_freed": bytes_freed,
                "files_deleted": files_deleted,
                "files_preserved": files_preserved,
            }

        with conn:
            conn.execute("DELETE FROM installations WHERE installation_id = ?", (installation_id,))

        active_hub.record_activity(
            category="storage",
            title="Model Removed",
            summary=f"Safely removed model installation {inst['name']}. Freed: {bytes_freed / (1024*1024):.1f} MB.",
            details={"installation_id": installation_id, "bytes_freed": bytes_freed},
        )

        return {
            "success": True,
            "installation_id": installation_id,
            "bytes_freed": bytes_freed,
            "files_deleted": files_deleted,
            "files_preserved": files_preserved,
        }

    def remove_installation(
        self,
        installation_id: str,
        delete_files: bool = True,
        hub: Optional[ObservabilityHub] = None,
        ollama_adapter: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Synchronous wrapper for remove_installation_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    asyncio.run,
                    self.remove_installation_async(
                        installation_id,
                        delete_files=delete_files,
                        hub=hub,
                        ollama_adapter=ollama_adapter,
                    ),
                ).result()
        else:
            return asyncio.run(
                self.remove_installation_async(
                    installation_id,
                    delete_files=delete_files,
                    hub=hub,
                    ollama_adapter=ollama_adapter,
                )
            )

    safe_remove_installation = remove_installation
