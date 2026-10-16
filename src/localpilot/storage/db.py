"""Authoritative SQLite state storage manager with migrations and thread-safe connections."""

from __future__ import annotations
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional


def get_default_data_dir() -> Path:
    """Returns platform default data directory."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "~\\AppData\\Local")
    elif os.uname().sysname == "Darwin":
        base = "~/Library/Application Support/LocalPilot"
    else:
        base = os.environ.get("XDG_DATA_HOME", "~/.local/share/localpilot")
    p = Path(os.path.expanduser(base)).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_default_log_dir() -> Path:
    """Returns platform default log directory."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "~\\AppData\\Local\\LocalPilot\\logs")
    elif os.uname().sysname == "Darwin":
        base = "~/Library/Logs/LocalPilot"
    else:
        base = os.environ.get("XDG_STATE_HOME", "~/.local/state/localpilot/logs")
    p = Path(os.path.expanduser(base)).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


class DatabaseManager:
    """Manages SQLite database connection, transactions, and schema migrations."""

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        if db_path is None:
            self.db_path = get_default_data_dir() / "state.db"
        else:
            self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    def get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    def _init_schema(self) -> None:
        conn = self.get_connection()
        with conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS catalog_sources (
                source_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                last_refreshed_at TEXT,
                status TEXT NOT NULL,
                details_json TEXT NOT NULL
            )
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS catalog_models (
                candidate_id TEXT PRIMARY KEY,
                model_id TEXT NOT NULL,
                source TEXT NOT NULL,
                revision TEXT,
                payload_json TEXT NOT NULL,
                verified_release_at TEXT,
                repo_created_at TEXT,
                repo_modified_at TEXT,
                artifact_published_at TEXT,
                metadata_fetched_at TEXT NOT NULL
            )
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS storage_artifacts (
                artifact_id TEXT PRIMARY KEY,
                physical_path TEXT NOT NULL UNIQUE,
                bytes INTEGER NOT NULL,
                sha256 TEXT,
                is_managed INTEGER NOT NULL DEFAULT 1,
                install_scope TEXT NOT NULL DEFAULT 'isolated',
                management_mode TEXT NOT NULL DEFAULT 'directly_managed',
                deletion_method TEXT NOT NULL DEFAULT 'delete_files',
                ref_count INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS installations (
                installation_id TEXT PRIMARY KEY,
                model_id TEXT NOT NULL,
                name TEXT NOT NULL,
                runtime_name TEXT NOT NULL,
                artifact_ids_json TEXT NOT NULL,
                readiness_stage TEXT NOT NULL,
                compatibility_status TEXT NOT NULL,
                update_status TEXT NOT NULL,
                ownership_class TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS running_instances (
                instance_id TEXT PRIMARY KEY,
                model_id TEXT NOT NULL,
                installation_id TEXT,
                pid INTEGER NOT NULL,
                process_create_time REAL NOT NULL,
                executable_path TEXT NOT NULL,
                endpoint_url TEXT NOT NULL,
                runtime_version TEXT,
                launch_config_json TEXT NOT NULL,
                log_path TEXT NOT NULL,
                ownership_class TEXT NOT NULL,
                observed_execution_mode TEXT NOT NULL,
                started_at TEXT NOT NULL
            )
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                operation_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                approved_plan_json TEXT NOT NULL,
                stage TEXT NOT NULL,
                checkpoints_json TEXT NOT NULL,
                progress_bytes INTEGER NOT NULL DEFAULT 0,
                total_bytes INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                error_category TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                owned_pids_json TEXT NOT NULL DEFAULT '[]',
                recovery_action TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                activity_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                details_json TEXT NOT NULL,
                operation_id TEXT,
                outcome TEXT NOT NULL
            )
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_decisions (
                decision_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                decision_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                outcome TEXT NOT NULL,
                reason_code TEXT
            )
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL,
                description TEXT NOT NULL
            )
            """)
        self._run_migrations(conn)

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        """Applies non-destructive schema migrations while preserving all existing user data."""
        with conn:
            # Check current applied migrations
            applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}

            if 1 not in applied:
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at, description) VALUES (1, datetime('now'), 'Initial v1 schema')"
                )
                applied.add(1)

            if 2 not in applied:
                # Migration v2: 3-dimension execution model (backend, service, deployment, execution_plans)
                inst_cols = {row[1] for row in conn.execute("PRAGMA table_info(installations)").fetchall()}
                if "inference_backend" not in inst_cols:
                    conn.execute("ALTER TABLE installations ADD COLUMN inference_backend TEXT DEFAULT 'llama.cpp'")
                if "managed_service" not in inst_cols:
                    conn.execute("ALTER TABLE installations ADD COLUMN managed_service TEXT DEFAULT 'none'")
                if "deployment_method" not in inst_cols:
                    conn.execute("ALTER TABLE installations ADD COLUMN deployment_method TEXT DEFAULT 'local_api_service'")
                if "execution_plan_json" not in inst_cols:
                    conn.execute("ALTER TABLE installations ADD COLUMN execution_plan_json TEXT DEFAULT '{}'")
                if "alternative_plans_json" not in inst_cols:
                    conn.execute("ALTER TABLE installations ADD COLUMN alternative_plans_json TEXT DEFAULT '[]'")

                # Backfill existing records: ollama -> unknown/service-managed + ollama service
                conn.execute("""
                    UPDATE installations
                    SET inference_backend = 'unknown/service-managed', managed_service = 'ollama', deployment_method = 'local_api_service'
                    WHERE runtime_name = 'ollama' AND (managed_service IS NULL OR managed_service = 'none')
                """)

                run_cols = {row[1] for row in conn.execute("PRAGMA table_info(running_instances)").fetchall()}
                if "execution_plan_id" not in run_cols:
                    conn.execute("ALTER TABLE running_instances ADD COLUMN execution_plan_id TEXT")
                if "inference_backend" not in run_cols:
                    conn.execute("ALTER TABLE running_instances ADD COLUMN inference_backend TEXT")
                if "managed_service" not in run_cols:
                    conn.execute("ALTER TABLE running_instances ADD COLUMN managed_service TEXT")
                if "deployment_method" not in run_cols:
                    conn.execute("ALTER TABLE running_instances ADD COLUMN deployment_method TEXT DEFAULT 'local_api_service'")
                if "container_id" not in run_cols:
                    conn.execute("ALTER TABLE running_instances ADD COLUMN container_id TEXT")

                conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_plans (
                    plan_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    installation_id TEXT,
                    inference_backend TEXT NOT NULL,
                    managed_service TEXT NOT NULL,
                    deployment_method TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """)

                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at, description) VALUES (2, datetime('now'), 'v2: 3-dimension execution model (backend, service, deployment)')"
                )


# Global shared instance
_global_db: Optional[DatabaseManager] = None


def get_db(db_path: Optional[Path | str] = None) -> DatabaseManager:
    global _global_db
    if db_path is not None:
        return DatabaseManager(db_path)
    if _global_db is None:
        _global_db = DatabaseManager()
    return _global_db
