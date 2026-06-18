"""Build-time patch: avoid redundant WhatsApp bridge npm installs on boot (MAG).

Upstream ``gateway/platforms/whatsapp.py`` treats the bridge dependencies as
fresh only when ``node_modules/.hermes-pkg-hash`` exists and matches the current
``package.json`` hash. Older installs predate that stamp, so every gateway boot
falls back to ``npm install`` even when the existing dependency tree is already
usable. When npm flakes, the whole adapter stays offline.

This patch makes the boot path more tolerant:
  1. Adds ``_has_usable_bridge_deps`` so older installs are accepted when
     ``node_modules`` exists and ``@whiskeysockets/baileys`` is present.
  2. Backfills ``node_modules/.hermes-pkg-hash`` automatically for those older
     installs, stopping the repeated npm install loop on later boots.
  3. Lets the adapter keep using the current dependency tree if writing the
     stamp fails, instead of aborting into another install attempt.

Idempotent + fail-loud like the other MAG bootstrap patches.
"""

import os
import pathlib
import sys

WHATSAPP_PY = pathlib.Path(
    os.getenv("WHATSAPP_PLATFORM_PY", "/opt/hermes/gateway/platforms/whatsapp.py")
)


def apply(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"  [skip] {label}: already patched")
        return text
    if old not in text:
        sys.exit(
            f"FATAL: WhatsApp adapter anchor not found for '{label}'. "
            f"Upstream whatsapp.py changed - update patch_whatsapp_boot_deps.py."
        )
    print(f"  [ok]   {label}")
    return text.replace(old, new, 1)


OLD_HELPER = '''def _file_content_hash(path: Path) -> str:
    """Return the first 16 hex chars of the SHA-256 of *path*'s contents.

    Used for the bridge staleness handshake: bridge.js reports its own
    source hash in ``/health`` (``scriptHash``), and the adapter compares
    it against the hash of bridge.js currently on disk.  A mismatch means
    a long-lived bridge process is serving code from before an update.
    Returns ``""`` when the file can't be read.
    """
    import hashlib
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


'''
NEW_HELPER = '''def _file_content_hash(path: Path) -> str:
    """Return the first 16 hex chars of the SHA-256 of *path*'s contents.

    Used for the bridge staleness handshake: bridge.js reports its own
    source hash in ``/health`` (``scriptHash``), and the adapter compares
    it against the hash of bridge.js currently on disk.  A mismatch means
    a long-lived bridge process is serving code from before an update.
    Returns ``""`` when the file can't be read.
    """
    import hashlib
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def _has_usable_bridge_deps(bridge_dir: Path) -> bool:
    """Return True when the WhatsApp bridge install looks usable.

    Some installs predate the .hermes-pkg-hash stamp. In that case we should
    not force `npm install` on every boot if the dependency tree is already
    present. We treat the install as usable when node_modules exists and the
    primary Baileys package is present.
    """
    node_modules = bridge_dir / "node_modules"
    baileys_pkg = node_modules / "@whiskeysockets" / "baileys" / "package.json"
    return node_modules.exists() and baileys_pkg.exists()


'''

OLD_INSTALL = '''            bridge_dir = bridge_path.parent
            _pkg_json = bridge_dir / "package.json"
            _dep_stamp = bridge_dir / "node_modules" / ".hermes-pkg-hash"
            _pkg_hash = _file_content_hash(_pkg_json)
            _deps_fresh = False
            if (bridge_dir / "node_modules").exists():
                try:
                    _deps_fresh = (_dep_stamp.read_text().strip() == _pkg_hash) and bool(_pkg_hash)
                except OSError:
                    _deps_fresh = False
            if not _deps_fresh:
                print(f"[{self.name}] Installing WhatsApp bridge dependencies...")
'''
NEW_INSTALL = '''            bridge_dir = bridge_path.parent
            _node_modules = bridge_dir / "node_modules"
            _pkg_json = bridge_dir / "package.json"
            _dep_stamp = _node_modules / ".hermes-pkg-hash"
            _pkg_hash = _file_content_hash(_pkg_json)
            _deps_fresh = False
            if _node_modules.exists():
                try:
                    _deps_fresh = (_dep_stamp.read_text().strip() == _pkg_hash) and bool(_pkg_hash)
                except OSError:
                    _deps_fresh = False
            if not _deps_fresh and _has_usable_bridge_deps(bridge_dir) and _pkg_hash:
                # Backfill the dependency stamp for older installs that already
                # have a usable node_modules tree. Without this, every restart
                # forces npm install and the adapter stays offline if npm flakes.
                try:
                    _dep_stamp.write_text(_pkg_hash)
                    _deps_fresh = True
                    logger.info("[%s] Backfilled WhatsApp dependency stamp for existing node_modules", self.name)
                except OSError:
                    logger.info("[%s] Using existing WhatsApp bridge dependencies without stamp", self.name)
                    _deps_fresh = True
            if not _deps_fresh:
                print(f"[{self.name}] Installing WhatsApp bridge dependencies...")
'''


def main() -> None:
    if not WHATSAPP_PY.exists():
        sys.exit(f"FATAL: whatsapp.py not found at {WHATSAPP_PY}")
    text = WHATSAPP_PY.read_text()
    print(f"Patching {WHATSAPP_PY}")
    updated = apply(text, OLD_HELPER, NEW_HELPER, "bridge deps helper")
    updated = apply(updated, OLD_INSTALL, NEW_INSTALL, "dependency stamp backfill")
    if updated != text:
        WHATSAPP_PY.write_text(updated)
        print("  WhatsApp boot dependency handling patched.")
    else:
        print("  WhatsApp boot dependency handling already up to date.")


if __name__ == "__main__":
    main()

