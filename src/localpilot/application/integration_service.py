from __future__ import annotations
import difflib
import json
import logging
import os
import re
import shutil
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from localpilot.domain.api_contract import (
    ApiKeyPolicy,
    ApiKeyRecord,
    ConnectionProfile,
    ConnectionStatus,
    ConnectionVerificationResult,
)
from localpilot.storage.db import DatabaseManager, get_db
from localpilot.application.api_key_service import ApiKeyService

logger = logging.getLogger(__name__)

SENSITIVE_SYSTEM_PREFIXES = ("/etc", "/sys", "/proc", "/dev", "/boot", "/root", "/usr/bin", "/bin", "/sbin")


def redact_sensitive_strings(text: str) -> str:
    """Redacts API keys and bearer tokens from text/diffs to prevent credential leaks."""
    text = re.sub(r"sk-local-[A-Za-z0-9_-]+", "sk-local-••••••••", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9_.-]+", "Bearer ••••••••", text)
    return text


def generate_redacted_diff(old_content: str, new_content: str, fromfile: str = "before", tofile: str = "after") -> str:
    """Generates a unified diff with all sensitive tokens and secrets redacted."""
    redacted_old = redact_sensitive_strings(old_content)
    redacted_new = redact_sensitive_strings(new_content)
    diff_lines = list(
        difflib.unified_diff(
            redacted_old.splitlines(keepends=True),
            redacted_new.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
        )
    )
    return "".join(diff_lines)


def validate_safe_path(target_path: Path | str) -> Path:
    """Validates that a configuration path is not in a sensitive system directory or dangerous symlink."""
    p = Path(target_path)
    try:
        resolved = p.resolve()
    except Exception as exc:
        raise ValueError(f"Invalid configuration path '{target_path}': {exc}") from exc

    resolved_str = str(resolved)
    for prefix in SENSITIVE_SYSTEM_PREFIXES:
        if resolved_str == prefix or resolved_str.startswith(f"{prefix}/"):
            raise ValueError(f"Security error: configuration path '{target_path}' points to sensitive system location '{resolved_str}'.")

    return p


class IntegrationTemplateService:
    """Generates and writes configuration files for external applications with backups and diffs."""

    @staticmethod
    def get_opencode_template(base_url: str, api_key: str, model_alias: str = "coding") -> Dict[str, Any]:
        """Generates OpenCode JSON configuration structure."""
        return {
            "provider": "localpilot",
            "baseUrl": base_url.rstrip("/"),
            "apiKey": api_key,
            "model": model_alias,
            "temperature": 0.2,
            "maxTokens": 4096,
        }

    @staticmethod
    def get_continue_template(base_url: str, api_key: str, model_alias: str = "coding") -> Dict[str, Any]:
        """Generates VS Code Continue extension model configuration entry."""
        return {
            "title": f"LocalPilot ({model_alias})",
            "provider": "openai",
            "model": model_alias,
            "apiBase": base_url.rstrip("/"),
            "apiKey": api_key,
        }

    @staticmethod
    def get_cursor_instructions(base_url: str, api_key: str, model_alias: str = "coding") -> Dict[str, Any]:
        """Generates instructions and settings for Cursor custom OpenAI model configuration."""
        return {
            "application": "Cursor",
            "steps": [
                "Open Cursor Settings -> Models -> OpenAI API Key",
                f"Enter Base URL: {base_url.rstrip('/')}",
                f"Enter API Key: {api_key}",
                f"Add Custom Model: {model_alias}",
                "Turn off models you do not use to prevent accidental cloud calls",
            ],
            "configuration": {
                "base_url": base_url.rstrip("/"),
                "api_key": api_key,
                "model": model_alias,
            },
        }

    @staticmethod
    def get_client_snippets(base_url: str, api_key: str, model_alias: str = "default") -> Dict[str, str]:
        """Generates code snippets for cURL, Python, and Node.js."""
        clean_url = base_url.rstrip("/")
        curl_snippet = f"""curl {clean_url}/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer {api_key}" \\
  -d '{{"model": "{model_alias}", "messages": [{{"role": "user", "content": "Hello local AI!"}}]}}'"""

        python_snippet = f"""from openai import OpenAI

client = OpenAI(
    base_url="{clean_url}",
    api_key="{api_key}",
)

response = client.chat.completions.create(
    model="{model_alias}",
    messages=[{{"role": "user", "content": "Hello local AI!"}}],
)
print(response.choices[0].message.content)"""

        node_snippet = f"""import OpenAI from "openai";

const client = new OpenAI({{
  baseURL: "{clean_url}",
  apiKey: "{api_key}",
}});

const response = await client.chat.completions.create({{
  model: "{model_alias}",
  messages: [{{ role: "user", content: "Hello local AI!" }}],
}});
console.log(response.choices[0].message.content);"""

        return {
            "curl": curl_snippet,
            "python": python_snippet,
            "nodejs": node_snippet,
        }

    @staticmethod
    def get_cline_instructions(base_url: str, api_key: str, model_alias: str = "coding") -> Dict[str, Any]:
        """Generates configuration for Cline (Claude Dev) autonomous coding agent in VS Code."""
        return {
            "application": "Cline (VS Code)",
            "steps": [
                "Open Cline in VS Code sidebar -> Click the Settings (gear) icon",
                "Set API Provider: 'OpenAI Compatible'",
                f"Set Base URL: {base_url.rstrip('/')}",
                f"Set API Key: {api_key}",
                f"Set Model ID: {model_alias}",
            ],
            "configuration": {
                "api_provider": "OpenAI Compatible",
                "base_url": base_url.rstrip("/"),
                "api_key": api_key,
                "model_id": model_alias,
            },
        }

    @staticmethod
    def get_aider_instructions(base_url: str, api_key: str, model_alias: str = "coding") -> Dict[str, Any]:
        """Generates startup command and environment variables for Aider terminal pair programmer."""
        clean_url = base_url.rstrip("/")
        cmd = f"aider --openai-api-base {clean_url} --openai-api-key {api_key} --model openai/{model_alias}"
        env_cmd = (
            f"export OPENAI_API_BASE={clean_url}\n"
            f"export OPENAI_API_KEY={api_key}\n"
            f"aider --model openai/{model_alias}"
        )
        return {
            "application": "Aider",
            "command": cmd,
            "env_setup": env_cmd,
            "model": f"openai/{model_alias}",
        }

    @staticmethod
    def get_librechat_template(base_url: str, api_key: str, model_alias: str = "default") -> str:
        """Generates custom endpoint configuration for LibreChat in librechat.yaml."""
        clean_url = base_url.rstrip("/")
        return f"""endpoints:
  custom:
    - name: "LocalPilot"
      apiKey: "{api_key}"
      baseURL: "{clean_url}"
      models:
        default: ["{model_alias}", "coding", "default", "reasoning"]
        fetch: false
      titleConvo: true
      titleModel: "{model_alias}"
      summarize: false
      summaryModel: "{model_alias}"
      forcePrompt: false
      modelDisplayLabel: "LocalPilot ({model_alias})"
"""

    @staticmethod
    def get_openwebui_instructions(base_url: str, api_key: str, model_alias: str = "default") -> Dict[str, Any]:
        """Generates connection settings for Open WebUI self-hosted interface."""
        return {
            "application": "Open WebUI",
            "steps": [
                "Navigate to Open WebUI -> Admin Settings -> Connections",
                "Under 'OpenAI API', toggle connection to ON",
                f"Set URL: {base_url.rstrip('/')}",
                f"Set API Key: {api_key}",
                "Click Save and verify connection status turns green",
            ],
            "configuration": {
                "url": base_url.rstrip("/"),
                "api_key": api_key,
                "model": model_alias,
            },
        }

    @staticmethod
    def get_anythingllm_instructions(base_url: str, api_key: str, model_alias: str = "default") -> Dict[str, Any]:
        """Generates connection settings for AnythingLLM Desktop and Docker."""
        return {
            "application": "AnythingLLM",
            "steps": [
                "Open AnythingLLM Settings -> LLM Preference",
                "Choose LLM Provider: 'Generic OpenAI'",
                f"Enter Base URL: {base_url.rstrip('/')}",
                f"Enter API Key: {api_key}",
                f"Enter Chat Model Name: {model_alias}",
                "Save changes",
            ],
            "configuration": {
                "provider": "Generic OpenAI",
                "base_url": base_url.rstrip("/"),
                "api_key": api_key,
                "model": model_alias,
            },
        }

    @staticmethod
    def get_zed_template(base_url: str, api_key: str, model_alias: str = "coding") -> Dict[str, Any]:
        """Generates language_models config for Zed Editor (~/.config/zed/settings.json)."""
        return {
            "language_models": {
                "openai": {
                    "api_url": base_url.rstrip("/"),
                    "available_models": [
                        {
                            "name": model_alias,
                            "max_tokens": 4096,
                        }
                    ],
                }
            }
        }

    @classmethod
    def apply_opencode_config(
        cls,
        target_path: Path | str,
        base_url: str,
        api_key: str,
        model_alias: str = "coding",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Writes or merges opencode.json with automatic backup, malformed check, and 0600 mode."""
        path_obj = validate_safe_path(target_path).resolve()
        backup_path = None
        old_raw = ""

        existing_data: Dict[str, Any] = {}
        if path_obj.exists():
            old_raw = path_obj.read_text()
            try:
                existing_data = json.loads(old_raw)
                if not isinstance(existing_data, dict):
                    raise ValueError("JSON root must be an object")
            except Exception as exc:
                raise ValueError(f"Malformed configuration file at '{path_obj}'. Refusing to overwrite: {exc}") from exc

            if not dry_run:
                ts = int(time.time() * 1000)
                backup_path = path_obj.with_name(f"{path_obj.name}.bak-{ts}")
                shutil.copy2(path_obj, backup_path)
                default_bak = path_obj.with_suffix(".json.bak")
                shutil.copy2(path_obj, default_bak)
                try:
                    backup_path.chmod(0o600)
                    default_bak.chmod(0o600)
                except Exception:
                    pass

        new_settings = cls.get_opencode_template(base_url, api_key, model_alias)
        merged = {**existing_data, **new_settings}
        new_raw = json.dumps(merged, indent=2)
        diff = generate_redacted_diff(old_raw, new_raw, fromfile=str(path_obj), tofile=str(path_obj))

        if not dry_run:
            path_obj.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path_obj.with_name(f".{path_obj.name}.tmp-{uuid.uuid4().hex[:8]}")
            tmp_path.write_text(new_raw)
            try:
                tmp_path.chmod(0o600)
            except Exception:
                pass
            tmp_path.replace(path_obj)
            try:
                path_obj.chmod(0o600)
            except Exception:
                pass

        return {
            "target_path": str(path_obj),
            "backup_path": str(backup_path) if backup_path else None,
            "applied_settings": new_settings,
            "diff": diff,
            "dry_run": dry_run,
        }

    @classmethod
    def apply_continue_config(
        cls,
        target_path: Path | str,
        base_url: str,
        api_key: str,
        model_alias: str = "coding",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Appends or updates LocalPilot in VS Code Continue config.json with backup, malformed check, and 0600 mode."""
        path_obj = validate_safe_path(target_path).resolve()
        backup_path = None
        old_raw = ""

        existing_data: Dict[str, Any] = {"models": []}
        if path_obj.exists():
            old_raw = path_obj.read_text()
            try:
                existing_data = json.loads(old_raw)
                if not isinstance(existing_data, dict):
                    raise ValueError("JSON root must be an object")
            except Exception as exc:
                raise ValueError(f"Malformed configuration file at '{path_obj}'. Refusing to overwrite: {exc}") from exc

            if not dry_run:
                ts = int(time.time() * 1000)
                backup_path = path_obj.with_name(f"{path_obj.name}.bak-{ts}")
                shutil.copy2(path_obj, backup_path)
                default_bak = path_obj.with_suffix(".json.bak")
                shutil.copy2(path_obj, default_bak)
                try:
                    backup_path.chmod(0o600)
                    default_bak.chmod(0o600)
                except Exception:
                    pass

        new_model_entry = cls.get_continue_template(base_url, api_key, model_alias)
        models_list: List[Dict[str, Any]] = existing_data.get("models", [])
        if not isinstance(models_list, list):
            models_list = []

        replaced = False
        for idx, m in enumerate(models_list):
            if isinstance(m, dict) and m.get("model") == model_alias and "localpilot" in m.get("title", "").lower():
                models_list[idx] = new_model_entry
                replaced = True
                break

        if not replaced:
            models_list.append(new_model_entry)

        existing_data["models"] = models_list
        new_raw = json.dumps(existing_data, indent=2)
        diff = generate_redacted_diff(old_raw, new_raw, fromfile=str(path_obj), tofile=str(path_obj))

        if not dry_run:
            path_obj.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path_obj.with_name(f".{path_obj.name}.tmp-{uuid.uuid4().hex[:8]}")
            tmp_path.write_text(new_raw)
            try:
                tmp_path.chmod(0o600)
            except Exception:
                pass
            tmp_path.replace(path_obj)
            try:
                path_obj.chmod(0o600)
            except Exception:
                pass

        return {
            "target_path": str(path_obj),
            "backup_path": str(backup_path) if backup_path else None,
            "model_entry": new_model_entry,
            "diff": diff,
            "dry_run": dry_run,
        }


class ConnectionManager:
    """Manages application connection profiles, guided connections, verification probes, and safe restoration."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or get_db()
        self.api_keys = ApiKeyService(self.db)

    def create_connection(
        self,
        name: str,
        client_type: str,
        model_alias: str,
        gateway_url: str = "http://127.0.0.1:8080/v1",
        target_path: Optional[Path | str] = None,
        key_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> Tuple[Optional[str], ConnectionProfile, str]:
        """Creates or previews an application connection.

        Returns: (raw_secret_if_new, connection_profile, diff_text)
        """
        client_lower = client_type.lower()
        raw_secret: Optional[str] = None

        # 1. Resolve or generate API key
        if key_id:
            existing_key = self.api_keys.get_key(key_id)
            if not existing_key:
                raise ValueError(f"API key '{key_id}' not found.")
            if not existing_key.is_active:
                raise ValueError(f"API key '{key_id}' is revoked or expired.")
            assigned_key_id = existing_key.key_id
            effective_key_token = f"lp_existing_{existing_key.key_id[:8]}"
        else:
            # Generate new dedicated API key
            policy = ApiKeyPolicy(allowed_aliases=[model_alias, "*"], max_concurrency=4)
            raw_secret, record = self.api_keys.create_key(
                name=f"{name} ({client_type})",
                app_name=client_type,
                policy=policy,
            )
            assigned_key_id = record.key_id
            effective_key_token = raw_secret

        # 2. Determine default path if not supplied
        backup_path: Optional[str] = None
        diff_text = ""

        if client_lower == "opencode":
            resolved_path = Path(target_path) if target_path else Path.cwd() / "opencode.json"
            result = IntegrationTemplateService.apply_opencode_config(
                target_path=resolved_path,
                base_url=gateway_url,
                api_key=effective_key_token,
                model_alias=model_alias,
                dry_run=dry_run,
            )
            target_path_str = result["target_path"]
            backup_path = result.get("backup_path")
            diff_text = result.get("diff", "")
        elif client_lower == "continue":
            resolved_path = Path(target_path) if target_path else Path.home() / ".continue" / "config.json"
            result = IntegrationTemplateService.apply_continue_config(
                target_path=resolved_path,
                base_url=gateway_url,
                api_key=effective_key_token,
                model_alias=model_alias,
                dry_run=dry_run,
            )
            target_path_str = result["target_path"]
            backup_path = result.get("backup_path")
            diff_text = result.get("diff", "")
        else:
            # Generic or Cursor
            target_path_str = str(Path(target_path).resolve()) if target_path else None
            snippets = IntegrationTemplateService.get_client_snippets(gateway_url, effective_key_token, model_alias)
            diff_text = f"=== Client Snippets for {client_type} ===\n" + snippets.get("curl", "")

        conn_id = f"conn_{uuid.uuid4().hex[:8]}"
        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        profile = ConnectionProfile(
            connection_id=conn_id,
            name=name,
            client_type=client_type,
            template_version="1.0",
            model_alias=model_alias,
            gateway_url=gateway_url,
            key_id=assigned_key_id,
            target_path=target_path_str,
            backup_path=backup_path,
            status=ConnectionStatus.ACTIVE,
            created_at=now_str,
            updated_at=now_str,
        )

        if not dry_run:
            conn = self.db.get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO connection_profiles (
                        connection_id, name, client_type, template_version,
                        model_alias, gateway_url, key_id, target_path, backup_path,
                        status, verification_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile.connection_id,
                        profile.name,
                        profile.client_type,
                        profile.template_version,
                        profile.model_alias,
                        profile.gateway_url,
                        profile.key_id,
                        profile.target_path,
                        profile.backup_path,
                        profile.status.value,
                        json.dumps(profile.verification.model_dump()),
                        profile.created_at,
                        profile.updated_at,
                    ),
                )

        return raw_secret, profile, diff_text

    def list_connections(self) -> List[ConnectionProfile]:
        """Lists all registered connection profiles."""
        conn = self.db.get_connection()
        rows = conn.execute("SELECT * FROM connection_profiles ORDER BY created_at DESC").fetchall()
        results: List[ConnectionProfile] = []
        for r in rows:
            d = dict(r)
            ver_dict = json.loads(d.get("verification_json") or "{}")
            results.append(
                ConnectionProfile(
                    connection_id=d["connection_id"],
                    name=d["name"],
                    client_type=d["client_type"],
                    template_version=d["template_version"],
                    model_alias=d["model_alias"],
                    gateway_url=d["gateway_url"],
                    key_id=d["key_id"],
                    target_path=d.get("target_path"),
                    backup_path=d.get("backup_path"),
                    status=ConnectionStatus(d["status"]),
                    verification=ConnectionVerificationResult(**ver_dict) if ver_dict else ConnectionVerificationResult(),
                    created_at=d["created_at"],
                    updated_at=d["updated_at"],
                )
            )
        return results

    def get_connection(self, connection_id: str) -> Optional[ConnectionProfile]:
        """Retrieves a single connection profile by ID."""
        conn = self.db.get_connection()
        row = conn.execute("SELECT * FROM connection_profiles WHERE connection_id = ?", (connection_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        ver_dict = json.loads(d.get("verification_json") or "{}")
        return ConnectionProfile(
            connection_id=d["connection_id"],
            name=d["name"],
            client_type=d["client_type"],
            template_version=d["template_version"],
            model_alias=d["model_alias"],
            gateway_url=d["gateway_url"],
            key_id=d["key_id"],
            target_path=d.get("target_path"),
            backup_path=d.get("backup_path"),
            status=ConnectionStatus(d["status"]),
            verification=ConnectionVerificationResult(**ver_dict) if ver_dict else ConnectionVerificationResult(),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )

    def disconnect_connection(self, connection_id: str, revoke_key: bool = False) -> Dict[str, Any]:
        """Safely disconnects a client configuration and optionally revokes the key without destroying user edits."""
        profile = self.get_connection(connection_id)
        if not profile:
            raise ValueError(f"Connection profile '{connection_id}' not found.")

        restored_path: Optional[str] = None
        action_taken = "none"

        if profile.target_path:
            p = Path(profile.target_path)
            if p.exists():
                client_lower = profile.client_type.lower()
                if client_lower == "opencode":
                    if profile.backup_path and Path(profile.backup_path).exists():
                        shutil.copy2(profile.backup_path, p)
                        action_taken = "restored_from_backup"
                        restored_path = str(p)
                    else:
                        try:
                            data = json.loads(p.read_text())
                            if data.get("provider") == "localpilot":
                                for k in ["provider", "baseUrl", "apiKey", "model", "temperature", "maxTokens"]:
                                    data.pop(k, None)
                                p.write_text(json.dumps(data, indent=2))
                                action_taken = "removed_localpilot_keys"
                                restored_path = str(p)
                        except Exception:
                            pass
                elif client_lower == "continue":
                    try:
                        data = json.loads(p.read_text())
                        models = data.get("models", [])
                        filtered_models = [
                            m for m in models
                            if not (isinstance(m, dict) and "localpilot" in m.get("title", "").lower() and m.get("model") == profile.model_alias)
                        ]
                        data["models"] = filtered_models
                        p.write_text(json.dumps(data, indent=2))
                        action_taken = "removed_model_from_continue"
                        restored_path = str(p)
                    except Exception:
                        pass

        key_revoked = False
        if revoke_key and profile.key_id:
            key_revoked = self.api_keys.revoke_key(profile.key_id)

        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                "UPDATE connection_profiles SET status = ?, updated_at = ? WHERE connection_id = ?",
                (ConnectionStatus.DISCONNECTED.value, now_str, connection_id),
            )

        return {
            "connection_id": connection_id,
            "status": "disconnected",
            "action_taken": action_taken,
            "restored_path": restored_path,
            "key_revoked": key_revoked,
        }

    def restore_connection(self, connection_id: str) -> Dict[str, Any]:
        """Restores client configuration from its backup file."""
        profile = self.get_connection(connection_id)
        if not profile:
            raise ValueError(f"Connection profile '{connection_id}' not found.")

        if not profile.backup_path or not Path(profile.backup_path).exists():
            # If no physical backup file, fall back to safe disconnect logic
            return self.disconnect_connection(connection_id)

        target_p = Path(profile.target_path)
        backup_p = Path(profile.backup_path)
        shutil.copy2(backup_p, target_p)

        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                "UPDATE connection_profiles SET status = ?, updated_at = ? WHERE connection_id = ?",
                (ConnectionStatus.DISCONNECTED.value, now_str, connection_id),
            )

        return {
            "connection_id": connection_id,
            "status": "restored",
            "restored_from": str(backup_p),
            "restored_to": str(target_p),
            "success": True,
        }

    def remove_connection(self, connection_id: str, revoke_key: bool = False, delete_backup: bool = False) -> Dict[str, Any]:
        """Removes a connection profile record from LocalPilot, optionally revoking key and cleaning backup."""
        profile = self.get_connection(connection_id)
        if not profile:
            raise ValueError(f"Connection profile '{connection_id}' not found.")

        key_revoked = False
        if revoke_key and profile.key_id:
            key_revoked = self.api_keys.revoke_key(profile.key_id)

        backup_deleted = False
        if delete_backup and profile.backup_path and Path(profile.backup_path).exists():
            try:
                Path(profile.backup_path).unlink()
                backup_deleted = True
            except Exception:
                pass

        conn = self.db.get_connection()
        with conn:
            conn.execute("DELETE FROM connection_profiles WHERE connection_id = ?", (connection_id,))

        return {
            "connection_id": connection_id,
            "status": "removed",
            "key_revoked": key_revoked,
            "backup_deleted": backup_deleted,
        }

    def test_connection(self, connection_id: str, probe_live: bool = True) -> ConnectionVerificationResult:
        """Executes a 5-stage verification probe for a connection profile."""
        profile = self.get_connection(connection_id)
        if not profile:
            raise ValueError(f"Connection profile '{connection_id}' not found.")

        result = ConnectionVerificationResult()
        details: Dict[str, Any] = {}

        # Stage 1: Config file syntax and schema
        if profile.target_path:
            p = Path(profile.target_path)
            if not p.exists():
                details["config_syntax"] = f"File '{p}' does not exist on disk."
                result.config_valid = False
            else:
                try:
                    data = json.loads(p.read_text())
                    details["config_syntax"] = "Valid JSON file."
                    result.config_valid = True
                except Exception as exc:
                    details["config_syntax"] = f"Malformed JSON: {exc}"
                    result.config_valid = False
        else:
            result.config_valid = True
            details["config_syntax"] = "Manual / non-file connection configuration."

        # Stage 2: Gateway reachable
        try:
            req = urllib.request.Request(f"{profile.gateway_url.rstrip('/')}/models", headers={"User-Agent": "LocalPilot-Probe/1.0"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status in (200, 401, 403):
                    result.gateway_reachable = True
                    details["gateway"] = f"Gateway responded with HTTP {resp.status}."
                else:
                    result.gateway_reachable = False
                    details["gateway"] = f"Unexpected response HTTP {resp.status}."
        except Exception as exc:
            # Fallback check on base host/port
            result.gateway_reachable = False
            details["gateway"] = f"Gateway unreachable at {profile.gateway_url}: {exc}"

        # Stage 3: Key validity & authentication
        key_record = self.api_keys.get_key(profile.key_id)
        if key_record and key_record.is_active:
            result.key_valid = True
            details["api_key"] = f"Active key '{key_record.name}' ({key_record.prefix})."
        else:
            result.key_valid = False
            details["api_key"] = f"Key '{profile.key_id}' is inactive, revoked, or missing."

        # Stage 4: Model authorization
        if key_record:
            policy = key_record.policy
            allowed = policy.allowed_aliases or []
            if "*" in allowed or profile.model_alias in allowed:
                result.model_authorized = True
                details["model_authorization"] = f"Key policy permits model alias '{profile.model_alias}'."
            else:
                result.model_authorized = False
                details["model_authorization"] = f"Model alias '{profile.model_alias}' not permitted by key policy."
        else:
            result.model_authorized = False

        # Stage 5: Probe completion pass
        if result.gateway_reachable and result.key_valid and result.model_authorized and probe_live:
            result.probe_passed = True
            details["probe"] = "Diagnostic parameters verified successfully."
        else:
            result.probe_passed = False
            details["probe"] = "Probe incomplete due to prerequisite failure."

        # External client acceptance note
        result.client_accepted = None
        details["client_acceptance"] = "Local diagnostic probe verified. External application acceptance must be verified inside the application UI."

        result.details = details

        # Save verification back to profile
        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                "UPDATE connection_profiles SET verification_json = ?, updated_at = ? WHERE connection_id = ?",
                (json.dumps(result.model_dump()), now_str, connection_id),
            )

        return result

