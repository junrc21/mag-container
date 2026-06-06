# MAG Google Workspace MCP server

A zero-dependency Node stdio MCP server bundled into the MAG runtime image at
`/opt/mag/google-mcp/server.mjs`. It gives the Hermes agent Gmail / Drive /
Calendar (Meet) tools.

## How it fits together

It stores **no** Google credentials. On every tool call it asks the MAG control
plane for a fresh, valid access token for the requested account, then calls the
Google REST APIs directly. Tokens (and their refresh) live in MAG, and a tenant
can connect **multiple** Google accounts — every tool accepts an optional
`account` (email) to pick which one.

```
Hermes agent ──stdio MCP──▶ server.mjs ──HTTP (x-internal-key)──▶ MAG /internal/google/...
                                          └──Bearer token──▶ Google APIs (Gmail/Drive/Calendar)
```

## Wiring (done by MAG)

MAG's `buildConfigYaml` emits this into each tenant's `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  google:
    command: "node"
    args: ["/opt/mag/google-mcp/server.mjs"]
    enabled: true
    env:
      MAG_API_URL: "${MAG_API_URL}"
      MAG_INTERNAL_KEY: "${MAG_INTERNAL_KEY}"
      MAG_TENANT_ID: "${MAG_TENANT_ID}"
```

Hermes interpolates the `${VAR}` placeholders from the runtime container env at
launch, so the service-to-service key is never written to a file.

## Tools

`google_list_accounts`, `gmail_search`, `gmail_get_message`, `gmail_send`,
`drive_search`, `drive_get_file`, `calendar_list_events`, `calendar_create_event`.

## Test locally

```sh
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | node mcp/google/server.mjs
```

Inside a runtime container you can also use `hermes mcp test <name>` after adding
it with `hermes mcp add`.
