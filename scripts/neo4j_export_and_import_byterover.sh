#!/usr/bin/env sh
set -eu

if [ -z "${NEO4J_URL:-}" ] || [ -z "${NEO4J_USER:-}" ] || [ -z "${NEO4J_PASSWORD:-}" ]; then
  echo "Missing env vars. Required: NEO4J_URL, NEO4J_USER, NEO4J_PASSWORD" >&2
  exit 2
fi

HERMES_HOME="${HERMES_HOME:-${HOME:-/opt/data}}"
EXPORT_DIR="${EXPORT_DIR:-$HERMES_HOME/exports/neo4j}"
CONTEXT_TREE_DIR="${CONTEXT_TREE_DIR:-$HERMES_HOME/byterover/.brv/context-tree}"

mkdir -p "$EXPORT_DIR"

python3 /opt/hermes/scripts/neo4j_export_jsonl.py "$EXPORT_DIR"
python3 /opt/hermes/scripts/neo4j_jsonl_to_byterover.py \
  "$EXPORT_DIR/nodes.jsonl" \
  "$EXPORT_DIR/rels.jsonl" \
  "$CONTEXT_TREE_DIR"

echo "Done."
echo "Next (optional): run 'brv curate' to consolidate imported notes."

