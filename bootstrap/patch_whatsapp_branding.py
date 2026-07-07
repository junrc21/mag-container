"""Build-time patch: rebrand the WhatsApp bridge from 'Hermes Agent' to 'MAG'.

Two changes in bridge.js:

1. browser array passed to makeWASocket — this is what WhatsApp shows in the pairing
   notification and under Settings > Linked Devices. Default is ['Hermes Agent', ...].
   Changed to ['MAG - CyriusX', ...] so clients see the product name, not the engine.

2. DEFAULT_REPLY_PREFIX — prepended to every outgoing WhatsApp message by the bridge
   (after the gateway output sanitizer runs, so 'Hermes Agent' would reach the user
   raw). Changed to '' so no internal name leaks into the chat.

Both changes are idempotent via MARKER guard.
"""

import pathlib, sys

BRIDGE = pathlib.Path("/opt/hermes/scripts/whatsapp-bridge/bridge.js")
MARKER = "// MAG_BRANDING_PATCH_APPLIED"

text = BRIDGE.read_text()

if MARKER in text:
    print("patch_whatsapp_branding: already applied, skipping.")
    sys.exit(0)

# ── 1. browser name ───────────────────────────────────────────────────────────
OLD_BROWSER = "browser: ['Hermes Agent', 'Chrome', '120.0'],"
NEW_BROWSER  = "browser: ['MAG - CyriusX', 'Chrome', '120.0'], // MAG_BRANDING_PATCH_APPLIED"

if OLD_BROWSER not in text:
    sys.exit("patch_whatsapp_branding: browser anchor not found in bridge.js (Hermes changed).")

text = text.replace(OLD_BROWSER, NEW_BROWSER, 1)

# ── 2. DEFAULT_REPLY_PREFIX ───────────────────────────────────────────────────
# The prefix is prepended by bridge.js AFTER the gateway output sanitizer runs,
# so any name here would bypass our 'Hermes' scrubber and reach the user verbatim.
OLD_PREFIX = "const DEFAULT_REPLY_PREFIX = '⚕ *Hermes Agent*\\n────────────\\n';"
NEW_PREFIX  = "const DEFAULT_REPLY_PREFIX = ''; // MAG_BRANDING_PATCH_APPLIED (no prefix)"

if OLD_PREFIX not in text:
    sys.exit("patch_whatsapp_branding: DEFAULT_REPLY_PREFIX anchor not found in bridge.js (Hermes changed).")

text = text.replace(OLD_PREFIX, NEW_PREFIX, 1)

BRIDGE.write_text(text)
print("patch_whatsapp_branding: applied (browser='MAG - CyriusX', prefix='').")
