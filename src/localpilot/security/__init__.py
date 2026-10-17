"""Security helpers: path sanitization, safe extraction, localhost binding checks, and keyring."""

from __future__ import annotations
import ipaddress
import json
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


_FORBIDDEN_METADATA_IPS = {"169.254.169.254", "metadata.google.internal"}
MAX_EXTRACT_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB
MAX_MEMBER_COUNT = 10_000


def validate_download_url(url: str) -> bool:
    """Ensures remote download URLs use HTTPS and do not target private/internal networks or cloud metadata."""
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise SecurityViolationError(f"Insecure scheme: downloads must use HTTPS, got {url}")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise SecurityViolationError(f"Invalid download URL (missing host): {url}")

    if hostname in _FORBIDDEN_METADATA_IPS:
        raise SecurityViolationError(f"Access to cloud metadata endpoint forbidden: {hostname}")

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        # Hostname is a domain name, not a raw IP
        ip = None

    if ip is not None:
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise SecurityViolationError(f"Access to private or local network address forbidden: {hostname}")

    return True


def sanitize_artifact_subpath(model_id: str, revision: Optional[str], filename: str) -> Tuple[str, str]:
    """Sanitizes model repo, revision, and filename into a secure relative subpath.

    Rejects path traversal, slashes in filename, absolute paths, or null bytes.
    Returns (dir_slug, safe_filename).
    """
    if not filename or filename in (".", "..") or "\x00" in filename:
        raise SecurityViolationError(f"Invalid or unsafe artifact filename: {filename!r}")
    if "/" in filename or "\\" in filename:
        raise SecurityViolationError(f"Filename must not contain directory slashes or path traversal: {filename!r}")

    clean_filename = Path(filename).name
    if not clean_filename or clean_filename in (".", "..") or "\x00" in clean_filename:
        raise SecurityViolationError(f"Invalid or unsafe artifact filename: {filename!r}")

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
        if hasattr(tarfile, "data_filter"):
            tar.extractall(path=t_dir, filter="data")
        else:
            tar.extractall(path=t_dir)


def safe_extract_zip(
    zip_path: Path | str,
    target_dir: Path | str,
    max_total_bytes: int = MAX_EXTRACT_BYTES,
    max_members: int = MAX_MEMBER_COUNT,
) -> None:
    """Safely extracts a zip archive ensuring no member escapes target directory

    and protecting against zip bombs.
    """
    t_dir = Path(target_dir).resolve()
    t_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        infolist = z.infolist()
        if len(infolist) > max_members:
            raise SecurityViolationError(
                f"Zip archive exceeds maximum member count: {len(infolist)} > {max_members}"
            )
        total_uncompressed = sum(e.file_size for e in infolist)
        if total_uncompressed > max_total_bytes:
            raise SecurityViolationError(
                f"Zip archive uncompressed size exceeds limit: {total_uncompressed} > {max_total_bytes}"
            )

        for e in infolist:
            name = e.filename
            if name.startswith("/") or ".." in Path(name).parts:
                raise SecurityViolationError(f"Path traversal member in zip archive: {name}")
            member_path = t_dir / name
            validate_path_within_root(member_path, t_dir)
        z.extractall(path=t_dir)



def _get_fallback_credentials_file() -> Path:
    data_dir = os.environ.get("LOCALPILOT_DATA_DIR", "~/.localpilot")
    p = Path(os.path.expanduser(data_dir)).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p / "credentials.json"


def get_stored_secret(service: str, username: str) -> Optional[str]:
    """Retrieves a secret from OS keyring safely, falling back to secure credentials file."""
    try:
        val = keyring.get_password(service, username)
        if val is not None:
            return val
    except Exception:
        pass

    try:
        cred_file = _get_fallback_credentials_file()
        if cred_file.exists():
            data = json.loads(cred_file.read_text("utf-8"))
            return data.get(f"{service}:{username}")
    except Exception:
        pass
    return None


def store_secret(service: str, username: str, secret: str) -> bool:
    """Stores a secret in OS keyring safely, falling back to secure credentials file with 0600 permissions."""
    stored_in_keyring = False
    try:
        keyring.set_password(service, username, secret)
        stored_in_keyring = True
    except Exception:
        stored_in_keyring = False

    try:
        cred_file = _get_fallback_credentials_file()
        data = {}
        if cred_file.exists():
            try:
                data = json.loads(cred_file.read_text("utf-8"))
            except Exception:
                data = {}
        data[f"{service}:{username}"] = secret

        tmp_file = cred_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(data), "utf-8")
        os.chmod(tmp_file, 0o600)
        tmp_file.replace(cred_file)
        os.chmod(cred_file, 0o600)
        return True
    except Exception:
        return stored_in_keyring


def delete_stored_secret(service: str, username: str) -> bool:
    """Deletes a secret from OS keyring safely and fallback credentials file."""
    deleted = False
    try:
        keyring.delete_password(service, username)
        deleted = True
    except Exception:
        pass

    try:
        cred_file = _get_fallback_credentials_file()
        if cred_file.exists():
            data = json.loads(cred_file.read_text("utf-8"))
            key = f"{service}:{username}"
            if key in data:
                del data[key]
                tmp_file = cred_file.with_suffix(".tmp")
                tmp_file.write_text(json.dumps(data), "utf-8")
                os.chmod(tmp_file, 0o600)
                tmp_file.replace(cred_file)
                os.chmod(cred_file, 0o600)
                deleted = True
    except Exception:
        pass
    return deleted
