"""Build-time patch: add POST /send to the WhatsApp bridge for proactive outbound messaging.

The MAG control-plane can tell the agent "send a message to <number>" and the agent
calls the MCP tool `send_whatsapp_message`, which POSTs to this endpoint.  The bridge
then validates the allowlist and delivers via the Baileys socket.

Authorization model (union — number needs to appear in AT LEAST one list):
  - WHATSAPP_OUTBOUND_ALLOWED_USERS  (explicit, managed by the tenant owner in the panel)
  - WHATSAPP_ALLOWED_USERS           (implicit: who can talk to the AI can receive outbound)
Wildcard '*' in either list = allow any destination.
Both lists empty = deny all (deny-by-default).

Prerequisites (all applied before this patch in the Dockerfile):
  - patch_whatsapp_bridge.py    → adds /qr + /status routes; resilient reconnect;
                                   provides `connectionState` and `sock` global vars.
  - patch_whatsapp_jid_normalization.py → adds `normalizeWhatsAppJid()` to bridge.js.

Idempotent + fail-loud (mirrors the other bootstrap patches): re-running skips already
patched edits; a missing anchor aborts the build so an upstream bridge.js change is
caught immediately instead of silently dropped.
"""

import os
import pathlib
import sys

BRIDGE_JS = pathlib.Path(
    os.getenv("WHATSAPP_BRIDGE_JS", "/opt/hermes/scripts/whatsapp-bridge/bridge.js")
)
MARKER = "_mag_whatsapp_outbound"


def apply(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"  [skip] {label}: already patched")
        return text
    if old not in text:
        sys.exit(
            f"FATAL: WhatsApp bridge anchor not found for '{label}'. "
            f"Upstream bridge.js changed — update patch_whatsapp_outbound.py."
        )
    print(f"  [ok]   {label}")
    return text.replace(old, new, 1)


# Anchor: the /status route added by patch_whatsapp_bridge.py (always present after it runs).
# We insert /send between /status and /health.
OLD_SEND = (
    "app.get('/status', (req, res) => {\n"
    "  res.json({ status: connectionState, me: (sock && sock.user && sock.user.id) || null, uptime: process.uptime() });\n"
    "});\n"
    "\n"
    "// Health check\n"
    "app.get('/health', (req, res) => {\n"
)

NEW_SEND = (
    "app.get('/status', (req, res) => {\n"
    "  res.json({ status: connectionState, me: (sock && sock.user && sock.user.id) || null, uptime: process.uptime() });\n"
    "});\n"
    "\n"
    "// _mag_whatsapp_outbound: proactive send with allowlist guard.\n"
    "// Authorization: number must appear in WHATSAPP_OUTBOUND_ALLOWED_USERS (explicit)\n"
    "// OR WHATSAPP_ALLOWED_USERS (implicit — if they can talk to the AI, the AI can reply).\n"
    "// Wildcard '*' in either list allows any destination. Both lists empty = deny all.\n"
    "app.post('/send', async (req, res) => {\n"
    "  const { chatId, message, confirmed_by_user } = req.body || {};\n"
    "\n"
    "  if (!confirmed_by_user)\n"
    "    return res.status(400).json({ error: 'confirmed_by_user must be true' });\n"
    "  if (!chatId || typeof chatId !== 'string' || !chatId.trim())\n"
    "    return res.status(400).json({ error: 'chatId is required' });\n"
    "  if (!message || typeof message !== 'string' || !message.trim())\n"
    "    return res.status(400).json({ error: 'message is required' });\n"
    "  if (!sock || connectionState !== 'connected')\n"
    "    return res.status(503).json({ error: `WhatsApp not connected (state: ${connectionState})` });\n"
    "\n"
    "  const digits = chatId.replace(/\\D/g, '');\n"
    "  if (!digits)\n"
    "    return res.status(400).json({ error: 'chatId must contain at least one digit' });\n"
    "\n"
    "  // Allowlist check (union of both lists).\n"
    "  const outList = (process.env.WHATSAPP_OUTBOUND_ALLOWED_USERS || '')\n"
    "    .split(',').map(s => s.trim()).filter(Boolean);\n"
    "  const inList  = (process.env.WHATSAPP_ALLOWED_USERS || '')\n"
    "    .split(',').map(s => s.trim()).filter(Boolean);\n"
    "  const allowed =\n"
    "    outList.includes('*') || inList.includes('*') ||\n"
    "    outList.some(n => n.replace(/\\D/g, '') === digits) ||\n"
    "    inList.some(n  => n.replace(/\\D/g, '') === digits);\n"
    "  if (!allowed) {\n"
    "    console.log(`[mag-outbound] BLOCKED send to ${chatId} — not in allowlist`);\n"
    "    return res.status(403).json({ error: 'Número não está na lista de destinos permitidos' });\n"
    "  }\n"
    "\n"
    "  try {\n"
    "    // normalizeWhatsAppJid injected by patch_whatsapp_jid_normalization.py.\n"
    "    const jid = normalizeWhatsAppJid(digits + '@s.whatsapp.net');\n"
    "    const result = await sock.sendMessage(jid, { text: message });\n"
    "    console.log(`[mag-outbound] Sent to ${jid}: ${String(message).slice(0, 60)}`);\n"
    "    return res.json({ success: true, messageId: result?.key?.id ?? null });\n"
    "  } catch (err) {\n"
    "    console.error(`[mag-outbound] Error sending to ${chatId}:`, err && err.message);\n"
    "    return res.status(500).json({ error: (err && err.message) || 'send failed' });\n"
    "  }\n"
    "});\n"
    "\n"
    "// Health check\n"
    "app.get('/health', (req, res) => {\n"
)


def main() -> None:
    if not BRIDGE_JS.exists():
        sys.exit(f"FATAL: bridge.js not found at {BRIDGE_JS}")
    text = BRIDGE_JS.read_text()
    print(f"Patching {BRIDGE_JS} ({MARKER})")
    text = apply(text, OLD_SEND, NEW_SEND, "/send endpoint")
    BRIDGE_JS.write_text(text)
    print("  WhatsApp outbound patch applied.")


if __name__ == "__main__":
    main()
