#!/usr/bin/env bash
set -euo pipefail

echo "== Env =="
echo "DATE: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "USER: $(id -u):$(id -g) ($(whoami))"
echo "HOME=${HOME:-}"
echo "XDG_DATA_HOME=${XDG_DATA_HOME:-}"
echo "XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-}"
echo "PATH=${PATH:-}"
echo

echo "== brv resolution =="
if command -v brv >/dev/null 2>&1; then
  echo "command -v brv: $(command -v brv)"
  brv --version || true
else
  echo "brv: NOT FOUND"
fi
echo "expected brv: ${BRV_INSTALL_DIR:-/opt/data/.local/share/brv-cli}/bin/brv"
ls -la "${BRV_INSTALL_DIR:-/opt/data/.local/share/brv-cli}/bin/brv" 2>/dev/null || true
echo

echo "== legacy ByteRover paths =="
for path in /opt/data/.brv-cli /opt/data/.local/share/brv/client /opt/data/.local/bin/brv; do
  if [ -e "${path}" ] || [ -L "${path}" ]; then
    ls -la "${path}" || true
  else
    echo "not found: ${path}"
  fi
done
echo

echo "== ByteRover project =="
BRV_PROJECT_DIR="/opt/data/byterover"
echo "project dir: ${BRV_PROJECT_DIR}"
ls -la "${BRV_PROJECT_DIR}" 2>/dev/null | head -n 40 || true
echo

echo "== brv status (timeout 20s) =="
if command -v brv >/dev/null 2>&1; then
  if command -v timeout >/dev/null 2>&1; then
    (cd "${BRV_PROJECT_DIR}" && timeout 20s brv status --format json) || true
  else
    echo "timeout(1) not installed; running without timeout (may hang)..." >&2
    (cd "${BRV_PROJECT_DIR}" && brv status --format json) || true
  fi
fi
echo

echo "== brv daemon processes =="
ps -eo pid,ppid,etime,pcpu,pmem,cmd | grep -E "brv-server\\.js|agent-process\\.js|/brv/.*daemon" | grep -v grep || true
echo

echo "== daemon file (if present) =="
DAEMON_JSON="/opt/data/.local/share/brv/daemon.json"
if [ -f "${DAEMON_JSON}" ]; then
  echo "found: ${DAEMON_JSON}"
  ls -la "${DAEMON_JSON}" || true
  head -n 50 "${DAEMON_JSON}" || true
else
  echo "not found: ${DAEMON_JSON}"
fi
