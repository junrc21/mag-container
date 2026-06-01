#!/usr/bin/env sh
set -eu

# Hermes runs as user "hermes" and persists under $HOME (/opt/data).
# This script:
# - ensures ByteRover CLI resolves to the canonical client bin (XDG_DATA_HOME)
# - seeds config/soul into /opt/data if missing (without overwriting)
# - optionally connects ByteRover provider (if requested via env)

# Canonical persistent home and XDG dirs (avoid drift across base images).
export HOME="/opt/data"
export XDG_DATA_HOME="${XDG_DATA_HOME:-/opt/data/.local/share}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-/opt/data/.config}"

BRV_PROJECT_DIR="/opt/data/byterover"
BRV_CLIENT_BIN="${XDG_DATA_HOME}/brv/client/bin/brv"
BRV_EXPECTED_BIN="${BRV_EXPECTED_BIN:-${BRV_CLIENT_BIN}}"

# Explicit PATH order:
# - Prefer ByteRover client bin (avoid .brv-cli shadowing).
# - Keep Hermes CLI + venv early.
export PATH="${XDG_DATA_HOME}/brv/client/bin:/opt/hermes/bin:/opt/hermes/.venv/bin:/opt/data/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

mkdir -p \
  "/opt/data/.local/bin" \
  "${XDG_DATA_HOME}" \
  "${XDG_CONFIG_HOME}" \
  "/opt/data/logs" \
  "/opt/data/sessions" \
  "${BRV_PROJECT_DIR}"

# Expose brv in /opt/data/.local/bin as a convenience, but ONLY if it points to
# the canonical client bin (never to ~/.brv-cli).
if [ -x "${BRV_CLIENT_BIN}" ]; then
  ln -sf "${BRV_CLIENT_BIN}" "/opt/data/.local/bin/brv" || true
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
if [ "${BRV_AUTO_INSTALL:-1}" = "1" ] && [ ! -x "${BRV_CLIENT_BIN}" ]; then
  echo "[entrypoint] ByteRover client missing at ${BRV_CLIENT_BIN}; attempting install..." >&2

  # Best-effort install: rely on HOME/XDG_* to steer install into the volume.
  # Do not fail startup if the installer is slow or unavailable.
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL https://byterover.dev/install.sh | sh >/dev/null 2>&1 || true
  fi

  # Normalize: only link /opt/data/.local/bin/brv if the canonical client exists.
  if [ -x "${BRV_CLIENT_BIN}" ]; then
    ln -sf "${BRV_CLIENT_BIN}" "/opt/data/.local/bin/brv" >/dev/null 2>&1 || true
  else
    echo "[entrypoint] WARNING: ByteRover client still not found at ${BRV_CLIENT_BIN}. Startup will continue without brv." >&2
  fi
fi

# Validate which brv will be used (help detect PATH shadowing).
if command -v brv >/dev/null 2>&1; then
  BRV_RESOLVED="$(command -v brv || true)"
  if [ "${BRV_RESOLVED}" != "${BRV_EXPECTED_BIN}" ]; then
    echo "[entrypoint] WARNING: brv resolves to '${BRV_RESOLVED}', expected '${BRV_EXPECTED_BIN}'" >&2
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

# Optional: connect provider on boot (NON-BLOCKING; explicit opt-in only).
# - Default: OFF (do not auto-connect)
# - Enable: BRV_CONNECT_ON_BOOT=1
#
# Connecting providers can be slow or fail on networking; never block Hermes startup.
if [ "${BRV_CONNECT_ON_BOOT:-0}" = "1" ] && command -v brv >/dev/null 2>&1; then
  (
    cd "${BRV_PROJECT_DIR}" 2>/dev/null || exit 0

    # Prefer GOOGLE_API_KEY; fall back to GEMINI_API_KEY if provided.
    API_KEY="${GOOGLE_API_KEY:-${GEMINI_API_KEY:-}}"
    if [ -z "${API_KEY}" ]; then
      exit 0
    fi

    # If already connected, do nothing. Providers list may be slow; cap time.
    if command -v timeout >/dev/null 2>&1; then
      timeout 10s brv providers list 2>/dev/null | grep -qiE 'google|gemini' && exit 0
      timeout 15s brv providers connect google --api-key "${API_KEY}" >/dev/null 2>&1 || true
    else
      brv providers list 2>/dev/null | grep -qiE 'google|gemini' && exit 0
      brv providers connect google --api-key "${API_KEY}" >/dev/null 2>&1 || true
    fi
  ) >/dev/null 2>&1 &
fi

exec hermes gateway run
