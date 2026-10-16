#!/bin/sh
# install-localpilot.sh - Installer for LocalPilot
# Usage:
#   sh install-localpilot.sh [OPTIONS]
# Options:
#   --scope isolated|user|system  Installation scope (default: isolated)
#   --prefix PATH                 Custom installation prefix
#   -y, --yes                     Non-interactive mode (accept defaults)
#   --check                       Check system compatibility without installing
#   --uninstall                   Uninstall LocalPilot application
#   --purge-models                Used with --uninstall to also delete models

set -e

SCOPE="isolated"
PREFIX=""
NON_INTERACTIVE=false
CHECK_ONLY=false
UNINSTALL=false
PURGE_MODELS=false

while [ $# -gt 0 ]; do
  case "$1" in
    --scope)
      SCOPE="$2"
      shift 2
      ;;
    --prefix)
      PREFIX="$2"
      shift 2
      ;;
    -y|--yes)
      NON_INTERACTIVE=true
      shift
      ;;
    --check)
      CHECK_ONLY=true
      shift
      ;;
    --uninstall)
      UNINSTALL=true
      shift
      ;;
    --purge-models)
      PURGE_MODELS=true
      shift
      ;;
    -h|--help)
      echo "LocalPilot Installer"
      echo "Usage: sh install-localpilot.sh [--scope isolated|user|system] [-y] [--check] [--uninstall]"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
  Linux)
    PLATFORM="linux"
    DEFAULT_APP_DIR="$HOME/.local/share/localpilot"
    DEFAULT_BIN_DIR="$HOME/.local/bin"
    ;;
  Darwin)
    PLATFORM="macos"
    DEFAULT_APP_DIR="$HOME/Library/Application Support/LocalPilot"
    DEFAULT_BIN_DIR="/usr/local/bin"
    [ -d "$HOME/.local/bin" ] && DEFAULT_BIN_DIR="$HOME/.local/bin"
    ;;
  *)
    PLATFORM="unsupported"
    ;;
esac

echo "============================================================"
echo "                   LocalPilot Installer                     "
echo "============================================================"
echo "Detected Platform: $OS ($ARCH)"
echo "Selected Scope   : $SCOPE"

if [ "$PLATFORM" = "unsupported" ]; then
  echo "Error: Operating system $OS is not officially supported."
  echo "LocalPilot supports Linux and macOS."
  exit 1
fi

# Determine target directories
case "$SCOPE" in
  isolated)
    APP_DIR="${PREFIX:-$DEFAULT_APP_DIR}"
    VENV_DIR="$APP_DIR/app-env"
    BIN_DIR="$APP_DIR/bin"
    LAUNCHER="$BIN_DIR/localpilot"
    ;;
  user)
    APP_DIR="${PREFIX:-$DEFAULT_APP_DIR}"
    VENV_DIR="$APP_DIR/app-env"
    BIN_DIR="${HOME}/.local/bin"
    LAUNCHER="$BIN_DIR/localpilot"
    ;;
  system)
    APP_DIR="/opt/localpilot"
    VENV_DIR="$APP_DIR/app-env"
    BIN_DIR="/usr/local/bin"
    LAUNCHER="$BIN_DIR/localpilot"
    ;;
  *)
    echo "Error: Invalid scope '$SCOPE'. Must be one of: isolated, user, system."
    exit 1
    ;;
esac

if [ "$UNINSTALL" = true ]; then
  echo "Uninstalling LocalPilot application..."
  if [ -f "$LAUNCHER" ]; then
    rm -f "$LAUNCHER"
    echo "Removed launcher: $LAUNCHER"
  fi
  if [ -d "$VENV_DIR" ]; then
    rm -rf "$VENV_DIR"
    echo "Removed application virtual environment: $VENV_DIR"
  fi
  if [ "$PURGE_MODELS" = true ]; then
    if [ -d "$APP_DIR/models" ]; then
      rm -rf "$APP_DIR/models"
      echo "Purged models: $APP_DIR/models"
    fi
  else
    echo "Notice: Model files preserved at $APP_DIR/models. (Use --purge-models to remove them)."
  fi
  echo "LocalPilot application uninstallation complete."
  exit 0
fi

# Check for Python 3.11+
PYTHON_BIN=""
for cand in python3 python3.14 python3.13 python3.12 python3.11; do
  if command -v "$cand" >/dev/null 2>&1; then
    VER="$($cand -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
    MAJOR="$(echo "$VER" | cut -d. -f1)"
    MINOR="$(echo "$VER" | cut -d. -f2)"
    if [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 11 ]; then
      PYTHON_BIN="$cand"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "Error: Python 3.11 or higher is required. Please install Python 3.11+."
  exit 1
fi

echo "Python Check     : Found $($PYTHON_BIN --version) ($PYTHON_BIN)"

if [ "$CHECK_ONLY" = true ]; then
  echo "Compatibility check passed. System is ready to run LocalPilot."
  exit 0
fi

if [ "$NON_INTERACTIVE" = false ]; then
  echo ""
  echo "LocalPilot will be installed with the following plan:"
  echo "  - Application Environment: $VENV_DIR"
  echo "  - Launcher Command       : $LAUNCHER"
  echo "  - System packages touched: None"
  echo ""
  printf "Proceed with installation? [Y/n]: "
  read -r CONFIRM
  case "$CONFIRM" in
    [nN][oO]|[nN])
      echo "Installation cancelled."
      exit 0
      ;;
  esac
fi

# Prepare directories
mkdir -p "$APP_DIR" "$BIN_DIR"

# Create isolated venv
echo "Creating application environment in $VENV_DIR..."
"$PYTHON_BIN" -m venv "$VENV_DIR"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "Installing LocalPilot into $VENV_DIR..."

if [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
  echo "Installing from local source directory..."
  "$VENV_DIR/bin/pip" install --upgrade pip >/dev/null 2>&1 || true
  "$VENV_DIR/bin/pip" install "$SCRIPT_DIR"
else
  echo "Downloading and installing LocalPilot from PyPI..."
  "$VENV_DIR/bin/pip" install --upgrade pip >/dev/null 2>&1 || true
  "$VENV_DIR/bin/pip" install "localpilot>=0.1.1"
fi

# Create launcher script
echo "Creating launcher: $LAUNCHER..."
cat <<EOF > "$LAUNCHER"
#!/bin/sh
exec "$VENV_DIR/bin/localpilot" "\$@"
EOF
chmod +x "$LAUNCHER"

# Convenience symlink to ~/.local/bin if available
USER_LOCAL_BIN="$HOME/.local/bin"
if [ -d "$USER_LOCAL_BIN" ] && [ "$BIN_DIR" != "$USER_LOCAL_BIN" ]; then
  mkdir -p "$USER_LOCAL_BIN"
  ln -sf "$LAUNCHER" "$USER_LOCAL_BIN/localpilot" 2>/dev/null || true
  echo "Convenience link created: $USER_LOCAL_BIN/localpilot"
fi

echo ""
echo "============================================================"
echo "        LocalPilot successfully installed!                  "
echo "============================================================"
echo "You can launch LocalPilot by running:"
echo "  $LAUNCHER"
if [ "$SCOPE" = "isolated" ] || [ "$SCOPE" = "user" ]; then
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
      echo ""
      echo "Tip: Add $BIN_DIR to your PATH in ~/.bashrc or ~/.zshrc:"
      echo "  export PATH=\"\$PATH:$BIN_DIR\""
      ;;
  esac
fi
echo ""
