"""Platform factory and adapter discovery."""

import platform
from localpilot.platforms.base import PlatformAdapter
from localpilot.platforms.linux import LinuxPlatformAdapter
from localpilot.platforms.macos import MacOSPlatformAdapter
from localpilot.platforms.unix import UnixPlatformAdapter


def get_platform_adapter() -> PlatformAdapter:
    """Returns the appropriate platform adapter for current OS."""
    sys_name = platform.system().lower()
    if sys_name == "linux":
        return LinuxPlatformAdapter()
    elif sys_name == "darwin":
        return MacOSPlatformAdapter()
    else:
        return UnixPlatformAdapter()
