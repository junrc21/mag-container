"""Build-time patch: WhatsApp cold reach-out safety throttle.

Problem: messaging a contact who has NEVER exchanged a message with this number before
("cold reach-out") is, per WhatsApp forum/GitHub-issue research, the single biggest
trigger for account restrictions/bans — far riskier than messaging an established
contact. The existing outbound allowlist (patch_whatsapp_outbound.py) already denies
proactive sends by default and requires the tenant to explicitly authorize destinations
— EXCEPT its `*` wildcard mode, which lets the agent message any brand-new number once a
tenant enables it. This patch adds an automatic warm/cold distinction and a safety
throttle for exactly that wildcard-authorized, cold-contact path.

This patch adds:

1. A persistent (survives container restart) contact-history store, one JSON file living
   BESIDE (not inside) the WhatsApp session directory, so a forced re-pair (new QR, same
   phone number) does not wipe contact memory — a real WhatsApp account's message history
   would obviously survive a re-login too.

2. An independent `messages.upsert` listener (Baileys supports multiple subscribers per
   event, so this does not touch the existing gateway/allowlist relay or the
   `messages.update` listener added by patch_whatsapp_ack_check.py) that records every
   inbound 1:1 message as proof the contact is "warm".

3. A safety gate inside `validateAndPrepareDestination`, exercised ONLY for destinations
   authorized purely by the outbound allowlist's `*` wildcard (explicit allowlist/inbound
   matches were deliberately named by the tenant and are never re-gated):
   - WHATSAPP_COLD_CONTACT_DAILY_CAP (default 5): max first-ever-contact sends / 24h.
   - WHATSAPP_OUTBOUND_MIN_DELAY_MS (default 3000): minimum spacing between ANY two
     proactive sends (warm or cold).
   - WHATSAPP_DAILY_SEND_CAP (default 80): total proactive sends / 24h (warm or cold).
   All three are env-var overridable heuristics informed by community anti-ban tooling
   norms, not official WhatsApp-published limits — tune after real usage data.

4. A `.code` on every throw in `validateAndPrepareDestination` (no_confirmation,
   not_allowlisted, cold_contact_cap_exceeded, send_rate_limited, daily_cap_exceeded) and
   a `reason` field on the /send and /send-media JSON error responses (429 for the three
   new throttle codes, unchanged 500 otherwise) — additive, backward compatible — so the
   agent's system prompt can give the tenant the RIGHT explanation for each case instead
   of one generic failure message.

NOTE: This patch requires BOTH patch_whatsapp_outbound.py (defines
      validateAndPrepareDestination/isOutboundAllowed/parseOutboundAllowedUsers) AND
      patch_whatsapp_ack_check.py (this patch anchors on its messages.update listener and
      its /send success-response text) to have already run.

Idempotent + fail-loud.
"""

import os
import pathlib
import sys

BRIDGE_JS = pathlib.Path(
    os.getenv("WHATSAPP_BRIDGE_JS", "/opt/hermes/scripts/whatsapp-bridge/bridge.js")
)
MARKER = "_mag_cold_contact_guard"


def apply(text: str, old: str, new: str, label: str) -> str:
    """Apply patch if not already applied. Fail if anchor not found."""
    if new in text:
        print(f"  [skip] {label}: already patched")
        return text
    if old not in text:
        sys.exit(
            f"FATAL [{MARKER}]: anchor not found for '{label}'. "
            f"Upstream bridge.js (or an earlier WhatsApp patch) changed — "
            f"update patch_whatsapp_cold_contact_guard.py."
        )
    print(f"  [ok]   {label}")
    return text.replace(old, new, 1)


# ============================================================================
# Part 1: contact-history store + helper functions, inserted right before
# validateAndPrepareDestination (defined by patch_whatsapp_outbound.py).
# ============================================================================

GUARD_FUNCS = """
// _mag_cold_contact_guard: persistent per-tenant contact history (survives container
// restarts) so we can tell a "warm" contact (prior inbound or outbound exchange) from a
// "cold" one (never exchanged a message) for the outbound safety throttle below. Lives
// BESIDE (not inside) SESSION_DIR so a forced re-pair (new QR, same phone number) does
// not wipe contact memory. Uses dynamic import() (not a static top-of-file import) so
// this block can be inserted mid-file; SESSION_DIR is read only inside the async
// continuation after the first `await`, which runs as a microtask AFTER the whole module
// body (including SESSION_DIR's own declaration, wherever it is) has finished executing —
// so this is safe regardless of where SESSION_DIR is declared in the file.
const _MAG_COLD_CAP = parseInt(process.env.WHATSAPP_COLD_CONTACT_DAILY_CAP || '5', 10);
const _MAG_MIN_DELAY_MS = parseInt(process.env.WHATSAPP_OUTBOUND_MIN_DELAY_MS || '3000', 10);
const _MAG_DAILY_CAP = parseInt(process.env.WHATSAPP_DAILY_SEND_CAP || '80', 10);
const _MAG_LOG_RETENTION_MS = 48 * 60 * 60 * 1000;
const _MAG_DAY_MS = 24 * 60 * 60 * 1000;

let _MAG_CONTACT_FILE = null;
let _magContactState = { version: 1, contacts: {}, outboundLog: [] };
const _magContactReady = (async () => {
  try {
    const fs = await import('node:fs');
    const path = await import('node:path');
    _MAG_CONTACT_FILE = path.join(path.dirname(SESSION_DIR), 'contact_history.json');
    const raw = fs.readFileSync(_MAG_CONTACT_FILE, 'utf8');
    const parsed = JSON.parse(raw);
    // Merge (don't replace) so any contact recorded during this brief boot race isn't lost.
    _magContactState.contacts = Object.assign({}, (parsed && parsed.contacts) || {}, _magContactState.contacts);
    _magContactState.outboundLog = ((parsed && parsed.outboundLog) || []).concat(_magContactState.outboundLog);
  } catch (_e) {
    console.log('[MAG] contact_history.json missing/unreadable, starting fresh:', _e && _e.message);
  }
})();

let _magContactWriteChain = Promise.resolve();
function _magPersistContacts() {
  const cutoff = Date.now() - _MAG_LOG_RETENTION_MS;
  _magContactState.outboundLog = _magContactState.outboundLog.filter((e) => e.ts >= cutoff);
  const snapshot = JSON.stringify(_magContactState);
  _magContactWriteChain = _magContactWriteChain.then(async () => {
    try {
      const fs = await import('node:fs');
      const path = await import('node:path');
      if (!_MAG_CONTACT_FILE) return; // path not resolved yet (extremely early boot)
      fs.mkdirSync(path.dirname(_MAG_CONTACT_FILE), { recursive: true });
      const tmp = _MAG_CONTACT_FILE + '.tmp';
      fs.writeFileSync(tmp, snapshot, 'utf8');
      fs.renameSync(tmp, _MAG_CONTACT_FILE);
    } catch (_e) {
      console.log('[MAG] failed to persist contact_history.json:', _e && _e.message);
    }
  }).catch(() => {});
}

function _magIsKnownContact(digits) {
  const c = _magContactState.contacts[digits];
  return !!(c && (c.inboundAt || c.outboundAt));
}

function _magRecordInbound(digits) {
  const now = new Date().toISOString();
  const c = _magContactState.contacts[digits] || (_magContactState.contacts[digits] = { firstSeenAt: now, inboundAt: null, outboundAt: null });
  c.inboundAt = now;
  _magPersistContacts();
}

function _magRecordOutbound(digits) {
  const now = Date.now();
  const wasCold = !_magIsKnownContact(digits);
  const iso = new Date(now).toISOString();
  const c = _magContactState.contacts[digits] || (_magContactState.contacts[digits] = { firstSeenAt: iso, inboundAt: null, outboundAt: null });
  c.outboundAt = iso;
  _magContactState.outboundLog.push({ ts: now, digits, cold: wasCold });
  _magPersistContacts();
}

function _magCountRecent(predicate, windowMs) {
  const cutoff = Date.now() - windowMs;
  return _magContactState.outboundLog.filter((e) => e.ts >= cutoff && predicate(e)).length;
}

function _magLastSendAt() {
  const log = _magContactState.outboundLog;
  return log.length ? log[log.length - 1].ts : 0;
}

// _mag_cold_contact_guard: re-derive whether a destination is authorized ONLY via the '*'
// wildcard entry in WHATSAPP_OUTBOUND_ALLOWED_USERS (no explicit outbound entry, no
// inbound-list match) — deliberately independent of isOutboundAllowed's internals/return
// shape (safer than changing that function's signature, since other call sites are not
// fully ruled out) — recomputes the same lists and match logic.
function _magIsWildcardOnlyMatch(chatId) {
  const outboundAllowed = parseOutboundAllowedUsers();
  if (!outboundAllowed.includes('*')) return false;
  const inboundAllowed = (process.env.WHATSAPP_ALLOWED_USERS || '').split(',')
    .map((s) => s.trim()).filter(Boolean);
  const normalized = normalizeWhatsAppJid(chatId);
  const digits = normalized.replace(/\\D/g, '');
  const explicitOutboundMatch = outboundAllowed.filter((v) => v !== '*')
    .some((a) => normalizeWhatsAppJid(a) === normalized || a.replace(/\\D/g, '') === digits);
  if (explicitOutboundMatch) return false;
  const inboundMatch = inboundAllowed.filter((v) => v !== '*')
    .some((a) => normalizeWhatsAppJid(a) === normalized || a.replace(/\\D/g, '') === digits);
  return !inboundMatch;
}
"""

OLD_BEFORE_VALIDATE = """// _mag_whatsapp_outbound: validate and normalize destination for sending
function validateAndPrepareDestination(chatId, confirmedByUser = false, systemAuthorized = false) {
  const original = chatId;
  const normalized = normalizeWhatsAppJid(chatId);

  if (systemAuthorized === true) {
    auditOutboundSend(original, normalized, 'authorized_runtime');
    return normalized;
  }

  // Require explicit confirmation for proactive messaging
  if (confirmedByUser !== true) {
    auditOutboundSend(original, normalized, 'denied', 'Missing confirmed_by_user flag');
    throw new Error('Proactive messaging requires explicit user confirmation (confirmed_by_user=true).');
  }

  if (!isOutboundAllowed(normalized)) {
    auditOutboundSend(original, normalized, 'denied', 'Destination not in outbound allowlist');
    throw new Error('Destination ' + chatId + ' is not allowed for proactive messaging.');
  }

  return normalized;
}"""

NEW_BEFORE_VALIDATE = GUARD_FUNCS + """
// _mag_whatsapp_outbound: validate and normalize destination for sending
function validateAndPrepareDestination(chatId, confirmedByUser = false, systemAuthorized = false) {
  const original = chatId;
  const normalized = normalizeWhatsAppJid(chatId);

  if (systemAuthorized === true) {
    auditOutboundSend(original, normalized, 'authorized_runtime');
    return normalized;
  }

  // Require explicit confirmation for proactive messaging
  if (confirmedByUser !== true) {
    auditOutboundSend(original, normalized, 'denied', 'Missing confirmed_by_user flag');
    const _magErr = new Error('Proactive messaging requires explicit user confirmation (confirmed_by_user=true).');
    _magErr.code = 'no_confirmation';
    throw _magErr;
  }

  if (!isOutboundAllowed(normalized)) {
    auditOutboundSend(original, normalized, 'denied', 'Destination not in outbound allowlist');
    const _magErr = new Error('Destination ' + chatId + ' is not allowed for proactive messaging.');
    _magErr.code = 'not_allowlisted';
    throw _magErr;
  }

  // _mag_cold_contact_guard: extra safety throttle, only for destinations authorized
  // purely by the '*' wildcard — explicit allowlist/inbound matches were deliberately
  // named by the tenant and are not re-gated here.
  const _magDigits = normalized.replace(/\\D/g, '');
  if (_magIsWildcardOnlyMatch(normalized) && !_magIsKnownContact(_magDigits)) {
    const _magColdCount = _magCountRecent((e) => e.cold, _MAG_DAY_MS);
    if (_magColdCount >= _MAG_COLD_CAP) {
      auditOutboundSend(original, normalized, 'denied', 'Cold-contact daily cap exceeded');
      const _magErr = new Error('Daily limit for messaging brand-new contacts has been reached.');
      _magErr.code = 'cold_contact_cap_exceeded';
      throw _magErr;
    }
  }
  if (Date.now() - _magLastSendAt() < _MAG_MIN_DELAY_MS) {
    auditOutboundSend(original, normalized, 'denied', 'Outbound send rate limit');
    const _magErr = new Error('Sending too fast — please wait a moment before the next message.');
    _magErr.code = 'send_rate_limited';
    throw _magErr;
  }
  if (_magCountRecent(() => true, _MAG_DAY_MS) >= _MAG_DAILY_CAP) {
    auditOutboundSend(original, normalized, 'denied', 'Daily send cap exceeded');
    const _magErr = new Error('Daily proactive-message limit has been reached.');
    _magErr.code = 'daily_cap_exceeded';
    throw _magErr;
  }

  return normalized;
}"""


# ============================================================================
# Part 2: inbound tracking — independent messages.upsert listener, inserted
# right after patch_whatsapp_ack_check.py's messages.update listener.
# ============================================================================

OLD_SOCK_CLOSE = """\
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

NEW_SOCK_CLOSE = """\
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

  // _mag_cold_contact_guard: independent listener (Baileys supports multiple
  // subscribers per event) — track inbound 1:1 messages so we can tell warm contacts
  // (someone who has messaged us before) from cold ones for the outbound safety throttle.
  sock.ev.on('messages.upsert', (_upsert) => {
    try {
      for (const _msg of (_upsert && _upsert.messages) || []) {
        const _remoteJid = _msg && _msg.key && _msg.key.remoteJid;
        const _fromMe = _msg && _msg.key && _msg.key.fromMe;
        if (!_remoteJid || _fromMe) continue;
        if (_remoteJid.endsWith('@g.us')) continue; // groups are out of scope for this guard
        const _normalized = normalizeWhatsAppJid(_remoteJid);
        const _digits = _normalized.replace(/\\D/g, '');
        if (_digits) _magRecordInbound(_digits);
      }
    } catch (_e) {
      console.log('[MAG] cold-contact-guard messages.upsert handler error:', _e && _e.message);
    }
  });
}

// HTTP server"""


# ============================================================================
# Part 3: outbound success tracking + failure-reason contract on /send.
# Anchored on patch_whatsapp_ack_check.py's full NEW_SEND_RESP text (unique —
# includes the ACK-wait block, so no ambiguity with /send-media below).
# ============================================================================

OLD_SEND_TAIL = """\
    auditOutboundSend(chatId, validatedChatId, 'success');
    res.json({
      success: true,
      messageId: messageIds[messageIds.length - 1],
      messageIds,
    });
  } catch (err) {
    auditOutboundSend(chatId, chatId, 'error', err);
    res.status(500).json({ error: err.message });
  }
});

// Edit a previously sent message"""

NEW_SEND_TAIL = """\
    _magRecordOutbound(validatedChatId.replace(/\\D/g, ''));
    auditOutboundSend(chatId, validatedChatId, 'success');
    res.json({
      success: true,
      messageId: messageIds[messageIds.length - 1],
      messageIds,
    });
  } catch (err) {
    auditOutboundSend(chatId, chatId, 'error', err);
    const _magReason = (err && err.code) || 'internal_error';
    const _magThrottled = _magReason === 'cold_contact_cap_exceeded' || _magReason === 'send_rate_limited' || _magReason === 'daily_cap_exceeded';
    res.status(_magThrottled ? 429 : 500).json({ error: err.message, reason: _magReason });
  }
});

// Edit a previously sent message"""


# ============================================================================
# Part 4: outbound success tracking + failure-reason contract on /send-media.
#
# NOTE: patch_whatsapp_outbound.py's own SEND_MEDIA_CHATID/SUCCESS sub-patches
# (documented in docs/whatsapp-outbound-implementation.md as applied) turn out to
# silently no-op against the real pinned base image — its /send-media route uses a
# different shape (`sendWithTimeout(chatId, msgPayload)` / `sent?.key?.id`, not the
# `sock.sendMessage(chatId, ...)` / separate `messageId` variable the patch assumed) and
# its ERROR-block sub-patch was a false-"already patched" positive (identical text to
# /send's own error block, patched moments earlier in the same script run, tricked the
# `if new in text: skip` idempotency check). Net effect in production today: the
# allowlist/confirmation GATE for /send-media genuinely works (that anchor DOES match),
# but `validatedChatId` is computed and then discarded — the actual send still goes out
# on the raw, non-normalized `chatId` — and there is no success/error audit log for media
# sends. That mismatch is a PRE-EXISTING bug outside this patch's scope (flagged
# separately); this patch anchors on the REAL current text (verified by extracting
# bridge.js from the pinned base image and applying the full patch chain locally) so our
# own hook lands correctly regardless.
# ============================================================================

OLD_SEND_MEDIA_TAIL = """\
    const sent = await sendWithTimeout(chatId, msgPayload);

    trackSentMessageId(sent);

    res.json({ success: true, messageId: sent?.key?.id });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});"""

NEW_SEND_MEDIA_TAIL = """\
    const sent = await sendWithTimeout(chatId, msgPayload);

    trackSentMessageId(sent);

    _magRecordOutbound(validatedChatId.replace(/\\D/g, ''));
    auditOutboundSend(chatId, validatedChatId, 'success');
    res.json({ success: true, messageId: sent?.key?.id });
  } catch (err) {
    auditOutboundSend(chatId, chatId, 'error', err);
    const _magReason = (err && err.code) || 'internal_error';
    const _magThrottled = _magReason === 'cold_contact_cap_exceeded' || _magReason === 'send_rate_limited' || _magReason === 'daily_cap_exceeded';
    res.status(_magThrottled ? 429 : 500).json({ error: err.message, reason: _magReason });
  }
});"""


def main() -> None:
    if not BRIDGE_JS.exists():
        sys.exit(f"FATAL: bridge.js not found at {BRIDGE_JS}")

    text = BRIDGE_JS.read_text(encoding="utf-8")
    print(f"Patching {BRIDGE_JS} ({MARKER})")

    if MARKER in text:
        print("  [skip] already patched — idempotent")
        return

    if "_mag_whatsapp_outbound" not in text:
        sys.exit(
            f"FATAL [{MARKER}]: patch_whatsapp_outbound.py must run before this patch "
            "(validateAndPrepareDestination/isOutboundAllowed not found)."
        )
    if "_mag_ack_check" not in text:
        sys.exit(
            f"FATAL [{MARKER}]: patch_whatsapp_ack_check.py must run before this patch "
            "(messages.update listener / ACK-wait /send response not found)."
        )

    text = apply(text, OLD_BEFORE_VALIDATE, NEW_BEFORE_VALIDATE, "contact-history store + safety gate")
    text = apply(text, OLD_SOCK_CLOSE, NEW_SOCK_CLOSE, "inbound messages.upsert listener")
    text = apply(text, OLD_SEND_TAIL, NEW_SEND_TAIL, "/send outbound tracking + failure reason")
    text = apply(text, OLD_SEND_MEDIA_TAIL, NEW_SEND_MEDIA_TAIL, "/send-media outbound tracking + failure reason")

    BRIDGE_JS.write_text(text, encoding="utf-8")
    print("  WhatsApp cold reach-out safety guard applied.")


if __name__ == "__main__":
    main()
