"""Build-time patch: harden the WhatsApp (Baileys) bridge + expose QR/status (MAG).

WhatsApp is a critical channel and a previous version "dropped" randomly: the bridge
hit ``DisconnectReason.loggedOut`` and did ``process.exit(1)`` — dying silently, never
re-pairing. This patch makes the Baileys bridge resilient and web-pairable:

  1. On loggedOut: DON'T exit. Wipe the dead session creds and re-arm pairing (re-init
     the socket → a fresh QR is emitted). State becomes 'logged_out' so the gateway /
     control plane can alert staff + the web can show "reconnect". No more silent drop.
  2. Reconnect with exponential backoff + jitter (capped at 60s) instead of a fixed 3s,
     so a flaky network can't cause a reconnect storm; after many tries -> state 'error'.
  3. Track the latest QR string + a richer connection state in memory.
  4. Expose ``GET /qr`` ({qr, status}) and ``GET /status`` ({status, me, uptime}) on the
     bridge's existing Express server, so the gateway can proxy them to the web.
  5. Even in --pair-only mode, run the HTTP server (upstream skipped it), so the web
     pairing flow can fetch the QR while pairing.

Idempotent + fail-loud (mirrors the other bootstrap patches): re-running applies only
the missing edits; an anchor that is neither original nor patched aborts the build so an
upstream Baileys/bridge change is caught, not silently dropped.
"""

import os
import pathlib
import sys

BRIDGE_JS = pathlib.Path(
    os.getenv("WHATSAPP_BRIDGE_JS", "/opt/hermes/scripts/whatsapp-bridge/bridge.js")
)
MARKER = "_mag_whatsapp_resilient"


def apply(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"  [skip] {label}: already patched")
        return text
    if old not in text:
        sys.exit(
            f"FATAL: WhatsApp bridge anchor not found for '{label}'. "
            f"Upstream bridge.js changed — update patch_whatsapp_bridge.py."
        )
    print(f"  [ok]   {label}")
    return text.replace(old, new, 1)


# --- Edit 1: global state (lastQr + reconnect counter) ------------------------
OLD_STATE = "let sock = null;\nlet connectionState = 'disconnected';\n"
NEW_STATE = (
    "let sock = null;\n"
    "let connectionState = 'disconnected';\n"
    "let lastQr = null;            // _mag_whatsapp_resilient: latest QR string for /qr\n"
    "let reconnectAttempts = 0;    // backoff counter for transient disconnects\n"
)

# --- Edit 2: capture QR + the whole close/open block --------------------------
OLD_CONN = (
    "    if (qr) {\n"
    "      console.log('\\n📱 Scan this QR code with WhatsApp on your phone:\\n');\n"
    "      qrcode.generate(qr, { small: true });\n"
    "      console.log('\\nWaiting for scan...\\n');\n"
    "    }\n"
    "\n"
    "    if (connection === 'close') {\n"
    "      const reason = new Boom(lastDisconnect?.error)?.output?.statusCode;\n"
    "      connectionState = 'disconnected';\n"
    "\n"
    "      if (reason === DisconnectReason.loggedOut) {\n"
    "        console.log('❌ Logged out. Delete session and restart to re-authenticate.');\n"
    "        process.exit(1);\n"
    "      } else {\n"
    "        // 515 = restart requested (common after pairing). Always reconnect.\n"
    "        if (reason === 515) {\n"
    "          console.log('↻ WhatsApp requested restart (code 515). Reconnecting...');\n"
    "        } else {\n"
    "          console.log(`⚠️  Connection closed (reason: ${reason}). Reconnecting in 3s...`);\n"
    "        }\n"
    "        setTimeout(startSocket, reason === 515 ? 1000 : 3000);\n"
    "      }\n"
    "    } else if (connection === 'open') {\n"
    "      connectionState = 'connected';\n"
    "      console.log('✅ WhatsApp connected!');\n"
)
NEW_CONN = (
    "    if (qr) {\n"
    "      lastQr = qr;                 // _mag_whatsapp_resilient: expose to /qr for the web\n"
    "      connectionState = 'qr';\n"
    "      console.log('\\n📱 Scan this QR code with WhatsApp on your phone:\\n');\n"
    "      qrcode.generate(qr, { small: true });\n"
    "      console.log('\\nWaiting for scan...\\n');\n"
    "    }\n"
    "\n"
    "    if (connection === 'close') {\n"
    "      const reason = new Boom(lastDisconnect?.error)?.output?.statusCode;\n"
    "      connectionState = 'disconnected';\n"
    "      lastQr = null;\n"
    "\n"
    "      if (reason === DisconnectReason.loggedOut) {\n"
    "        // _mag_whatsapp_resilient: a logged-out session is DEAD. Don't exit silently\n"
    "        // (that was the random drop). Wipe the dead creds and re-arm pairing so a\n"
    "        // fresh QR is emitted; gateway/control-plane surface 'logged_out' to staff/web.\n"
    "        console.log('❌ Logged out — clearing session and re-arming pairing (QR).');\n"
    "        try {\n"
    "          if (existsSync(SESSION_DIR)) {\n"
    "            for (const f of readdirSync(SESSION_DIR)) { try { unlinkSync(path.join(SESSION_DIR, f)); } catch (e) {} }\n"
    "          }\n"
    "        } catch (e) { console.log('   (failed to clear session:', e && e.message, ')'); }\n"
    "        connectionState = 'logged_out';\n"
    "        reconnectAttempts = 0;\n"
    "        setTimeout(startSocket, 1500);\n"
    "      } else if (reason === 515) {\n"
    "        console.log('↻ WhatsApp requested restart (code 515). Reconnecting...');\n"
    "        reconnectAttempts = 0;\n"
    "        setTimeout(startSocket, 1000);\n"
    "      } else {\n"
    "        // Exponential backoff + jitter, capped — survive flaky networks, no storm.\n"
    "        reconnectAttempts += 1;\n"
    "        const base = Math.min(60000, 1000 * Math.pow(2, Math.min(reconnectAttempts, 6)));\n"
    "        const delay = Math.round(base / 2 + Math.random() * (base / 2));\n"
    "        if (reconnectAttempts >= 10) connectionState = 'error';\n"
    "        console.log(`⚠️  Connection closed (reason: ${reason}). Reconnect #${reconnectAttempts} in ${delay}ms...`);\n"
    "        setTimeout(startSocket, delay);\n"
    "      }\n"
    "    } else if (connection === 'open') {\n"
    "      connectionState = 'connected';\n"
    "      lastQr = null;\n"
    "      reconnectAttempts = 0;\n"
    "      console.log('✅ WhatsApp connected!');\n"
)

# --- Edit 3: /qr + /status endpoints (before /health) -------------------------
OLD_HEALTH = "// Health check\napp.get('/health', (req, res) => {\n"
NEW_HEALTH = (
    "// _mag_whatsapp_resilient: expose QR + connection state so the gateway can proxy\n"
    "// them to the web (QR pairing + live status).\n"
    "app.get('/qr', (req, res) => {\n"
    "  res.json({ qr: lastQr, status: connectionState });\n"
    "});\n"
    "app.get('/status', (req, res) => {\n"
    "  res.json({ status: connectionState, me: (sock && sock.user && sock.user.id) || null, uptime: process.uptime() });\n"
    "});\n"
    "\n"
    "// Health check\n"
    "app.get('/health', (req, res) => {\n"
)

# --- Edit 4: run the HTTP server even in --pair-only --------------------------
OLD_PAIR = (
    "if (PAIR_ONLY) {\n"
    "  // Pair-only mode: just connect, show QR, save creds, exit. No HTTP server.\n"
    "  console.log('📱 WhatsApp pairing mode');\n"
    "  console.log(`📁 Session: ${SESSION_DIR}`);\n"
    "  console.log();\n"
    "  startSocket();\n"
    "} else {\n"
)
NEW_PAIR = (
    "if (PAIR_ONLY) {\n"
    "  // _mag_whatsapp_resilient: run the HTTP server even while pairing, so the gateway\n"
    "  // can fetch /qr + /status for the web flow (upstream skipped the server here).\n"
    "  console.log('📱 WhatsApp pairing mode (HTTP server on for web QR)');\n"
    "  console.log(`📁 Session: ${SESSION_DIR}`);\n"
    "  app.listen(PORT, '127.0.0.1', () => console.log(`🌉 WhatsApp bridge (pair) listening on port ${PORT}`));\n"
    "  startSocket();\n"
    "} else {\n"
)


def main() -> None:
    if not BRIDGE_JS.exists():
        sys.exit(f"FATAL: bridge.js not found at {BRIDGE_JS}")
    text = BRIDGE_JS.read_text()
    print(f"Patching {BRIDGE_JS} ({MARKER})")
    text = apply(text, OLD_STATE, NEW_STATE, "global state (lastQr)")
    text = apply(text, OLD_CONN, NEW_CONN, "connection.update (resilient reconnect)")
    text = apply(text, OLD_HEALTH, NEW_HEALTH, "/qr + /status endpoints")
    text = apply(text, OLD_PAIR, NEW_PAIR, "HTTP server in pair mode")
    BRIDGE_JS.write_text(text)
    print("  WhatsApp bridge patched.")


if __name__ == "__main__":
    main()
