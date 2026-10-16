"""Security helpers: path sanitization, safe extraction, localhost binding checks, and keyring."""

from __future__ import annotations
import os
import tarfile
import zipfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
import keyring


class SecurityViolationError(ValueError):
    """Raised when an operation violates security boundaries."""
    pass


def validate_path_within_root(target: Path | str, root: Path | str) -> Path:
    """Ensures target path does not escape the allowed root directory."""
    resolved_target = Path(target).resolve()
    resolved_root = Path(root).resolve()
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as e:
        raise SecurityViolationError(f"Path traversal detected: {target} is outside {root}") from e
    return resolved_target


def validate_localhost_endpoint(url: str) -> bool:
    """Ensures external connections only target localhost."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname in ("127.0.0.1", "localhost", "::1"):
        return True
    raise SecurityViolationError(f"Security restriction: endpoints must bind to localhost, got {url}")


def safe_extract_tar(tar_path: Path | str, target_dir: Path | str) -> None:
    """Safely extracts a tar archive ensuring no member escapes target directory."""
    t_dir = Path(target_dir).resolve()
    t_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:*") as tar:
        for member in tar.getmembers():
            member_path = t_dir / member.name
            validate_path_within_root(member_path, t_dir)
        tar.extractall(path=t_dir)


def safe_extract_zip(zip_path: Path | str, target_dir: Path | str) -> None:
    """Safely extracts a zip archive ensuring no member escapes target directory."""
    t_dir = Path(target_dir).resolve()
    t_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        for name in z.namelist():
            member_path = t_dir / name
            validate_path_within_root(member_path, t_dir)
        z.extractall(path=t_dir)


def get_stored_secret(service: str, username: str) -> Optional[str]:
    """Retrieves a secret from OS keyring safely."""
    try:
        return keyring.get_password(service, username)
    except Exception:
        return None


def store_secret(service: str, username: str, secret: str) -> bool:
    """Stores a secret in OS keyring safely."""
    try:
        keyring.set_password(service, username, secret)
        return True
    except Exception:
        return False


def delete_stored_secret(service: str, username: str) -> bool:
    """Deletes a secret from OS keyring safely."""
    try:
        keyring.delete_password(service, username)
        return True
    except Exception:
        return False
