"""Automatic startup service managing user-level init integration for Linux and macOS.

Strict safety invariants:
- Never requests root/sudo access.
- Only targets user-level paths (~/.config/systemd/user or ~/Library/LaunchAgents).
- Supports dry-run preview and atomic configuration file generation.
"""

from __future__ import annotations
import os
import platform
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from localpilot.storage.db import get_default_log_dir


class StartupService:
    """Manages user-level init services for starting LocalPilot on user login."""

    def __init__(self, platform_name: Optional[str] = None) -> None:
        if platform_name:
            self.platform = platform_name.lower()
        else:
            self.platform = "darwin" if sys.platform == "darwin" else ("linux" if sys.platform.startswith("linux") else "unsupported")

    def get_service_file_path(self) -> Path:
        """Returns the user-space service or agent file path."""
        if self.platform == "darwin":
            return Path.home() / "Library" / "LaunchAgents" / "com.localpilot.gateway.plist"
        elif self.platform == "linux":
            return Path.home() / ".config" / "systemd" / "user" / "localpilot-gateway.service"
        else:
            return Path.home() / ".localpilot" / "startup" / "gateway.service"

    def get_executable_command(self) -> str:
        """Determines the exact user-level executable command to run LocalPilot gateway."""
        py_path = sys.executable
        return f"{py_path} -m localpilot gateway start"

    def generate_service_definition(self, exec_cmd: Optional[str] = None) -> str:
        """Generates unit file or plist content."""
        cmd = exec_cmd or self.get_executable_command()
        log_dir = get_default_log_dir()

        if self.platform == "linux":
            return f"""[Unit]
Description=LocalPilot Local Inference Gateway
Documentation=https://github.com/fakhruldeen/LocalPilot
After=network.target

[Service]
Type=simple
ExecStart={cmd}
Restart=on-failure
RestartSec=5s
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:{log_dir / 'gateway-service.log'}
StandardError=append:{log_dir / 'gateway-service.err'}

[Install]
WantedBy=default.target
"""
        elif self.platform == "darwin":
            cmd_parts = cmd.split()
            args_xml = "\n".join(f"        <string>{p}</string>" for p in cmd_parts)
            return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.localpilot.gateway</string>
    <key>ProgramArguments</key>
    <array>
{args_xml}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>{log_dir / 'gateway-launchd.log'}</string>
    <key>StandardErrorPath</key>
    <string>{log_dir / 'gateway-launchd.err'}</string>
</dict>
</plist>
"""
        else:
            return f"# Automatic startup unsupported on {self.platform}\n# Command: {cmd}\n"

    def preview(self) -> Dict[str, Any]:
        """Returns the generated unit file and target path without touching the filesystem."""
        path = self.get_service_file_path()
        content = self.generate_service_definition()
        return {
            "platform": self.platform,
            "target_path": str(path),
            "content": content,
            "supported": self.platform in ("linux", "darwin"),
        }

    def get_status(self) -> Dict[str, Any]:
        """Checks the current installation and execution status of the startup service."""
        path = self.get_service_file_path()
        installed = path.exists()
        enabled = False
        active = False
        message = ""

        if not installed:
            message = "Startup service is not installed."
            return {
                "platform": self.platform,
                "installed": False,
                "enabled": False,
                "active": False,
                "path": str(path),
                "message": message,
            }

        if self.platform == "linux":
            try:
                res_enabled = subprocess.run(
                    ["systemctl", "--user", "is-enabled", "localpilot-gateway.service"],
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                )
                enabled = res_enabled.stdout.strip() == "enabled"

                res_active = subprocess.run(
                    ["systemctl", "--user", "is-active", "localpilot-gateway.service"],
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                )
                active = res_active.stdout.strip() == "active"
                message = f"systemd user service: enabled={enabled}, active={active}"
            except Exception as exc:
                message = f"Unit installed at {path}, but systemctl inspection failed: {exc}"
        elif self.platform == "darwin":
            try:
                res = subprocess.run(
                    ["launchctl", "list"],
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                )
                if "com.localpilot.gateway" in res.stdout:
                    enabled = True
                    active = True
                    message = "launchd agent loaded and active."
                else:
                    enabled = True
                    active = False
                    message = "launchd plist installed but not currently loaded."
            except Exception as exc:
                message = f"LaunchAgent plist installed at {path}, but launchctl check failed: {exc}"
        else:
            message = f"Startup service not supported on {self.platform}."

        return {
            "platform": self.platform,
            "installed": installed,
            "enabled": enabled,
            "active": active,
            "path": str(path),
            "message": message,
        }

    def enable(self) -> Dict[str, Any]:
        """Installs and enables the startup service."""
        if self.platform not in ("linux", "darwin"):
            raise ValueError(f"Automatic startup is not supported on platform '{self.platform}'.")

        path = self.get_service_file_path()
        content = self.generate_service_definition()

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex[:8]}")
        tmp_path.write_text(content)
        try:
            tmp_path.chmod(0o644)
        except Exception:
            pass
        tmp_path.replace(path)

        success = True
        system_out = ""

        if self.platform == "linux":
            try:
                subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True, timeout=5.0)
                res = subprocess.run(["systemctl", "--user", "enable", "localpilot-gateway.service"], capture_output=True, text=True, timeout=5.0)
                system_out = res.stdout.strip() or res.stderr.strip()
            except Exception as exc:
                system_out = f"Written unit file, but systemctl enable failed: {exc}"
                success = False
        elif self.platform == "darwin":
            try:
                res = subprocess.run(["launchctl", "load", "-w", str(path)], capture_output=True, text=True, timeout=5.0)
                system_out = res.stdout.strip() or res.stderr.strip()
            except Exception as exc:
                system_out = f"Written plist file, but launchctl load failed: {exc}"
                success = False

        return {
            "platform": self.platform,
            "path": str(path),
            "enabled": success,
            "system_output": system_out,
        }

    def disable(self) -> Dict[str, Any]:
        """Disables and removes the startup service."""
        path = self.get_service_file_path()
        system_out = ""

        if self.platform == "linux":
            try:
                subprocess.run(["systemctl", "--user", "stop", "localpilot-gateway.service"], capture_output=True, timeout=5.0)
                subprocess.run(["systemctl", "--user", "disable", "localpilot-gateway.service"], capture_output=True, timeout=5.0)
                subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=5.0)
            except Exception as exc:
                system_out += f"systemctl stop/disable note: {exc} "
        elif self.platform == "darwin":
            if path.exists():
                try:
                    subprocess.run(["launchctl", "unload", "-w", str(path)], capture_output=True, timeout=5.0)
                except Exception as exc:
                    system_out += f"launchctl unload note: {exc} "

        if path.exists():
            path.unlink()

        return {
            "platform": self.platform,
            "path": str(path),
            "disabled": True,
            "system_output": system_out.strip(),
        }
