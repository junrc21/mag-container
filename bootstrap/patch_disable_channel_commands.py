"""Build-time patch: disable gateway slash commands on client channels (MAG).

End users on Telegram/WhatsApp/etc. must NOT be able to run gateway slash
commands (/model, /reset, /restart, /yolo, /new, /cron, …) — with them a client
could change the LLM model, restart/reset the agent, toggle yolo, and so on. This
patch closes that hole:

  1. MessageEvent.is_command() (gateway/platforms/base.py): on client channels
     ONLY ``/start`` stays a recognized command (Telegram launch / deep-link
     pairing ping); every other ``/...`` is NOT recognized and flows to the agent
     as NORMAL TEXT — it does not execute and shows no "admin-only" denial (which
     would leak that a command system even exists, against the secrecy barrier).
     Internal staff surfaces (api_server / local / cli) keep all commands, so the
     god-mode admin path is unaffected. is_command() is the single chokepoint:
     get_command(), get_command_args() and the channel adapters all go through it.
  2. Telegram set_my_commands menu (gateway/platforms/telegram.py): registered
     with an EMPTY list so the "/" hint menu does not appear in the client's UI.

Idempotent + fail-loud (mirrors the other bootstrap patches): re-running applies
only the missing edits; an anchor that is neither original nor patched aborts the
build so an upstream Hermes change is caught, not silently leaked.
"""

import os
import pathlib

BASE_PY = pathlib.Path(os.getenv("GATEWAY_BASE_PY", "/opt/hermes/gateway/platforms/base.py"))
TELEGRAM_PY = pathlib.Path(os.getenv("GATEWAY_TELEGRAM_PY", "/opt/hermes/gateway/platforms/telegram.py"))

MARKER = "_mag_channel_commands_disabled"

# --- Edit 1: gate is_command() by platform ------------------------------------
OLD_IS_COMMAND = (
    '    def is_command(self) -> bool:\n'
    '        """Check if this is a command message (e.g., /new, /reset)."""\n'
    '        return self.text.startswith("/")\n'
)
NEW_IS_COMMAND = (
    '    def is_command(self) -> bool:\n'
    '        """Check if this is a command message (e.g., /new, /reset)."""\n'
    '        # _mag_channel_commands_disabled: end users on client channels must not\n'
    '        # run gateway slash commands (/model, /reset, /restart, /yolo, …). Only\n'
    '        # /start stays a command there (Telegram launch/deep-link ping); every\n'
    '        # other "/..." is NOT recognized -> flows to the agent as normal text\n'
    '        # (no execution, no "admin-only" leak). Internal surfaces keep all.\n'
    '        if not self.text.startswith("/"):\n'
    '            return False\n'
    '        try:\n'
    '            _plat = self.source.platform.value if (self.source and self.source.platform) else ""\n'
    '        except Exception:\n'
    '            _plat = ""\n'
    '        if not _plat or _plat in ("api_server", "local", "cli"):\n'
    '            return True\n'
    '        _first = self.text.split(maxsplit=1)[0][1:].lower()\n'
    '        if "@" in _first:\n'
    '            _first = _first.split("@", 1)[0]\n'
    '        return _first == "start"\n'
)

# --- Edit 2: empty the Telegram "/" command menu ------------------------------
# Core substring is identical at every registration site (default + forum lazy);
# matching without leading whitespace replaces all of them, indentation intact.
OLD_MENU_CORE = "bot_commands = [BotCommand(name, desc) for name, desc in menu_commands]"
NEW_MENU_CORE = (
    "bot_commands = []  # _mag_channel_commands_disabled: hide the slash-command menu from clients"
)

# --- Edit 3: make the clear independent of the command registry ----------------
# The registry call is the only thing that could raise BEFORE the empty-list
# registration runs (and skip the clear). We don't need the registry at all when
# clearing, so replace the call with a literal empty result at every site.
OLD_MENU_CALL = "telegram_menu_commands(max_commands=MAX_COMMANDS_PER_SCOPE)"
NEW_MENU_CALL = "([], 0)  # _mag_channel_commands_disabled: skip registry; always clear"

# --- Edit 4: explicit deleteMyCommands on boot (belt-and-suspenders) ------------
# setMyCommands([]) already clears, but deleteMyCommands makes the removal explicit
# and survives any quirk. Injected only in the main boot loop (default/private/
# group scopes — where the "/" menu shows for clients).
OLD_SET = "await self._bot.set_my_commands(bot_commands, scope=scope_cls())"
NEW_SET = (
    "await self._bot.delete_my_commands(scope=scope_cls())\n"
    "                        await self._bot.set_my_commands(bot_commands, scope=scope_cls())"
)


def _patch_base() -> int:
    if not BASE_PY.exists():
        raise SystemExit(f"platforms/base.py not found at {BASE_PY}")
    text = BASE_PY.read_text()
    if MARKER in text:
        return 0
    if OLD_IS_COMMAND not in text:
        raise SystemExit("patch_disable_channel_commands: is_command anchor missing (Hermes changed).")
    text = text.replace(OLD_IS_COMMAND, NEW_IS_COMMAND, 1)
    BASE_PY.write_text(text)
    return 1


def _patch_telegram() -> int:
    if not TELEGRAM_PY.exists():
        raise SystemExit(f"platforms/telegram.py not found at {TELEGRAM_PY}")
    text = TELEGRAM_PY.read_text()
    edits = 0

    # Edit 2 — register an empty command list at every site.
    if NEW_MENU_CORE not in text:
        if OLD_MENU_CORE not in text:
            raise SystemExit("patch_disable_channel_commands: telegram menu anchor missing (Hermes changed).")
        text = text.replace(OLD_MENU_CORE, NEW_MENU_CORE)
        edits += 1

    # Edit 3 — registry-independent clear.
    if NEW_MENU_CALL not in text:
        if OLD_MENU_CALL not in text:
            raise SystemExit("patch_disable_channel_commands: telegram menu-call anchor missing (Hermes changed).")
        text = text.replace(OLD_MENU_CALL, NEW_MENU_CALL)
        edits += 1

    # Edit 4 — explicit deleteMyCommands in the main boot loop.
    if NEW_SET not in text:
        if OLD_SET not in text:
            raise SystemExit("patch_disable_channel_commands: telegram set_my_commands anchor missing (Hermes changed).")
        text = text.replace(OLD_SET, NEW_SET, 1)
        edits += 1

    if edits:
        TELEGRAM_PY.write_text(text)
    return edits


def main() -> None:
    edits = _patch_base() + _patch_telegram()
    if edits == 0:
        print("OK: channel slash commands already disabled (idempotent no-op)")
    else:
        print(f"OK: disabled channel slash commands ({edits} file(s) patched)")


if __name__ == "__main__":
    main()
