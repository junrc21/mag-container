"""Build-time contract check: verify the WhatsApp bridge exposes POST /send.

The MAG whatsapp-outbound MCP server (`/opt/mag/whatsapp-outbound-mcp/server.mjs`)
calls `POST /send` on the bridge (localhost:WHATSAPP_BRIDGE_PORT) to send proactive
messages.  The bridge ALREADY has this route in its upstream source; this patch just
verifies it's present so a future Hermes upgrade that removes it is caught immediately
at build time (fail-loud), not silently at runtime.

No code is injected — the existing /send handler is correct and already used by the
Hermes gateway for normal WhatsApp replies.  All application-level guards
(confirmed_by_user, allowlist via WHATSAPP_OUTBOUND_ALLOWED_USERS /
WHATSAPP_ALLOWED_USERS) are enforced by the MCP server before it calls /send.
"""

import os
import pathlib
import sys

BRIDGE_JS = pathlib.Path(
    os.getenv("WHATSAPP_BRIDGE_JS", "/opt/hermes/scripts/whatsapp-bridge/bridge.js")
)
MARKER = "_mag_whatsapp_outbound"


def main() -> None:
    if not BRIDGE_JS.exists():
        sys.exit(f"FATAL: bridge.js not found at {BRIDGE_JS}")
    text = BRIDGE_JS.read_text()

    # Contract check: the /send route the MCP server relies on must exist.
    SEND_ANCHOR = "app.post('/send',"
    if SEND_ANCHOR not in text:
        sys.exit(
            "FATAL: WhatsApp bridge has no POST /send route. "
            "Upstream bridge.js changed — update patch_whatsapp_outbound.py and the MCP server."
        )

    print(f"Patching {BRIDGE_JS} ({MARKER})")
    print("  [ok]   /send contract check (route present — no injection needed)")


if __name__ == "__main__":
    main()
