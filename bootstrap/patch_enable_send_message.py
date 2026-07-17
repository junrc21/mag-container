"""Build-time patch: register ``send_message`` as an agent-callable tool.

Upstream Hermes ships ``tools/send_message_tool.py`` with the full outbound
send engine (per-platform senders, target resolution, channel-directory
lookup) but deliberately WITHOUT a ``registry.register(...)`` call — see the
NOTE left in that file. Upstream's stance: the agent should not decide on its
own to fire off cross-platform messages to people it wasn't asked to contact.
Outbound delivery is otherwise only reachable via cron (cron/scheduler.py),
the `hermes send` CLI, the kanban notifier, and the standalone MCP server —
never from inside a live chat turn.

MAG's product requirement is narrower and safer than "let the agent message
anyone": a tenant's MAG may message a contact who has ALREADY talked to her
(resolved via `send_message(action="list")`, backed by the channel directory
built from real inbound sessions — see gateway/channel_directory.py) or whose
handle/ID the user supplies in the same turn. She must never cold-message
someone genuinely unknown. That policy is enforced in the per-tenant system
prompt (MAG's internal.service.ts, "Sending a message to someone" hard rule)
— NOT here. This patch only makes the capability reachable at all; it does
not relax Telegram's/each platform's own real anti-cold-DM restrictions
(a bot still cannot message a user/bot that has never started a chat with
it — that is enforced by the platform's API, independent of this patch).

Two edits:
  1. toolsets.py — add "send_message" to _HERMES_CORE_TOOLS (so it's part of
     every platform toolset: hermes-telegram, hermes-discord, ...) and define
     a "messaging" toolset entry so admins can enable/disable it per tenant
     via disabled_toolsets, exactly like any other toolset (MAG's own admin
     panel already has a "messaging" toggle in TOOLSET_CATALOG — it was a
     no-op until now because Hermes had no toolset by that name).
  2. tools/send_message_tool.py — replace the "intentionally NOT registered"
     comment with the actual registry.register(...) call, following the
     documented pattern (see tools/clarify_tool.py for the canonical
     example).

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

TOOLSETS_PY = pathlib.Path(os.getenv("TOOLSETS_PY", "/opt/hermes/toolsets.py"))
SEND_MESSAGE_TOOL_PY = pathlib.Path(
    os.getenv("SEND_MESSAGE_TOOL_PY", "/opt/hermes/tools/send_message_tool.py")
)

MARKER = "MAG: registered as an agent-callable tool"

# --- toolsets.py edit 1: add send_message to the shared core tool list ---------
CORE_TOOLS_ANCHOR = (
    '    "computer_use",\n'
    "]\n"
)
CORE_TOOLS_REPLACEMENT = (
    '    "computer_use",\n'
    "    # Send a message to a known contact/channel on a connected platform\n"
    '    # (toolset "messaging" — disableable independently via disabled_toolsets).\n'
    '    "send_message",\n'
    "]\n"
)

# --- toolsets.py edit 2: define the "messaging" toolset ------------------------
MESSAGING_TOOLSET_ANCHOR = (
    '    "clarify": {\n'
    '        "description": "Ask the user clarifying questions (multiple-choice or open-ended)",\n'
    '        "tools": ["clarify"],\n'
    '        "includes": []\n'
    "    },\n"
    "    \n"
)
MESSAGING_TOOLSET_BLOCK = (
    '    "messaging": {\n'
    '        "description": "Send a message to a known contact/channel on a connected messaging platform",\n'
    '        "tools": ["send_message"],\n'
    '        "includes": []\n'
    "    },\n"
    "\n"
)

# --- send_message_tool.py edit: register the tool -------------------------------
REGISTRY_ANCHOR = (
    "# --- Registry ---\n"
    "from tools.registry import tool_error\n"
    "\n"
    "# NOTE: ``send_message`` is intentionally NOT registered as an agent-callable\n"
    "# model tool. The agent should not decide on its own to fire off cross-platform\n"
    "# messages or reactions. The send engine in this module (``_send_to_platform``,\n"
    "# ``_send_via_adapter``, ``_parse_target_ref``, the per-platform ``_send_*``\n"
    "# helpers) remains the shared transport used by:\n"
    "#   - cron delivery (cron/scheduler.py)\n"
    "#   - the ``hermes send`` CLI command (hermes_cli/send_cmd.py)\n"
    "#   - the gateway kanban notifier (dashboard-toggled, outside agent control)\n"
    "#   - the standalone MCP server (mcp_serve.py), which is an opt-in surface\n"
    "# Those callers import the helpers directly; none of them need the registry\n"
    "# entry.\n"
)
REGISTRY_BLOCK = (
    "# --- Registry ---\n"
    "from tools.registry import registry, tool_error\n"
    "\n"
    "# MAG: registered as an agent-callable tool under toolset \"messaging\"\n"
    "# (disableable per-tenant via disabled_toolsets, same as any other toolset).\n"
    "# Upstream Hermes ships this file WITHOUT a registry entry on purpose (the\n"
    "# agent should not autonomously message people it wasn't asked to message) —\n"
    "# see MAG's own product policy instead: MAG only sends to a contact who has\n"
    "# already messaged her (resolved via action=\"list\"/channel_directory) or\n"
    "# whose handle/ID the user just supplied in this same turn; she never\n"
    "# initiates contact with someone truly unknown. That's enforced in the\n"
    "# per-tenant system prompt (internal.service.ts), not here — this registration\n"
    "# only makes the capability reachable at all.\n"
    "registry.register(\n"
    '    name="send_message",\n'
    '    toolset="messaging",\n'
    "    schema=SEND_MESSAGE_SCHEMA,\n"
    "    handler=send_message_tool,\n"
    "    check_fn=lambda: True,\n"
    '    emoji="\U0001f4e4",\n'
    ")\n"
)


def _patch_toolsets() -> None:
    if not TOOLSETS_PY.exists():
        raise SystemExit(f"toolsets.py not found at {TOOLSETS_PY}")
    text = TOOLSETS_PY.read_text()

    if '"send_message"' in text:
        print("OK: toolsets.py already patched (idempotent no-op)")
        return

    if CORE_TOOLS_ANCHOR not in text:
        raise SystemExit(
            "patch_enable_send_message: _HERMES_CORE_TOOLS anchor missing (Hermes changed)."
        )
    text = text.replace(CORE_TOOLS_ANCHOR, CORE_TOOLS_REPLACEMENT, 1)

    if MESSAGING_TOOLSET_ANCHOR not in text:
        raise SystemExit(
            "patch_enable_send_message: clarify toolset anchor missing (Hermes changed)."
        )
    text = text.replace(
        MESSAGING_TOOLSET_ANCHOR, MESSAGING_TOOLSET_ANCHOR + MESSAGING_TOOLSET_BLOCK, 1
    )

    TOOLSETS_PY.write_text(text)
    print("OK: patched toolsets.py (core tools list + messaging toolset)")


def _patch_send_message_tool() -> None:
    if not SEND_MESSAGE_TOOL_PY.exists():
        raise SystemExit(f"send_message_tool.py not found at {SEND_MESSAGE_TOOL_PY}")
    text = SEND_MESSAGE_TOOL_PY.read_text()

    if MARKER in text:
        print("OK: send_message_tool.py already patched (idempotent no-op)")
        return

    if REGISTRY_ANCHOR not in text:
        raise SystemExit(
            "patch_enable_send_message: registry comment anchor missing (Hermes changed)."
        )
    text = text.replace(REGISTRY_ANCHOR, REGISTRY_BLOCK, 1)

    SEND_MESSAGE_TOOL_PY.write_text(text)
    print("OK: patched send_message_tool.py (registered send_message)")


def main() -> None:
    _patch_toolsets()
    _patch_send_message_tool()


if __name__ == "__main__":
    main()
