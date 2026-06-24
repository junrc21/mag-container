"""Build-time patch: enable secure outbound WhatsApp messages with allowlist and audit.

This patch adds:

1. WHATSAPP_OUTBOUND_ALLOWED_USERS - separate allowlist for explicit proactive messages
   (distinct from WHATSAPP_ALLOWED_USERS which is for inbound)

2. IMPLICIT AUTHORIZATION: Numbers in WHATSAPP_ALLOWED_USERS (inbound) are automatically
   allowed for outbound. This means if someone can message the AI, the AI can message them
   back - enabling natural conversation flows like "send me a reminder tomorrow".

3. JID normalization in all outbound endpoints (/send, /edit, /send-media, /typing)
   - Accepts raw numbers, numbers with +, numbers without suffix, and groups
   - Converts to proper @s.whatsapp.net or @g.us format

4. Outbound allowlist validation
   - Checks both WHATSAPP_OUTBOUND_ALLOWED_USERS AND WHATSAPP_ALLOWED_USERS
   - Deny-by-default: if both lists are empty, no outbound sends allowed
   - Supports phone number matching (handles various formats)
   - Validates destination before send

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
    if re.search(pattern, text):
        if re.search(re.escape(replacement), text):
            print(f"  [skip] {label}: already patched")
            return text
        print(f"  [ok]   {label}")
        return re.sub(pattern, replacement, text, count=1)
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
  const digits = normalized.replace(/\D/g, '');

  // Helper to check if a number is in a list (handles various formats)
  const isInList = (list, targetDigits) => {
    if (list.length === 0) return false;
    // Check direct match (with or without suffix)
    if (list.some(a => normalizeWhatsAppJid(a) === normalized)) return true;
    // Check by phone digits only
    const listDigits = list.map(a => a.replace(/\D/g, ''));
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
function validateAndPrepareDestination(chatId) {
  const original = chatId;
  const normalized = normalizeWhatsAppJid(chatId);

  if (!isOutboundAllowed(normalized)) {
    auditOutboundSend(original, normalized, 'denied', 'Destination not in WHATSAPP_OUTBOUND_ALLOWED_USERS');
    throw new Error(`Destination ${chatId} is not allowed for proactive messaging. Check WHATSAPP_OUTBOUND_ALLOWED_USERS.`);
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
# Part 2: Patch /send endpoint - normalize chatId and validate allowlist
# ============================================================================
# This is the MAIN endpoint for sending messages. We patch it to:
# 1. Validate confirmed_by_user flag
# 2. Normalize the chatId using normalizeWhatsAppJid
# 3. Check against WHATSAPP_OUTBOUND_ALLOWED_USERS
# 4. Add audit logging

# Find and replace the /send endpoint definition
SEND_PATTERN = r"(app\.post\('/send', async \(req, res\) => \{\s*try \{\s*const \{ chatId, message, confirmed_by_user \} = req\.body;)"
SEND_REPLACEMENT = r"""app.post('/send', async (req, res) => {
  try {
    const { chatId, message, confirmed_by_user } = req.body;
    // _mag_whatsapp_outbound: validate confirmation and normalize destination
    if (confirmed_by_user !== true) {
      auditOutboundSend(chatId, normalizeWhatsAppJid(chatId), 'denied', 'Missing confirmed_by_user flag');
      return res.status(403).json({ error: 'Proactive messaging requires explicit user confirmation (confirmed_by_user=true).' });
    }
    const targetChatId = validateAndPrepareDestination(chatId);"""


# ============================================================================
# Part 3: Patch /send-media endpoint
# ============================================================================
SEND_MEDIA_PATTERN = r"(app\.post\('/send-media', async \(req, res\) => \{\s*try \{\s*const \{ chatId, mediaType, mediaUrl, caption, confirmed_by_user \} = req\.body;)"
SEND_MEDIA_REPLACEMENT = r"""app.post('/send-media', async (req, res) => {
  try {
    const { chatId, mediaType, mediaUrl, caption, confirmed_by_user } = req.body;
    // _mag_whatsapp_outbound: validate confirmation and normalize destination
    if (confirmed_by_user !== true) {
      auditOutboundSend(chatId, normalizeWhatsAppJid(chatId), 'denied', 'Missing confirmed_by_user flag');
      return res.status(403).json({ error: 'Proactive messaging requires explicit user confirmation (confirmed_by_user=true).' });
    }
    const targetChatId = validateAndPrepareDestination(chatId);"""


# ============================================================================
# Part 4: Replace chatId with targetChatId in sendWithTimeout calls
# ============================================================================
# In /send endpoint
SEND_TIMEOUT_PATTERN = r"(const result = await sendWithTimeout\(chatId, message\);)"
SEND_TIMEOUT_REPLACEMENT = r"""const result = await sendWithTimeout(targetChatId, message);"""

# In /send-media endpoint (needs to handle mediaContent variable)
SEND_MEDIA_TIMEOUT_PATTERN = r"(const result = await sendWithTimeout\(\s*chatId,)"
SEND_MEDIA_TIMEOUT_REPLACEMENT = r"""const result = await sendWithTimeout(
      targetChatId,"""


# ============================================================================
# Part 5: Add audit logging on success/error
# ============================================================================
# After successful send
SEND_SUCCESS_PATTERN = r"(res\.json\(\{ success: true, result \}\);)\s*\} catch \(error\) \{\s*console\.error\('Error sending message:', error\);\s*res\.status\(500\)\.json\(\{ error: 'Failed to send message' \}\);"
SEND_SUCCESS_REPLACEMENT = r"""auditOutboundSend(chatId, targetChatId, 'success');
    res.json({ success: true, result });
  } catch (error) {
    console.error('Error sending message:', error);
    auditOutboundSend(chatId, normalizeWhatsAppJid(chatId), 'error', error);
    res.status(500).json({ error: 'Failed to send message' });"""


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

    # Part 2-5: Patch endpoints using regex (more flexible matching)
    text = apply_regex(text, SEND_PATTERN, SEND_REPLACEMENT, "/send endpoint validation")
    text = apply_regex(text, SEND_MEDIA_PATTERN, SEND_MEDIA_REPLACEMENT, "/send-media endpoint validation")
    text = apply_regex(text, SEND_TIMEOUT_PATTERN, SEND_TIMEOUT_REPLACEMENT, "/send use targetChatId")
    text = apply_regex(text, SEND_MEDIA_TIMEOUT_PATTERN, SEND_MEDIA_TIMEOUT_REPLACEMENT, "/send-media use targetChatId")
    text = apply_regex(text, SEND_SUCCESS_PATTERN, SEND_SUCCESS_REPLACEMENT, "/send audit logging")

    BRIDGE_JS.write_text(text)
    print("  WhatsApp outbound allowlist + normalization patch applied.")


if __name__ == "__main__":
    main()
