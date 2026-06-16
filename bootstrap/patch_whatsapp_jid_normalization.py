"""Build-time patch: normalize WhatsApp JIDs to prevent jidDecode failures.

WhatsApp requires properly formatted JIDs:
  - Groups: <digits>@g.us
  - DMs: <digits>@s.whatsapp.net
  - Newsletter/lists: <digits>@lid

When users send messages to groups listed without the @g.us suffix (e.g. "120363407454678781"),
jidDecode fails with "Cannot destructure property 'user' of 'jidDecode(...)' as it is undefined."

This patch adds a normalizeWhatsAppJid function that:
  - Appends @g.us to 18+ digit numbers (groups)
  - Appends @s.whatsapp.net to 10-15 digit numbers (phone numbers)
  - Preserves existing suffixes (@g.us, @s.whatsapp.net, @lid)

The function is applied wherever jidDecode is called or where JIDs are used.

Idempotent + fail-loud.
"""

import os
import pathlib
import re
import sys

BRIDGE_JS = pathlib.Path(
    os.getenv("WHATSAPP_BRIDGE_JS", "/opt/hermes/scripts/whatsapp-bridge/bridge.js")
)
MARKER = "_mag_whatsapp_jid_normalize"


def apply(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"  [skip] {label}: already patched")
        return text
    if old not in text:
        sys.exit(
            f"FATAL: WhatsApp bridge anchor not found for '{label}'. "
            f"Upstream bridge.js changed — update patch_whatsapp_jid_normalization.py."
        )
    print(f"  [ok]   {label}")
    return text.replace(old, new, 1)


# --- Edit 1: Add normalizeWhatsAppJid function (after imports) -------------------
# The bridge.js uses ES6 imports, so we anchor after the qrcode-terminal import
OLD_IMPORTS = "import qrcode from 'qrcode-terminal';\n"
NEW_IMPORTS = (
    "import qrcode from 'qrcode-terminal';\n"
    "\n"
    "// _mag_whatsapp_jid_normalize: Normalize WhatsApp JIDs to prevent jidDecode failures.\n"
    "// Groups need @g.us suffix, DMs need @s.whatsapp.net or @lid. Auto-detect by length.\n"
    "function normalizeWhatsAppJid(jid) {\n"
    "  if (!jid || typeof jid !== 'string') return jid;\n"
    "  // Already has suffix - preserve as-is\n"
    "  if (jid.includes('@')) return jid;\n"
    "  // Strip any non-digit characters for length check\n"
    "  const digits = jid.replace(/\\D/g, '');\n"
    "  if (!digits) return jid;\n"
    "  // Groups are typically 18+ digits (e.g. 120363407454678781 -> 18 digits)\n"
    "  // Phone numbers are 10-15 digits (country code + number)\n"
    "  if (digits.length >= 17) {\n"
    "    // Group JID\n"
    "    return digits + '@g.us';\n"
    "  } else if (digits.length >= 10 && digits.length <= 15) {\n"
    "    // DM/phone JID\n"
    "    return digits + '@s.whatsapp.net';\n"
    "  }\n"
    "  // Fallback - return original if we can't determine type\n"
    "  return jid;\n"
    "}\n"
    "\n"
)


# --- Edit 2: Wrap jidDecode calls with normalization ---------------------------
# This is a common pattern in Baileys that can fail with bare numbers


# --- Edit 3: Normalize chatId in message sending ---------------------------------
# This is informational - actual normalization happens via jidDecode wrapping above
# If the bridge uses a sendMessage function, we add a comment reminder
OLD_SEND_MSG = "async function sendMessage("
NEW_SEND_MSG = (
    "async function sendMessage(\n"
    "  // _mag_whatsapp_jid_normalize: chatId should have @g.us (groups) or @s.whatsapp.net (DMs)\n"
    "  // The jidDecode wrapping above handles normalization automatically.\n"
)


def main() -> None:
    if not BRIDGE_JS.exists():
        sys.exit(f"FATAL: bridge.js not found at {BRIDGE_JS}")
    text = BRIDGE_JS.read_text()
    print(f"Patching {BRIDGE_JS} ({MARKER})")

    # Add the normalize function after imports
    text = apply(text, OLD_IMPORTS, NEW_IMPORTS, "normalizeWhatsAppJid function")

    # Wrap jidDecode calls with normalization (if any exist)
    # Pattern: jidDecode(<argument(s)>) -> jidDecode(normalizeWhatsAppJid(<argument(s)>))
    # We match the full call including arguments and closing paren
    jiddecode_count = text.count("jidDecode(")
    already_wrapped = text.count("jidDecode(normalizeWhatsAppJid(")
    if jiddecode_count > already_wrapped:
        # Pattern to match jidDecode(...) with balanced parentheses
        # This matches: jidDecode( followed by anything until matching )
        # And checks idempotency: if already contains normalizeWhatsAppJid, skip
        def wrap_jiddecode(match):
            # match.group(0) is the full call: jidDecode(... )
            full_call = match.group(0)
            # Idempotency: if already contains normalizeWhatsAppJid, don't modify
            if 'normalizeWhatsAppJid' in full_call:
                return full_call
            # Extract the arguments (remove "jidDecode(" at start and ")" at end)
            args = full_call[len("jidDecode("):-1]
            return f"jidDecode(normalizeWhatsAppJid({args}))"

        # Pattern: jidDecode( ... ) with balanced parens
        pattern = r'\bjidDecode\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)'
        text = re.sub(pattern, wrap_jiddecode, text)
        wrapped = jiddecode_count - already_wrapped
        print(f"  [ok]   wrapped {wrapped} jidDecode() calls")
    else:
        print(f"  [skip] jidDecode wrapping (not found or already patched)")

    # If sendMessage function exists, add a comment about normalization
    # Only patch if sendMessage exists and isn't already patched
    if "async function sendMessage(" in text and "_mag_whatsapp_jid_normalize" not in text:
        # Check if the comment is already there
        send_msg_section = text.split("async function sendMessage(")[1].split("\n")[0] if "async function sendMessage(" in text else ""
        if "_mag_whatsapp_jid_normalize" not in send_msg_section:
            text = apply(text, "async function sendMessage(", NEW_SEND_MSG, "sendMessage comment")
        else:
            print(f"  [skip] sendMessage comment: already patched")
    else:
        print(f"  [skip] sendMessage comment (not found or already patched)")

    BRIDGE_JS.write_text(text)
    print("  WhatsApp JID normalization patched.")


if __name__ == "__main__":
    main()
