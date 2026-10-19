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
            conn.execute("""
            CREATE TABLE IF NOT EXISTS model_favorites (
                model_ref TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                notes TEXT
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
                applied.add(2)

            if 3 not in applied:
                inst_cols = {row[1] for row in conn.execute("PRAGMA table_info(installations)").fetchall()}
                if "execution_spec_json" not in inst_cols:
                    conn.execute("ALTER TABLE installations ADD COLUMN execution_spec_json TEXT")
                run_cols = {row[1] for row in conn.execute("PRAGMA table_info(running_instances)").fetchall()}
                if "execution_spec_json" not in run_cols:
                    conn.execute("ALTER TABLE running_instances ADD COLUMN execution_spec_json TEXT")
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at, description) VALUES (3, datetime('now'), 'v3: canonical execution specification')"
                )
                applied.add(3)

            if 4 not in applied:
                # Migration v4: Versioned model management, aliases, verification, and API gateway tables
                conn.execute("""
                CREATE TABLE IF NOT EXISTS logical_models (
                    model_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    description TEXT,
                    task_type TEXT NOT NULL,
                    default_alias TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)

                conn.execute("""
                CREATE TABLE IF NOT EXISTS model_versions (
                    version_id TEXT PRIMARY KEY,
                    logical_model_id TEXT NOT NULL REFERENCES logical_models(model_id),
                    version_tag TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    artifact_uri TEXT NOT NULL,
                    artifact_digest TEXT NOT NULL,
                    artifact_size_bytes INTEGER NOT NULL,
                    quantization TEXT,
                    context_length INTEGER NOT NULL,
                    installed_path TEXT NOT NULL,
                    installation_id TEXT REFERENCES installations(installation_id),
                    compatibility_matrix_json TEXT NOT NULL DEFAULT '{}',
                    is_pinned INTEGER NOT NULL DEFAULT 0,
                    pinned_reason TEXT,
                    pin_count INTEGER NOT NULL DEFAULT 0,
                    is_deleted INTEGER NOT NULL DEFAULT 0,
                    deleted_at TEXT
                )
                """)

                conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_profiles (
                    profile_id TEXT PRIMARY KEY,
                    version_id TEXT NOT NULL REFERENCES model_versions(version_id),
                    profile_name TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    managed_service TEXT NOT NULL,
                    deployment_method TEXT NOT NULL,
                    threads INTEGER,
                    gpu_layers INTEGER,
                    context_tokens INTEGER,
                    temperature REAL,
                    created_at TEXT NOT NULL
                )
                """)

                conn.execute("""
                CREATE TABLE IF NOT EXISTS model_aliases (
                    alias_name TEXT PRIMARY KEY,
                    target_version_id TEXT NOT NULL REFERENCES model_versions(version_id),
                    logical_model_id TEXT NOT NULL REFERENCES logical_models(model_id),
                    is_system_alias INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)

                conn.execute("""
                CREATE TABLE IF NOT EXISTS alias_history (
                    history_id TEXT PRIMARY KEY,
                    alias_name TEXT NOT NULL,
                    previous_version_id TEXT,
                    new_version_id TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    reason TEXT NOT NULL
                )
                """)

                conn.execute("""
                CREATE TABLE IF NOT EXISTS verification_records (
                    verification_id TEXT PRIMARY KEY,
                    version_id TEXT NOT NULL REFERENCES model_versions(version_id),
                    verified_at TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    benchmark_tps REAL,
                    first_token_latency_ms REAL,
                    error_message TEXT
                )
                """)

                conn.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    key_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    prefix TEXT NOT NULL,
                    hashed_secret TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    revoked_at TEXT,
                    policy_json TEXT NOT NULL DEFAULT '{}'
                )
                """)

                conn.execute("""
                CREATE TABLE IF NOT EXISTS api_requests (
                    request_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    key_id TEXT REFERENCES api_keys(key_id),
                    app_name TEXT,
                    model_alias TEXT NOT NULL,
                    resolved_version_id TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    latency_ms REAL NOT NULL DEFAULT 0.0,
                    first_token_latency_ms REAL,
                    status_code INTEGER NOT NULL,
                    error_code TEXT
                )
                """)

                self.backfill_v4_from_installations(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at, description) VALUES (4, datetime('now'), 'v4: versioned model management, aliases, and api gateway')"
                )
                applied.add(4)

            if 5 not in applied:
                # Migration v5: Guided application connection profiles and lifecycle hooks
                conn.execute("""
                CREATE TABLE IF NOT EXISTS connection_profiles (
                    connection_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    client_type TEXT NOT NULL,
                    template_version TEXT NOT NULL,
                    model_alias TEXT NOT NULL,
                    gateway_url TEXT NOT NULL,
                    key_id TEXT NOT NULL REFERENCES api_keys(key_id),
                    target_path TEXT,
                    backup_path TEXT,
                    status TEXT NOT NULL,
                    verification_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)

                conn.execute("""
                CREATE TABLE IF NOT EXISTS lifecycle_hooks (
                    hook_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    command_json TEXT NOT NULL DEFAULT '[]',
                    is_enabled INTEGER NOT NULL DEFAULT 0,
                    approved_by_user INTEGER NOT NULL DEFAULT 0,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    is_tripped INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)

                conn.execute("""
                CREATE TABLE IF NOT EXISTS hook_runs (
                    run_id TEXT PRIMARY KEY,
                    hook_id TEXT NOT NULL REFERENCES lifecycle_hooks(hook_id),
                    event_type TEXT NOT NULL,
                    triggered_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    exit_code INTEGER,
                    duration_ms REAL NOT NULL,
                    output_summary TEXT
                )
                """)

                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at, description) VALUES (5, datetime('now'), 'v5: connection profiles and lifecycle hooks')"
                )
                applied.add(5)

            if 6 not in applied:
                # Migration v6: Benchmarks, task runs, search collections, and voice runs
                conn.execute("""
                CREATE TABLE IF NOT EXISTS benchmarks (
                    benchmark_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    prompt_text TEXT NOT NULL,
                    expected_tokens INTEGER,
                    created_at TEXT NOT NULL
                )
                """)

                conn.execute("""
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    run_id TEXT PRIMARY KEY,
                    model_alias TEXT NOT NULL,
                    version_id TEXT,
                    suite_name TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL,
                    completion_tokens INTEGER NOT NULL,
                    ttft_ms REAL NOT NULL,
                    tps REAL NOT NULL,
                    total_duration_ms REAL NOT NULL,
                    peak_memory_mb REAL NOT NULL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    raw_metrics_json TEXT NOT NULL DEFAULT '{}'
                )
                """)

                conn.execute("""
                CREATE TABLE IF NOT EXISTS task_runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    model_alias TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_summary TEXT NOT NULL,
                    output_text TEXT,
                    duration_ms REAL NOT NULL DEFAULT 0.0,
                    tokens_used INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    created_at TEXT NOT NULL
                )
                """)

                conn.execute("""
                CREATE TABLE IF NOT EXISTS task_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES task_runs(run_id),
                    name TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """)

                conn.execute("""
                CREATE TABLE IF NOT EXISTS search_collections (
                    collection_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    path TEXT NOT NULL,
                    file_count INTEGER NOT NULL DEFAULT 0,
                    passage_count INTEGER NOT NULL DEFAULT 0,
                    last_indexed_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ready'
                )
                """)

                conn.execute("""
                CREATE TABLE IF NOT EXISTS search_passages (
                    passage_id TEXT PRIMARY KEY,
                    collection_id TEXT NOT NULL REFERENCES search_collections(collection_id),
                    file_path TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    line_start INTEGER NOT NULL,
                    line_end INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """)

                conn.execute("""
                CREATE TABLE IF NOT EXISTS voice_runs (
                    run_id TEXT PRIMARY KEY,
                    direction TEXT NOT NULL,
                    model TEXT NOT NULL,
                    duration_ms REAL NOT NULL DEFAULT 0.0,
                    audio_seconds REAL NOT NULL DEFAULT 0.0,
                    transcript TEXT,
                    status TEXT NOT NULL DEFAULT 'completed',
                    error_message TEXT,
                    created_at TEXT NOT NULL
                )
                """)

                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at, description) VALUES (6, datetime('now'), 'v6: benchmarks, tasks, search, and voice')"
                )
                applied.add(6)

    def backfill_v4_from_installations(self, conn: sqlite3.Connection) -> None:
        """Non-destructive idempotent backfill promoting installations to versioned model management."""
        installations = conn.execute("SELECT * FROM installations").fetchall()
        inst_col_names = [col[0] for col in conn.execute("SELECT * FROM installations LIMIT 0").description]

        for row in installations:
            inst = dict(zip(inst_col_names, row))
            m_id = inst.get("model_id") or "default-model"
            inst_id = inst.get("installation_id") or "inst-0"
            now_str = inst.get("installed_at") or "2026-01-01T00:00:00Z"

            # Logical model
            existing_lm = conn.execute("SELECT model_id FROM logical_models WHERE model_id = ?", (m_id,)).fetchone()
            if not existing_lm:
                conn.execute(
                    "INSERT INTO logical_models (model_id, display_name, description, task_type, default_alias, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (m_id, m_id, f"Model {m_id}", "general", f"{m_id}:latest", now_str, now_str)
                )

            # Model version
            ver_id = f"ver-{inst_id[:12]}"
            existing_ver = conn.execute("SELECT version_id FROM model_versions WHERE version_id = ?", (ver_id,)).fetchone()
            if not existing_ver:
                conn.execute(
                    "INSERT INTO model_versions (version_id, logical_model_id, version_tag, created_at, "
                    "artifact_uri, artifact_digest, artifact_size_bytes, quantization, context_length, installed_path, installation_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ver_id,
                        m_id,
                        "v1",
                        now_str,
                        inst.get("source_url") or inst.get("model_path") or "local",
                        inst.get("sha256") or "unknown",
                        inst.get("size_bytes") or 0,
                        inst.get("quantization") or "Q4_K_M",
                        2048,
                        inst.get("model_path") or "",
                        inst_id,
                    )
                )

            # Execution profile
            prof_id = f"prof-{inst_id[:12]}"
            existing_prof = conn.execute("SELECT profile_id FROM execution_profiles WHERE profile_id = ?", (prof_id,)).fetchone()
            if not existing_prof:
                conn.execute(
                    "INSERT INTO execution_profiles (profile_id, version_id, profile_name, backend, managed_service, deployment_method, context_tokens, temperature, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        prof_id,
                        ver_id,
                        "default",
                        inst.get("inference_backend") or "llama.cpp",
                        inst.get("managed_service") or "none",
                        inst.get("deployment_method") or "local_api_service",
                        2048,
                        0.7,
                        now_str,
                    )
                )

            # Aliases
            alias_tag = f"{m_id}:latest"
            if not conn.execute("SELECT alias_name FROM model_aliases WHERE alias_name = ?", (alias_tag,)).fetchone():
                conn.execute(
                    "INSERT INTO model_aliases (alias_name, target_version_id, logical_model_id, is_system_alias, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1, ?, ?)",
                    (alias_tag, ver_id, m_id, now_str, now_str)
                )
                conn.execute(
                    "INSERT INTO alias_history (history_id, alias_name, previous_version_id, new_version_id, changed_at, reason) "
                    "VALUES (?, ?, NULL, ?, ?, 'Initial migration backfill')",
                    (f"hist-{alias_tag}-init", alias_tag, ver_id, now_str)
                )

            # Default global alias
            if not conn.execute("SELECT alias_name FROM model_aliases WHERE alias_name = 'default'").fetchone():
                conn.execute(
                    "INSERT INTO model_aliases (alias_name, target_version_id, logical_model_id, is_system_alias, created_at, updated_at) "
                    "VALUES ('default', ?, ?, 1, ?, ?)",
                    (ver_id, m_id, now_str, now_str)
                )
                conn.execute(
                    "INSERT INTO alias_history (history_id, alias_name, previous_version_id, new_version_id, changed_at, reason) "
                    "VALUES (?, 'default', NULL, ?, ?, 'Initial migration default alias')",
                    (f"hist-default-{ver_id}", ver_id, now_str)
                )

    def add_favorite(self, model_ref: str, notes: Optional[str] = None) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn = self.get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO model_favorites (model_ref, created_at, notes) VALUES (?, ?, ?)",
                (model_ref, now, notes),
            )

    def remove_favorite(self, model_ref: str) -> None:
        conn = self.get_connection()
        with conn:
            conn.execute("DELETE FROM model_favorites WHERE model_ref = ?", (model_ref,))

    def list_favorites(self) -> List[str]:
        conn = self.get_connection()
        rows = conn.execute("SELECT model_ref FROM model_favorites ORDER BY created_at DESC").fetchall()
        return [r["model_ref"] for r in rows]

    def list_favorites_detailed(self) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        rows = conn.execute("SELECT model_ref, created_at, notes FROM model_favorites ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def is_favorite(self, model_ref: str) -> bool:
        conn = self.get_connection()
        row = conn.execute("SELECT 1 FROM model_favorites WHERE model_ref = ?", (model_ref,)).fetchone()
        return row is not None

    # V6 Table Helpers: Benchmarks
    def record_benchmark_run(self, run: Dict[str, Any]) -> None:
        import json
        conn = self.get_connection()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO benchmark_runs (
                    run_id, model_alias, version_id, suite_name, prompt_tokens,
                    completion_tokens, ttft_ms, tps, total_duration_ms, peak_memory_mb,
                    created_at, raw_metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["run_id"],
                    run["model_alias"],
                    run.get("version_id"),
                    run["suite_name"],
                    run.get("prompt_tokens", 0),
                    run.get("completion_tokens", 0),
                    run.get("ttft_ms", 0.0),
                    run.get("tps", 0.0),
                    run.get("total_duration_ms", 0.0),
                    run.get("peak_memory_mb", 0.0),
                    run["created_at"],
                    json.dumps(run.get("raw_metrics", {})),
                ),
            )

    def list_benchmark_runs(self, model_alias: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        import json
        conn = self.get_connection()
        if model_alias:
            rows = conn.execute(
                "SELECT * FROM benchmark_runs WHERE model_alias = ? ORDER BY created_at DESC LIMIT ?",
                (model_alias, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM benchmark_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if "raw_metrics_json" in d:
                try:
                    d["raw_metrics"] = json.loads(d["raw_metrics_json"])
                except Exception:
                    d["raw_metrics"] = {}
            result.append(d)
        return result

    def get_benchmark_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        import json
        conn = self.get_connection()
        row = conn.execute("SELECT * FROM benchmark_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        if "raw_metrics_json" in d:
            try:
                d["raw_metrics"] = json.loads(d["raw_metrics_json"])
            except Exception:
                d["raw_metrics"] = {}
        return d

    # V6 Table Helpers: Tasks
    def record_task_run(self, run: Dict[str, Any], artifacts: Optional[List[Dict[str, Any]]] = None) -> None:
        conn = self.get_connection()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO task_runs (
                    run_id, task_id, model_alias, status, input_summary,
                    output_text, duration_ms, tokens_used, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["run_id"],
                    run["task_id"],
                    run["model_alias"],
                    run["status"],
                    run.get("input_summary", ""),
                    run.get("output_text"),
                    run.get("duration_ms", 0.0),
                    run.get("tokens_used", 0),
                    run.get("error_message"),
                    run["created_at"],
                ),
            )
            if artifacts:
                for art in artifacts:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO task_artifacts (
                            artifact_id, run_id, name, artifact_type, content, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            art["artifact_id"],
                            run["run_id"],
                            art["name"],
                            art["artifact_type"],
                            art["content"],
                            art.get("created_at", run["created_at"]),
                        ),
                    )

    def list_task_runs(self, task_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        if task_id:
            rows = conn.execute(
                "SELECT * FROM task_runs WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM task_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # V6 Table Helpers: Search Collections & Passages
    def save_search_collection(self, collection_id: str, name: str, path: str, file_count: int, passage_count: int) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn = self.get_connection()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO search_collections (
                    collection_id, name, path, file_count, passage_count, last_indexed_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'ready')
                """,
                (collection_id, name, path, file_count, passage_count, now),
            )

    def list_search_collections(self) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        rows = conn.execute("SELECT * FROM search_collections ORDER BY last_indexed_at DESC").fetchall()
        return [dict(r) for r in rows]

    def get_search_collection(self, name_or_id: str) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        row = conn.execute(
            "SELECT * FROM search_collections WHERE collection_id = ? OR name = ?",
            (name_or_id, name_or_id),
        ).fetchone()
        return dict(row) if row else None

    def delete_search_collection(self, collection_id: str) -> None:
        conn = self.get_connection()
        with conn:
            conn.execute("DELETE FROM search_passages WHERE collection_id = ?", (collection_id,))
            conn.execute("DELETE FROM search_collections WHERE collection_id = ?", (collection_id,))

    def save_search_passages(self, passages: List[Dict[str, Any]]) -> None:
        import json
        conn = self.get_connection()
        with conn:
            for p in passages:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO search_passages (
                        passage_id, collection_id, file_path, relative_path,
                        line_start, line_end, content, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        p["passage_id"],
                        p["collection_id"],
                        p["file_path"],
                        p.get("relative_path", p["file_path"]),
                        p.get("line_start", 1),
                        p.get("line_end", 1),
                        p["content"],
                        json.dumps(p.get("metadata", {})),
                    ),
                )

    def get_search_passages(self, collection_id: str) -> List[Dict[str, Any]]:
        import json
        conn = self.get_connection()
        rows = conn.execute(
            "SELECT * FROM search_passages WHERE collection_id = ?",
            (collection_id,),
        ).fetchall()
        res = []
        for r in rows:
            d = dict(r)
            if "metadata_json" in d:
                try:
                    d["metadata"] = json.loads(d["metadata_json"])
                except Exception:
                    d["metadata"] = {}
            res.append(d)
        return res

    # V6 Table Helpers: Voice
    def record_voice_run(self, run: Dict[str, Any]) -> None:
        conn = self.get_connection()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO voice_runs (
                    run_id, direction, model, duration_ms, audio_seconds,
                    transcript, status, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["run_id"],
                    run["direction"],
                    run["model"],
                    run.get("duration_ms", 0.0),
                    run.get("audio_seconds", 0.0),
                    run.get("transcript"),
                    run.get("status", "completed"),
                    run.get("error_message"),
                    run["created_at"],
                ),
            )

    def list_voice_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        rows = conn.execute("SELECT * FROM voice_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# Global shared instance
_global_db: Optional[DatabaseManager] = None


def get_db(db_path: Optional[Path | str] = None) -> DatabaseManager:
    global _global_db
    if db_path is not None:
        return DatabaseManager(db_path)
    if _global_db is None:
        _global_db = DatabaseManager()
    return _global_db
