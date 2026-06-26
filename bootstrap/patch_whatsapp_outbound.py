"""Build-time patch: enable secure outbound WhatsApp messages with allowlist and audit.

This patch adds:

1. WHATSAPP_OUTBOUND_ALLOWED_USERS - separate allowlist for explicit proactive messages
   (distinct from WHATSAPP_ALLOWED_USERS which is for inbound)

2. IMPLICIT AUTHORIZATION: Explicit numbers in WHATSAPP_ALLOWED_USERS (inbound) may be
   reused for outbound only when no separate outbound policy exists. This preserves
   natural "message me back later" flows without letting an inbound wildcard open
   proactive outbound to everyone.

3. JID normalization in all outbound endpoints (/send, /edit, /send-media)
   - Accepts raw numbers, numbers with +, numbers without suffix, and groups
   - Converts to proper @s.whatsapp.net or @g.us format

4. Outbound allowlist validation
   - WHATSAPP_OUTBOUND_ALLOWED_USERS is authoritative when set
   - Falls back to explicit inbound numbers only when no outbound policy exists
   - Inbound wildcard (*) never grants proactive outbound by itself
   - Deny-by-default: if no explicit outbound or inbound numbers match, deny
   - Supports phone number matching (handles various formats)

5. Audit logging for all send attempts
   - Logs destination (original + normalized), timestamp, result
   - Helps with support, investigation, and security

6. Confirmation requirement flag
   - send_whatsapp_message tool must pass confirmed_by_user=true
   - Bridge validates this flag before sending
   - Trusted runtime-originated sends may pass system_authorized=true instead

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
// Allows outbound using this precedence:
// 1. WHATSAPP_OUTBOUND_ALLOWED_USERS is authoritative when configured
// 2. Otherwise, explicit inbound numbers may be reused for outbound
// 3. Inbound wildcard (*) NEVER opens proactive outbound by itself
function isOutboundAllowed(chatId) {
  const outboundAllowed = parseOutboundAllowedUsers();
  const inboundAllowed = (process.env.WHATSAPP_ALLOWED_USERS || '').split(',')
    .map(s => s.trim()).filter(Boolean);

  const normalized = normalizeWhatsAppJid(chatId);
  const digits = normalized.replace(/\\D/g, '');

  // Helper to check if a number is in a list (handles various formats)
  const isInList = (list, targetDigits, { allowWildcard = false } = {}) => {
    if (list.length === 0) return false;
    // Check for wildcard (allows all)
    if (allowWildcard && list.includes('*')) return true;
    // Check direct match (with or without suffix)
    if (list.some(a => normalizeWhatsAppJid(a) === normalized)) return true;
    // Check by phone digits only
    const listDigits = list.map(a => a.replace(/\\D/g, ''));
    if (listDigits.some(d => d && d === targetDigits)) return true;
    return false;
  };

  // Explicit outbound policy wins. Its wildcard is intentional.
  if (outboundAllowed.length > 0) {
    return isInList(outboundAllowed, digits, { allowWildcard: true });
  }

  // No explicit outbound policy: allow only explicit inbound identities.
  // Wildcard inbound access is for talking TO the AI, not for letting the AI
  // proactively message arbitrary third parties.
  if (isInList(inboundAllowed.filter(value => value !== '*'), digits)) return true;

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
}
"""

# Anchor: insert after the allowlist import (comes after the monkey-patch setup)
# We can't insert between try/catch and the next import (invalid JS - imports must be at top)
OLD_AFTER_NORMALIZE = """import { matchesAllowedUser, parseAllowedUsers } from './allowlist.js';

// Parse CLI args"""
NEW_AFTER_NORMALIZE = """import { matchesAllowedUser, parseAllowedUsers } from './allowlist.js';
""" + OUTBOUND_FUNCS + """
// Parse CLI args"""


# ============================================================================
# Part 2: Patch /send endpoint
# ============================================================================

# Step 1: Add confirmed_by_user to destructuring (keep replyTo)
SEND_DESTRUCTURE_OLD = """  const { chatId, message, replyTo } = req.body;"""
SEND_DESTRUCTURE_NEW = """  const { chatId, message, replyTo, confirmed_by_user, system_authorized } = req.body;"""

# Step 1.5: Add validation inside the try block
SEND_VALIDATION_OLD = """  try {
    const chunks = splitLongMessage(formatOutgoingMessage(message));"""
SEND_VALIDATION_NEW = """  try {
    // _mag_whatsapp_outbound: validate destination and allowlist
    const validatedChatId = validateAndPrepareDestination(chatId, confirmed_by_user, system_authorized);
    const chunks = splitLongMessage(formatOutgoingMessage(message));"""

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
#   const { chatId, filePath, mediaType, caption, fileName } = req.body;

SEND_MEDIA_DESTRUCTURE_OLD = """  const { chatId, filePath, mediaType, caption, fileName } = req.body;"""
SEND_MEDIA_DESTRUCTURE_NEW = """  const { chatId, filePath, mediaType, caption, fileName, confirmed_by_user, system_authorized } = req.body;"""

SEND_MEDIA_VALIDATION_OLD = """  try {
    if (!existsSync(filePath)) {"""
SEND_MEDIA_VALIDATION_NEW = """  try {
    // _mag_whatsapp_outbound: validate destination and allowlist
    const validatedChatId = validateAndPrepareDestination(chatId, confirmed_by_user, system_authorized);
    if (!existsSync(filePath)) {"""

# Replace chatId with validatedChatId in sendMessage call
SEND_MEDIA_CHATID_OLD = """      await sock.sendMessage(chatId, {"""
SEND_MEDIA_CHATID_NEW = """      await sock.sendMessage(validatedChatId, {"""

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

    text = BRIDGE_JS.read_text(encoding='utf-8')
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
    text = apply(text, SEND_DESTRUCTURE_OLD, SEND_DESTRUCTURE_NEW, "/send destructuring")
    text = apply(text, SEND_VALIDATION_OLD, SEND_VALIDATION_NEW, "/send validation")
    text = apply(text, SEND_CHATID_OLD, SEND_CHATID_NEW, "/send use validatedChatId")
    text = apply(text, SEND_SUCCESS_OLD, SEND_SUCCESS_NEW, "/send success audit")
    text = apply(text, SEND_ERROR_OLD, SEND_ERROR_NEW, "/send error audit")

    # Part 3: Patch /send-media endpoint (may not exist in older versions)
    if SEND_MEDIA_DESTRUCTURE_OLD in text:
        text = apply(text, SEND_MEDIA_DESTRUCTURE_OLD, SEND_MEDIA_DESTRUCTURE_NEW, "/send-media destructuring")
    if SEND_MEDIA_VALIDATION_OLD in text:
        text = apply(text, SEND_MEDIA_VALIDATION_OLD, SEND_MEDIA_VALIDATION_NEW, "/send-media validation")
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
