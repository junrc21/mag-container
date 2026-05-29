#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Clone /opt/data storage from an existing EasyPanel service.

This script runs on the HOST (Docker Swarm manager), not inside the app container.

It detects what is mounted at /opt/data for the chosen running task container:
- If it's a bind mount (common for EasyPanel "Volume" mounts), it rsyncs the host directory.
- If it's a Docker named volume, it clones volume->volume using a temporary Alpine container.

Usage:
  sudo ./scripts/clone_opt_data_on_host.sh --service <swarm_service_name> [--uid 10000 --gid 10000]

Examples:
  sudo ./scripts/clone_opt_data_on_host.sh --service cyriusx_hermes-mag

Output:
  Prints the cloned source path/volume name you should mount as /opt/data in the NEW service.
EOF
}

SERVICE=""
UID="10000"
GID="10000"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service) SERVICE="${2:-}"; shift 2;;
    --uid) UID="${2:-}"; shift 2;;
    --gid) GID="${2:-}"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [[ -z "$SERVICE" ]]; then
  echo "--service is required" >&2
  usage
  exit 2
fi

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 2
fi

echo "[1/4] Locating a running container for service: $SERVICE"
CID="$(docker ps --filter "label=com.docker.swarm.service.name=${SERVICE}" --format '{{.ID}}' | head -n 1 || true)"
if [[ -z "$CID" ]]; then
  echo "No running container found for service '${SERVICE}'." >&2
  echo "Check: docker service ls | grep -i ${SERVICE}" >&2
  exit 1
fi

echo "[2/4] Inspecting mount for /opt/data on container: $CID"
MOUNT_LINE="$(docker inspect "$CID" --format '{{range .Mounts}}{{if eq .Destination "/opt/data"}}{{println .Type "|" .Name "|" .Source}}{{end}}{{end}}' | head -n 1 || true)"
if [[ -z "$MOUNT_LINE" ]]; then
  echo "Could not find a mount with destination /opt/data on container ${CID}." >&2
  exit 1
fi

IFS="|" read -r MOUNT_TYPE MOUNT_NAME MOUNT_SOURCE <<<"$MOUNT_LINE"
MOUNT_TYPE="$(echo "$MOUNT_TYPE" | xargs)"
MOUNT_NAME="$(echo "$MOUNT_NAME" | xargs)"
MOUNT_SOURCE="$(echo "$MOUNT_SOURCE" | xargs)"

STAMP="$(date +%Y%m%d-%H%M%S)"

if [[ "$MOUNT_TYPE" == "bind" ]]; then
  SRC="$MOUNT_SOURCE"
  if [[ ! -d "$SRC" ]]; then
    echo "Bind mount source does not exist or is not a directory: $SRC" >&2
    exit 1
  fi

  DST="${SRC}-clone-${STAMP}"
  echo "[3/4] Cloning bind mount directory:"
  echo "  from: $SRC"
  echo "  to:   $DST"
  mkdir -p "$DST"
  rsync -aH --numeric-ids "${SRC}/" "${DST}/"
  chown -R "${UID}:${GID}" "${DST}"

  echo "[4/4] Done."
  echo
  echo "Mount this host path as /opt/data in the NEW service:"
  echo "  ${DST}"
  exit 0
fi

if [[ "$MOUNT_TYPE" == "volume" ]]; then
  OLDVOL="$MOUNT_NAME"
  if [[ -z "$OLDVOL" ]]; then
    echo "Volume mount detected but volume name is empty. Raw: $MOUNT_LINE" >&2
    exit 1
  fi

  NEWVOL="${OLDVOL}_clone_${STAMP}"
  echo "[3/4] Cloning docker volume:"
  echo "  from: $OLDVOL"
  echo "  to:   $NEWVOL"
  docker volume create "$NEWVOL" >/dev/null
  docker run --rm -u 0 -v "${OLDVOL}:/from" -v "${NEWVOL}:/to" alpine sh -lc \
    "cd /from && cp -a . /to && chown -R ${UID}:${GID} /to"

  echo "[4/4] Done."
  echo
  echo "Mount this docker volume as /opt/data in the NEW service:"
  echo "  ${NEWVOL}"
  exit 0
fi

echo "Unsupported mount type for /opt/data: ${MOUNT_TYPE} (raw: $MOUNT_LINE)" >&2
exit 1

