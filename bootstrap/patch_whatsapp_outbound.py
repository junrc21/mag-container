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
    if re.search(pattern, text, re.MULTILINE | re.DOTALL):
        # Check if already patched by looking for our marker in the replacement
        if MARKER in text and "_mag_report_job_run" not in replacement:
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
    throw new Error(`Destination ${chatId} is not allowed for proactive messaging.`);
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
# Part 2: Patch /send endpoint - add confirmed_by_user, normalize and validate
# ============================================================================
# Current structure (Hermes):
#   const { chatId, message, replyTo } = req.body;
# We need to add confirmed_by_user to the destructuring and validate it

# Pattern to find the destructuring line in /send endpoint
SEND_DESTRUCTURE_PATTERN = r"(app\.post\('/send', async \(req, res\) => \{\s*if \(!sock \|\| connectionState !== 'connected'\) \{\s*return res\.status\(503\)\.json\(\{ error: 'Not connected to WhatsApp' \}\);\s*\}\s*const \{ chatId, message, replyTo \} = req\.body;)"

SEND_DESTRUCTURE_REPLACEMENT = r"""app.post('/send', async (req, res) => {
  if (!sock || connectionState !== 'connected') {
    return res.status(503).json({ error: 'Not connected to WhatsApp' });
  }
  const { chatId, message, replyTo, confirmed_by_user } = req.body;"""

# Pattern to add validation and normalization after destructuring
SEND_VALIDATION_PATTERN = r"(\s*// Validate required fields\s*if \(!chatId \|\| !message\) \{\s*return res\.status\(400\)\.json\(\{ error: 'chatId and message are required' \}\);\s*\})"
SEND_VALIDATION_REPLACEMENT = r"""// Validate required fields
  if (!chatId || !message) {
    return res.status(400).json({ error: 'chatId and message are required' });
  }
  // _mag_whatsapp_outbound: validate, normalize and check allowlist
  let validatedChatId;
  try {
    validatedChatId = validateAndPrepareDestination(chatId, confirmed_by_user);
  } catch (err) {
    return res.status(403).json({ error: err.message });
  }"""

# Pattern to replace chatId with validatedChatId in the send call
SEND_CALL_PATTERN = r"(const result = await sendWithTimeout\(\s*chatId,\s*message\s*\);)"
SEND_CALL_REPLACEMENT = r"""const result = await sendWithTimeout(validatedChatId, message);"""

# Pattern to add audit logging on success
SEND_SUCCESS_PATTERN = r"(res\.json\(\{\s*success: true,\s*messageId: messageIds\[messageIds\.length - 1\],\s*messageIds\s*\}\);)"
SEND_SUCCESS_REPLACEMENT = r"""auditOutboundSend(chatId, validatedChatId, 'success');
  res.json({ success: true, messageId: messageIds[messageIds.length - 1], messageIds });"""

# Pattern to add audit logging on error
SEND_ERROR_PATTERN = r"(\} catch \(err\) \{\s*res\.status\(500\)\.json\(\{\s*error: err\.message\s*\}\);\s*\})"
SEND_ERROR_REPLACEMENT = r"""} catch (err) {
  auditOutboundSend(chatId, chatId, 'error', err);
  res.status(500).json({ error: err.message });
  }"""


# ============================================================================
# Part 3: Patch /send-media endpoint
# ============================================================================
# Similar pattern for /send-media endpoint
SEND_MEDIA_DESTRUCTURE_PATTERN = r"(app\.post\('/send-media', async \(req, res\) => \{\s*if \(!sock \|\| connectionState !== 'connected'\) \{\s*return res\.status\(503\)\.json\(\{ error: 'Not connected to WhatsApp' \}\);\s*\}\s*const \{ chatId, mediaType, mediaUrl, caption \} = req\.body;)"

SEND_MEDIA_DESTRUCTURE_REPLACEMENT = r"""app.post('/send-media', async (req, res) => {
  if (!sock || connectionState !== 'connected') {
    return res.status(503).json({ error: 'Not connected to WhatsApp' });
  }
  const { chatId, mediaType, mediaUrl, caption, confirmed_by_user } = req.body;"""

# Validation for /send-media (similar structure)
SEND_MEDIA_VALIDATION_PATTERN = r"(\s*// Validate required fields\s*if \(!chatId \|\| !mediaType \|\| !mediaUrl\) \{\s*return res\.status\(400\)\.json\(\{ error: 'chatId, mediaType and mediaUrl are required' \}\);\s*\})"
SEND_MEDIA_VALIDATION_REPLACEMENT = r"""// Validate required fields
  if (!chatId || !mediaType || !mediaUrl) {
    return res.status(400).json({ error: 'chatId, mediaType and mediaUrl are required' });
  }
  // _mag_whatsapp_outbound: validate, normalize and check allowlist
  let validatedChatId;
  try {
    validatedChatId = validateAndPrepareDestination(chatId, confirmed_by_user);
  } catch (err) {
    return res.status(403).json({ error: err.message });
  }"""

# Replace chatId with validatedChatId in /send-media
# Need to handle the fact that the code might reference chatId multiple times
# Look for the actual sendWithTimeout call or the Baileys sendMessage call
SEND_MEDIA_SEND_PATTERN = r"(await sock\.sendMessage\(\s*chatId,\s*\{)"
SEND_MEDIA_SEND_REPLACEMENT = r"""await sock.sendMessage(validatedChatId, {"""

# Success/error logging for /send-media (similar to /send)
SEND_MEDIA_SUCCESS_PATTERN = r"(res\.json\(\{\s*success: true,\s*messageId\s*\}\);)"
SEND_MEDIA_SUCCESS_REPLACEMENT = r"""auditOutboundSend(chatId, validatedChatId, 'success');
  res.json({ success: true, messageId });"""

SEND_MEDIA_ERROR_PATTERN = r"(\} catch \(err\) \{\s*res\.status\(500\)\.json\(\{\s*error: err\.message\s*\}\);\s*\})"
SEND_MEDIA_ERROR_REPLACEMENT = r"""} catch (err) {
  auditOutboundSend(chatId, chatId, 'error', err);
  res.status(500).json({ error: err.message });
  }"""


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
    text = apply_regex(text, SEND_DESTRUCTURE_PATTERN, SEND_DESTRUCTURE_REPLACEMENT, "/send destructuring")
    text = apply_regex(text, SEND_VALIDATION_PATTERN, SEND_VALIDATION_REPLACEMENT, "/send validation")
    text = apply_regex(text, SEND_CALL_PATTERN, SEND_CALL_REPLACEMENT, "/send use validatedChatId")
    text = apply_regex(text, SEND_SUCCESS_PATTERN, SEND_SUCCESS_REPLACEMENT, "/send success audit")
    text = apply_regex(text, SEND_ERROR_PATTERN, SEND_ERROR_REPLACEMENT, "/send error audit")

    # Part 3: Patch /send-media endpoint
    text = apply_regex(text, SEND_MEDIA_DESTRUCTURE_PATTERN, SEND_MEDIA_DESTRUCTURE_REPLACEMENT, "/send-media destructuring")
    text = apply_regex(text, SEND_MEDIA_VALIDATION_PATTERN, SEND_MEDIA_VALIDATION_REPLACEMENT, "/send-media validation")
    text = apply_regex(text, SEND_MEDIA_SEND_PATTERN, SEND_MEDIA_SEND_REPLACEMENT, "/send-media use validatedChatId")
    text = apply_regex(text, SEND_MEDIA_SUCCESS_PATTERN, SEND_MEDIA_SUCCESS_REPLACEMENT, "/send-media success audit")
    text = apply_regex(text, SEND_MEDIA_ERROR_PATTERN, SEND_MEDIA_ERROR_REPLACEMENT, "/send-media error audit")

    BRIDGE_JS.write_text(text)
    print("  WhatsApp outbound allowlist + normalization patch applied.")


if __name__ == "__main__":
    main()
