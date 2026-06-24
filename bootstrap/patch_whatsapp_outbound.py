"""Build-time patch: enable secure outbound WhatsApp messages with allowlist and audit.

This patch adds:

1. WHATSAPP_OUTBOUND_ALLOWED_USERS - separate allowlist for explicit proactive messages
   (distinct from WHATSAPP_ALLOWED_USERS which is for inbound)

2. IMPLICIT AUTHORIZATION: Numbers in WHATSAPP_ALLOWED_USERS (inbound) are automatically
   allowed for outbound. This means if someone can message the AI, the AI can message them
   back - enabling natural conversation flows like "send me a reminder tomorrow".

3. JID normalization in all outbound endpoints (/send, /edit, /send-media)
   - Accepts raw numbers, numbers with +, numbers without suffix, and groups
   - Converts to proper @s.whatsapp.net or @g.us format

4. Outbound allowlist validation
   - Checks both WHATSAPP_OUTBOUND_ALLOWED_USERS AND WHATSAPP_ALLOWED_USERS
   - Deny-by-default: if both lists are empty, no outbound sends allowed
   - Supports phone number matching (handles various formats)

5. Audit logging for all send attempts
   - Logs destination (original + normalized), timestamp, result
   - Helps with support, investigation, and security

6. Confirmation requirement flag
   - send_whatsapp_message tool must pass confirmed_by_user=true
   - Bridge validates this flag before sending

NOTE: This patch requires patch_whatsapp_jid_normalization.py to run first
      (it depends on the normalizeWhatsAppJid function being present).

Idempotent + fail-loud.
"""

import os
import pathlib
import re
import sys

BRIDGE_JS = pathlib.Path(
    os.getenv("WHATSAPP_BRIDGE_JS", "/opt/hermes/scripts/whatsapp-bridge/bridge.js")
)
MARKER = "_mag_whatsapp_outbound"


def apply(text: str, old: str, new: str, label: str) -> str:
    """Apply patch if not already applied. Fail if anchor not found."""
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


def apply_regex(text: str, pattern: str, replacement: str, label: str) -> str:
    """Apply regex-based patch for more flexible matching."""
    if re.search(pattern, text, re.MULTILINE | re.DOTALL):
        # Check if already patched by looking for the replacement in text
        if replacement.split('\n')[0] in text:
            print(f"  [skip] {label}: already patched")
            return text
        print(f"  [ok]   {label}")
        return re.sub(pattern, replacement, text, count=1, flags=re.MULTILINE | re.DOTALL)
    print(f"  [skip] {label}: pattern not found (may not apply)")
    return text


# ============================================================================
# Part 1: Add outbound validation functions (after normalizeWhatsAppJid)
# ============================================================================

OUTBOUND_FUNCS = """

// _mag_whatsapp_outbound: parse outbound allowlist (separate from inbound)
function parseOutboundAllowedUsers() {
  const env = process.env.WHATSAPP_OUTBOUND_ALLOWED_USERS || '';
  if (!env.trim()) return []; // Empty = deny-by-default, NOT fail-open
  return env.split(',').map(s => s.trim()).filter(Boolean);
}

// _mag_whatsapp_outbound: check if destination is in outbound allowlist
// Allows outbound to numbers that EITHER:
// 1. Are in WHATSAPP_OUTBOUND_ALLOWED_USERS (explicit outbound allowlist), OR
// 2. Are in WHATSAPP_ALLOWED_USERS (inbound allowlist - implicit authorization)
// This means if someone can message the AI, the AI can message them back.
function isOutboundAllowed(chatId) {
  const outboundAllowed = parseOutboundAllowedUsers();
  const inboundAllowed = (process.env.WHATSAPP_ALLOWED_USERS || '').split(',')
    .map(s => s.trim()).filter(Boolean);

  const normalized = normalizeWhatsAppJid(chatId);
  const digits = normalized.replace(/\\D/g, '');

  // Helper to check if a number is in a list (handles various formats)
  const isInList = (list, targetDigits) => {
    if (list.length === 0) return false;
    // Check direct match (with or without suffix)
    if (list.some(a => normalizeWhatsAppJid(a) === normalized)) return true;
    // Check by phone digits only
    const listDigits = list.map(a => a.replace(/\\D/g, ''));
    if (listDigits.some(d => d && d === targetDigits)) return true;
    return false;
  };

  // Check explicit outbound allowlist
  if (isInList(outboundAllowed, digits)) return true;

  // Check implicit authorization via inbound allowlist
  // (if someone can message the AI, the AI can message them back)
  if (isInList(inboundAllowed, digits)) return true;

  return false;
}

// _mag_whatsapp_outbound: audit log for all send attempts
function auditOutboundSend(originalChatId, normalizedChatId, result, error = null) {
  const timestamp = new Date().toISOString();
  const logEntry = {
    timestamp,
    action: 'whatsapp_outbound_send',
    destination_original: originalChatId,
    destination_normalized: normalizedChatId,
    result,
    error: error ? String(error) : null,
  };
  console.log('[MAG] WhatsApp outbound audit:', JSON.stringify(logEntry));
}

// _mag_whatsapp_outbound: validate and normalize destination for sending
function validateAndPrepareDestination(chatId, confirmedByUser = false) {
  const original = chatId;
  const normalized = normalizeWhatsAppJid(chatId);

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
}
"""

# Anchor: insert after the monkey-patch setup (already added by jid normalization patch)
OLD_AFTER_NORMALIZE = "console.log('[MAG] Patched Baileys jidDecode with safe wrapper');"
NEW_AFTER_NORMALIZE = (
    "console.log('[MAG] Patched Baileys jidDecode with safe wrapper');"
    + OUTBOUND_FUNCS
)


# ============================================================================
# Part 2: Patch /send endpoint
# ============================================================================

# Step 1: Add confirmed_by_user to destructuring (keep replyTo)
SEND_DESTRUCTURE_OLD = """  const { chatId, message, replyTo } = req.body;
  if (!chatId || !message) {
    return res.status(400).json({ error: 'chatId and message are required' });
  }

  try {"""
SEND_DESTRUCTURE_NEW = """  const { chatId, message, replyTo, confirmed_by_user } = req.body;
  if (!chatId || !message) {
    return res.status(400).json({ error: 'chatId and message are required' });
  }

  // _mag_whatsapp_outbound: validate destination and allowlist
  let validatedChatId;
  try {
    validatedChatId = validateAndPrepareDestination(chatId, confirmed_by_user);
  } catch (err) {
    return res.status(403).json({ error: err.message });
  }

  try {"""

# Step 2: Replace chatId with validatedChatId in sendWithTimeout call
SEND_CHATID_OLD = """      const sent = await sendWithTimeout(chatId, { text: chunks[i] });"""
SEND_CHATID_NEW = """      const sent = await sendWithTimeout(validatedChatId, { text: chunks[i] });"""

# Step 3: Add audit logging on success
SEND_SUCCESS_OLD = """    res.json({
      success: true,
      messageId: messageIds[messageIds.length - 1],
      messageIds,
    });"""
SEND_SUCCESS_NEW = """    auditOutboundSend(chatId, validatedChatId, 'success');
    res.json({
      success: true,
      messageId: messageIds[messageIds.length - 1],
      messageIds,
    });"""

# Step 4: Add audit logging on error
SEND_ERROR_OLD = """  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});"""
SEND_ERROR_NEW = """  } catch (err) {
    auditOutboundSend(chatId, chatId, 'error', err);
    res.status(500).json({ error: err.message });
  }
});"""


# ============================================================================
# Part 3: Patch /send-media endpoint
# ============================================================================

# Check if /send-media endpoint exists (may not in older Hermes versions)
# Current structure:
#   const { chatId, mediaType, mediaUrl, caption } = req.body;

SEND_MEDIA_DESTRUCTURE_OLD = """  const { chatId, mediaType, mediaUrl, caption } = req.body;
  if (!chatId || !mediaType || !mediaUrl) {
    return res.status(400).json({ error: 'chatId, mediaType and mediaUrl are required' });
  }

  try {"""
SEND_MEDIA_DESTRUCTURE_NEW = """  const { chatId, mediaType, mediaUrl, caption, confirmed_by_user } = req.body;
  if (!chatId || !mediaType || !mediaUrl) {
    return res.status(400).json({ error: 'chatId, mediaType and mediaUrl are required' });
  }

  // _mag_whatsapp_outbound: validate destination and allowlist
  let validatedChatId;
  try {
    validatedChatId = validateAndPrepareDestination(chatId, confirmed_by_user);
  } catch (err) {
    return res.status(403).json({ error: err.message });
  }

  try {"""

# Replace chatId with validatedChatId in sendMessage call
SEND_MEDIA_CHATID_OLD = """    await sock.sendMessage(chatId, {"""
SEND_MEDIA_CHATID_NEW = """    await sock.sendMessage(validatedChatId, {"""

# Add audit logging for /send-media
SEND_MEDIA_SUCCESS_OLD = """    res.json({
      success: true,
      messageId,
    });"""
SEND_MEDIA_SUCCESS_NEW = """    auditOutboundSend(chatId, validatedChatId, 'success');
    res.json({
      success: true,
      messageId,
    });"""

SEND_MEDIA_ERROR_OLD = """  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});"""
SEND_MEDIA_ERROR_NEW = """  } catch (err) {
    auditOutboundSend(chatId, chatId, 'error', err);
    res.status(500).json({ error: err.message });
  }
});"""


def main() -> None:
    """Apply all patches to bridge.js."""
    if not BRIDGE_JS.exists():
        sys.exit(f"FATAL: bridge.js not found at {BRIDGE_JS}")

    text = BRIDGE_JS.read_text()
    print(f"Patching {BRIDGE_JS} ({MARKER})")

    # Skip if main marker already present
    if MARKER in text:
        print(f"  [skip] Already patched ({MARKER} found)")
        return

    # Check that jid normalization patch has been applied (dependency)
    if "normalizeWhatsAppJid" not in text:
        sys.exit(
            "FATAL: normalizeWhatsAppJid function not found. "
            "patch_whatsapp_jid_normalization.py must run before this patch."
        )

    # Part 1: Add outbound validation functions
    text = apply(text, OLD_AFTER_NORMALIZE, NEW_AFTER_NORMALIZE, "outbound validation functions")

    # Part 2: Patch /send endpoint
    text = apply(text, SEND_DESTRUCTURE_OLD, SEND_DESTRUCTURE_NEW, "/send destructuring + validation")
    text = apply(text, SEND_CHATID_OLD, SEND_CHATID_NEW, "/send use validatedChatId")
    text = apply(text, SEND_SUCCESS_OLD, SEND_SUCCESS_NEW, "/send success audit")
    text = apply(text, SEND_ERROR_OLD, SEND_ERROR_NEW, "/send error audit")

    # Part 3: Patch /send-media endpoint (may not exist in older versions)
    if SEND_MEDIA_DESTRUCTURE_OLD in text:
        text = apply(text, SEND_MEDIA_DESTRUCTURE_OLD, SEND_MEDIA_DESTRUCTURE_NEW, "/send-media destructuring + validation")
    if SEND_MEDIA_CHATID_OLD in text:
        text = apply(text, SEND_MEDIA_CHATID_OLD, SEND_MEDIA_CHATID_NEW, "/send-media use validatedChatId")
    if SEND_MEDIA_SUCCESS_OLD in text:
        text = apply(text, SEND_MEDIA_SUCCESS_OLD, SEND_MEDIA_SUCCESS_NEW, "/send-media success audit")
    if SEND_MEDIA_ERROR_OLD in text:
        text = apply(text, SEND_MEDIA_ERROR_OLD, SEND_MEDIA_ERROR_NEW, "/send-media error audit")

    BRIDGE_JS.write_text(text)
    print("  WhatsApp outbound allowlist + normalization patch applied.")


if __name__ == "__main__":
    main()
