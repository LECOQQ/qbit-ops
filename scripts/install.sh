#!/bin/sh
# Install qbit-ops for people with no uv, pipx, Homebrew or Docker yet.
#
# Usage (pin the URL to a release tag, never to `main`):
#   curl -LsSf https://raw.githubusercontent.com/LECOQQ/qbit-ops/vX.Y.Z/scripts/install.sh | sh
#
# See README.md, section "Get started", for how to verify this script's
# published checksum before piping it into a shell.
#
# This wraps uv (https://docs.astral.sh/uv/): installing uv first if it
# is missing, then `uv tool install "qbit-ops[tui]"`. uv downloads its
# own managed Python when none on the system satisfies
# `Requires-Python`, so this works even where no Python is installed at
# all -- it never touches a system Python or requires root.

set -eu

UV_INSTALL_URL="https://astral.sh/uv/install.sh"
PACKAGE_SPEC="qbit-ops[tui]"

# --- refuse root ---------------------------------------------------------
#
# qbit-ops installs into the invoking user's home directory, never
# system-wide. Running as root would put it where the regular user
# cannot find, upgrade or remove it.

if [ "$(id -u)" = "0" ]; then
  cat >&2 <<'EOF'
Refusing to run as root.

qbit-ops installs per-user (into your home directory), not system-wide.
Run this script as your normal account instead, without sudo:

    curl -LsSf <this script's URL> | sh
EOF
  exit 1
fi

# --- resolve uv, installing it if missing ---------------------------------

if command -v uv >/dev/null 2>&1; then
  UV_BIN=$(command -v uv)
  echo "uv is already installed: $UV_BIN"
else
  echo "uv not found. About to install it via the official Astral installer:"
  echo "    curl -LsSf $UV_INSTALL_URL | sh"
  echo ""
  curl -LsSf "$UV_INSTALL_URL" | sh

  if [ -f "$HOME/.local/bin/env" ]; then
    # shellcheck source=/dev/null
    . "$HOME/.local/bin/env"
  fi

  if command -v uv >/dev/null 2>&1; then
    UV_BIN=$(command -v uv)
  elif [ -x "$HOME/.local/bin/uv" ]; then
    UV_BIN="$HOME/.local/bin/uv"
  else
    echo "uv was installed but is not on PATH; re-open your shell and re-run this script." >&2
    exit 1
  fi
fi

# --- announce, then install qbit-ops --------------------------------------

TOOL_BIN_DIR=$("$UV_BIN" tool dir --bin 2>/dev/null || echo "the directory 'uv tool dir --bin' reports")

echo ""
echo "About to run: $UV_BIN tool install \"$PACKAGE_SPEC\""
echo "This installs qbit-ops, with its optional TUI, for your user only."
echo "Executables will be placed in: $TOOL_BIN_DIR"
echo ""

"$UV_BIN" tool install "$PACKAGE_SPEC"

echo ""
echo "qbit-ops installed. Executables are in: $TOOL_BIN_DIR"
echo "Make sure that directory is on your PATH, then run: qbit-ops init"
