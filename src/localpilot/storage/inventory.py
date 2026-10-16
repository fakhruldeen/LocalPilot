"""Inventory tracking, storage categories, and safe removal policies (Section 18)."""

from __future__ import annotations
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from enum import Enum
from localpilot.domain.ownership import OwnershipClass
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

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or get_db()
        self.data_dir = get_default_data_dir()
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
    ) -> None:
        path_str = str(Path(physical_path).resolve())
        conn = self.db.get_connection()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with conn:
            # Check if artifact already registered
            row = conn.execute("SELECT ref_count FROM storage_artifacts WHERE physical_path = ?", (path_str,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE storage_artifacts SET ref_count = ref_count + 1 WHERE physical_path = ?",
                    (path_str,),
                )
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

    def safe_remove_installation(
        self,
        installation_id: str,
        delete_files: bool = True,
    ) -> Dict[str, Any]:
        """Removes an installation record and deletes artifact files ONLY if no other installation references them."""
        conn = self.db.get_connection()
        with conn:
            inst = conn.execute("SELECT * FROM installations WHERE installation_id = ?", (installation_id,)).fetchone()
            if not inst:
                return {"success": False, "error": f"Installation {installation_id} not found."}

            artifact_ids = json.loads(inst["artifact_ids_json"])
            files_deleted = []
            files_preserved = []
            bytes_freed = 0

            if delete_files:
                for art_id in artifact_ids:
                    art = conn.execute("SELECT * FROM storage_artifacts WHERE artifact_id = ?", (art_id,)).fetchone()
                    if not art:
                        continue

                    # Decrement ref_count
                    new_ref = art["ref_count"] - 1
                    if new_ref > 0:
                        conn.execute("UPDATE storage_artifacts SET ref_count = ? WHERE artifact_id = ?", (new_ref, art_id))
                        files_preserved.append(art["physical_path"])
                    else:
                        # No more references - safe to remove file only if localpilot-owned and managed
                        p = Path(art["physical_path"])
                        is_owned = inst["ownership_class"] == OwnershipClass.LOCALPILOT_OWNED.value
                        if p.exists() and art["is_managed"] and is_owned:
                            sz = p.stat().st_size if p.is_file() else self._get_dir_size(p)
                            if p.is_file():
                                p.unlink()
                            else:
                                shutil.rmtree(p)
                            bytes_freed += sz
                            files_deleted.append(str(p))
                        conn.execute("DELETE FROM storage_artifacts WHERE artifact_id = ?", (art_id,))

            conn.execute("DELETE FROM installations WHERE installation_id = ?", (installation_id,))

            return {
                "success": True,
                "installation_id": installation_id,
                "bytes_freed": bytes_freed,
                "files_deleted": files_deleted,
                "files_preserved": files_preserved,
            }
