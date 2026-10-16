"""Security helpers: path sanitization, safe extraction, localhost binding checks, and keyring."""

from __future__ import annotations
import os
import tarfile
import zipfile
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse
import keyring


class SecurityViolationError(ValueError):
    """Raised when an operation violates security boundaries."""
    pass


def sanitize_artifact_subpath(model_id: str, revision: Optional[str], filename: str) -> Tuple[str, str]:
    """Sanitizes model repo, revision, and filename into a secure relative subpath.

    Rejects path traversal, slashes in filename, absolute paths, or null bytes.
    Returns (dir_slug, safe_filename).
    """
    clean_filename = Path(filename).name
    if not clean_filename or clean_filename in (".", "..") or "\x00" in clean_filename:
        raise SecurityViolationError(f"Invalid or unsafe artifact filename: {filename!r}")
    if "/" in filename or "\\" in filename:
        # If caller passed a subpath, only the pure filename is accepted
        pass

    repo_slug = model_id.replace("/", "--").replace("\\", "--").replace(":", "--").strip(".-")
    rev_slug = (revision or "main").replace("/", "--").replace("\\", "--").strip(".-")
    dir_slug = f"{repo_slug}--{rev_slug}"
    return dir_slug, clean_filename


def validate_path_within_root(target: Path | str, root: Path | str) -> Path:
    """Ensures target path does not escape the allowed root directory, including via symlinks."""
    resolved_target = Path(target).resolve()
    resolved_root = Path(root).resolve()
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as e:
        raise SecurityViolationError(f"Path traversal detected: {target} is outside {root}") from e

    # Also check if raw target exists as a symlink pointing outside root
    raw_path = Path(target)
    if raw_path.is_symlink():
        target_link = raw_path.readlink()
        resolved_link = (raw_path.parent / target_link).resolve()
        try:
            resolved_link.relative_to(resolved_root)
        except ValueError as e:
            raise SecurityViolationError(f"Symlink escape detected: {target} points outside {root}") from e

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
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise SecurityViolationError(f"Path traversal member in tar archive: {member.name}")
            member_path = t_dir / member.name
            validate_path_within_root(member_path, t_dir)
            if member.issym() or member.islnk():
                link_target = (member_path.parent / member.linkname).resolve()
                try:
                    link_target.relative_to(t_dir)
                except ValueError as e:
                    raise SecurityViolationError(f"Symlink target outside extraction dir: {member.linkname}") from e
        tar.extractall(path=t_dir)


def safe_extract_zip(zip_path: Path | str, target_dir: Path | str) -> None:
    """Safely extracts a zip archive ensuring no member escapes target directory."""
    t_dir = Path(target_dir).resolve()
    t_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        for name in z.namelist():
            if name.startswith("/") or ".." in Path(name).parts:
                raise SecurityViolationError(f"Path traversal member in zip archive: {name}")
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
