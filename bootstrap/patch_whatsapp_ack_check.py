"""Patch bridge.js to detect and surface WhatsApp server delivery errors to /send callers.

Problem: sock.sendMessage() in Baileys resolves when the message is queued LOCALLY, not
when the WA server acknowledges delivery. Errors like 463 (RESTRICT_ALL_COMPANIONS) arrive
asynchronously via the Baileys logger. The bridge's /send route returned {"success":true}
immediately, so the MCP server — and therefore the agent — never learned of the failure.

Fix (3 parts):
  1. Replace the pino logger with a pino-stream-intercepting wrapper that captures Baileys
     error-ACK log calls (logger.warn({id, error}, 'received error in ack')).
  2. Inject a sock.ev.on('messages.update', ...) listener inside startSocket() as a
     belt-and-suspenders fallback for status=0 (ERROR) delivery receipts.
  3. In the /send route, await a Promise ONLY when confirmed_by_user===true (proactive
     outbound from the MCP server). Gateway replies go through /send too — the ACK-wait
     must NOT apply to them or every agent reply is delayed by 8 seconds unnecessarily.

Tuning: set WHATSAPP_ACK_WAIT_MS env var (default 8000 ms). Long enough to catch error
463 RESTRICT_ALL_COMPANIONS which arrives asynchronously from the WA server.
"""

import os
import pathlib
import sys

BRIDGE_JS = pathlib.Path(
    os.getenv("WHATSAPP_BRIDGE_JS", "/opt/hermes/scripts/whatsapp-bridge/bridge.js")
)
MARKER = "_mag_ack_check"


# ── Part 1: pino stream interceptor ─────────────────────────────────────────

OLD_LOGGER = "const logger = pino({ level: 'warn' });"

NEW_LOGGER = """\
// _mag_ack_check: pending-outbound map and pino stream interceptor.
// Baileys logs delivery errors as logger.warn({from, id, error}, 'received error in ack').
// We parse every pino log line written to stdout and, when we spot an {id, error} pair,
// immediately reject the in-flight Promise registered by /send for that messageId.
const _magPendingOutbound = new Map();
const _MAG_ACK_WAIT_MS = parseInt(process.env.WHATSAPP_ACK_WAIT_MS || '8000', 10);
const _magPinoStream = {
  write(data) {
    process.stdout.write(data);
    try {
      const str = typeof data === 'string' ? data : data.toString('utf8');
      const obj = JSON.parse(str);
      // Baileys pino error format: {attrs: {id, error}, msg: 'received error in ack'}
      const _aId = obj?.attrs?.id || obj?.id;
      const _aErr = obj?.attrs?.error || obj?.error;
      if (_aId && _aErr) {
        console.log('[bridge-ack] pino error: id=' + _aId + ' error=' + _aErr);
        const _p = _magPendingOutbound.get(_aId);
        if (_p) { _magPendingOutbound.delete(_aId); _p.reject(new Error(`WA error ${_aErr}`)); }
        else { console.log('[bridge-ack] late error (after window): id=' + _aId); }
      }
    } catch (_e) { /* non-JSON or wrong shape — ignore */ }
  }
};
const logger = pino({ level: 'warn' }, _magPinoStream);\
"""


# ── Part 2: messages.update listener inside startSocket() ────────────────────

# Injected just before the closing `}` of startSocket (after messages.upsert closes).
OLD_SOCK_CLOSE = """\
    }
  });
}

// HTTP server"""

NEW_SOCK_CLOSE = """\
    }
  });

  // _mag_ack_check: belt-and-suspenders — catch status=0 (ERROR) delivery receipts
  // that arrive via the Baileys event bus rather than the logger.
  sock.ev.on('messages.update', (_updates) => {
    for (const _upd of (_updates || [])) {
      const _id = _upd?.key?.id;
      if (!_id) continue;
      const _p = _magPendingOutbound.get(_id);
      if (!_p) continue;
      if (_upd?.update?.status === 0) {
        _magPendingOutbound.delete(_id);
        _p.reject(new Error('WA rejeitou a entrega (status ERROR)'));
      }
    }
  });
}

// HTTP server"""


# ── Part 3: ACK-wait in the /send route ──────────────────────────────────────

OLD_SEND_RESP = """\
    res.json({
      success: true,
      messageId: messageIds[messageIds.length - 1],
      messageIds,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Edit a previously sent message"""

NEW_SEND_RESP = """\
    // _mag_ack_check: only apply ACK-wait for proactive outbound (confirmed_by_user===true).
    // Gateway replies also use /send — without this guard every agent reply would be delayed 8s.
    // Errors like 463 arrive via the pino interceptor or messages.update; no error within
    // the window resolves the Promise and we return success.
    const _magLastId = messageIds[messageIds.length - 1];
    if (_magLastId && req.body?.confirmed_by_user === true) {
      try {
        await new Promise((resolve, reject) => {
          _magPendingOutbound.set(_magLastId, { resolve, reject });
          setTimeout(() => { _magPendingOutbound.delete(_magLastId); resolve(null); }, _MAG_ACK_WAIT_MS);
        });
      } catch (_ackErr) {
        return res.status(500).json({ error: `Falha no envio: ${_ackErr.message}` });
      }
    }
    res.json({
      success: true,
      messageId: messageIds[messageIds.length - 1],
      messageIds,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Edit a previously sent message"""


def main() -> None:
    if not BRIDGE_JS.exists():
        sys.exit(f"FATAL: bridge.js not found at {BRIDGE_JS}")

    text = BRIDGE_JS.read_text()

    if MARKER in text:
        print(f"Patching {BRIDGE_JS} ({MARKER})")
        print("  [skip] already patched — idempotent")
        return

    # Validate all 3 anchors before mutating anything.
    missing = []
    if OLD_LOGGER not in text:
        missing.append("logger declaration anchor")
    if OLD_SOCK_CLOSE not in text:
        missing.append("startSocket close anchor")
    if OLD_SEND_RESP not in text:
        missing.append("/send success response anchor")
    if missing:
        sys.exit(
            f"FATAL [{MARKER}]: anchor(s) not found in bridge.js — "
            f"upstream changed: {', '.join(missing)}. "
            "Update patch_whatsapp_ack_check.py."
        )

    text = text.replace(OLD_LOGGER, NEW_LOGGER, 1)
    text = text.replace(OLD_SOCK_CLOSE, NEW_SOCK_CLOSE, 1)
    text = text.replace(OLD_SEND_RESP, NEW_SEND_RESP, 1)

    BRIDGE_JS.write_text(text)
    print(f"Patching {BRIDGE_JS} ({MARKER})")
    print("  [ok]   pino stream interceptor for error-ACK detection")
    print("  [ok]   messages.update listener (belt-and-suspenders STATUS=0)")
    print("  [ok]   /send ACK-wait (8 s window, conditional on confirmed_by_user, env: WHATSAPP_ACK_WAIT_MS)")


if __name__ == "__main__":
    main()
