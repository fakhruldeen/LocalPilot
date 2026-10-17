"""Targeted security regression and boundary tests for LocalPilot audit."""

from __future__ import annotations
import io
import os
import tarfile
import zipfile
from pathlib import Path
import pytest

from localpilot.observability.redaction import redact_data, redact_string
from localpilot.runtimes.base import InstallationScope, RuntimeInstallPlan
from localpilot.runtimes.llamacpp import LlamaCppRuntimeAdapter
from localpilot.security import (
    SecurityViolationError,
    safe_extract_tar,
    safe_extract_zip,
    sanitize_artifact_subpath,
    validate_download_url,
    validate_localhost_endpoint,
    validate_path_within_root,
)


# =============================================================================
# 1. Path Sanitization & Traversal Defense
# =============================================================================

def test_sanitize_artifact_subpath_null_byte_and_empty() -> None:
    """Rejects null bytes and empty names in artifact filenames."""
    with pytest.raises(SecurityViolationError, match="Invalid or unsafe artifact filename"):
        sanitize_artifact_subpath("repo/model", "v1", "")

    with pytest.raises(SecurityViolationError, match="Invalid or unsafe artifact filename"):
        sanitize_artifact_subpath("repo/model", "v1", "model\x00.gguf")


def test_sanitize_artifact_subpath_rejects_slashes() -> None:
    """Verifies that sanitize_artifact_subpath rejects filenames with slashes or traversal."""
    with pytest.raises(SecurityViolationError, match="directory slashes or path traversal"):
        sanitize_artifact_subpath("repo/model", "v1", "../../sub/model.gguf")

    with pytest.raises(SecurityViolationError, match="directory slashes or path traversal"):
        sanitize_artifact_subpath("repo/model", "v1", "sub/model.gguf")


# =============================================================================
# 2. Download URL & SSRF Defense
# =============================================================================

def test_validate_download_url_enforces_https_and_blocks_ssrf() -> None:
    """Ensures download URLs require HTTPS and reject private/cloud metadata IPs."""
    assert validate_download_url("https://huggingface.co/repo/model.gguf") is True

    # Reject plaintext HTTP
    with pytest.raises(SecurityViolationError, match="must use HTTPS"):
        validate_download_url("http://huggingface.co/repo/model.gguf")

    # Reject cloud metadata IP
    with pytest.raises(SecurityViolationError, match="cloud metadata endpoint"):
        validate_download_url("https://169.254.169.254/latest/meta-data")

    # Reject private RFC 1918 IPs
    with pytest.raises(SecurityViolationError, match="private or local network"):
        validate_download_url("https://192.168.1.1/model.gguf")

    with pytest.raises(SecurityViolationError, match="private or local network"):
        validate_download_url("https://10.0.0.1/model.gguf")

    # Reject loopback IP
    with pytest.raises(SecurityViolationError, match="private or local network"):
        validate_download_url("https://127.0.0.1/model.gguf")


# =============================================================================
# 3. Zip Bomb & Resource Exhaustion Defense
# =============================================================================

def test_safe_extract_zip_rejects_oversized_archive(tmp_path: Path) -> None:
    """Verifies safe_extract_zip rejects archives exceeding max_total_bytes."""
    zip_path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("large.txt", b"A" * 5000)

    dest = tmp_path / "extract_dest"
    with pytest.raises(SecurityViolationError, match="uncompressed size exceeds limit"):
        safe_extract_zip(zip_path, dest, max_total_bytes=1000)


def test_validate_path_within_root_rejects_escape(tmp_path: Path) -> None:
    """Ensures target path cannot escape the allowed root directory."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("sensitive")

    with pytest.raises(SecurityViolationError, match="outside"):
        validate_path_within_root(root / "../outside.txt", root)


def test_validate_path_within_root_rejects_symlink_escape(tmp_path: Path) -> None:
    """Ensures symlink pointing outside root is detected and rejected."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("sensitive")

    symlink_path = root / "link.txt"
    symlink_path.symlink_to(outside)

    with pytest.raises(SecurityViolationError, match="outside"):
        validate_path_within_root(symlink_path, root)


# =============================================================================
# 2. Archive Extraction Security
# =============================================================================

def test_safe_extract_tar_rejects_traversal_member(tmp_path: Path) -> None:
    """Verifies that safe_extract_tar blocks tar members with ../ traversal."""
    tar_path = tmp_path / "malicious.tar"
    with tarfile.open(tar_path, "w") as tar:
        ti = tarfile.TarInfo(name="../escape.txt")
        data = b"EVIL"
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))

    dest = tmp_path / "extract_dest"
    with pytest.raises(SecurityViolationError, match="Path traversal member in tar archive"):
        safe_extract_tar(tar_path, dest)


def test_safe_extract_zip_rejects_traversal_member(tmp_path: Path) -> None:
    """Verifies that safe_extract_zip blocks zip members with ../ traversal."""
    zip_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("../escape.txt", b"EVIL")

    dest = tmp_path / "extract_dest"
    with pytest.raises(SecurityViolationError, match="Path traversal member in zip archive"):
        safe_extract_zip(zip_path, dest)


# =============================================================================
# 3. Localhost Endpoint Enforcement
# =============================================================================

def test_validate_localhost_endpoint_enforcement() -> None:
    """Ensures non-localhost endpoints are rejected."""
    assert validate_localhost_endpoint("http://127.0.0.1:8080") is True
    assert validate_localhost_endpoint("http://localhost:11434") is True
    assert validate_localhost_endpoint("http://[::1]:1234") is True

    # External IP and domain rejection
    with pytest.raises(SecurityViolationError, match="must bind to localhost"):
        validate_localhost_endpoint("http://192.168.1.1:8080")

    with pytest.raises(SecurityViolationError, match="must bind to localhost"):
        validate_localhost_endpoint("http://api.huggingface.co/endpoint")

    with pytest.raises(SecurityViolationError, match="must bind to localhost"):
        validate_localhost_endpoint("http://169.254.169.254/latest/meta-data")


# =============================================================================
# 4. Secret Redaction & PII Protection
# =============================================================================

def test_redact_secrets_in_strings_and_dictionaries() -> None:
    """Verifies complete redaction of API keys, bearer tokens, and home paths."""
    home_dir = os.path.expanduser("~")
    raw_str = f"Connecting from {home_dir}/models with token=hf_abcdef1234567890abcdef123456"
    redacted = redact_string(raw_str)

    assert home_dir not in redacted
    assert "~" in redacted
    assert "hf_abcdef" not in redacted
    assert "***[REDACTED]***" in redacted

    payload = {
        "user_prompt": "Tell me a secret",
        "api_key": "sk-secret-123456789",
        "metadata": {"path": f"{home_dir}/file.gguf", "status": "ok"},
    }
    redacted_payload = redact_data(payload)
    assert redacted_payload["user_prompt"] == "***[REDACTED]***"
    assert redacted_payload["api_key"] == "***[REDACTED]***"
    assert home_dir not in redacted_payload["metadata"]["path"]


def test_credential_storage_fallback_on_headless_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verifies that in headless environments without OS keyring, credentials fall back to a secure 0600 file."""
    import stat
    import keyring
    from localpilot.security import delete_stored_secret, get_stored_secret, store_secret

    monkeypatch.setenv("LOCALPILOT_DATA_DIR", str(tmp_path))

    def mock_fail(*args, **kwargs):
        raise RuntimeError("No DBus/SecretService daemon available")

    monkeypatch.setattr(keyring, "get_password", mock_fail)
    monkeypatch.setattr(keyring, "set_password", mock_fail)
    monkeypatch.setattr(keyring, "delete_password", mock_fail)

    # Storing secret should succeed via fallback file
    assert store_secret("localpilot", "hf_token", "hf_test_secret_12345") is True

    cred_file = tmp_path / "credentials.json"
    assert cred_file.exists()
    file_mode = stat.S_IMODE(cred_file.stat().st_mode)
    assert file_mode == 0o600

    # Reading secret should retrieve from fallback file
    retrieved = get_stored_secret("localpilot", "hf_token")
    assert retrieved == "hf_test_secret_12345"

    # Deleting secret should purge from fallback file
    assert delete_stored_secret("localpilot", "hf_token") is True
    assert get_stored_secret("localpilot", "hf_token") is None


def test_install_script_symlink_safety_avoids_overwriting_binary(tmp_path: Path) -> None:
    """Verifies that installer convenience symlink logic will not overwrite an existing non-symlink file."""
    import subprocess

    user_local_bin = tmp_path / "bin"
    user_local_bin.mkdir(parents=True)
    target_binary = user_local_bin / "localpilot"
    target_binary.write_text("existing_custom_tool", "utf-8")

    launcher = tmp_path / "launcher_script"
    launcher.write_text("#!/bin/sh\necho localpilot", "utf-8")

    # Run the exact bash snippet used in install-localpilot.sh
    script = f"""
    USER_LOCAL_BIN="{user_local_bin}"
    BIN_DIR="{tmp_path / 'other'}"
    LAUNCHER="{launcher}"
    if [ -d "$USER_LOCAL_BIN" ] && [ "$BIN_DIR" != "$USER_LOCAL_BIN" ]; then
      TARGET_LINK="$USER_LOCAL_BIN/localpilot"
      if [ -e "$TARGET_LINK" ] && [ ! -L "$TARGET_LINK" ]; then
        echo "SKIPPED_DUE_TO_EXISTING_FILE"
      else
        ln -sf "$LAUNCHER" "$TARGET_LINK" 2>/dev/null || true
      fi
    fi
    """
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    assert "SKIPPED_DUE_TO_EXISTING_FILE" in res.stdout
    # Ensure existing file was NOT overwritten with symlink
    assert not target_binary.is_symlink()
    assert target_binary.read_text("utf-8") == "existing_custom_tool"


