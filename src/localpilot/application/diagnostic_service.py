"""Diagnostic and self-healing repair service for LocalPilot."""

from __future__ import annotations
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from localpilot.domain.task_contract import (
    DiagnosticFinding,
    FindingSeverity,
    RepairPlan,
    RepairResult,
)
from localpilot.storage.db import DatabaseManager, get_db
from localpilot.hardware.inspector import SystemInspector


class DiagnosticService:
    """Performs deep health checks across hardware, runtimes, database, models, and connections."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or get_db()
        self.inspector = SystemInspector()

    def run_general_diagnostics(self) -> List[DiagnosticFinding]:
        """Runs overall system health, hardware, and state database checks."""
        findings: List[DiagnosticFinding] = []

        # 1. System hardware & resources
        try:
            report = self.inspector.inspect()
            hw = report.hardware
            safe_budget_gb = hw.safe_ram_budget_bytes / (1024**3)
            avail_ram_gb = hw.ram_available_bytes / (1024**3)
            disk_free_gb = hw.disk_available_bytes / (1024**3)

            if avail_ram_gb < 2.0:
                findings.append(
                    DiagnosticFinding(
                        finding_id="sys_low_ram",
                        category="system",
                        severity=FindingSeverity.WARNING,
                        title="Low Available Memory",
                        message=f"Only {avail_ram_gb:.1f} GB of RAM is available. Large models may be throttled or fail to load.",
                        can_repair=False,
                        remediation_suggestion="Close heavy applications or select a smaller model (1B-3B parameters).",
                    )
                )
            else:
                findings.append(
                    DiagnosticFinding(
                        finding_id="sys_ram_ok",
                        category="system",
                        severity=FindingSeverity.INFO,
                        title="System Memory Normal",
                        message=f"{avail_ram_gb:.1f} GB available (Safe allocation budget: {safe_budget_gb:.1f} GB).",
                    )
                )

            if disk_free_gb < 5.0:
                findings.append(
                    DiagnosticFinding(
                        finding_id="sys_low_disk",
                        category="system",
                        severity=FindingSeverity.ERROR if disk_free_gb < 1.0 else FindingSeverity.WARNING,
                        title="Low Disk Space",
                        message=f"Only {disk_free_gb:.1f} GB of storage is free. New model downloads may fail.",
                        can_repair=True,
                        repair_action="clean_temp_cache",
                        remediation_suggestion="Clean temporary files or remove unused models.",
                    )
                )
            else:
                findings.append(
                    DiagnosticFinding(
                        finding_id="sys_disk_ok",
                        category="system",
                        severity=FindingSeverity.INFO,
                        title="Storage Space Adequate",
                        message=f"{disk_free_gb:.1f} GB free storage available.",
                    )
                )
        except Exception as exc:
            findings.append(
                DiagnosticFinding(
                    finding_id="sys_inspect_err",
                    category="system",
                    severity=FindingSeverity.ERROR,
                    title="Hardware Inspection Error",
                    message=f"Unable to inspect hardware parameters: {exc}",
                )
            )

        # 2. Database & instance health
        try:
            conn = self.db.get_connection()
            # Check stale running instances
            stale_instances = conn.execute("SELECT instance_id, pid FROM running_instances").fetchall()
            stale_count = 0
            for row in stale_instances:
                pid = row["pid"]
                try:
                    os.kill(pid, 0)
                except OSError:
                    stale_count += 1

            if stale_count > 0:
                findings.append(
                    DiagnosticFinding(
                        finding_id="db_stale_instances",
                        category="runtime",
                        severity=FindingSeverity.WARNING,
                        title="Stale Runtime Instance Records",
                        message=f"Found {stale_count} dead runtime instance process record(s) in database.",
                        can_repair=True,
                        repair_action="clean_stale_instances",
                        remediation_suggestion="Remove dead process tracking entries from local state database.",
                    )
                )
            else:
                findings.append(
                    DiagnosticFinding(
                        finding_id="db_instances_ok",
                        category="runtime",
                        severity=FindingSeverity.INFO,
                        title="Instance Registry Healthy",
                        message="No orphaned runtime instance processes detected.",
                    )
                )

            # Check default model alias
            default_alias = conn.execute("SELECT target_version_id FROM model_aliases WHERE alias_name = 'default'").fetchone()
            if not default_alias:
                any_version = conn.execute("SELECT version_id FROM model_versions WHERE is_deleted = 0 LIMIT 1").fetchone()
                if any_version:
                    findings.append(
                        DiagnosticFinding(
                            finding_id="model_missing_default_alias",
                            category="model",
                            severity=FindingSeverity.WARNING,
                            title="Missing 'default' Model Alias",
                            message="The 'default' alias is not set. Tools and connections relying on default will fail.",
                            can_repair=True,
                            repair_action="repair_default_alias",
                            remediation_suggestion=f"Assign 'default' alias to available version '{any_version['version_id']}'.",
                        )
                    )
                else:
                    findings.append(
                        DiagnosticFinding(
                            finding_id="model_no_installed_models",
                            category="model",
                            severity=FindingSeverity.INFO,
                            title="No Models Installed",
                            message="No local models have been installed yet. Run 'localpilot models' to discover and install a model.",
                        )
                    )
        except Exception as exc:
            findings.append(
                DiagnosticFinding(
                    finding_id="db_error",
                    category="runtime",
                    severity=FindingSeverity.ERROR,
                    title="Database Health Error",
                    message=f"Failed to query state database: {exc}",
                )
            )

        return findings

    def check_model_diagnostics(self, model_alias: str) -> List[DiagnosticFinding]:
        """Diagnoses a specific model alias, version mapping, and artifact files."""
        findings: List[DiagnosticFinding] = []
        conn = self.db.get_connection()

        row = conn.execute(
            """
            SELECT a.alias_name, a.target_version_id, v.installed_path, v.artifact_size_bytes, v.quantization, v.is_deleted
            FROM model_aliases a
            JOIN model_versions v ON a.target_version_id = v.version_id
            WHERE a.alias_name = ?
            """,
            (model_alias,),
        ).fetchone()

        if not row:
            findings.append(
                DiagnosticFinding(
                    finding_id=f"model_{model_alias}_not_found",
                    category="model",
                    target_id=model_alias,
                    severity=FindingSeverity.ERROR,
                    title=f"Model Alias '{model_alias}' Not Found",
                    message=f"No version is mapped to alias '{model_alias}'. Run 'localpilot alias list' to see available aliases.",
                    can_repair=False,
                )
            )
            return findings

        if row["is_deleted"]:
            findings.append(
                DiagnosticFinding(
                    finding_id=f"model_{model_alias}_deleted",
                    category="model",
                    target_id=model_alias,
                    severity=FindingSeverity.ERROR,
                    title=f"Target Version for '{model_alias}' Is Marked Deleted",
                    message=f"Alias points to deleted version '{row['target_version_id']}'.",
                    can_repair=True,
                    repair_action="repoint_alias",
                    remediation_suggestion="Repoint alias to an active model version.",
                )
            )

        inst_path = Path(row["installed_path"])
        if not inst_path.exists():
            findings.append(
                DiagnosticFinding(
                    finding_id=f"model_{model_alias}_file_missing",
                    category="model",
                    target_id=model_alias,
                    severity=FindingSeverity.ERROR,
                    title="Model Artifact File Missing on Disk",
                    message=f"Artifact expected at '{inst_path}' does not exist on disk.",
                    can_repair=False,
                    remediation_suggestion="Re-download or reinstall the model artifact.",
                )
            )
        else:
            findings.append(
                DiagnosticFinding(
                    finding_id=f"model_{model_alias}_ok",
                    category="model",
                    target_id=model_alias,
                    severity=FindingSeverity.INFO,
                    title=f"Model '{model_alias}' Ready",
                    message=f"Artifact exists ({row['artifact_size_bytes'] / (1024**2):.1f} MB, {row['quantization']}) at {inst_path}.",
                )
            )

        return findings

    def check_connection_diagnostics(self, connection_id: str) -> List[DiagnosticFinding]:
        """Diagnoses a specific external application connection."""
        from localpilot.application.integration_service import ConnectionManager
        findings: List[DiagnosticFinding] = []
        mgr = ConnectionManager(self.db)
        profile = mgr.get_connection(connection_id)

        if not profile:
            findings.append(
                DiagnosticFinding(
                    finding_id=f"conn_{connection_id}_not_found",
                    category="connection",
                    target_id=connection_id,
                    severity=FindingSeverity.ERROR,
                    title=f"Connection '{connection_id}' Not Found",
                    message=f"No registered connection with ID '{connection_id}'.",
                )
            )
            return findings

        test_res = mgr.test_connection(connection_id, probe_live=False)

        if not test_res.config_valid:
            findings.append(
                DiagnosticFinding(
                    finding_id=f"conn_{connection_id}_config_invalid",
                    category="connection",
                    target_id=connection_id,
                    severity=FindingSeverity.ERROR,
                    title="Configuration Syntax Error",
                    message=test_res.details.get("config_syntax", "Malformed config file"),
                    can_repair=bool(profile.backup_path and Path(profile.backup_path).exists()),
                    repair_action="restore_connection_config",
                    remediation_suggestion="Restore configuration from backup.",
                )
            )

        if not test_res.key_valid:
            findings.append(
                DiagnosticFinding(
                    finding_id=f"conn_{connection_id}_key_invalid",
                    category="connection",
                    target_id=connection_id,
                    severity=FindingSeverity.ERROR,
                    title="API Key Inactive or Missing",
                    message=test_res.details.get("api_key", "API key invalid"),
                    can_repair=True,
                    repair_action="regenerate_connection_key",
                    remediation_suggestion="Generate and assign a new active API key for this connection.",
                )
            )

        if not test_res.model_authorized:
            findings.append(
                DiagnosticFinding(
                    finding_id=f"conn_{connection_id}_model_unauthorized",
                    category="connection",
                    target_id=connection_id,
                    severity=FindingSeverity.WARNING,
                    title="Model Alias Unauthorized",
                    message=test_res.details.get("model_authorization", "Key policy restricts model"),
                    can_repair=True,
                    repair_action="authorize_model_for_key",
                    remediation_suggestion="Update API key policy to permit the configured model alias.",
                )
            )

        if not findings:
            findings.append(
                DiagnosticFinding(
                    finding_id=f"conn_{connection_id}_ok",
                    category="connection",
                    target_id=connection_id,
                    severity=FindingSeverity.INFO,
                    title=f"Connection '{connection_id}' Healthy",
                    message=f"Configuration for {profile.name} is valid and active.",
                )
            )

        return findings

    def check_tool_diagnostics(self, tool_id: str) -> List[DiagnosticFinding]:
        """Diagnoses tool availability, required capabilities, and model readiness."""
        findings: List[DiagnosticFinding] = []
        conn = self.db.get_connection()

        # Check if any model is available
        version = conn.execute("SELECT version_id FROM model_versions WHERE is_deleted = 0 LIMIT 1").fetchone()
        if not version:
            findings.append(
                DiagnosticFinding(
                    finding_id=f"tool_{tool_id}_no_model",
                    category="tool",
                    target_id=tool_id,
                    severity=FindingSeverity.ERROR,
                    title=f"No Models Available for Tool '{tool_id}'",
                    message="At least one local model must be installed to execute AI tools.",
                    can_repair=False,
                    remediation_suggestion="Install a model via 'localpilot models'.",
                )
            )
        else:
            findings.append(
                DiagnosticFinding(
                    finding_id=f"tool_{tool_id}_ok",
                    category="tool",
                    target_id=tool_id,
                    severity=FindingSeverity.INFO,
                    title=f"Tool '{tool_id}' Ready",
                    message=f"Execution environment is ready to handle task '{tool_id}'.",
                )
            )

        return findings

    def get_repair_plan(self, finding: DiagnosticFinding) -> Optional[RepairPlan]:
        """Generates an explicit, reviewed repair plan for a repairable finding."""
        if not finding.can_repair or not finding.repair_action:
            return None

        if finding.repair_action == "clean_stale_instances":
            return RepairPlan(
                plan_id="plan_clean_stale_instances",
                finding_id=finding.finding_id,
                action="clean_stale_instances",
                description="Prune non-running process records from local running_instances database.",
                risk_level="low",
                requires_confirmation=False,
            )

        if finding.repair_action == "clean_temp_cache":
            return RepairPlan(
                plan_id="plan_clean_temp_cache",
                finding_id=finding.finding_id,
                action="clean_temp_cache",
                description="Remove temporary build and log artifacts from ~/.localpilot/tmp.",
                risk_level="low",
                requires_confirmation=True,
            )

        if finding.repair_action == "repair_default_alias":
            return RepairPlan(
                plan_id="plan_repair_default_alias",
                finding_id=finding.finding_id,
                action="repair_default_alias",
                description="Map 'default' model alias to the first active installed model version.",
                risk_level="low",
                requires_confirmation=True,
            )

        if finding.repair_action == "restore_connection_config":
            return RepairPlan(
                plan_id=f"plan_restore_conn_{finding.target_id}",
                finding_id=finding.finding_id,
                action="restore_connection_config",
                description=f"Restore backup configuration for connection '{finding.target_id}'.",
                risk_level="medium",
                requires_confirmation=True,
            )

        if finding.repair_action == "authorize_model_for_key":
            return RepairPlan(
                plan_id=f"plan_auth_model_{finding.target_id}",
                finding_id=finding.finding_id,
                action="authorize_model_for_key",
                description="Authorize configured model in API key policy.",
                risk_level="low",
                requires_confirmation=True,
            )

        return None

    def execute_repair(self, plan: RepairPlan) -> RepairResult:
        """Executes an approved repair plan safely."""
        conn = self.db.get_connection()

        if plan.action == "clean_stale_instances":
            stale_instances = conn.execute("SELECT instance_id, pid FROM running_instances").fetchall()
            pruned_ids = []
            for row in stale_instances:
                pid = row["pid"]
                try:
                    os.kill(pid, 0)
                except OSError:
                    pruned_ids.append(row["instance_id"])

            if pruned_ids:
                with conn:
                    conn.executemany(
                        "DELETE FROM running_instances WHERE instance_id = ?",
                        [(iid,) for iid in pruned_ids],
                    )

            return RepairResult(
                plan_id=plan.plan_id,
                success=True,
                message=f"Pruned {len(pruned_ids)} stale instance record(s).",
                details={"pruned_instance_ids": pruned_ids},
            )

        if plan.action == "clean_temp_cache":
            tmp_dir = Path.home() / ".localpilot" / "tmp"
            deleted_count = 0
            if tmp_dir.exists():
                for item in tmp_dir.iterdir():
                    try:
                        if item.is_file() or item.is_symlink():
                            item.unlink()
                        elif item.is_dir():
                            shutil.rmtree(item)
                        deleted_count += 1
                    except Exception:
                        pass
            return RepairResult(
                plan_id=plan.plan_id,
                success=True,
                message=f"Cleaned {deleted_count} temporary file(s).",
                details={"deleted_items": deleted_count},
            )

        if plan.action == "repair_default_alias":
            from datetime import datetime, timezone
            now_str = datetime.now(timezone.utc).isoformat()
            ver = conn.execute("SELECT version_id, logical_model_id FROM model_versions WHERE is_deleted = 0 LIMIT 1").fetchone()
            if not ver:
                return RepairResult(plan_id=plan.plan_id, success=False, message="No active versions found to map 'default'.")

            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO model_aliases (alias_name, target_version_id, logical_model_id, is_system_alias, created_at, updated_at) "
                    "VALUES ('default', ?, ?, 1, ?, ?)",
                    (ver["version_id"], ver["logical_model_id"], now_str, now_str),
                )
            return RepairResult(
                plan_id=plan.plan_id,
                success=True,
                message=f"Mapped 'default' alias to version '{ver['version_id']}'.",
            )

        if plan.action == "restore_connection_config":
            from localpilot.application.integration_service import ConnectionManager
            conn_id = plan.finding_id.replace("conn_", "").replace("_config_invalid", "")
            mgr = ConnectionManager(self.db)
            res = mgr.restore_connection(conn_id)
            return RepairResult(
                plan_id=plan.plan_id,
                success=res.get("success", False),
                message=f"Restored configuration from backup for {conn_id}.",
                details=res,
            )

        if plan.action == "authorize_model_for_key":
            from localpilot.application.api_key_service import ApiKeyService
            from localpilot.application.integration_service import ConnectionManager
            conn_id = plan.finding_id.replace("conn_", "").replace("_model_unauthorized", "")
            mgr = ConnectionManager(self.db)
            profile = mgr.get_connection(conn_id)
            if not profile:
                return RepairResult(plan_id=plan.plan_id, success=False, message=f"Connection {conn_id} not found.")

            key_svc = ApiKeyService(self.db)
            key_record = key_svc.get_key(profile.key_id)
            if not key_record:
                return RepairResult(plan_id=plan.plan_id, success=False, message=f"Key {profile.key_id} not found.")

            policy = key_record.policy
            if profile.model_alias not in policy.allowed_aliases:
                policy.allowed_aliases.append(profile.model_alias)
            key_svc.update_key_policy(profile.key_id, policy)
            return RepairResult(
                plan_id=plan.plan_id,
                success=True,
                message=f"Added model '{profile.model_alias}' to API key policy.",
            )

        return RepairResult(plan_id=plan.plan_id, success=False, message=f"Unknown repair action: {plan.action}")
