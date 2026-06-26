"""Build-time patch: reconcile WhatsApp LID allowlist mapping formats.

Root cause:

1. allowlist.js only resolves a LID sender when a reverse mapping file exists:
     lid-mapping-<lid>_reverse.json -> "<phone>"
2. bridge.js builds a reverse in-memory map from forward files instead:
     lid-mapping-<phone>.json -> "<lid>"
3. The two modules therefore disagree on the mapping contract. In practice a
   tenant can have a forward file that bridge.js understands, while
   matchesAllowedUser() still rejects the sender with allowlist_mismatch.

This patch makes the mapping format symmetric and self-healing:

- allowlist.js now accepts BOTH forward and reverse mapping files.
- bridge.js materializes missing reverse files from existing forward files on
  startup and whenever creds are updated.

Idempotent + fail-loud.
"""

import os
import pathlib
import sys

BRIDGE_DIR = pathlib.Path(
    os.getenv("WHATSAPP_BRIDGE_DIR", "/opt/hermes/scripts/whatsapp-bridge")
)
ALLOWLIST_JS = BRIDGE_DIR / "allowlist.js"
BRIDGE_JS = BRIDGE_DIR / "bridge.js"
MARKER = "_mag_whatsapp_lid_allowlist"


def apply(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"  [skip] {label}: already patched")
        return text
    if old not in text:
        sys.exit(
            f"FATAL: WhatsApp bridge anchor not found for '{label}'. "
            "Upstream allowlist/bridge sources changed - update "
            "patch_whatsapp_lid_allowlist.py."
        )
    print(f"  [ok]   {label}")
    return text.replace(old, new, 1)


ALLOWLIST_IMPORT_OLD = "import { existsSync, readFileSync } from 'fs';\n"
ALLOWLIST_IMPORT_NEW = (
    "import { existsSync, readFileSync, readdirSync } from 'fs';\n"
)

ALLOWLIST_READ_HELPER_OLD = """function readMappingFile(sessionDir, identifier, suffix = '') {
  const filePath = path.join(sessionDir, `lid-mapping-${identifier}${suffix}.json`);
  if (!existsSync(filePath)) {
    return null;
  }

  try {
    const parsed = JSON.parse(readFileSync(filePath, 'utf8'));
    const normalized = normalizeWhatsAppIdentifier(parsed);
    return normalized || null;
  } catch {
    return null;
  }
}
"""

ALLOWLIST_READ_HELPER_NEW = """function readMappingFile(sessionDir, identifier, suffix = '') {
  const filePath = path.join(sessionDir, `lid-mapping-${identifier}${suffix}.json`);
  if (!existsSync(filePath)) {
    return null;
  }

  try {
    const parsed = JSON.parse(readFileSync(filePath, 'utf8'));
    const normalized = normalizeWhatsAppIdentifier(parsed);
    return normalized || null;
  } catch {
    return null;
  }
}

function findForwardMappedIdentifiers(sessionDir, identifier) {
  const matches = new Set();

  try {
    for (const fileName of readdirSync(sessionDir)) {
      const directMatch = fileName.match(/^lid-mapping-(\\d+)\\.json$/);
      if (!directMatch) {
        continue;
      }

      const mapped = readMappingFile(sessionDir, directMatch[1]);
      if (mapped === identifier) {
        matches.add(directMatch[1]);
      }
    }
  } catch {
    return matches;
  }

  return matches;
}
"""

ALLOWLIST_EXPAND_OLD = """    for (const suffix of ['', '_reverse']) {
      const mapped = readMappingFile(sessionDir, current, suffix);
      if (mapped && !resolved.has(mapped)) {
        queue.push(mapped);
      }
    }
"""

ALLOWLIST_EXPAND_NEW = """    for (const suffix of ['', '_reverse']) {
      const mapped = readMappingFile(sessionDir, current, suffix);
      if (mapped && !resolved.has(mapped)) {
        queue.push(mapped);
      }
    }

    for (const mapped of findForwardMappedIdentifiers(sessionDir, current)) {
      if (!resolved.has(mapped)) {
        queue.push(mapped);
      }
    }
"""

BRIDGE_SYNC_ANCHOR_OLD = """// Build LID → phone reverse map from session files (lid-mapping-{phone}.json)
function buildLidMap() {
"""

BRIDGE_SYNC_ANCHOR_NEW = """// _mag_whatsapp_lid_allowlist: keep forward/reverse LID mapping files in sync.
function syncReverseLidMappings() {
  try {
    for (const fileName of readdirSync(SESSION_DIR)) {
      const directMatch = fileName.match(/^lid-mapping-(\\d+)\\.json$/);
      if (!directMatch) {
        continue;
      }

      const phone = directMatch[1];
      const directPath = path.join(SESSION_DIR, fileName);
      const lid = String(JSON.parse(readFileSync(directPath, 'utf8')) || '')
        .trim()
        .replace(/:.*@/, '@')
        .replace(/@.*/, '')
        .replace(/^\\+/, '');

      if (!lid) {
        continue;
      }

      const reversePath = path.join(SESSION_DIR, `lid-mapping-${lid}_reverse.json`);
      let currentPhone = '';

      if (existsSync(reversePath)) {
        try {
          currentPhone = String(JSON.parse(readFileSync(reversePath, 'utf8')) || '')
            .trim()
            .replace(/:.*@/, '@')
            .replace(/@.*/, '')
            .replace(/^\\+/, '');
        } catch {
          currentPhone = '';
        }
      }

      if (currentPhone !== phone) {
        writeFileSync(reversePath, JSON.stringify(phone));
      }
    }
  } catch {}
}

// Build LID → phone reverse map from session files (lid-mapping-{phone}.json)
function buildLidMap() {
"""

BRIDGE_SYNC_CALL_OLD = "let lidToPhone = buildLidMap();\n"
BRIDGE_SYNC_CALL_NEW = "syncReverseLidMappings();\nlet lidToPhone = buildLidMap();\n"

BRIDGE_CREDS_OLD = "  sock.ev.on('creds.update', () => { saveCreds(); lidToPhone = buildLidMap(); });\n"
BRIDGE_CREDS_NEW = (
    "  sock.ev.on('creds.update', () => { saveCreds(); syncReverseLidMappings(); "
    "lidToPhone = buildLidMap(); });\n"
)


def main() -> None:
    if not ALLOWLIST_JS.exists():
        sys.exit(f"FATAL: allowlist.js not found at {ALLOWLIST_JS}")
    if not BRIDGE_JS.exists():
        sys.exit(f"FATAL: bridge.js not found at {BRIDGE_JS}")

    print(f"Patching {ALLOWLIST_JS} + {BRIDGE_JS} ({MARKER})")

    allowlist_text = ALLOWLIST_JS.read_text(encoding="utf-8")
    allowlist_text = apply(
        allowlist_text,
        ALLOWLIST_IMPORT_OLD,
        ALLOWLIST_IMPORT_NEW,
        "allowlist imports",
    )
    allowlist_text = apply(
        allowlist_text,
        ALLOWLIST_READ_HELPER_OLD,
        ALLOWLIST_READ_HELPER_NEW,
        "allowlist forward mapping helper",
    )
    allowlist_text = apply(
        allowlist_text,
        ALLOWLIST_EXPAND_OLD,
        ALLOWLIST_EXPAND_NEW,
        "allowlist alias expansion",
    )
    ALLOWLIST_JS.write_text(allowlist_text, encoding="utf-8")

    bridge_text = BRIDGE_JS.read_text(encoding="utf-8")
    bridge_text = apply(
        bridge_text,
        BRIDGE_SYNC_ANCHOR_OLD,
        BRIDGE_SYNC_ANCHOR_NEW,
        "bridge reverse mapping sync helper",
    )
    bridge_text = apply(
        bridge_text,
        BRIDGE_SYNC_CALL_OLD,
        BRIDGE_SYNC_CALL_NEW,
        "bridge startup reverse sync",
    )
    bridge_text = apply(
        bridge_text,
        BRIDGE_CREDS_OLD,
        BRIDGE_CREDS_NEW,
        "bridge creds reverse sync",
    )
    BRIDGE_JS.write_text(bridge_text, encoding="utf-8")

    print("  WhatsApp LID allowlist mapping reconciliation applied.")


if __name__ == "__main__":
    main()
