"""Build-time patch: make code execution UNAVAILABLE on client channels (MAG, Task A).

On a client channel (Telegram/WhatsApp/…) the agent must not be able to call
``execute_code``. ``patch_channel_noise_suppress.py`` already DENIES it at the
approval gate (fail-closed, no client prompt), but an eager/jailbroken model still
SEES the tool, so it loops: call execute_code → deny → retry → … stalling the reply
~60s+ before it finally refuses (observed live: a plain "15% of 2300" sat "typing"
for minutes). This removes the tool from the turn entirely on client channels by
adding the ``code_execution`` toolset to ``disabled_toolsets`` at agent-build time,
so the model never sees it and never loops. No capability is lost: execute_code on
a client channel was already fully denied — this only kills the wasteful loop.
Internal staff surfaces (api_server / local / cli) keep it for file/PDF generation.
The approval-time deny stays as defence-in-depth.

Two agent-build sites in gateway/run.py both compute ``platform_key`` and
``disabled_toolsets`` right before constructing the agent; this injects the gate
after each. Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))

MARKER = "MAG_disable_channel_code_exec"

# Same logical line at two build sites — different local var name + indentation.
SITES = (
    ('            disabled_toolsets = agent_cfg.get("disabled_toolsets") or None\n', "            "),
    ('        disabled_toolsets = agent_cfg_local.get("disabled_toolsets") or None\n', "        "),
)


def _gate(indent: str) -> str:
    i = indent
    return (
        f"{i}# {MARKER}: on a client channel, code execution AND the interactive browser\n"
        f"{i}# must be UNAVAILABLE (not merely denied at the approval gate) — otherwise the\n"
        f"{i}# model loops calling execute_code -> deny -> retry (or substitutes the equally\n"
        f"{i}# slow agent-browser eval), stalling the reply ~60s+ before it answers. Even a\n"
        f"{i}# plain calculation got routed through these heavy tools on a channel. Internal\n"
        f"{i}# surfaces (api_server/local/cli) keep them for file/PDF gen and real browsing.\n"
        f'{i}if platform_key not in ("api_server", "local", "cli"):\n'
        f'{i}    disabled_toolsets = sorted({{"code_execution", "browser", *(disabled_toolsets or [])}})\n'
    )


def main() -> None:
    if not RUN_PY.exists():
        raise SystemExit(f"gateway run.py not found at {RUN_PY}")
    text = RUN_PY.read_text()

    if MARKER in text:
        print("OK: channel code-exec disable already patched (idempotent no-op)")
        return

    for anchor, indent in SITES:
        if anchor not in text:
            raise SystemExit(
                f"patch_disable_channel_code_exec: anchor not found (Hermes changed): {anchor!r}"
            )
        text = text.replace(anchor, anchor + _gate(indent), 1)

    RUN_PY.write_text(text)
    print("OK: patched client channels to disable the code_execution toolset (Task A)")


if __name__ == "__main__":
    main()
