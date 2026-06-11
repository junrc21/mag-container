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

# Memory must be BEST-EFFORT. brv has no per-call timeout — on a codex plan without a
# brv API key, or a slow/unreachable ByteRover backend, `brv query` (recall, BEFORE the
# reply) and `brv curate` (the post-turn write) can hang for minutes and stall the turn.
# Wrap the launcher so ONLY those two subcommands are time-bounded; the daemon and every
# other subcommand pass through untouched. They get DIFFERENT budgets:
#   • query  — blocks turn START, so it must be SHORT (default 15s).
#   • curate — runs in the background (sync_turn) or as an explicit save; the brv backend
#     does LLM work and legitimately takes ~15-25s, so a 15s cap silently DROPS the write
#     ("Thinking..." killed mid-curate). It gets a longer budget (default 60s).
# Tune via MAG_BRV_QUERY_TIMEOUT / MAG_BRV_CURATE_TIMEOUT. Idempotent, and UPGRADES an
# older single-budget wrapper (the idempotency check keys on MAG_BRV_CURATE_TIMEOUT).
if [ -e "${BRV_CLIENT_BIN}" ] && ! grep -q "MAG_BRV_CURATE_TIMEOUT" "${BRV_CLIENT_BIN}" 2>/dev/null; then
  BRV_REAL_BIN="${BRV_INSTALL_DIR}/lib/bin/brv"
  if [ -e "${BRV_REAL_BIN}" ]; then
    rm -f "${BRV_CLIENT_BIN}"
    cat > "${BRV_CLIENT_BIN}" <<BRVWRAP
#!/usr/bin/env bash
# MAG_brv_timeout: bound query/curate so a slow/hung brv backend never stalls a turn
# (memory is best-effort). query is short (blocks turn start); curate gets longer
# (background + LLM-backed write, ~15-25s). Daemon + other subcommands pass through.
case "\$1" in
  query)  exec timeout "\${MAG_BRV_QUERY_TIMEOUT:-15}"  "${BRV_REAL_BIN}" "\$@" ;;
  curate) exec timeout "\${MAG_BRV_CURATE_TIMEOUT:-60}" "${BRV_REAL_BIN}" "\$@" ;;
  *) exec "${BRV_REAL_BIN}" "\$@" ;;
esac
BRVWRAP
    chmod +x "${BRV_CLIENT_BIN}"
    echo "[entrypoint] brv launcher wrapped (query \${MAG_BRV_QUERY_TIMEOUT:-15}s / curate \${MAG_BRV_CURATE_TIMEOUT:-60}s)" >&2
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

# Connect ByteRover's LLM provider on boot (NON-BLOCKING; opt-in via BRV_CONNECT_ON_BOOT=1).
#
# CRITICAL: ByteRover must use the SAME provider Hermes is configured with — NEVER a
# hardcoded one. We mirror LLM_PROVIDER (set by the control plane from the plan's LLM).
#  - Key-based providers (gemini/anthropic/openai): explicit `providers connect --api-key`.
#  - OAuth providers (codex / openai-codex): DO NOTHING here. ByteRover inherits the codex
#    auth Hermes already holds — there is NO second login, NO second account, NO api key.
# Hardcoding gemini here (old behavior) silently overrode codex inheritance and pointed
# memory at a different/depleted LLM.
if [ "${BRV_CONNECT_ON_BOOT:-0}" = "1" ] && command -v brv >/dev/null 2>&1; then
  (
    cd "${BRV_PROJECT_DIR}" 2>/dev/null || exit 0

    # Longer cap: a short timeout was killing the connect mid-handshake on fresh tenants.
    TO_CONNECT="timeout 75s"
    command -v timeout >/dev/null 2>&1 || TO_CONNECT=""

    # Connect ByteRover to the memory provider the control plane resolved from the PLAN's LLM:
    #   BRV_PROVIDER = brv provider name (google/anthropic/openai/xai/…)
    #   BRV_API_KEY  = the plan's API key (decrypted server-side; never hardcoded)
    # Both come from the admin panel via the provisioner. Empty for OAuth plans (codex), whose
    # brv login is browser-only — those need an API-key LLM on the plan, or a 1x manual brv login.
    if [ -n "${BRV_PROVIDER:-}" ] && [ -n "${BRV_API_KEY:-}" ]; then
      ${TO_CONNECT} brv providers connect "${BRV_PROVIDER}" \
        --api-key "${BRV_API_KEY}" ${LLM_MODEL:+--model "${LLM_MODEL}"} >/dev/null 2>&1 || true
    fi

    # Curations must AUTO-APPLY. Newer brv ships HITL review ON by default, which only
    # *stages* curations instead of writing them — so memory silently never persists.
    ${TO_CONNECT} brv review --disable >/dev/null 2>&1 || true
  ) >/dev/null 2>&1 &
fi

exec hermes gateway run
