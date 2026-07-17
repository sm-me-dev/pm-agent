#!/usr/bin/env bash
# pm-agent installer — Linux / macOS
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/sm-me-dev/pm-agent/main/scripts/install.sh | bash
#   PM_AGENT_VERSION=0.3.0 curl -fsSL ... | bash
#
# Security note: Piping curl to shell is convenient but skips your usual
# verification steps. Review the script before running:
#   curl -fsSL https://raw.githubusercontent.com/sm-me-dev/pm-agent/main/scripts/install.sh
#
# This script installs pm-agent via pipx into an isolated environment.

set -euo pipefail

INSTALL_DIR="${HOME}/.local/bin"
PM_AGENT_REPO="sm-me-dev/pm-agent"
PYPI_PACKAGE="pm-agent"

# ---- Pre-flight checks ----

if ! command -v python3 &>/dev/null && ! command -v python &>/dev/null; then
  echo "error: Python 3.12+ is required but not found."
  echo "  Install Python: https://www.python.org/downloads/"
  exit 1
fi

PYTHON="$(command -v python3 || command -v python)"
PYVER=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [ "$(echo "$PYVER" | cut -d. -f1)" -lt 3 ] || { [ "$(echo "$PYVER" | cut -d. -f1)" -eq 3 ] && [ "$(echo "$PYVER" | cut -d. -f2)" -lt 12 ]; }; then
  echo "error: Python 3.12+ required (found $PYVER)"
  exit 1
fi

# ---- pipx ----

if command -v pipx &>/dev/null; then
  PIPX="$(command -v pipx)"
else
  echo "pipx not found — installing via pip..."

  if ! "$PYTHON" -m pip --version &>/dev/null; then
    echo "error: pip is not available."
    exit 1
  fi

  "$PYTHON" -m pip install --user pipx 2>&1
  PIPX="${INSTALL_DIR}/pipx"

  if ! command -v "$PIPX" &>/dev/null; then
    echo ""
    echo "  pipx installed to ${INSTALL_DIR}/pipx but it is not in PATH."
    echo "  Add the following to your shell config (~/.bashrc, ~/.zshrc, etc.):"
    echo "    export PATH=\"\$PATH:${INSTALL_DIR}\""
    echo ""
    # If the current shell supports PATH extension, offer it
    case "${SHELL}" in
      */bash|*/zsh)
        echo "  You can also run:  export PATH=\"\$PATH:${INSTALL_DIR}\""
        ;;
    esac
    echo ""
    PIPX="${INSTALL_DIR}/pipx"
  fi
fi

# ---- Install / upgrade pm-agent ----

VERSION="${PM_AGENT_VERSION:-}"

if [ -n "$VERSION" ]; then
  INSTALL_SPEC="pm-agent==${VERSION}"
else
  INSTALL_SPEC="pm-agent"
fi

echo "Installing pm-agent via pipx ..."

if "$PIPX" list --short 2>/dev/null | grep -qi "^pm-agent "; then
  if [ -n "$VERSION" ]; then
    "$PIPX" install "$INSTALL_SPEC" --force 2>&1
  else
    "$PIPX" upgrade pm-agent 2>&1 || "$PIPX" inject pm-agent pm-agent 2>/dev/null || "$PIPX" install "$INSTALL_SPEC" --force
  fi
else
  "$PIPX" install "$INSTALL_SPEC" 2>&1
fi

# ---- Verify ----

echo ""
if command -v pm-agent &>/dev/null; then
  echo "✓ pm-agent installed successfully"
  pm-agent --help 2>&1 | head -1
  echo ""
  echo "Get started:"
  echo "  cd /path/to/your/project && pm-agent init"
  echo "  pm-agent status"
else
  echo "✓ pm-agent installed"
  echo ""
  echo "  Ensure ${INSTALL_DIR} is in your PATH, then run:"
  echo "    pm-agent --help"
fi
echo ""
echo "  Documentation: https://github.com/${PM_AGENT_REPO}"
