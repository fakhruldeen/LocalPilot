"""Redaction of tokens, keys, passwords, user prompts, and private paths."""

from __future__ import annotations
import os
import re
from typing import Any, Dict, List, Union

# Pattern for Hugging Face tokens
_HF_TOKEN_RE = re.compile(r"(hf_[a-zA-Z0-9]{34,})", re.IGNORECASE)
# Generic bearer/API tokens
_BEARER_RE = re.compile(r"(bearer\s+)([a-zA-Z0-9_\-\.]{16,})", re.IGNORECASE)
# Key/secret field names
_SENSITIVE_KEYS = {
    "password", "secret", "token", "api_key", "authorization", "auth",
    "private_key", "access_token", "user_prompt", "model_response"
}


def redact_string(val: str) -> str:
    """Redacts secrets and home directory paths from a string."""
    if not val:
        return val

    # Redact HF tokens
    val = _HF_TOKEN_RE.sub("hf_***[REDACTED]***", val)
    # Redact Bearer tokens
    val = _BEARER_RE.sub(r"\1***[REDACTED]***", val)

    # Redact home path
    home = os.path.expanduser("~")
    if home and home != "/":
        val = val.replace(home, "~")

    return val


def redact_data(obj: Any) -> Any:
    """Recursively redacts sensitive keys and values in mappings and lists."""
    if isinstance(obj, str):
        return redact_string(obj)
    if isinstance(obj, dict):
        result: Dict[str, Any] = {}
        for k, v in obj.items():
            if str(k).lower() in _SENSITIVE_KEYS:
                result[k] = "***[REDACTED]***"
            else:
                result[k] = redact_data(v)
        return result
    if isinstance(obj, list):
        return [redact_data(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(redact_data(item) for item in obj)
    return obj
