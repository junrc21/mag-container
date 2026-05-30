#!/usr/bin/env sh
set -eu

# Hermes runs as user "hermes" and persists under $HOME (/opt/data).
# This script:
# - ensures ByteRover CLI is available on PATH via a stable symlink
# - seeds config/soul into /opt/data if missing (without overwriting)
# - optionally connects ByteRover provider (if requested via env)

export HOME="${HOME:-/opt/data}"
export PATH="$HOME/.local/bin:${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"

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

# Auto-install ByteRover CLI into the persistent volume if missing.
# Default: enabled (set BRV_AUTO_INSTALL=0 to disable).
if [ "${BRV_AUTO_INSTALL:-1}" = "1" ] && ! command -v brv >/dev/null 2>&1; then
  curl -fsSL https://byterover.dev/install.sh | sh
  if [ -x "$HOME/.brv-cli/bin/brv" ]; then
    ln -sf "$HOME/.brv-cli/bin/brv" "$HOME/.local/bin/brv" || true
  fi
fi

# Ensure Edge TTS Python package is importable from Hermes' Python.
# Some base images ship without `pip`/`ensurepip` in the venv; Hermes can still
# lazy-install for runtime use, but ad-hoc scripts that `import edge_tts` will fail.
# Default: enabled (set EDGE_TTS_AUTO_INSTALL=0 to disable).
if [ "${EDGE_TTS_AUTO_INSTALL:-1}" = "1" ]; then
  HERMES_PY="/opt/hermes/.venv/bin/python3"
  if [ -x "${HERMES_PY}" ] && ! "${HERMES_PY}" -c "import edge_tts" >/dev/null 2>&1; then
    if command -v uv >/dev/null 2>&1; then
      VENV_SITE="$("${HERMES_PY}" -c "import site; print(site.getsitepackages()[0])")"
      mkdir -p "${VENV_SITE}"
      uv pip install --python "${HERMES_PY}" edge-tts --target "${VENV_SITE}" >/dev/null 2>&1 || true
    fi
  fi
fi

# Optional: connect provider on boot (idempotent).
# Behavior:
# - If BRV_CONNECT_ON_BOOT=1: always attempt connect (if API key present).
# - If BRV_CONNECT_ON_BOOT=0: never attempt connect.
# - If unset: auto-attempt connect only when config.yaml indicates memory provider "byterover".
if command -v brv >/dev/null 2>&1; then
  SHOULD_CONNECT="0"
  if [ "${BRV_CONNECT_ON_BOOT:-}" = "1" ]; then
    SHOULD_CONNECT="1"
  elif [ "${BRV_CONNECT_ON_BOOT:-}" = "" ]; then
    if [ -f "$HOME/config.yaml" ] && grep -q "provider: byterover" "$HOME/config.yaml"; then
      SHOULD_CONNECT="1"
    fi
  fi

  if [ "${SHOULD_CONNECT}" = "1" ]; then
    # Prefer GOOGLE_API_KEY; fall back to GEMINI_API_KEY if provided.
    API_KEY="${GOOGLE_API_KEY:-${GEMINI_API_KEY:-}}"
    if [ -n "${API_KEY}" ]; then
      brv providers connect google --api-key "${API_KEY}" >/dev/null 2>&1 || true
    fi
  fi
fi

exec hermes gateway run
