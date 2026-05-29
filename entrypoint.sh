#!/usr/bin/env sh
set -eu

# Hermes runs as user "hermes" and persists under $HOME (/opt/data).
# This script:
# - ensures ByteRover CLI is available on PATH via a stable symlink
# - seeds config/soul into /opt/data if missing (without overwriting)
# - optionally connects ByteRover provider (if requested via env)

export HOME="${HOME:-/opt/data}"

mkdir -p "$HOME/.local/bin" "$HOME/logs" "$HOME/sessions" "$HOME/byterover"

# Expose brv in PATH for non-interactive gateway shells.
if [ -x "$HOME/.brv-cli/bin/brv" ]; then
  ln -sf "$HOME/.brv-cli/bin/brv" "$HOME/.local/bin/brv" || true
fi

# Seed files only if missing (keep user's persistent versions intact).
if [ ! -f "$HOME/config.yaml" ] && [ -f /opt/hermes/bootstrap/config.yaml ]; then
  cp /opt/hermes/bootstrap/config.yaml "$HOME/config.yaml"
fi

if [ ! -f "$HOME/soul.md" ] && [ -f /opt/hermes/bootstrap/soul.md ]; then
  cp /opt/hermes/bootstrap/soul.md "$HOME/soul.md"
fi

# Optional: install ByteRover CLI if missing.
# Set BRV_AUTO_INSTALL=1 to enable.
if [ "${BRV_AUTO_INSTALL:-0}" = "1" ] && ! command -v brv >/dev/null 2>&1; then
  curl -fsSL https://byterover.dev/install.sh | sh
  if [ -x "$HOME/.brv-cli/bin/brv" ]; then
    ln -sf "$HOME/.brv-cli/bin/brv" "$HOME/.local/bin/brv" || true
  fi
fi

# Optional: connect provider on boot (safe to be idempotent).
# Set BRV_CONNECT_ON_BOOT=1 to enable.
if [ "${BRV_CONNECT_ON_BOOT:-0}" = "1" ] && command -v brv >/dev/null 2>&1; then
  # Prefer GOOGLE_API_KEY; fall back to GEMINI_API_KEY if provided.
  API_KEY="${GOOGLE_API_KEY:-${GEMINI_API_KEY:-}}"
  if [ -n "${API_KEY}" ]; then
    brv providers connect google --api-key "${API_KEY}" >/dev/null 2>&1 || true
  fi
fi

exec hermes gateway run
