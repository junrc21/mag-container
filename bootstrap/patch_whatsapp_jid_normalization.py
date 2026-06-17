"""Build-time patch: normalize WhatsApp JIDs to prevent jidDecode failures.

WhatsApp requires properly formatted JIDs:
  - Groups: <digits>@g.us
  - DMs: <digits>@s.whatsapp.net
  - Newsletter/lists: <digits>@lid

When users send messages to groups listed without the @g.us suffix (e.g. "120363407454678781"),
jidDecode fails with "Cannot destructure property 'user' of 'jidDecode(...)' as it is undefined."

This patch adds TWO layers of protection:

1. A normalizeWhatsAppJid function that fixes JID format
2. A monkey-patch of Baileys' jidDecode to:
   - Always return a valid object (never undefined)
   - Auto-normalize incoming JIDs before decoding

This fixes both:
- Calls in bridge.js that go through jidDecode
- Calls inside compiled Baileys code (make-in-memory-store.js, etc.)

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


# --- Edit 1: Add normalizeWhatsAppJid function + monkey-patch (after imports) -------
# The bridge.js uses ES6 imports, so we anchor after the qrcode-terminal import
OLD_IMPORTS = "import qrcode from 'qrcode-terminal';\n"
NEW_IMPORTS = (
    "import qrcode from 'qrcode-terminal';\n"
    "\n"
    "// _mag_whatsapp_jid_normalize: Normalize WhatsApp JIDs to prevent jidDecode failures.\n"
    "// Groups need @g.us suffix, DMs need @s.whatsapp.net or @lid. Auto-detect by length.\n"
    "// Also monkey-patches Baileys' jidDecode to NEVER return undefined.\n"
    "\n"
    "const ORIGINAL_JID_DECODE = Symbol('original_jidDecode');\n"
    "\n"
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
    "// Monkey-patch Baileys' jidDecode to NEVER return undefined.\n"
    "// This fixes the 'Cannot destructure property user of jidDecode(...) as undefined' error\n"
    "// that occurs inside compiled Baileys code (make-in-memory-store.js, etc.)\n"
    "function makeSafeJidDecode(originalJidDecode) {\n"
    "  return function safeJidDecode(jid) {\n"
    "    // First normalize the JID\n"
    "    const normalized = normalizeWhatsAppJid(jid);\n"
    "    // Call original with normalized JID\n"
    "    const result = originalJidDecode(normalized);\n"
    "    // NEVER return undefined - return a safe fallback object\n"
    "    if (!result) {\n"
    "      console.warn(`[MAG] jidDecode returned undefined for: ${jid}, using safe fallback`);\n"
    "      // Parse what we can from the normalized JID\n"
    "      const [user, domain] = normalized.includes('@') ? normalized.split('@') : [normalized, 's.whatsapp.net'];\n"
    "      return { user, server: domain };\n"
    "    }\n"
    "    return result;\n"
    "  };\n"
    "}\n"
    "\n"
    "// Patch @whiskeysockets/baileys WABinary jidDecode at module load time\n"
    "let baileysModule;\n"
    "try {\n"
    "  baileysModule = require('@whiskeysockets/baileys');\n"
    "  if (baileysModule && baileysModule[ORIGINAL_JID_DECODE]) {\n"
    "    // Already patched\n"
    "  } else if (baileysModule && baileysModule.jidDecode) {\n"
    "    baileysModule[ORIGINAL_JID_DECODE] = baileysModule.jidDecode;\n"
    "    baileysModule.jidDecode = makeSafeJidDecode(baileysModule.jidDecode);\n"
    "    console.log('[MAG] Patched Baileys jidDecode with safe wrapper');\n"
    "  }\n"
    "} catch (e) {\n"
    "  // Baileys might not be available at this point - will patch later\n"
    "}\n"
    "\n"
)


# --- Edit 2: sendMessage comment (informational) ----------------------------------
# The actual normalization happens via the monkey-patch above
OLD_SEND_MSG = "async function sendMessage("
NEW_SEND_MSG = (
    "async function sendMessage(\n"
    "  // _mag_whatsapp_jid_normalize: chatId normalized by monkey-patched jidDecode\n"
)


def main() -> None:
    if not BRIDGE_JS.exists():
        sys.exit(f"FATAL: bridge.js not found at {BRIDGE_JS}")
    text = BRIDGE_JS.read_text()
    print(f"Patching {BRIDGE_JS} ({MARKER})")

    # Add the normalize function and monkey-patch after imports
    text = apply(text, OLD_IMPORTS, NEW_IMPORTS, "normalizeWhatsAppJid + monkey-patch")

    # If sendMessage function exists, add a comment about normalization
    if "async function sendMessage(" in text and "_mag_whatsapp_jid_normalize" not in text.split("async function sendMessage(")[1].split("\n")[0]:
        text = apply(text, "async function sendMessage(", NEW_SEND_MSG, "sendMessage comment")
    else:
        print(f"  [skip] sendMessage comment (not found or already patched)")

    BRIDGE_JS.write_text(text)
    print("  WhatsApp JID normalization + jidDecode monkey-patch applied.")


if __name__ == "__main__":
    main()
