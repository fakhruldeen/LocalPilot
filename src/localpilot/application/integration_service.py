"""Guided integration configuration generator for supported local developer applications."""

from __future__ import annotations
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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

    @classmethod
    def apply_opencode_config(
        cls,
        target_path: Path,
        base_url: str,
        api_key: str,
        model_alias: str = "coding",
    ) -> Dict[str, Any]:
        """Writes or merges opencode.json with automatic backup and diff reporting."""
        target_path = target_path.resolve()
        backup_path = None

        existing_data: Dict[str, Any] = {}
        if target_path.exists():
            backup_path = target_path.with_suffix(".json.bak")
            shutil.copy2(target_path, backup_path)
            try:
                existing_data = json.loads(target_path.read_text())
            except Exception:
                existing_data = {}

        new_settings = cls.get_opencode_template(base_url, api_key, model_alias)
        merged = {**existing_data, **new_settings}

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps(merged, indent=2))

        return {
            "target_path": str(target_path),
            "backup_path": str(backup_path) if backup_path else None,
            "applied_settings": new_settings,
        }

    @classmethod
    def apply_continue_config(
        cls,
        target_path: Path,
        base_url: str,
        api_key: str,
        model_alias: str = "coding",
    ) -> Dict[str, Any]:
        """Appends or updates LocalPilot in VS Code Continue config.json with backup."""
        target_path = target_path.resolve()
        backup_path = None

        existing_data: Dict[str, Any] = {"models": []}
        if target_path.exists():
            backup_path = target_path.with_suffix(".json.bak")
            shutil.copy2(target_path, backup_path)
            try:
                existing_data = json.loads(target_path.read_text())
            except Exception:
                existing_data = {"models": []}

        new_model_entry = cls.get_continue_template(base_url, api_key, model_alias)
        models_list: List[Dict[str, Any]] = existing_data.get("models", [])

        # Check if already present and replace, otherwise append
        replaced = False
        for idx, m in enumerate(models_list):
            if m.get("model") == model_alias and "localpilot" in m.get("title", "").lower():
                models_list[idx] = new_model_entry
                replaced = True
                break

        if not replaced:
            models_list.append(new_model_entry)

        existing_data["models"] = models_list

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps(existing_data, indent=2))

        return {
            "target_path": str(target_path),
            "backup_path": str(backup_path) if backup_path else None,
            "model_entry": new_model_entry,
        }
