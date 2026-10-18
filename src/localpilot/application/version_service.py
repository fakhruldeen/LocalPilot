"""Application service for versioned model management, aliases, and safety-checked lifecycle."""

from __future__ import annotations
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from localpilot.domain.versioning import (
    AliasHistoryRecord,
    ExecutionProfile,
    LogicalModel,
    ModelAlias,
    ModelVersion,
    VerificationRecord,
)
from localpilot.storage.db import DatabaseManager, get_db

logger = logging.getLogger(__name__)


class ModelVersionService:
    """Manages logical models, immutable versions, execution profiles, and atomic aliases."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or get_db()
        self._active_leases: Dict[str, Dict[str, Any]] = {}
        self._lease_lock = threading.Lock()

    # =========================================================================
    # Logical Models
    # =========================================================================

    def list_logical_models(self) -> List[LogicalModel]:
        """Returns all registered logical models."""
        conn = self.db.get_connection()
        rows = conn.execute("SELECT * FROM logical_models ORDER BY display_name ASC").fetchall()
        col_names = [col[0] for col in conn.execute("SELECT * FROM logical_models LIMIT 0").description]
        result: List[LogicalModel] = []
        for row in rows:
            d = dict(zip(col_names, row))
            result.append(
                LogicalModel(
                    logical_model_id=d["model_id"],
                    display_name=d["display_name"],
                    description=d.get("description"),
                    task_type=d.get("task_type", "general"),
                    family=d.get("task_type", "general"),
                    default_alias=d.get("default_alias"),
                    created_at=d["created_at"],
                    updated_at=d.get("updated_at"),
                )
            )
        return result

    def get_logical_model(self, model_id: str) -> Optional[LogicalModel]:
        """Gets a logical model by ID."""
        conn = self.db.get_connection()
        row = conn.execute("SELECT * FROM logical_models WHERE model_id = ?", (model_id,)).fetchone()
        if not row:
            return None
        col_names = [col[0] for col in conn.execute("SELECT * FROM logical_models LIMIT 0").description]
        d = dict(zip(col_names, row))
        return LogicalModel(
            logical_model_id=d["model_id"],
            display_name=d["display_name"],
            description=d.get("description"),
            task_type=d.get("task_type", "general"),
            family=d.get("task_type", "general"),
            default_alias=d.get("default_alias"),
            created_at=d["created_at"],
            updated_at=d.get("updated_at"),
        )

    def register_logical_model(self, model: LogicalModel) -> LogicalModel:
        """Registers or updates a logical model record."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self.db.get_connection()
        with conn:
            existing = conn.execute("SELECT model_id FROM logical_models WHERE model_id = ?", (model.logical_model_id,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE logical_models SET display_name = ?, description = ?, task_type = ?, default_alias = ?, updated_at = ? "
                    "WHERE model_id = ?",
                    (model.display_name, model.description, model.task_type, model.default_alias, now, model.logical_model_id),
                )
            else:
                conn.execute(
                    "INSERT INTO logical_models (model_id, display_name, description, task_type, default_alias, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (model.logical_model_id, model.display_name, model.description, model.task_type, model.default_alias, model.created_at or now, now),
                )
        return model

    # =========================================================================
    # Model Versions
    # =========================================================================

    def list_versions(self, model_id: Optional[str] = None, include_deleted: bool = False) -> List[ModelVersion]:
        """Lists model versions, optionally filtered by logical model ID."""
        conn = self.db.get_connection()
        query = "SELECT * FROM model_versions WHERE 1=1"
        params: List[Any] = []
        if not include_deleted:
            query += " AND is_deleted = 0"
        if model_id:
            query += " AND logical_model_id = ?"
            params.append(model_id)
        query += " ORDER BY created_at DESC"

        rows = conn.execute(query, tuple(params)).fetchall()
        col_names = [col[0] for col in conn.execute("SELECT * FROM model_versions LIMIT 0").description]
        result: List[ModelVersion] = []
        for row in rows:
            d = dict(zip(col_names, row))
            compat = {}
            if d.get("compatibility_matrix_json"):
                try:
                    compat = json.loads(d["compatibility_matrix_json"])
                except Exception:
                    pass
            result.append(
                ModelVersion(
                    version_id=d["version_id"],
                    logical_model_id=d["logical_model_id"],
                    version_label=d.get("version_tag", "v1"),
                    source=d.get("artifact_uri", "local"),
                    artifact_uri=d.get("artifact_uri"),
                    artifact_digest=d.get("artifact_digest"),
                    artifact_size_bytes=d.get("artifact_size_bytes", 0),
                    quantization=d.get("quantization"),
                    context_length=d.get("context_length", 2048),
                    installed_path=d.get("installed_path"),
                    installation_id=d.get("installation_id"),
                    compatibility_matrix=compat,
                    is_pinned=bool(d.get("is_pinned", 0)),
                    pinned_reason=d.get("pinned_reason"),
                    pin_count=d.get("pin_count", 0),
                    is_deleted=bool(d.get("is_deleted", 0)),
                    deleted_at=d.get("deleted_at"),
                    created_at=d["created_at"],
                )
            )
        return result

    def get_version(self, version_id: str, include_deleted: bool = True) -> Optional[ModelVersion]:
        """Retrieves a model version by its exact ID."""
        conn = self.db.get_connection()
        query = "SELECT * FROM model_versions WHERE version_id = ?"
        if not include_deleted:
            query += " AND is_deleted = 0"
        row = conn.execute(query, (version_id,)).fetchone()
        if not row:
            return None
        col_names = [col[0] for col in conn.execute("SELECT * FROM model_versions LIMIT 0").description]
        d = dict(zip(col_names, row))
        compat = {}
        if d.get("compatibility_matrix_json"):
            try:
                compat = json.loads(d["compatibility_matrix_json"])
            except Exception:
                pass
        return ModelVersion(
            version_id=d["version_id"],
            logical_model_id=d["logical_model_id"],
            version_label=d.get("version_tag", "v1"),
            source=d.get("artifact_uri", "local"),
            artifact_uri=d.get("artifact_uri"),
            artifact_digest=d.get("artifact_digest"),
            artifact_size_bytes=d.get("artifact_size_bytes", 0),
            quantization=d.get("quantization"),
            context_length=d.get("context_length", 2048),
            installed_path=d.get("installed_path"),
            installation_id=d.get("installation_id"),
            compatibility_matrix=compat,
            is_pinned=bool(d.get("is_pinned", 0)),
            pinned_reason=d.get("pinned_reason"),
            pin_count=d.get("pin_count", 0),
            is_deleted=bool(d.get("is_deleted", 0)),
            deleted_at=d.get("deleted_at"),
            created_at=d["created_at"],
        )

    def register_version(self, version: ModelVersion, profile: Optional[ExecutionProfile] = None) -> ModelVersion:
        """Registers a new immutable model version and optional default execution profile."""
        conn = self.db.get_connection()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with conn:
            # Ensure logical model exists
            existing_lm = conn.execute("SELECT model_id FROM logical_models WHERE model_id = ?", (version.logical_model_id,)).fetchone()
            if not existing_lm:
                conn.execute(
                    "INSERT INTO logical_models (model_id, display_name, description, task_type, default_alias, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'general', ?, ?, ?)",
                    (version.logical_model_id, version.logical_model_id, f"Model family {version.logical_model_id}", f"{version.logical_model_id}:latest", now, now),
                )

            compat_json = json.dumps(version.compatibility_matrix) if version.compatibility_matrix else "{}"
            conn.execute(
                """
                INSERT INTO model_versions (
                    version_id, logical_model_id, version_tag, created_at,
                    artifact_uri, artifact_digest, artifact_size_bytes, quantization,
                    context_length, installed_path, installation_id, compatibility_matrix_json,
                    is_pinned, pinned_reason, pin_count, is_deleted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    version.version_id,
                    version.logical_model_id,
                    version.version_label,
                    version.created_at or now,
                    version.artifact_uri or version.source,
                    version.artifact_digest or "unknown",
                    version.artifact_size_bytes,
                    version.quantization,
                    version.context_length,
                    version.installed_path or "",
                    version.installation_id,
                    compat_json,
                    1 if version.is_pinned else 0,
                    version.pinned_reason,
                    version.pin_count,
                ),
            )

            if profile:
                conn.execute(
                    """
                    INSERT INTO execution_profiles (
                        profile_id, version_id, profile_name, backend, managed_service,
                        deployment_method, threads, gpu_layers, context_tokens, temperature, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile.profile_id,
                        version.version_id,
                        profile.profile_name,
                        str(profile.inference_backend),
                        str(profile.managed_service),
                        str(profile.deployment_method),
                        profile.threads,
                        profile.gpu_layers,
                        profile.context_tokens,
                        profile.temperature,
                        profile.created_at or now,
                    ),
                )

        return version

    def pin_version(self, version_id: str, reason: str = "User pinned") -> ModelVersion:
        """Pins a version to protect it from automatic cleanup or pruning."""
        version = self.get_version(version_id)
        if not version:
            raise ValueError(f"Version '{version_id}' does not exist.")
        if version.is_deleted:
            raise ValueError(f"Cannot pin deleted version '{version_id}'.")

        conn = self.db.get_connection()
        new_count = version.pin_count + 1
        with conn:
            conn.execute(
                "UPDATE model_versions SET is_pinned = 1, pinned_reason = ?, pin_count = ? WHERE version_id = ?",
                (reason, new_count, version_id),
            )
        version.is_pinned = True
        version.pinned_reason = reason
        version.pin_count = new_count
        return version

    def unpin_version(self, version_id: str) -> ModelVersion:
        """Unpins a version, decrementing pin count and clearing pin protection if count is 0."""
        version = self.get_version(version_id)
        if not version:
            raise ValueError(f"Version '{version_id}' does not exist.")

        new_count = max(0, version.pin_count - 1)
        is_pinned = 1 if new_count > 0 else 0
        pinned_reason = version.pinned_reason if is_pinned else None

        conn = self.db.get_connection()
        with conn:
            conn.execute(
                "UPDATE model_versions SET is_pinned = ?, pinned_reason = ?, pin_count = ? WHERE version_id = ?",
                (is_pinned, pinned_reason, new_count, version_id),
            )
        version.is_pinned = bool(is_pinned)
        version.pinned_reason = pinned_reason
        version.pin_count = new_count
        return version

    def acquire_execution_lease(self, version_id: str, owner: str = "request") -> str:
        """Acquires an ephemeral in-flight execution lease on a model version.

        This guarantees that concurrent requests prevent version or file deletion
        without mutating or clearing persistent user retention pins.
        """
        version = self.get_version(version_id, include_deleted=False)
        if not version:
            raise ValueError(f"Version '{version_id}' does not exist or is deleted.")
        lease_id = f"lease-{uuid.uuid4().hex}"
        with self._lease_lock:
            self._active_leases[lease_id] = {
                "version_id": version_id,
                "owner": owner,
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        return lease_id

    def release_execution_lease(self, lease_id: str) -> None:
        """Releases an in-flight execution lease."""
        with self._lease_lock:
            self._active_leases.pop(lease_id, None)

    def is_version_in_use(self, version_id: str) -> bool:
        """Checks if a version is currently in use by persistent pin or active execution leases."""
        with self._lease_lock:
            has_lease = any(item["version_id"] == version_id for item in self._active_leases.values())
        if has_lease:
            return True
        ver = self.get_version(version_id, include_deleted=False)
        return bool(ver and ver.is_pinned)

    def list_active_leases(self) -> List[Dict[str, Any]]:
        """Returns a snapshot of all active in-flight execution leases."""
        with self._lease_lock:
            return [{"lease_id": k, **v} for k, v in self._active_leases.items()]

    def get_active_leases_count(self) -> int:
        """Returns the number of current active in-flight execution leases."""
        with self._lease_lock:
            return len(self._active_leases)

    def compare_versions(self, version_a_id: str, version_b_id: str) -> Dict[str, Any]:
        """Compares two model versions side-by-side."""
        ver_a = self.get_version(version_a_id)
        ver_b = self.get_version(version_b_id)
        if not ver_a:
            raise ValueError(f"Version '{version_a_id}' does not exist.")
        if not ver_b:
            raise ValueError(f"Version '{version_b_id}' does not exist.")

        profiles_a = self.list_execution_profiles(ver_a.version_id)
        profiles_b = self.list_execution_profiles(ver_b.version_id)

        return {
            "version_a": {
                "version_id": ver_a.version_id,
                "logical_model_id": ver_a.logical_model_id,
                "version_label": ver_a.version_label,
                "quantization": ver_a.quantization,
                "artifact_size_bytes": ver_a.artifact_size_bytes,
                "context_length": ver_a.context_length,
                "is_pinned": ver_a.is_pinned,
                "created_at": ver_a.created_at,
                "profiles": [p.model_dump() for p in profiles_a],
            },
            "version_b": {
                "version_id": ver_b.version_id,
                "logical_model_id": ver_b.logical_model_id,
                "version_label": ver_b.version_label,
                "quantization": ver_b.quantization,
                "artifact_size_bytes": ver_b.artifact_size_bytes,
                "context_length": ver_b.context_length,
                "is_pinned": ver_b.is_pinned,
                "created_at": ver_b.created_at,
                "profiles": [p.model_dump() for p in profiles_b],
            },
            "differences": {
                "same_logical_model": ver_a.logical_model_id == ver_b.logical_model_id,
                "size_diff_bytes": ver_b.artifact_size_bytes - ver_a.artifact_size_bytes,
                "quantization_changed": ver_a.quantization != ver_b.quantization,
                "context_length_changed": ver_a.context_length != ver_b.context_length,
            },
        }

    # =========================================================================
    # Execution Profiles
    # =========================================================================

    def list_execution_profiles(self, version_id: Optional[str] = None) -> List[ExecutionProfile]:
        """Lists execution profiles for a given version or all profiles."""
        conn = self.db.get_connection()
        query = "SELECT * FROM execution_profiles"
        params: List[Any] = []
        if version_id:
            query += " WHERE version_id = ?"
            params.append(version_id)
        query += " ORDER BY created_at ASC"

        rows = conn.execute(query, tuple(params)).fetchall()
        col_names = [col[0] for col in conn.execute("SELECT * FROM execution_profiles LIMIT 0").description]
        result: List[ExecutionProfile] = []
        for row in rows:
            d = dict(zip(col_names, row))
            result.append(
                ExecutionProfile(
                    profile_id=d["profile_id"],
                    version_id=d["version_id"],
                    profile_name=d["profile_name"],
                    inference_backend=d["backend"],
                    managed_service=d["managed_service"],
                    deployment_method=d["deployment_method"],
                    threads=d.get("threads"),
                    gpu_layers=d.get("gpu_layers"),
                    context_tokens=d.get("context_tokens", 2048),
                    temperature=d.get("temperature", 0.7),
                    created_at=d["created_at"],
                )
            )
        return result

    def get_execution_profile(self, profile_id: str) -> Optional[ExecutionProfile]:
        """Gets an execution profile by ID."""
        conn = self.db.get_connection()
        row = conn.execute("SELECT * FROM execution_profiles WHERE profile_id = ?", (profile_id,)).fetchone()
        if not row:
            return None
        col_names = [col[0] for col in conn.execute("SELECT * FROM execution_profiles LIMIT 0").description]
        d = dict(zip(col_names, row))
        return ExecutionProfile(
            profile_id=d["profile_id"],
            version_id=d["version_id"],
            profile_name=d["profile_name"],
            inference_backend=d["backend"],
            managed_service=d["managed_service"],
            deployment_method=d["deployment_method"],
            threads=d.get("threads"),
            gpu_layers=d.get("gpu_layers"),
            context_tokens=d.get("context_tokens", 2048),
            temperature=d.get("temperature", 0.7),
            created_at=d["created_at"],
        )

    def create_execution_profile(self, profile: ExecutionProfile) -> ExecutionProfile:
        """Registers a new execution profile."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO execution_profiles (
                    profile_id, version_id, profile_name, backend, managed_service,
                    deployment_method, threads, gpu_layers, context_tokens, temperature, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.profile_id,
                    profile.version_id,
                    profile.profile_name,
                    str(profile.inference_backend),
                    str(profile.managed_service),
                    str(profile.deployment_method),
                    profile.threads,
                    profile.gpu_layers,
                    profile.context_tokens,
                    profile.temperature,
                    profile.created_at or now,
                ),
            )
        return profile

    # =========================================================================
    # Model Aliases & Rollback
    # =========================================================================

    def list_aliases(self) -> List[ModelAlias]:
        """Lists all registered model aliases."""
        conn = self.db.get_connection()
        rows = conn.execute("SELECT * FROM model_aliases ORDER BY alias_name ASC").fetchall()
        col_names = [col[0] for col in conn.execute("SELECT * FROM model_aliases LIMIT 0").description]
        result: List[ModelAlias] = []
        for row in rows:
            d = dict(zip(col_names, row))
            result.append(
                ModelAlias(
                    alias_name=d["alias_name"],
                    version_id=d["target_version_id"],
                    logical_model_id=d.get("logical_model_id"),
                    is_system_alias=bool(d.get("is_system_alias", 0)),
                    created_at=d["created_at"],
                    updated_at=d["updated_at"],
                )
            )
        return result

    def get_alias(self, alias_name: str) -> Optional[ModelAlias]:
        """Gets a model alias by name."""
        conn = self.db.get_connection()
        row = conn.execute("SELECT * FROM model_aliases WHERE alias_name = ?", (alias_name,)).fetchone()
        if not row:
            return None
        col_names = [col[0] for col in conn.execute("SELECT * FROM model_aliases LIMIT 0").description]
        d = dict(zip(col_names, row))
        return ModelAlias(
            alias_name=d["alias_name"],
            version_id=d["target_version_id"],
            logical_model_id=d.get("logical_model_id"),
            is_system_alias=bool(d.get("is_system_alias", 0)),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )

    def resolve_alias(self, alias_or_version_id: str) -> Optional[ModelVersion]:
        """Resolves an alias name or direct version ID to an installed ModelVersion."""
        alias = self.get_alias(alias_or_version_id)
        if alias:
            return self.get_version(alias.version_id, include_deleted=False)

        # Check if caller passed exact version ID directly
        direct_ver = self.get_version(alias_or_version_id, include_deleted=False)
        if direct_ver:
            return direct_ver

        return None

    def activate_alias(
        self,
        alias_name: str,
        version_id: str,
        profile_id: Optional[str] = None,
        reason: str = "Manual activation",
    ) -> ModelAlias:
        """Atomically points an alias to a target version and profile, recording history."""
        target_version = self.get_version(version_id, include_deleted=False)
        if not target_version:
            conn = self.db.get_connection()
            # Try by installation_id or logical_model_id
            row = conn.execute(
                "SELECT version_id FROM model_versions WHERE (installation_id = ? OR logical_model_id = ?) AND is_deleted = 0 ORDER BY created_at DESC LIMIT 1",
                (version_id, version_id),
            ).fetchone()
            if row:
                target_version = self.get_version(row["version_id"], include_deleted=False)
                if target_version:
                    version_id = target_version.version_id

            if not target_version:
                try:
                    from localpilot.sources.catalog_service import CatalogService
                    cand = CatalogService().get_candidate(version_id)
                    if cand:
                        row = conn.execute(
                            "SELECT version_id FROM model_versions WHERE (logical_model_id = ? OR installation_id = ?) AND is_deleted = 0 ORDER BY created_at DESC LIMIT 1",
                            (cand.model_id, cand.candidate_id),
                        ).fetchone()
                        if row:
                            target_version = self.get_version(row["version_id"], include_deleted=False)
                            if target_version:
                                version_id = target_version.version_id
                except Exception:
                    pass

        if not target_version:
            raise ValueError(f"Target version or installed model '{version_id}' does not exist or is deleted.")

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self.db.get_connection()
        with conn:
            existing_alias = conn.execute("SELECT * FROM model_aliases WHERE alias_name = ?", (alias_name,)).fetchone()
            prev_ver_id = None
            if existing_alias:
                col_names = [col[0] for col in conn.execute("SELECT * FROM model_aliases LIMIT 0").description]
                existing_dict = dict(zip(col_names, existing_alias))
                prev_ver_id = existing_dict.get("target_version_id")

                conn.execute(
                    "UPDATE model_aliases SET target_version_id = ?, logical_model_id = ?, updated_at = ? WHERE alias_name = ?",
                    (version_id, target_version.logical_model_id, now, alias_name),
                )
            else:
                conn.execute(
                    "INSERT INTO model_aliases (alias_name, target_version_id, logical_model_id, is_system_alias, created_at, updated_at) "
                    "VALUES (?, ?, ?, 0, ?, ?)",
                    (alias_name, version_id, target_version.logical_model_id, now, now),
                )

            # Record history entry
            history_id = f"hist-{uuid.uuid4().hex[:8]}"
            op_type = "activate" if prev_ver_id is None else "switch"
            conn.execute(
                """
                INSERT INTO alias_history (
                    history_id, alias_name, previous_version_id, new_version_id, changed_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (history_id, alias_name, prev_ver_id, version_id, now, f"[{op_type}] {reason}"),
            )

        return ModelAlias(
            alias_name=alias_name,
            version_id=version_id,
            logical_model_id=target_version.logical_model_id,
            profile_id=profile_id,
            is_system_alias=False,
            created_at=now,
            updated_at=now,
        )

    def rollback_alias(self, alias_name: str) -> Optional[ModelAlias]:
        """Rolls back an alias to its immediately preceding valid version."""
        alias = self.get_alias(alias_name)
        if not alias:
            raise ValueError(f"Alias '{alias_name}' does not exist.")

        history = self.get_alias_history(alias_name)
        # Look backwards for a history record that had a previous_version_id
        target_rollback_ver: Optional[str] = None
        for record in reversed(history):
            if record.previous_version_id and record.previous_version_id != alias.version_id:
                # Check that target version is not deleted
                cand = self.get_version(record.previous_version_id, include_deleted=False)
                if cand:
                    target_rollback_ver = cand.version_id
                    break

        if not target_rollback_ver:
            raise ValueError(f"No prior valid version found to rollback alias '{alias_name}'.")

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        target_ver = self.get_version(target_rollback_ver)
        assert target_ver is not None

        conn = self.db.get_connection()
        with conn:
            conn.execute(
                "UPDATE model_aliases SET target_version_id = ?, logical_model_id = ?, updated_at = ? WHERE alias_name = ?",
                (target_rollback_ver, target_ver.logical_model_id, now, alias_name),
            )
            history_id = f"hist-{uuid.uuid4().hex[:8]}"
            conn.execute(
                """
                INSERT INTO alias_history (
                    history_id, alias_name, previous_version_id, new_version_id, changed_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (history_id, alias_name, alias.version_id, target_rollback_ver, now, "[rollback] Rollback to previous version"),
            )

        return ModelAlias(
            alias_name=alias_name,
            version_id=target_rollback_ver,
            logical_model_id=target_ver.logical_model_id,
            updated_at=now,
        )

    def get_alias_history(self, alias_name: Optional[str] = None) -> List[AliasHistoryRecord]:
        """Retrieves history of alias redirects and activations."""
        conn = self.db.get_connection()
        query = "SELECT * FROM alias_history"
        params: List[Any] = []
        if alias_name:
            query += " WHERE alias_name = ?"
            params.append(alias_name)
        query += " ORDER BY changed_at ASC"

        rows = conn.execute(query, tuple(params)).fetchall()
        col_names = [col[0] for col in conn.execute("SELECT * FROM alias_history LIMIT 0").description]
        result: List[AliasHistoryRecord] = []
        for row in rows:
            d = dict(zip(col_names, row))
            result.append(
                AliasHistoryRecord(
                    history_id=d["history_id"],
                    alias_name=d["alias_name"],
                    previous_version_id=d.get("previous_version_id"),
                    new_version_id=d["new_version_id"],
                    operation="switch",
                    reason=d.get("reason"),
                    changed_at=d["changed_at"],
                )
            )
        return result

    # =========================================================================
    # Safe Model Removal & Shared Artifact Protection
    # =========================================================================

    def safe_remove_version(self, version_id: str, force: bool = False) -> Dict[str, Any]:
        """Safely removes a version, protecting pinned versions and reference-counted physical files."""
        version = self.get_version(version_id, include_deleted=True)
        if not version:
            raise ValueError(f"Version '{version_id}' does not exist.")
        if version.is_deleted:
            return {"deleted_from_db": True, "file_deleted": False, "message": "Already marked deleted."}

        # 1. Enforce Execution Lease Check (in-flight requests)
        with self._lease_lock:
            active_leases_for_ver = [lid for lid, item in self._active_leases.items() if item["version_id"] == version_id]
        if active_leases_for_ver and not force:
            raise ValueError(
                f"Version '{version_id}' is currently in active execution use ({len(active_leases_for_ver)} active lease(s)) "
                f"and cannot be removed."
            )

        # 2. Enforce Pin Protection
        if version.is_pinned and not force:
            raise ValueError(
                f"Version '{version_id}' is pinned ({version.pinned_reason or 'user protected'}) "
                f"and cannot be removed without unpinning or using force."
            )

        # 3. Enforce Active Alias Check
        conn = self.db.get_connection()
        aliases = conn.execute("SELECT alias_name FROM model_aliases WHERE target_version_id = ?", (version_id,)).fetchall()
        if aliases and not force:
            active_names = ", ".join([a[0] for a in aliases])
            raise ValueError(
                f"Cannot remove version '{version_id}' because it is currently active under alias(es): {active_names}. "
                f"Reassign aliases before removing."
            )

        # 3. Reference-counted Shared Artifact Safety
        file_path_str = version.installed_path
        digest_str = version.artifact_digest

        other_sharers = 0
        if file_path_str or digest_str:
            query = "SELECT COUNT(*) FROM model_versions WHERE version_id != ? AND is_deleted = 0 AND (installed_path = ? OR (artifact_digest = ? AND artifact_digest != 'unknown'))"
            other_sharers = conn.execute(query, (version_id, file_path_str or "", digest_str or "")).fetchone()[0]

        file_deleted = False
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with conn:
            # Mark version as deleted in DB
            conn.execute("UPDATE model_versions SET is_deleted = 1, deleted_at = ? WHERE version_id = ?", (now, version_id))

            # If aliases pointed to this version and force was used, clean up those aliases or mark unassigned
            if aliases and force:
                conn.execute("DELETE FROM model_aliases WHERE target_version_id = ?", (version_id,))

        if other_sharers == 0 and file_path_str:
            p = Path(file_path_str)
            if p.exists() and p.is_file():
                try:
                    p.unlink()
                    file_deleted = True
                except Exception as exc:
                    logger.warning(f"Could not delete physical model file '{file_path_str}': {exc}")

        return {
            "version_id": version_id,
            "deleted_from_db": True,
            "file_deleted": file_deleted,
            "shared_by_other_versions": other_sharers > 0,
            "reason": "Shared physical artifact preserved" if (other_sharers > 0) else "Removed successfully",
        }

    remove_version = safe_remove_version

    # =========================================================================
    # Verification Records
    # =========================================================================

    def record_verification(self, record: VerificationRecord) -> VerificationRecord:
        """Stores evidence of local host verification."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO verification_records (
                    verification_id, version_id, verified_at, passed, benchmark_tps,
                    first_token_latency_ms, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.verification_id,
                    record.version_id,
                    record.verified_at or now,
                    1 if record.passed else 0,
                    record.benchmark_tps,
                    record.first_token_latency_ms,
                    record.error_message,
                ),
            )
        return record

    def get_verifications(self, version_id: str) -> List[VerificationRecord]:
        """Lists verification records for a model version."""
        conn = self.db.get_connection()
        rows = conn.execute(
            "SELECT * FROM verification_records WHERE version_id = ? ORDER BY verified_at DESC",
            (version_id,),
        ).fetchall()
        col_names = [col[0] for col in conn.execute("SELECT * FROM verification_records LIMIT 0").description]
        result: List[VerificationRecord] = []
        for row in rows:
            d = dict(zip(col_names, row))
            result.append(
                VerificationRecord(
                    verification_id=d["verification_id"],
                    version_id=d["version_id"],
                    verified_at=d["verified_at"],
                    passed=bool(d["passed"]),
                    benchmark_tps=d.get("benchmark_tps"),
                    first_token_latency_ms=d.get("first_token_latency_ms"),
                    error_message=d.get("error_message"),
                )
            )
        return result
