# LocalPilot Installation Guide

LocalPilot supports Linux and macOS. It can be installed in several convenient ways depending on your workflow.

---

## Method 1: Automated Shell Installer (Recommended for Beginners)

If you do not want to manage Python virtual environments manually, use our automated one-line installer:

```bash
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash
```

### What the installer does:
1. Detects your operating system (Linux or macOS) and architecture (`x86_64` or `arm64`).
2. Checks for Python 3.11+.
3. Creates an isolated environment in `~/.local/share/localpilot/app-env` (or macOS equivalent).
4. Installs the latest release of `localpilot` from PyPI without touching any system packages.
5. Creates a launcher at `~/.local/bin/localpilot`.

### Installer Options:
You can download and pass custom flags to the installer script:

```bash
# Check system readiness without installing anything
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash -s -- --check

# Non-interactive mode (accept defaults automatically)
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash -s -- -y

# Specify installation scope: isolated (default), user, or system
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash -s -- --scope user
```

---

## Method 2: Install via `pipx` (Recommended for Python Users)

[`pipx`](https://pypa.github.io/pipx/) installs CLI tools in isolated virtual environments while making them globally accessible on your PATH:

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
# or
uv pip install localpilot
```

---

## Post-Installation Verification

Verify your installation by running the hardware diagnostic doctor:

```bash
localpilot doctor
```

You should see your computer's hardware profile and safe memory budget.

---

## Uninstallation

LocalPilot respects your computer and leaves no hidden background processes.

### If installed via the shell installer:
```bash
# Uninstalls LocalPilot but keeps your downloaded model files safe
sh install-localpilot.sh --uninstall

# Uninstalls LocalPilot AND purges all downloaded models
sh install-localpilot.sh --uninstall --purge-models
```

### If installed via `pipx` or `pip`:
```bash
pipx uninstall localpilot
# or
pip uninstall localpilot
```

To remove LocalPilot configuration and cached catalog data:
```bash
rm -rf ~/.local/share/localpilot
```
