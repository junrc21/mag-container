#!/usr/bin/env python3
"""
Convert Neo4j JSONL export into ByteRover Context Tree markdown entries.

This does NOT attempt a 1:1 graph migration. It creates a safe "imports/neo4j"
domain that can later be curated/merged by ByteRover.

Input:
  - nodes.jsonl (from neo4j_export_jsonl.py)
  - rels.jsonl  (from neo4j_export_jsonl.py)

Output:
  - <out_dir>/imports/neo4j/nodes/<id>.md
  - <out_dir>/imports/neo4j/rels/<id>.md

By default, out_dir is Hermes home ByteRover context tree:
  $HERMES_HOME/byterover/.brv/context-tree
where HERMES_HOME defaults to $HOME.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SENSITIVE_KEY_RE = re.compile(r"(pass(word)?|token|secret|api[_-]?key|bearer|cookie|auth)", re.I)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def _redact_props(props: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for k, v in props.items():
        if SENSITIVE_KEY_RE.search(k):
            cleaned[k] = "[REDACTED]"
        else:
            cleaned[k] = v
    return cleaned


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _md_header(title: str) -> str:
    return f"# {title}\n"


def _write_md(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("Usage: neo4j_jsonl_to_byterover.py <nodes.jsonl> <rels.jsonl> [<context_tree_dir>]")

    nodes_path = Path(sys.argv[1])
    rels_path = Path(sys.argv[2])

    hermes_home = os.environ.get("HERMES_HOME") or os.environ.get("HOME") or "/opt/data"
    default_out = Path(hermes_home) / "byterover" / ".brv" / "context-tree"
    out_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else default_out

    nodes = _load_jsonl(nodes_path)
    rels = _load_jsonl(rels_path)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    import_root = out_dir / "imports" / "neo4j"

    # Nodes
    for n in nodes:
        node_id = n.get("neo4j_id")
        labels = n.get("labels") or []
        props = _redact_props(n.get("props") or {})
        title = props.get("name") or props.get("title") or f"Node {node_id}"

        content = []
        content.append(_md_header(_safe_str(title)))
        content.append("")
        content.append("## Source")
        content.append(f"- origin: neo4j")
        content.append(f"- neo4j_id: {node_id}")
        content.append(f"- labels: {', '.join([_safe_str(l) for l in labels])}")
        content.append(f"- exported_at_utc: {timestamp}")
        content.append("")
        content.append("## Properties")
        for k in sorted(props.keys()):
            content.append(f"- {k}: {_safe_str(props[k])}")
        content.append("")
        _write_md(import_root / "nodes" / f"{node_id}.md", "\n".join(content).strip() + "\n")

    # Relationships
    for r in rels:
        rel_id = r.get("neo4j_rel_id")
        rel_type = r.get("type")
        start_id = r.get("start_id")
        end_id = r.get("end_id")
        props = _redact_props(r.get("props") or {})

        content = []
        content.append(_md_header(f"{rel_type} ({start_id} -> {end_id})"))
        content.append("")
        content.append("## Source")
        content.append(f"- origin: neo4j")
        content.append(f"- neo4j_rel_id: {rel_id}")
        content.append(f"- type: {_safe_str(rel_type)}")
        content.append(f"- start_id: {start_id}")
        content.append(f"- end_id: {end_id}")
        content.append(f"- exported_at_utc: {timestamp}")
        content.append("")
        if props:
            content.append("## Properties")
            for k in sorted(props.keys()):
                content.append(f"- {k}: {_safe_str(props[k])}")
            content.append("")

        _write_md(import_root / "rels" / f"{rel_id}.md", "\n".join(content).strip() + "\n")

    print("Wrote ByteRover import tree:")
    print(f"- {import_root / 'nodes'}")
    print(f"- {import_root / 'rels'}")


if __name__ == "__main__":
    main()

