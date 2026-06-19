"""Build-time patch: make the MAG Telegram block list authoritative in Hermes core.

Stock Hermes has no "denied/blocked user" concept — authorization is allow-only
(env allowlist ∪ pairing approved store ∪ allow-all). MAG needs a deny path: an owner
who rejects a pending request, or bans a user, must be able to keep them out *even if*
that user is also in TELEGRAM_ALLOWED_USERS, and the bot must stop re-issuing them a
pairing code (otherwise they reappear in the panel's pending list every message).

The block list itself lives in our own module (gateway/platforms/mag_telegram_pairing.py,
``is_blocked()``). This patch injects two checks that consult it:

  1. gateway/authz_mixin.py :: _is_user_authorized — deny a blocked user before any
     allow path runs. Block beats allowlist, pairing store, and allow-all.
  2. gateway/run.py — in the unauthorized-DM handler, drop a blocked user silently
     instead of generating + sending a fresh pairing code.

Both call sites import lazily and swallow errors (fail-open on the block check) so a
missing/renamed module can never lock every user out.

Idempotent + fail-loud on anchor drift (mirrors the other bootstrap patches).
"""

import os
import pathlib
import sys

AUTHZ = pathlib.Path(
    os.getenv("GATEWAY_AUTHZ_MIXIN_PY", "/opt/hermes/gateway/authz_mixin.py")
)
RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))
MARKER = "_mag_is_blocked"


def apply(path: pathlib.Path, old: str, new: str, label: str) -> None:
    if not path.exists():
        sys.exit(f"FATAL: {path} not found — cannot apply '{label}'.")
    text = path.read_text()
    if new in text:
        print(f"  [skip] {label}: already patched")
        return
    if old not in text:
        sys.exit(
            f"FATAL: anchor not found for '{label}' in {path.name}. "
            f"Upstream Hermes changed — update patch_authz_blocklist.py."
        )
    path.write_text(text.replace(old, new, 1))
    print(f"  [ok]   {label}")


# --- Edit 1: _is_user_authorized — block beats every allow path ----------------
AUTHZ_OLD = (
    "        user_id = source.user_id\n"
    "\n"
    "        # Telegram (and similar) authorize entire group/forum/channel chats\n"
)
AUTHZ_NEW = (
    "        user_id = source.user_id\n"
    "\n"
    "        # MAG: the block list takes precedence over every allow path (env\n"
    "        # allowlist, pairing approved store, allow-all). A blocked user is\n"
    "        # never authorized. Fail-open on any error so a bad block file can't\n"
    "        # lock everyone out.\n"
    "        if user_id:\n"
    "            try:\n"
    "                from gateway.platforms.mag_telegram_pairing import is_blocked as _mag_is_blocked\n"
    "                if _mag_is_blocked(source.platform.value if source.platform else \"\", str(user_id)):\n"
    "                    return False\n"
    "            except Exception:\n"
    "                pass\n"
    "\n"
    "        # Telegram (and similar) authorize entire group/forum/channel chats\n"
)

# --- Edit 2: unauthorized-DM handler — don't re-issue a code to a blocked user -
RUN_OLD = (
    '            logger.warning("Unauthorized user: %s (%s) on %s", source.user_id, source.user_name, source.platform.value)\n'
    "            # In DMs: offer pairing code. In groups: silently ignore.\n"
)
RUN_NEW = (
    '            logger.warning("Unauthorized user: %s (%s) on %s", source.user_id, source.user_name, source.platform.value)\n'
    "            # MAG: a blocked user is denied silently — never re-issued a pairing\n"
    "            # code, so they don't reappear in the panel's pending list.\n"
    "            if source.user_id:\n"
    "                try:\n"
    "                    from gateway.platforms.mag_telegram_pairing import is_blocked as _mag_is_blocked\n"
    "                    if _mag_is_blocked(source.platform.value if source.platform else \"\", str(source.user_id)):\n"
    "                        return None\n"
    "                except Exception:\n"
    "                    pass\n"
    "            # In DMs: offer pairing code. In groups: silently ignore.\n"
)


def main() -> None:
    print(f"Patching Hermes authorization for the MAG block list ({MARKER})")
    apply(AUTHZ, AUTHZ_OLD, AUTHZ_NEW, "authz_mixin._is_user_authorized: block beats allow")
    apply(RUN_PY, RUN_OLD, RUN_NEW, "run.py unauthorized handler: no code for blocked user")
    print("  block-list authorization patched.")


if __name__ == "__main__":
    main()
