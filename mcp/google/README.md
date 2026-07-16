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

- **Accounts**: `google_list_accounts`
- **Gmail**: `gmail_search`, `gmail_get_message` (includes attachment metadata),
  `gmail_get_attachment` (downloads to the tenant workspace for `MEDIA:` delivery),
  `gmail_send` (optional attachments via `attachmentPaths`), `gmail_create_draft`,
  `gmail_list_drafts`, `gmail_create_label`, `gmail_update_message` (read/unread,
  star, archive, apply/remove a label)
- **Drive**: `drive_search`, `drive_get_file`, `drive_create_file`,
  `drive_update_file`, `drive_delete_file` (trash, reversible), `drive_share_file`
  (invite by email and/or an anyone-with-link), `drive_copy_file`
- **Calendar**: `calendar_list_calendars`, `calendar_list_events`,
  `calendar_create_event` (optional `recurrence` RRULE), `calendar_update_event`
  (partial update, optional `recurrence`)

Drive has full CRUD for plain/binary files (create/read/update/delete/share/copy).
It cannot edit the *content* of a native Google Docs/Sheets/Slides file (Drive's
media-upload endpoint only replaces raw bytes; editing a native doc needs the
separate Docs/Sheets API, not implemented here) — `drive_update_file` can still
rename one. Binary attachment/file downloads follow the same `MEDIA:<path>`
convention as `pdf-tools-mcp`: the file is written to `/opt/data/workspace/google`
and the tool tells the agent to include a `MEDIA:` line in its own reply so the
channel adapter delivers it as a real attachment.

## Test locally

```sh
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | node mcp/google/server.mjs
```

Inside a runtime container you can also use `hermes mcp test <name>` after adding
it with `hermes mcp add`.
