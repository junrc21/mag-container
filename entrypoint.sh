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
export BRV_INSTALL_DIR="${BRV_INSTALL_DIR:-${XDG_DATA_HOME}/brv-cli}"
BRV_GLOBAL_DATA_DIR="${BRV_GLOBAL_DATA_DIR:-${XDG_DATA_HOME}/brv}"
BRV_SETTINGS_FILE="${BRV_GLOBAL_DATA_DIR}/settings.json"

BRV_PROJECT_DIR="/opt/data/byterover"
BRV_CLIENT_BIN="${BRV_INSTALL_DIR}/bin/brv"
BRV_EXPECTED_BIN="${BRV_EXPECTED_BIN:-${BRV_CLIENT_BIN}}"

# Explicit PATH order:
# - Prefer the single canonical ByteRover install under /opt/data.
# - Keep Hermes CLI + venv early.
export PATH="${BRV_INSTALL_DIR}/bin:/opt/hermes/bin:/opt/hermes/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

mkdir -p \
  "${BRV_INSTALL_DIR}" \
  "${BRV_GLOBAL_DATA_DIR}" \
  "${XDG_DATA_HOME}" \
  "${XDG_CONFIG_HOME}" \
  "/opt/data/logs" \
  "/opt/data/sessions" \
  "${BRV_PROJECT_DIR}"

# ── MAG per-tenant bootstrap ────────────────────────────────────────────────
# The MAG control plane mounts each tenant's generated files (config.yaml, .env,
# SOUL.md and hooks/) read-only at /mag/bootstrap. Copy them into the persistent
# /opt/data volume (HOME) so Hermes and the gateway event hooks pick them up.
# Directories are merged into existing destinations so the pre-created
# /opt/data/hooks gets populated; plain files are only seeded when absent, so a
# tenant's persisted state (e.g. a config.yaml rewritten by a runtime reload) is
# never clobbered on restart. Safe no-op when /mag/bootstrap is not mounted.
MAG_BOOTSTRAP_DIR="/mag/bootstrap"
mkdir -p /opt/data/workspace /opt/data/hooks
if [ -d "$MAG_BOOTSTRAP_DIR" ]; then
  for item in "$MAG_BOOTSTRAP_DIR"/* "$MAG_BOOTSTRAP_DIR"/.[!.]*; do
    [ -e "$item" ] || continue
    name="$(basename "$item")"
    if [ -d "$item" ]; then
      mkdir -p "/opt/data/$name"
      cp -R "$item"/. "/opt/data/$name"/
    elif [ ! -e "/opt/data/$name" ]; then
      cp -R "$item" "/opt/data/$name"
    fi
  done
fi
# ────────────────────────────────────────────────────────────────────────────

# Remove legacy/shadow installs that caused duplicate ByteRover clients/daemons.
# Keep this on by default because /opt/data is persistent across restarts.
if [ "${BRV_CLEAN_LEGACY_INSTALLS:-1}" = "1" ]; then
  rm -rf "/opt/data/.brv-cli" "${XDG_DATA_HOME}/brv/client" 2>/dev/null || true
  rm -f "/opt/data/.local/bin/brv" 2>/dev/null || true
fi

# Disable ByteRover background auto-update by default. This avoids extra
# update processes inside the tenant runtime while preserving manual updates.
if [ "${BRV_DISABLE_AUTOUPDATE:-1}" = "1" ] && command -v python3 >/dev/null 2>&1; then
  python3 - "$BRV_SETTINGS_FILE" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)

data = {"schemaVersion": "2", "values": {}}
if path.exists():
    try:
        current = json.loads(path.read_text())
        if isinstance(current, dict):
            data.update({k: v for k, v in current.items() if k != "values"})
            if isinstance(current.get("values"), dict):
                data["values"].update(current["values"])
    except Exception:
        pass

data.setdefault("schemaVersion", "2")
data.setdefault("values", {})
data["values"]["update.checkForUpdates"] = False
path.write_text(json.dumps(data))
PY
fi

# The upstream installer appends ~/.brv-cli/bin to shell startup files even
# when we force a different install dir. Remove those legacy entries so every
# future shell resolves the canonical persistent path only.
clean_shell_path_refs() {
  target="$1"
  [ -f "$target" ] || return 0
  tmp="$(mktemp)"
  grep -v '\.brv-cli/bin' "$target" > "$tmp" || true
  mv "$tmp" "$target"
}

clean_all_shell_path_refs() {
  clean_shell_path_refs "$HOME/.profile"
  clean_shell_path_refs "$HOME/.bashrc"
  clean_shell_path_refs "$HOME/.zshrc"
}

ensure_shell_path_ref() {
  target="$1"
  [ -f "$target" ] || return 0
  line='export PATH="$HOME/.local/share/brv-cli/bin:$PATH"'
  grep -Fqx "$line" "$target" 2>/dev/null && return 0
  printf '\n%s\n' "$line" >> "$target"
}

ensure_all_shell_path_refs() {
  ensure_shell_path_ref "$HOME/.profile"
  ensure_shell_path_ref "$HOME/.bashrc"
  ensure_shell_path_ref "$HOME/.zshrc"
}

clean_all_shell_path_refs
ensure_all_shell_path_refs

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
    curl -fsSL https://byterover.dev/install.sh | BRV_INSTALL_DIR="${BRV_INSTALL_DIR}" sh >/dev/null 2>&1 || true
  fi

  if [ -x "${BRV_CLIENT_BIN}" ]; then
    echo "[entrypoint] ByteRover client installed at ${BRV_CLIENT_BIN}" >&2
  else
    echo "[entrypoint] WARNING: ByteRover client still not found at ${BRV_CLIENT_BIN}. Startup will continue without brv." >&2
  fi
fi

# The installer may rewrite shell startup files after our pre-clean. Run the
# cleanup again so every later shell stays aligned with the canonical install.
clean_all_shell_path_refs
ensure_all_shell_path_refs

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

    # Pin a STABLE Gemini model for ByteRover. brv's default is a preview model
    # (gemini-3-flash-preview) that hits "high demand" rate limits, causing 4x
    # retries (~2min each) on every curate/query — which stalls memory writes and
    # the synchronous prefetch read. gemini-2.5-flash (GA) is fast and reliable.
    BRV_MODEL="${BRV_MODEL:-gemini-2.5-flash}"

    # If already connected, do nothing. Providers list may be slow; cap time.
    if command -v timeout >/dev/null 2>&1; then
      timeout 10s brv providers list 2>/dev/null | grep -qiE 'google|gemini' && exit 0
      timeout 20s brv providers connect google --api-key "${API_KEY}" --model "${BRV_MODEL}" >/dev/null 2>&1 || true
    else
      brv providers list 2>/dev/null | grep -qiE 'google|gemini' && exit 0
      brv providers connect google --api-key "${API_KEY}" --model "${BRV_MODEL}" >/dev/null 2>&1 || true
    fi
  ) >/dev/null 2>&1 &
fi

exec hermes gateway run
