"""Build-time patch: harden the WhatsApp (Baileys) bridge + expose QR/status.

This patch keeps the bridge alive across disconnects and startup failures.
It also exposes QR/status endpoints for the MAG web pairing flow.

Key protections:
1. loggedOut no longer exits the process; it clears the dead session and re-arms QR pairing
2. reconnects use backoff + jitter instead of a fixed loop
3. async bridge boot is always wrapped in a guarded scheduler with retry
4. unhandledRejection / uncaughtException are logged and routed into the same retry path
5. pair-only mode still runs the local HTTP server for the web QR flow

Idempotent + fail-loud.
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
            f"Upstream bridge.js changed - update patch_whatsapp_bridge.py."
        )
    print(f"  [ok]   {label}")
    return text.replace(old, new, 1)


OLD_STATE = "let sock = null;\nlet connectionState = 'disconnected';\n"
NEW_STATE = """let sock = null;
let connectionState = 'disconnected';
let lastQr = null;            // _mag_whatsapp_resilient: latest QR string for /qr
let reconnectAttempts = 0;    // backoff counter for transient disconnects
let pendingStartTimer = null; // one scheduled socket restart at a time

function computeReconnectDelay() {
  const base = Math.min(60000, 1000 * Math.pow(2, Math.min(reconnectAttempts, 6)));
  return Math.round(base / 2 + Math.random() * (base / 2));
}

function scheduleSocketStart(delayMs = 0, trigger = 'startup') {
  if (pendingStartTimer) return;
  const run = () => {
    pendingStartTimer = null;
    Promise.resolve()
      .then(() => startSocket())
      .catch((err) => {
        lastQr = null;
        connectionState = 'error';
        reconnectAttempts += 1;
        const delay = computeReconnectDelay();
        const detail = err && (err.stack || err.message || String(err));
        console.error(`[MAG] startSocket failed (${trigger}):`, detail);
        console.log(`[MAG] Scheduling bridge restart #${reconnectAttempts} in ${delay}ms...`);
        scheduleSocketStart(delay, `${trigger}:retry`);
      });
  };
  if (delayMs > 0) {
    pendingStartTimer = setTimeout(run, delayMs);
    return;
  }
  run();
}

function handleFatalBridgeError(trigger, err) {
  lastQr = null;
  connectionState = 'error';
  reconnectAttempts += 1;
  const detail = err && (err.stack || err.message || String(err));
  const delay = computeReconnectDelay();
  console.error(`[MAG] WhatsApp bridge fatal error (${trigger}):`, detail);
  scheduleSocketStart(delay, trigger);
}

process.on('unhandledRejection', (reason) => {
  handleFatalBridgeError('unhandledRejection', reason);
});
process.on('uncaughtException', (err) => {
  handleFatalBridgeError('uncaughtException', err);
});
"""


OLD_CONN = """    if (qr) {
      console.log('\\n📱 Scan this QR code with WhatsApp on your phone:\\n');
      qrcode.generate(qr, { small: true });
      console.log('\\nWaiting for scan...\\n');
    }

    if (connection === 'close') {
      const reason = new Boom(lastDisconnect?.error)?.output?.statusCode;
      connectionState = 'disconnected';

      if (reason === DisconnectReason.loggedOut) {
        console.log('❌ Logged out. Delete session and restart to re-authenticate.');
        process.exit(1);
      } else {
        // 515 = restart requested (common after pairing). Always reconnect.
        if (reason === 515) {
          console.log('↻ WhatsApp requested restart (code 515). Reconnecting...');
        } else {
          console.log(`⚠️  Connection closed (reason: ${reason}). Reconnecting in 3s...`);
        }
        setTimeout(startSocket, reason === 515 ? 1000 : 3000);
      }
    } else if (connection === 'open') {
      connectionState = 'connected';
      console.log('✅ WhatsApp connected!');
"""

NEW_CONN = """    if (qr) {
      lastQr = qr;                 // _mag_whatsapp_resilient: expose to /qr for the web
      connectionState = 'qr';
      console.log('\\n📱 Scan this QR code with WhatsApp on your phone:\\n');
      qrcode.generate(qr, { small: true });
      console.log('\\nWaiting for scan...\\n');
    }

    if (connection === 'close') {
      const reason = new Boom(lastDisconnect?.error)?.output?.statusCode;
      connectionState = 'disconnected';
      lastQr = null;

      if (reason === DisconnectReason.loggedOut) {
        // _mag_whatsapp_resilient: a logged-out session is dead. Re-arm pairing
        // instead of exiting so the bridge comes back with a fresh QR.
        console.log('❌ Logged out - clearing session and re-arming pairing (QR).');
        try {
          if (existsSync(SESSION_DIR)) {
            for (const f of readdirSync(SESSION_DIR)) { try { unlinkSync(path.join(SESSION_DIR, f)); } catch (e) {} }
          }
        } catch (e) { console.log('   (failed to clear session:', e && e.message, ')'); }
        connectionState = 'logged_out';
        reconnectAttempts = 0;
        scheduleSocketStart(1500, 'logged_out');
      } else if (reason === 515) {
        console.log('↻ WhatsApp requested restart (code 515). Reconnecting...');
        reconnectAttempts = 0;
        scheduleSocketStart(1000, 'restart_requested');
      } else {
        reconnectAttempts += 1;
        const delay = computeReconnectDelay();
        if (reconnectAttempts >= 10) connectionState = 'error';
        console.log(`⚠️  Connection closed (reason: ${reason}). Reconnect #${reconnectAttempts} in ${delay}ms...`);
        scheduleSocketStart(delay, 'connection_close');
      }
    } else if (connection === 'open') {
      connectionState = 'connected';
      lastQr = null;
      reconnectAttempts = 0;
      console.log('✅ WhatsApp connected!');
"""


OLD_HEALTH = "// Health check\napp.get('/health', (req, res) => {\n"
NEW_HEALTH = """// _mag_whatsapp_resilient: expose QR + connection state so the gateway can proxy
// them to the web (QR pairing + live status).
app.get('/qr', (req, res) => {
  res.json({ qr: lastQr, status: connectionState });
});
app.get('/status', (req, res) => {
  res.json({ status: connectionState, me: (sock && sock.user && sock.user.id) || null, uptime: process.uptime() });
});

// Health check
app.get('/health', (req, res) => {
"""


OLD_PAIR = """if (PAIR_ONLY) {
  // Pair-only mode: just connect, show QR, save creds, exit. No HTTP server.
  console.log('📱 WhatsApp pairing mode');
  console.log(`📁 Session: ${SESSION_DIR}`);
  console.log();
  startSocket();
} else {
"""

NEW_PAIR = """if (PAIR_ONLY) {
  // _mag_whatsapp_resilient: run the HTTP server even while pairing, so the gateway
  // can fetch /qr + /status for the web flow.
  console.log('📱 WhatsApp pairing mode (HTTP server on for web QR)');
  console.log(`📁 Session: ${SESSION_DIR}`);
  app.listen(PORT, '127.0.0.1', () => console.log(`🌉 WhatsApp bridge (pair) listening on port ${PORT}`));
  scheduleSocketStart(0, 'pair_boot');
} else {
"""


OLD_NORMAL_START = """} else {
  app.listen(PORT, '127.0.0.1', () => {
    console.log(`🌉 WhatsApp bridge listening on port ${PORT} (mode: ${WHATSAPP_MODE})`);
    console.log(`📁 Session stored in: ${SESSION_DIR}`);
    if (ALLOWED_USERS.size > 0) {
      console.log(`🔒 Allowed users: ${Array.from(ALLOWED_USERS).join(', ')}`);
    } else if (WHATSAPP_MODE === 'self-chat') {
      console.log(`🔒 Self-chat mode — only your own messages to yourself are processed.`);
    } else {
      console.log(`🔒 No WHATSAPP_ALLOWED_USERS set — incoming messages are rejected.`);
      console.log(`   Set WHATSAPP_ALLOWED_USERS=<phone> to authorize specific users,`);
      console.log(`   or WHATSAPP_ALLOWED_USERS=* for an explicit open bot.`);
    }
    console.log();
    startSocket();
  });
}
"""

NEW_NORMAL_START = """} else {
  app.listen(PORT, '127.0.0.1', () => {
    console.log(`🌉 WhatsApp bridge listening on port ${PORT} (mode: ${WHATSAPP_MODE})`);
    console.log(`📁 Session stored in: ${SESSION_DIR}`);
    if (ALLOWED_USERS.size > 0) {
      console.log(`🔒 Allowed users: ${Array.from(ALLOWED_USERS).join(', ')}`);
    } else if (WHATSAPP_MODE === 'self-chat') {
      console.log(`🔒 Self-chat mode — only your own messages to yourself are processed.`);
    } else {
      console.log(`🔒 No WHATSAPP_ALLOWED_USERS set — incoming messages are rejected.`);
      console.log(`   Set WHATSAPP_ALLOWED_USERS=<phone> to authorize specific users,`);
      console.log(`   or WHATSAPP_ALLOWED_USERS=* for an explicit open bot.`);
    }
    console.log();
    scheduleSocketStart(0, 'boot');
  });
}
"""


def main() -> None:
    if not BRIDGE_JS.exists():
        sys.exit(f"FATAL: bridge.js not found at {BRIDGE_JS}")
    text = BRIDGE_JS.read_text()
    print(f"Patching {BRIDGE_JS} ({MARKER})")
    text = apply(text, OLD_STATE, NEW_STATE, "global state + guarded startup helpers")
    text = apply(text, OLD_CONN, NEW_CONN, "connection.update (resilient reconnect)")
    text = apply(text, OLD_HEALTH, NEW_HEALTH, "/qr + /status endpoints")
    text = apply(text, OLD_PAIR, NEW_PAIR, "HTTP server in pair mode")
    text = apply(text, OLD_NORMAL_START, NEW_NORMAL_START, "guarded normal-mode startup")
    BRIDGE_JS.write_text(text)
    print("  WhatsApp bridge patched.")


if __name__ == "__main__":
    main()
