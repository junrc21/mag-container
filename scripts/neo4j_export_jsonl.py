#!/usr/bin/env python3
"""
Export Neo4j nodes + relationships via the transactional HTTP API to JSONL.

This is designed for EasyPanel/Hermes deployments where you only have the HTTP endpoint.

Outputs:
  - nodes.jsonl: one JSON object per node
  - rels.jsonl: one JSON object per relationship

Environment variables:
  - NEO4J_URL (required): e.g. https://host/db/neo4j/tx/commit
  - NEO4J_USER (required)
  - NEO4J_PASSWORD (required)
  - NEO4J_PAGE_SIZE (optional, default 500)
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise SystemExit(f"Missing required env var: {name}")
    return value


@dataclass(frozen=True)
class Neo4jHttp:
    url: str
    user: str
    password: str

    def commit(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        body = {"statements": [{"statement": statement, "parameters": parameters or {}}]}
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        auth = base64.b64encode(f"{self.user}:{self.password}".encode("utf-8")).decode("ascii")
        req.add_header("Authorization", f"Basic {auth}")

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise SystemExit(f"Neo4j HTTP error: {e.code} {e.reason}") from e
        except Exception as e:
            raise SystemExit(f"Neo4j request failed: {e}") from e

        errors = payload.get("errors") or []
        if errors:
            raise SystemExit(f"Neo4j returned errors: {errors}")
        return payload["results"][0]["data"]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def export_nodes(client: Neo4jHttp, out_path: Path, page_size: int) -> int:
    total = 0
    skip = 0
    while True:
        data = client.commit(
            """
            MATCH (n)
            RETURN id(n) AS id, labels(n) AS labels, properties(n) AS props
            ORDER BY id(n)
            SKIP $skip
            LIMIT $limit
            """,
            {"skip": skip, "limit": page_size},
        )
        if not data:
            break

        rows = []
        for d in data:
            node_id, labels, props = d["row"]
            rows.append({"neo4j_id": node_id, "labels": labels, "props": props})

        _write_jsonl(out_path, rows)
        total += len(rows)
        skip += page_size
        time.sleep(0.05)
    return total


def export_rels(client: Neo4jHttp, out_path: Path, page_size: int) -> int:
    total = 0
    skip = 0
    while True:
        data = client.commit(
            """
            MATCH (a)-[r]->(b)
            RETURN id(r) AS id, type(r) AS type, id(a) AS start_id, id(b) AS end_id, properties(r) AS props
            ORDER BY id(r)
            SKIP $skip
            LIMIT $limit
            """,
            {"skip": skip, "limit": page_size},
        )
        if not data:
            break

        rows = []
        for d in data:
            rel_id, rel_type, start_id, end_id, props = d["row"]
            rows.append(
                {
                    "neo4j_rel_id": rel_id,
                    "type": rel_type,
                    "start_id": start_id,
                    "end_id": end_id,
                    "props": props,
                }
            )

        _write_jsonl(out_path, rows)
        total += len(rows)
        skip += page_size
        time.sleep(0.05)
    return total


def main() -> None:
    url = _env("NEO4J_URL")
    user = _env("NEO4J_USER")
    password = _env("NEO4J_PASSWORD")
    page_size = int(os.environ.get("NEO4J_PAGE_SIZE", "500"))

    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "./exports/neo4j")
    nodes_path = out_dir / "nodes.jsonl"
    rels_path = out_dir / "rels.jsonl"

    # overwrite by default to keep runs deterministic
    out_dir.mkdir(parents=True, exist_ok=True)
    if nodes_path.exists():
        nodes_path.unlink()
    if rels_path.exists():
        rels_path.unlink()

    client = Neo4jHttp(url=url, user=user, password=password)

    nodes_count = export_nodes(client, nodes_path, page_size)
    rels_count = export_rels(client, rels_path, page_size)

    print(f"Export complete: nodes={nodes_count} rels={rels_count}")
    print(f"- {nodes_path}")
    print(f"- {rels_path}")


if __name__ == "__main__":
    main()

