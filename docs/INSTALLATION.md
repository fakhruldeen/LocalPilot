# LocalPilot Installation & Removal Guide

This guide details installation options, environment scopes, and safe uninstallation procedures for Linux and macOS (and Windows via WSL2).

---

## Method 1: Automated Installer (Recommended)

For users who want a self-contained installation without configuring Python environments manually:

```bash
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash
```

### What the Installer Does
1. **Checks Compatibility**: Verifies that your operating system is Linux or macOS, and checks for Python 3.11+.
2. **Creates an Isolated App Directory**:
   * **Linux**: `~/.local/share/localpilot`
   * **macOS**: `~/Library/Application Support/LocalPilot`
3. **Installs LocalPilot**: Creates a dedicated Python virtual environment (`app-env`) inside that directory, installing required packages without modifying your system-wide Python environment.
4. **Sets Up the Launcher**: Creates an executable command at `~/.local/share/localpilot/bin/localpilot` and creates a convenience link at `~/.local/bin/localpilot` if available.
5. **No Administrator Privileges**: The installer runs entirely in unprivileged user space and never asks for `sudo` or root passwords.

### Non-Interactive and Custom Flags
You can pass arguments to the installer via `bash -s --`:

```bash
# Check system readiness without downloading packages
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash -s -- --check

# Non-interactive mode (automatically accept defaults)
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash -s -- -y

# Install to a specific scope: isolated (default), user, or system
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash -s -- --scope user
```

---

## Method 2: Install via `pipx` (For Python Users)

[`pipx`](https://pypa.github.io/pipx/) installs command-line applications in isolated virtual environments while exposing their binary on your PATH:

```bash
pipx install localpilot
```

To upgrade in the future:
```bash
pipx upgrade localpilot
```

---

## Method 3: Install via `pip` or `uv`

If you are using `pip`:
```bash
pip install localpilot
```

If you are using `uv`:
```bash
uv tool install localpilot
```

---

## Post-Installation Verification

Confirm that LocalPilot is ready by running the hardware diagnostic doctor:

```bash
localpilot doctor
```

This verifies your CPU, RAM budget, graphics detection, and storage paths.

---

## Safe Uninstallation & Removal

LocalPilot is designed to be cleanly removable without leaving hidden background daemons.

### Option A: Remove Application While Preserving Downloaded Models (Recommended)
If you wish to remove the LocalPilot software but keep your downloaded AI models safe on disk:

```bash
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash -s -- --uninstall
```

* **What is removed**: The launcher script and the isolated virtual environment (`app-env`).
* **What is kept**: Your downloaded model weight files in the `models/` directory remain untouched so you do not have to redownload gigabytes of data if you reinstall later.

If installed via `pipx` or `pip`:
```bash
pipx uninstall localpilot
# or
pip uninstall localpilot
```

---

### Option B: Complete Removal Including All Downloaded Models

> [!WARNING]
> This option permanently deletes all downloaded model files from your computer. This action cannot be undone and will require redownloading all models if you choose to use them again.

To remove both the application and purge all downloaded model weight files:

```bash
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash -s -- --uninstall --purge-models
```
